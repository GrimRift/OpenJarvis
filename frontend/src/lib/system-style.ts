/** Colours and breathing highlights shared by the system panel and tile (M41). */

export const ACCENT = '#22d3ee';
export const AMBER = '#f5a524';
export const MUTED = '#a1a1aa';
export const DIM = '#71717a';

export const colorFor = (high: boolean) => (high ? AMBER : ACCENT);

/** Breathing highlights; shared so the panel and tile animate alike. */
export const SYSTEM_STYLE = `
@keyframes sys-breathe-a { 0%,100% { box-shadow: 0 0 0 rgba(245,165,36,0) inset; } 50% { box-shadow: 0 0 26px rgba(245,165,36,0.22) inset; } }
@keyframes sys-breathe-c { 0%,100% { box-shadow: 0 0 0 rgba(34,211,238,0) inset; } 50% { box-shadow: 0 0 26px rgba(34,211,238,0.22) inset; } }
.sys-breathe-amber { animation: sys-breathe-a 2.6s ease-in-out infinite; }
.sys-breathe { animation: sys-breathe-c 2.6s ease-in-out infinite; }
@media (prefers-reduced-motion: reduce) { .sys-breathe-amber, .sys-breathe { animation: none; } }
`;
