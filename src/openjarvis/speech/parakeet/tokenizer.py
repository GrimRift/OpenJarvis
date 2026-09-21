"""The SentencePiece vocabulary shipped with the ONNX export, read through
the ``tokenizers`` library (already in Sage's venv via transformers)."""

from __future__ import annotations

import os
from typing import Optional


class Tokenizer:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.vocab_size = int(inner.get_vocab_size())

    @classmethod
    def load(cls, model_dir: str) -> "Tokenizer":
        from tokenizers import Tokenizer as HFTokenizer

        path = os.path.join(model_dir, "tokenizer.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"missing tokenizer.json in {model_dir}")
        return cls(HFTokenizer.from_file(path))

    def token_to_id(self, token: str) -> int:
        value = self._inner.token_to_id(token)
        if value is None:
            raise KeyError(f"tokenizer has no token {token!r}")
        return int(value)

    def id_to_token(self, index: int) -> Optional[str]:
        return self._inner.id_to_token(index)
