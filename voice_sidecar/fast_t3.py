"""Chatterbox Turbo's speech-token loop, as a replayed CUDA graph.

``T3.inference_turbo`` runs one GPT-2 step per speech token (25 a second of
audio) from Python, then samples with Hugging Face's logits processors: a
dozen small kernels, each launched separately. On this laptop's RTX 5050 that
was 9-13 ms per token, about 1 s for a typical first line (measured 23-24
September), and it is most of the wait before Sage's voice.

Here the step -- embedding, transformer, head, and the same sampling rules --
is captured once as a CUDA graph and replayed. The transformer runs the same
fp32 kernels over a static KV cache, and the sampling is the stock
processors' own arithmetic in their own order (temperature, top-k, top-p,
then repetition penalty). Teacher-forced along the stock loop's own tokens,
every step's distribution matched it to 5e-7 total variation (float
rounding) over 270 steps on 24 September; only the random stream differs, as
it does between any two runs. Nothing about the voice changes -- it is the
same model computing the same thing, faster.

A static cache is attended over its full length every step, so its length is
the cost: 4.0 ms a step at 512 slots, 6.2 ms at 1536. Each piece therefore
uses the smallest of a few cache lengths that holds its prompt (the voice's
conditioning plus the text, ~380-400) and its length budget, each with its
own captured graph.

It also stops a runaway. The stock loop ran to 1,000 tokens (40 s of speech)
when the model missed its stop token, took 17 s over one short line after an
interruption on 23 September and then ran out of memory making audio from
it. Here generation stops at the engine's length budget and reports it, and
the engine's existing retry takes over.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("voice_sidecar")

#: Speech tokens per second of audio, for the length budget.
TOKENS_PER_SECOND = 25
#: Cache lengths, prompt included. 512 holds a short line (~4.5 s of speech
#: after the prompt); 1536 about 44 s. A piece that could need more goes to
#: the stock loop.
CACHE_BUCKETS = (512, 640, 768, 1024, 1536)
#: The caches almost every piece fits, kept captured for good. The longer
#: ones exist for the odd long sentence: once made they held 180 MB (Nano)
#: to 480 MB (Turbo) of the card for the rest of the process, plus their
#: graphs' private memory, and the live sidecar sat ~460 MB above a fresh
#: one (24 September). They are released after IDLE_RELEASE_SECONDS unused.
KEEP_BUCKETS = (512, 640, 768)
IDLE_RELEASE_SECONDS = 60.0


class PieceTooLong(ValueError):
    """The piece's prompt and budget exceed the longest cache: use the stock
    loop for this one."""


class _Bucket:
    """A static cache of one length and the decode step captured over it."""

    def __init__(self, length: int, config: Any) -> None:
        from transformers import StaticCache

        self.length = length
        self.cache = StaticCache(config=config, max_cache_len=length)
        self.graph: Optional[Any] = None
        self.last_used = time.monotonic()


class GraphedT3:
    """Captured decode steps, reused for every piece."""

    def __init__(self, t3: Any, device: str) -> None:
        import torch

        self.t3 = t3
        self.device = device
        self.hp = t3.hp
        vocab = t3.speech_head.out_features
        self.vocab = vocab
        # Static inputs and outputs every graph reads and writes.
        self.tok = torch.zeros(1, 1, dtype=torch.long, device=device)
        self.pos = torch.zeros(1, dtype=torch.long, device=device)
        self.seen = torch.zeros(1, vocab, dtype=torch.bool, device=device)
        self.next = torch.zeros(1, 1, dtype=torch.long, device=device)
        self.params = torch.zeros(4, device=device)  # temperature, top_p, penalty, _
        # The last step's sampling distribution, for the equivalence check.
        self.probs = torch.zeros(1, vocab, device=device)
        self.top_k = 0
        self.buckets: Dict[int, _Bucket] = {}
        self.cache: Optional[Any] = None  # the bucket a step runs over

    # ------------------------------------------------------------ sampling

    def _sample(self, logits: Any) -> Any:
        import torch

        probs = self._probs(logits)
        self.probs.copy_(probs)
        return torch.multinomial(probs, num_samples=1)

    def _probs(self, logits: Any) -> Any:
        """The stock processors, in their order, on one step's logits.

        Mirrors ``inference_turbo``: TemperatureLogitsWarper, TopKLogitsWarper,
        TopPLogitsWarper, RepetitionPenaltyLogitsProcessor, softmax,
        multinomial. The repetition penalty's "input ids" are kept as a mask
        of tokens already said: the processor gathers and scatters by id, so
        a token said twice is penalised once, as a mask does.
        """
        import torch

        temperature, top_p, penalty = self.params[0], self.params[1], self.params[2]
        scores = logits / temperature
        if self.top_k > 0:
            k = min(self.top_k, scores.shape[-1])
            kth = torch.topk(scores, k).values[..., -1:]
            scores = scores.masked_fill(scores < kth, -float("inf"))
        # TopPLogitsWarper: sort ascending, drop the low tail whose mass is at
        # most 1 - top_p, always keep the top token.
        sorted_logits, sorted_idx = torch.sort(scores, descending=False)
        cumulative = sorted_logits.softmax(dim=-1).cumsum(dim=-1)
        remove_sorted = cumulative <= (1 - top_p)
        remove_sorted[..., -1:] = False
        remove = remove_sorted.scatter(1, sorted_idx, remove_sorted)
        scores = scores.masked_fill(remove, -float("inf"))
        penalised = torch.where(scores < 0, scores * penalty, scores / penalty)
        scores = torch.where(self.seen, penalised, scores)
        return torch.softmax(scores, dim=-1)

    def _step(self) -> None:
        emb = self.t3.speech_emb(self.tok)
        out = self.t3.tfmr(
            inputs_embeds=emb,
            past_key_values=self.cache,
            use_cache=True,
            cache_position=self.pos,
        )
        logits = self.t3.speech_head(out[0])[:, -1, :]
        token = self._sample(logits)
        self.next.copy_(token)
        self.seen.scatter_(1, token, True)

    def _capture(self, bucket: _Bucket) -> None:
        import torch

        self.cache = bucket.cache
        # The warm-up steps below write the cache at `pos`, which still holds
        # the last piece's position. A capture of a smaller cache than that
        # piece used wrote past its end: "index_copy_(): index out of
        # bounds", a device-side assert, and the sidecar was silent until
        # restarted (24 September, mid-reply). Slot 0 is inside every cache,
        # and the prefill that follows overwrites it.
        self.pos.zero_()
        self.tok.zero_()
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(2):
                self._step()
        torch.cuda.current_stream().wait_stream(stream)
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph):
            self._step()
        bucket.graph = graph

    def _bucket(self, needed: int) -> _Bucket:
        for length in CACHE_BUCKETS:
            if length >= needed:
                bucket = self.buckets.get(length)
                if bucket is None:
                    bucket = _Bucket(length, self.t3.tfmr.config)
                    self.buckets[length] = bucket
                return bucket
        raise PieceTooLong(needed)

    def prepare(self, lengths: Tuple[int, ...]) -> None:
        """Capture the steps for *lengths* ahead of the first piece that
        needs them (a capture costs a few steps' time). Needs a ``generate``
        first, which sets the sampling the graphs bake in."""
        for length in lengths:
            bucket = self._bucket(length)
            if bucket.graph is None:
                self._capture(bucket)

    def release_idle(self, now: Optional[float] = None) -> int:
        """Drop long-piece caches unused for IDLE_RELEASE_SECONDS, with
        their graphs; returns how many. Caller holds the engine's lock, so
        no piece is running over them. A later long piece captures again."""
        now = time.monotonic() if now is None else now
        idle = [
            length
            for length, bucket in self.buckets.items()
            if length not in KEEP_BUCKETS
            and now - bucket.last_used > IDLE_RELEASE_SECONDS
        ]
        for length in idle:
            bucket = self.buckets.pop(length)
            if self.cache is bucket.cache:
                self.cache = None
            bucket.graph = None
            bucket.cache = None
        return len(idle)

    # ---------------------------------------------------------- generation

    def _prefill(self, bucket: _Bucket, embeds: Any) -> Any:
        import torch

        bucket.cache.reset()
        out = self.t3.tfmr(
            inputs_embeds=embeds,
            past_key_values=bucket.cache,
            use_cache=True,
            cache_position=torch.arange(embeds.shape[1], device=self.device),
        )
        return self.t3.speech_head(out[0][:, -1:])[:, -1, :]

    def generate(
        self,
        t3_cond: Any,
        text_tokens: Any,
        *,
        temperature: float,
        top_k: int,
        top_p: float,
        repetition_penalty: float,
        max_tokens: int,
    ) -> Tuple[Any, bool]:
        """Speech tokens for one piece, and whether it stopped by itself.

        ``False`` means the budget ran out first: a runaway.
        """
        import torch

        t3 = self.t3
        start = self.hp.start_speech_token * torch.ones_like(text_tokens[:, :1])
        embeds, _ = t3.prepare_input_embeds(
            t3_cond=t3_cond,
            text_tokens=text_tokens,
            speech_tokens=start,
            cfg_weight=0.0,
        )
        prompt = embeds.shape[1]
        bucket = self._bucket(prompt + max_tokens)
        if self.top_k != int(top_k):
            self.top_k = int(top_k)
            for other in self.buckets.values():
                other.graph = None  # top-k is a shape: recapture
        self.params[0] = float(temperature)
        self.params[1] = float(top_p)
        self.params[2] = float(repetition_penalty)

        if bucket.graph is None:
            # Capture runs the step on whatever the cache holds; the prefill
            # below starts this piece's cache over from the prompt.
            self._capture(bucket)
        self.cache = bucket.cache
        bucket.last_used = time.monotonic()
        first_logits = self._prefill(bucket, embeds)
        # The first draw penalises the start token, as the stock loop's first
        # call does (its "input ids" are the start token alone); every later
        # draw penalises only the tokens generated so far.
        self.seen.zero_()
        self.seen[0, self.hp.start_speech_token] = True
        first = self._sample(first_logits)
        self.seen.zero_()
        self.seen.scatter_(1, first, True)

        tokens = [first]
        stopped = bool(first.item() == self.hp.stop_speech_token)
        current = first
        step = 0
        while not stopped and len(tokens) < max_tokens:
            self.tok.copy_(current)
            self.pos.fill_(prompt + step)
            bucket.graph.replay()
            current = self.next.clone()
            tokens.append(current)
            step += 1
            stopped = bool(current.item() == self.hp.stop_speech_token)
        all_tokens = torch.cat(tokens, dim=1)
        if stopped:
            all_tokens = all_tokens[:, :-1]
        return all_tokens, stopped


def token_budget(seconds: float) -> int:
    """Speech tokens allowed for a piece that may last *seconds*."""
    return int(math.ceil(seconds * TOKENS_PER_SECOND)) + 2
