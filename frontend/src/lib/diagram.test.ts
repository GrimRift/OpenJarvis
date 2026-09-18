import { describe, expect, it } from 'vitest';
import {
  MAX_COLUMNS,
  MAX_MARKS,
  MAX_NODES,
  MAX_ROWS,
  activeNodeIndex,
  parseDiagram,
} from './diagram';

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
    // "Parts of nothing" and a comparison with no grid are flows that
    // mislabelled themselves; the content is still worth drawing.
    const parts = parseDiagram('{"shape":"parts","title":"T","nodes":[{"label":"Cement"},{"label":"Sand"}]}');
    expect(parts?.shape).toBe('flow');
    const noGrid = parseDiagram(
      JSON.stringify({
        shape: 'comparison',
        title: 'T',
        nodes: [{ label: 'Lens' }, { label: 'Mirror' }],
      }),
    );
    expect(noGrid?.shape).toBe('flow');
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
    shape: 'flow',
    title: 'How Sage handles a request',
    nodes: [
      { label: 'Understand request', note: 'I identify your goal.', icon: 'eye' },
      { label: 'Choose capability', note: 'I select the tool needed.', icon: 'gear' },
      { label: 'Verify result', note: 'I check whether it worked.', icon: 'check' },
    ],
  });

  it('keeps every node and its note', () => {
    const d = parseDiagram(real);
    expect(d?.shape).toBe('flow');
    expect(d?.nodes).toHaveLength(3);
    expect(d?.nodes[0].note).not.toBe(d?.nodes[1].note);
  });

  it('declines to light a step when nothing identifies one', () => {
    const d = parseDiagram(real)!;
    expect(activeNodeIndex('let me walk you through it', d.nodes)).toBe(-1);
  });
});

describe('a comparison is a grid', () => {
  const cars = JSON.stringify({
    shape: 'comparison',
    title: 'Mazda3 versus Vios and Civic',
    columns: ['Mazda3', 'Toyota Vios', 'Honda Civic'],
    rows: [
      { label: 'Driving feel', cells: ['Most refined', 'Comfortable', 'Sporty'] },
      { label: 'Running cost', cells: ['Moderate', 'Cheapest', 'Moderate'], mark: 'trigger' },
      { label: 'Space', cells: ['Rear seat tight', 'Smaller cabin', 'Most spacious'] },
    ],
  });

  it('holds three things compared, not two', () => {
    // Three cars in two columns is what made the first version read thin:
    // "Vios / Civic" had to share a heading.
    const d = parseDiagram(cars);
    expect(d?.shape).toBe('comparison');
    expect(d?.columns).toEqual(['Mazda3', 'Toyota Vios', 'Honda Civic']);
    expect(d?.rows).toHaveLength(3);
    expect(d?.rows?.[1].mark).toBe('trigger');
  });

  it('pads and trims every row to the column count', () => {
    const ragged = parseDiagram(
      JSON.stringify({
        shape: 'comparison',
        title: 'T',
        columns: ['A', 'B'],
        rows: [
          { label: 'Short', cells: ['only one'] },
          { label: 'Long', cells: ['a', 'b', 'c', 'd'] },
        ],
      }),
    );
    // A ragged row would shift the whole grid sideways.
    expect(ragged?.rows?.map((r) => r.cells.length)).toEqual([2, 2]);
    expect(ragged?.rows?.[0].cells[1]).toBe('');
  });

  it('caps the columns and the rows', () => {
    const huge = parseDiagram(
      JSON.stringify({
        shape: 'comparison',
        title: 'T',
        columns: ['a', 'b', 'c', 'd', 'e', 'f'],
        rows: Array.from({ length: 12 }, (_, i) => ({
          label: `Row ${i}`,
          cells: ['x', 'y', 'z', 'w', 'v', 'u'],
        })),
      }),
    );
    expect(huge?.columns).toHaveLength(MAX_COLUMNS);
    expect(huge?.rows).toHaveLength(MAX_ROWS);
  });

  it('needs two columns and two rows to be a grid at all', () => {
    const thin = parseDiagram(
      JSON.stringify({
        shape: 'comparison',
        title: 'T',
        columns: ['Only one'],
        rows: [{ label: 'A', cells: ['x'] }],
        nodes: [{ label: 'One' }, { label: 'Two' }],
      }),
    );
    expect(thin?.shape).toBe('flow');
  });

  it('carries a short fact on a box', () => {
    const d = parseDiagram(
      '{"title":"T","nodes":[{"label":"Engine","fact":"2.0L gasoline"},{"label":"Body"}]}',
    );
    expect(d?.nodes[0].fact).toBe('2.0L gasoline');
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

describe('finding the diagram inside an answer', () => {
  const answer = [
    'I work as a local-first assistant named Sage.',
    '',
    '```sage-diagram',
    '{"shape":"flow","title":"How Sage works","nodes":[',
    '{"label":"Understand request","icon":"eye"},',
    '{"label":"Choose capability","icon":"gear"}]}',
    '```',
    '',
    'A few examples follow.',
  ].join('\n');

  it('pulls the block out of a real answer', async () => {
    const { diagramSourceIn } = await import('./diagram-presenter');
    const source = diagramSourceIn(answer);
    expect(source).toBeTruthy();
    expect(parseDiagram(source!)?.title).toBe('How Sage works');
  });

  it('finds nothing in an ordinary answer, or a half-streamed block', async () => {
    const { diagramSourceIn } = await import('./diagram-presenter');
    expect(diagramSourceIn('Just prose, no diagram.')).toBeNull();
    expect(diagramSourceIn('```sage-diagram\n{"shape":"flow"')).toBeNull();
  });
});
