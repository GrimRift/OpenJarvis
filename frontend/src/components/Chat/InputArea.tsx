import { useState, useRef, useCallback, useEffect } from 'react';
import { Send, Square, Paperclip, Search, VolumeX, Volume2, X } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore, generateId, documentsFor } from '../../lib/store';
import {
  MAX_IMAGES,
  imageFilesFrom,
  planAttachments,
  type AttachedImage,
} from '../../lib/image-attach';
import { apiFetch } from '../../lib/api';
import { streamChat, streamResearch } from '../../lib/sse';
import {
  diagramMode,
  isCloseDiagramCommand,
  mayBecomeCloseDiagramCommand,
} from '../../lib/diagram';
import {
  openDiagramFromAnswer,
  useDiagramPresenter,
} from '../../lib/diagram-presenter';
import type { ChatRequest } from '../../lib/sse';
import {
  fetchSavings,
  getBase,
  synthesizeSpeech,
  transcribeAudio,
  attachDocument,
  ingestDocument,
} from '../../lib/api';
import { playFiller, playGreeting, preloadGreetings } from '../../lib/greeting';
import {
  GREETING_GIVE_UP_MS,
  GREETING_PAUSE_MS,
  PHRASE_MS,
  PRE_ROLL_MS,
  continuesPastWakePhrase,
  greetingDelayMs,
  isOnlyWakePhrase,
  stripWakePhrase,
} from '../../lib/wake-follow';
import { fillerDue, initialFillerState, nextFillerCheckMs } from '../../lib/filler';
import {
  type BargeVerdict,
  describeVerdict,
  INTERRUPTED_MARK,
  isEchoTurn,
  isStopCommand,
  judge,
} from '../../lib/barge-in';
import { listConnectors, getSyncStatus } from '../../lib/connectors-api';
import { serializeToolCallArguments } from '../../lib/tool-call';
import {
  isDigestPrompt,
  pushSpokenDelta,
  shouldStreamReplySpeech,
  shouldSynthesizeReplyAudio,
  speakableText,
} from '../../lib/audio-policy';
import { shouldFlushStreamRender } from '../../lib/stream-render-policy';
import { getVoiceProfile } from '../../lib/voice-profiles';
import { useStreamingTts } from '../../hooks/useStreamingTts';
import { MicButton } from './MicButton';
import { useSpeech } from '../../hooks/useSpeech';
import { useWakeWord } from '../../hooks/useWakeWord';
import { turnSurvivesStatus, useFluxSpeech } from '../../hooks/useFluxSpeech';
import {
  CONTINUATION_WINDOW_MS,
  isContinuation,
  mergeTurns,
} from '../../lib/turn-continuation';
import { setVoiceTraceSink, voiceTrace } from '../../lib/voice-trace';
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
// How long a turn may hear nothing at all before the microphone is
// released: a wake word that fired on noise is the case this exists for.
// Two settings, one for after "Hey Sage" and one for the follow-up window
// after a reply, so the second can be more relaxed than the first.
function listenMs(kind: 'wake' | 'followUp'): number {
  const s = useAppStore.getState().settings;
  return (kind === 'wake' ? s.wakeWordListenSeconds : s.continuousListenSeconds) * 1000;
}

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

/**
 * `voiceOnly` renders just the controls — Deep Research, microphone, and a
 * speech mute — with no text field, send button or keyboard hint. The voice
 * page uses it so that surface stays purely spoken while still driving the
 * same wake word, transcription, sending and streaming-speech logic that
 * lives in this component.
 */
export function InputArea({ voiceOnly = false }: { voiceOnly?: boolean } = {}) {
  const [input, setInput] = useState('');
  // Ephemeral: images ride one request, show as a thumbnail while the tab is
  // open, and are stripped before conversations reach localStorage.
  const [attachments, setAttachments] = useState<AttachedImage[]>([]);
  // Documents ride the conversation as text. Ephemeral by default: a one-off
  // file must not silently join the searchable corpus, which is what the
  // Data Sources page is for.
  const [documents, setDocuments] = useState<
    { id: string; name: string; text: string; note: string; indexed: boolean }[]
  >([]);
  const [docBusy, setDocBusy] = useState<string>('');
  const [docIndexed, setDocIndexed] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  // Read inside a stable callback, so attaching a second image sees the first
  // instead of a closure captured when the component mounted.
  const visionUseLocal = useAppStore((st) => st.settings.visionUseLocal);
  const visionLocalModel = useAppStore((st) => st.settings.visionLocalModel);
  const attachmentsRef = useRef<AttachedImage[]>([]);
  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);
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
  // Barge-in (lib/barge-in.ts). While a voice reply plays the Flux turn is
  // kept open so the user can talk over it; `bargeTriggeredRef` records
  // that this turn cut the reply, which is what separates the user's
  // end-of-turn from an echo's.
  const bargeListeningRef = useRef(false);
  /** The Flux turn that was only "close the diagram", so it never becomes a
   * message and never counts as an interruption. */
  const diagramCommandTurnRef = useRef<number | null>(null);
  const bargeTriggeredRef = useRef(false);
  // Everything sent to the synthesiser for the reply now playing, so a
  // turn made only of Sage's own words is known for the echo it is.
  const spokenTextRef = useRef('');
  // The last verdict on the open turn, so its end can be logged with a reason.
  const bargeVerdictRef = useRef<BargeVerdict | null>(null);
  const interruptedRef = useRef(false);
  // Distinguishes a hands-free (wake-word / continuous-mode) recording from
  // a manual mic-button click, so only the hands-free path auto-stops on a
  // timeout and auto-sends — a manual stop always leaves the transcribed
  // text in the box for the user to review/edit, same as today.
  const autoTriggeredRef = useRef(false);
  const autoStopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Flux has no equivalent of the local 12s fallback: Deepgram only ends
  // turns it has started, so a wake word that fires on noise produces no
  // events at all and the microphone stays live indefinitely. This releases
  // it when nothing is ever heard, and is cancelled the moment Deepgram
  // reports real speech so a long question is never cut short.
  const fluxSilenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Held from a wake-word trigger until listening has actually started, so
  // repeat detections of the same utterance can't stack up greetings.
  const wakeWordBusyRef = useRef(false);
  // True between Flux beginTurn() and its EndOfTurn. Flux never sets
  // useSpeech's `speechState`, so without this the orb would sit idle for a
  // whole spoken turn and the mic button would offer to start another.
  const [fluxTurnActive, setFluxTurnActive] = useState(false);
  const {
    begin: beginStreamingSpeech,
    speak: speakStreaming,
    stop: stopSpeaking,
  } = useStreamingTts();
  // Guards against two sends for one turn if Deepgram repeats a final event.
  const lastFluxTurnRef = useRef<number | null>(null);
  // Turn continuation (see lib/turn-continuation.ts). After a Flux turn is
  // submitted the microphone keeps transmitting until Sage's audio starts or
  // the window lapses; speech in that window is the rest of the sentence.
  const continuationRef = useRef<{ submittedAt: number | null; text: string }>({
    submittedAt: null,
    text: '',
  });
  const continuingRef = useRef(false);
  const continuationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The executing end-of-turn handler holds a stale sendMessage after the
  // abort re-renders; the ref always points at the current one.
  const sendMessageRef = useRef<(overrideContent?: string) => Promise<void>>(
    async () => {},
  );

  const activeId = useAppStore((s) => s.activeId);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const streamState = useAppStore((s) => s.streamState);
  const messages = useAppStore((s) => s.messages);
  const speechEnabled = useAppStore((s) => s.settings.speechEnabled);
  const diagramsEnabled = useAppStore((s) => s.settings.diagramsEnabled);
  const micBoost = useAppStore((s) => s.settings.micBoost);
  const noiseSuppression = useAppStore((s) => s.settings.noiseSuppression);
  const diagramsAutomatic = useAppStore((s) => s.settings.diagramsAutomatic);
  const wakeWordGreetingEnabled = useAppStore((s) => s.settings.wakeWordGreetingEnabled);
  const wakeWordFastFollow = useAppStore((s) => s.settings.wakeWordFastFollow);
  // One-breath wake word (lib/wake-follow.ts): the turn opened straight
  // from the wake phrase, greeting deferred until a pause. `greeted` keeps
  // the pause greeting to one per wake, whichever path notices the pause
  // first (the timer, or an end-of-turn holding only the phrase).
  const fastFollowRef = useRef<{ active: boolean; greeted: boolean; startedAt: number }>({
    active: false,
    greeted: false,
    startedAt: 0,
  });
  const greetingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const takeRecentAudioRef = useRef<((ms: number) => Int16Array) | null>(null);
  const ambientRmsRef = useRef<(() => number) | null>(null);
  const clearGreetingTimer = () => {
    if (greetingTimerRef.current) {
      clearTimeout(greetingTimerRef.current);
      greetingTimerRef.current = null;
    }
  };
  const fluxEnabled = useAppStore((s) => s.settings.fluxEnabled);
  const voiceRepliesEnabled = useAppStore((s) => s.settings.voiceRepliesEnabled);
  const speakTypedReplies = useAppStore((s) => s.settings.speakTypedReplies);
  const ttsVoiceId = useAppStore((s) => s.settings.ttsVoiceId);
  const ttsVoice = getVoiceProfile(ttsVoiceId);
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
      clearFluxSilenceTimer();
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
        clearContinuationWindow();
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

  const attachFiles = useCallback(
    async (files: File[]) => {
      if (!files.length) return;
      const { accepted, rejected } = planAttachments(
        attachmentsRef.current,
        files.map((f) => ({ name: f.name, type: f.type, size: f.size })),
      );
      if (rejected) toast.error(rejected);
      if (!accepted.length) return;

      const read = await Promise.all(
        accepted.map(
          (index) =>
            new Promise<AttachedImage | null>((resolve) => {
              const file = files[index];
              const reader = new FileReader();
              reader.onload = () =>
                resolve({
                  id: generateId(),
                  dataUrl: String(reader.result || ''),
                  name: file.name,
                  bytes: file.size,
                });
              // A failed read is one image, not the message: drop it and carry
              // on rather than rejecting the whole attach.
              reader.onerror = () => resolve(null);
              reader.readAsDataURL(file);
            }),
        ),
      );
      const usable = read.filter((image): image is AttachedImage => image !== null);
      if (usable.length < accepted.length) toast.error('Some images could not be read');
      if (usable.length) setAttachments((prev) => [...prev, ...usable].slice(0, MAX_IMAGES));
    },
    [],
  );

  const handlePaste = useCallback(
    (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const files = imageFilesFrom(Array.from(event.clipboardData?.items || []));
      if (!files.length) return;
      // Only claim the paste when it actually carried an image, so pasting
      // text into the composer still behaves normally.
      event.preventDefault();
      void attachFiles(files);
    },
    [attachFiles],
  );

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setDragActive(false);
      const files = Array.from(event.dataTransfer?.files || []);
      void attachFiles(files);
    },
    [attachFiles],
  );

  const handleDocument = useCallback(
    async (file: File) => {
      // Blocking, and it says why. A garbled paper is re-read page by page,
      // measured at ~4 minutes for fifteen pages, and a silent spinner that
      // long reads as a hang.
      setDocBusy(`Reading ${file.name}…`);
      try {
        const doc = await attachDocument(file);
        if (docIndexed) {
          setDocBusy(`Indexing ${file.name}…`);
          await ingestDocument(file);
        }
        setDocuments((prev) => [
          ...prev,
          {
            id: generateId(),
            name: doc.filename,
            text: doc.text,
            note: doc.note,
            indexed: docIndexed,
          },
        ]);
        if (doc.pages_reread > 0) {
          toast.success(
            `${doc.filename}: ${doc.pages_reread} page(s) re-read because the text came out garbled`,
          );
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Could not read that file');
      } finally {
        setDocBusy('');
      }
    },
    [docIndexed],
  );

  const removeDocument = useCallback((id: string) => {
    setDocuments((prev) => prev.filter((d) => d.id !== id));
  }, []);

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((image) => image.id !== id));
  }, []);

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

    // Taken before the state clears, so a slow request cannot lose them.
    const outgoingImages = attachments.map((a) => a.dataUrl);
    setAttachments([]);
    // An image turn can go to a local vision model instead, so the picture
    // never leaves the machine. Otherwise it rides the model already selected.
    const turnModel =
      outgoingImages.length && visionUseLocal && visionLocalModel
        ? visionLocalModel
        : selectedModel;

    // The whole document goes in, by the user's choice over truncating it.
    // Taken before the state clears so a slow request cannot lose it.
    const outgoingDocs = documents;
    setDocuments([]);
    const documentPreamble = outgoingDocs
      .map((d) => `[Attached document: ${d.name}]\n${d.text}`)
      .join('\n\n');
    const contentWithDocs = documentPreamble
      ? `${documentPreamble}\n\n${content}`
      : content;

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content,
      timestamp: Date.now(),
      images: outgoingImages.length ? outgoingImages : undefined,
      documents: outgoingDocs.length
        ? outgoingDocs.map((d) => ({ name: d.name, text: d.text }))
        : undefined,
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
        // From the session map: the store strips documents before
        // persisting, exactly as it does images.
        const docs = m.documents ?? documentsFor(m.id) ?? [];
        apiMessages.push({
          role: m.role,
          // The bubble shows what was typed; the model gets the attached
          // document text in front of it, replayed from history on every
          // later turn -- unlike images below, because a document is attached
          // in order to be discussed.
          //
          // Framed so it cannot be mistaken for a filename to go and open.
          // `[Attached document: name]` read as an instruction to fetch the
          // file: the model called file_read, got "File not found:
          // C:\\AI\\<name>", and answered that it had no file -- with the
          // whole text sitting in the same message. Captured from a real
          // request while chasing exactly that report.
          content: docs.length
            ? `${docs
                .map(
                  (d) =>
                    `--- BEGIN ATTACHED DOCUMENT: ${d.name} ---\n${d.text}\n\n--- END ATTACHED DOCUMENT ---`,
                )
                .join('\n\n')}\n\n${m.content}`
            : m.content,
          // Only this turn's images. Replaying earlier ones would re-send
          // megabytes and re-bill them on every later message.
          images:
            m.id === userMsg.id && outgoingImages.length
              ? outgoingImages
              : undefined,
        });
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
    // Set when the turn ends because the user pressed Stop (or switched
    // model). Speaking a reply the user just interrupted is the opposite
    // of what Stop means, and the batch fallback that did it also mounted
    // a player for a clip that could not load -- the slider that appears
    // and vanishes.
    let userStopped = false;
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
    // Voice replies -- and typed ones when Speak Typed Replies is on --
    // stream raw model deltas to the server while the exact same deltas
    // continue into chat/history below. Digest prompts are excluded because
    // that agent may return its own ready-made audio. A typed reply stops
    // being spoken at the spoken limit; a voice reply is read in full.
    const incrementalSpeech =
      voiceRepliesEnabled &&
      shouldStreamReplySpeech(wasVoice, content, speakTypedReplies)
        ? beginStreamingSpeech(ttsVoice)
        : null;
    spokenTextRef.current = '';
    // "One moment, sir." when nothing has been said back for a while,
    // generating or waiting on a tool alike, then "still working on it"
    // every twenty seconds of continued silence. The clip is plain audio,
    // never `audioPlaying` (see playFiller). While a clip is playing the
    // answer's speech is held -- the text still streams on screen -- so the
    // clip is heard whole rather than talked over by its own reply.
    const filler = initialFillerState(incrementalSpeech !== null, Date.now());
    let fillerTimer: ReturnType<typeof setTimeout> | null = null;
    let fillerPlaying: Promise<void> | null = null;
    let spokenChars = 0;
    const heldDeltas: string[] = [];
    const speakNow = (delta: string) => {
      if (!incrementalSpeech) return;
      spokenChars = pushSpokenDelta(spokenChars, delta, !wasVoice, incrementalSpeech);
      spokenTextRef.current += delta;
    };
    const speakDelta = (delta: string) => {
      if (fillerPlaying) heldDeltas.push(delta);
      else speakNow(delta);
    };
    const releaseHeld = () => {
      fillerPlaying = null;
      for (const delta of heldDeltas.splice(0)) speakNow(delta);
    };
    const scheduleFiller = () => {
      if (!filler.spoken) return;
      fillerTimer = setTimeout(() => {
        fillerTimer = null;
        const due = fillerDue(filler, Date.now());
        if (!due) return;
        filler.lastFillerAt = Date.now();
        useAppStore.getState().addLogEntry({
          timestamp: Date.now(), level: 'info', category: 'voice',
          message: due === 'again' ? 'Sage (aloud): still working on it' : 'Sage (aloud): one moment',
        });
        fillerPlaying = playFiller({ voiceId: ttsVoice.id, again: due === 'again' }).then(
          releaseHeld,
          releaseHeld,
        );
        scheduleFiller();
      }, nextFillerCheckMs(filler, Date.now()));
    };
    scheduleFiller();
    const noteContentStarted = () => {
      filler.contentStarted = true;
    };
    const endFiller = () => {
      filler.ended = true;
      if (fillerTimer) {
        clearTimeout(fillerTimer);
        fillerTimer = null;
      }
    };
    // The answer's speech, finished only after a clip still playing has
    // ended and the words held behind it have gone out.
    const finishSpeech = () => {
      if (!incrementalSpeech) return undefined;
      if (!fillerPlaying) return incrementalSpeech.finish();
      return fillerPlaying.then(() => {
        releaseHeld();
        return incrementalSpeech.finish();
      });
    };

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
      message: `You${wasVoice ? ' (voice)' : ''}: ${content.replace(/\s+/g, ' ').slice(0, 240)}`,
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
          } else if (ev.type === 'web_search_call') {
            toolCalls.push({
              id: generateId(),
              tool: 'web_search',
              arguments: serializeToolCallArguments(ev.arguments),
              status: 'running',
            });
            setStreamState({
              phase: `Searching the web: ${ev.arguments?.query ?? ''}`,
              activeToolCalls: [...toolCalls],
            });
          } else if (ev.type === 'web_search_result') {
            const pending = [...toolCalls]
              .reverse()
              .find((call) => call.tool === 'web_search' && call.status === 'running');
            if (pending) {
              pending.status = ev.success === false ? 'error' : 'success';
              pending.metadata = {
                num_results: ev.num_results,
                sources: ev.sources ?? [],
                images: ev.images ?? [],
                explicit_image_search: ev.explicit_image_search ?? false,
              };
            }
            updateLastAssistant(
              convId,
              accumulatedContent,
              [...toolCalls],
              undefined,
              undefined,
              undefined,
              [...researchTraces],
              flushSources(),
            );
          } else if (ev.type === 'synthesis') {
            if (!ttftMs) ttftMs = Date.now() - startTime;
            accumulatedContent += ev.text;
            noteContentStarted();
            speakDelta(ev.text);
            const now = Date.now();
            if (
              shouldFlushStreamRender(now, lastFlush, accumulatedContent.length)
            ) {
              setStreamState({ content: accumulatedContent, phase: '' });
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
          model: turnModel,
          messages: apiMessages,
          stream: true,
          temperature,
          max_tokens: maxTokens,
          voice: wasVoice,
          diagrams: diagramMode(diagramsEnabled, diagramsAutomatic),
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
        } else if (eventName === 'text_retract') {
          // The model wrote this in the same round as a tool call: a
          // preamble, not the answer. Take it off the message (the answer
          // after the tool restates it) and let it stand as the status line
          // while the tool runs. Speech already synthesised from it cannot
          // be unsaid; that is the one limit here.
          try {
            const data = JSON.parse(sseEvent.data);
            const chars = Number(data.chars ?? 0);
            if (chars > 0 && accumulatedContent.endsWith(String(data.text ?? '').slice(-chars))) {
              accumulatedContent = accumulatedContent.slice(0, -chars);
              updateLastAssistant(convId, accumulatedContent, [...toolCalls]);
            }
            const said = String(data.text ?? '').trim();
            if (said) setStreamState({ phase: said.length > 80 ? `${said.slice(0, 77)}...` : said });
          } catch {
            /* skip */
          }
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
            const preview = String(data.result ?? '')
              .replace(/\s+/g, ' ')
              .slice(0, 160);
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(),
              level: data.success ? 'info' : 'warn',
              category: 'tool',
              message:
                `${data.tool} → ${data.success ? 'ok' : 'failed'}` +
                (typeof data.latency === 'number' ? ` in ${data.latency.toFixed(1)}s` : '') +
                (preview ? `: ${preview}` : ''),
            });
          } catch {}
        } else {
          try {
            const data = JSON.parse(sseEvent.data);
            const delta = data.choices?.[0]?.delta;
            if (data.usage) usage = data.usage;
            if (data.complexity) complexity = data.complexity;
            if (data.audio) audio = data.audio;
            if (data.audio) incrementalSpeech?.cancel();
            if (delta?.content) {
              if (!ttftMs) ttftMs = Date.now() - startTime;
              accumulatedContent += delta.content;
              noteContentStarted();
              speakDelta(delta.content);

              const now = Date.now();
              if (
                shouldFlushStreamRender(
                  now,
                  lastFlush,
                  accumulatedContent.length,
                )
              ) {
                setStreamState({ content: accumulatedContent, phase: '' });
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
        userStopped = true;
        if (!accumulatedContent) accumulatedContent = '(Generation stopped)';
        else if (interruptedRef.current) {
          // Cut by the user talking over it. The mark stays in the text the
          // model sees next turn, so "go on" continues from here.
          accumulatedContent += INTERRUPTED_MARK;
        }
        interruptedRef.current = false;
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
      // A stopped or failed turn must not say "one moment" afterwards.
      endFiller();
      // First, before anything that can throw.
      //
      // `isStreaming` feeds `micDisabled`, so a turn that leaves it set
      // disables the microphone and the wake word can never re-arm -- for the
      // rest of the session, recoverable only by reloading the page. This
      // reset used to sit some fifty lines further down, behind
      // `updateLastAssistant`, `flushSources()` and the telemetry maths: one
      // unusual message throwing anywhere in there took the wake word with
      // it, which is exactly the "it stopped re-arming after a while" report.
      // Bookkeeping is worth doing, but never at the cost of the microphone.
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      resetStream();
      abortRef.current = null;

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
      useAppStore.getState().addLogEntry({
        timestamp: Date.now(), level: 'info', category: 'chat',
        message: `Sage: ${accumulatedContent.replace(/\s+/g, ' ').slice(0, 240)}${
          accumulatedContent.length > 240 ? '…' : ''
        } (${accumulatedContent.length} chars)`,
      });

      const incrementalSpeechResult = finishSpeech();

      // Voice replies and interactive morning digests use browser-side TTS
      // after the text is already visible. Fire-and-forget: this keeps the
      // slower media request out of the chat response's critical path.
      //
      // Incremental playback deliberately makes no optimistic audio claim:
      // the Flux turn remains active while generation/TTS is pending, and the
      // streaming hook marks audioPlaying only when Cartesia announces the
      // first real PCM. This keeps the orb truthful without re-arming the mic.
      // A diagram shows itself only when Sage is about to say it aloud. With
      // replies muted, or a typed reply that is not spoken, it stays a card
      // in the chat waiting to be clicked -- nothing leaps over the screen
      // for an answer the user is reading. Decided here, above the branches,
      // because the Voice page speaks through the incremental path and the
      // chat through the batch one, and both must open it.
      const replyWillBeSpoken =
        Boolean(incrementalSpeechResult) ||
        (!userStopped &&
          voiceRepliesEnabled &&
          shouldSynthesizeReplyAudio(
            wasVoice,
            content,
            Boolean(audio),
            accumulatedContent,
            speakTypedReplies,
          ));
      if (replyWillBeSpoken) {
        openDiagramFromAnswer(assistantMsg.id, accumulatedContent);
      }

      if (incrementalSpeechResult) {
        incrementalSpeechResult
          .then((outcome) => {
            // Guarded inside the handler rather than around the block so the
            // promise keeps its .catch and a stopped turn cannot surface as
            // an unhandled rejection.
            if (userStopped) return;
            if (outcome !== 'failed-before-audio') return;
            // No streamed audio was heard, so batch fallback cannot replay
            // any opening. A manual Stop resolves as cancelled and never
            // reaches this branch.
            return synthesizeSpeech(accumulatedContent, {
              voice_id: ttsVoice.id,
              speed: ttsVoice.speed,
              volume: ttsVoice.volume,
            }).then((meta) => {
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
          .catch(() => {});
      } else if (
        !userStopped &&
        voiceRepliesEnabled &&
        shouldSynthesizeReplyAudio(
          wasVoice,
          content,
          Boolean(audio),
          accumulatedContent,
          speakTypedReplies,
        )
      ) {
        const playbackOwner = `reply-request-${generateId()}`;
        const releasePlayback = () =>
          useAppStore.getState().setAudioPlayback(playbackOwner, false);
        useAppStore.getState().setAudioPlayback(playbackOwner, true);
        // Streamed first: the batch endpoint returns nothing until the whole
        // clip exists (1.55s for a line, 6.92s for a paragraph, all silence),
        // while the stream starts speaking at about 0.41s. Streamed replies
        // are deliberately ephemeral — no file, so no replay control.
        // Spoken text is prepared, displayed text is not: code blocks and
        // link targets are unlistenable, and a very long answer traps the
        // listener with no way to skim.
        const spokenContent = speakableText(accumulatedContent);
        spokenTextRef.current = spokenContent;
        useDiagramPresenter.getState().noteSpoken(spokenContent);
        speakStreaming(spokenContent, ttsVoice)
          .then((spoke) => {
            if (spoke) {
              releasePlayback();
              return;
            }
            // Nothing was heard, so falling back cannot repeat anything.
            return synthesizeSpeech(spokenContent, {
              voice_id: ttsVoice.id,
              speed: ttsVoice.speed,
              volume: ttsVoice.volume,
            }).then((meta) => {
              updateLastAssistant(
                convId,
                accumulatedContent,
                undefined,
                undefined,
                undefined,
                { url: meta.url, autoPlay: true },
              );
              // Let the mounted AudioPlayer acquire its own claim before the
              // request-level bridge releases this one.
              window.setTimeout(releasePlayback, 100);
            });
          })
          .catch(() => {
            // TTS failure for a voice reply shouldn't surface as a chat
            // error — the text answer already rendered fine.
            releasePlayback();
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
    beginStreamingSpeech,
    speakStreaming,
    voiceRepliesEnabled,
    speakTypedReplies,
    ttsVoice,
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
      const ultraAssistantId = generateId();
      addMessage(convId, {
        id: ultraAssistantId,
        role: 'assistant',
        content: answer,
        timestamp: Date.now(),
      });

      // Same ordering as the streamed path: claim playback before the TTS
      // round trip, or the wake word re-arms into the gap and false-triggers
      // on ambient noise before the reply starts speaking.
      //
      // This used to call synthesizeSpeech directly, so every Ultra-mode
      // reply took the batch endpoint and left a player behind — the streamed
      // path was never reached on the one route a voice turn actually takes
      // when Flux Ultra is on. It also ignored voiceRepliesEnabled, so muting
      // Sage did not mute this.
      if (wasVoice && answer && voiceRepliesEnabled) {
        // Same rule as the streamed path: it shows itself only because it is
        // about to be spoken.
        openDiagramFromAnswer(ultraAssistantId, answer);
        const playbackOwner = `reply-request-${generateId()}`;
        const releasePlayback = () =>
          useAppStore.getState().setAudioPlayback(playbackOwner, false);
        useAppStore.getState().setAudioPlayback(playbackOwner, true);
        spokenTextRef.current = answer;
        useDiagramPresenter.getState().noteSpoken(answer);
        speakStreaming(answer, ttsVoice)
          .then((spoke) => {
            if (spoke) {
              releasePlayback();
              return;
            }
            return synthesizeSpeech(answer, {
              voice_id: ttsVoice.id,
              speed: ttsVoice.speed,
              volume: ttsVoice.volume,
            }).then((meta) => {
              updateLastAssistant(
                convId,
                answer,
                undefined,
                undefined,
                undefined,
                { url: meta.url, autoPlay: true },
              );
              window.setTimeout(releasePlayback, 100);
            });
          })
          .catch(() => {
            releasePlayback();
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
          speakStreaming,
      voiceRepliesEnabled,
    speakTypedReplies,
      ttsVoice,
    ],
  );

  sendMessageRef.current = sendMessage;

  const clearContinuationWindow = useCallback(() => {
    if (continuationTimerRef.current) {
      clearTimeout(continuationTimerRef.current);
      continuationTimerRef.current = null;
    }
    continuationRef.current = { submittedAt: null, text: '' };
  }, []);

  // Keep listening for the rest of the sentence, starting NOW -- at
  // submission, not after the reply. The first version opened this after
  // `await sendMessage()`, which resolves only when the whole reply has
  // streamed; the window then opened just as continuous conversation had
  // re-armed the mic, and its timer ended that turn four seconds later.
  // The mic went dead until a refresh. Audio, not the timer, is the real end
  // of the window; the timer is a backstop for replies that make no sound,
  // and it may only close the turn it opened: a later turn started by
  // continuous conversation or the mic button owns the microphone by then.
  const openContinuationWindow = useCallback(
    (text: string) => {
      const stamp = Date.now();
      continuationRef.current = { submittedAt: stamp, text };
      voiceTrace('window.open', { chars: text.length });
      flux.beginTurn();
      setFluxTurnActive(true);
      if (continuationTimerRef.current) clearTimeout(continuationTimerRef.current);
      continuationTimerRef.current = setTimeout(() => {
        continuationTimerRef.current = null;
        if (continuingRef.current) return;
        if (continuationRef.current.submittedAt !== stamp) {
          voiceTrace('window.timer.stale');
          return;
        }
        voiceTrace('window.timer.close');
        continuationRef.current = { submittedAt: null, text: '' };
        flux.endTurn();
        setFluxTurnActive(false);
      }, CONTINUATION_WINDOW_MS);
    },
    // flux is stable across renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // The slider rides on top of the automatic gain, and must reach the audio
  // path even when the microphone is already open.
  useEffect(() => {
    flux.setMicBoost(micBoost);
    // flux is stable across renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [micBoost]);

  const handleFluxEndOfTurn = useCallback(
    async (transcript: string, turnIndex: number, speculativeAnswer?: string) => {
      clearFluxSilenceTimer();
      // Deepgram can repeat a final event; one confirmed turn sends once.
      if (lastFluxTurnRef.current === turnIndex) return;
      lastFluxTurnRef.current = turnIndex;

      // This turn only asked for the diagram to go away. It has gone; the
      // words are not a message, and Sage keeps talking.
      if (
        diagramCommandTurnRef.current === turnIndex ||
        (useDiagramPresenter.getState().current && isCloseDiagramCommand(transcript))
      ) {
        useDiagramPresenter.getState().close();
        diagramCommandTurnRef.current = null;
        // Keep the microphone open. Returning bare left the Flux turn hanging
        // until its silence timer expired, so after dismissing a picture Sage
        // went deaf for twelve seconds -- the user only closed a diagram,
        // they did not end the conversation.
        voiceTrace('diagram.closedKeepListening');
        flux.beginTurn();
        armFluxSilenceTimer('followUp');
        return;
      }

      let spoken = (transcript || '').trim();
      if (fastFollowRef.current.active) {
        clearGreetingTimer();
        const elapsed = Date.now() - fastFollowRef.current.startedAt;
        if (isOnlyWakePhrase(spoken, elapsed, fastFollowRef.current.greeted)) {
          // "Hey Sage." and then nothing: the user paused. Greet now and
          // keep listening for the question.
          if (!fastFollowRef.current.greeted && wakeWordGreetingEnabled) {
            greetAfterPause('phrase-only');
          }
          voiceTrace('wake.phraseOnly');
          flux.beginTurn();
          armFluxSilenceTimer('wake');
          return;
        }
        fastFollowRef.current = { active: false, greeted: false, startedAt: 0 };
        spoken = stripWakePhrase(spoken);
      }

      // The rest of a sentence that was cut off. The half-answer was stopped
      // when this speech began; withdraw it and the half-question now, and
      // send the whole question as one turn. If the resumed speech turned
      // out to be nothing (a breath, a false start), the half-question is
      // re-asked on its own: its answer was cancelled on a false alarm, and
      // leaving "(Generation stopped)" behind would punish the user for it.
      if (continuingRef.current) {
        continuingRef.current = false;
        const first = continuationRef.current.text;
        const text = spoken ? mergeTurns(first, spoken) : first;
        clearContinuationWindow();
        setFluxTurnActive(false);
        // The abort's re-render must land before sending, or sendMessage
        // still sees the stream it just cancelled and drops the turn.
        await new Promise((resolve) => setTimeout(resolve, 0));
        if (activeId) useAppStore.getState().retractLastExchange(activeId);
        if (text) {
          voiceOriginatedRef.current = true;
          // Not awaited: the window must open at submission (see
          // openContinuationWindow), and the merged question can itself be
          // cut off again.
          void sendMessageRef.current(text).catch(() => {});
          openContinuationWindow(text);
        }
        return;
      }

      // A turn that ended while Sage was still speaking and never reached
      // the interruption threshold is Sage's own voice leaking past echo
      // cancellation, not the user. Keep listening for a real one.
      if (
        bargeListeningRef.current &&
        isEchoTurn({
          enabled: true,
          sageSpeaking: useAppStore.getState().audioPlaying,
          voiceReply: true,
          triggered: bargeTriggeredRef.current,
        })
      ) {
        voiceTrace('barge.echoDropped', {
          chars: spoken.length,
          reason: bargeVerdictRef.current?.reason ?? 'no-words',
        });
        const verdict = bargeVerdictRef.current;
        bargeVerdictRef.current = null;
        const line =
          verdict && verdict.decision === 'reject' ? describeVerdict(verdict, spoken) : null;
        if (line) {
          useAppStore.getState().addLogEntry({
            timestamp: Date.now(), level: 'info', category: 'voice', message: line,
          });
        }
        flux.beginTurn();
        return;
      }
      bargeVerdictRef.current = null;
      const wasBargeIn = bargeTriggeredRef.current;
      bargeListeningRef.current = false;
      bargeTriggeredRef.current = false;

      if (!spoken) {
        setFluxTurnActive(false);
        return;
      }
      // Talked over Sage only to stop it: the cut already happened, and
      // "stop" is not a question. Sent on, it once became "be quiet for
      // thirty minutes".
      if (wasBargeIn && isStopCommand(spoken)) {
        voiceTrace('barge.stopOnly', { chars: spoken.length });
        setFluxTurnActive(false);
        return;
      }
      voiceOriginatedRef.current = true;

      const text = spoken;
      // A released answer arrives only on a confirmed final, already checked
      // against this turn's identity and transcript server-side. If posting
      // it is declined for any reason, fall through and generate normally
      // rather than losing the turn.
      let handled = false;
      if (speculativeAnswer && speculativeAnswer.trim()) {
        handled = await releaseSpeculativeAnswer(text, speculativeAnswer.trim());
      }
      // Not awaited: sendMessage resolves when the whole reply has streamed,
      // and the window has to be open long before that.
      if (!handled) void sendMessage(text).catch(() => {});
      openContinuationWindow(text);
    },
    [
      releaseSpeculativeAnswer,
      sendMessage,
      clearContinuationWindow,
      openContinuationWindow,
      activeId,
    ],
  );

  useEffect(() => clearContinuationWindow, [clearContinuationWindow]);

  const handleFluxUnavailable = useCallback(
    async (reason: string, audio: Int16Array | null) => {
      voiceTrace('flux.unavailable', { reason, bufferedSamples: audio?.length ?? 0 });
      clearFluxSilenceTimer();
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

  const clearFluxSilenceTimer = useCallback(() => {
    if (fluxSilenceTimerRef.current) {
      clearTimeout(fluxSilenceTimerRef.current);
      fluxSilenceTimerRef.current = null;
    }
  }, []);

  // A pending timer would otherwise fire after unmount and call endTurn on a
  // torn-down socket.
  useEffect(() => clearFluxSilenceTimer, [clearFluxSilenceTimer]);

  const armFluxSilenceTimer = useCallback((kind: 'wake' | 'followUp') => {
    clearFluxSilenceTimer();
    fluxSilenceTimerRef.current = setTimeout(() => {
      fluxSilenceTimerRef.current = null;
      // Silent by design: nothing was said, so there is nothing to report.
      voiceTrace('silence.timer.close', { kind });
      // The conversation is over: nothing was said in the window Sage left
      // open. Let the server drop the speech connection it was holding for
      // the next turn rather than leaving it open until its TTL.
      void apiFetch('/v1/speech/conversation-ended', { method: 'POST' }).catch(
        () => {},
      );
      flux.endTurn();
      clearFluxSilenceTimer();
      setFluxTurnActive(false);
    }, listenMs(kind));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearFluxSilenceTimer]);

  const flux = useFluxSpeech({
    enabled: fluxActive,
    eager: fluxEagerEnabled,
    suppressNoise: noiseSuppression !== 'off',
    model: selectedModel,
    onEndOfTurn: handleFluxEndOfTurn,
    onSessionReady: () => {
      // Turn indices restart with the session; a stale index here would
      // drop the first turn after a reconnect as a repeat.
      lastFluxTurnRef.current = null;
    },
    onTurnStarted: () => {
      // Real speech: from here Deepgram owns the ending.
      clearFluxSilenceTimer();
      const state = continuationRef.current;
      if (
        !continuingRef.current &&
        isContinuation(
          {
            submittedAt: state.submittedAt,
            submittedText: state.text,
            sageSpeaking: useAppStore.getState().audioPlaying,
          },
          Date.now(),
        )
      ) {
        // The user was not finished. Stop the answer to the half-question:
        // the stream, and the speech already being synthesised from it --
        // aborting only the stream left the half-answer playing and its
        // audio claim set, which is what kept the wake word from re-arming.
        // The withdrawal itself waits for the resumed turn to end, after the
        // aborted stream's own cleanup has run, so nothing writes into a
        // message that is no longer there.
        continuingRef.current = true;
        voiceTrace('window.continuation');
        if (continuationTimerRef.current) {
          clearTimeout(continuationTimerRef.current);
          continuationTimerRef.current = null;
        }
        stopSpeaking();
        stopStreaming();
      }
    },
    onUpdate: (transcript, turnIndex, words) => {
      // Words beyond the wake phrase: the user carried on, no greeting.
      // (StartOfTurn cannot tell: the pre-rolled phrase opens a turn too.)
      if (fastFollowRef.current.active && continuesPastWakePhrase(transcript)) {
        clearGreetingTimer();
      }
      // "Close the diagram" is an instruction to the screen, not an
      // interruption of the answer: it must be caught before barge-in judges
      // the same words, or asking for the picture to go would also cut Sage
      // off mid-sentence. The voice carries on; only the overlay goes.
      if (useDiagramPresenter.getState().current) {
        if (isCloseDiagramCommand(transcript)) {
          useDiagramPresenter.getState().close();
          diagramCommandTurnRef.current = turnIndex;
          voiceTrace('diagram.closedByVoice', { heard: transcript });
          return;
        }
        // Still might become one: "close the" reaches the cutting threshold
        // before "diagram" arrives, so judging now interrupts Sage on the way
        // to being asked not to. Wait for the turn to finish instead.
        if (mayBecomeCloseDiagramCommand(transcript)) {
          voiceTrace('diagram.closeMaybe', { heard: transcript });
          return;
        }
      }
      // The rest of this turn is that same command still arriving; never let
      // it reach barge-in or the composer.
      if (diagramCommandTurnRef.current === turnIndex) return;
      if (!bargeListeningRef.current) return;
      // A partial for a turn already finalised is stale; it belongs to the
      // normal path that handled it.
      if (lastFluxTurnRef.current !== null && turnIndex <= lastFluxTurnRef.current) return;
      const { settings, audioPlaying: sageSpeaking } = useAppStore.getState();
      if (
        !settings.bargeInEnabled ||
        !sageSpeaking ||
        !lastReplyWasVoiceRef.current ||
        bargeTriggeredRef.current
      ) {
        return;
      }
      const verdict = judge(words, spokenTextRef.current, settings.bargeInMode);
      bargeVerdictRef.current = verdict;
      if (verdict.decision !== 'cut') return;
      // The user is talking over the reply: stop the voice and the model,
      // keep the microphone where it is. Deepgram's end-of-turn for what
      // they are saying arrives through the normal path.
      bargeTriggeredRef.current = true;
      interruptedRef.current = true;
      voiceTrace('barge.interrupt', {
        chars: transcript.length,
        reason: verdict.reason,
        mode: settings.bargeInMode,
        heard: words.map((w) => `${w.word}:${w.confidence.toFixed(2)}`).join(' '),
        spokenChars: spokenTextRef.current.length,
      });
      useAppStore.getState().addLogEntry({
        timestamp: Date.now(), level: 'info', category: 'voice',
        message: describeVerdict(verdict, transcript) ?? 'You interrupted Sage',
      });
      stopSpeaking();
      const store = useAppStore.getState();
      if (store.streamState.isStreaming) {
        // The abort handler in sendMessage appends the mark.
        stopStreaming();
      } else if (store.activeId) {
        // The text had already finished; only the voice was cut. Mark it
        // here so the transcript and the next turn still know.
        const last = store.messages[store.messages.length - 1];
        if (last?.role === 'assistant' && !last.content.endsWith(INTERRUPTED_MARK)) {
          updateLastAssistant(store.activeId, last.content + INTERRUPTED_MARK);
        }
        interruptedRef.current = false;
      }
    },
    onTurnResumed: () => {
      // The speaker carried on; the server has already discarded its
      // speculative work. Nothing was shown here, so nothing to undo.
    },
    onUnavailable: handleFluxUnavailable,
  });

  // A turn cannot outlive the socket that owns it.
  //
  // `fluxTurnActive` is cleared by EndOfTurn, and by onUnavailable when a
  // drop is detected — but an *intentional* teardown (the effect re-running,
  // Flux being switched off, a reconnect) sets `intentionalStopRef` and so
  // never calls back. A turn open at that moment stayed open forever, and
  // since `effectiveSpeechState` reports 'recording' while it is set and the
  // wake word only fires from 'idle', the wake word could never re-arm for
  // the rest of the session. Only a page reload cleared it.
  //
  // Keying off the status rather than off each teardown path covers every
  // way a socket can stop being the one that started the turn.
  useEffect(() => {
    if (!turnSurvivesStatus(flux.status)) setFluxTurnActive(false);
  }, [flux.status]);

  // Entry point for both the wake word and continuous-conversation re-arm.
  // Real silence detection (see useSpeech's VAD) stops the recording as soon
  // as the user finishes talking — the fixed 12s timer below is only a
  // fallback for the rare case VAD never sees speech at all (e.g. the
  // trigger was itself a false positive), not the normal way this ends.
  // Previously the timer WAS the only mechanism, so every turn waited out
  // the same multi-second pause no matter how short the question was.
  const beginAutoRecording = useCallback(async () => {
    if (micDisabled || effectiveSpeechState !== 'idle') {
      voiceTrace('cc.skipped', { micDisabled, speech: effectiveSpeechState });
      return;
    }
    voiceTrace('cc.beginTurn');
    autoTriggeredRef.current = true;
    if (fluxActive) {
      // Flux decides when the turn ends, so there is no 12s fallback timer
      // and no local recording to stop. This turn now owns the microphone;
      // a continuation window left over from the last reply must not be
      // able to end it.
      clearContinuationWindow();
      setFluxTurnActive(true);
      flux.beginTurn();
      armFluxSilenceTimer('followUp');
      return;
    }
    await startRecording(finishAutoRecording);
    autoStopTimerRef.current = setTimeout(() => {
      finishAutoRecording();
    }, listenMs('followUp'));
    // effectiveSpeechState folds in fluxTurnActive; leaving it out of the
    // dependencies let this callback keep a stale 'recording' after the
    // turn had ended, and continuous conversation skipped every re-arm
    // from the third turn on. The trace showed the two values disagreeing
    // in the same millisecond.
  }, [micDisabled, speechState, effectiveSpeechState, startRecording, finishAutoRecording]);

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
  const beginWakeWordRecording = useCallback(async (info?: { sinceFiringMs: number; verified: boolean }) => {
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
      // An unverified firing (no transcript check ran) gets the old,
      // sequential flow: no pre-roll of audio nobody vouched for.
      const fastFollow = wakeWordFastFollow && fluxActive && info?.verified !== false;
      const greeting = wakeWordGreetingEnabled && !fastFollow
        ? playGreeting({
            voiceId: ttsVoice.id,
            onFailure: (reason) =>
              toast.error(`Greeting didn't play — ${reason}`, { duration: 8000 }),
          })
        : undefined;
      if (fluxActive) {
        // Marked busy before awaiting the greeting, not after: this is what
        // disarms the wake word, and leaving it armed through the greeting
        // let a second detection of the same "Hey Sage" through.
        setFluxTurnActive(true);
        if (fastFollow && !greeting) {
          // One breath: the turn starts from the wake phrase itself, with
          // the audio the detector already heard as pre-roll, and the
          // greeting waits to see whether the user pauses.
          const sinceFiringMs = info?.sinceFiringMs ?? 0;
          fastFollowRef.current = {
            active: true,
            greeted: false,
            startedAt: Date.now() - sinceFiringMs,
          };
          // Enough pre-roll to reach back past the phrase, no more:
          // Deepgram works through it at about real time before it can
          // end a turn, so every spare second is a second before "Yes,
          // Sir?" when the user paused.
          const preRollMs = Math.min(PRE_ROLL_MS, sinceFiringMs + PHRASE_MS);
          const speechRms = flux.setSpeechLevel(ambientRmsRef.current?.() ?? 0);
          flux.beginTurn(takeRecentAudioRef.current?.(preRollMs));
          armFluxSilenceTimer('wake');
          voiceTrace('wake.fastFollow', { sinceFiringMs, preRollMs, speechRms });
          if (wakeWordGreetingEnabled) {
            clearGreetingTimer();
            // The pause is a fact about the microphone: a second with no
            // sound at speech level since the phrase ended. Checked every
            // 100 ms rather than decided by Deepgram's first partial, which
            // arrived after a greeting had already talked over "tell me
            // about". Silence began at the end of the phrase, which the
            // server says was `sinceFiringMs` ago.
            const phraseEndedAt = Date.now() - sinceFiringMs;
            const quietSince = () => Math.max(phraseEndedAt, flux.lastSoundAt());
            const check = () => {
              greetingTimerRef.current = null;
              if (!fastFollowRef.current.active || fastFollowRef.current.greeted) return;
              const quietFor = Date.now() - quietSince();
              if (quietFor >= GREETING_PAUSE_MS) {
                greetAfterPause('timer');
                return;
              }
              // Give up once a question is clearly under way.
              if (Date.now() - phraseEndedAt > GREETING_GIVE_UP_MS) return;
              greetingTimerRef.current = setTimeout(check, 100);
            };
            greetingTimerRef.current = setTimeout(check, greetingDelayMs(sinceFiringMs));
          }
          return;
        }
        // Same sequential contract as the local path: the greeting finishes
        // before any audio is transmitted, so Sage's own voice never enters
        // the turn Deepgram is judging.
        if (greeting) await greeting;
        flux.beginTurn();
        armFluxSilenceTimer('wake');
        return;
      }
      await startRecording(finishAutoRecording, { waitBeforeCapture: greeting });
      autoStopTimerRef.current = setTimeout(() => {
        finishAutoRecording();
      }, listenMs('wake'));
    } finally {
      // By now startRecording has set speechState to 'recording', so the
      // ordinary guard above takes over from here.
      wakeWordBusyRef.current = false;
    }
  }, [
    micDisabled,
    speechState,
    effectiveSpeechState,
    startRecording,
    finishAutoRecording,
    wakeWordGreetingEnabled,
    wakeWordFastFollow,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ]);

  // The pause greeting: "Yes, Sir?" once, into an open turn, with the
  // microphone held for the clip's length so the greeting is not heard back
  // as the user's words.
  const greetAfterPause = (why: 'timer' | 'phrase-only') => {
    const state = fastFollowRef.current;
    if (!state.active || state.greeted) return;
    state.greeted = true;
    clearGreetingTimer();
    voiceTrace('wake.pauseGreeting', { why });
    const clip = playGreeting({
      voiceId: ttsVoice.id,
      onFailure: (reason) =>
        toast.error(`Greeting didn't play — ${reason}`, { duration: 8000 }),
    });
    flux.holdAudio(clip);
  };

  useEffect(() => {
    if (speechState !== 'recording' && autoStopTimerRef.current) {
      clearTimeout(autoStopTimerRef.current);
      autoStopTimerRef.current = null;
    }
  }, [speechState]);

  const wakeWordEnabled = useAppStore((s) => s.settings.wakeWordEnabled);

  // A spoken moment that invites an answer (M37): open the microphone for
  // it, so "yes, actually" needs no wake word. Only in Flux mode with the
  // wake word or continuous conversation on -- the modes in which the user
  // has already said the mic may open by itself -- and only while the
  // moment is fresh; the same silence rule as after a reply closes it.
  const replyWindowAt = useAppStore((s) => s.replyWindowAt);
  useEffect(() => {
    if (replyWindowAt === null) return;
    if (!fluxActive || !(wakeWordEnabled || continuousConversationEnabled)) return;
    if (Date.now() - replyWindowAt > 15_000) return;
    voiceTrace('moment.replyWindow');
    void beginAutoRecording();
    // Only the arrival of a new window should open the mic.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replyWindowAt]);

  // Fetch and decode the clips while the wake word is merely armed, so the
  // first trigger doesn't pay for the download at the moment it matters.
  useEffect(() => {
    if (wakeWordEnabled && wakeWordGreetingEnabled) preloadGreetings();
  }, [wakeWordEnabled, wakeWordGreetingEnabled]);
  const continuousConversationEnabled = useAppStore((s) => s.settings.continuousConversationEnabled);
  const wakeWordVerify = useAppStore((s) => s.settings.wakeWordVerify);
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

  // While Sage speaks, the microphone either closes or listens for an
  // interruption. Closed is the old rule: echo cancellation is imperfect,
  // and Sage's own reply reaching Deepgram would be transcribed as the
  // user's next turn. With barge-in on, for a voice reply in Flux mode, the
  // turn is kept open instead and the policy in lib/barge-in.ts tells the
  // user from the echo.
  useEffect(() => {
    if (audioPlaying) {
      // Sage is audibly speaking: anything said now is a new turn, not the
      // rest of the last one.
      clearContinuationWindow();
      const bargeIn =
        fluxActive &&
        useAppStore.getState().settings.bargeInEnabled &&
        lastReplyWasVoiceRef.current;
      if (bargeIn) {
        if (!fluxTurnActive) {
          flux.beginTurn();
          setFluxTurnActive(true);
        }
        clearFluxSilenceTimer();
        bargeListeningRef.current = true;
        bargeTriggeredRef.current = false;
        voiceTrace('barge.listening');
      } else if (fluxTurnActive) {
        voiceTrace('audio.closesTurn');
        flux.endTurn();
        clearFluxSilenceTimer();
        setFluxTurnActive(false);
      }
    } else if (bargeListeningRef.current && !bargeTriggeredRef.current) {
      // The reply finished uninterrupted. With continuous conversation the
      // open turn simply becomes the next question's turn; otherwise it
      // closes now, as it would have at playback start under the old rule.
      bargeListeningRef.current = false;
      if (continuousConversationEnabled && lastReplyWasVoiceRef.current) {
        voiceTrace('barge.handoff');
        armFluxSilenceTimer('followUp');
      } else if (fluxTurnActive) {
        voiceTrace('barge.closed');
        flux.endTurn();
        clearFluxSilenceTimer();
        setFluxTurnActive(false);
      }
    }
    // flux.beginTurn/endTurn are stable; re-running on the flags alone is intended.
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

  useEffect(() => {
    setVoiceTraceSink((event, detail, at) =>
      useAppStore.getState().addLogEntry({
        timestamp: at,
        level: 'info',
        category: 'voice',
        message: `${event} ${detail}`.trim(),
      }),
    );
    return () => setVoiceTraceSink(null);
  }, []);

  const wakeGate =
    wakeWordEnabled &&
    !wakeWordSuspended &&
    !micDisabled &&
    effectiveSpeechState === 'idle' &&
    !audioPlaying &&
    wakeWordSettled;
  const lastGateRef = useRef<boolean | null>(null);
  useEffect(() => {
    // Only when the answer changes. Every input change used to log a full
    // snapshot, which buried the events that matter under identical lines.
    if (lastGateRef.current === wakeGate) return;
    lastGateRef.current = wakeGate;
    voiceTrace(wakeGate ? 'gate.open' : 'gate.closed', {
      wakeWordEnabled,
      suspended: wakeWordSuspended,
      micDisabled,
      streaming: streamState.isStreaming,
      speech: effectiveSpeechState,
      fluxTurnActive,
      audioPlaying,
      settled: wakeWordSettled,
      cc: continuousConversationEnabled,
      flux: flux.status,
    });
    // Intentionally broad: this is the diagnostic, not the behaviour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    wakeGate,
    wakeWordEnabled,
    wakeWordSuspended,
    micDisabled,
    streamState.isStreaming,
    effectiveSpeechState,
    fluxTurnActive,
    audioPlaying,
    wakeWordSettled,
    continuousConversationEnabled,
    flux.status,
  ]);

  const { error: wakeWordError, takeRecentAudio, ambientRms } = useWakeWord(
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
    // A firing the transcript did not back: the words were not there. One
    // line so a false trigger is readable afterwards instead of a mystery.
    (heard) =>
      useAppStore.getState().addLogEntry({
        timestamp: Date.now(), level: 'info', category: 'voice',
        message: heard
          ? `Ignored a wake-word-like sound: heard "${heard}"`
          : 'Ignored a wake-word-like sound: no words in it',
      }),
    wakeWordVerify,
    noiseSuppression === 'all',
  );
  takeRecentAudioRef.current = takeRecentAudio;
  ambientRmsRef.current = ambientRms;

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
    if (wasPlaying && !audioPlaying) {
      const fire =
        continuousConversationEnabled &&
        lastReplyWasVoiceRef.current &&
        !micDisabled &&
        speechState === 'idle';
      voiceTrace('cc.audioEnded', {
        fire,
        cc: continuousConversationEnabled,
        lastReplyWasVoice: lastReplyWasVoiceRef.current,
        micDisabled,
        speech: speechState,
        fluxTurnActive,
      });
      if (fire) beginAutoRecording();
    }
  }, [audioPlaying, continuousConversationEnabled, micDisabled, speechState, beginAutoRecording]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  if (voiceOnly) {
    const iconButton = (
      active: boolean,
      onClick: () => void,
      label: string,
      icon: React.ReactNode,
      disabled = false,
    ) => (
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-pressed={active}
        aria-label={label}
        title={label}
        className="p-2.5 rounded-full transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default"
        style={{
          background: active ? 'var(--color-accent-subtle)' : 'transparent',
          border: `1px solid ${active ? 'var(--color-accent)' : 'var(--color-border)'}`,
          color: active ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
        }}
      >
        {icon}
      </button>
    );

    return (
      <div className="flex items-center justify-center gap-3">
        {iconButton(
          deepResearch,
          () => setDeepResearch(!deepResearch),
          deepResearch ? 'Deep Research: on' : 'Deep Research: off',
          <Search size={16} />,
          streamState.isStreaming,
        )}

        <MicButton
          state={effectiveSpeechState}
          onClick={handleMicClick}
          disabled={micDisabled}
          reason={micReason}
        />

        {/* One press stops what Sage is saying, and nothing else. It used
            to mute as well, so stopping a reply took a second press to hear
            the next one; muting lives in Settings. */}
        {iconButton(
          audioPlaying,
          stopSpeaking,
          !voiceRepliesEnabled
            ? 'Replies muted in Settings'
            : audioPlaying
              ? 'Stop speaking'
              : 'Nothing is being spoken',
          audioPlaying || !voiceRepliesEnabled ? <VolumeX size={16} /> : <Volume2 size={16} />,
          !audioPlaying,
        )}
      </div>
    );
  }

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

          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.txt,.md,.csv"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleDocument(file);
              e.target.value = '';
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={streamState.isStreaming || Boolean(docBusy)}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-colors cursor-pointer disabled:cursor-default disabled:opacity-50"
            style={{
              background: 'transparent',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-tertiary)',
            }}
            title="Attach a document (.pdf, .docx, .txt, .md, .csv)"
            aria-label="Attach a document"
          >
            <Paperclip size={12} />
            Attach
          </button>

          <button
            type="button"
            onClick={() => setDocIndexed((v) => !v)}
            aria-pressed={docIndexed}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-colors cursor-pointer"
            style={{
              background: docIndexed ? 'var(--color-accent-subtle)' : 'transparent',
              border: `1px solid ${docIndexed ? 'var(--color-accent)' : 'var(--color-border)'}`,
              color: docIndexed ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
            }}
            title={
              docIndexed
                ? 'Attachments are also saved and stay searchable later'
                : 'Attachments are used in this conversation only'
            }
          >
            {docIndexed ? 'Save to knowledge' : 'This chat only'}
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
      {(documents.length > 0 || docBusy) && (
        <div className="flex flex-wrap items-center gap-2 px-1 pb-2">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs"
              style={{
                background: 'var(--color-bg-tertiary)',
                border: '1px solid var(--color-input-border)',
              }}
              title={doc.note || `${doc.name} — this conversation only`}
            >
              <Paperclip size={12} aria-hidden="true" />
              <span>{doc.name}</span>
              {doc.indexed && (
                <span style={{ color: 'var(--color-accent)' }}>saved</span>
              )}
              <button
                onClick={() => removeDocument(doc.id)}
                title={`Remove ${doc.name}`}
                aria-label={`Remove ${doc.name}`}
                className="cursor-pointer"
                style={{ color: 'var(--color-text-tertiary)' }}
              >
                <X size={12} />
              </button>
            </div>
          ))}
          {docBusy && (
            <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
              {docBusy} a page that did not extract cleanly is re-read by
              looking at it, which can take a few minutes.
            </span>
          )}
        </div>
      )}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 px-1 pb-2">
          {attachments.map((image) => (
            <div key={image.id} className="relative group">
              <img
                src={image.dataUrl}
                alt={image.name}
                title={image.name}
                className="h-16 w-16 object-cover rounded-lg"
                style={{ border: '1px solid var(--color-input-border)' }}
              />
              <button
                onClick={() => removeAttachment(image.id)}
                title={`Remove ${image.name}`}
                aria-label={`Remove ${image.name}`}
                className="absolute -top-1.5 -right-1.5 p-0.5 rounded-full cursor-pointer"
                style={{
                  background: 'var(--color-bg-tertiary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-input-border)',
                }}
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
      <div
        className="flex items-center gap-2 rounded-2xl px-4 py-3 transition-shadow"
        onDragOver={(e) => {
          // Only claim a drag that is actually carrying files, so selecting
          // text and dragging it does not light up the whole composer.
          if (!Array.from(e.dataTransfer.types || []).includes('Files')) return;
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
        style={{
          background: 'var(--color-input-bg)',
          border: dragActive
            ? '1px dashed var(--color-accent)'
            : '1px solid var(--color-input-border)',
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
          onPaste={handlePaste}
          placeholder={
            selectedModel
              ? 'Message Sage — paste/drop image'
              : 'Pick a model first (⌘K)...'
          }
          rows={1}
          className="flex-1 bg-transparent outline-none resize-none text-sm leading-relaxed"
          style={{ color: 'var(--color-text)', maxHeight: '200px' }}
          disabled={streamState.isStreaming || modelLoading}
        />
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
            <>
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
            </>
          )}
        </div>
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
