import { describe, expect, it } from 'vitest';
import { interpretFluxMessage, turnSurvivesStatus } from './useFluxSpeech';

const turnInfo = (event: string, over: Record<string, unknown> = {}) =>
  JSON.stringify({
    type: 'TurnInfo',
    event,
    turn_index: 1,
    transcript: 'what is the capital of France',
    end_of_turn_confidence: '0.85',
    ...over,
  });

describe('Flux message interpretation', () => {
  describe('turn lifecycle', () => {
    it('treats a confirmed EndOfTurn as the send signal', () => {
      const action = interpretFluxMessage(turnInfo('EndOfTurn'), null);
      expect(action).toEqual({
        kind: 'endTurn',
        turnIndex: 1,
        transcript: 'what is the capital of France',
        words: [],
      });
    });

    it("carries the final's words and the server's voice check", () => {
      const action = interpretFluxMessage(
        turnInfo('EndOfTurn', {
          words: [{ word: 'hello', confidence: 0.9 }],
          speaker: { verdict: 'sage', user: 0.51, sage: 0.88, seconds: 2.1 },
        }),
        null,
      );
      expect(action.kind === 'endTurn' && action.words).toEqual([{ word: 'hello', confidence: 0.9 }]);
      expect(action.kind === 'endTurn' && action.speaker).toEqual({
        verdict: 'sage', user: 0.51, sage: 0.88, seconds: 2.1,
      });
    });

    it('ignores a voice check it cannot read', () => {
      const action = interpretFluxMessage(turnInfo('EndOfTurn', { speaker: { verdict: 'maybe' } }), null);
      expect(action.kind === 'endTurn' && 'speaker' in action).toBe(false);
    });

    it('treats EagerEndOfTurn as speculative only, never a send', () => {
      const action = interpretFluxMessage(turnInfo('EagerEndOfTurn'), null);
      expect(action.kind).toBe('speculate');
    });

    it('treats TurnResumed as a cancellation', () => {
      const action = interpretFluxMessage(turnInfo('TurnResumed'), null);
      expect(action).toEqual({ kind: 'cancelSpeculation', turnIndex: 1 });
    });

    it('surfaces Update as a partial transcript for barge-in only', () => {
      expect(interpretFluxMessage(turnInfo('Update'), null).kind).toBe('update');
    });

    it('carries word confidences on an Update, dropping malformed entries', () => {
      const action = interpretFluxMessage(
        turnInfo('Update', { words: [{ word: 'stop', confidence: 0.9 }, { word: 7 }, null] }),
        null,
      );
      expect(action).toEqual({
        kind: 'update',
        turnIndex: 1,
        transcript: expect.any(String),
        words: [{ word: 'stop', confidence: 0.9 }],
      });
    });

    it('reports StartOfTurn without any side effect', () => {
      expect(interpretFluxMessage(turnInfo('StartOfTurn'), null).kind).toBe(
        'turnStarted',
      );
    });

    it('is the only signal that speech has actually begun', () => {
      // Deepgram ends only turns it started, so a wake word that fires on
      // noise produces no events at all. The caller arms a timeout on
      // beginTurn and cancels it here; without a distinct StartOfTurn the
      // microphone would stay live indefinitely, and cancelling on EndOfTurn
      // instead would cut off any question longer than the timeout.
      const speechSignals = ['StartOfTurn', 'Update', 'EagerEndOfTurn', 'EndOfTurn']
        .map((event) => [event, interpretFluxMessage(turnInfo(event), null).kind]);

      expect(
        speechSignals.filter(([, kind]) => kind === 'turnStarted'),
      ).toEqual([['StartOfTurn', 'turnStarted']]);
    });
  });

  describe('duplicate and out-of-order events', () => {
    it('ignores a repeated EndOfTurn for a turn already sent', () => {
      const action = interpretFluxMessage(turnInfo('EndOfTurn'), 1);
      expect(action.kind).toBe('ignore');
    });

    it('ignores an EndOfTurn for an older turn arriving late', () => {
      const action = interpretFluxMessage(
        turnInfo('EndOfTurn', { turn_index: 0 }),
        3,
      );
      expect(action.kind).toBe('ignore');
    });

    it('still accepts the next genuine turn', () => {
      const action = interpretFluxMessage(
        turnInfo('EndOfTurn', { turn_index: 2 }),
        1,
      );
      expect(action.kind).toBe('endTurn');
    });

    it('accepts turn 0 of a fresh session after a reconnect', () => {
      // Deepgram numbers turns per connection. After three turns the guard
      // held 2; the reconnected session's first final arrived as turn 0 and
      // was swallowed as out-of-order -- and so were the next two. The user
      // saw the mic go dead after the third exchange and refreshed. A new
      // session must start the guard from nothing, which connect() now does;
      // this pins the contract that null means "accept the next final".
      const afterReconnect = null;
      const action = interpretFluxMessage(
        turnInfo('EndOfTurn', { turn_index: 0 }),
        afterReconnect,
      );
      expect(action.kind).toBe('endTurn');
    });

    it('does not suppress speculation for an already-final turn', () => {
      // Cancellation and speculation are not gated by the final-turn guard;
      // only sending is.
      expect(interpretFluxMessage(turnInfo('EagerEndOfTurn'), 1).kind).toBe(
        'speculate',
      );
      expect(interpretFluxMessage(turnInfo('TurnResumed'), 1).kind).toBe(
        'cancelSpeculation',
      );
    });
  });

  describe('availability and failure', () => {
    it('surfaces FluxUnavailable with its reason', () => {
      const action = interpretFluxMessage(
        JSON.stringify({ type: 'FluxUnavailable', reason: 'no key' }),
        null,
      );
      expect(action).toEqual({ kind: 'unavailable', reason: 'no key' });
    });

    it('treats a mid-session FluxError as unavailable so it can fall back', () => {
      const action = interpretFluxMessage(
        JSON.stringify({ type: 'FluxError', reason: 'socket died' }),
        null,
      );
      expect(action.kind).toBe('unavailable');
    });

    it('reports readiness', () => {
      expect(
        interpretFluxMessage(JSON.stringify({ type: 'FluxReady' }), null).kind,
      ).toBe('ready');
    });

    it('supplies a reason even when the server omits one', () => {
      const action = interpretFluxMessage(
        JSON.stringify({ type: 'FluxUnavailable' }),
        null,
      );
      expect(action.kind === 'unavailable' && action.reason).toBeTruthy();
    });
  });

  describe('malformed input', () => {
    it('ignores unparseable JSON rather than throwing', () => {
      expect(interpretFluxMessage('not json at all', null).kind).toBe('ignore');
    });

    it('ignores an unknown message type', () => {
      expect(
        interpretFluxMessage(JSON.stringify({ type: 'Connected' }), null).kind,
      ).toBe('ignore');
    });

    it('ignores an unknown turn event', () => {
      expect(interpretFluxMessage(turnInfo('SomethingNew'), null).kind).toBe(
        'ignore',
      );
    });

    it('defaults a missing transcript to empty rather than undefined', () => {
      const action = interpretFluxMessage(
        JSON.stringify({ type: 'TurnInfo', event: 'EndOfTurn', turn_index: 1 }),
        null,
      );
      expect(action.kind === 'endTurn' && action.transcript).toBe('');
    });

    it('ignores a JSON array', () => {
      expect(interpretFluxMessage('[1,2,3]', null).kind).toBe('ignore');
    });
  });
});

describe('speculative answer release', () => {
  it('carries a released answer on a confirmed final', () => {
    const action = interpretFluxMessage(
      turnInfo('EndOfTurn', { speculative_answer: 'Paris.' }),
      null,
    );
    expect(action.kind === 'endTurn' && action.speculativeAnswer).toBe('Paris.');
  });

  it('omits the field entirely when the server released nothing', () => {
    const action = interpretFluxMessage(turnInfo('EndOfTurn'), null);
    expect(action.kind === 'endTurn' && 'speculativeAnswer' in action).toBe(false);
  });

  it('ignores an empty released answer rather than posting a blank reply', () => {
    const action = interpretFluxMessage(
      turnInfo('EndOfTurn', { speculative_answer: '   ' }),
      null,
    );
    expect(action.kind === 'endTurn' && action.speculativeAnswer).toBeUndefined();
  });

  it('ignores a non-string released answer', () => {
    const action = interpretFluxMessage(
      turnInfo('EndOfTurn', { speculative_answer: { text: 'Paris.' } }),
      null,
    );
    expect(action.kind === 'endTurn' && action.speculativeAnswer).toBeUndefined();
  });

  it('never surfaces an answer on a speculative event', () => {
    // The server only ever attaches it to a final, but the client must not
    // depend on that to stay safe.
    const action = interpretFluxMessage(
      turnInfo('EagerEndOfTurn', { speculative_answer: 'Paris.' }),
      null,
    );
    expect(action.kind).toBe('speculate');
    expect('speculativeAnswer' in action).toBe(false);
  });

  it('never surfaces an answer on TurnResumed', () => {
    const action = interpretFluxMessage(
      turnInfo('TurnResumed', { speculative_answer: 'Paris.' }),
      null,
    );
    expect(action.kind).toBe('cancelSpeculation');
    expect('speculativeAnswer' in action).toBe(false);
  });

  it('drops a released answer on a duplicate final', () => {
    const action = interpretFluxMessage(
      turnInfo('EndOfTurn', { speculative_answer: 'Paris.' }),
      1,
    );
    expect(action.kind).toBe('ignore');
  });
});


// A turn used to outlive its socket. EndOfTurn and onUnavailable both clear
// it, but an intentional teardown — the effect re-running, Flux switched off,
// a reconnect — sets intentionalStopRef and never calls back, so a turn open
// at that moment stayed open forever. InputArea reports 'recording' while one
// is open and the wake word only fires from 'idle', so the wake word could
// never re-arm until the page was reloaded.
describe('turnSurvivesStatus', () => {
  it('keeps a turn while the socket is connected', () => {
    expect(turnSurvivesStatus('connected')).toBe(true);
  });

  it.each(['idle', 'connecting', 'unavailable', 'error'] as const)(
    'ends a turn on status %s, so the wake word can re-arm',
    (status) => {
      expect(turnSurvivesStatus(status)).toBe(false);
    },
  );

  it('ends a turn while reconnecting, not just on a hard failure', () => {
    // The reconnect path is the one that stranded it: no error is surfaced.
    expect(turnSurvivesStatus('connecting')).toBe(false);
  });
});

describe('the socket URL names the provider', () => {
  it('flux is the default and the only one that may speculate', async () => {
    const { buildFluxWsUrl } = await import('./useFluxSpeech');
    const g = globalThis as unknown as { window?: { location: { origin: string } } };
    const saved = g.window;
    g.window = { location: { origin: 'http://localhost:8000' } };
    try {
      expect(buildFluxWsUrl(true, 'm')).toBe('ws://localhost:8000/v1/speech/flux?eager=1&model=m');
      expect(buildFluxWsUrl(true, 'm', 'flux')).toContain('eager=1');
      const parakeet = buildFluxWsUrl(true, 'm', 'parakeet');
      expect(parakeet).toContain('provider=parakeet');
      expect(parakeet).not.toContain('eager');
    } finally {
      g.window = saved;
    }
  });
});
