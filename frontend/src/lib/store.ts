import { create } from 'zustand';
import { DEFAULT_VOICE_PROFILE, isKnownVoiceId } from './voice-profiles';
import { type BargeMode, BARGE_MODES, DEFAULT_BARGE_MODE } from './barge-in';

export type WakeWordVerify = 'local' | 'off';
// Streaming transcription: Deepgram Flux (cloud), NVIDIA Parakeet (local),
// or none -- 'whisper' is the push-to-talk faster-whisper path that was the
// default before either existed and remains the fallback for both.
export type SttProvider = 'flux' | 'parakeet' | 'whisper';
export const STT_PROVIDERS: readonly SttProvider[] = ['flux', 'parakeet', 'whisper'];
export type TtsProvider = 'cartesia' | 'chatterbox';
export const TTS_PROVIDERS: readonly TtsProvider[] = ['cartesia', 'chatterbox'];

/** Keep the provider choice and the older flags telling one story. */
export function normaliseSpeechProviders<T extends {
  sttProvider: SttProvider;
  fluxEnabled: boolean;
  fluxEagerEnabled: boolean;
}>(settings: T): T {
  // `fluxEnabled` predates providers and still gates the streaming socket
  // in InputArea; both streaming providers use that socket.
  const fluxEnabled = settings.sttProvider !== 'whisper';
  // Ultra is Flux's speculation. Parakeet has no eager end of turn, and a
  // stored combination with eager on and streaming off must not
  // resurrect it.
  const fluxEagerEnabled =
    settings.sttProvider === 'flux' ? settings.fluxEagerEnabled : false;
  return { ...settings, fluxEnabled, fluxEagerEnabled };
}
export const WAKE_WORD_VERIFY_MODES: readonly WakeWordVerify[] = ['local', 'off'];
export const LISTEN_SECONDS_MIN = 3;
export const LISTEN_SECONDS_MAX = 30;
export const DEFAULT_LISTEN_SECONDS = 8;

function clampSeconds(value: unknown, fallback: number): number {
  const n = typeof value === 'number' && Number.isFinite(value) ? Math.round(value) : fallback;
  return Math.min(LISTEN_SECONDS_MAX, Math.max(LISTEN_SECONDS_MIN, n));
}
import {
  DEFAULT_CLOUD_MODEL,
  preferredModelId,
} from './model-preference';
import type {
  Conversation,
  ChatMessage,
  LiveEnergyMetrics,
  LogEntry,
  ModelInfo,
  MessageTelemetry,
  ResearchSearchTrace,
  ResearchSource,
  SavingsData,
  ServerInfo,
  StreamState,
  ToolCallInfo,
  TokenUsage,
} from '../types';
import type { ManagedAgent } from './api';
import { isEmbedOnlyModel } from './model-capabilities';
import { serializeToolCallArguments } from './tool-call';

export interface CachedConnector {
  connector_id: string;
  display_name: string;
  connected: boolean;
  chunks: number;
}

export interface AgentEvent {
  type: string;
  timestamp: number;
  data: Record<string, unknown>;
}

import { withoutImages } from './image-attach';

/**
 * Attached images, held for the life of the tab and keyed by message id.
 *
 * Deliberately outside the conversation store. `addMessage` re-reads
 * conversations from localStorage on every call, so anything living on the
 * message itself is either persisted or gone by the next render — there is no
 * in-memory middle ground there. A few MB of screenshots do not belong in the
 * storage quota, so they live here instead and vanish when the tab closes.
 */
const sessionImages = new Map<string, string[]>();

export function rememberImages(messageId: string, images: string[]): void {
  if (images.length) sessionImages.set(messageId, images);
}

export function imagesFor(messageId: string): string[] | undefined {
  return sessionImages.get(messageId);
}

/**
 * Attached documents.
 *
 * Unlike images these are **persisted**, and the difference is two orders of
 * magnitude: a screenshot is megabytes, a research paper is about 20 KB of
 * text. Keeping them only in memory was copied from the image path and was
 * wrong -- a page reload, or Vite replacing this module during development,
 * silently dropped the paper mid-conversation, and the next question got
 * "please reattach the file" with no explanation.
 *
 * The session map remains for anything too large to store, so an oversized
 * document still works for the life of the tab rather than failing outright.
 */
const sessionDocuments = new Map<string, Array<{ name: string; text: string }>>();

/** Largest document written to localStorage. Beyond this it stays in memory. */
export const MAX_PERSISTED_DOCUMENT_CHARS = 400_000;

export function rememberDocuments(
  messageId: string,
  documents: Array<{ name: string; text: string }>,
): void {
  if (documents.length) sessionDocuments.set(messageId, documents);
}

export function documentsFor(
  messageId: string,
): Array<{ name: string; text: string }> | undefined {
  return sessionDocuments.get(messageId);
}

// ── localStorage persistence ──────────────────────────────────────────

const CONVERSATIONS_KEY = 'openjarvis-conversations';
const SETTINGS_KEY = 'openjarvis-settings';
const OPTIN_KEY = 'openjarvis-optin';
const OPTIN_NAME_KEY = 'openjarvis-display-name';
const OPTIN_EMAIL_KEY = 'openjarvis-email';
const OPTIN_ANONID_KEY = 'openjarvis-anon-id';
const OPTIN_SEEN_KEY = 'openjarvis-optin-seen';

interface ConversationStore {
  version: 1;
  conversations: Record<string, Conversation>;
  activeId: string | null;
}

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

// A voice reply's `audio.autoPlay` flag is meant to fire once, at the
// moment the reply arrives live. It's stored as part of the message
// though, so without this, reopening a past chat (or just refreshing the
// page) would replay that stale flag and auto-play old audio every time.
function withoutAutoPlay(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((m) =>
    m.audio?.autoPlay ? { ...m, audio: { ...m.audio, autoPlay: false } } : m,
  );
}

/**
 * The conversations, parsed once and kept.
 *
 * Every change to a chat -- the question going in, each reply update, a
 * stop -- used to parse all of them out of localStorage and write all of
 * them back: 171 conversations, 2.35 MB, 50-86 ms each, on the main thread.
 * Those were the orb's stutters, measured frame for frame against the voice
 * trace (24 September): the moment the user finished talking, when a reply
 * began and ended, when it was stopped. Read once; another tab's write
 * (the storage event) drops the copy so it is read again.
 */
let cachedStore: ConversationStore | null = null;

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('storage', (event) => {
    if (event.key === CONVERSATIONS_KEY || event.key === null) cachedStore = null;
  });
}

function loadConversations(): ConversationStore {
  if (!cachedStore) {
    cachedStore = readConversations();
    return cachedStore;
  }
  // A reply's autoPlay is for the moment it arrives. Read from storage it
  // was always cleared (readConversations); kept in memory it would stay
  // set, and every spoken reply in the chat would play again on the next
  // message. Copied, not edited: the UI holds these objects.
  for (const conversation of Object.values(cachedStore.conversations)) {
    if (!conversation.messages.some((m) => m.audio?.autoPlay)) continue;
    conversation.messages = conversation.messages.map((m) =>
      m.audio?.autoPlay ? { ...m, audio: { ...m.audio, autoPlay: false } } : m,
    );
  }
  return cachedStore;
}

function readConversations(): ConversationStore {
  try {
    const raw = localStorage.getItem(CONVERSATIONS_KEY);
    if (!raw) return { version: 1, conversations: {}, activeId: null };
    const parsed = JSON.parse(raw);
    if (parsed.version === 1) {
      let repaired = false;
      for (const conversation of Object.values(parsed.conversations ?? {}) as Conversation[]) {
        for (const message of conversation.messages ?? []) {
          for (const toolCall of message.toolCalls ?? []) {
            const argumentsText = serializeToolCallArguments(toolCall.arguments);
            if (argumentsText !== toolCall.arguments) {
              toolCall.arguments = argumentsText;
              repaired = true;
            }
          }
          // withoutAutoPlay() below only ever cleaned the in-memory copy for
          // display — the flag stayed true in what's actually saved here.
          // addMessage/updateLastAssistant read straight from this function
          // and set the result directly into `messages`, bypassing that
          // in-memory cleanup entirely: every reply ever spoken in a chat
          // would replay at once the moment a new voice message was sent in
          // it, because the "new" state they installed was this raw,
          // never-actually-repaired data. Stripping it here, at the one
          // place every caller reads from, means there is no second copy
          // left to go stale.
          if (message.audio?.autoPlay) {
            message.audio.autoPlay = false;
            repaired = true;
          }
        }
      }
      // A moment (something Sage said aloud) belongs to exactly one chat.
      // The feed once re-added the same moments into every chat that was
      // opened while a stale hook instance held an old watermark, so each
      // conversation opened with the same four lines at the top. Keep the
      // first chat's copy, in creation order; drop the rest.
      const seenMoments = new Set<string>();
      const ordered = (Object.values(parsed.conversations ?? {}) as Conversation[]).sort(
        (a, b) => a.createdAt - b.createdAt,
      );
      for (const conversation of ordered) {
        const kept = (conversation.messages ?? []).filter((message) => {
          if (!message.moment) return true;
          if (seenMoments.has(message.id)) return false;
          seenMoments.add(message.id);
          return true;
        });
        if (kept.length !== (conversation.messages ?? []).length) {
          conversation.messages = kept;
          repaired = true;
        }
      }
      if (repaired) {
        try {
          localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(parsed));
        } catch {
          // Keep the repaired conversations usable in memory when storage is
          // read-only or full. A failed best-effort writeback must not make
          // otherwise readable conversation history disappear from the UI.
        }
      }
      return parsed;
    }
    return { version: 1, conversations: {}, activeId: null };
  } catch {
    return { version: 1, conversations: {}, activeId: null };
  }
}

/** Each conversation's saved JSON, reused while it is unchanged. */
const savedJson = new Map<string, { conversation: Conversation; updatedAt: number; count: number; json: string }>();
let pendingSave: ConversationStore | null = null;
let saveScheduled = false;

function writeConversations(store: ConversationStore): void {
  // Images are session-only. A screenshot is 1-3MB of base64 and a handful
  // would quietly exhaust the localStorage quota, taking the whole
  // conversation history with them.
  //
  // Only a conversation that changed is encoded again: the other 170 are
  // the text they were last time.
  const parts: string[] = [];
  const live = new Set<string>();
  for (const [id, conversation] of Object.entries(store.conversations)) {
    live.add(id);
    const last = conversation.messages[conversation.messages.length - 1];
    const known = savedJson.get(id);
    let json: string;
    if (
      known &&
      known.conversation === conversation &&
      known.updatedAt === conversation.updatedAt &&
      known.count === conversation.messages.length &&
      last === conversation.messages[conversation.messages.length - 1]
    ) {
      json = known.json;
    } else {
      json = JSON.stringify(withoutImages(conversation));
      savedJson.set(id, {
        conversation,
        updatedAt: conversation.updatedAt,
        count: conversation.messages.length,
        json,
      });
    }
    parts.push(`${JSON.stringify(id)}:${json}`);
  }
  for (const id of savedJson.keys()) if (!live.has(id)) savedJson.delete(id);
  const head = JSON.stringify({ version: store.version, activeId: store.activeId ?? null });
  localStorage.setItem(
    CONVERSATIONS_KEY,
    `${head.slice(0, -1)},"conversations":{${parts.join(',')}}}`,
  );
}

function flushConversations(): void {
  saveScheduled = false;
  const store = pendingSave;
  pendingSave = null;
  if (!store) return;
  try {
    writeConversations(store);
  } catch {
    // Storage full or read-only: the chats stay usable in memory.
  }
}

/**
 * Save when the page is idle rather than in the middle of a frame, and once
 * for a burst of changes. A browser without idle callbacks (and the tests)
 * saves at once. Anything pending is written before the page goes away.
 */
function saveConversations(store: ConversationStore): void {
  pendingSave = store;
  const idle = typeof window !== 'undefined'
    ? (window as Window & { requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number }).requestIdleCallback
    : undefined;
  if (typeof idle !== 'function') {
    flushConversations();
    return;
  }
  if (saveScheduled) return;
  saveScheduled = true;
  idle.call(window, flushConversations, { timeout: 2000 });
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('pagehide', flushConversations);
  window.addEventListener('beforeunload', flushConversations);
}

export type ThemeMode = 'light' | 'dark' | 'system';

export type OrbDesign = 'constellation' | 'cloud';

interface Settings {
  theme: ThemeMode;
  apiUrl: string;
  // Local server API key (OPENJARVIS_API_KEY). Sent as a Bearer token on
  // /v1 + /api requests so a key-protected `jarvis serve` doesn't 401 the
  // frontend (#266). Empty = no auth header (keyless local default).
  apiKey: string;
  fontSize: 'small' | 'default' | 'large';
  /** Which orb Sage draws. 'constellation' is a particle shell webbed with
   * lines and lit in patches; 'cloud' is the original particle cloud. */
  orbDesign: OrbDesign;
  /** Spikes on the constellation while Sage speaks: short fans thrown out
   * from where a pulse is travelling. Off keeps the silhouette a circle. */
  orbSpikes: boolean;
  defaultModel: string;
  // Cloud is ~6x faster at Sage's real prompt sizes (11.7s vs 1.9s measured on
  // the same question at ~6,200 input tokens), so it is the default. Falls
  // back to the local model automatically whenever cloud is unavailable.
  preferCloudModel: boolean;
  cloudModel: string;
  // Which model answers a turn with an image attached. Cloud by default: it
  // is the stronger reasoner and needs no local download. Switching to local
  // keeps the picture on this machine, which is the reason to want it.
  visionUseLocal: boolean;
  visionLocalModel: string;
  defaultAgent: string;
  temperature: number;
  maxTokens: number;
  speechEnabled: boolean;
  wakeWordEnabled: boolean;
  wakeWordGreetingEnabled: boolean;
  /**
   * Second opinion on every wake-word firing: the last two seconds are
   * transcribed and must contain the phrase. Local is a small dedicated
   * model on the GPU (~130 ms); off is the bare detector.
   */
  wakeWordVerify: WakeWordVerify;
  /** Seconds the microphone stays open after "Hey Sage" with nothing said. */
  wakeWordListenSeconds: number;
  /**
   * "Hey Sage, any news?" in one breath: the turn starts from the wake
   * phrase itself and "Yes, Sir?" plays only after a pause.
   */
  wakeWordFastFollow: boolean;
  /** Sage may draw a diagram at all. Off means it never does, and the
   * instruction is left out of the prompt entirely. */
  /** Extra microphone gain on top of the automatic level-matching, 1-4.
   * For a laptop mic across the room when the automatic ceiling is not
   * enough on its own. */
  micBoost: number;
  /** Where the browser strips steady background noise:
   * 'conversation' (default), 'all' (also the wake word) or 'off'. */
  noiseSuppression: 'conversation' | 'all' | 'off';
  diagramsEnabled: boolean;
  /** Sage decides when a diagram helps. Off means it draws one only when
   * asked ("show me how", "illustrate that"). */
  diagramsAutomatic: boolean;
  continuousConversationEnabled: boolean;
  /** Seconds the microphone stays open for a follow-up after a reply. */
  continuousListenSeconds: number;
  // Speak replies to voice-originated turns. On by default -- it is the point
  // of talking to Sage -- but a streamed reply has no player, so this and the
  // stop control are the only ways to silence it.
  voiceRepliesEnabled: boolean;
  // Speak replies to typed messages too, not just spoken ones. Off by
  // default: someone typing in a quiet room has not asked to be talked at.
  // voiceRepliesEnabled still wins, so muting silences this as well.
  speakTypedReplies: boolean;
  /** Talking over a spoken reply cuts it (Flux voice mode). */
  bargeInEnabled: boolean;
  /** How sure the words must be before they cut (lib/barge-in.ts). */
  bargeInMode: BargeMode;
  ttsVoiceId: string;
  sttProvider: SttProvider;
  ttsProvider: TtsProvider;
  // Derived from sttProvider (see normaliseSpeechProviders): true for any
  // streaming provider. Kept because it gates the streaming socket.
  fluxEnabled: boolean;
  // Speculative EagerEndOfTurn work. Dependent on fluxEnabled, and separately
  // opt-in because it can start extra cloud LLM generations that are
  // discarded when the speaker resumes.
  fluxEagerEnabled: boolean;
}

function loadSettings(): Settings {
  const defaults: Settings = {
    theme: 'system',
    apiUrl: '',
    apiKey: '',
    fontSize: 'default',
    orbDesign: 'constellation',
    orbSpikes: true,
    defaultModel: 'qwen3.5:4b',
    preferCloudModel: true,
    cloudModel: DEFAULT_CLOUD_MODEL,
    defaultAgent: '',
    temperature: 0.7,
    maxTokens: 4096,
    speechEnabled: false,
    wakeWordEnabled: false,
    wakeWordGreetingEnabled: true,
    continuousConversationEnabled: false,
    visionUseLocal: false,
    visionLocalModel: 'qwen3-vl:8b',
    voiceRepliesEnabled: true,
    speakTypedReplies: false,
    bargeInEnabled: true,
    bargeInMode: DEFAULT_BARGE_MODE,
    wakeWordVerify: 'local',
    wakeWordListenSeconds: DEFAULT_LISTEN_SECONDS,
    wakeWordFastFollow: true,
    micBoost: 1,
    noiseSuppression: 'conversation',
    diagramsEnabled: true,
    diagramsAutomatic: true,
    continuousListenSeconds: DEFAULT_LISTEN_SECONDS,
    ttsVoiceId: DEFAULT_VOICE_PROFILE.id,
    sttProvider: 'whisper',
    ttsProvider: 'cartesia',
    fluxEnabled: false,
    fluxEagerEnabled: false,
  };
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw);
    // An empty string is a legacy/unset value, not a deliberate choice to
    // clear the default model — fall back to defaults.defaultModel instead
    // of letting a stale '' persist through the merge below.
    const merged = {
      ...defaults,
      ...parsed,
      defaultModel: parsed.defaultModel || defaults.defaultModel,
      ttsVoiceId: isKnownVoiceId(parsed.ttsVoiceId)
        ? parsed.ttsVoiceId
        : defaults.ttsVoiceId,
      bargeInMode: BARGE_MODES.includes(parsed.bargeInMode)
        ? parsed.bargeInMode
        : DEFAULT_BARGE_MODE,
      wakeWordVerify: WAKE_WORD_VERIFY_MODES.includes(parsed.wakeWordVerify)
        ? parsed.wakeWordVerify
        : defaults.wakeWordVerify,
      wakeWordListenSeconds: clampSeconds(parsed.wakeWordListenSeconds, DEFAULT_LISTEN_SECONDS),
      continuousListenSeconds: clampSeconds(
        parsed.continuousListenSeconds,
        DEFAULT_LISTEN_SECONDS,
      ),
    };
    // Saved before the orb could be chosen: take the new default rather
    // than leaving the key undefined and the canvas blank.
    if (merged.orbDesign !== 'cloud' && merged.orbDesign !== 'constellation') {
      merged.orbDesign = defaults.orbDesign;
    }
    if (typeof merged.orbSpikes !== 'boolean') merged.orbSpikes = defaults.orbSpikes;
    // Settings saved before providers existed carry only fluxEnabled.
    if (!STT_PROVIDERS.includes(parsed.sttProvider)) {
      merged.sttProvider = parsed.fluxEnabled ? 'flux' : 'whisper';
    }
    if (!TTS_PROVIDERS.includes(parsed.ttsProvider)) {
      merged.ttsProvider = defaults.ttsProvider;
    }
    return normaliseSpeechProviders(merged);
  } catch {
    return defaults;
  }
}

function saveSettings(settings: Settings): void {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

// ── Store ─────────────────────────────────────────────────────────────

const INITIAL_STREAM: StreamState = {
  conversationId: null,
  isStreaming: false,
  phase: '',
  elapsedMs: 0,
  activeToolCalls: [],
  content: '',
};

interface AppState {
  // Conversations
  conversations: Conversation[];
  activeId: string | null;
  messages: ChatMessage[];
  streamState: StreamState;

  // Models & server
  models: ModelInfo[];
  modelsLoading: boolean;
  selectedModel: string;
  cloudModelAvailable: boolean;
  serverInfo: ServerInfo | null;
  savings: SavingsData | null;

  // Settings
  settings: Settings;

  // Command palette
  commandPaletteOpen: boolean;

  // Sidebar
  sidebarOpen: boolean;

  // Mirrors useSpeech()'s local state so components outside the composer
  // (the orb) can react to mic activity without lifting the whole hook.
  voiceState: 'idle' | 'recording' | 'transcribing';
  // Mirrors whether any AudioPlayer (TTS voice reply) is actually playing,
  // so the orb's "speaking" state tracks real spoken audio, not just
  // token-streaming duration.
  audioPlaying: boolean;
  /** What the server believes about the desk (M36): 'present', 'away', 'disabled', 'unknown'. */
  presenceState: string;
  /** The server is saying something aloud -- a reminder, a schedule notice,
   * a moment. Moves the orb only: unlike audioPlaying it never re-arms the
   * microphone (see lib/server-voice.ts). */
  serverSpeaking: boolean;
  /** ms timestamp: a spoken moment just ended and a reply may be listened for. */
  replyWindowAt: number | null;
  // Each playback path owns a separate claim. A boolean alone allowed an old
  // stream or player cleanup to mark newer audio idle while it was audible.
  audioPlaybackOwners: Record<string, true>;

  // Opt-in sharing
  optInEnabled: boolean;
  optInDisplayName: string;
  optInEmail: string;
  optInAnonId: string;
  optInModalSeen: boolean;
  optInModalOpen: boolean;

  // Actions: conversations
  loadConversations: () => void;
  importOverlayConversation: () => Promise<void>;
  createConversation: (model?: string) => string;
  startNewChat: () => void;
  selectConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
  togglePinConversation: (id: string) => void;
  loadMessages: (conversationId: string | null) => void;
  addMessage: (conversationId: string, message: ChatMessage) => void;
  /**
   * Drop the last user turn and any reply to it. Used when a voice turn was
   * cut off mid-sentence and the rest arrived after Sage had already
   * started answering: the half-question and its half-answer are withdrawn
   * so the merged question can be sent as one turn.
   */
  retractLastExchange: (conversationId: string) => string | null;
  updateLastAssistant: (
    conversationId: string,
    content: string,
    toolCalls?: ToolCallInfo[],
    usage?: TokenUsage,
    telemetry?: MessageTelemetry,
    audio?: { url: string; autoPlay?: boolean },
    researchTraces?: ResearchSearchTrace[],
    researchSources?: ResearchSource[],
  ) => void;
  setStreamState: (state: Partial<StreamState>) => void;
  resetStream: () => void;

  // Deep Research toggle
  deepResearch: boolean;
  setDeepResearch: (on: boolean) => void;

  // Actions: models & server
  setModels: (models: ModelInfo[]) => void;
  setModelsLoading: (loading: boolean) => void;
  setSelectedModel: (model: string) => void;
  setCloudModelAvailable: (available: boolean) => void;
  setServerInfo: (info: ServerInfo | null) => void;
  setPresenceState: (state: string) => void;
  setServerSpeaking: (speaking: boolean) => void;
  requestReplyWindow: (at: number) => void;
  setSavings: (data: SavingsData | null) => void;
  incrementSavings: (usage: TokenUsage) => void;

  // Live GPU metrics — streamed from /api/research system_metrics events.
  // When non-null, the System panel renders this instead of polled values
  // so Power (W) and Energy (kJ) update in real time during a research run.
  liveEnergy: LiveEnergyMetrics | null;
  setLiveEnergy: (data: LiveEnergyMetrics | null) => void;

  // Actions: settings
  updateSettings: (partial: Partial<Settings>) => void;

  // Actions: UI
  setCommandPaletteOpen: (open: boolean) => void;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setVoiceState: (state: 'idle' | 'recording' | 'transcribing') => void;
  setAudioPlayback: (owner: string, playing: boolean) => void;

  // Data sources (cached between visits to avoid empty-state flicker)
  cachedConnectors: CachedConnector[] | null;
  setCachedConnectors: (list: CachedConnector[] | null) => void;

  // Agents
  managedAgents: ManagedAgent[];
  managedAgentsLoading: boolean;
  selectedAgentId: string | null;

  // Actions: agents
  setManagedAgents: (agents: ManagedAgent[]) => void;
  setManagedAgentsLoading: (loading: boolean) => void;
  setSelectedAgentId: (id: string | null) => void;

  // Agent events (live stream)
  agentEvents: AgentEvent[];
  addAgentEvent: (event: AgentEvent) => void;
  clearAgentEvents: () => void;

  // Actions: opt-in sharing
  setOptIn: (enabled: boolean, displayName: string, email: string) => void;
  setOptInModalOpen: (open: boolean) => void;
  markOptInModalSeen: () => void;

  // Logs
  logEntries: LogEntry[];
  addLogEntry: (entry: LogEntry) => void;
  clearLogs: () => void;

  // Model loading
  modelLoading: boolean;
  setModelLoading: (loading: boolean) => void;
}

export const useAppStore = create<AppState>((set, get) => {
  const initial = loadConversations();
  // An empty conversation carries nothing and is only ever an artefact of
  // creating one eagerly instead of on first message. Drop them at load so
  // the sidebar cannot fill with "New chat" rows.
  const emptyIds = Object.values(initial.conversations)
    .filter((c) => c.messages.length === 0)
    .map((c) => c.id);
  if (emptyIds.length > 0) {
    for (const id of emptyIds) delete initial.conversations[id];
    if (initial.activeId && emptyIds.includes(initial.activeId)) {
      initial.activeId = null;
    }
    saveConversations(initial);
  }
  const convList = Object.values(initial.conversations).sort(
    (a, b) => b.updatedAt - a.updatedAt,
  );

  return {
    conversations: convList,
    // Opening or refreshing the UI starts a fresh chat rather than dropping
    // you back into whatever was on screen days ago. The history is still in
    // `conversations` and reachable from the sidebar; only the landing point
    // changes.
    activeId: null,
    messages: [],
    streamState: INITIAL_STREAM,

    models: [],
    modelsLoading: true,
    selectedModel: '',
    cloudModelAvailable: false,
    serverInfo: null,
    savings: null,

    settings: loadSettings(),

    commandPaletteOpen: false,
    sidebarOpen: true,
    voiceState: 'idle',
    audioPlaying: false,
    presenceState: 'unknown',
    serverSpeaking: false,
    replyWindowAt: null,
    audioPlaybackOwners: {},

    optInEnabled: localStorage.getItem(OPTIN_KEY) === 'true',
    optInDisplayName: localStorage.getItem(OPTIN_NAME_KEY) || '',
    optInEmail: localStorage.getItem(OPTIN_EMAIL_KEY) || '',
    optInAnonId: localStorage.getItem(OPTIN_ANONID_KEY) || crypto.randomUUID(),
    optInModalSeen: localStorage.getItem(OPTIN_SEEN_KEY) === 'true',
    optInModalOpen: false,

    // ── Conversations ───────────────────────────────────────────────

    loadConversations: () => {
      const store = loadConversations();
      set({
        conversations: Object.values(store.conversations).sort(
          (a, b) => b.updatedAt - a.updatedAt,
        ),
        activeId: store.activeId,
      });
    },

    importOverlayConversation: async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        const raw = await invoke<string>('get_overlay_conversation');
        if (!raw || raw === '[]') return;
        const overlay = JSON.parse(raw);
        if (!overlay.id || !overlay.messages?.length) return;
        const store = loadConversations();
        const existing = store.conversations[overlay.id];
        // Only update if the overlay has newer/more messages
        if (existing && existing.messages.length >= overlay.messages.length) return;
        // Track first use of overlay for this conversation
        if (!existing) {
          import('../lib/analytics').then(({ track }) => {
            track('feature_used', { feature_name: 'overlay' });
          });
        }
        store.conversations[overlay.id] = {
          id: overlay.id,
          title: overlay.title || 'Overlay chat',
          createdAt: overlay.createdAt || Date.now(),
          updatedAt: overlay.updatedAt || Date.now(),
          model: overlay.model || 'default',
          messages: overlay.messages,
        };
        saveConversations(store);
        set({
          conversations: Object.values(store.conversations).sort(
            (a, b) => b.updatedAt - a.updatedAt,
          ),
        });
      } catch {
        // Overlay command unavailable (non-Tauri or no overlay data)
      }
    },

    // Clears the active thread without writing a row. The composer creates
    // one lazily on the first message (`activeId ?? createConversation(...)`),
    // so nothing is persisted until something is actually said — the same
    // rule the sidebar's "+" already follows.
    startNewChat: () => set({ activeId: null, messages: [] }),

    createConversation: (model?: string) => {
      const store = loadConversations();
      const conv: Conversation = {
        id: generateId(),
        title: 'New chat',
        createdAt: Date.now(),
        updatedAt: Date.now(),
        model: model || get().selectedModel || 'default',
        messages: [],
      };
      store.conversations[conv.id] = conv;
      store.activeId = conv.id;
      saveConversations(store);
      set({
        conversations: Object.values(store.conversations).sort(
          (a, b) => b.updatedAt - a.updatedAt,
        ),
        activeId: conv.id,
        messages: [],
      });
      return conv.id;
    },

    selectConversation: (id: string) => {
      const store = loadConversations();
      store.activeId = id;
      saveConversations(store);
      const conv = store.conversations[id];
      set({
        activeId: id,
        messages: conv ? withoutAutoPlay(conv.messages) : [],
      });
    },

    togglePinConversation: (id: string) => {
      const store = loadConversations();
      const conv = store.conversations[id];
      if (!conv) return;
      // A new object: the save reuses a conversation's last JSON while the
      // object and its updatedAt are unchanged, and pinning is not an update
      // (it must not move the chat to the top of its day).
      store.conversations[id] = { ...conv, pinned: !conv.pinned };
      saveConversations(store);
      set({
        conversations: Object.values(store.conversations).sort(
          (a, b) => b.updatedAt - a.updatedAt,
        ),
      });
    },

    deleteConversation: (id: string) => {
      const streamState = get().streamState;
      if (streamState.isStreaming && streamState.conversationId === id) return;

      const store = loadConversations();
      delete store.conversations[id];
      if (store.activeId === id) {
        const remaining = Object.keys(store.conversations);
        store.activeId = remaining.length > 0 ? remaining[0] : null;
      }
      saveConversations(store);
      const convList = Object.values(store.conversations).sort(
        (a, b) => b.updatedAt - a.updatedAt,
      );
      const activeConv = store.activeId
        ? store.conversations[store.activeId]
        : null;
      set({
        conversations: convList,
        activeId: store.activeId,
        messages: activeConv ? withoutAutoPlay(activeConv.messages) : [],
      });
    },

    loadMessages: (conversationId: string | null) => {
      if (!conversationId) {
        set({ messages: [] });
        return;
      }
      const store = loadConversations();
      const conv = store.conversations[conversationId];
      set({ messages: conv ? withoutAutoPlay(conv.messages) : [] });
    },

    addMessage: (conversationId: string, message: ChatMessage) => {
      const store = loadConversations();
      const conv = store.conversations[conversationId];
      if (!conv) return;
      // Held aside rather than on the message: this store round-trips through
      // localStorage, so an image left here would be persisted or lost.
      if (message.images?.length) {
        rememberImages(message.id, message.images);
      }
      // Documents ride the stored message so they survive a reload; only an
      // unusually large one is held aside, to stay clear of the quota.
      const documents = message.documents ?? [];
      const tooBig =
        documents.reduce((n, d) => n + d.text.length, 0) >
        MAX_PERSISTED_DOCUMENT_CHARS;
      if (documents.length && tooBig) {
        rememberDocuments(message.id, documents);
      }
      const { images: _ephemeral, ...rest } = message;
      const persisted = tooBig ? { ...rest, documents: undefined } : rest;
      conv.messages.push(persisted);
      conv.updatedAt = Date.now();
      if (message.role === 'user' && conv.title === 'New chat') {
        conv.title =
          message.content.slice(0, 50) +
          (message.content.length > 50 ? '...' : '');
      }
      saveConversations(store);
      const conversations = Object.values(store.conversations).sort(
        (a, b) => b.updatedAt - a.updatedAt,
      );
      if (get().activeId === conversationId) {
        set({ messages: [...conv.messages], conversations });
      } else {
        set({ conversations });
      }
    },

    retractLastExchange: (conversationId: string) => {
      const store = loadConversations();
      const conv = store.conversations[conversationId];
      if (!conv || conv.messages.length === 0) return null;
      // Walk back over any assistant replies to the last user turn.
      let idx = conv.messages.length - 1;
      while (idx >= 0 && conv.messages[idx].role !== 'user') idx -= 1;
      if (idx < 0) return null;
      const userText = conv.messages[idx].content;
      conv.messages.splice(idx);
      conv.updatedAt = Date.now();
      saveConversations(store);
      const conversations = Object.values(store.conversations).sort(
        (a, b) => b.updatedAt - a.updatedAt,
      );
      if (get().activeId === conversationId) {
        set({ messages: [...conv.messages], conversations });
      } else {
        set({ conversations });
      }
      return userText;
    },

    updateLastAssistant: (
      conversationId: string,
      content: string,
      toolCalls?: ToolCallInfo[],
      usage?: TokenUsage,
      telemetry?: MessageTelemetry,
      audio?: { url: string; autoPlay?: boolean },
      researchTraces?: ResearchSearchTrace[],
      researchSources?: ResearchSource[],
    ) => {
      const store = loadConversations();
      const conv = store.conversations[conversationId];
      if (!conv) return;
      const lastMsg = conv.messages[conv.messages.length - 1];
      if (lastMsg && lastMsg.role === 'assistant') {
        // A new object, not an edit: the conversations are kept in memory
        // now, and the message already on screen is this same object -- a
        // bubble memoised on it would never see the reply grow.
        conv.messages[conv.messages.length - 1] = {
          ...lastMsg,
          content,
          ...(toolCalls ? { toolCalls } : {}),
          ...(usage ? { usage } : {}),
          ...(telemetry ? { telemetry } : {}),
          ...(audio ? { audio } : {}),
          ...(researchTraces ? { researchTraces } : {}),
          ...(researchSources ? { researchSources } : {}),
        };
        conv.updatedAt = Date.now();
        saveConversations(store);
        if (get().activeId === conversationId) {
          set({ messages: [...conv.messages] });
        }
      }
    },

    setStreamState: (partial: Partial<StreamState>) => {
      set((s) => ({ streamState: { ...s.streamState, ...partial } }));
    },

    resetStream: () => {
      set({ streamState: INITIAL_STREAM });
    },

    // ── Deep Research ─────────────────────────────────────────────
    deepResearch: false,
    setDeepResearch: (on: boolean) => set({ deepResearch: on }),

    // ── Models & server ────────────────────────────────────────────

    setModels: (models: ModelInfo[]) =>
      set((state) => {
        // Ollama returns embed-only models (e.g. nomic-embed-text) in the
        // same list as chat models. Auto-picking models[0] selected the
        // embedder and every chat failed with HTTP 400 "does not support
        // chat". Prefer a real chat model for selection / fallback.
        const chatModels = models.filter((m) => !isEmbedOnlyModel(m.id));
        const preferred =
          preferredModelId(
            chatModels.map((m) => m.id),
            {
              preferCloudModel: state.settings.preferCloudModel,
              cloudModel: state.settings.cloudModel,
              localModel: state.settings.defaultModel,
              cloudAvailable: state.cloudModelAvailable,
            },
          ) ||
          models.find((m) => !isEmbedOnlyModel(m.id))?.id ||
          '';

        const currentIsBad =
          !!state.selectedModel && isEmbedOnlyModel(state.selectedModel);
        const currentMissing =
          !!state.selectedModel &&
          !models.some((m) => m.id === state.selectedModel);

        if (!state.selectedModel || currentIsBad || currentMissing) {
          // Prefer a real chat model. If none exist, clear a bad/missing
          // selection rather than keeping an embed-only id that 400s on chat.
          return {
            models,
            selectedModel: preferred,
          };
        }
        return { models };
      }),
    setModelsLoading: (loading: boolean) => set({ modelsLoading: loading }),
    setSelectedModel: (model: string) => set({ selectedModel: model }),

    // Cloud models are deliberately absent from /v1/models, so availability
    // comes from the provider key instead. Arrives after the model list, so
    // the selection is revisited once it does.
    setCloudModelAvailable: (available: boolean) =>
      set((state) => {
        if (state.cloudModelAvailable === available) return {};
        const next: Partial<AppState> = { cloudModelAvailable: available };
        const target = preferredModelId(
          state.models.filter((m) => !isEmbedOnlyModel(m.id)).map((m) => m.id),
          {
            preferCloudModel: state.settings.preferCloudModel,
            cloudModel: state.settings.cloudModel,
            localModel: state.settings.defaultModel,
            cloudAvailable: available,
          },
        );
        if (target && target !== state.selectedModel) next.selectedModel = target;
        return next;
      }),
    setServerInfo: (info: ServerInfo | null) => set({ serverInfo: info }),
    setPresenceState: (state: string) => set({ presenceState: state }),
    setServerSpeaking: (speaking: boolean) => set({ serverSpeaking: speaking }),
    requestReplyWindow: (at: number) => set({ replyWindowAt: at }),
    setSavings: (data: SavingsData | null) => set({ savings: data }),
    incrementSavings: (usage: TokenUsage) => {
      const cur = get().savings;
      const prompt = usage.prompt_tokens ?? 0;
      const completion = usage.completion_tokens ?? 0;
      const total = usage.total_tokens ?? prompt + completion;
      set({
        savings: {
          total_calls: (cur?.total_calls ?? 0) + 1,
          total_prompt_tokens: (cur?.total_prompt_tokens ?? 0) + prompt,
          total_completion_tokens: (cur?.total_completion_tokens ?? 0) + completion,
          total_tokens: (cur?.total_tokens ?? 0) + total,
          local_cost: cur?.local_cost ?? 0,
          per_provider: cur?.per_provider ?? [],
          token_counting_version: cur?.token_counting_version,
        },
      });
    },

    liveEnergy: null,
    setLiveEnergy: (data: LiveEnergyMetrics | null) => set({ liveEnergy: data }),

    cachedConnectors: null,
    setCachedConnectors: (list) => set({ cachedConnectors: list }),

    // ── Settings ───────────────────────────────────────────────────

    updateSettings: (partial: Partial<Settings>) => {
      const current = get().settings;
      const next = { ...current, ...partial };
      // Older callers toggle fluxEnabled directly; read that as a provider
      // choice so the two never disagree.
      if ('fluxEnabled' in partial && !('sttProvider' in partial)) {
        next.sttProvider = partial.fluxEnabled
          ? current.sttProvider === 'whisper'
            ? 'flux'
            : current.sttProvider
          : 'whisper';
      }
      const updated = normaliseSpeechProviders(next);
      saveSettings(updated);
      set({ settings: updated });
    },

    // ── UI ──────────────────────────────────────────────────────────

    setCommandPaletteOpen: (open: boolean) => set({ commandPaletteOpen: open }),
    toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
    setSidebarOpen: (open: boolean) => set({ sidebarOpen: open }),
    setVoiceState: (state) => set({ voiceState: state }),
    setAudioPlayback: (owner, playing) =>
      set((state) => {
        const alreadyOwned = Boolean(state.audioPlaybackOwners[owner]);
        if (alreadyOwned === playing) return {};

        const owners = { ...state.audioPlaybackOwners };
        if (playing) owners[owner] = true;
        else delete owners[owner];
        return {
          audioPlaybackOwners: owners,
          audioPlaying: Object.keys(owners).length > 0,
        };
      }),

    // ── Agents ─────────────────────────────────────────────────────

    managedAgents: [],
    managedAgentsLoading: false,
    selectedAgentId: null,

    setManagedAgents: (agents) => set({ managedAgents: agents }),
    setManagedAgentsLoading: (loading) => set({ managedAgentsLoading: loading }),
    setSelectedAgentId: (id) => set({ selectedAgentId: id }),

    agentEvents: [],
    addAgentEvent: (event) => set((s) => ({
      agentEvents: [...s.agentEvents.slice(-99), event],
    })),
    clearAgentEvents: () => set({ agentEvents: [] }),

    // ── Logs ────────────────────────────────────────────────────────
    logEntries: [],
    addLogEntry: (entry) => set((s) => ({
      logEntries: [...s.logEntries.slice(-499), entry],
    })),
    clearLogs: () => set({ logEntries: [] }),

    // ── Model loading ───────────────────────────────────────────────
    modelLoading: false,
    setModelLoading: (loading) => set({ modelLoading: loading }),

    // ── Opt-in sharing ──────────────────────────────────────────────

    setOptIn: (enabled: boolean, displayName: string, email: string) => {
      const anonId = get().optInAnonId;
      localStorage.setItem(OPTIN_KEY, String(enabled));
      localStorage.setItem(OPTIN_NAME_KEY, displayName);
      localStorage.setItem(OPTIN_EMAIL_KEY, email);
      localStorage.setItem(OPTIN_ANONID_KEY, anonId);
      set({ optInEnabled: enabled, optInDisplayName: displayName, optInEmail: email });
    },
    setOptInModalOpen: (open: boolean) => set({ optInModalOpen: open }),
    markOptInModalSeen: () => {
      localStorage.setItem(OPTIN_SEEN_KEY, 'true');
      set({ optInModalSeen: true });
    },
  };
});

export { generateId };

// A hot swap of this module leaves two copies alive: the voice player then
// updates one store while the orb and the microphone read the other, and
// Sage speaks with the orb on "standing by" and the mic closed (24
// September, after an edit to the store). Reload the page instead.
if (import.meta.hot) import.meta.hot.accept(() => window.location.reload());
