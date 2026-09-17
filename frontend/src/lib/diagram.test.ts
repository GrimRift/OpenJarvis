import { describe, expect, it } from 'vitest';
import { MAX_MARKS, MAX_NODES, activeNodeIndex, parseDiagram } from './diagram';

const flow = JSON.stringify({
  shape: 'flow',
  title: 'How a crack heals',
  nodes: [
    { label: 'A crack forms', note: 'Stress opens a gap.' },
    { label: 'Water gets in', mark: 'trigger', icon: 'droplet' },
    { label: 'Limestone forms', mark: 'result' },
  ],
});

describe('parseDiagram', () => {
  it('reads a flow', () => {
    const d = parseDiagram(flow);
    expect(d?.shape).toBe('flow');
    expect(d?.title).toBe('How a crack heals');
    expect(d?.nodes.map((n) => n.label)).toEqual([
      'A crack forms',
      'Water gets in',
      'Limestone forms',
    ]);
    expect(d?.nodes[1].mark).toBe('trigger');
  });

  it('gives up quietly on anything unusable', () => {
    // Half-streamed JSON is the common case, not an error.
    expect(parseDiagram('{"shape":"flow","ti')).toBeNull();
    expect(parseDiagram('')).toBeNull();
    expect(parseDiagram('"a string"')).toBeNull();
    expect(parseDiagram('{"title":"No nodes"}')).toBeNull();
    expect(parseDiagram('{"title":"One node","nodes":[{"label":"Alone"}]}')).toBeNull();
    expect(parseDiagram('{"nodes":[{"label":"A"},{"label":"B"}]}')).toBeNull();
  });

  it('keeps colour rare and the box count sane', () => {
    const many = parseDiagram(
      JSON.stringify({
        title: 'Too much',
        nodes: Array.from({ length: 14 }, (_, i) => ({
          label: `Step number ${i}`,
          mark: 'result',
        })),
      }),
    );
    expect(many?.nodes).toHaveLength(MAX_NODES);
    expect(many?.nodes.filter((n) => n.mark).length).toBe(MAX_MARKS);
  });

  it('drops a note that only repeats the label', () => {
    const d = parseDiagram(
      '{"title":"T","nodes":[{"label":"Water","note":"water"},{"label":"Sand"}]}',
    );
    expect(d?.nodes[0].note).toBeUndefined();
  });

  it('falls back to a flow when parts or comparison are incomplete', () => {
    // "Parts of nothing" and a one-sided comparison are flows that
    // mislabelled themselves; the content is still worth drawing.
    const parts = parseDiagram('{"shape":"parts","title":"T","nodes":[{"label":"Cement"},{"label":"Sand"}]}');
    expect(parts?.shape).toBe('flow');
    const lopsided = parseDiagram(
      JSON.stringify({
        shape: 'comparison',
        title: 'T',
        sides: ['Refractor', 'Reflector'],
        nodes: [{ label: 'Lens', side: 0 }, { label: 'Glass', side: 0 }],
      }),
    );
    expect(lopsided?.shape).toBe('flow');
  });

  it('keeps a whole parts and comparison diagram', () => {
    const parts = parseDiagram(
      JSON.stringify({
        shape: 'parts',
        title: 'What concrete is made of',
        subject: 'Concrete',
        nodes: [{ label: 'Cement' }, { label: 'Aggregate' }, { label: 'Water' }],
      }),
    );
    expect([parts?.shape, parts?.subject]).toEqual(['parts', 'Concrete']);
    const vs = parseDiagram(
      JSON.stringify({
        shape: 'comparison',
        title: 'Two telescopes',
        sides: ['Refractor', 'Reflector'],
        nodes: [{ label: 'Uses a lens', side: 0 }, { label: 'Uses a mirror', side: 1 }],
      }),
    );
    expect(vs?.sides).toEqual(['Refractor', 'Reflector']);
    expect(vs?.nodes[1].side).toBe(1);
  });
});

describe('activeNodeIndex', () => {
  const nodes = [
    { label: 'A crack forms' },
    { label: 'Water gets in' },
    { label: 'Dormant spores wake' },
  ];

  it('lights a step only on a word that belongs to it alone', () => {
    expect(activeNodeIndex('moisture reaches the spores now', nodes)).toBe(2);
    expect(activeNodeIndex('first a crack opens', nodes)).toBe(0);
  });

  it('follows the latest thing said', () => {
    expect(activeNodeIndex('a crack opens, then water arrives', nodes)).toBe(1);
  });

  it('stays dark when nothing identifies a step', () => {
    expect(activeNodeIndex('', nodes)).toBe(-1);
    expect(activeNodeIndex('let me explain the whole process', nodes)).toBe(-1);
    // "forms" is a stop word, and a word two steps share proves nothing.
    const shared = [{ label: 'Calcium forms' }, { label: 'Carbonate forms' }];
    expect(activeNodeIndex('it forms quickly', shared)).toBe(-1);
  });
});

describe('what the model really sent', () => {
  // Captured live on 18 September. A comparison names the same row on both
  // sides, which is why nothing downstream may identify a node by its label.
  const real = JSON.stringify({
    shape: 'comparison',
    title: 'Refractor versus reflector telescopes',
    sides: ['Refractor', 'Reflector'],
    nodes: [
      { label: 'Light gathering', note: 'Uses a front lens.', icon: 'eye', side: 0 },
      { label: 'Light gathering', note: 'Uses a curved mirror.', icon: 'eye', side: 1 },
      { label: 'Image quality', note: 'Sharp, high-contrast views.', icon: 'crystal', side: 0 },
      { label: 'Image quality', note: 'Excellent light gathering.', icon: 'spark', side: 1 },
    ],
  });

  it('survives duplicated labels across the two sides', () => {
    const d = parseDiagram(real);
    expect(d?.shape).toBe('comparison');
    expect(d?.nodes).toHaveLength(4);
    expect(d?.nodes.filter((n) => n.side === 1)).toHaveLength(2);
    // Same label, different notes: the pair must both survive.
    expect(d?.nodes[0].note).not.toBe(d?.nodes[1].note);
  });

  it('cannot be identified by label, so the highlight declines', () => {
    const d = parseDiagram(real)!;
    // Every distinguishing word is shared between the two sides, so there is
    // no confident answer and nothing should light up.
    expect(activeNodeIndex('light gathering matters most', d.nodes)).toBe(-1);
  });
});

describe('isCloseDiagramCommand', () => {
  it('hears the ways of asking for it to go', async () => {
    const { isCloseDiagramCommand } = await import('./diagram');
    for (const said of [
      'close the diagram',
      'Close diagram.',
      'close it',
      'hide it',
      'hide the diagram',
      'dismiss the illustration',
      'remove that chart',
      'get rid of the diagram',
      'get rid of it',
      'close that',
    ]) {
      expect(isCloseDiagramCommand(said), said).toBe(true);
    }
  });

  it('does not fire on ordinary talk', async () => {
    const { isCloseDiagramCommand } = await import('./diagram');
    for (const said of [
      '',
      'the diagram shows how water enters the crack',
      'close the deal before Friday',
      'what does the third step mean',
      // A command is short; a sentence that merely contains the words is not
      // someone asking for the overlay to close.
      'I was going to close the diagram but actually tell me more about step two',
      'can you explain that picture in more detail please',
    ]) {
      expect(isCloseDiagramCommand(said), said).toBe(false);
    }
  });
});

describe('rowEndsOn', () => {
  it('alternates, so the wrap arrow hangs under the box the row ended on', async () => {
    const { rowEndsOn } = await import('./diagram');
    // Row 0 runs left-to-right and hands down on the right; row 1 runs back.
    expect(rowEndsOn(0)).toBe('right');
    expect(rowEndsOn(1)).toBe('left');
    expect(rowEndsOn(2)).toBe('right');
  });
});

describe('the close command against what Deepgram really sent', () => {
  it('survives the phantom word that cut Sage off', async () => {
    const { isCloseDiagramCommand } = await import('./diagram');
    // 18 September trace: barge-in cut the reply on
    // "Close:1.00 to:0.80 the:0.98 diagram.:0.98" because the exact-article
    // rule missed the inserted "to".
    expect(isCloseDiagramCommand('Close to the diagram.')).toBe(true);
    expect(isCloseDiagramCommand('close up the diagram')).toBe(true);
    expect(isCloseDiagramCommand('can you close the diagram')).toBe(true);
  });

  it('waits while a command could still be forming', async () => {
    const { mayBecomeCloseDiagramCommand } = await import('./diagram');
    // The partials that arrive before the word "diagram" does.
    expect(mayBecomeCloseDiagramCommand('Close')).toBe(true);
    expect(mayBecomeCloseDiagramCommand('Close to')).toBe(true);
    expect(mayBecomeCloseDiagramCommand('Close to the')).toBe(true);
    // Nothing to do with closing, so barge-in judges it as usual.
    expect(mayBecomeCloseDiagramCommand('what does step two mean')).toBe(false);
    expect(mayBecomeCloseDiagramCommand('')).toBe(false);
    // Too long to be a command: the user is talking, not instructing.
    expect(
      mayBecomeCloseDiagramCommand('I was going to close the diagram but tell me more'),
    ).toBe(false);
  });

  it('still leaves ordinary speech alone', async () => {
    const { isCloseDiagramCommand } = await import('./diagram');
    expect(isCloseDiagramCommand('close the deal before Friday')).toBe(false);
    expect(isCloseDiagramCommand('the diagram shows how water enters')).toBe(false);
  });
});
