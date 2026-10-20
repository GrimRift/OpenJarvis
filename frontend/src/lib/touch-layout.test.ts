import { describe, expect, it } from 'vitest';
import { isTouchLayout, keyboardOpen, touchOrbSize, touchZoomFor } from './touch-layout';

const phone = { coarse: true, width: 393, height: 852, desktopShell: false };

describe('isTouchLayout', () => {
  it('a phone and an iPad get the touch layout', () => {
    expect(isTouchLayout(phone)).toBe(true);
    expect(isTouchLayout({ ...phone, width: 820, height: 1180 })).toBe(true); // iPad Air 11"
    expect(isTouchLayout({ ...phone, width: 1024, height: 1366 })).toBe(true); // iPad Air 13"
  });

  it('a mouse, the desktop app, or a big touch screen keep the desktop layout', () => {
    expect(isTouchLayout({ ...phone, coarse: false })).toBe(false);
    // A narrow desktop window is still the desktop.
    expect(isTouchLayout({ ...phone, coarse: false, width: 400 })).toBe(false);
    expect(isTouchLayout({ ...phone, desktopShell: true })).toBe(false);
    expect(isTouchLayout({ ...phone, width: 1920, height: 1080 })).toBe(false);
  });
});

describe('touchZoomFor', () => {
  it('leaves a phone at 1, either way up', () => {
    expect(touchZoomFor(393, 852)).toBe(1);
    expect(touchZoomFor(852, 393)).toBe(1);
  });

  it('scales an iPad up, never past 1.3', () => {
    expect(touchZoomFor(820, 1180)).toBe(1.3);
    expect(touchZoomFor(1180, 820)).toBe(1.3);
    expect(touchZoomFor(1024, 1366)).toBe(1.3);
    const mini = touchZoomFor(744, 1133);
    expect(mini).toBeGreaterThan(1);
    expect(mini).toBeLessThanOrEqual(1.3);
  });
});

describe('keyboardOpen', () => {
  it('only a big shrink is the keyboard (the URL bar hiding is not)', () => {
    expect(keyboardOpen(844, 844)).toBe(false);
    expect(keyboardOpen(844, 780)).toBe(false);
    expect(keyboardOpen(844, 500)).toBe(true);
  });
});

describe('touchOrbSize', () => {
  it('fills a phone across, keeps clear of the controls, and caps at 360', () => {
    expect(touchOrbSize(390, 844)).toBe(334);
    expect(touchOrbSize(631, 908)).toBe(360);
    // A short screen (landscape phone) still gets a usable orb.
    expect(touchOrbSize(844, 390)).toBe(180);
  });
});
