import { describe, expect, it, vi } from 'vitest';
import { landOnChat } from './landing';

function fakeHistory() {
  return { state: { idx: 3 }, replaceState: vi.fn() };
}

describe('landOnChat', () => {
  it('sends a reload on another page back to Chat', () => {
    const history = fakeHistory();
    expect(landOnChat({ pathname: '/dashboard', search: '', hash: '' }, history)).toBe(true);
    expect(history.replaceState).toHaveBeenCalledWith({ idx: 3 }, '', '/');
  });

  it('drops a query or hash too', () => {
    const history = fakeHistory();
    expect(landOnChat({ pathname: '/', search: '?tab=x', hash: '' }, history)).toBe(true);
    expect(history.replaceState).toHaveBeenCalledWith({ idx: 3 }, '', '/');
  });

  it('leaves a load already on Chat alone', () => {
    const history = fakeHistory();
    expect(landOnChat({ pathname: '/', search: '', hash: '' }, history)).toBe(false);
    expect(history.replaceState).not.toHaveBeenCalled();
  });
});
