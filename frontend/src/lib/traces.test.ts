import { describe, expect, it } from 'vitest';
import { failedTools, stepLabel, stepSeconds, traceSeconds, type Trace } from './traces';

// The real shape GET /v1/traces returns (9 October).
const trace: Trace = {
  id: 'd1c52128709a4f68',
  query: 'what time is it in tokyo?',
  model: 'gpt-6-luna',
  duration_ms: 3070,
  total_tokens: 22562,
  steps: [
    {
      step_type: 'generate',
      duration_seconds: 3.07,
      input: { model: 'gpt-6-luna' },
      output: { tokens: 22562, rounds: 2 },
      metadata: {},
    },
    {
      step_type: 'tool_call',
      duration_seconds: 0.02,
      input: { tool: 'world_time', arguments: '{"location": "Tokyo"}' },
      output: { success: true, result: 'Tokyo: 8:17 PM' },
    },
    {
      step_type: 'tool_call',
      duration_seconds: 3.0,
      input: { tool: 'youtube_play', arguments: '{}' },
      output: { success: false, result: 'Opera GX is not listening', error: 'Opera GX is not listening' },
    },
    { step_type: 'respond', duration_seconds: 3.07, output: { content: 'It’s 8:17 PM in Tokyo, Sir.' } },
  ],
};

describe('trace readings', () => {
  it('reads durations in seconds, never NaN', () => {
    expect(traceSeconds(trace)).toBeCloseTo(3.07);
    expect(stepSeconds(trace.steps![1])).toBeCloseTo(0.02);
    expect(stepSeconds({ step_type: 'route' })).toBe(0);
    expect(traceSeconds({ id: 'x' })).toBe(0);
  });

  it('counts failed tools', () => {
    expect(failedTools(trace)).toBe(1);
    expect(failedTools({ id: 'x' })).toBe(0);
  });

  it('labels each step', () => {
    expect(stepLabel(trace.steps![0])).toBe('gpt-6-luna · 2 rounds · 22,562 tokens');
    expect(stepLabel(trace.steps![1])).toBe('world_time');
    expect(stepLabel(trace.steps![3])).toBe('It’s 8:17 PM in Tokyo, Sir.');
    // A step with nothing in it must not throw (the old crash).
    expect(stepLabel({ step_type: 'generate' })).toBe('');
  });
});
