// Stub of '@hermes/plugin-sdk' for the screenshot harness. Not shipped; the real host provides this.
import { createElement as e, forwardRef } from 'react'

export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar-nav'
export const PALETTE_AREA = 'palette'

const cx = (...a) => a.filter(Boolean).join(' ')

const BTN = {
  base: {
    display: 'inline-flex', alignItems: 'center', gap: 6, borderRadius: 6, cursor: 'pointer',
    fontSize: 12.5, lineHeight: '16px', fontFamily: 'inherit', border: '1px solid transparent', whiteSpace: 'nowrap'
  },
  variant: {
    default: { background: 'var(--ui-accent)', color: 'var(--ui-base)', borderColor: 'var(--ui-accent)' },
    secondary: { background: 'var(--ui-control-active-background)', color: 'var(--ui-text-primary)' },
    outline: { background: 'transparent', color: 'var(--ui-text-primary)', borderColor: 'var(--ui-stroke-secondary)' },
    ghost: { background: 'transparent', color: 'var(--ui-text-secondary)' },
    chip: { background: 'var(--ui-control-hover-background)', color: 'var(--ui-text-primary)' }
  },
  size: {
    default: { padding: '6px 12px' }, sm: { padding: '4px 10px' }, xs: { padding: '2px 8px', fontSize: 11.5 },
    'icon-xs': { padding: 3 }
  }
}

export const Button = forwardRef(function Button({ variant = 'default', size = 'default', style, disabled, ...p }, ref) {
  return e('button', {
    ref, type: 'button', disabled, ...p,
    style: { ...BTN.base, ...BTN.variant[variant], ...BTN.size[size], opacity: disabled ? 0.45 : 1, cursor: disabled ? 'default' : 'pointer', ...style }
  })
})

const FIELD = {
  width: '100%', boxSizing: 'border-box', background: 'var(--ui-bg-tertiary)', color: 'var(--ui-text-primary)',
  border: '1px solid var(--ui-stroke-secondary)', borderRadius: 6, padding: '6px 9px', fontSize: 13, fontFamily: 'inherit'
}
export const Input = forwardRef(function Input({ style, ...p }, ref) {
  return e('input', { ref, ...p, style: { ...FIELD, ...style } })
})
export const Textarea = forwardRef(function Textarea({ style, ...p }, ref) {
  return e('textarea', { ref, ...p, style: { ...FIELD, ...style } })
})
export function Badge({ children, style }) {
  return e('span', { style: { padding: '1px 6px', borderRadius: 4, fontSize: 11, background: 'var(--ui-control-hover-background)', ...style } }, children)
}
export function EmptyState({ title, description }) {
  return e('div', { style: { textAlign: 'center', padding: 28, color: 'var(--ui-text-tertiary)' } },
    e('div', { style: { color: 'var(--ui-text-primary)', fontWeight: 600, marginBottom: 4 } }, title), e('div', null, description))
}
// Same glyph font the host uses (@vscode/codicons, loaded by index.html), so shots show real icons.
export function Codicon({ name, style, spinning }) {
  return e('span', { 'data-codicon': name, className: cx('codicon', `codicon-${name}`, spinning && 'codicon-modifier-spin'), style: { flexShrink: 0, ...style } })
}

const notes = []
export const host = {
  notify: n => { notes.push(n); window.__notes = notes },
  navigate: p => { window.__navigated = p },
  onEvent: () => () => {}
}

// The host's one copy path (lib/clipboard.ts). The harness records the text and tries the browser clipboard.
export async function writeClipboardText(text) {
  if (!text) return
  window.__copied = text
  try { await navigator.clipboard.writeText(text) } catch (_e) { /* the stub still recorded it */ }
}
