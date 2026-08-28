import { useState, useRef, useCallback, useEffect } from 'react';
import { Send, Square, Paperclip, Search, VolumeX } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore, generateId } from '../../lib/store';
import { streamChat, streamResearch } from '../../lib/sse';
import type { ChatRequest } from '../../lib/sse';
import {
  fetchSavings,
  getBase,
  synthesizeSpeech,
  transcribeAudio,
} from '../../lib/api';
import { playGreeting, preloadGreetings } from '../../lib/greeting';
import { listConnectors, getSyncStatus } from '../../lib/connectors-api';
import { serializeToolCallArguments } from '../../lib/tool-call';
import { shouldSynthesizeReplyAudio } from '../../lib/audio-policy';
import { useStreamingTts } from '../../hooks/useStreamingTts';
import { MicButton } from './MicButton';
import { useSpeech } from '../../hooks/useSpeech';
import { useWakeWord } from '../../hooks/useWakeWord';
import { useFluxSpeech } from '../../hooks/useFluxSpeech';
import type {
  ChatMessage,
  MessageTelemetry,
  ResearchSearchTrace,
  ResearchSource,
  TokenUsage,
  ToolCallInfo,
} from '../../types';

/**
 * Wrap raw PCM in a WAV container for the local transcription endpoint.
 *
 * Only used on the Flux fallback path: the buffered turn is raw 16-bit mono
 * samples, and faster-whisper is handed a file, not a stream.
 */
function pcm16ToWav(samples: Int16Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };
  write(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  write(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    view.setInt16(44 + i * 2, samples[i], true);
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

// While Deep Research is toggled on, poll connected sources for sync
// progress so we can surface "Searching over N items — sync in progress"
// next to the toggle. Polling is gated on `enabled` so toggling DR off
// stops the network chatter immediately.
function useResearchCorpusSync(enabled: boolean): {
  syncing: boolean;
  itemsSynced: number;
} {
  const [state, setState] = useState({ syncing: false, itemsSynced: 0 });

  useEffect(() => {
    if (!enabled) {
      setState({ syncing: false, itemsSynced: 0 });
      return;
    }
    let cancelled = false;

    const poll = async () => {
      try {
        const list = await listConnectors();
        const connected = list.filter((c) => c.connected);
        if (connected.length === 0) {
          if (!cancelled) setState({ syncing: false, itemsSynced: 0 });
          return;
        }
        const results = await Promise.all(
          connected.map(async (c) => {
            try {
              return await getSyncStatus(c.connector_id);
            } catch {
              return null;
            }
          }),
        );
        let syncing = false;
        let itemsSynced = 0;
        for (const r of results) {
          if (!r) continue;
          if (r.state === 'syncing') syncing = true;
          itemsSynced += r.items_synced ?? 0;
        }
        if (!cancelled) setState({ syncing, itemsSynced });
      } catch {
        // Network blip — leave previous state intact.
      }
    };

    poll();
    const interval = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [enabled]);

  return state;
}

export function InputArea() {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // True from a successful mic transcription until the user edits the box
  // by hand (cleared in the textarea's onChange) or sends — used to gate
  // auto-speaking the reply to voice-initiated messages only.
  const voiceOriginatedRef = useRef(false);
  // Persists past voiceOriginatedRef's reset-at-send-time so the
  // continuous-conversation effect (which fires once the reply's audio
  // finishes, well after send) can still tell whether that exchange was
  // voice-initiated.
  const lastReplyWasVoiceRef = useRef(false);
  // Distinguishes a hands-free (wake-word / continuous-mode) recording from
  // a manual mic-button click, so only the hands-free path auto-stops on a
  // timeout and auto-sends — a manual stop always leaves the transcribed
  // text in the box for the user to review/edit, same as today.
  const autoTriggeredRef = useRef(false);
  const autoStopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Held from a wake-word trigger until listening has actually started, so
  // repeat detections of the same utterance can't stack up greetings.
  const wakeWordBusyRef = useRef(false);
  // True between Flux beginTurn() and its EndOfTurn. Flux never sets
  // useSpeech's `speechState`, so without this the orb would sit idle for a
  // whole spoken turn and the mic button would offer to start another.
  const [fluxTurnActive, setFluxTurnActive] = useState(false);
  const { speak: speakStreaming, stop: stopSpeaking } = useStreamingTts();
  // Guards against two sends for one turn if Deepgram repeats a final event.
  const lastFluxTurnRef = useRef<number | null>(null);

  const activeId = useAppStore((s) => s.activeId);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const streamState = useAppStore((s) => s.streamState);
  const messages = useAppStore((s) => s.messages);
  const speechEnabled = useAppStore((s) => s.settings.speechEnabled);
  const wakeWordGreetingEnabled = useAppStore((s) => s.settings.wakeWordGreetingEnabled);
  const fluxEnabled = useAppStore((s) => s.settings.fluxEnabled);
  const voiceRepliesEnabled = useAppStore((s) => s.settings.voiceRepliesEnabled);
  const fluxEagerEnabled = useAppStore((s) => s.settings.fluxEagerEnabled);
  // Flux replaces the local silence timer as the end-of-turn decision.
  // Declared here because handleMicClick, defined well above the Flux
  // hook, needs it too. With the toggle off nothing Flux-related runs.
  const fluxActive = speechEnabled && fluxEnabled;
  const maxTokens = useAppStore((s) => s.settings.maxTokens);
  const temperature = useAppStore((s) => s.settings.temperature);
  const createConversation = useAppStore((s) => s.createConversation);
  const addMessage = useAppStore((s) => s.addMessage);
  const updateLastAssistant = useAppStore((s) => s.updateLastAssistant);
  const setStreamState = useAppStore((s) => s.setStreamState);
  const resetStream = useAppStore((s) => s.resetStream);
  const modelLoading = useAppStore((s) => s.modelLoading);
  const deepResearch = useAppStore((s) => s.deepResearch);
  const setDeepResearch = useAppStore((s) => s.setDeepResearch);
  const corpusSync = useResearchCorpusSync(deepResearch);
  const isCurrentChatStreaming = streamState.isStreaming && streamState.conversationId === activeId;

  const {
    state: speechState,
    error: speechError,
    available: speechAvailable,
    startRecording,
    stopRecording,
  } = useSpeech();

  // Abort in-flight stream when the user switches models mid-generation.
  // This prevents errors from trying to continue a stream with a stale model.
  const prevModelRef = useRef(selectedModel);
  useEffect(() => {
    if (prevModelRef.current !== selectedModel && streamState.isStreaming) {
      abortRef.current?.abort();
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      resetStream();
      abortRef.current = null;
    }
    prevModelRef.current = selectedModel;
  }, [selectedModel, streamState.isStreaming, resetStream]);

  const micDisabled = !speechEnabled || !speechAvailable || streamState.isStreaming;
  const micReason: 'not-enabled' | 'no-backend' | 'streaming' | undefined =
    !speechEnabled ? 'not-enabled'
    : !speechAvailable ? 'no-backend'
    : streamState.isStreaming ? 'streaming'
    : undefined;

  useEffect(() => {
    if (speechError) {
      toast.error(speechError, { duration: 8000 });
    }
  }, [speechError]);

  // Mirror into the store so the orb (rendered in ChatArea, outside this
  // component) can react to mic activity without lifting useSpeech() up.
  // A Flux turn never touches useSpeech, so `speechState` stays 'idle' for
  // the whole utterance. Everything that asks "is the mic live right now?"
  // must consult this instead, or it will believe nothing is happening:
  // the orb sat idle, the mic button showed no active state, and — worst —
  // the wake word stayed armed and re-triggered on the user's own question
  // mid-turn.
  const effectiveSpeechState = fluxTurnActive ? 'recording' : speechState;

  useEffect(() => {
    useAppStore.getState().setVoiceState(effectiveSpeechState);
  }, [effectiveSpeechState]);

  const handleMicClick = useCallback(async () => {
    // A Flux turn leaves speechState 'idle', so checking it directly sent
    // every press down the "start" branch: the wake word was un-suspended
    // and a competing local recording began, which is why pressing the
    // button during a Flux turn could not pause anything.
    if (fluxTurnActive) {
      setWakeWordSuspended(true);
      toast('Listening paused — tap the mic again to talk.', { duration: 4000 });
      flux.endTurn();
      setFluxTurnActive(false);
      return;
    }
    if (speechState === 'recording') {
      // Stopping by hand also stops listening. Without this the button only
      // ended the current recording, the wake word re-armed a second later,
      // and a false trigger started another one — so pressing it repeatedly
      // appeared to do nothing at all.
      setWakeWordSuspended(true);
      toast('Listening paused — tap the mic again to talk.', { duration: 4000 });
      try {
        const text = await stopRecording();
        if (text) {
          setInput((prev) => (prev ? prev + ' ' + text : text));
          voiceOriginatedRef.current = true;
        }
      } catch {
        // Error is captured in useSpeech
      }
    } else {
      setWakeWordSuspended(false);
      if (fluxActive) {
        setFluxTurnActive(true);
        flux.beginTurn();
        return;
      }
      await startRecording();
    }
    // flux is stable across renders; fluxActive/fluxTurnActive drive it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [speechState, fluxActive, fluxTurnActive, startRecording, stopRecording]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [input]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    resetStream();
  }, [resetStream]);

  const sendMessage = useCallback(async (overrideContent?: string) => {
    const content = (overrideContent ?? input).trim();
    if (!content || streamState.isStreaming) return;
    if (!selectedModel) {
      toast.error('Pick a model first (⌘K)');
      return;
    }

    setInput('');
    const wasVoice = voiceOriginatedRef.current;
    voiceOriginatedRef.current = false;
    lastReplyWasVoiceRef.current = wasVoice;

    let convId = activeId;
    if (!convId) {
      convId = createConversation(selectedModel);
    }

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content,
      timestamp: Date.now(),
    };
    addMessage(convId, userMsg);

    // Build API messages before adding assistant placeholder.
    //
    // A turn that used tools is replayed as the model produced it: an
    // assistant tool-use message plus its tool results, not just the final
    // text. Sending only the text made prior turns look like questions
    // answered from nothing, and the model copied that shape — asked to
    // open an app a second time it reproduced the earlier "<app> has been
    // opened for you" verbatim, called no tool, and nothing opened. Results
    // are truncated because they are replayed on every later turn and some
    // tools return a lot; the shape is what matters here, not the detail.
    const currentMessages = useAppStore.getState().messages;
    const apiMessages: ChatRequest['messages'] = [];
    for (const m of currentMessages) {
      const calls = m.role === 'assistant' ? m.toolCalls ?? [] : [];
      if (calls.length > 0) {
        apiMessages.push({
          role: m.role,
          content: m.content,
          tool_calls: calls.map((tc) => ({
            id: tc.id,
            type: 'function',
            // `|| '{}'` rather than `?? '{}'`: an empty string is the case
            // that matters. Arguments are not always recoverable when the
            // server reports a tool call after the fact, and conversations
            // already saved with a blank value would otherwise keep sending
            // unparseable JSON — the model backend rejects the request and
            // every later message in that chat fails.
            function: { name: tc.tool, arguments: tc.arguments || '{}' },
          })),
        });
        for (const tc of calls) {
          apiMessages.push({
            role: 'tool',
            content: (tc.result ?? '').slice(0, 500),
            tool_call_id: tc.id,
            name: tc.tool,
          });
        }
      } else {
        apiMessages.push({ role: m.role, content: m.content });
      }
    }

    const assistantMsg: ChatMessage = {
      id: generateId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isResearch: deepResearch || undefined,
    };
    addMessage(convId, assistantMsg);

    // Start streaming
    const startTime = Date.now();
    const timer = setInterval(() => {
      setStreamState({ elapsedMs: Date.now() - startTime });
    }, 100);
    timerRef.current = timer;

    const controller = new AbortController();
    abortRef.current = controller;

    let accumulatedContent = '';
    let usage: TokenUsage | undefined;
    let complexity: { score: number; tier: string; suggested_max_tokens: number } | undefined;
    let audio: { url: string } | undefined;
    const toolCalls: ToolCallInfo[] = [];
    const researchTraces: ResearchSearchTrace[] = [];
    const researchSourcesByRef = new Map<number, ResearchSource>();
    const flushSources = () =>
      Array.from(researchSourcesByRef.values()).sort((a, b) => a.ref - b.ref);
    let lastFlush = 0;
    let ttftMs: number | undefined;

    setStreamState({
      conversationId: convId,
      isStreaming: true,
      phase: deepResearch ? 'Researching...' : 'Generating...',
      elapsedMs: 0,
      activeToolCalls: [],
      content: '',
    });
    useAppStore.getState().addLogEntry({
      timestamp: Date.now(),
      level: 'info',
      category: 'chat',
      message: deepResearch
        ? `Research: "${content.slice(0, 80)}${content.length > 80 ? '...' : ''}"`
        : `Request: "${content.slice(0, 80)}${content.length > 80 ? '...' : ''}" → ${selectedModel}`,
    });

    try {
      if (deepResearch) {
        for await (const ev of streamResearch(
          content,
          selectedModel,
          controller.signal,
        )) {
          if (ev.type === 'search_call') {
            const trace: ResearchSearchTrace = {
              id: generateId(),
              query: ev.arguments?.query ?? '',
              person: ev.arguments?.person,
              timeRange: ev.arguments?.time_range,
              status: 'pending',
            };
            researchTraces.push(trace);
            setStreamState({ phase: `Searching: ${trace.query}` });
            updateLastAssistant(
              convId,
              accumulatedContent,
              undefined,
              undefined,
              undefined,
              undefined,
              [...researchTraces],
              flushSources(),
            );
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(),
              level: 'info',
              category: 'tool',
              message: `Search: "${trace.query}"${trace.person ? ` (person: ${trace.person})` : ''}`,
            });
          } else if (ev.type === 'search_result') {
            const pending = [...researchTraces].reverse().find((t) => t.status === 'pending');
            if (pending) {
              pending.status = 'complete';
              pending.numHits = ev.num_hits;
              pending.topTitles = ev.top_titles;
            }
            if (ev.sources) {
              for (const src of ev.sources) {
                if (src && typeof src.ref === 'number' && !researchSourcesByRef.has(src.ref)) {
                  researchSourcesByRef.set(src.ref, src);
                }
              }
            }
            updateLastAssistant(
              convId,
              accumulatedContent,
              undefined,
              undefined,
              undefined,
              undefined,
              [...researchTraces],
              flushSources(),
            );
          } else if (ev.type === 'synthesis') {
            if (!ttftMs) ttftMs = Date.now() - startTime;
            accumulatedContent += ev.text;
            setStreamState({ content: accumulatedContent, phase: '' });
            const now = Date.now();
            if (now - lastFlush >= 80) {
              updateLastAssistant(
                convId,
                accumulatedContent,
                undefined,
                undefined,
                undefined,
                undefined,
                [...researchTraces],
                flushSources(),
              );
              lastFlush = now;
            }
          } else if (ev.type === 'system_metrics') {
            // Live GPU sample — feed straight to the System panel so Power
            // (W) and Energy (kJ) tick up in real time as the agent runs.
            useAppStore.getState().setLiveEnergy({
              power_w: ev.power_w,
              energy_j: ev.energy_j,
              duration_s: ev.duration_s,
            });
          } else if (ev.type === 'error') {
            // Backend setup/worker failure (Ollama down, planner model
            // missing, KnowledgeStore locked, etc.). Without surfacing the
            // message, the user sees only the generic "No response was
            // generated" fallback and has no way to self-diagnose.
            const msg = ev.message || 'Research failed (no detail provided)';
            accumulatedContent = accumulatedContent
              ? `${accumulatedContent}\n\n**Research stopped:** ${msg}`
              : `**Research failed:** ${msg}`;
            setStreamState({ content: accumulatedContent, phase: '' });
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(),
              level: 'error',
              category: 'chat',
              message: `Deep Research error: ${msg}`,
            });
            toast.error(msg, { duration: 8000 });
          } else if (ev.type === 'done') {
            if (ev.usage) {
              usage = {
                prompt_tokens: ev.usage.prompt_tokens ?? 0,
                completion_tokens: ev.usage.completion_tokens ?? 0,
                total_tokens:
                  ev.usage.total_tokens ??
                  (ev.usage.prompt_tokens ?? 0) +
                    (ev.usage.completion_tokens ?? 0),
              };
              // Optimistically roll this research turn into the session
              // counters so the Session panel updates the moment the
              // stream finishes, regardless of how /v1/savings aggregates
              // research telemetry server-side.
              useAppStore.getState().incrementSavings(usage);
            }
            // Hold the final live numbers visible for a beat so the panel
            // doesn't flash to 0 between the SSE close and the next
            // /v1/telemetry/energy poll picking up the persisted record.
            window.setTimeout(() => {
              useAppStore.getState().setLiveEnergy(null);
            }, 1500);
            break;
          }
        }
      } else {
      for await (const sseEvent of streamChat(
        {
          model: selectedModel,
          messages: apiMessages,
          stream: true,
          temperature,
          max_tokens: maxTokens,
          voice: wasVoice,
        },
        controller.signal,
      )) {
        const eventName = sseEvent.event;

        if (eventName === 'agent_turn_start') {
          setStreamState({ phase: 'Agent thinking...' });
        } else if (eventName === 'inference_start') {
          setStreamState({ phase: 'Generating...' });
          useAppStore.getState().addLogEntry({
            timestamp: Date.now(), level: 'info', category: 'chat',
            message: `Generating with ${selectedModel}...`,
          });
        } else if (eventName === 'tool_call_start') {
          try {
            const data = JSON.parse(sseEvent.data);
            const tc: ToolCallInfo = {
              id: generateId(),
              tool: data.tool,
              arguments: serializeToolCallArguments(data.arguments),
              status: 'running',
            };
            toolCalls.push(tc);
            setStreamState({
              phase: `Calling ${data.tool}...`,
              activeToolCalls: [...toolCalls],
            });
            updateLastAssistant(convId, accumulatedContent, [...toolCalls]);
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(), level: 'info', category: 'tool',
              message: `Calling ${data.tool}(${serializeToolCallArguments(data.arguments)})`,
            });
          } catch {}
        } else if (eventName === 'tool_call_end') {
          try {
            const data = JSON.parse(sseEvent.data);
            const tc = toolCalls.find(
              (t) => t.tool === data.tool && t.status === 'running',
            );
            if (tc) {
              tc.status = data.success ? 'success' : 'error';
              tc.latency = data.latency;
              tc.result = data.result;
              if (data.metadata && typeof data.metadata === 'object') {
                tc.metadata = data.metadata;
              }
            }
            setStreamState({
              phase: 'Generating...',
              activeToolCalls: [...toolCalls],
            });
            updateLastAssistant(convId, accumulatedContent, [...toolCalls]);
          } catch {}
        } else {
          try {
            const data = JSON.parse(sseEvent.data);
            const delta = data.choices?.[0]?.delta;
            if (data.usage) usage = data.usage;
            if (data.complexity) complexity = data.complexity;
            if (data.audio) audio = data.audio;
            if (delta?.content) {
              if (!ttftMs) ttftMs = Date.now() - startTime;
              accumulatedContent += delta.content;
              setStreamState({ content: accumulatedContent, phase: '' });

              const now = Date.now();
              if (now - lastFlush >= 80) {
                updateLastAssistant(
                  convId,
                  accumulatedContent,
                  toolCalls.length > 0 ? [...toolCalls] : undefined,
                );
                lastFlush = now;
              }
            }
            if (data.choices?.[0]?.finish_reason === 'stop') break;
          } catch {}
        }
      }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        // User cancelled or model switch — keep whatever was accumulated
        if (!accumulatedContent) accumulatedContent = '(Generation stopped)';
      } else {
        const errMsg = err?.message || String(err);
        accumulatedContent =
          accumulatedContent || `Error: ${errMsg}`;
        useAppStore.getState().addLogEntry({
          timestamp: Date.now(), level: 'error', category: 'chat',
          message: `Stream error: ${errMsg}`,
        });
      }
      // If we tore out mid-research, make sure the live System panel
      // numbers don't get stuck on the last sample.
      useAppStore.getState().setLiveEnergy(null);
    } finally {
      if (!accumulatedContent) {
        accumulatedContent = 'No response was generated. Please try again.';
      }
      const totalMs = Date.now() - startTime;
      const _CLOUD_PREFIXES = ['gpt-', 'o1-', 'o3-', 'o4-', 'claude-', 'gemini-', 'openrouter/', 'MiniMax-', 'chatgpt-'];
      const selectedOwner = useAppStore.getState().models.find((m) => m.id === selectedModel)?.owned_by;
      const engineLabel = selectedOwner === 'litellm'
        ? 'litellm'
        : _CLOUD_PREFIXES.some(p => selectedModel.startsWith(p)) ? 'cloud' : 'ollama';
      const telemetry: MessageTelemetry = {
        engine: engineLabel,
        model_id: selectedModel,
        total_ms: totalMs,
        ttft_ms: ttftMs,
        tokens_per_sec: usage?.completion_tokens
          ? usage.completion_tokens / (totalMs / 1000)
          : undefined,
        complexity_score: complexity?.score,
        complexity_tier: complexity?.tier,
        suggested_max_tokens: complexity?.suggested_max_tokens,
      };
      // Audio is only set when THIS response's agent actually produced it
      // (e.g. morning digest) — carried through the stream's finish event
      // rather than a separate post-hoc /api/digest probe, which used to
      // attach the last digest's audio to any unrelated message sent
      // afterward on the same day.
      //
      // Such audio always autoplays, unlike the synthesized voice-reply path
      // below which stays gated on `wasVoice`. The distinction: this audio is
      // something the agent deliberately produced as the point of the reply
      // (the digest is meant to be listened to, and is generated either way,
      // so withholding playback only adds a click), whereas TTS of an
      // ordinary typed answer would be reading text aloud that the user was
      // already reading.
      const audioMeta: { url: string; autoPlay?: boolean } | undefined = audio
        ? { url: audio.url, autoPlay: true }
        : undefined;

      updateLastAssistant(
        convId,
        accumulatedContent,
        toolCalls.length > 0 ? toolCalls : undefined,
        usage,
        telemetry,
        audioMeta,
        researchTraces.length > 0 ? researchTraces : undefined,
        researchSourcesByRef.size > 0 ? flushSources() : undefined,
      );
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      resetStream();
      useAppStore.getState().addLogEntry({
        timestamp: Date.now(), level: 'info', category: 'chat',
        message: `Response: ${accumulatedContent.length} chars`,
      });
      abortRef.current = null;

      // Voice replies and interactive morning digests use browser-side TTS
      // after the text is already visible. Fire-and-forget: this keeps the
      // slower media request out of the chat response's critical path.
      //
      // setAudioPlaying(true) here, ahead of the actual player mounting,
      // matters: resetStream() above already dropped isStreaming, which
      // re-enables the wake-word listener. Without staking this claim
      // immediately, there's an unguarded window between stream end and
      // the synthesized audio actually starting — long enough (a network
      // round trip to the TTS backend) for the wake word to hear ambient
      // noise, false-trigger, and start a new recording before the reply
      // has even started speaking.
      if (
        voiceRepliesEnabled &&
        shouldSynthesizeReplyAudio(
          wasVoice,
          content,
          Boolean(audio),
          accumulatedContent,
        )
      ) {
        useAppStore.getState().setAudioPlaying(true);
        // Streamed first: the batch endpoint returns nothing until the whole
        // clip exists (1.55s for a line, 6.92s for a paragraph, all silence),
        // while the stream starts speaking at about 0.41s. Streamed replies
        // are deliberately ephemeral — no file, so no replay control.
        speakStreaming(accumulatedContent)
          .then((spoke) => {
            if (spoke) return;
            // Nothing was heard, so falling back cannot repeat anything.
            return synthesizeSpeech(accumulatedContent).then((meta) => {
              updateLastAssistant(
                convId,
                accumulatedContent,
                undefined,
                undefined,
                undefined,
                { url: meta.url, autoPlay: true },
              );
            });
          })
          .catch(() => {
            // TTS failure for a voice reply shouldn't surface as a chat
            // error — the text answer already rendered fine.
            useAppStore.getState().setAudioPlaying(false);
          });
      }

      // Research path updates session counters optimistically from the
      // `done` event's usage payload — re-fetching here would overwrite
      // it with a potentially stale snapshot if the server's research
      // telemetry hasn't been merged into /v1/savings yet.
      if (!deepResearch) {
        fetchSavings()
          .then((data) => useAppStore.getState().setSavings(data))
          .catch(() => {});
      }
    }
  }, [
    input,
    activeId,
    selectedModel,
    streamState.isStreaming,
    createConversation,
    addMessage,
    updateLastAssistant,
    setStreamState,
    resetStream,
    deepResearch,
    temperature,
    maxTokens,
      speakStreaming,
      voiceRepliesEnabled,
  ]);

  // Hands-free stop: transcribes and sends immediately, unlike a manual
  // mic-click stop (which only populates the box for the user to review).
  const finishAutoRecording = useCallback(async () => {
    if (autoStopTimerRef.current) {
      clearTimeout(autoStopTimerRef.current);
      autoStopTimerRef.current = null;
    }
    try {
      const text = await stopRecording();
      if (text && text.trim()) {
        voiceOriginatedRef.current = true;
        await sendMessage(text);
      }
    } catch {
      // Error is captured in useSpeech
    }
  }, [stopRecording, sendMessage]);

  /**
   * Post an already-generated answer without streaming a new one.
   *
   * The latency win of Ultra mode is skipping generation entirely, so this
   * writes the pair straight into the conversation. Only ever called with
   * text the server released against a confirmed turn: speculation is
   * discarded there for anything tool-shaped, so this path never needs to
   * run a tool, and deliberately cannot.
   *
   * Returns false when it declines, so the caller falls back to the normal
   * streamed path rather than dropping the turn.
   */
  const releaseSpeculativeAnswer = useCallback(
    async (transcript: string, answer: string): Promise<boolean> => {
      if (streamState.isStreaming) return false;
      if (!selectedModel) return false;

      const convId = activeId ?? createConversation(selectedModel);
      const wasVoice = voiceOriginatedRef.current;
      voiceOriginatedRef.current = false;
      lastReplyWasVoiceRef.current = wasVoice;

      addMessage(convId, {
        id: generateId(),
        role: 'user',
        content: transcript,
        timestamp: Date.now(),
      });
      addMessage(convId, {
        id: generateId(),
        role: 'assistant',
        content: answer,
        timestamp: Date.now(),
      });

      // Same ordering as the streamed path: claim playback before the TTS
      // round trip, or the wake word re-arms into the gap and false-triggers
      // on ambient noise before the reply starts speaking.
      if (wasVoice && answer) {
        useAppStore.getState().setAudioPlaying(true);
        synthesizeSpeech(answer)
          .then((meta) => {
            updateLastAssistant(convId, answer, undefined, undefined, undefined, {
              url: meta.url,
              autoPlay: true,
            });
          })
          .catch(() => {
            useAppStore.getState().setAudioPlaying(false);
          });
      }
      return true;
    },
    [
      activeId,
      addMessage,
      createConversation,
      selectedModel,
      streamState.isStreaming,
      updateLastAssistant,
    ],
  );

  const handleFluxEndOfTurn = useCallback(
    async (transcript: string, turnIndex: number, speculativeAnswer?: string) => {
      setFluxTurnActive(false);
      // Deepgram can repeat a final event; one confirmed turn sends once.
      if (lastFluxTurnRef.current === turnIndex) return;
      lastFluxTurnRef.current = turnIndex;

      const text = (transcript || '').trim();
      if (!text) return;
      voiceOriginatedRef.current = true;

      // A released answer arrives only on a confirmed final, already checked
      // against this turn's identity and transcript server-side. If posting
      // it is declined for any reason, fall through and generate normally
      // rather than losing the turn.
      if (speculativeAnswer && speculativeAnswer.trim()) {
        const posted = await releaseSpeculativeAnswer(text, speculativeAnswer.trim());
        if (posted) return;
      }
      await sendMessage(text);
    },
    [releaseSpeculativeAnswer, sendMessage],
  );

  const handleFluxUnavailable = useCallback(
    async (reason: string, audio: Int16Array | null) => {
      setFluxTurnActive(false);
      toast.error(`Cloud transcription unavailable — using local. ${reason}`, {
        duration: 6000,
      });
      // Don't lose the utterance: whatever of the turn was captured is
      // transcribed locally rather than dropped.
      if (!audio || audio.length === 0) return;
      try {
        const wav = pcm16ToWav(audio, 16000);
        const result = await transcribeAudio(wav, 'flux-fallback.wav');
        const text = (result?.text || '').trim();
        if (text) {
          voiceOriginatedRef.current = true;
          await sendMessage(text);
        }
      } catch {
        toast.error('Local transcription of that turn failed.', { duration: 6000 });
      }
    },
    [sendMessage],
  );

  const flux = useFluxSpeech({
    enabled: fluxActive,
    eager: fluxEagerEnabled,
    onEndOfTurn: handleFluxEndOfTurn,
    onTurnResumed: () => {
      // The speaker carried on; the server has already discarded its
      // speculative work. Nothing was shown here, so nothing to undo.
    },
    onUnavailable: handleFluxUnavailable,
  });

  // Entry point for both the wake word and continuous-conversation re-arm.
  // Real silence detection (see useSpeech's VAD) stops the recording as soon
  // as the user finishes talking — the fixed 12s timer below is only a
  // fallback for the rare case VAD never sees speech at all (e.g. the
  // trigger was itself a false positive), not the normal way this ends.
  // Previously the timer WAS the only mechanism, so every turn waited out
  // the same multi-second pause no matter how short the question was.
  const beginAutoRecording = useCallback(async () => {
    if (micDisabled || effectiveSpeechState !== 'idle') return;
    autoTriggeredRef.current = true;
    if (fluxActive) {
      // Flux decides when the turn ends, so there is no 12s fallback timer
      // and no local recording to stop.
      setFluxTurnActive(true);
      flux.beginTurn();
      return;
    }
    await startRecording(finishAutoRecording);
    autoStopTimerRef.current = setTimeout(() => {
      finishAutoRecording();
    }, 12000);
  }, [micDisabled, speechState, startRecording, finishAutoRecording]);

  // Wake-word variant: acknowledge out loud, then listen. Only the wake word
  // does this — the continuous-conversation re-arm above stays silent, since
  // greeting after every reply would talk over an ongoing exchange.
  //
  // Strictly sequential: the greeting finishes before anything is recorded.
  // An earlier version overlapped the two and cut the greeting short as soon
  // as the user was heard, so a command said in one breath wasn't lost —
  // but that put Sage's voice and the user's in the same recording and made
  // the greeting's audibility depend on echo cancellation working well.
  // Waiting is the predictable trade: a beat of latency, in exchange for the
  // greeting always being heard in full and the recording holding only the
  // user. The microphone is still opened during the greeting (see
  // waitBeforeCapture), so speaking the instant it ends loses nothing.
  const beginWakeWordRecording = useCallback(async () => {
    // speechState only becomes 'recording' once the greeting has finished,
    // so for that whole window it still reads 'idle' and cannot by itself
    // keep a second trigger out. A ref closes the gap immediately, before
    // any await — one observed "Hey Sage" started three overlapping
    // greetings without it.
    if (micDisabled || effectiveSpeechState !== 'idle' || wakeWordBusyRef.current)
      return;
    wakeWordBusyRef.current = true;
    try {
      autoTriggeredRef.current = true;
      const greeting = wakeWordGreetingEnabled
        ? playGreeting({
            onFailure: (reason) =>
              toast.error(`Greeting didn't play — ${reason}`, { duration: 8000 }),
          })
        : undefined;
      if (fluxActive) {
        // Marked busy before awaiting the greeting, not after: this is what
        // disarms the wake word, and leaving it armed through the greeting
        // let a second detection of the same "Hey Sage" through.
        setFluxTurnActive(true);
        // Same sequential contract as the local path: the greeting finishes
        // before any audio is transmitted, so Sage's own voice never enters
        // the turn Deepgram is judging.
        if (greeting) await greeting;
        flux.beginTurn();
        return;
      }
      await startRecording(finishAutoRecording, { waitBeforeCapture: greeting });
      autoStopTimerRef.current = setTimeout(() => {
        finishAutoRecording();
      }, 12000);
    } finally {
      // By now startRecording has set speechState to 'recording', so the
      // ordinary guard above takes over from here.
      wakeWordBusyRef.current = false;
    }
  }, [micDisabled, speechState, startRecording, finishAutoRecording, wakeWordGreetingEnabled]);

  useEffect(() => {
    if (speechState !== 'recording' && autoStopTimerRef.current) {
      clearTimeout(autoStopTimerRef.current);
      autoStopTimerRef.current = null;
    }
  }, [speechState]);

  const wakeWordEnabled = useAppStore((s) => s.settings.wakeWordEnabled);

  // Fetch and decode the clips while the wake word is merely armed, so the
  // first trigger doesn't pay for the download at the moment it matters.
  useEffect(() => {
    if (wakeWordEnabled && wakeWordGreetingEnabled) preloadGreetings();
  }, [wakeWordEnabled, wakeWordGreetingEnabled]);
  const continuousConversationEnabled = useAppStore((s) => s.settings.continuousConversationEnabled);
  const audioPlaying = useAppStore((s) => s.audioPlaying);
  const wasAudioPlayingRef = useRef(false);

  // Stays false for a beat after audio stops playing, before the
  // wake-word listener is allowed to re-arm. !audioPlaying alone wasn't
  // enough: a recorded live test showed the wake word false-triggering
  // within ~1s of every single voice reply ending, consistently, with
  // no real speech following — most likely the playback-cutoff
  // click/pop, or the TTS voice's own tail bleeding through imperfect
  // echo cancellation. Each false trigger resolves itself (vad_filter
  // correctly finds silence, so nothing gets sent) but the listener
  // re-arms and re-triggers again moments later, indefinitely — from
  // the user's side this looked exactly like "the mic won't turn off."
  const [wakeWordSettled, setWakeWordSettled] = useState(true);
  const settleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Set when the user stops a recording by hand, so the wake word does not
  // immediately re-arm behind them. Cleared when they start one deliberately,
  // or when the Settings toggle is touched — otherwise pausing here would
  // silently outlive the switch that is supposed to control it.
  const [wakeWordSuspended, setWakeWordSuspended] = useState(false);

  // Never transmit while Sage is speaking. Echo cancellation is imperfect,
  // and Sage's own reply reaching Deepgram would be transcribed as the
  // user's next turn — the same failure the wake-word gating exists for.
  useEffect(() => {
    if (audioPlaying && fluxTurnActive) {
      flux.endTurn();
      setFluxTurnActive(false);
    }
    // flux.endTurn is stable; re-running on the flag alone is intended.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioPlaying, fluxTurnActive]);

  useEffect(() => {
    if (settleTimerRef.current) {
      clearTimeout(settleTimerRef.current);
      settleTimerRef.current = null;
    }
    if (audioPlaying) {
      setWakeWordSettled(false);
      return;
    }
    settleTimerRef.current = setTimeout(() => {
      setWakeWordSettled(true);
      settleTimerRef.current = null;
    }, 1200);
    return () => {
      if (settleTimerRef.current) {
        clearTimeout(settleTimerRef.current);
        settleTimerRef.current = null;
      }
    };
  }, [audioPlaying]);

  const { error: wakeWordError } = useWakeWord(
    beginWakeWordRecording,
    // !audioPlaying matters as much as speechState === 'idle' here:
    // speechState returns to 'idle' as soon as transcription finishes,
    // well before a reply is generated or its voice playback finishes.
    // Without this, the wake-word mic starts listening again while Sage's
    // own TTS reply is still playing through the speakers — echo
    // cancellation isn't perfect, so it can hear (and re-trigger on)
    // itself, independent of any toggle. wakeWordSettled adds the
    // post-playback cooldown described above.
    wakeWordEnabled &&
      !wakeWordSuspended &&
      !micDisabled &&
      effectiveSpeechState === 'idle' &&
      !audioPlaying &&
      wakeWordSettled,
  );

  // The Settings switch is the authority: flipping it either way ends a
  // pause started from the mic button, so the two controls cannot disagree
  // about whether Sage is listening.
  useEffect(() => {
    setWakeWordSuspended(false);
  }, [wakeWordEnabled]);

  useEffect(() => {
    if (wakeWordError) {
      toast.error(`Wake word: ${wakeWordError}`, { duration: 8000 });
    }
  }, [wakeWordError]);

  // Re-arms listening once a voice-initiated reply finishes actually being
  // spoken (not just when the text/stream finishes) — matches the real
  // pace of the conversation instead of jumping in over Sage.
  useEffect(() => {
    const wasPlaying = wasAudioPlayingRef.current;
    wasAudioPlayingRef.current = audioPlaying;
    if (
      wasPlaying &&
      !audioPlaying &&
      continuousConversationEnabled &&
      lastReplyWasVoiceRef.current &&
      !micDisabled &&
      speechState === 'idle'
    ) {
      beginAutoRecording();
    }
  }, [audioPlaying, continuousConversationEnabled, micDisabled, speechState, beginAutoRecording]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="px-4 pb-4 pt-2" style={{ maxWidth: 'var(--chat-max-width)', margin: '0 auto', width: '100%' }}>
      <div className="mb-2 flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setDeepResearch(!deepResearch)}
            disabled={streamState.isStreaming}
            aria-pressed={deepResearch}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-colors cursor-pointer disabled:cursor-default disabled:opacity-50"
            style={{
              background: deepResearch ? 'var(--color-accent-subtle)' : 'transparent',
              border: `1px solid ${deepResearch ? 'var(--color-accent)' : 'var(--color-border)'}`,
              color: deepResearch ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
            }}
            title={deepResearch ? 'Deep Research: on' : 'Deep Research: off'}
          >
            <Search size={12} />
            Deep Research
          </button>
        </div>
        {deepResearch && corpusSync.syncing && corpusSync.itemsSynced > 0 && (
          <div
            className="text-[11px] leading-snug"
            style={{ color: 'var(--color-text-tertiary)' }}
          >
            Searching over{' '}
            <span key={corpusSync.itemsSynced} className="sync-bump" style={{ color: 'var(--color-text-secondary)' }}>
              {corpusSync.itemsSynced.toLocaleString()}
            </span>{' '}
            items — sync in progress, results will improve as more data is indexed.
          </div>
        )}
      </div>
      <div
        className="flex items-center gap-2 rounded-2xl px-4 py-3 transition-shadow"
        style={{
          background: 'var(--color-input-bg)',
          border: '1px solid var(--color-input-border)',
          boxShadow: 'var(--shadow-sm)',
        }}
      >
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            voiceOriginatedRef.current = false;
          }}
          onKeyDown={handleKeyDown}
          placeholder={selectedModel ? 'Message Sage...' : 'Pick a model first (⌘K)...'}
          rows={1}
          className="flex-1 bg-transparent outline-none resize-none text-sm leading-relaxed"
          style={{ color: 'var(--color-text)', maxHeight: '200px' }}
          disabled={streamState.isStreaming || modelLoading}
        />
        {isCurrentChatStreaming ? (
          <button
            onClick={stopStreaming}
            className="p-2 rounded-xl transition-colors shrink-0 cursor-pointer"
            style={{ background: 'var(--color-error)', color: 'var(--color-on-accent)' }}
            title="Stop generating"
          >
            <Square size={16} />
          </button>
        ) : (
          <div className="flex items-center gap-1">
            {audioPlaying && (
              <button
                onClick={stopSpeaking}
                title="Stop speaking"
                aria-label="Stop speaking"
                className="p-2 rounded-xl transition-colors shrink-0 cursor-pointer"
                style={{
                  background: 'var(--color-bg-tertiary)',
                  color: 'var(--color-accent)',
                }}
              >
                <VolumeX size={16} />
              </button>
            )}
            <MicButton
              state={effectiveSpeechState}
              onClick={handleMicClick}
              disabled={micDisabled}
              reason={micReason}
            />
            <button
              onClick={() => sendMessage()}
              disabled={streamState.isStreaming || !input.trim() || modelLoading || !selectedModel}
              title={selectedModel ? 'Send message' : 'Pick a model first (⌘K)'}
              className="p-2 rounded-xl transition-colors shrink-0 cursor-pointer disabled:opacity-30 disabled:cursor-default"
              style={{
                background: input.trim() ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                color: input.trim() ? 'white' : 'var(--color-text-tertiary)',
              }}
            >
              <Send size={16} />
            </button>
          </div>
        )}
      </div>
      <div className="flex items-center justify-center mt-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
        <span>
          <kbd className="font-mono">Enter</kbd> to send &middot;{' '}
          <kbd className="font-mono">Shift+Enter</kbd> for new line
        </span>
      </div>
    </div>
  );
}
