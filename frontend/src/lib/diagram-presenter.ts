/**
 * Which diagram is on screen, and what Sage has said while it has been.
 *
 * Kept outside the chat components because two unrelated places drive it: a
 * message renders the card that opens one, and the speech pipeline feeds it
 * the words that decide which step is lit.
 */

import { create } from 'zustand';
import { type Diagram, parseDiagram } from './diagram';

interface DiagramPresenterState {
  current: Diagram | null;
  /** Everything spoken since this diagram opened. */
  spoken: string;
  /** Diagrams already shown once, so scrolling past an old message in the
   * history never reopens it. */
  seen: Set<string>;
  open: (key: string, diagram: Diagram) => void;
  close: () => void;
  /** True when this diagram has not been auto-shown before. */
  isNew: (key: string) => boolean;
  noteSpoken: (text: string) => void;
}

export const useDiagramPresenter = create<DiagramPresenterState>((set, get) => ({
  current: null,
  spoken: '',
  seen: new Set<string>(),

  open: (key, diagram) => {
    const seen = new Set(get().seen);
    seen.add(key);
    set({ current: diagram, spoken: '', seen });
  },

  close: () => set({ current: null, spoken: '' }),

  isNew: (key) => !get().seen.has(key),

  noteSpoken: (text) => {
    if (!text) return;
    // Only while something is open: the transcript is for the highlight, not
    // a log, and it resets with every diagram.
    if (!get().current) return;
    set((state) => ({ spoken: `${state.spoken} ${text}`.slice(-4000) }));
  },
}));

/** A stable key for a diagram, so the same one is never auto-shown twice. */
export function diagramKey(messageId: string, source: string): string {
  return `${messageId}:${source.length}`;
}

const BLOCK = /```sage-diagram\s*([\s\S]*?)```/;

/** The diagram inside an answer, if it holds a finished one. */
export function diagramSourceIn(content: string): string | null {
  const found = BLOCK.exec(content || '');
  return found ? found[1].trim() : null;
}

/**
 * Show the diagram an answer carries, wherever that answer is displayed.
 *
 * The chat bubble's card can open one because it renders the block, but the
 * Voice page draws its own transcript and has no bubble -- so on Voice the
 * diagram never appeared at all. Opening from the answer itself covers both
 * surfaces; the `seen` key keeps the two routes from showing it twice.
 */
export function openDiagramFromAnswer(messageId: string, content: string): void {
  const source = diagramSourceIn(content);
  if (!source) return;
  const state = useDiagramPresenter.getState();
  const key = diagramKey(messageId, source);
  if (!state.isNew(key)) return;
  const diagram = parseDiagram(source);
  if (diagram) state.open(key, diagram);
}
