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

  it('keeps a reload on Voice on Voice', () => {
    const history = fakeHistory();
    expect(landOnChat({ pathname: '/voice', search: '', hash: '' }, history)).toBe(false);
    expect(history.replaceState).not.toHaveBeenCalled();
  });

  it('keeps Voice but drops its query or hash', () => {
    const history = fakeHistory();
    expect(landOnChat({ pathname: '/voice', search: '?x=1', hash: '' }, history)).toBe(true);
    expect(history.replaceState).toHaveBeenCalledWith({ idx: 3 }, '', '/voice');
  });

  it('leaves a load already on Chat alone', () => {
    const history = fakeHistory();
    expect(landOnChat({ pathname: '/', search: '', hash: '' }, history)).toBe(false);
    expect(history.replaceState).not.toHaveBeenCalled();
  });
});
