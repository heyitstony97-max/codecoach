// CodeCoach: the desktop half. One page at /codecoach with six views:
// Library, Course map, Practice (with the live code box), Read (the materials, with read aloud), Timeline, Progress.
//
// Loaded uncompiled by Hermes Desktop: no JSX, only the three allowed imports,
// theme variables only (never a literal color), scoped timers via ctx.
import {
  host,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  PALETTE_AREA,
  Button,
  Input,
  Textarea,
  Badge,
  EmptyState,
  Codicon
} from '@hermes/plugin-sdk'
import { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

// ---------------------------------------------------------------------------
// Plumbing
// ---------------------------------------------------------------------------

let CTX = null
const pending = { next: false }
// The question the learner has open and unanswered in Practice, if any (Read puts it back ungraded).
const openQuestion = { id: null }
const listeners = new Set()
const bus = {
  on(fn) {
    listeners.add(fn)
    return () => listeners.delete(fn)
  },
  emit(type, payload) {
    listeners.forEach(fn => {
      try {
        fn(type, payload)
      } catch (_e) {
        /* a bad listener must not break the others */
      }
    })
  }
}

const api = (path, opts) => CTX.rest(path, opts)
// What the learner sees when a request fails. The desktop throws "<status>: <body>" for HTTP errors and a
// plain message for timeouts or a dead socket; none of that belongs on screen raw.
const BACKEND_DOWN = "CodeCoach's backend is not answering right now. Wait a moment and try again; your progress is saved."
const errText = e => {
  const raw = e && e.message ? String(e.message) : String(e || 'Something went wrong')
  const code = (e && e.statusCode) || Number((raw.match(/^(\d{3}): /) || [])[1]) || 0
  if (/timed out connecting/i.test(raw)) return 'CodeCoach took too long to answer. It may be busy with the model; wait a moment and try again.'
  if (code >= 500 || /failed to fetch|networkerror|fetch failed|econnrefused|econnreset|socket hang up|load failed/i.test(raw)) {
    const m = raw.match(/^\d{3}: (\{[\s\S]*\})$/)
    if (m && code >= 500 && code !== 502 && code !== 503 && code !== 504) {
      try {
        const d = JSON.parse(m[1]).detail
        if (typeof d === 'string' && d) return d
      } catch (_e) {
        /* fall through to the generic text */
      }
    }
    return BACKEND_DOWN
  }
  const body = raw.match(/^\d{3}: (\{[\s\S]*\})$/)
  if (body) {
    try {
      const d = JSON.parse(body[1]).detail
      if (typeof d === 'string' && d) return d
    } catch (_e) {
      /* keep the raw text */
    }
  }
  return raw
}

// h(type, props, ...children): jsx()/jsxs() without JSX syntax. `key` is lifted out of props.
function h(type, props, ...kids) {
  const { key, ...rest } = props || {}
  const flat = kids.length === 1 ? kids[0] : kids
  if (kids.length === 0) return jsx(type, rest, key)
  if (kids.length === 1) return jsx(type, { ...rest, children: flat }, key)
  return jsxs(type, { ...rest, children: flat }, key)
}

const C = {
  text: 'var(--ui-text-primary)',
  t2: 'var(--ui-text-secondary)',
  t3: 'var(--ui-text-tertiary)',
  t4: 'var(--ui-text-quaternary)',
  line: 'var(--ui-stroke-secondary)',
  accent: 'var(--ui-accent)',
  green: 'var(--ui-green)',
  yellow: 'var(--ui-yellow)',
  red: 'var(--ui-red)'
}
// The host's panel and popover fills. --ui-base is the theme's FOREGROUND seed (text colour), never a fill:
// painting with it made the Sources panel near-black on the light theme.
const SURFACE = 'var(--ui-surface-background, var(--ui-bg-editor, Canvas))'
const ELEVATED = 'var(--ui-bg-elevated, var(--ui-surface-background, Canvas))'
const tint = (c, pct) => `color-mix(in srgb, ${c} ${pct}%, transparent)`
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'

// One spacing scale (4/8/12/16/24/32) and one type scale (12/14/16/20/24).
const SP = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 }
const FS = { cap: 12, body: 14, lead: 16, title: 20, big: 24 }
const MAX_WIDE = 1100 // dashboards
const MAX_READ = '72ch' // reading text

const S = {
  page: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, color: C.text, fontSize: FS.body, lineHeight: '22px' },
  body: { flex: 1, minHeight: 0, overflowY: 'auto', padding: `${SP.xl}px ${SP.xl}px ${SP.xxl}px` },
  card: {
    border: `1px solid ${C.line}`,
    borderRadius: 10,
    padding: SP.lg,
    background: tint(C.text, 3)
  },
  row: { display: 'flex', alignItems: 'center', gap: SP.sm },
  h2: { fontSize: FS.lead, fontWeight: 600, margin: 0, lineHeight: '24px' },
  h3: { fontSize: FS.cap, fontWeight: 600, margin: 0, color: C.t2, letterSpacing: '0.04em', textTransform: 'uppercase' },
  muted: { color: C.t3, fontSize: FS.body, lineHeight: '20px' },
  cap: { color: C.t3, fontSize: FS.cap, lineHeight: '16px' },
  field: {
    width: '100%',
    boxSizing: 'border-box',
    background: tint(C.text, 4),
    color: C.text,
    border: `1px solid ${C.line}`,
    borderRadius: 6,
    padding: '8px 12px',
    fontSize: FS.body
  }
}
// A centred column. `max` is px or a CSS length such as MAX_READ.
const Wrap = ({ max = MAX_WIDE, gap = SP.lg, children, ...rest }) =>
  h('div', { style: { maxWidth: max, margin: '0 auto', display: 'flex', flexDirection: 'column', gap, minWidth: 0 }, ...rest }, children)

const NOT_COVERED = 'Not in your materials'

function Chip({ tone, children, title }) {
  const col = tone === 'good' ? C.green : tone === 'bad' ? C.red : tone === 'warn' ? C.yellow : tone === 'accent' ? C.accent : C.t2
  return h(
    'span',
    {
      title,
      style: {
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 8px',
        borderRadius: 999,
        fontSize: FS.cap,
        lineHeight: '16px',
        whiteSpace: 'nowrap',
        color: col,
        background: tint(col, 14),
        border: `1px solid ${tint(col, 30)}`
      }
    },
    children
  )
}

function Bar({ value, tone, height = 6, label }) {
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)))
  const col = tone === 'good' ? C.green : tone === 'warn' ? C.yellow : C.accent
  return h(
    'div',
    {
      role: 'progressbar',
      'aria-valuenow': pct,
      'aria-valuemin': 0,
      'aria-valuemax': 100,
      'aria-label': label,
      style: { height, borderRadius: height, background: tint(C.text, 10), overflow: 'hidden', width: '100%' }
    },
    h('div', { style: { width: `${pct}%`, height: '100%', background: col, borderRadius: height } })
  )
}

function Banner({ tone, children }) {
  const col = tone === 'bad' ? C.red : tone === 'warn' ? C.yellow : C.accent
  return h(
    'div',
    {
      role: tone === 'bad' ? 'alert' : 'status',
      style: {
        display: 'flex',
        gap: 8,
        alignItems: 'flex-start',
        padding: '8px 12px',
        borderRadius: 8,
        border: `1px solid ${tint(col, 40)}`,
        background: tint(col, 10),
        color: C.text,
        fontSize: FS.body,
        lineHeight: '20px'
      }
    },
    h(Codicon, { name: tone === 'bad' ? 'error' : tone === 'warn' ? 'warning' : 'info', style: { color: col, marginTop: 1 } }),
    h('div', { style: { minWidth: 0, flex: 1 } }, children)
  )
}

// Loading: calm grey bars instead of a blank flash. The text stays for screen readers and tests.
function Spinner({ text }) {
  const bar = (w, hgt = 14) => h('div', { className: 'ath-skel', style: { width: w, height: hgt, borderRadius: 6, background: tint(C.text, 8) } })
  return h(
    'div',
    { role: 'status', 'aria-live': 'polite', style: { display: 'flex', flexDirection: 'column', gap: SP.md, padding: SP.lg, maxWidth: 560, margin: '0 auto' } },
    h('div', { style: { ...S.row, color: C.t3 } }, h(Codicon, { name: 'loading', spinning: true }), text || 'Loading'),
    bar('70%', 20),
    bar('100%'),
    bar('88%'),
    bar('52%')
  )
}

// Shared page CSS: keyboard focus rings, skeleton pulse, hover. Theme variables only.
const PAGE_CSS = `
.ath button, .ath select, .ath input, .ath textarea { font-family: inherit; }
.ath button:focus-visible, .ath select:focus-visible, .ath input:focus-visible, .ath textarea:focus-visible, .ath summary:focus-visible, .ath [role=radio]:focus-visible, .ath [tabindex]:focus-visible { outline: 2px solid var(--ui-accent) !important; outline-offset: 2px; }
.ath svg g[role=button]:focus-visible { outline: none !important; }
.ath svg g[role=button]:focus-visible rect { stroke: var(--ui-accent); stroke-width: 3; }
@keyframes ath-pulse { 0%, 100% { opacity: 1 } 50% { opacity: 0.45 } }
.ath .ath-skel { animation: ath-pulse 1.4s ease-in-out infinite; }
@media (prefers-reduced-motion: reduce) { .ath .ath-skel { animation: none; } }
.ath .ath-row:hover { background: color-mix(in srgb, var(--ui-text-primary) 4%, transparent); }
.ath .ath-askchip:hover { background: color-mix(in srgb, var(--ui-accent) 14%, transparent) !important; color: var(--ui-text-primary) !important; border-color: color-mix(in srgb, var(--ui-accent) 40%, transparent) !important; }
.ath .ath-asklink:hover { text-decoration: underline; }
.ath .ath-askbox:focus-within { border-color: var(--ui-accent) !important; }
.ath .ath-askbox textarea:focus-visible { outline: none !important; }
`

function useAsync(loader, deps) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    setState(s => ({ ...s, loading: true, error: null }))
    Promise.resolve()
      .then(loader)
      .then(
        data => alive && setState({ loading: false, data, error: null }),
        e => alive && setState({ loading: false, data: null, error: errText(e) })
      )
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  return { ...state, reload: useCallback(() => setTick(t => t + 1), []) }
}

function Async({ st, children, what }) {
  if (st.loading && !st.data) return h(Spinner, { text: `Loading ${what || ''}` })
  if (st.error)
    return h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: 10 } },
      h(Banner, { tone: 'bad' }, `Could not load ${what || 'this'}: ${st.error}`),
      h('div', null, h(Button, { onClick: st.reload }, 'Try again'))
    )
  return children(st.data)
}

// ---------------------------------------------------------------------------
// Text helpers: find a quote inside a passage the way the server's gate does
// (case, curly quotes, dashes and whitespace are forgiven).
// ---------------------------------------------------------------------------

function normMap(text) {
  let out = ''
  const idx = []
  let lastSpace = true
  for (let i = 0; i < text.length; i++) {
    let c = text[i]
    if (c === '\u2018' || c === '\u2019') c = "'"
    else if (c === '\u201c' || c === '\u201d') c = '"'
    else if (c === '\u2013' || c === '\u2014' || c === '\u2212') c = '-'
    if (/\s/.test(c)) {
      if (lastSpace) continue
      c = ' '
      lastSpace = true
    } else {
      lastSpace = false
    }
    out += c.toLowerCase()
    idx.push(i)
  }
  return { out, idx }
}

function findSpan(text, quote) {
  if (!text || !quote) return null
  const direct = text.indexOf(quote)
  if (direct >= 0) return [direct, direct + quote.length]
  const t = normMap(text)
  const q = normMap(quote).out.trim()
  if (!q) return null
  const at = t.out.indexOf(q)
  if (at < 0) return null
  return [t.idx[at], t.idx[at + q.length - 1] + 1]
}

// `keys` are [start, end] offsets of the words that carry the answer (computed by the server inside the cited
// quote). Any that fall outside the quote span are dropped, so a word absent from the quote is never marked.
function Highlighted({ text, span, keys }) {
  if (!span) return h('span', null, text)
  const ks = (keys || []).filter(k => k && k[0] >= span[0] && k[1] <= span[1] && k[1] > k[0]).sort((a, b) => a[0] - b[0])
  const inner = []
  let at = span[0]
  ks.forEach(k => {
    if (k[0] < at) return
    if (k[0] > at) inner.push(text.slice(at, k[0]))
    inner.push(h('strong', { key: k[0], 'data-key-words': '1', style: { background: tint(C.accent, 34), color: C.text, borderRadius: 2, boxShadow: `0 0 0 1px ${tint(C.accent, 60)}` } }, text.slice(k[0], k[1])))
    at = k[1]
  })
  if (at < span[1]) inner.push(text.slice(at, span[1]))
  return h(
    'span',
    null,
    text.slice(0, span[0]),
    h(
      'mark',
      {
        'data-quote': '1',
        style: {
          background: tint(C.yellow, 30),
          color: C.text,
          borderRadius: 2,
          padding: '1px 0',
          boxShadow: `0 0 0 1px ${tint(C.yellow, 55)}`
        }
      },
      inner
    ),
    text.slice(span[1])
  )
}

function PassageBlock({ location, text, span, docName, label, cite, keys }) {
  return h(
    'div',
    { style: { borderLeft: `3px solid ${span ? C.yellow : C.line}`, paddingLeft: 12 } },
    h(
      'div',
      { style: { ...S.row, marginBottom: 4, flexWrap: 'wrap' } },
      cite && cite.doc_id ? h(CiteLink, { cite, location, style: { fontSize: 12, fontWeight: 600 } }) : h('span', { style: { fontSize: 12, fontWeight: 600, color: C.t2 } }, location),
      docName ? h('span', { style: S.muted }, docName) : null,
      label ? h(Chip, { tone: 'good' }, label) : null
    ),
    h('div', { style: { lineHeight: '21px', fontSize: 13.5 } }, h(Highlighted, { text, span, keys }))
  )
}

// A cited quote, highlighted inside its full passage, with where it lives.
function QuoteProof({ citation }) {
  const st = useAsync(() => api(`/passages/${encodeURIComponent(citation.passage_id)}`), [citation.passage_id])
  const loc = citation.location || (st.data && st.data.passage.location) || citation.passage_id
  if (st.data) {
    const p = st.data.passage
    const span = findSpan(p.text, citation.quote)
    return h(PassageBlock, { location: loc, text: p.text, span, keys: citation.key_spans, docName: st.data.doc_name, cite: { doc_id: citation.doc_id || p.doc_id, passage_id: citation.passage_id } })
  }
  if (st.error) {
    return h(PassageBlock, { location: loc, text: citation.quote, span: [0, citation.quote.length], cite: { doc_id: citation.doc_id, passage_id: citation.passage_id } })
  }
  return h(Spinner, { text: 'Finding the quote' })
}

const fmtDue = iso => {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
const pct = v => `${Math.round((v || 0) * 100)}%`
// "Next review" in plain words: in 10 minutes / later today / tomorrow / in 3 days.
const whenAgain = iso => {
  const d = new Date(iso)
  if (!iso || Number.isNaN(d.getTime())) return null
  const mins = Math.round((d.getTime() - Date.now()) / 60000)
  if (mins < 2) return 'in a minute'
  if (mins < 60) return `in ${mins} minutes`
  const day = t => new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime()
  const days = Math.round((day(d) - day(new Date())) / 86400000)
  if (days <= 0) return 'later today'
  if (days === 1) return 'tomorrow'
  return `in ${days} days`
}
// Partial credit only: "You got 2 of 3". Right-or-wrong formats get no score; others already say it in the feedback.
const partialText = (item, res) => {
  const sc = res.score
  if (res.correct !== false || !(sc > 0 && sc < 1) || /\d+\s*(of|\/)\s*\d+/.test(res.feedback || '')) return null
  const p = (item && item.payload) || {}
  const n = item.format === 'match' ? (p.pairs || []).length : item.format === 'order' ? Math.max(0, (p.events || []).length - 1) : item.format === 'short' ? (p.terms_expected || (p.required_terms || []).length) : 0
  return n ? `You got ${Math.round(sc * n)} of ${n}` : null
}

// ---------------------------------------------------------------------------
// Library
// ---------------------------------------------------------------------------

const FIXED_SUBJECTS = ['History', 'Programming', 'Math', 'Science', 'Language & Writing', 'Business', 'Other']
const UNCATEGORIZED = 'Uncategorized'
const NEW_SUBJECT = '__new__'
const MAX_SUBJECT = 40

// Alphabetical, "Uncategorized" last.
function groupBySubject(courses) {
  const by = new Map()
  courses.forEach(c => {
    const k = c.subject || UNCATEGORIZED
    if (!by.has(k)) by.set(k, [])
    by.get(k).push(c)
  })
  return [...by.entries()]
    .sort((a, b) => (a[0] === UNCATEGORIZED ? 1 : b[0] === UNCATEGORIZED ? -1 : a[0].localeCompare(b[0])))
    .map(([name, items]) => ({ name, items }))
}

// The fixed list plus any subject already in use, de-duplicated and sorted.
function subjectOptions(courses) {
  const set = new Set(FIXED_SUBJECTS)
  courses.forEach(c => c.subject && set.add(c.subject))
  return [...set].sort((a, b) => a.localeCompare(b))
}

// A select over the subjects; "New subject..." swaps it for a small text field.
function SubjectSelect({ value, options, noneLabel, onPick, disabled, label, compact }) {
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const known = value && !options.includes(value) ? [...options, value] : options
  const field = { ...S.field, width: 'auto', maxWidth: 190, padding: compact ? '2px 6px' : '6px 8px', fontSize: compact ? 12 : 13 }
  if (adding) {
    const save = () => {
      const v = draft.trim().slice(0, MAX_SUBJECT)
      setAdding(false)
      setDraft('')
      if (v) onPick(v)
    }
    return h(
      'span',
      { style: { ...S.row, gap: 4 } },
      h('input', {
        value: draft,
        autoFocus: true,
        maxLength: MAX_SUBJECT,
        placeholder: 'New subject',
        'aria-label': 'New subject name',
        style: { ...field, width: 130 },
        onChange: e => setDraft(e.target.value),
        onKeyDown: e => {
          if (e.key === 'Enter') save()
          if (e.key === 'Escape') setAdding(false)
        }
      }),
      h(Button, { size: 'sm', variant: 'outline', onClick: save }, 'Save'),
      h(Button, { size: 'sm', variant: 'ghost', onClick: () => setAdding(false) }, 'Cancel')
    )
  }
  return h(
    'select',
    {
      value: value || '',
      disabled,
      'aria-label': label,
      style: field,
      onChange: e => {
        const v = e.target.value
        if (v === NEW_SUBJECT) setAdding(true)
        else onPick(v)
      }
    },
    h('option', { value: '' }, noneLabel),
    known.map(o => h('option', { key: o, value: o }, o)),
    h('option', { value: NEW_SUBJECT }, 'New subject\u2026')
  )
}

function CourseCard({ c, selected, onOpen, onPractice, options, onSubject }) {
  const strict = c.mode === 'strict'
  const parts = [`${c.covered} ${c.covered === 1 ? 'topic' : 'topics'}`]
  if (c.not_covered) parts.push(`${c.not_covered} ${c.not_covered === 1 ? "isn't" : "aren't"} in your materials`)
  if (c.items_active === 0) parts.push('questions are still being prepared')
  else parts.push(c.due_reviews ? `${c.due_reviews} due for review` : 'nothing due')
  return h(
    'div',
    { 'data-testid': 'course-card', style: { ...S.card, borderColor: selected ? C.accent : C.line, display: 'flex', flexDirection: 'column', gap: SP.md, padding: SP.lg } },
    h(
      'div',
      { style: { ...S.row, flexWrap: 'wrap', gap: SP.sm } },
      h('span', { style: { fontWeight: 600, fontSize: FS.lead, lineHeight: '24px', overflowWrap: 'anywhere' } }, c.title),
      h('span', { title: c.mode_reason || (strict ? 'Answers come only from your materials' : 'Answers may use general knowledge'), role: 'img', 'aria-label': strict ? 'Answers come only from your materials' : 'Open mode', style: { display: 'inline-flex' } }, h(Codicon, { name: strict ? 'shield' : 'info', style: { color: strict ? C.green : C.yellow } }))
    ),
    h('div', { style: S.muted }, parts.join(' \u00b7 ')),
    h(
      'div',
      { style: { ...S.row, gap: SP.md } },
      h('div', { style: { flex: 1, maxWidth: 360 } }, h(Bar, { value: c.mastery, tone: c.mastery >= 0.7 ? 'good' : undefined, label: 'Course progress' })),
      h('span', { style: S.muted }, `${pct(c.mastery)} progress`)
    ),
    h(
      'div',
      { style: { ...S.row, justifyContent: 'space-between', flexWrap: 'wrap', gap: SP.md } },
      onSubject
        ? h(
            'div',
            { style: S.row },
            h('span', { style: S.muted }, 'Subject'),
            h(SubjectSelect, { value: c.subject || '', options, noneLabel: UNCATEGORIZED, label: `Subject for ${c.title}`, compact: true, onPick: v => onSubject(c, v) })
          )
        : h('span', null),
      h(
        'div',
        { style: S.row },
        h(Button, { size: 'sm', variant: 'ghost', onClick: onOpen }, 'See topics'),
        h(Button, { onClick: onPractice, 'data-testid': 'course-continue' }, h(Codicon, { name: 'play' }), 'Continue')
      )
    )
  )
}

function ImportPanel({ onImported, subjects }) {
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [path, setPath] = useState('')
  const [syllabus, setSyllabus] = useState('')
  const [syllabusPath, setSyllabusPath] = useState('')
  const [mode, setMode] = useState('strict')
  const [job, setJob] = useState(null)
  const [err, setErr] = useState(null)
  const pollRef = useRef(null)
  const jobIdRef = useRef(null)

  const stopPoll = () => {
    if (pollRef.current) {
      pollRef.current()
      pollRef.current = null
    }
  }

  const apply = useCallback(
    j => {
      setJob(prev => ({ ...(prev || {}), ...j }))
      if (j.status === 'done') {
        stopPoll()
        onImported(j.course_id)
      } else if (j.status === 'error') {
        stopPoll()
        setErr(j.error || 'The import failed.')
      }
    },
    [onImported]
  )

  useEffect(() => {
    const off = bus.on((type, p) => {
      if (type === 'job' && p && p.job_id === jobIdRef.current) {
        apply({ pct: p.pct, stage: p.stage, status: p.pct >= 100 ? 'done' : 'running' })
        if (p.pct >= 100) poll()
      }
    })
    return () => {
      off()
      stopPoll()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const poll = () => {
    const id = jobIdRef.current
    if (!id) return
    api(`/jobs/${encodeURIComponent(id)}`).then(
      j => apply({ ...j }),
      e => setErr(errText(e))
    )
  }

  const start = async () => {
    setErr(null)
    const hasText = text.trim().length > 0
    const hasPath = path.trim().length > 0
    if (!title.trim()) return setErr('Give your notes a title.')
    if (hasText === hasPath) return setErr('Add your programming notes: paste text in the big box, or give a file path (not both).')
    const hasSyl = syllabus.trim().length > 0
    const hasSylPath = syllabusPath.trim().length > 0
    if (hasSyl && hasSylPath) return setErr('Paste the syllabus OR give its file path, not both.')
    try {
      const body = { title: title.trim(), mode }
      body.subject = 'Programming'
      if (hasText) body.text = text
      else body.path = path.trim()
      if (hasSyl) body.syllabus_text = syllabus
      else if (hasSylPath) body.syllabus_path = syllabusPath.trim()
      const r = await api('/courses/import', { method: 'POST', body })
      jobIdRef.current = r.job_id
      setJob({ id: r.job_id, status: 'queued', pct: 0, stage: 'queued' })
      stopPoll()
      // Fallback for when the push event never arrives (sockets can drop).
      pollRef.current = CTX.setInterval(poll, 1500)
    } catch (e) {
      setErr(errText(e))
    }
  }

  const busy = job && (job.status === 'queued' || job.status === 'running')

  // A slow local model can sit on one step for minutes. Say how long, so the bar never looks frozen.
  const [now, setNow] = useState(Date.now())
  const stageRef = useRef({ key: '', at: Date.now() })
  const stageKey = job ? `${job.stage}|${job.pct}` : ''
  if (stageRef.current.key !== stageKey) stageRef.current = { key: stageKey, at: Date.now() }
  useEffect(() => {
    if (!busy) return undefined
    const t = CTX.setInterval(() => setNow(Date.now()), 1000)
    return t
  }, [busy])
  const stuckFor = busy ? Math.floor((now - stageRef.current.at) / 1000) : 0

  return h(
    'div',
    { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 10 } },
    h('h3', { style: S.h3 }, 'Add your programming notes'),
    h('div', { style: S.muted }, 'Programming notes only: lecture notes, a book chapter or docs. Paste text, or give the path to a PDF, text or Markdown file. CodeCoach builds topics and coding practice from it, and every question quotes your notes.'),
    h(Input, {
      value: title,
      placeholder: 'Title, e.g. "CS50 week 1: C basics"',
      onChange: e => setTitle(e.target.value),
      disabled: busy,
      'aria-label': 'Course title'
    }),
    h(Textarea, {
      value: text,
      placeholder: 'Paste your lecture notes, a book chapter or docs here...',
      rows: 5,
      onChange: e => setText(e.target.value),
      disabled: busy,
      'aria-label': 'Pasted text',
      style: { resize: 'vertical' }
    }),
    h(Input, {
      value: path,
      placeholder: 'or the path to a PDF, text or markdown file: /home/you/notes/pointers.pdf',
      onChange: e => setPath(e.target.value),
      disabled: busy,
      'aria-label': 'File path'
    }),
    h(Textarea, {
      value: syllabus,
      placeholder: 'Syllabus (optional): one programming topic per line, numbered or bulleted',
      rows: 3,
      onChange: e => setSyllabus(e.target.value),
      disabled: busy,
      'aria-label': 'Syllabus (optional)',
      style: { resize: 'vertical' }
    }),
    h(Input, {
      value: syllabusPath,
      placeholder: 'or a syllabus file path: /home/you/notes/syllabus.md',
      onChange: e => setSyllabusPath(e.target.value),
      disabled: busy,
      'aria-label': 'Syllabus file path'
    }),
    h('div', { style: S.muted }, "Add a syllabus and CodeCoach shows which of its topics your notes don't cover."),
    h(
      'div',
      { style: { ...S.row, flexWrap: 'wrap' } },
      h('span', { style: { ...S.muted, marginRight: 4 } }, 'Answers come from:'),
      h(
        Button,
        { size: 'sm', variant: mode === 'strict' ? 'default' : 'outline', onClick: () => setMode('strict'), 'aria-pressed': mode === 'strict' },
        'Only my materials'
      ),
      h(
        Button,
        { size: 'sm', variant: mode === 'open' ? 'default' : 'outline', onClick: () => setMode('open'), 'aria-pressed': mode === 'open' },
        'Open'
      )
    ),
    mode === 'open'
      ? h(
          Banner,
          { tone: 'warn' },
          'Open mode lets the model add facts that are not in your materials. Those questions are labeled "Not from your materials" and nothing checks them against a source. Strict is the safe default.'
        )
      : h('div', { style: S.muted }, 'Recommended. Every question quotes your materials word for word, and questions that cannot be quoted are thrown out.'),
    err ? h(Banner, { tone: 'bad' }, err) : null,
    job
      ? h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
          h(Bar, { value: (job.pct || 0) / 100, label: 'Import progress', height: 8 }),
          h(
            'div',
            { style: S.muted },
            job.status === 'done' ? 'Done.' : `${Math.round(job.pct || 0)}% - ${job.stage || job.status}${stuckFor >= 10 ? ` (still working on this step, ${stuckFor} s; this can take a few minutes)` : ''}`
          )
        )
      : null,
    h(
      'div',
      { style: S.row },
      h(Button, { onClick: start, disabled: busy }, busy ? 'Importing...' : 'Import')
    )
  )
}

function LibraryView({ courses, courseId, setCourseId, setView, reload }) {
  // Subject changes show at once; the server row wins again once the list reloads.
  const [local, setLocal] = useState({})
  const [err, setErr] = useState(null)
  useEffect(() => setLocal({}), [courses])
  const shown = courses.map(c => (c.id in local ? { ...c, subject: local[c.id] } : c))
  const options = subjectOptions(shown)
  const groups = groupBySubject(shown)

  const changeSubject = async (c, value) => {
    setErr(null)
    const next = value && value.trim() ? value.trim().slice(0, MAX_SUBJECT) : null
    try {
      const r = await api(`/courses/${encodeURIComponent(c.id)}`, { method: 'PATCH', body: { subject: next } })
      setLocal(prev => ({ ...prev, [c.id]: r && 'subject' in r ? r.subject : next }))
    } catch (e) {
      setErr(`Could not change the subject: ${errText(e)}`)
    }
  }

  const card = c =>
    h(CourseCard, {
      key: c.id,
      c,
      selected: c.id === courseId,
      options,
      onSubject: changeSubject,
      onOpen: () => {
        setCourseId(c.id)
        setView('map')
      },
      onPractice: () => {
        setCourseId(c.id)
        setView('practice')
      }
    })

  return h(
    'div',
    { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(380px,1fr))', gap: SP.xl, alignItems: 'start', maxWidth: MAX_WIDE, margin: '0 auto' } },
    h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: 10 } },
      h('h3', { style: S.h3 }, 'Your courses'),
      err ? h(Banner, { tone: 'bad' }, err) : null,
      courses.length === 0
        ? h(EmptyState, { title: 'No programming notes yet', description: 'To start, paste your lecture notes, a book chapter or docs into the Import box (or give the path to a PDF), then press Import. Or build a program from scratch in Projects.' })
        : groups.length === 1
          ? groups[0].items.map(card)
          : groups.map(g =>
              h(
                'section',
                { key: g.name, 'aria-label': g.name, style: { display: 'flex', flexDirection: 'column', gap: 10 } },
                h(
                  'div',
                  { style: { ...S.row, marginTop: 6, paddingBottom: 4, borderBottom: `1px solid ${C.line}` } },
                  h('span', { style: { fontWeight: 600, fontSize: 13 } }, g.name),
                  h('span', { style: S.muted }, `${g.items.length} ${g.items.length === 1 ? 'course' : 'courses'}`)
                ),
                g.items.map(card)
              )
            )
    ),
    h(ImportPanel, {
      subjects: options,
      onImported: id => {
        reload()
        if (id) setCourseId(id)
      }
    })
  )
}

// ---------------------------------------------------------------------------
// Course map: layered DAG layout in JS, drawn as SVG
// ---------------------------------------------------------------------------

// Longest-path layering, then barycenter ordering (down/up sweeps).
function layoutGraph(objs, edges, dims) {
  const ids = objs.map(o => o.id)
  const known = new Set(ids)
  const preds = new Map(ids.map(i => [i, []]))
  const succs = new Map(ids.map(i => [i, []]))
  edges.forEach(e => {
    if (known.has(e.prereq) && known.has(e.objective)) {
      preds.get(e.objective).push(e.prereq)
      succs.get(e.prereq).push(e.objective)
    }
  })
  const layerOf = new Map()
  const visiting = new Set()
  const depth = id => {
    if (layerOf.has(id)) return layerOf.get(id)
    if (visiting.has(id)) return 0 // cycle guard; the server rejects cycles
    visiting.add(id)
    const d = preds.get(id).reduce((m, p) => Math.max(m, depth(p) + 1), 0)
    visiting.delete(id)
    layerOf.set(id, d)
    return d
  }
  ids.forEach(depth)
  const nLayers = ids.length ? Math.max(...ids.map(i => layerOf.get(i))) + 1 : 0
  const layers = Array.from({ length: nLayers }, () => [])
  ids.forEach(i => layers[layerOf.get(i)].push(i))

  const posIn = new Map()
  const reindex = () =>
    layers.forEach(l => {
      l.forEach((id, k) => posIn.set(id, k - (l.length - 1) / 2))
    })
  reindex()
  const sweep = (order, neighbours) => {
    order.forEach(li => {
      const l = layers[li]
      const bary = new Map()
      l.forEach((id, k) => {
        const ns = neighbours.get(id)
        bary.set(id, ns.length ? ns.reduce((s, n) => s + posIn.get(n), 0) / ns.length : posIn.get(id))
      })
      l.sort((a, b) => bary.get(a) - bary.get(b) || ids.indexOf(a) - ids.indexOf(b))
      l.forEach((id, k) => posIn.set(id, k - (l.length - 1) / 2))
    })
  }
  const down = layers.map((_, i) => i).slice(1)
  const up = layers.map((_, i) => i).slice(0, -1).reverse()
  for (let it = 0; it < 4; it++) {
    sweep(down, preds)
    sweep(up, succs)
  }
  const maxRows = layers.reduce((m, l) => Math.max(m, l.length), 1)
  const { NW, NH, GX, GY, PAD } = dims
  const pos = new Map()
  layers.forEach((l, li) => {
    const colH = l.length * NH + (l.length - 1) * GY
    const full = maxRows * NH + (maxRows - 1) * GY
    l.forEach((id, k) => {
      pos.set(id, { x: PAD + li * (NW + GX), y: PAD + (full - colH) / 2 + k * (NH + GY), layer: li })
    })
  })
  return {
    pos,
    width: PAD * 2 + nLayers * NW + Math.max(0, nLayers - 1) * GX,
    height: PAD * 2 + maxRows * NH + (maxRows - 1) * GY
  }
}

const masteryClass = o => (o.coverage === 'not_covered' ? 'gap' : o.mastery >= 0.7 ? 'known' : o.mastery > 0 ? 'shaky' : 'new')
const CLASS_STYLE = {
  known: { col: C.green, label: 'Known' },
  shaky: { col: C.yellow, label: 'Shaky' },
  new: { col: C.t3, label: 'New' },
  gap: { col: C.t4, label: NOT_COVERED }
}

function wrapLabel(s, width, lines) {
  const words = s.split(/\s+/)
  const out = []
  let cur = ''
  for (const w of words) {
    if ((cur + ' ' + w).trim().length > width) {
      if (cur) out.push(cur)
      cur = w
    } else cur = (cur + ' ' + w).trim()
    if (out.length === lines) break
  }
  if (cur && out.length < lines) out.push(cur)
  const used = out.join(' ').length
  if (used < s.length) {
    const last = out[out.length - 1] || ''
    out[out.length - 1] = (last.length > width - 1 ? last.slice(0, width - 1) : last).replace(/[\s,.;:]+$/, '') + '\u2026'
  }
  return out
}

const FORMAT_LABEL = {
  mc: 'Multiple choice', cloze: 'Fill in the blank', order: 'Put in order', match: 'Match',
  short: 'Short answer', explain: 'Explain', code: 'Code', math: 'Math', numeric: 'Numeric'
}
const formatLabel = f => FORMAT_LABEL[f] || (f ? f.charAt(0).toUpperCase() + f.slice(1) : '')

// How an item is checked, derived from its format and origin (no contract field needed).
const CHECKED_BY = {
  code: { icon: 'shield', text: 'Checked by running tests in a sandbox' },
  math: { icon: 'verified', text: 'Checked by SymPy' },
  numeric: { icon: 'verified', text: 'Checked with unit math' }
}
const checkedBy = item => CHECKED_BY[item.format] || (item.origin === 'materials' ? { icon: 'verified', text: 'Checked against your source' } : null)

// The reply text sometimes opens with the verdict word the box already shows.
const trimVerdict = text => {
  if (typeof text !== 'string') return text
  let t = text
  for (let i = 0; i < 3; i++) {
    const n = t.replace(/^\s*(correct|not quite|not correct|incorrect|right|wrong)\s*(?:[.!:\u2014-]+\s*|$)/i, '')
    if (n === t) break
    t = n
  }
  return t
}

const clipText = (t, n) => (t.length > n ? t.slice(0, n - 1).replace(/[\s,.;:]+$/, '') + '\u2026' : t)
// A topic as a heading, not an exam prompt: "Describe the four special spikes ... ." reads "The four special
// spikes ...". The course map stores goals as instructions; shown that way every screen felt like a test (Anthony).
// Only the visible heading changes; aria labels and data attributes keep the full statement.
const TASK_VERB = /^(describe|explain|summari[sz]e|trace|identify|list|compare|contrast|analy[sz]e|discuss|outline|define|state|name|recall|recognize|recognise|apply|use|understand|evaluate|interpret|classify|distinguish|give|demonstrate|examine|explore|determine|calculate|compute|find|solve)\s+/i
const topicTitle = s => {
  let t = String(s || '').replace(/\s+/g, ' ').trim()
  const cut = t.replace(TASK_VERB, '')
  if (cut !== t && cut.split(' ').length >= 2) t = cut
  t = t.replace(/[.\s]+$/, '')
  // gcc, npm, `main`, x2: names and code keep their case; only an ordinary first word is capitalised.
  const first = t.split(' ')[0] || ''
  if (!t || !/[aeiouy]/i.test(first) || /[`\d_.()\/-]/.test(first)) return t
  return t[0].toUpperCase() + t.slice(1)
}
const stripVerb = (statement, verb) => {
  const re = new RegExp(`^${verb}\\s+`, 'i')
  return statement.replace(re, '')
}

const DIMS = { NW: 208, NH: 92, GX: 36, GY: 12, PAD: 12 }

function MapGraph({ objs, edges, selected, onSelect }) {
  const g = useMemo(() => layoutGraph(objs, edges, DIMS), [objs, edges])
  const { NW, NH } = DIMS
  const byId = useMemo(() => new Map(objs.map(o => [o.id, o])), [objs])
  const related = useMemo(() => {
    const s = new Set()
    if (selected) edges.forEach(e => (e.prereq === selected || e.objective === selected) && s.add(`${e.prereq}>${e.objective}`))
    return s
  }, [selected, edges])
  const wrapRef = useRef(null)
  const [w, setW] = useState(1200)
  useEffect(() => {
    const el = wrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(() => setW(el.clientWidth))
    ro.observe(el)
    setW(el.clientWidth)
    return () => ro.disconnect()
  }, [])
  const scale = Math.max(0.8, Math.min(1.5, (w - 2) / g.width))
  return h(
    'div',
    {
      ref: wrapRef,
      style: { overflow: 'auto', border: `1px solid ${C.line}`, borderRadius: 6, background: tint(C.text, 2) }
    },
    h(
      'svg',
      {
        width: g.width * scale,
        height: g.height * scale,
        viewBox: `0 0 ${g.width} ${g.height}`,
        role: 'img',
        'aria-label': 'Prerequisite graph of the course objectives, arrows point from prerequisite to objective',
        style: { display: 'block' }
      },
      h(
        'defs',
        null,
        h(
          'marker',
          { id: 'ath-arrow', viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
          h('path', { d: 'M0,0 L8,4 L0,8 z', fill: 'currentColor' })
        )
      ),
      edges.map(e => {
        const a = g.pos.get(e.prereq)
        const b = g.pos.get(e.objective)
        if (!a || !b) return null
        const hot = related.has(`${e.prereq}>${e.objective}`)
        const x1 = a.x + NW
        const y1 = a.y + NH / 2
        const x2 = b.x
        const y2 = b.y + NH / 2
        const mx = (x1 + x2) / 2
        return h('path', {
          key: `${e.prereq}>${e.objective}`,
          d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2 - 1},${y2}`,
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: hot ? 2 : 1,
          markerEnd: 'url(#ath-arrow)',
          style: { color: hot ? C.accent : tint(C.text, 28), opacity: !selected || hot ? 1 : 0.55 }
        })
      }),
      objs.map(o => {
        const p = g.pos.get(o.id)
        const cls = masteryClass(o)
        const st = CLASS_STYLE[cls]
        const isSel = selected === o.id
        const lines = wrapLabel(o.statement, 28, 3)
        return h(
          'g',
          {
            key: o.id,
            transform: `translate(${p.x},${p.y})`,
            onClick: () => onSelect(o.id),
            onKeyDown: ev => (ev.key === 'Enter' || ev.key === ' ') && (ev.preventDefault(), onSelect(o.id)),
            tabIndex: 0,
            role: 'button',
            'aria-label': `${o.statement} - ${cls === 'gap' ? NOT_COVERED : st.label}`,
            style: { cursor: 'pointer', outline: 'none' }
          },
          h('title', null, `${o.statement}\n${cls === 'gap' ? NOT_COVERED : `${st.label}, mastery ${pct(o.mastery)}`}`),
          h('rect', {
            width: NW,
            height: NH,
            rx: 5,
            fill: cls === 'gap' ? tint(C.text, 4) : tint(st.col, cls === 'new' ? 8 : 20),
            stroke: isSel ? C.accent : cls === 'gap' ? C.t4 : st.col,
            strokeWidth: isSel ? 2.5 : 1.2,
            strokeDasharray: cls === 'gap' ? '4 3' : undefined,
            opacity: cls === 'gap' ? 0.8 : 1
          }),
          lines.map((ln, i) => h('text', { key: i, x: 12, y: 22 + i * 16, fontSize: 13, fill: C.text }, ln)),
          cls === 'gap'
            ? h('text', { x: 12, y: NH - 10, fontSize: 12, fontStyle: 'italic', fill: C.t3 }, NOT_COVERED)
            : h('text', { x: NW - 12, y: NH - 10, fontSize: 12, fontWeight: 600, textAnchor: 'end', fill: st.col }, `${st.label} ${pct(o.mastery)}`)
        )
      })
    )
  )
}

function Legend() {
  return h(
    'div',
    { style: { ...S.row, gap: 14, flexWrap: 'wrap' } },
    ['new', 'shaky', 'known', 'gap'].map(k =>
      h(
        'span',
        { key: k, style: { ...S.row, gap: 6, ...S.muted } },
        h('span', {
          style: {
            width: 14,
            height: 10,
            borderRadius: 2,
            border: `1.5px ${k === 'gap' ? 'dashed' : 'solid'} ${k === 'gap' ? C.t4 : CLASS_STYLE[k].col}`,
            background: k === 'gap' ? tint(C.text, 4) : tint(CLASS_STYLE[k].col, k === 'new' ? 8 : 25)
          }
        }),
        k === 'gap' ? NOT_COVERED : CLASS_STYLE[k].label
      )
    ),
    h('span', { style: S.muted }, 'Arrows point from a prerequisite to what depends on it.')
  )
}

function ObjectivePanel({ obj, map, courseId, onSelect, onPractice, inList }) {
  const objs = map.units.flatMap(u => u.lessons.flatMap(l => l.objectives))
  const unit = map.units.find(u => u.id === obj.unit)
  const lesson = unit && unit.lessons.find(l => l.id === obj.lesson)
  const cls = masteryClass(obj)
  const passages = useAsync(
    () => (obj.coverage === 'covered' ? Promise.all([...new Set(obj.passage_ids)].slice(0, 3).map(id => api(`/passages/${encodeURIComponent(id)}`))) : Promise.resolve([])),
    [obj.id]
  )
  const prereqs = obj.prereqs.map(id => objs.find(o => o.id === id)).filter(Boolean)
  return h(
    'div',
    { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 10, ...(inList ? { border: 'none', background: 'transparent', padding: 0 } : {}) } },
    inList ? null : h(
      'div',
      { style: { ...S.row, justifyContent: 'space-between', alignItems: 'flex-start' } },
      h(
        'div',
        null,
        h('div', { style: S.muted }, `${unit ? unit.title : ''}${lesson ? ' / ' + lesson.title : ''}`),
        h('div', { style: { ...S.h2, marginTop: 2 } }, topicTitle(obj.statement))
      ),
      h(
        'div',
        { style: { ...S.row, flexShrink: 0 } },
        cls === 'gap'
          ? h(Chip, null, obj.status_label || NOT_COVERED)
          : h(Chip, { tone: cls === 'known' ? 'good' : cls === 'shaky' ? 'warn' : undefined }, `${CLASS_STYLE[cls].label}, ${pct(obj.mastery)}`),
        cls !== 'gap' ? h(Button, { size: 'sm', onClick: onPractice }, 'Practice this') : null
      )
    ),
    cls === 'gap'
      ? h(
          Banner,
          null,
          'Your materials do not cover this objective, so CodeCoach will not teach or quiz it. Import more material if you want it covered.'
        )
      : h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: 10 } },
          h(
            'div',
            { style: { ...S.muted, display: 'flex', gap: 14, flexWrap: 'wrap' } },
            h('span', null, `${obj.items_active} ${obj.items_active === 1 ? "question" : "questions"}`),
            h('span', null, `${obj.due_reviews} due for review`),
            prereqs.length
              ? h(
                  'span',
                  { style: S.row },
                  'Needs:',
                  prereqs.map(p => h(Button, { key: p.id, size: 'xs', variant: 'chip', onClick: () => onSelect(p.id) }, `${p.verb} ${clipText(stripVerb(p.statement, p.verb), 36)}`))
                )
              : h('span', null, 'No prerequisites')
          ),
          h('h3', { style: S.h3 }, 'Lesson: read this first'),
          h(ListenBar, { key: obj.id, courseId, passages: [...new Set(obj.passage_ids)].map(id => ({ id, doc_id: id.split(':')[0] })), what: 'this topic' }),
          h(Async, { st: passages, what: 'the lesson' }, list =>
            h(
              'div',
              { style: { display: 'flex', flexDirection: 'column', gap: 10 } },
              list.map((r, i) => h(PassageBlock, { key: `${i}-${r.passage.id}`, location: r.passage.location, text: r.passage.text, docName: r.doc_name, label: 'From your materials', cite: { doc_id: r.passage.doc_id, passage_id: r.passage.id } }))
            )
          )
        )
  )
}

// Study guide export: the server writes <data dir>/exports/<course>-study-guide.md and returns the same
// Markdown, which the browser then saves as a download.
function ExportGuide({ courseId }) {
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const [err, setErr] = useState(null)
  const go = async () => {
    setBusy(true)
    setErr(null)
    try {
      const r = await api(`/courses/${encodeURIComponent(courseId)}/export`, { method: 'POST' })
      setDone(r)
      try {
        const url = URL.createObjectURL(new Blob([r.markdown], { type: 'text/markdown;charset=utf-8' }))
        const a = document.createElement('a')
        a.href = url
        a.download = r.filename
        a.setAttribute('data-testid', 'export-download')
        document.body.appendChild(a)
        a.click()
        a.remove()
        setTimeout(() => URL.revokeObjectURL(url), 10000)
      } catch (_e) {
        /* The file is already saved on disk; the download is a convenience. */
      }
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(false)
    }
  }
  return h(
    'div',
    { style: { ...S.row, flexWrap: 'wrap' } },
    h(Button, { size: 'sm', variant: 'outline', onClick: go, disabled: busy, 'data-testid': 'export-guide' }, h(Codicon, { name: 'export' }), busy ? 'Exporting' : 'Export study guide'),
    done ? h('span', { style: S.muted, 'data-testid': 'export-done', 'data-export-file': done.filename, 'data-export-path': done.path || '' }, done.path ? `Saved to ${done.path}` : `Downloaded ${done.filename}`) : null,
    err ? h(Banner, { tone: 'bad' }, err) : null
  )
}

// A one-line plain summary of where the student stands in this course.
function courseSummary(objs) {
  const covered = objs.filter(o => o.coverage === 'covered')
  const known = covered.filter(o => masteryClass(o) === 'known').length
  const gaps = objs.length - covered.length
  const n = covered.length
  const part1 = `${known} of ${n} ${n === 1 ? 'topic' : 'topics'} known`
  const part2 = gaps ? ` \u00b7 ${gaps} ${gaps === 1 ? "topic isn't" : "topics aren't"} in your materials` : ''
  return { known, covered: n, gaps, text: part1 + part2 }
}

// learn: this topic's row of the Learn overview (null when the backend has none). A topic taught in Learn but not yet
// reviewed shows "Learned"/"In progress"; only a topic Review has proven (Known) keeps that stronger label.
function TopicRow({ o, open, onToggle, onPractice, onLearn, learn, children }) {
  const cls = masteryClass(o)
  const st = CLASS_STYLE[cls]
  const ls = learn && cls !== 'gap' ? learn.status : null
  const learnedNew = ls === 'learned' && cls !== 'known'
  const label = learnedNew ? 'Learned' : ls === 'in_progress' && cls !== 'known' ? 'In progress' : st.label
  const tone = learnedNew ? 'good' : cls === 'known' ? 'good' : cls === 'shaky' ? 'warn' : undefined
  return h(
    'div',
    { 'data-testid': 'topic-row', 'data-topic-id': o.id, 'data-topic-state': cls, 'data-learn-status': ls || undefined, style: { borderBottom: `1px solid ${C.line}` } },
    h(
      'div',
      { className: 'ath-row', style: { display: 'flex', alignItems: 'center', gap: SP.lg, padding: `${SP.md}px ${SP.lg}px`, flexWrap: 'wrap' } },
      h(
        'button',
        { type: 'button', onClick: onToggle, 'aria-expanded': open, style: { flex: '1 1 260px', minWidth: 0, textAlign: 'left', background: 'transparent', border: 'none', color: C.text, font: 'inherit', fontSize: FS.lead, lineHeight: '24px', padding: 0, cursor: 'pointer', overflowWrap: 'anywhere' } },
        topicTitle(o.statement)
      ),
      h('div', { style: { width: 150, flex: 'none', display: 'flex' } }, h(Chip, { tone }, label)),
      h('div', { style: { width: 120, flex: 'none' } }, cls === 'gap' ? null : h(Bar, { value: o.mastery || 0, tone: cls === 'known' ? 'good' : cls === 'shaky' ? 'warn' : undefined, height: 6, label: `${o.statement}: ${pct(o.mastery)}` })),
      h(
        'div',
        { style: { width: 220, flex: 'none', display: 'flex', justifyContent: 'flex-end', gap: SP.xs } },
        cls === 'gap' ? null : [
          learn ? h(Button, { key: 'learn', size: 'sm', variant: ls === 'learned' ? 'ghost' : 'secondary', onClick: onLearn, 'data-testid': 'topic-learn' }, ls === 'learned' ? 'Learn again' : ls === 'in_progress' ? 'Continue' : 'Learn') : null,
          h(Button, { key: 'practice', size: 'sm', variant: 'ghost', onClick: onPractice }, 'Practice this')
        ]
      )
    ),
    open ? h('div', { style: { padding: `0 ${SP.lg}px ${SP.lg}px` } }, children) : null
  )
}

function CourseView({ courseId, course, initialSel, startPractice, startLearn, onRead }) {
  const st = useAsync(() => api(`/courses/${encodeURIComponent(courseId)}/map`), [courseId])
  const lv = useLearnOverview(courseId, 0).ov
  const learnOf = id => (lv ? lv.topics.find(t => t.objective_id === id) || null : null)
  const tl = useAsync(() => api(`/courses/${encodeURIComponent(courseId)}/timeline`), [courseId])
  const [sel, setSel] = useState(initialSel || null)
  // The student's last choice (List, Map, Timeline) sticks, also across courses.
  const [modeWanted, setModeRaw] = useState(initialSel ? 'map' : CTX.storage.get('courseMode', 'list'))
  const setMode = m => (CTX.storage.set('courseMode', m), setModeRaw(m))
  const hasTimeline = !!(tl.data && tl.data.events && tl.data.events.length)
  const modes = [['list', 'List', 'list-unordered'], ['map', 'Map', 'type-hierarchy']].concat(hasTimeline ? [['timeline', 'Timeline', 'history']] : [])
  return h(Async, { st, what: 'the course' }, map => {
    const objs = map.units.flatMap(u => u.lessons.flatMap(l => l.objectives))
    const obj = objs.find(o => o.id === sel)
    const sm = courseSummary(objs)
    const dueNow = objs.reduce((n, o) => n + (o.coverage === 'covered' ? o.due_reviews || 0 : 0), 0)
    const mode = modeWanted === 'timeline' && !hasTimeline ? 'list' : modeWanted
    const strict = course && course.mode === 'strict'
    return h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: SP.lg, maxWidth: mode === 'map' ? '100%' : MAX_WIDE, margin: '0 auto' } },
      h(
        'div',
        { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: SP.md, padding: SP.xl } },
        h('h1', { style: { fontSize: FS.big, lineHeight: '32px', fontWeight: 600, margin: 0, overflowWrap: 'anywhere' } }, map.course.title),
        h('div', { style: { fontSize: FS.lead, color: C.t2 }, 'data-testid': 'course-summary' }, sm.text),
        h('div', { style: { maxWidth: 420 } }, h(Bar, { value: sm.covered ? sm.known / sm.covered : 0, tone: 'good', height: 8, label: 'Topics known' })),
        h(
          'div',
          { style: { ...S.row, gap: SP.md, flexWrap: 'wrap', marginTop: SP.xs } },
          lv && lv.next_objective_id
            ? [
                h(Button, { key: 'learn', onClick: () => startLearn(lv.next_objective_id), 'data-testid': 'continue-learning' }, h(Codicon, { name: 'play' }), 'Continue learning'),
                h(Button, { key: 'review', variant: 'secondary', disabled: !sm.covered, onClick: () => startPractice(null), 'data-testid': 'continue-practice' }, h(Codicon, { name: 'history' }), dueNow ? `Review (${dueNow} due)` : 'Review')
              ]
            : h(Button, { disabled: !sm.covered, onClick: () => startPractice(null), 'data-testid': 'continue-practice' }, h(Codicon, { name: lv ? 'history' : 'play' }), lv ? `Review (${dueNow} due)` : 'Continue practice'),
          h(ExportGuide, { courseId }),
          h(Button, { size: 'sm', variant: 'ghost', onClick: () => onRead() }, h(Codicon, { name: 'book' }), 'Read')
        ),
        course
          ? h(
              'div',
              { style: { ...S.row, ...S.muted }, 'data-testid': 'course-mode' },
              h(Codicon, { name: strict ? 'shield' : 'info', style: { color: strict ? C.green : C.yellow } }),
              strict ? 'Answers come only from your materials.' : `Open mode: ${course.mode_reason || 'answers may use general knowledge.'}`
            )
          : null
      ),
      h(
        'div',
        { style: { ...S.row, justifyContent: 'space-between', flexWrap: 'wrap', gap: SP.md } },
        h('div', { role: 'group', 'aria-label': 'Course view', style: { ...S.row, gap: SP.xs } }, modes.map(([id, label, icon]) => h(Button, { key: id, size: 'sm', variant: mode === id ? 'secondary' : 'ghost', 'aria-pressed': mode === id, onClick: () => setMode(id) }, h(Codicon, { name: icon }), label))),
        mode === 'map' ? h(Legend, null) : null
      ),
      mode === 'timeline'
        ? h(TimelineView, { courseId })
        : mode === 'map'
          ? h(
              'div',
              { style: { display: 'flex', flexDirection: 'column', gap: SP.md } },
              h(MapGraph, { objs, edges: map.edges, selected: sel, onSelect: setSel }),
              obj ? h(ObjectivePanel, { obj, map, courseId, onSelect: setSel, onPractice: () => startPractice(obj.id) }) : h('div', { style: S.muted }, 'Select a topic to read its lesson and see its questions.')
            )
          : h(
              'div',
              { style: { ...S.card, padding: 0, overflow: 'hidden' }, 'data-testid': 'topic-list' },
              objs.length === 0 ? h('div', { style: { padding: SP.xl, ...S.muted } }, 'No topics yet. Import some materials in the Library.') : null,
              objs.map(o => h(TopicRow, { key: o.id, o, open: sel === o.id, onToggle: () => setSel(sel === o.id ? null : o.id), onPractice: () => startPractice(o.id), onLearn: () => startLearn(o.id), learn: learnOf(o.id) }, h(ObjectivePanel, { obj: o, map, courseId, onSelect: setSel, onPractice: () => startPractice(o.id), inList: true })))
            )
    )
  })
}

// ---------------------------------------------------------------------------
// Practice: lesson step, then one question at a time
// ---------------------------------------------------------------------------

function initialResponse(item) {
  const p = item.payload
  switch (item.format) {
    case 'mc':
      return null
    case 'order':
      return p.events.map((_, i) => i)
    case 'match':
      return p.lefts.map(() => '')
    case 'numeric':
      return { val: '', unit: p.unit || '' }
    case 'explain':
      return { text: '', shown: false }
    default:
      return ''
  }
}

function toRequest(item, v) {
  switch (item.format) {
    case 'match':
      return v.map((r, l) => [l, Number(r)])
    case 'numeric':
      return `${v.val} ${v.unit}`.trim()
    default:
      return v
  }
}

function isReady(item, v) {
  switch (item.format) {
    case 'mc':
      return v !== null
    case 'match':
      return v.every(r => r !== '') && new Set(v).size === v.length
    case 'numeric':
      return String(v.val).trim() !== ''
    case 'explain':
      return false
    case 'order':
      return true
    default:
      return String(v).trim() !== ''
  }
}

const optionStyle = (active, disabled) => ({
  display: 'flex',
  gap: 10,
  alignItems: 'flex-start',
  textAlign: 'left',
  width: '100%',
  padding: '9px 11px',
  borderRadius: 5,
  cursor: disabled ? 'default' : 'pointer',
  color: C.text,
  fontSize: 13.5,
  background: active ? tint(C.accent, 14) : tint(C.text, 3),
  border: `1px solid ${active ? C.accent : C.line}`
})

// After the answer: the right option goes green, a wrong pick goes red.
function markOption(base, result, picked, text) {
  if (!result) return base
  if (text === result.correct_answer) return { ...base, background: tint(C.green, 14), border: `1px solid ${C.green}` }
  if (picked) return { ...base, background: tint(C.red, 12), border: `1px solid ${C.red}` }
  return base
}

function McInput({ item, value, onChange, disabled, result }) {
  return h(
    'div',
    { role: 'radiogroup', style: { display: 'flex', flexDirection: 'column', gap: 6 } },
    item.payload.options.map((o, i) =>
      h(
        'button',
        {
          key: i,
          type: 'button',
          role: 'radio',
          'aria-checked': value === i,
          disabled,
          onClick: () => onChange(i),
          style: markOption(optionStyle(value === i, disabled), result, value === i, o)
        },
        h('span', { style: { color: C.t3, fontWeight: 600, width: 16 } }, String.fromCharCode(65 + i)),
        h('span', { style: { flex: 1 } }, o),
        result && o === result.correct_answer ? h(Chip, { tone: 'good' }, 'Correct answer') : result && value === i ? h(Chip, { tone: 'bad' }, 'Your answer') : null
      )
    )
  )
}

function ClozeInput({ item, value, onChange, disabled, onEnter }) {
  const parts = item.payload.text_with_blank.split('____')
  return h(
    'div',
    { 'data-testid': 'question-prompt', style: { lineHeight: '40px', fontSize: FS.title, fontWeight: 500 } },
    parts[0],
    h('input', {
      value,
      disabled,
      'aria-label': 'Fill in the blank',
      onChange: e => onChange(e.target.value),
      onKeyDown: e => e.key === 'Enter' && onEnter(),
      style: { ...S.field, width: 190, display: 'inline-block', margin: '0 6px', borderColor: C.accent, fontSize: FS.lead }
    }),
    parts[1] || ''
  )
}

function OrderInput({ item, value, onChange, disabled }) {
  const move = (pos, dir) => {
    const next = value.slice()
    const j = pos + dir
    ;[next[pos], next[j]] = [next[j], next[pos]]
    onChange(next)
  }
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: 6 } },
    h('div', { style: S.muted }, 'Put these in the order they happened, earliest first.'),
    value.map((evIdx, pos) =>
      h(
        'div',
        { key: evIdx, style: { ...S.row, ...optionStyle(false, true), cursor: 'default' } },
        h('span', { style: { color: C.t3, fontWeight: 600, width: 18 } }, pos + 1),
        h('span', { style: { flex: 1 } }, item.payload.events[evIdx].label),
        h(Button, { size: 'icon-xs', variant: 'ghost', disabled: disabled || pos === 0, onClick: () => move(pos, -1), 'aria-label': `Move "${item.payload.events[evIdx].label}" up` }, h(Codicon, { name: 'arrow-up' })),
        h(Button, { size: 'icon-xs', variant: 'ghost', disabled: disabled || pos === value.length - 1, onClick: () => move(pos, 1), 'aria-label': `Move "${item.payload.events[evIdx].label}" down` }, h(Codicon, { name: 'arrow-down' }))
      )
    )
  )
}

function MatchInput({ item, value, onChange, disabled }) {
  const used = new Set(value.filter(v => v !== ''))
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: 6 } },
    h('div', { style: S.muted }, 'Match each item on the left to one on the right. Each answer is used once.'),
    item.payload.lefts.map((l, li) =>
      h(
        'div',
        { key: li, style: { display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 10, alignItems: 'center' } },
        h('div', { style: { ...optionStyle(false, true), cursor: 'default' } }, l),
        h(
          'select',
          {
            value: value[li],
            disabled,
            'aria-label': `Match for ${l}`,
            onChange: e => {
              const next = value.slice()
              next[li] = e.target.value
              onChange(next)
            },
            style: { ...S.field, padding: '9px 8px' }
          },
          h('option', { value: '' }, 'Choose...'),
          item.payload.rights.map((r, ri) => h('option', { key: ri, value: String(ri), disabled: used.has(String(ri)) && value[li] !== String(ri) }, r))
        )
      )
    )
  )
}

function TextInput({ item, value, onChange, disabled, onEnter, multi, placeholder }) {
  if (multi) {
    return h(Textarea, { value, rows: 4, disabled, onChange: e => onChange(e.target.value), placeholder, 'aria-label': 'Your answer' })
  }
  return h(Input, {
    value,
    disabled,
    placeholder,
    'aria-label': 'Your answer',
    onChange: e => onChange(e.target.value),
    onKeyDown: e => e.key === 'Enter' && onEnter()
  })
}

function NumericInput({ item, value, onChange, disabled, onEnter }) {
  const p = item.payload
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
    p.given.length
      ? h(
          'div',
          { style: { ...S.row, flexWrap: 'wrap' } },
          p.given.map(q => h(Chip, { key: q.name }, `${q.name} = ${q.value}${q.unit ? ' ' + q.unit : ''}`))
        )
      : null,
    h(
      'div',
      { style: S.row },
      h('span', { style: S.muted }, p.quantity || 'Answer'),
      h(Input, { value: value.val, disabled, inputMode: 'decimal', placeholder: 'value', 'aria-label': 'Value', style: { width: 140 }, onChange: e => onChange({ ...value, val: e.target.value }), onKeyDown: e => e.key === 'Enter' && onEnter() }),
      h(Input, { value: value.unit, disabled, placeholder: 'unit', 'aria-label': 'Unit', style: { width: 100 }, onChange: e => onChange({ ...value, unit: e.target.value }), onKeyDown: e => e.key === 'Enter' && onEnter() })
    )
  )
}

const MATH_HINTS = {
  solve: 'Type the value(s), e.g. 4   or   2, 3   (x = 2 or x = 3 also works)',
  evaluate: 'Type a number, e.g. 0.5, 1/2 or 25%',
  diff: 'Type an expression, e.g. 3x^2 + 2   (x^2, x**2 and x\u00b2 all work)',
  integrate: 'Type an expression, e.g. x^2 / 2   (a + C is optional)',
  simplify: 'Type an expression, e.g. x + 1   (x^2, x**2 and x\u00b2 all work)'
}

function MathInput({ item, value, onChange, disabled, onEnter }) {
  const p = item.payload
  const OPS = { diff: 'Differentiate', integrate: 'Integrate', solve: 'Solve', simplify: 'Simplify', evaluate: 'Evaluate' }
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
    h(
      'div',
      { style: { ...S.row, flexWrap: 'wrap' } },
      h(Chip, { tone: 'accent' }, `${OPS[p.op] || p.op} with respect to ${p.var}`),
      h('code', { style: { fontFamily: MONO, fontSize: 14 } }, p.expr)
    ),
    p.origin === 'practice' ? h('div', { style: S.muted }, 'Practice problem made by CodeCoach \u00b7 the answer is checked by SymPy') : null,
    h(Input, { value, disabled, placeholder: p.units_ok ? 'Type a number (units are optional), e.g. 25 or 25 percent' : MATH_HINTS[p.op] || MATH_HINTS.simplify, 'aria-label': 'Your expression', style: { fontFamily: MONO }, onChange: e => onChange(e.target.value), onKeyDown: e => e.key === 'Enter' && onEnter() })
  )
}

const RATINGS = [
  { v: 0, label: 'Not close' },
  { v: 0.33, label: 'Partly' },
  { v: 0.67, label: 'Mostly' },
  { v: 1, label: 'Spot on' }
]

function ExplainInput({ item, value, onChange, disabled, objectiveId, courseId, onRate }) {
  const passages = useAsync(
    () =>
      value.shown
        ? api(`/courses/${encodeURIComponent(courseId)}/map`).then(m => {
            const o = m.units.flatMap(u => u.lessons.flatMap(l => l.objectives)).find(x => x.id === objectiveId)
            return Promise.all(((o && o.passage_ids) || []).slice(0, 3).map(id => api(`/passages/${encodeURIComponent(id)}`)))
          })
        : Promise.resolve(null),
    [value.shown]
  )
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: 10 } },
    h(Textarea, { value: value.text, rows: 5, disabled: disabled || value.shown, placeholder: 'Explain it in your own words first...', 'aria-label': 'Your explanation', onChange: e => onChange({ ...value, text: e.target.value }) }),
    !value.shown
      ? h('div', { style: S.row }, h(Button, { disabled: !value.text.trim(), onClick: () => onChange({ ...value, shown: true }) }, 'Show the source passage'), h('span', { style: S.muted }, 'Nothing is graded by AI. You compare with the passage and rate yourself.'))
      : h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: 10 } },
          h(Async, { st: passages, what: 'the passage' }, list =>
            h('div', { style: { display: 'flex', flexDirection: 'column', gap: 10 } }, (list || []).map((r, i) => h(PassageBlock, { key: `${i}-${r.passage.id}`, location: r.passage.location, text: r.passage.text, docName: r.doc_name })))
          ),
          h('div', { style: { fontWeight: 600 } }, 'How close was your answer to the passage?'),
          h(
            'div',
            { style: S.row, role: 'group', 'aria-label': 'Self rating' },
            RATINGS.map(r => h(Button, { key: r.v, variant: 'outline', disabled, onClick: () => onRate(r.v) }, r.label))
          ),
          h('div', { style: S.muted }, 'Self-rated answers count for half as much toward mastery.')
        )
  )
}

function FormatInput(props) {
  switch (props.item.format) {
    case 'mc':
      return h(McInput, props)
    case 'cloze':
      return h(ClozeInput, props)
    case 'order':
      return h(OrderInput, props)
    case 'match':
      return h(MatchInput, props)
    case 'short':
      return h(
        'div',
        { style: { display: 'flex', flexDirection: 'column', gap: 6 } },
        h(TextInput, { ...props, multi: true, placeholder: 'Answer in a sentence or two...' }),
        h('div', { style: S.muted }, `Checked for ${props.item.payload.terms_expected} key term${props.item.payload.terms_expected === 1 ? '' : 's'} from your materials.`)
      )
    case 'numeric':
      return h(NumericInput, props)
    case 'math':
      return h(MathInput, props)
    case 'explain':
      return h(ExplainInput, props)
    default:
      return h(Banner, { tone: 'warn' }, `This version cannot show "${props.item.format}" questions yet.`)
  }
}

// `inline`: just the quiet Hint button (it sits beside Check answer); the hint text itself is shown below by the caller.
function HintLadder({ item, hints, onHint, busy, err, inline }) {
  const next = hints.length + 1
  const max = item.hints_available || 3
  const button = next <= max ? h(Button, { variant: 'ghost', size: 'sm', disabled: busy, onClick: onHint }, h(Codicon, { name: 'lightbulb' }), hints.length ? `Stronger hint (${next} of ${max})` : 'Hint') : null
  if (inline === true) return button
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
    hints.map(r =>
      h(
        'div',
        { key: r.hint.level, style: { ...S.card, padding: SP.md, background: tint(C.accent, 7) } },
        h('div', { style: { ...S.row, marginBottom: 4 } }, h(Chip, { tone: 'accent' }, `Hint ${r.hint.level} of ${max}`), r.location ? h('span', { style: S.muted }, r.location) : null),
        h('div', null, r.hint.text)
      )
    ),
    err ? h(Banner, { tone: 'bad' }, err) : null,
    button && inline !== 'list' ? h('div', null, button) : null
  )
}

function CheckedBadge({ item }) {
  const cb = checkedBy(item)
  if (cb) return h(Chip, { tone: 'good', title: 'How CodeCoach verifies the answer to this question' }, h(Codicon, { name: cb.icon }), cb.text)
  return h(Chip, { tone: 'warn', title: 'Not checked against your materials' }, item.source_label || 'Not from your materials')
}

// `learn`: Learn mode shows no score, no topic progress and no review times; the verdict is just right or not yet.
function Verdict({ res, item, learn }) {
  const selfRated = res.correct === null || res.correct === undefined
  const tone = selfRated ? 'accent' : res.correct ? 'good' : 'bad'
  const col = tone === 'good' ? C.green : tone === 'bad' ? C.red : C.accent
  const title = selfRated ? 'Self-rated' : res.correct ? 'Correct' : 'Not quite'
  const bits = learn ? null : partialText(item, res)
  return h(
    'div',
    { style: { border: `1px solid ${tint(col, 45)}`, background: tint(col, 9), borderRadius: 6, padding: 12, display: 'flex', flexDirection: 'column', gap: 6 } },
    h(
      'div',
      { style: { ...S.row, flexWrap: 'wrap' } },
      h(Codicon, { name: tone === 'good' ? 'pass-filled' : tone === 'bad' ? 'error' : 'info', style: { color: col, fontSize: 16 } }),
      h('span', { style: { fontWeight: 600, fontSize: 14 } }, title),
      bits ? h('span', { style: S.muted }, bits) : null,
      !learn && res.mastery !== null && res.mastery !== undefined ? h(Chip, null, `Topic progress ${pct(res.mastery)}`) : null,
      !learn && res.next_review && whenAgain(res.next_review.due) ? h(Chip, null, `You'll see this again ${whenAgain(res.next_review.due)}`) : null
    ),
    trimVerdict(res.feedback) ? h('div', null, trimVerdict(res.feedback)) : null,
    res.correct === false && res.correct_answer !== null && res.correct_answer !== undefined && item.format !== 'code'
      ? h('div', null, h('span', { style: S.muted }, 'Answer: '), h('strong', null, typeof res.correct_answer === 'string' ? res.correct_answer : JSON.stringify(res.correct_answer)))
      : null
  )
}

// SVG plot of a SymPy-verified expression (GET /items/{id}/plot).
function PlotChart({ plot }) {
  const W = 640
  const H = 300
  const m = { l: 46, r: 14, t: 14, b: 36 }
  const series = plot.series
  const ys = series.flatMap(s => s.points.map(p => p[1]).filter(y => y !== null && Number.isFinite(y)))
  const [x0, x1] = plot.x_range
  let y0 = Math.min(...ys, 0)
  let y1 = Math.max(...ys, 0)
  if (y0 === y1) {
    y0 -= 1
    y1 += 1
  }
  const pad = (y1 - y0) * 0.06
  y0 -= pad
  y1 += pad
  const sx = x => m.l + ((x - x0) / (x1 - x0)) * (W - m.l - m.r)
  const sy = y => H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b)
  const ticks = (a, b, n) => {
    const raw = (b - a) / n
    const mag = Math.pow(10, Math.floor(Math.log10(raw)))
    const step = [1, 2, 5, 10].map(k => k * mag).find(s => s >= raw) || raw
    const out = []
    for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) out.push(Math.abs(v) < 1e-9 ? 0 : v)
    return out
  }
  const cols = [C.accent, C.yellow, C.green, C.red]
  const segs = s => {
    const out = []
    let cur = []
    s.points.forEach(([x, y]) => {
      if (y === null || !Number.isFinite(y)) {
        if (cur.length) out.push(cur)
        cur = []
      } else cur.push(`${sx(x).toFixed(1)},${sy(y).toFixed(1)}`)
    })
    if (cur.length) out.push(cur)
    return out
  }
  const fmt = v => (Math.abs(v) >= 100 || Number.isInteger(v) ? String(Math.round(v)) : v.toFixed(1))
  return h(
    'div',
    { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 8 } },
    h('div', { style: { ...S.row, justifyContent: 'space-between' } }, h('div', { style: { fontWeight: 600 } }, plot.title), h(Chip, { tone: 'good', title: 'Every point was computed by SymPy from the expression' }, `computed by ${plot.verified_by}`)),
    h(
      'svg',
      { viewBox: `0 0 ${W} ${H}`, width: '100%', style: { maxWidth: 760, display: 'block' }, role: 'img', 'aria-label': `Plot: ${plot.title}` },
      ticks(y0, y1, 8).map(v => h('g', { key: `y${v}` }, h('line', { x1: m.l, x2: W - m.r, y1: sy(v), y2: sy(v), stroke: C.line, strokeOpacity: 0.5, strokeWidth: 1 }), h('text', { x: m.l - 6, y: sy(v) + 3.5, fontSize: 10, textAnchor: 'end', fill: C.t3 }, fmt(v)))),
      ticks(x0, x1, 6).map(v => h('g', { key: `x${v}` }, h('line', { x1: sx(v), x2: sx(v), y1: m.t, y2: H - m.b, stroke: C.line, strokeOpacity: 0.35, strokeWidth: 1 }), h('text', { x: sx(v), y: H - m.b + 15, fontSize: 10, textAnchor: 'middle', fill: C.t3 }, fmt(v)))),
      y0 < 0 && y1 > 0 ? h('line', { x1: m.l, x2: W - m.r, y1: sy(0), y2: sy(0), stroke: C.t2, strokeWidth: 1.2 }) : null,
      x0 < 0 && x1 > 0 ? h('line', { x1: sx(0), x2: sx(0), y1: m.t, y2: H - m.b, stroke: C.t2, strokeWidth: 1.2 }) : null,
      series.map((s, i) => segs(s).map((pts, k) => h('polyline', { key: `${i}-${k}`, points: pts.join(' '), fill: 'none', stroke: cols[i % cols.length], strokeWidth: 2, strokeLinejoin: 'round' }))),
      h('text', { x: (m.l + W - m.r) / 2, y: H - 4, fontSize: 11, textAnchor: 'middle', fill: C.t2 }, plot.x_label),
      h('text', { x: 12, y: (m.t + H - m.b) / 2, fontSize: 11, textAnchor: 'middle', fill: C.t2, transform: `rotate(-90 12 ${(m.t + H - m.b) / 2})` }, plot.y_label)
    ),
    h('div', { style: { ...S.row, gap: 16, flexWrap: 'wrap' } }, series.map((s, i) => h('span', { key: i, style: { ...S.row, gap: 6, fontSize: 12 } }, h('span', { style: { width: 16, height: 3, background: cols[i % cols.length], borderRadius: 2 } }), h('span', null, s.label), h('code', { style: { fontFamily: MONO, color: C.t3, fontSize: 11 } }, s.expr))))
  )
}

function PlotLoader({ itemId }) {
  const st = useAsync(() => api(`/items/${encodeURIComponent(itemId)}/plot`), [itemId])
  return h(Async, { st, what: 'the graph' }, plot => h(PlotChart, { plot }))
}

// --- the live code box -----------------------------------------------------

const DEFAULT_SANDBOX_NOTE = 'sandboxed: no network, time and memory limits'

function CodeBox({ item, submit, locked }) {
  const p = item.payload
  const [code, setCode] = useState(p.starter || '')
  const [run, setRun] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [sandboxNote, setSandboxNote] = useState(null)
  const [dirty, setDirty] = useState(false)
  const codeRef = useRef(code)
  const inflight = useRef(false)
  const timer = useRef(null)
  const runner = useRef(null)
  const escaped = useRef(false)
  const taRef = useRef(null)

  const runNow = () => {
    if (inflight.current || locked) return
    inflight.current = true
    setBusy(true)
    const sent = codeRef.current
    api(`/items/${encodeURIComponent(item.id)}/run`, { method: 'POST', body: { code: sent } })
      .then(
        res => {
          // Drop the answer if the learner kept typing: it describes stale code.
          if (codeRef.current === sent) {
            setRun(res)
            setErr(null)
            setDirty(false)
            if (res && typeof res.sandbox === 'string') setSandboxNote(res.sandbox)
          }
        },
        e => {
          if (codeRef.current === sent) {
            const msg = errText(e)
            setErr(msg)
            if (/sandbox/i.test(msg)) setSandboxNote(msg)
          }
        }
      )
      .finally(() => {
        inflight.current = false
        setBusy(false)
        if (codeRef.current !== sent) runner.current()
      })
  }
  runner.current = runNow

  // Show the starter's visible tests right away (red until the learner fixes it) instead of "not run yet".
  useEffect(() => {
    if (p.starter && !locked) runner.current()
  }, [item.id])

  useEffect(
    () => () => {
      if (timer.current) timer.current()
    },
    []
  )

  const onChange = v => {
    codeRef.current = v
    setCode(v)
    setDirty(true)
    if (timer.current) timer.current()
    timer.current = CTX.setTimeout(() => runner.current(), 800)
  }

  const onKeyDown = e => {
    if (e.key === 'Escape') {
      escaped.current = true
      return
    }
    if (e.key === 'Tab' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !escaped.current) {
      e.preventDefault()
      const ta = e.target
      const a = ta.selectionStart
      const b = ta.selectionEnd
      const v = ta.value.slice(0, a) + '    ' + ta.value.slice(b)
      onChange(v)
      CTX.setTimeout(() => {
        if (taRef.current) taRef.current.selectionStart = taRef.current.selectionEnd = a + 4
      }, 0)
      return
    }
    escaped.current = false
  }

  const results = run && run.results ? run.results : null
  const names = results || p.visible_tests.map(t => ({ name: t.name, passed: null }))
  const passed = run ? run.passed : 0
  const total = run ? run.total : p.visible_tests.length
  const unavailable = sandboxNote && sandboxNote !== DEFAULT_SANDBOX_NOTE

  return h(
    'div',
    { style: { display: 'grid', gridTemplateColumns: 'minmax(0,1.3fr) minmax(0,1fr)', gap: 14, alignItems: 'start' } },
    h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
      h(
        'div',
        { style: { ...S.row, justifyContent: 'space-between' } },
        h('span', { style: { ...S.row, ...S.muted } }, h(Chip, { tone: 'accent' }, p.language === 'c' ? 'C' : 'Python'), 'Edit freely. Tests re-run when you pause typing.'),
        h(Chip, { tone: unavailable ? 'bad' : 'good', title: `${p.time_limit_s}s per run, ${p.mem_mb} MB memory` }, h(Codicon, { name: 'shield' }), sandboxNote || DEFAULT_SANDBOX_NOTE)
      ),
      h(Textarea, {
        ref: taRef,
        value: code,
        rows: 15,
        disabled: locked,
        spellCheck: false,
        'aria-label': `${p.language} code editor`,
        onChange: e => onChange(e.target.value),
        onKeyDown,
        wrap: 'off',
        style: { fontFamily: MONO, fontSize: 13, lineHeight: '19px', tabSize: 4, whiteSpace: 'pre', overflow: 'auto', resize: 'vertical', minHeight: 280 }
      }),
      h('div', { style: S.muted }, 'Tab inserts spaces. Press Esc then Tab to move focus out of the editor.'),
      h(
        'div',
        { style: S.row },
        h(Button, { disabled: locked || busy, onClick: () => submit(codeRef.current) }, 'Submit: run hidden tests'),
        h(Button, { variant: 'outline', disabled: locked, onClick: () => runner.current() }, 'Run now'),
        h('span', { style: S.muted }, p.hidden_test_count ? `${p.hidden_test_count} hidden tests run on submit` : '')
      )
    ),
    h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
      h(
        'div',
        { style: { ...S.row, justifyContent: 'space-between' } },
        h('h3', { style: S.h3 }, 'Visible tests'),
        h(
          'span',
          { style: { ...S.row, ...S.muted } },
          busy ? h(Codicon, { name: 'loading', spinning: true }) : null,
          busy ? 'running' : dirty ? 'waiting for you to pause' : run ? `${passed} of ${total} passing` : 'not run yet'
        )
      ),
      err ? h(Banner, { tone: 'bad' }, err) : null,
      h(RunResults, { run, names, dirty })
    )
  )
}

// The result of a code run: compile error, timeout note and one row per test (expected vs got). Shared by the
// Practice code box and the Project screen. Hidden tests (visible === false) show pass/fail only.
function RunResults({ run, names, dirty }) {
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
    run && run.compile_error
      ? h('div', null, h('div', { style: { ...S.row, color: C.red, fontWeight: 600, marginBottom: 4 } }, h(Codicon, { name: 'error' }), 'Compile error'), h('pre', { style: { ...mono(), borderColor: tint(C.red, 45), color: C.text } }, run.compile_error))
      : null,
    run && run.timed_out ? h(Banner, { tone: 'warn' }, 'The program took too long and was stopped.') : null,
    h(
      'ul',
      { style: { listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, opacity: dirty && run ? 0.65 : 1, transition: 'opacity 120ms' } },
      names.map(t => {
        const col = t.passed === null ? C.t4 : t.passed ? C.green : C.red
        return h(
          'li',
          { key: t.name, 'data-run-result': t.passed === null ? 'none' : t.passed ? 'pass' : 'fail', style: { border: `1px solid ${t.passed === null ? C.line : tint(col, 45)}`, background: t.passed === null ? 'transparent' : tint(col, 8), borderRadius: 5, padding: '7px 10px' } },
          h(
            'div',
            { style: S.row },
            h(Codicon, { name: t.passed === null ? 'circle-outline' : t.passed ? 'pass-filled' : 'error', style: { color: col } }),
            h('span', { style: { fontWeight: 600, flex: 1 } }, t.name),
            h('span', { style: { color: col, fontSize: 11 } }, t.passed === null ? 'not run' : t.passed ? 'passed' : 'failed'),
            t.time_ms !== null && t.time_ms !== undefined && t.passed !== null ? h('span', { style: S.muted }, `${t.time_ms} ms`) : null
          ),
          t.passed === false
            ? h(
                'div',
                { style: { marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 } },
                t.detail && !(run && run.compile_error) ? h('div', { style: { fontSize: 12 } }, t.detail) : null,
                (run && run.compile_error) || (t.expected === null && t.stdout === null && t.visible === false)
                  ? null
                  : h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 } }, h('div', null, h('div', { style: S.muted }, 'expected'), h('pre', { style: mono() }, shown(t.expected))), h('div', null, h('div', { style: S.muted }, 'got'), h('pre', { style: mono() }, shown(t.stdout))))
              )
            : null
        )
      })
    )
  )
}

const shown = s => (s === null || s === undefined || s === '' ? '(nothing)' : s)
function mono() {
  return { fontFamily: MONO, fontSize: 12, margin: 0, padding: '5px 7px', background: tint(C.text, 6), border: `1px solid ${C.line}`, borderRadius: 4, whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: C.text }
}

function LessonCard({ lesson, courseId, onStart, onRead }) {
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 820 } },
    h('div', { style: S.muted }, `${lesson.unit_title} / ${lesson.lesson_title}`),
    h('div', { style: S.h2 }, topicTitle(lesson.statement)),
    courseId ? h(ListenBar, { courseId, passages: lesson.passages.map(p => ({ id: p.passage_id, doc_id: p.doc_id, text: p.text })), what: 'this topic' }) : null,
    lesson.framing ? h('div', { style: { color: C.t2 } }, lesson.framing) : null,
    h('div', { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 14 } }, lesson.passages.map(ps => h('div', { key: ps.passage_id }, h(PassageBlock, { location: ps.location, text: ps.text, label: lesson.source_label, cite: { doc_id: ps.doc_id, passage_id: ps.passage_id } }), onRead ? h(Button, { size: 'sm', variant: 'ghost', onClick: () => onRead(ps.passage_id), 'data-testid': 'read-in-context' }, h(Codicon, { name: 'book' }), 'Read in context') : null))),
    h('div', { style: S.row }, h(Button, { onClick: onStart }, 'I have read it: start the questions'), h('span', { style: S.muted }, 'The passage is hidden while a question is open. You will see it again with your answer.'))
  )
}

// Practice, plus "Mistakes (N)": a session of up to 10 items whose latest attempt scored below full credit,
// oldest first. Grading and scheduling are the normal ones; the server decides the list.
const MISTAKES_MAX = 10

function PracticeView(props) {
  const { courseId, objectiveId, token, onProgressChange } = props
  const [mist, setMist] = useState(null)
  const [run, setRun] = useState(0)
  const [count, setCount] = useState(0)
  const refresh = useCallback(() => {
    api(`/courses/${encodeURIComponent(courseId)}/mistakes`)
      .then(r => setCount(r.count))
      .catch(() => {})
  }, [courseId])
  useEffect(() => {
    refresh()
  }, [refresh, token, run])
  useEffect(() => {
    setMist(null)
  }, [token, objectiveId, courseId])
  const start = async () => {
    try {
      const r = await api(`/courses/${encodeURIComponent(courseId)}/mistakes`)
      setCount(r.count)
      if (!r.count) return
      setMist({ upto: r.upto, served: 0, max: Math.min(r.session, MISTAKES_MAX) })
      setRun(x => x + 1)
    } catch (_e) {
      /* The button stays; nothing changed. */
    }
  }
  const leave = () => {
    setMist(null)
    setRun(x => x + 1)
  }
  const bar = mist
    ? h('div', { style: S.row, 'data-mistakes-session': `${mist.served}/${mist.max}` }, h(Chip, { tone: 'warn' }, `Mistakes review: ${Math.min(mist.served, mist.max)} of ${mist.max}`), h(Button, { size: 'sm', variant: 'ghost', onClick: leave, 'data-testid': 'mistakes-leave' }, 'Back to normal practice'))
    : count > 0
      ? h('div', { style: S.row }, h(Button, { size: 'sm', variant: 'outline', onClick: start, 'data-testid': 'mistakes-start', 'data-count': count, title: 'Practise the questions you last got wrong' }, h(Codicon, { name: 'debug-restart' }), `Mistakes (${count})`))
      : null
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.md } },
    bar ? h('div', { style: { maxWidth: 760, width: '100%', margin: '0 auto' } }, bar) : null,
    h(PracticeBody, {
      ...props,
      token: `${token}:${run}`,
      mist,
      onServed: () => setMist(m => (m ? { ...m, served: m.served + 1 } : m)),
      onProgressChange: () => {
        if (onProgressChange) onProgressChange()
        refresh()
      }
    })
  )
}

function PracticeBody({ courseId, objectiveId, token, onProgressChange, onRead, onLearn, paused, onCloseSources, mist, onServed }) {
  const mistRef = useRef(null)
  mistRef.current = mist
  const servedRef = useRef(null)
  servedRef.current = onServed
  const [step, setStep] = useState('loading')
  const [next, setNext] = useState(null)
  const [resp, setResp] = useState(null)
  const [res, setRes] = useState(null)
  const [hints, setHints] = useState([])
  const [hintBusy, setHintBusy] = useState(false)
  const [hintErr, setHintErr] = useState(null)
  const [err, setErr] = useState(null)
  const [reported, setReported] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [plotFor, setPlotFor] = useState(false)
  const pausedRef = useRef(false)
  pausedRef.current = !!paused

  // While Sources is open no question may sit unanswered beside the source: it goes back to the queue ungraded
  // (the same POST /items/:id/release that opening Read uses) and Practice waits for the panel to close.
  const heldRef = useRef(null)
  const holdItem = async it => {
    openQuestion.id = null
    if (heldRef.current === it.id) return
    heldRef.current = it.id
    try {
      await api(`/items/${encodeURIComponent(it.id)}/release`, { method: 'POST' })
    } catch (_e) {
      /* Nothing was graded either way. */
    }
  }

  const load = useCallback(
    async objective => {
      setStep('loading')
      setErr(null)
      setRes(null)
      setHints([])
      setHintErr(null)
      setReported(false)
      setPlotFor(false)
      try {
        const m = mistRef.current
        if (m && m.served >= m.max) {
          setNext({ kind: 'done', message: 'You have been through this mistakes session. Your wrong answers stay listed until you get them right.' })
          setStep('done')
          return
        }
        const q = m ? `?mistakes_upto=${m.upto}` : objective ? `?objective_id=${encodeURIComponent(objective)}` : ''
        const n = await api(`/courses/${encodeURIComponent(courseId)}/next${q}`)
        if (m && n.kind === 'item' && servedRef.current) servedRef.current()
        setNext(n)
        if (n.kind === 'item' && pausedRef.current) {
          holdItem(n.item)
          setStep('paused')
        } else if (n.kind === 'item') {
          setResp(initialResponse(n.item))
          setStep('item')
        } else setStep(n.kind)
      } catch (e) {
        setErr(errText(e))
        setStep('error')
      }
    },
    [courseId]
  )

  useEffect(() => {
    load(objectiveId)
  }, [load, objectiveId, token])

  const item = next && next.item
  openQuestion.id = step === 'item' && item ? item.id : null

  useEffect(() => {
    if (paused && step === 'item' && item) {
      holdItem(item)
      setStep('paused')
    } else if (!paused && step === 'paused') load(objectiveId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused, step])

  const submitResponse = async request => {
    if (submitting) return
    setSubmitting(true)
    setErr(null)
    try {
      const r = await api(`/items/${encodeURIComponent(item.id)}/answer`, { method: 'POST', body: { response: request } })
      setRes(r)
      setStep('answered')
      if (r.next_review) CTX.storage.set('nextDue', r.next_review.due)
      if (onProgressChange) onProgressChange()
    } catch (e) {
      setErr(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  const askHint = async () => {
    setHintBusy(true)
    setHintErr(null)
    try {
      const r = await api(`/items/${encodeURIComponent(item.id)}/hint`, { method: 'POST', body: { level: hints.length + 1 } })
      setHints(hs => [...hs, r])
    } catch (e) {
      setHintErr(errText(e))
    } finally {
      setHintBusy(false)
    }
  }

  const report = async () => {
    try {
      await api(`/items/${encodeURIComponent(item.id)}/report`, { method: 'POST', body: { reason: 'Reported from the CodeCoach page' } })
      setReported(true)
      host.notify({ kind: 'info', message: 'Reported. This question is out of your bank.' })
    } catch (e) {
      setErr(errText(e))
    }
  }

  if (step === 'loading') return h(Spinner, { text: 'Finding your next step' })
  if (step === 'error') return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 10 } }, h(Banner, { tone: 'bad' }, err), h('div', null, h(Button, { onClick: () => load(objectiveId) }, 'Try again')))
  if (step === 'done' && !mist)
    return h(
      'div',
      { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: SP.sm, maxWidth: 560, margin: '0 auto', padding: SP.xl, textAlign: 'center', alignItems: 'center' }, 'data-testid': 'practice-caught-up' },
      h(Codicon, { name: 'pass-filled', style: { color: C.green, fontSize: 24 } }),
      h('div', { style: { fontSize: FS.title, fontWeight: 600 } }, "You're caught up"),
      h(
        'div',
        { style: S.muted },
        'Nothing is due. Pick a topic on the ',
        h('button', { type: 'button', onClick: () => bus.emit('topic', null), 'data-testid': 'caught-up-map', style: { all: 'unset', cursor: 'pointer', color: C.accent, textDecoration: 'underline' } }, 'Course page'),
        ' to practise it now, or come back later.'
      )
    )
  if (step === 'done') return h(EmptyState, { title: 'Mistakes review finished', description: next.message || 'Come back when reviews are due, or open the map to pick an objective.' })
  if (step === 'paused')
    return h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: SP.md, maxWidth: 760, margin: '0 auto' }, 'data-practice-held': '1' },
      h(Banner, { tone: 'info' }, 'Your open question went back to the queue ungraded. Sources is open, and a question is never shown beside the source it was written from.'),
      h('div', null, h(Button, { onClick: onCloseSources }, 'Close Sources and continue'))
    )
  if (step === 'lesson') return h(LessonGate, { key: next.lesson.objective_id, onRead, onLearn, courseId, lesson: next.lesson, onStart: () => load(next.lesson.objective_id) })

  const answered = step === 'answered'
  const isCode = item.format === 'code'
  const ready = isReady(item, resp)
  const reasonLabel = { new: 'New', review: 'Review', interleave: 'Mixed practice', placement: 'Placement' }[next.reason]

  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.lg, maxWidth: isCode ? 1180 : 760, margin: '0 auto' } },
    h('div', { style: { ...S.row, flexWrap: 'wrap' } }, reasonLabel ? h(Chip, null, reasonLabel) : null, h(Chip, null, formatLabel(item.format)), CheckedBadge({ item }), h('div', { style: { flex: 1 } }), !reported ? h(Button, { size: 'xs', variant: 'ghost', onClick: report, title: 'Remove this question from your bank' }, h(Codicon, { name: 'report' }), 'Report this question') : h(Chip, { tone: 'warn' }, 'Reported')),
    h(
      'div',
      { style: { ...S.card, padding: SP.xl, display: 'flex', flexDirection: 'column', gap: SP.lg } },
      // Fill in the blank shows its sentence once, inline with the box (the chip above is the label).
      item.format === 'cloze' ? null : h('div', { style: { fontSize: FS.title, lineHeight: '30px', fontWeight: 500 }, 'data-testid': 'question-prompt' }, item.prompt),
      isCode
        ? h(CodeBox, { key: item.id, item, locked: answered, submit: code => submitResponse(code) })
        : h(FormatInput, { item, value: resp, onChange: setResp, disabled: answered || submitting, onEnter: () => ready && submitResponse(toRequest(item, resp)), result: res, objectiveId: item.objective_id, courseId, onRate: v => submitResponse(v) }),
      !answered && item.format !== 'explain' && !isCode
        ? h('div', { style: { ...S.row, gap: SP.md, flexWrap: 'wrap' } }, h(Button, { disabled: !ready || submitting, onClick: () => submitResponse(toRequest(item, resp)) }, 'Check answer'), !reported ? h(HintLadder, { item, hints, onHint: askHint, busy: hintBusy, err: hintErr, inline: true }) : null)
        : null,
      err && step !== 'error' ? h(Banner, { tone: 'bad' }, err) : null,
      !answered && !reported && item.format !== 'explain' && !isCode ? h(HintLadder, { item, hints, onHint: askHint, busy: hintBusy, err: hintErr, inline: 'list' }) : null,
      !answered && !reported && (item.format === 'explain' || isCode) ? h(HintLadder, { item, hints, onHint: askHint, busy: hintBusy, err: hintErr }) : null,
    ),
    answered
      ? h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.lg } },
          h(Verdict, { res, item }),
          res.citations && res.citations.length
            ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.md } }, h('h3', { style: S.h3 }, 'Where this comes from'), res.citations.map((c, i) => h(QuoteProof, { key: `${c.passage_id}-${i}`, citation: res.correct === false ? c : { ...c, key_spans: [] } })))
            : h('div', { style: S.muted }, item.origin === 'materials' ? '' : 'This question is not from your materials, so there is no source to show.'),
          item.format === 'math' ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 8 } }, !plotFor ? h('div', null, h(Button, { variant: 'outline', onClick: () => setPlotFor(true) }, h(Codicon, { name: 'graph' }), 'Show the graph')) : h(PlotLoader, { itemId: item.id })) : null,
          h('div', { style: S.row }, h(Button, { onClick: () => load(null) }, 'Next question'))
        )
      : null,
    reported && !answered ? h('div', null, h(Button, { onClick: () => load(null) }, 'Next question')) : null
  )
}

// ---------------------------------------------------------------------------
// Timeline: an SVG axis built from gate-passed dates
// ---------------------------------------------------------------------------

function TimelineChart({ events, selected, onSelect }) {
  const evs = events.filter(e => e.sort_year !== null && e.sort_year !== undefined)
  const W = 1180
  const H = 330
  const axisY = 170
  const m = 60
  if (evs.length === 0) return h(EmptyState, { title: 'No dated events yet', description: 'Dates appear here once questions with dates pass the source check.' })
  const y0 = Math.min(...evs.map(e => e.sort_year))
  const y1 = Math.max(...evs.map(e => e.sort_year))
  const span = Math.max(1, y1 - y0)
  const sx = y => m + ((y - y0) / span) * (W - 2 * m)
  const LEVELS = [-1, 1, -2, 2]
  // Give each label a lane (above/below, near/far) so neighbours never overlap.
  const placed = []
  evs.forEach((e, i) => {
    const x = sx(e.sort_year)
    let lane = LEVELS[i % LEVELS.length]
    for (let t = 0; t < LEVELS.length; t++) {
      const cand = LEVELS[(i + t) % LEVELS.length]
      if (!placed.some(p => p.lane === cand && Math.abs(p.x - x) < 150)) {
        lane = cand
        break
      }
    }
    placed.push({ x, lane, e })
  })
  const step = Math.max(1, Math.round(span / 8))
  const tickYears = []
  for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) tickYears.push(y)
  return h(
    'svg',
    { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': 'Timeline of events from your materials', style: { display: 'block', border: `1px solid ${C.line}`, borderRadius: 6, background: tint(C.text, 2) } },
    h('line', { x1: m - 20, x2: W - m + 20, y1: axisY, y2: axisY, stroke: C.t3, strokeWidth: 2 }),
    tickYears.map(y => h('g', { key: y }, h('line', { x1: sx(y), x2: sx(y), y1: axisY - 4, y2: axisY + 4, stroke: C.t3 }), h('text', { x: sx(y), y: axisY + 18, fontSize: 10, textAnchor: 'middle', fill: C.t4 }, y))),
    placed.map(({ x, lane, e }) => {
      const up = lane < 0
      const far = Math.abs(lane) === 2
      const len = far ? 118 : 58
      const ty = up ? axisY - len : axisY + len
      const isSel = selected === e.passage_id + e.label
      const bw = 164
      const bx = Math.max(4, Math.min(W - bw - 4, x - bw / 2))
      return h(
        'g',
        { key: e.passage_id + e.label, tabIndex: 0, role: 'button', 'aria-label': `${e.date_text}: ${e.label}. Show the quote`, onClick: () => onSelect(e.passage_id + e.label), onKeyDown: ev => (ev.key === 'Enter' || ev.key === ' ') && (ev.preventDefault(), onSelect(e.passage_id + e.label)), style: { cursor: 'pointer', outline: 'none' } },
        h('line', { x1: x, x2: x, y1: axisY, y2: ty, stroke: isSel ? C.accent : C.t4, strokeWidth: 1.2 }),
        h('circle', { cx: x, cy: axisY, r: isSel ? 7 : 5.5, fill: isSel ? C.accent : C.t2, stroke: C.t3 }),
        h('rect', { x: bx, y: up ? ty - 40 : ty, width: bw, height: 40, rx: 4, fill: isSel ? tint(C.accent, 16) : tint(C.text, 6), stroke: isSel ? C.accent : C.line }),
        h('text', { x: bx + 8, y: (up ? ty - 40 : ty) + 16, fontSize: 11, fontWeight: 700, fill: isSel ? C.accent : C.t2 }, e.date_text.length > 20 ? e.date_text.slice(0, 19) + '\u2026' : e.date_text),
        h('text', { x: bx + 8, y: (up ? ty - 40 : ty) + 31, fontSize: 11, fill: C.text }, e.label.length > 26 ? e.label.slice(0, 25) + '\u2026' : e.label),
        h('title', null, `${e.date_text}: ${e.label}`)
      )
    })
  )
}

function TimelineView({ courseId }) {
  const st = useAsync(() => api(`/courses/${encodeURIComponent(courseId)}/timeline`), [courseId])
  const [sel, setSel] = useState(null)
  return h(Async, { st, what: 'the timeline' }, data => {
    const chosen = data.events.find(e => e.passage_id + e.label === sel) || null
    return h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: 12 } },
      h('div', null, h('div', { style: S.h2 }, 'Timeline'), h('div', { style: S.muted }, 'Every date comes from a quote in your materials. Click an event to read it.')),
      h(TimelineChart, { events: data.events, selected: sel, onSelect: setSel }),
      chosen
        ? h('div', { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: 8 } }, h('div', { style: { ...S.row, flexWrap: 'wrap' } }, h('strong', null, chosen.label), h(Chip, null, chosen.date_text)), h(QuoteProof, { citation: { passage_id: chosen.passage_id, quote: chosen.quote, location: chosen.location, doc_id: chosen.doc_id } }))
        : h('div', { style: S.muted }, 'Select an event to see its quote and where it is in your materials.')
    )
  })
}

// ---------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------

// The first quoted span a drop reason names ('...'), if any. Whether it is in the source is for the find to say.
function dropQuote(detail) {
  const m = /(?:^|[\s(:])['\u2018]([^'\u2019]{5,}?)['\u2019](?=[\s,.:;)]|$)/.exec(detail || '')
  return m ? m[1].trim() : null
}

// Passage ids look like `history-md:a-dream-slowly-becomes-a-reality:5`. Students get a place name instead.
const PASSAGE_ID = /\b[a-z][a-z0-9_]*(?:-[a-z0-9_]+)*:([a-z][a-z0-9]*(?:-[a-z0-9]+)*):(\d+)\b/g
function humanDetail(detail) {
  return String(detail || '').replace(PASSAGE_ID, (_m, slug, n) => {
    const t = slug.replace(/-/g, ' ')
    return `\u201c${t.charAt(0).toUpperCase() + t.slice(1)}\u201d, paragraph ${n}`
  })
}

const dayName = iso => {
  const d = new Date(`${iso}T12:00:00`)
  return Number.isNaN(d.getTime()) ? iso.slice(5) : d.toLocaleDateString(undefined, { weekday: 'short' })
}

function StatCard({ label, value, sub, tone }) {
  return h(
    'div',
    { style: { ...S.card, padding: SP.lg, display: 'flex', flexDirection: 'column', gap: SP.xs }, 'data-testid': 'stat-card' },
    h('div', { style: S.muted }, label),
    h('div', { style: { fontSize: FS.big, lineHeight: '32px', fontWeight: 600, color: tone || C.text } }, value),
    h('div', { style: S.muted }, sub)
  )
}

function RecentDays({ days }) {
  const maxDay = Math.max(1, ...days.map(d => d.attempts))
  const H = 72
  return h(
    'div',
    { 'data-testid': 'recent-days', role: 'img', 'aria-label': 'Answers in the last 7 days', style: { display: 'flex', gap: SP.sm, alignItems: 'flex-end' } },
    days.map(d =>
      h(
        'div',
        { key: d.date, title: `${d.date}: ${d.correct} of ${d.attempts} correct`, style: { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: SP.xs, minWidth: 0 } },
        h(
          'div',
          { style: { width: '100%', maxWidth: 40, height: H, display: 'flex', alignItems: 'flex-end' } },
          h('div', { style: { width: '100%', height: d.attempts ? Math.max(6, (d.attempts / maxDay) * H) : 3, borderRadius: 4, background: d.attempts ? tint(C.accent, 35) : tint(C.text, 10), position: 'relative', overflow: 'hidden' } }, d.correct ? h('div', { style: { position: 'absolute', left: 0, right: 0, bottom: 0, height: `${Math.round((d.correct / d.attempts) * 100)}%`, background: C.green } }) : null)
        ),
        h('span', { style: S.cap }, dayName(d.date))
      )
    )
  )
}

function ProgressView({ courseId }) {
  const st = useAsync(() => Promise.all([api(`/courses/${encodeURIComponent(courseId)}/progress`), api(`/courses/${encodeURIComponent(courseId)}/drops`), api(`/courses/${encodeURIComponent(courseId)}/map`)]), [courseId])
  return h(Async, { st, what: 'progress' }, ([p, drops, map]) => {
    const units = map.units.map(u => {
      const os = u.lessons.flatMap(l => l.objectives).filter(o => o.coverage === 'covered')
      const gaps = u.lessons.flatMap(l => l.objectives).length - os.length
      return { id: u.id, title: u.title, mastery: os.length ? os.reduce((s, o) => s + o.mastery, 0) / os.length : 0, n: os.length, gaps }
    })
    const nextDue = p.next_due || CTX.storage.get('nextDue', null)
    const days = (p.recent_days || []).slice(-7)
    const hasAcc = p.accuracy !== null && p.accuracy !== undefined
    return h(
      Wrap,
      { gap: SP.xl },
      h(
        'div',
        { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: SP.md } },
        h(StatCard, { label: 'Course progress', value: pct(p.mean_mastery), sub: `${p.mastered} of ${p.covered} topics known` }),
        h(StatCard, { label: 'Due for review', value: String(p.due_reviews), sub: p.due_reviews ? (nextDue ? `next: ${fmtDue(nextDue)}` : 'ready now') : "You're caught up" }),
        h(StatCard, { label: 'Accuracy', value: hasAcc ? pct(p.accuracy) : '-', sub: p.attempts ? `over ${p.attempts} ${p.attempts === 1 ? 'answer' : 'answers'}` : 'No answers yet' }),
        h(StatCard, { label: 'Questions ready', value: String(p.items_active), sub: p.items_reported ? `${p.items_reported} reported by you` : 'Each one has a quote from your materials' })
      ),
      h(
        'div',
        { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))', gap: SP.xl, alignItems: 'start' } },
        h(
          'div',
          { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: SP.md } },
          h('h3', { style: S.h3 }, 'Progress by unit'),
          units.map(u => h('div', { key: u.id }, h('div', { style: { ...S.row, justifyContent: 'space-between', marginBottom: SP.xs } }, h('span', null, u.title), h('span', { style: S.muted }, u.n ? `${pct(u.mastery)}${u.gaps ? `, ${u.gaps} not in your materials` : ''}` : NOT_COVERED)), h(Bar, { value: u.mastery, tone: u.mastery >= 0.7 ? 'good' : u.mastery > 0 ? 'warn' : undefined, label: u.title })))
        ),
        h(
          'div',
          { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: SP.md } },
          h('h3', { style: S.h3 }, 'Last 7 days'),
          days.length ? h(RecentDays, { days }) : h('div', { style: S.muted }, 'Answer a few questions and your week will show up here.'),
          h('div', { style: S.cap }, 'Green is the share you got right.')
        )
      ),
      h(
        'details',
        { style: { ...S.card, padding: 0 }, 'data-testid': 'checks' },
        // a flex summary loses the browser's disclosure marker, so the chevron makes it read as clickable
        h('summary', { style: { ...S.row, padding: SP.lg, cursor: 'pointer', fontSize: FS.lead, fontWeight: 600, listStyle: 'none' } }, h(Codicon, { name: 'chevron-down' }), drops.total ? `How CodeCoach checks its questions \u00b7 ${drops.total} failed the fact-check` : 'How CodeCoach checks its questions'),
        h(
          'div',
          { style: { padding: `0 ${SP.lg}px ${SP.lg}px`, display: 'flex', flexDirection: 'column', gap: SP.md } },
          h('div', { style: S.muted }, 'CodeCoach drops a question when its source check fails. Nothing here ever reached you.'),
          drops.total ? h('div', { style: S.cap }, `${drops.total} questions failed the fact-check and were never shown`) : null,
          drops.by_gate.filter(g => g.count > 0).map(g => h('div', { key: g.gate_code, style: { ...S.row, alignItems: 'flex-start' } }, h('span', { style: { minWidth: 28, textAlign: 'right', fontWeight: 600, color: C.yellow } }, g.count), h('span', { style: { flex: 1 } }, g.label))),
          drops.recent.length ? h('h3', { style: { ...S.h3, marginTop: SP.sm } }, 'Latest') : null,
          drops.recent.map(d => {
            const g = drops.by_gate.find(x => x.gate_code === d.gate_code)
            return h('div', { key: d.id, style: { ...S.card, padding: SP.md } }, h('div', { style: { fontWeight: 600 } }, g ? g.label : d.gate_code), h('div', { style: S.muted }, `${formatLabel(d.format)} question - ${humanDetail(d.detail)}`), dropQuote(d.detail) ? h('div', { style: { marginTop: SP.xs } }, h(Button, { size: 'sm', variant: 'ghost', onClick: () => bus.emit('source', { query: dropQuote(d.detail) }), 'aria-label': `Find \"${dropQuote(d.detail)}\" in Sources`, 'data-testid': 'drop-find' }, h(Codicon, { name: 'search' }), 'Find the quote in Sources')) : null)
          }),
          h('div', { style: { ...S.row, marginTop: SP.xs } }, h(Chip, { tone: p.reports ? 'warn' : undefined }, `${p.reports} reported by you`), h('span', { style: S.muted }, 'Reported questions are removed from your bank.'))
        )
      )
    )
  })
}


// ---------------------------------------------------------------------------
// Page shell
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Read: the course's own text, in order, with an optional read-aloud
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Sources: the course's own files, shown as they are, beside whichever view is open.
// Long texts are cut into chunks at blank lines; a chunk is only turned into text nodes while it is
// within ~1400 px of the viewport. The rest are empty boxes with the height they last measured.
// ---------------------------------------------------------------------------

const LAZY_MARGIN = '1400px 0px'

// Which blocks are near the viewport. `bind(i)` is a stable ref per block; `force(i)` pins one on (jumps).
function useLazy(count, enabled) {
  const [vis, setVis] = useState(() => new Set([0, 1]))
  const box = useRef(null)
  if (!box.current) box.current = { io: null, els: new Map(), refs: new Map(), heights: new Map(), pinned: new Set(), f: 1 }
  const z = box.current
  useEffect(() => {
    if (!enabled || typeof IntersectionObserver === 'undefined') return undefined
    const o = new IntersectionObserver(
      entries => {
        setVis(prev => {
          const n = new Set(prev)
          let changed = false
          for (const e of entries) {
            const i = Number(e.target.dataset.b)
            if (e.isIntersecting) {
              if (!n.has(i)) (n.add(i), (changed = true))
            } else if (n.has(i) && !z.pinned.has(i)) (n.delete(i), (changed = true))
          }
          return changed ? n : prev
        })
      },
      { rootMargin: LAZY_MARGIN }
    )
    z.io = o
    z.els.forEach(el => o.observe(el))
    return () => {
      o.disconnect()
      z.io = null
    }
  }, [enabled, count, z])
  const bind = i => {
    if (!z.refs.has(i)) {
      z.refs.set(i, el => {
        if (el) {
          z.els.set(i, el)
          if (z.io) z.io.observe(el)
        } else {
          const old = z.els.get(i)
          if (old && z.io) z.io.unobserve(old)
          z.els.delete(i)
        }
      })
    }
    return z.refs.get(i)
  }
  const force = (...ids) => {
    ids.forEach(i => i >= 0 && i < count && z.pinned.add(i))
    setVis(prev => {
      const n = new Set(prev)
      ids.forEach(i => i >= 0 && i < count && n.add(i))
      return n
    })
  }
  return { vis, enabled, z, bind, force, release: () => z.pinned.clear(), forget: () => z.heights.clear() }
}

function LazyBlock({ i, lz, raw, render, style }) {
  const on = !lz.enabled || lz.vis.has(i)
  useLayoutEffect(() => {
    const el = lz.z.els.get(i)
    if (lz.enabled && on && el) {
      const hgt = el.getBoundingClientRect().height
      if (hgt > 0) {
        if (raw > 0 && !lz.z.heights.has(i)) lz.z.f = 0.7 * lz.z.f + 0.3 * Math.min(8, Math.max(0.1, hgt / raw))
        lz.z.heights.set(i, hgt)
      }
    }
  })
  const fixed = on ? undefined : lz.z.heights.get(i) || Math.round(raw * lz.z.f)
  return h('div', { ref: lz.bind(i), 'data-b': i, style: { ...style, height: fixed } }, on ? render() : null)
}

// Scroll the nearest scrolling ancestor so `getEl()` sits `frac` of the way down it, then keep nudging while
// neighbouring blocks render and move things; stops after two quiet checks.
function settleTo(getEl, frac, center) {
  let tries = 0
  let quiet = 0
  const tick = () => {
    tries += 1
    const el = getEl()
    if (el) {
      let sc = el.parentElement
      while (sc && !/(auto|scroll)/.test(getComputedStyle(sc).overflowY)) sc = sc.parentElement
      if (sc) {
        const a = el.getBoundingClientRect()
        const b = sc.getBoundingClientRect()
        const delta = center ? a.top + a.height / 2 - (b.top + b.height / 2) : a.top - (b.top + b.height * frac)
        if (Math.abs(delta) > 3) {
          sc.scrollTop += delta
          quiet = 0
        } else quiet += 1
      }
    }
    if (quiet < 2 && tries < 25) CTX.setTimeout(tick, tries < 3 ? 30 : 60)
  }
  tick()
}

function chunkText(text, target = 4000) {
  const out = []
  const n = text.length
  let s = 0
  while (s < n) {
    let e = Math.min(n, s + target)
    if (e < n) {
      const brk = text.indexOf('\n\n', e)
      if (brk >= 0 && brk - s <= target * 4) e = brk + 2
      else {
        const lim = Math.min(n, s + target * 2)
        let k = text.lastIndexOf('\n', lim)
        if (k <= s) k = text.lastIndexOf(' ', lim)
        e = k > s ? k + 1 : lim
      }
    }
    out.push([s, e])
    s = e
  }
  return out
}

// Is each chunk entered inside a fenced code block? (markdown only)
function fenceStates(text, chunks) {
  const out = []
  let fence = false
  chunks.forEach(([a, b]) => {
    out.push(fence)
    let s = a
    while (s < b) {
      let e = text.indexOf('\n', s)
      e = e < 0 || e >= b ? b : e + 1
      if (/^ {0,3}(```|~~~)/.test(text.slice(s, Math.min(e, s + 8)))) fence = !fence
      s = e
    }
  })
  return out
}

function mdLines(text, a, b, fence0) {
  const out = []
  let fence = fence0
  let s = a
  while (s < b) {
    let e = text.indexOf('\n', s)
    e = e < 0 || e >= b ? b : e + 1
    const line = text.slice(s, Math.min(e, s + 12))
    let st = 'plain'
    if (/^ {0,3}(```|~~~)/.test(line)) {
      st = 'code'
      fence = !fence
    } else if (fence) st = 'code'
    else if (/^ {0,3}#{1,6}\s/.test(line)) st = 'head'
    let marks = null
    if (st === 'head') {
      // The `## ` marker stays in the text (offsets never move); it is only hidden when drawn.
      const m = /^ {0,3}(#{1,6})[ \t]+/.exec(text.slice(s, Math.min(e, s + 20)))
      if (m) {
        st = `h${Math.min(3, m[1].length)}`
        marks = [[s, s + m[0].length, 'hide']]
      } else st = 'plain'
    } else if (st === 'plain') {
      const body = text.slice(s, e)
      const bl = /^( {0,6})([-*+]|\d{1,3}[.)])[ \t]+/.exec(body)
      if (bl) marks = [[s + bl[1].length, s + bl[1].length + bl[2].length, 'bullet']]
      const re = /\*\*([^*\n]+?)\*\*/g
      let bm
      while ((bm = re.exec(body))) {
        marks = marks || []
        marks.push([s + bm.index, s + bm.index + 2, 'hide'], [s + bm.index + 2, s + bm.index + bm[0].length - 2, 'bold'], [s + bm.index + bm[0].length - 2, s + bm.index + bm[0].length, 'hide'])
      }
    }
    out.push([s, e, st, marks])
    s = e
  }
  return out
}

const MD_STYLE = {
  head: { fontWeight: 700, fontSize: '1.12em' },
  h1: { fontWeight: 700, fontSize: '1.45em', lineHeight: 1.4 },
  h2: { fontWeight: 700, fontSize: '1.25em', lineHeight: 1.4 },
  h3: { fontWeight: 600, fontSize: '1.1em' },
  code: { fontFamily: MONO, fontSize: '0.92em' },
  plain: null
}
// Markdown markers are drawn invisible, not removed: the characters stay in the text, so every offset, highlight,
// find count and citation jump is exactly what it was.
const MD_MARK = { hide: { fontSize: 0, color: 'transparent' }, bold: { fontWeight: 700 }, bullet: { color: C.accent, fontWeight: 700 } }

// Turn text[a,b) into nodes. Nothing is dropped or reworded: styles and marks only wrap the same characters.
function renderSpan(text, a, b, lines, overlays, stage) {
  const pts = new Set([a, b])
  lines.forEach(([s, e, , marks]) => {
    pts.add(Math.max(a, s))
    pts.add(Math.min(b, e))
    if (marks) marks.forEach(([ms, me]) => (pts.add(Math.max(a, Math.min(b, ms))), pts.add(Math.max(a, Math.min(b, me)))))
  })
  overlays.forEach(o => (pts.add(Math.max(a, Math.min(b, o.s))), pts.add(Math.max(a, Math.min(b, o.e)))))
  const xs = [...pts].sort((p, q) => p - q)
  const pieces = []
  let li = 0
  for (let k = 0; k + 1 < xs.length; k++) {
    const p = xs[k]
    const q = xs[k + 1]
    if (q <= p) continue
    while (li + 1 < lines.length && lines[li][1] <= p) li++
    const st = lines[li][2]
    let mk = ''
    const lm = lines[li][3]
    if (lm) for (const [ms, me, kind] of lm) if (ms <= p && me >= q) mk = kind
    let hl = false
    let find = false
    let cur = false
    let first = false
    for (const o of overlays) {
      if (o.s <= p && o.e >= q) {
        if (o.k === 'hl') (hl = true, (first = o.s === p))
        else if (o.k === 'cur') cur = true
        else find = true
      }
    }
    const key = `${st}${mk ? ':' + mk : ''}|${hl ? 1 : 0}${find ? 1 : 0}${cur ? 1 : 0}`
    const prev = pieces[pieces.length - 1]
    if (prev && prev.key === key && !first) prev.e = q
    else pieces.push({ key, s: p, e: q, st, mk, hl, find, cur, first })
  }
  return pieces.map(pc => {
    const t = text.slice(pc.s, pc.e)
    if (pc.key.endsWith('|000') && pc.st === 'plain' && !pc.mk) return t
    const style = { ...(MD_STYLE[pc.st] || {}), ...(pc.mk ? MD_MARK[pc.mk] : {}) }
    const props = { key: pc.s }
    if (pc.hl) {
      const strong = stage === 'strong'
      style.background = tint(C.accent, strong ? 30 : 11)
      style.boxShadow = `-3px 0 0 ${strong ? C.accent : tint(C.accent, 45)}`
      props['data-hl'] = strong ? 'strong' : 'faint'
      if (pc.first) props['data-hl-first'] = '1'
    }
    if (pc.find) {
      style.background = tint(C.yellow, 34)
      style.borderRadius = 2
      props['data-find'] = '1'
    }
    if (pc.cur) {
      style.background = tint(C.yellow, 70)
      style.outline = `1px solid ${C.yellow}`
      props['data-cur'] = '1'
    }
    return h('span', { ...props, style }, t)
  })
}

function findAll(text, lower, q) {
  const out = []
  if (!q) return out
  if (lower && lower.length === text.length) {
    const ql = q.toLowerCase()
    let i = lower.indexOf(ql)
    while (i >= 0 && out.length < 300000) {
      out.push(i)
      i = lower.indexOf(ql, i + Math.max(1, ql.length))
    }
  } else {
    const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')
    for (const m of text.matchAll(re)) {
      out.push(m.index)
      if (out.length >= 300000) break
    }
  }
  return out
}

const fmtSize = n => (n >= 1048576 ? `${(n / 1048576).toFixed(2)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} chars`)
const lowerBound = (arr, v) => {
  let lo = 0
  let hi = arr.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (arr[mid] < v) lo = mid + 1
    else hi = mid
  }
  return lo
}

function SourceText({ doc, width, hl, stage, matches, qlen, cur, jump }) {
  const text = doc.text
  const isMd = doc.kind === 'markdown'
  const chunks = useMemo(() => chunkText(text), [text])
  const fences = useMemo(() => (isMd ? fenceStates(text, chunks) : null), [text, chunks, isMd])
  const lz = useLazy(chunks.length, true)
  const rootRef = useRef(null)
  const cpl = Math.max(20, Math.floor((width - 44) / 6.4))
  const rawFor = i => {
    const [a, b] = chunks[i]
    return Math.ceil((b - a) / cpl + (b - a) / 90) * 24 + 8
  }
  useEffect(() => {
    lz.forget()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [width])
  const chunkOf = off => {
    let lo = 0
    let hi = chunks.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (chunks[mid][0] <= off) lo = mid
      else hi = mid - 1
    }
    return lo
  }
  // A jump (citation or find) renders the target's chunk, then scrolls until it stays put.
  useEffect(() => {
    if (!jump) return
    const off = jump.k === 'hl' ? jump.off : matches[cur]
    if (off === undefined) return
    const ci = chunkOf(off)
    lz.force(ci - 1, ci, ci + 1)
    const sel = jump.k === 'hl' ? '[data-hl-first]' : '[data-cur="1"]'
    CTX.setTimeout(() => {
      settleTo(() => rootRef.current && rootRef.current.querySelector(sel), 0.3, false)
      CTX.setTimeout(() => lz.release(), 1500)
    }, 0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump, cur])
  return h(
    'div',
    { ref: rootRef, 'data-sources-chunks': chunks.length, style: { whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: '24px', fontSize: FS.body, paddingLeft: 6 } },
    chunks.map(([a, b], i) =>
      h(LazyBlock, {
        key: i,
        i,
        lz,
        raw: rawFor(i),
        render: () => {
          const overlays = []
          if (hl && hl.e > a && hl.s < b) overlays.push({ s: hl.s, e: hl.e, k: 'hl' })
          if (qlen) {
            for (let k = lowerBound(matches, a - qlen + 1); k < matches.length && matches[k] < b; k++) overlays.push({ s: matches[k], e: matches[k] + qlen, k: k === cur ? 'cur' : 'find' })
          }
          const lines = isMd ? mdLines(text, a, b, fences[i]) : [[a, b, 'plain']]
          return renderSpan(text, a, b, lines, overlays, stage)
        }
      })
    )
  )
}

// The source guide: this file's headings in order, the topics whose passages are in it, and the syllabus lines
// the materials do not cover. Assembled by the server from the file's own text and the course map; no model.
function SourceGuide({ courseId, docId, ready, onHeading, onPassage, onTopic }) {
  const key = `sources:guide:${courseId}`
  const [open, setOpen] = useState(() => !CTX.storage.get(key, false))
  const st = useAsync(() => (docId ? api(`/courses/${encodeURIComponent(courseId)}/sources/${encodeURIComponent(docId)}/guide`) : Promise.resolve(null)), [courseId, docId, ready])
  const g = st.data && st.data.doc_id === docId ? st.data : null
  const toggle = () => {
    setOpen(o => {
      CTX.storage.set(key, o)
      return !o
    })
  }
  const sec = { fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, color: C.t3, marginTop: 6 }
  const link = { all: 'unset', cursor: 'pointer', color: C.text, fontSize: 12, lineHeight: '18px', display: 'block' }
  return h(
    'div',
    { 'data-testid': 'source-guide', 'data-open': open ? '1' : '0', style: { borderBottom: `1px solid ${C.line}`, padding: '6px 12px' } },
    h(
      'button',
      { type: 'button', onClick: toggle, 'aria-expanded': open, 'aria-controls': 'codecoach-source-guide', 'data-testid': 'guide-toggle', style: { all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600, fontSize: 12.5, width: '100%' } },
      h(Codicon, { name: open ? 'chevron-down' : 'chevron-right' }),
      'Source guide'
    ),
    open
      ? h(
          'div',
          { id: 'codecoach-source-guide', style: { maxHeight: 220, overflowY: 'auto', paddingTop: 6, marginTop: 6, borderTop: `1px solid ${C.line}`, scrollPaddingTop: 8 } },
          st.error ? h('div', { style: S.muted }, `Could not build the guide: ${st.error}`) : null,
          !g && !st.error ? h('div', { style: S.muted }, 'Building the guide') : null,
          g
            ? [
                h('div', { key: 'ch', style: sec }, 'Contents'),
                g.headings.length
                  ? h('div', { key: 'cl', 'data-guide': 'contents' }, g.headings.map((x, i) => h('button', { key: i, type: 'button', onClick: () => onHeading(x), 'data-guide-heading': x.title, style: { ...link, paddingLeft: Math.max(0, x.level - 1) * 12, color: C.accent } }, x.title)))
                  : h('div', { key: 'cn', style: S.muted }, 'This file has no headings.'),
                h('div', { key: 'th', style: sec }, 'Topics in this file'),
                g.topics.length
                  ? h(
                      'div',
                      { key: 'tl', 'data-guide': 'topics' },
                      g.topics.map(t =>
                        h(
                          'div',
                          { key: t.objective_id, style: { ...S.row, gap: 6 } },
                          h('button', { type: 'button', onClick: () => onTopic(t.objective_id), 'data-guide-topic': t.objective_id, title: 'Open this topic', style: { ...link, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, t.statement),
                          h(Chip, { tone: t.mastery >= 0.85 ? 'good' : t.mastery >= 0.4 ? 'warn' : undefined }, pct(t.mastery)),
                          h(Button, { size: 'xs', variant: 'ghost', onClick: () => onPassage(t.passage_id), 'aria-label': `Show where "${t.statement}" is in this file`, title: 'Show in the text' }, h(Codicon, { name: 'go-to-file' }))
                        )
                      )
                    )
                  : h('div', { key: 'tn', style: S.muted }, 'No topic uses this file.'),
                g.not_covered.length
                  ? h(
                      'div',
                      { key: 'nc', 'data-guide': 'not-covered' },
                      h('div', { style: sec }, g.not_covered_label),
                      g.not_covered.map((s, i) => h('div', { key: i, style: { ...S.muted, lineHeight: '18px' } }, s))
                    )
                  : null
              ]
            : null
        )
      : null
  )
}

function SourcesPanel({ courseId, target, onClose, onTopic }) {
  const list = useAsync(() => api(`/courses/${encodeURIComponent(courseId)}/sources`), [courseId])
  const [pick, setPick] = useState(null)
  const files = list.data || []
  const docId = pick || (target && target.docId) || (files[0] && files[0].doc_id) || null
  const docSt = useAsync(() => (docId ? api(`/courses/${encodeURIComponent(courseId)}/sources/${encodeURIComponent(docId)}`) : Promise.resolve(null)), [courseId, docId])
  const doc = docSt.data && docSt.data.doc_id === docId ? docSt.data : null
  const [query, setQuery] = useState('')
  const [q, setQ] = useState('')
  const [cur, setCur] = useState(0)
  const [hl, setHl] = useState(null)
  const [stage, setStage] = useState('strong')
  const [jump, setJump] = useState(null)
  const [width, setWidth] = useState(480)
  const handled = useRef('')
  const boxRef = useRef(null)
  const timer = useRef(null)
  const debounce = useRef(null)
  const jumpN = useRef(0)

  useEffect(() => {
    if (target && target.docId) setPick(target.docId)
  }, [target])
  useEffect(() => {
    const el = boxRef.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(() => setWidth(Math.round(el.getBoundingClientRect().width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const lower = useMemo(() => (doc && q ? doc.text.toLowerCase() : null), [doc, q])
  const matches = useMemo(() => (doc && q ? findAll(doc.text, lower, q) : []), [doc, lower, q])

  // A citation (or a drop-log quote) opens its document, then lights its passage.
  useEffect(() => {
    if (!target || !doc) return
    if (target.docId && target.docId !== doc.doc_id) return
    const key = `${target.tick}:${doc.doc_id}`
    if (handled.current === key) return
    handled.current = key
    if (target.query) {
      setQuery(target.query)
      setQ(target.query)
      setCur(0)
      setHl(null)
      setJump({ k: 'cur', n: ++jumpN.current })
      return
    }
    const p = doc.passages.find(x => x.id === target.passageId)
    if (p && p.start !== null && p.start !== undefined) {
      let a = p.start
      let b = p.end
      // An Ask quote: light exactly its characters (offsets are into the passage), checked against the text.
      if (target.span && target.span.e > target.span.s) {
        const same = (x, y) => x.replace(/\s+/g, ' ').trim().toLowerCase() === y.replace(/\s+/g, ' ').trim().toLowerCase()
        const s0 = p.start + target.span.s
        const e0 = p.start + target.span.e
        if (e0 <= p.end && same(doc.text.slice(s0, e0), target.span.q || '')) {
          a = s0
          b = e0
        } else {
          const at = doc.text.slice(p.start, p.end).toLowerCase().indexOf((target.span.q || '').toLowerCase())
          if (at >= 0 && target.span.q) {
            a = p.start + at
            b = a + target.span.q.length
          }
        }
      }
      setHl({ s: a, e: b, id: p.id })
      setStage('strong')
      if (timer.current) timer.current()
      // A passage being read aloud stays lit until the next one takes over.
      if (!target.hold) timer.current = CTX.setTimeout(() => setStage('faint'), 2000)
      setJump({ k: 'hl', off: a, n: ++jumpN.current })
    }
  }, [target, doc])
  const jumpTo = (s, e) => {
    setQuery('')
    setQ('')
    setHl({ s, e, id: `g${s}` })
    setStage('strong')
    if (timer.current) timer.current()
    timer.current = CTX.setTimeout(() => setStage('faint'), 2000)
    setJump({ k: 'hl', off: s, n: ++jumpN.current })
  }
  const guideHeading = x => jumpTo(x.start, x.start + Math.max(1, x.title.length))
  const guidePassage = pid => {
    const p = doc && doc.passages.find(x => x.id === pid)
    if (p && p.start !== null && p.start !== undefined) jumpTo(p.start, p.end)
  }
  useEffect(
    () => () => {
      if (timer.current) timer.current()
    },
    []
  )

  const applyQuery = v => {
    setQ(v)
    setCur(0)
    setJump(v ? { k: 'cur', n: ++jumpN.current } : null)
  }
  const onQuery = v => {
    setQuery(v)
    if (debounce.current) debounce.current()
    debounce.current = CTX.setTimeout(() => applyQuery(v), 160)
  }
  const step = d => {
    if (!matches.length) return
    setCur(c => (c + d + matches.length) % matches.length)
    setJump({ k: 'cur', n: ++jumpN.current })
  }
  const total = matches.length
  const meta = files.find(f => f.doc_id === docId)

  return h(
    'aside',
    { id: 'codecoach-sources', role: 'complementary', 'aria-label': 'Sources', 'data-sources-panel': courseId, 'data-ready': doc ? '1' : '0', 'data-listening': target && target.hold ? target.passageId : '', style: { display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%', background: SURFACE } },
    h(
      'div',
      { style: { ...S.row, padding: '8px 12px', borderBottom: `1px solid ${C.line}` } },
      h('span', { style: { ...S.row, fontWeight: 600 } }, h(Codicon, { name: 'file-text' }), 'Sources'),
      h('div', { style: { flex: 1 } }),
      h(Button, { size: 'sm', variant: 'ghost', onClick: onClose, 'aria-label': 'Close Sources', title: 'Close (Esc)', 'data-testid': 'sources-close' }, h(Codicon, { name: 'close' }))
    ),
    h(SourceGuide, { courseId, docId, ready: !!doc, onHeading: guideHeading, onPassage: guidePassage, onTopic }),
    h(
      'div',
      { style: { padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 6, borderBottom: `1px solid ${C.line}` } },
      list.error ? h(Banner, { tone: 'bad' }, `Could not load the sources: ${list.error}`) : null,
      files.map(f =>
        h(
          'div',
          { key: f.doc_id, style: { display: 'flex', flexDirection: 'column', gap: 3 } },
          h(
          'button',
          {  type: 'button', onClick: () => setPick(f.doc_id), 'aria-pressed': f.doc_id === docId, 'data-doc': f.doc_id, style: { ...S.row, background: f.doc_id === docId ? tint(C.accent, 14) : 'transparent', border: `1px solid ${f.doc_id === docId ? tint(C.accent, 50) : C.line}`, borderRadius: 4, padding: '4px 8px', color: C.text, cursor: 'pointer', textAlign: 'left', fontSize: 12 } },
          h('span', { style: { flex: 1, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, f.name),
          h('span', { style: S.muted }, f.kind),
          h('span', { style: S.muted }, fmtSize(f.chars))
        )
                ,
          f.note ? h('div', { style: { ...S.muted, color: C.yellow }, 'data-source-note': f.doc_id }, f.note) : null
        )
      ),
      h(
        'div',
        { style: S.row },
        h('input', {
          type: 'search',
          value: query,
          placeholder: 'Find in this file',
          'aria-label': 'Find in source',
          'data-testid': 'sources-find',
          onChange: e => onQuery(e.target.value),
          onKeyDown: e => {
            if (e.key === 'Enter') {
              e.preventDefault()
              if (query !== q) applyQuery(query)
              else step(e.shiftKey ? -1 : 1)
            }
          },
          style: { ...S.field, flex: 1 }
        }),
        h('span', { style: { ...S.muted, minWidth: 74, textAlign: 'right' }, 'data-testid': 'sources-count', 'aria-live': 'polite' }, q ? (total ? `${cur + 1} of ${total.toLocaleString()}` : 'No matches') : ''),
        h(Button, { size: 'sm', variant: 'ghost', onClick: () => step(-1), disabled: !total, 'aria-label': 'Previous match' }, h(Codicon, { name: 'chevron-up' })),
        h(Button, { size: 'sm', variant: 'ghost', onClick: () => step(1), disabled: !total, 'aria-label': 'Next match' }, h(Codicon, { name: 'chevron-down' }))
      ),
      doc && doc.reconstructed ? h('div', { style: S.muted, 'data-testid': 'sources-rebuilt' }, 'Rebuilt from the imported paragraphs.') : null,
      h('div', { style: S.muted, 'data-testid': 'sources-integrity' }, 'Practice pauses while Sources is open, so answers stay hidden.')
    ),
    h(
      'div',
      { ref: boxRef, 'data-sources-text': docId || '', style: { flex: 1, minHeight: 0, overflowY: 'auto', padding: '10px 12px 24px' } },
      docSt.error ? h(Banner, { tone: 'bad' }, `Could not load this file: ${docSt.error}`) : null,
      !doc && !docSt.error ? h(Spinner, { text: 'Opening the file' }) : null,
      doc ? h(SourceText, { key: `${docId}:${doc.text.length}`, doc: { ...doc, kind: doc.kind || (meta && meta.kind) }, width, hl, stage, matches, qlen: q.length, cur, jump }) : null
    )
  )
}

// A location that opens the panel on its passage. Without a doc id (an older payload) it stays plain text.
function CiteLink({ cite, location, style }) {
  if (!cite || !cite.doc_id || !cite.passage_id) return h('span', { style }, location)
  return h(
    'button',
    {
      type: 'button',
      onClick: () => bus.emit('source', { docId: cite.doc_id, passageId: cite.passage_id }),
      'aria-label': `Open ${location} in Sources`,
      title: 'Open in Sources',
      'data-cite': cite.passage_id,
      // drop undefined entries: Read passed `color: undefined`, which erased the accent and left browser-black text on the dark theme
      style: { background: 'none', border: 'none', padding: 0, margin: 0, font: 'inherit', cursor: 'pointer', color: C.accent, textDecoration: 'underline', textUnderlineOffset: 2, ...Object.fromEntries(Object.entries(style || {}).filter(([, v]) => v !== undefined)) }
    },
    location
  )
}

const RATES = [0.75, 1, 1.25, 1.5, 2]
const AUDIO_KEEP = 3 // clips kept in memory around the current paragraph

// "para 1" alone confused readers: a one-paragraph section shows no number, others show "para 2 of 4".
function readLabel(p, count) {
  const re = /,?\s*para\.?\s*(\d+)\s*$/i
  const m = re.exec(p.location)
  if (!m) return p.location
  if (count === 1) return p.location.replace(re, '')
  return Number(m[1]) <= count ? `${p.location} of ${count}` : p.location
}

// "Listen to this topic": the topic's passages, spoken verbatim one after another through the same audio route
// and cache as Read aloud, with the same speed control and voice fallback. While it plays the passage being
// read is announced on the bus so an open Sources panel can light it.
function ListenBar({ courseId, passages, what }) {
  const [cur, setCur] = useState(null)
  const [playing, setPlaying] = useState(false)
  const [rate, setRate] = useState(() => CTX.storage.get('readRate', 1))
  const [note, setNote] = useState(null)
  const R = useRef({ audio: null, gen: 0, fallback: false, rate: 1, ps: [] })
  R.current.rate = rate
  R.current.ps = passages
  const docOf = p => p.doc_id || String(p.id).split(':')[0]

  const halt = () => {
    const R0 = R.current
    R0.gen += 1
    if (R0.audio) R0.audio.pause()
    if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel()
  }
  const finish = () => {
    R.current.started = false
    setPlaying(false)
    bus.emit('listening', { stop: true })
  }
  const advance = (i, gen) => {
    const R0 = R.current
    if (gen !== R0.gen) return
    if (i + 1 < R0.ps.length) playAt(i + 1)
    else finish()
  }
  const speak = async (i, gen) => {
    const R0 = R.current
    const p = R0.ps[i]
    if (typeof speechSynthesis === 'undefined' || typeof SpeechSynthesisUtterance === 'undefined') {
      setNote('No voice is available on this device to read aloud.')
      return finish()
    }
    let text = p.text
    if (!text) {
      try {
        text = (await api(`/passages/${encodeURIComponent(p.id)}`)).passage.text
      } catch (_e) {
        return finish()
      }
    }
    if (gen !== R0.gen) return
    const u = new SpeechSynthesisUtterance(text)
    u.rate = R0.rate
    u.onend = () => gen === R0.gen && advance(i, gen)
    u.onerror = () => gen === R0.gen && finish()
    speechSynthesis.cancel()
    speechSynthesis.speak(u)
  }
  const playAt = async i => {
    const R0 = R.current
    halt()
    const gen = R0.gen
    const p = R0.ps[i]
    if (!p) return
    setCur(i)
    setPlaying(true)
    R0.started = true
    bus.emit('listening', { docId: docOf(p), passageId: p.id })
    if (R0.fallback) return speak(i, gen)
    try {
      const r = await api(`/courses/${encodeURIComponent(courseId)}/passages/${encodeURIComponent(p.id)}/audio?encoding=base64`)
      if (gen !== R0.gen) return
      if (!R0.audio) R0.audio = new Audio()
      const a = R0.audio
      a.onended = () => advance(i, gen)
      a.src = `data:${r.mime};base64,${r.data_base64}`
      a.playbackRate = R0.rate
      await a.play()
    } catch (_e) {
      if (gen !== R0.gen) return
      R0.fallback = true
      setNote("Hermes could not read this aloud, so this device's own voice is reading instead.")
      speak(i, gen)
    }
  }
  const toggle = () => {
    if (playing) {
      halt()
      finish()
    } else if (passages.length) playAt(cur == null || cur >= passages.length ? 0 : cur)
  }
  const step = d => {
    const k = Math.max(0, Math.min(passages.length - 1, (cur == null ? (d > 0 ? -1 : passages.length) : cur) + d))
    if (playing) playAt(k)
    else setCur(k)
  }
  useEffect(() => {
    const R0 = R.current
    if (R0.audio) R0.audio.playbackRate = rate
    CTX.storage.set('readRate', rate)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rate])
  useEffect(
    () => () => {
      const R0 = R.current
      R0.gen += 1
      if (R0.audio) R0.audio.pause()
      if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel()
      if (R0.started) bus.emit('listening', { stop: true })
    },
    []
  )
  if (!passages.length) return null
  return h(
    'div',
    { style: { ...S.row, flexWrap: 'wrap' }, role: 'group', 'aria-label': `Listen to ${what}`, 'data-listen': playing ? 'playing' : 'idle', 'data-listen-at': cur == null ? '' : passages[cur] ? passages[cur].id : '' },
    h(Button, { size: 'sm', variant: 'outline', onClick: toggle, 'aria-label': playing ? 'Pause listening' : `Listen to ${what}`, 'data-testid': 'listen-toggle' }, h(Codicon, { name: playing ? 'debug-pause' : 'unmute' }), playing ? 'Pause' : cur == null ? 'Listen' : 'Resume'),
    passages.length > 1 ? h(Button, { size: 'sm', variant: 'ghost', onClick: () => step(-1), 'aria-label': 'Previous passage', title: 'Previous passage' }, h(Codicon, { name: 'chevron-up' })) : null,
    passages.length > 1 ? h(Button, { size: 'sm', variant: 'ghost', onClick: () => step(1), 'aria-label': 'Next passage', title: 'Next passage' }, h(Codicon, { name: 'chevron-down' })) : null,
    h('label', { style: { ...S.row, ...S.muted } }, 'Speed', h('select', { value: rate, 'aria-label': 'Listening speed', onChange: e => setRate(Number(e.target.value)), style: { ...S.field, width: 'auto' } }, RATES.map(r => h('option', { key: r, value: r }, `${r}x`)))),
    cur != null ? h('span', { style: S.muted, 'data-testid': 'listen-pos' }, `Passage ${cur + 1} of ${passages.length}`) : null,
    note ? h('span', { style: S.muted }, note) : null
  )
}

const READ_BLOCK = 16
const READ_LAZY_FROM = 150

function ReadView({ courseId, focus, note, onTopic }) {
  const st = useAsync(() => api(`/courses/${encodeURIComponent(courseId)}/materials`), [courseId])
  const sections = (st.data && st.data.sections) || []
  const flat = useMemo(() => sections.flatMap(s => s.passages), [st.data])
  const lazyOn = flat.length > READ_LAZY_FROM
  // Long books render in blocks as they near the viewport; short courses render whole, as before.
  const blocks = useMemo(() => {
    const out = []
    const of = new Map()
    sections.forEach(s => {
      for (let a = 0; a < s.passages.length; a += READ_BLOCK) {
        const ps = s.passages.slice(a, a + READ_BLOCK)
        ps.forEach(p => of.set(p.id, out.length))
        out.push({ i: out.length, sec: s, ps, raw: ps.reduce((t, p) => t + Math.ceil(p.text.length / 105) * 21 + 44, 0) })
      }
    })
    return { list: out, of }
  }, [st.data])
  const lz = useLazy(blocks.list.length, lazyOn)
  const [cur, setCur] = useState(null)
  const [playing, setPlaying] = useState(false)
  const [rate, setRate] = useState(() => CTX.storage.get('readRate', 1))
  const [flash, setFlash] = useState(null)
  const [voiceNote, setVoiceNote] = useState(null)
  const [prefetched, setPrefetched] = useState([])
  const R = useRef({ audio: null, clips: new Map(), gen: 0, fallback: false, rate: 1, flat: [] })
  const rootRef = useRef(null)
  R.current.rate = rate
  R.current.flat = flat

  const scrollTo = id => {
    const find = () => rootRef.current && rootRef.current.querySelector(`[data-passage="${CSS.escape(id)}"]`)
    if (lazyOn) {
      const b = blocks.of.get(id)
      if (b !== undefined) lz.force(b - 1, b, b + 1)
      CTX.setTimeout(() => {
        settleTo(find, 0.5, true)
        CTX.setTimeout(() => lz.release(), 1500)
      }, 0)
      return
    }
    const el = find()
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }
  const renderPassage = (p, s) => {
    const on = p.id === (cur != null && flat[cur] ? flat[cur].id : null)
    const lit = p.id === flash
    const hot = on || lit
    return h(
      'div',
      { key: p.id, 'data-passage': p.id, 'data-current': on ? '1' : '0', 'data-flash': lit ? '1' : '0', style: { borderLeft: `3px solid ${hot ? C.accent : C.line}`, paddingLeft: 12, paddingTop: 4, paddingBottom: 4, background: hot ? tint(C.accent, 12) : 'transparent', borderRadius: 3, transition: 'background 200ms' } },
      h('div', { style: { ...S.row, marginBottom: 4, flexWrap: 'wrap' } },
        h(CiteLink, { cite: { doc_id: p.doc_id, passage_id: p.id }, location: readLabel(p, s.passages.length), style: { fontSize: 12, fontWeight: 600, color: p.doc_id ? undefined : C.t2 } }),
        h(Button, { size: 'sm', variant: 'ghost', onClick: () => playAt(flat.findIndex(q => q.id === p.id)), 'aria-label': `Read aloud from ${p.location}`, title: 'Read aloud from here' }, h(Codicon, { name: 'play' })),
        p.objectives.map(t => h('button', { key: t.id, type: 'button', onClick: () => onTopic(t.id), title: `Open topic: ${t.statement}`, style: { all: 'unset', cursor: 'pointer' } }, h(Chip, { tone: 'accent' }, t.statement)))
      ),
      h('div', { style: { lineHeight: '21px', fontSize: 13.5, whiteSpace: p.display ? 'pre-line' : 'pre-wrap' } }, p.display || p.text)
    )
  }

  // "Read in context": scroll to the passage and light it for a moment.
  useEffect(() => {
    if (!focus || !st.data) return undefined
    setFlash(focus)
    const t1 = setTimeout(() => scrollTo(focus), 60)
    const t2 = setTimeout(() => setFlash(null), 3200)
    return () => (clearTimeout(t1), clearTimeout(t2))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, st.data])

  const clipFor = i => {
    const R0 = R.current
    const p = R0.flat[i]
    if (!p) return Promise.reject(new Error('no passage'))
    if (!R0.clips.has(p.id)) {
      const pr = api(`/courses/${encodeURIComponent(courseId)}/passages/${encodeURIComponent(p.id)}/audio?encoding=base64`).then(r => `data:${r.mime};base64,${r.data_base64}`)
      pr.catch(() => R0.clips.delete(p.id))
      R0.clips.set(p.id, pr)
      setPrefetched(l => (l.includes(p.id) ? l : [...l, p.id]))
      for (const [id] of R0.clips) {
        const k = R0.flat.findIndex(q => q.id === id)
        if (k >= 0 && (k < i - 1 || k > i + AUDIO_KEEP)) R0.clips.delete(id)
      }
    }
    return R0.clips.get(p.id)
  }

  const stopAll = () => {
    const R0 = R.current
    R0.gen += 1
    if (R0.audio) R0.audio.pause()
    if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel()
  }

  const speak = (i, gen) => {
    const R0 = R.current
    const p = R0.flat[i]
    if (typeof speechSynthesis === 'undefined' || typeof SpeechSynthesisUtterance === 'undefined') {
      setVoiceNote('No voice is available on this device to read aloud.')
      setPlaying(false)
      return
    }
    const u = new SpeechSynthesisUtterance(p.text)
    u.rate = R0.rate
    u.onend = () => gen === R0.gen && advance(i, gen)
    u.onerror = () => gen === R0.gen && setPlaying(false)
    speechSynthesis.cancel()
    speechSynthesis.speak(u)
  }

  const advance = (i, gen) => {
    const R0 = R.current
    if (gen !== R0.gen) return
    if (i + 1 < R0.flat.length) playAt(i + 1)
    else setPlaying(false)
  }

  const playAt = async i => {
    const R0 = R.current
    stopAll()
    const gen = R0.gen
    const p = R0.flat[i]
    if (!p) return
    setCur(i)
    setPlaying(true)
    scrollTo(p.id)
    if (R0.fallback) return speak(i, gen)
    try {
      const url = await clipFor(i)
      if (gen !== R0.gen) return
      if (!R0.audio) R0.audio = new Audio()
      const a = R0.audio
      a.onended = () => advance(i, gen)
      a.src = url
      a.playbackRate = R0.rate
      await a.play()
      if (gen !== R0.gen) return
      if (i + 1 < R0.flat.length) clipFor(i + 1).catch(() => {}) // next paragraph ready before this one ends
    } catch (e) {
      if (gen !== R0.gen) return
      R0.fallback = true
      setVoiceNote("Hermes could not read this aloud, so this device's own voice is reading instead.")
      speak(i, gen)
    }
  }

  const pause = () => {
    stopAll()
    setPlaying(false)
  }
  const toggle = () => {
    if (playing) return pause()
    if (flat.length) playAt(cur == null ? 0 : cur)
  }
  const step = d => {
    const base = cur == null ? (d > 0 ? -1 : flat.length) : cur
    const k = Math.max(0, Math.min(flat.length - 1, base + d))
    if (playing) playAt(k)
    else {
      setCur(k)
      scrollTo(flat[k].id)
    }
  }
  const toggleRef = useRef(null)
  toggleRef.current = toggle

  useEffect(() => {
    const onKey = e => {
      if (e.code !== 'Space' && e.key !== ' ') return
      const t = e.target
      const tag = t && t.tagName ? t.tagName.toLowerCase() : ''
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || (t && t.isContentEditable)) return
      e.preventDefault()
      toggleRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const R0 = R.current
    if (R0.audio) R0.audio.playbackRate = rate
    CTX.storage.set('readRate', rate)
    if (playing && R0.fallback && cur != null) playAt(cur)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rate])

  useEffect(
    () => () => {
      const R0 = R.current
      R0.gen += 1
      if (R0.audio) R0.audio.pause()
      if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel()
    },
    []
  )

  if (st.loading && !st.data) return h(Spinner, { text: 'Loading the materials' })
  if (st.error) return h(Banner, { tone: 'bad' }, `Could not load the materials: ${st.error}`)
  const d = st.data
  const outline = d.source === 'outline'
  if (!flat.length) return h(EmptyState, { title: 'Nothing to read yet', description: 'This course has no text.' })
  const curId = cur != null && flat[cur] ? flat[cur].id : null

  const controls = h(
    'div',
    { style: { ...S.row, flexWrap: 'wrap', position: 'sticky', top: -16, zIndex: 2, padding: '8px 0', background: SURFACE }, role: 'group', 'aria-label': 'Read aloud' },
    h(Button, { size: 'sm', variant: 'ghost', onClick: () => step(-1), 'aria-label': 'Previous paragraph', title: 'Previous paragraph' }, h(Codicon, { name: 'chevron-up' })),
    h(Button, { size: 'sm', onClick: toggle, 'aria-label': playing ? 'Pause' : 'Play', 'data-testid': 'read-toggle' }, h(Codicon, { name: playing ? 'debug-pause' : 'play' }), playing ? 'Pause' : cur == null ? 'Read aloud' : 'Resume'),
    h(Button, { size: 'sm', variant: 'ghost', onClick: () => step(1), 'aria-label': 'Next paragraph', title: 'Next paragraph' }, h(Codicon, { name: 'chevron-down' })),
    h('label', { style: { ...S.row, ...S.muted } }, 'Speed', h('select', { value: rate, 'aria-label': 'Reading speed', onChange: e => setRate(Number(e.target.value)), style: { ...S.field, width: 'auto' } }, RATES.map(r => h('option', { key: r, value: r }, `${r}x`)))),
    h('span', { style: S.muted }, 'Space plays or pauses')
  )

  return h(
    'div',
    { ref: rootRef, 'data-reading': curId || '', 'data-playing': playing ? '1' : '0', 'data-prefetched': prefetched.join('|'), style: { display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 820 } },
    h('div', { style: S.h2 }, d.title),
    note ? h(Banner, { tone: 'info' }, note) : null,
    outline ? h(Banner, { tone: 'warn' }, d.label) : null,
    voiceNote ? h(Banner, { tone: 'warn' }, voiceNote) : null,
    controls,
    sections.map(s =>
      h(
        'section',
        { key: s.id, style: { display: 'flex', flexDirection: 'column', gap: 8 } },
        h('h3', { style: { ...S.h3, marginTop: 10 } }, s.title),
        lazyOn
          ? blocks.list.filter(b => b.sec === s).map(b => h(LazyBlock, { key: b.i, i: b.i, lz, raw: b.raw, style: { display: 'flex', flexDirection: 'column', gap: 8 }, render: () => b.ps.map(p => renderPassage(p, s)) }))
          : s.passages.map(p => renderPassage(p, s))
      )
    )
  )
}

// ---------------------------------------------------------------------------
// Ask: chat over the learner's own materials. The server shows a sentence only if code found its quote word for
// word; this view makes that visible: numbered quote chips, the quote in a popover, a click that lights the exact
// characters in Sources, and the sentences that were thrown out.
// ---------------------------------------------------------------------------

const ASK_BADGE = 'Every sentence checked against a quote from your materials'
const ASK_CLOSEST = 'Closest passages (not an answer)'
const ASK_MAX = 300
const ASK_OFF = 'Ask only works on a course in strict mode, where every fact comes from your materials.'
// Mirrors codecoach/importer/build.py OUTLINE_MODE_REASON: the course was built from an outline with no study files.
const OUTLINE_REASON = 'no source materials attached'
const IMPORT_HINT = 'Import programming notes, a book chapter or docs in the Library. You can paste this outline as its syllabus.'
// Ask on a course it cannot search: say why and what to do, instead of a question box that can only fail.
function AskOff({ reason, onLibrary }) {
  const outline = reason === OUTLINE_REASON
  return h(
    'div',
    { style: { ...S.card, display: 'flex', gap: SP.md, alignItems: 'flex-start', padding: SP.xl }, 'data-ask-off': outline ? 'outline' : 'open' },
    h(Codicon, { name: 'library', style: { color: C.accent, fontSize: FS.title, marginTop: 2 } }),
    h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: SP.sm, minWidth: 0 } },
      h('div', { style: { fontSize: FS.title, lineHeight: '28px', fontWeight: 600 } }, outline ? 'Nothing to search yet' : 'Ask is off for this course'),
      h('div', { style: { color: C.t2, fontSize: FS.lead, lineHeight: '24px', maxWidth: MAX_READ } },
        outline
          ? 'This course is only an outline, so there are no study materials for Ask to search. Ask answers only from your own files, with an exact quote behind every answer.'
          : `${ASK_OFF} This course is in open mode${reason ? ` (${reason})` : ''}.`),
      outline ? h('div', { style: { color: C.t2, fontSize: FS.lead, lineHeight: '24px', maxWidth: MAX_READ } }, IMPORT_HINT) : null,
      onLibrary ? h('div', { style: { marginTop: SP.xs } }, h(Button, { onClick: onLibrary, 'data-testid': 'ask-go-library' }, 'Go to Library')) : null
    )
  )
}

// The server's reason for removing a sentence, in plain English.
function askWhy(r) {
  const why = String((r && r.why) || '')
  const list = s =>
    s
      .split(',')
      .map(x => x.trim())
      .filter(Boolean)
  const m = /^not in its quote:\s*(.+)$/i.exec(why)
  if (r && r.gate === 'ask_unsupported_fact' && m) {
    const xs = list(m[1])
    if (xs.length && xs.every(x => /^\d{4}$/.test(x))) return xs.length === 1 ? 'names a year that is not in the quote' : 'names years that are not in the quote'
    if (xs.length && xs.every(x => /^[\d.,$%]+$/.test(x))) return 'names a number that is not in the quote'
    return `names ${xs.slice(0, 3).join(', ')}, which is not in the quote`
  }
  const w = /^words its quote does not use:\s*(.+)$/i.exec(why)
  if (r && r.gate === 'ask_unsupported_fact' && w) return `uses words the quote does not contain (${list(w[1]).slice(0, 4).join(', ')})`
  if (r && r.gate === 'ask_no_cite') return 'has no quote to back it'
  if (r && r.gate === 'ask_too_long') return 'is too long to check against a quote'
  if (r && r.gate === 'ask_quote_missing') return 'its quote is not word for word in your materials'
  if (r && r.gate === 'ask_negation') return 'says the opposite of its quote, or drops a "not"'
  return 'is not backed by a quote in your materials'
}

const askStatusLabel = s => (s === 'answered' ? 'Answered' : s === 'not_covered' ? NOT_COVERED : s === 'error' ? 'Could not answer' : s === 'unavailable' ? 'Off' : String(s || ''))

// "3 min ago", "2 h ago", "4 d ago". Falls back to nothing when the stored time is not readable.
function askAgo(iso) {
  const t = Date.parse(iso || '')
  if (!t) return ''
  const m = Math.max(0, Math.round((Date.now() - t) / 60000))
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  if (m < 60 * 24) return `${Math.round(m / 60)} h ago`
  return `${Math.round(m / 1440)} d ago`
}

// A topic title for a question: drop a "Course: " prefix and a leading task verb ("Explain ..."), then cut at a
// clause or word boundary, about 50 characters at most. A title cut mid-clause would read as a broken question.
function askShort(s, max = 50) {
  let t = String(s || '').replace(/\s+/g, ' ').trim()
  if (t.includes(': ')) t = t.slice(t.lastIndexOf(': ') + 2)
  t = t.replace(/^(describe|explain|summari[sz]e|trace|identify|list|compare|analy[sz]e|discuss|outline|define|state|apply|use|understand)\s+/i, '').replace(/^what (?!happened)/i, '').replace(/[.?!\s]+$/, '')
  if (t.length <= max) return t
  let at = -1
  const re = /,| (?:and|when|on|at|made|who|why|after|for|that|which|during|because|reveals|shows|about) /g
  for (let m = re.exec(t); m; m = re.exec(t)) if (m.index >= 15 && m.index <= max) at = m.index
  if (at > 0) return t.slice(0, at)
  const cut = t.slice(0, max)
  const i = cut.lastIndexOf(' ')
  return (i > 15 ? cut.slice(0, i) : cut).replace(/[\s,;:.\-]+$/, '')
}

// Suggested questions from the course's own topic titles: the 3 lessons backed by the most passages. No model.
function askSuggestions(map) {
  const seen = new Set()
  const rows = []
  for (const u of (map && map.units) || []) {
    for (const l of u.lessons || []) {
      const ids = new Set((l.objectives || []).flatMap(o => o.passage_ids || []))
      const title = askShort(l.title)
      const key = title.toLowerCase()
      if (!ids.size || !title || seen.has(key)) continue
      seen.add(key)
      rows.push({ title, n: ids.size })
    }
  }
  rows.sort((a, b) => b.n - a.n)
  return rows.slice(0, 3).map(r => `What does the material say about ${r.title}?`)
}

const askKey = c => `${c.passage_id}:${c.quote_start}:${c.quote_end}`

// The inline number after a sentence. Quiet by default; hover shows the quote, a click jumps to its source card.
function AskChip({ n, cite, onOpen }) {
  const [open, setOpen] = useState(false)
  const [flip, setFlip] = useState(false)
  const box = useRef(null)
  useEffect(() => {
    if (open && box.current) setFlip(box.current.getBoundingClientRect().left + 380 > window.innerWidth - SP.xl)
  }, [open])
  const id = `codecoach-ask-pop-${n}-${cite.passage_id}`.replace(/[^a-zA-Z0-9_-]/g, '_')
  return h(
    'span',
    { ref: box, style: { position: 'relative', display: 'inline-block', marginLeft: SP.xs }, onMouseEnter: () => setOpen(true), onMouseLeave: () => setOpen(false) },
    h(
      'button',
      {
        type: 'button',
        className: 'ath-askchip',
        onClick: () => onOpen(cite, n),
        onFocus: () => setOpen(true),
        onBlur: () => setOpen(false),
        onKeyDown: e => e.key === 'Escape' && setOpen(false),
        'aria-describedby': open ? id : undefined,
        'aria-label': `Source ${n}, ${cite.location}. Show the quote below`,
        'data-ask-chip': String(n),
        'data-cite': cite.passage_id,
        style: { cursor: 'pointer', font: 'inherit', fontSize: FS.cap, lineHeight: '16px', minWidth: 20, padding: '0 6px', borderRadius: 999, color: C.t2, background: tint(C.text, 7), border: `1px solid ${tint(C.text, 12)}`, verticalAlign: 'baseline', position: 'relative', top: -1 }
      },
      String(n)
    ),
    open
      ? h(
          'span',
          { id, role: 'tooltip', 'data-ask-popover': String(n), style: { position: 'absolute', ...(flip ? { right: 0 } : { left: 0 }), top: '100%', marginTop: SP.xs, zIndex: 20, width: 380, maxWidth: '70vw', display: 'block', textAlign: 'left', padding: `${SP.sm}px ${SP.md}px`, borderRadius: 8, border: `1px solid ${C.line}`, background: ELEVATED, boxShadow: `0 6px 18px ${tint(C.text, 22)}`, fontSize: FS.cap, lineHeight: '18px', color: C.text } },
          h('span', { style: { display: 'block', color: C.t3, marginBottom: SP.xs }, 'data-ask-popover-loc': '1' }, `Quote ${n}, ${cite.location}${cite.extended_by_code ? ' (added by CodeCoach)' : ''}`),
          h('span', { style: { display: 'block', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', borderLeft: `3px solid ${C.accent}`, paddingLeft: SP.sm }, 'data-ask-popover-quote': '1' }, cite.quote)
        )
      : null
  )
}

// One source: number, the exact stored quote in a quote style, its place in the file, and a link to Sources.
function AskCard({ n, cite, flash, onOpen }) {
  return h(
    'li',
    { id: `codecoach-ask-card-${n}`, 'data-ask-card': String(n), 'data-flash': flash ? '1' : '0', style: { listStyle: 'none', display: 'flex', gap: SP.md, alignItems: 'flex-start', padding: SP.md, border: `1px solid ${flash ? C.accent : C.line}`, borderRadius: 10, background: flash ? tint(C.accent, 10) : tint(C.text, 3), transition: 'background 300ms, border-color 300ms', minWidth: 0 } },
    h('span', { style: { flex: 'none', minWidth: 22, height: 22, borderRadius: 999, background: tint(C.text, 8), color: C.t2, fontSize: FS.cap, lineHeight: '22px', textAlign: 'center', fontWeight: 600 } }, String(n)),
    h(
      'div',
      { style: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: SP.sm } },
      h('div', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', borderLeft: `3px solid ${C.accent}`, paddingLeft: SP.md, fontSize: FS.body, lineHeight: '22px', color: C.text }, 'data-ask-card-quote': String(n) }, cite.quote),
      h(
        'div',
        { style: { ...S.row, justifyContent: 'space-between', gap: SP.md, flexWrap: 'wrap' } },
        h('span', { style: S.cap, 'data-ask-card-loc': String(n) }, `${cite.location}${cite.extended_by_code ? ' (added by CodeCoach)' : ''}`),
        h('button', { type: 'button', onClick: () => onOpen(cite), 'data-ask-card-open': String(n), 'aria-label': `Open source ${n} in Sources`, style: { all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: SP.xs, fontSize: FS.cap, color: C.accent, borderRadius: 4 }, className: 'ath-asklink' }, 'Open in Sources', h(Codicon, { name: 'arrow-right' }))
      )
    )
  )
}

function AskNotes({ lines }) {
  if (!lines.length) return null
  return h(
    'div',
    { style: { display: 'flex', gap: SP.md, alignItems: 'flex-start', padding: `${SP.md}px ${SP.lg}px`, borderRadius: 10, background: tint(C.text, 5), color: C.t2, fontSize: FS.body, lineHeight: '20px' }, 'data-ask-notes': String(lines.length) },
    h(Codicon, { name: 'info', style: { color: C.t3, marginTop: 2 } }),
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.xs, minWidth: 0 } }, lines.map((l, i) => h('div', { key: i, ...l.attrs }, l.text)))
  )
}

function AskSkeleton() {
  const bar = (w, k) => h('div', { key: k, className: 'ath-skel', style: { width: w, height: 14, borderRadius: 6, background: tint(C.text, 8) } })
  return h('div', { 'aria-hidden': 'true', 'data-ask-skeleton': '1', style: { display: 'flex', flexDirection: 'column', gap: SP.md, maxWidth: MAX_READ } }, [bar('96%', 1), bar('88%', 2), bar('62%', 3)])
}

function AskAnswer({ result, onOpen, onTry }) {
  const [openRemoved, setOpenRemoved] = useState(false)
  const [flash, setFlash] = useState(null)
  useEffect(() => {
    if (!flash) return undefined
    return CTX.setTimeout(() => setFlash(null), 1800)
  }, [flash])
  const st = result.status
  if (st === 'unavailable') return h(Banner, { tone: 'info' }, result.reason || ASK_OFF)
  if (st === 'error')
    return h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.md }, 'data-ask-status': 'error' }, h(Banner, { tone: 'bad' }, result.message || 'CodeCoach could not answer that just now.'), h('div', null, h(Button, { size: 'sm', onClick: onTry }, 'Try again')))
  if (st === 'not_covered') {
    return h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: SP.lg }, 'data-ask-status': 'not_covered' },
      h(
        'div',
        { style: { ...S.card, display: 'flex', gap: SP.md, alignItems: 'flex-start', padding: SP.xl } },
        h(Codicon, { name: 'library', style: { color: C.accent, fontSize: FS.title, marginTop: 2 } }),
        h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.xs, minWidth: 0 } },
          h('div', { style: { fontSize: FS.title, lineHeight: '28px', fontWeight: 600 }, 'data-ask-not-covered': '1' }, "Your materials don't cover this"),
          h('div', { style: { color: C.t2, fontSize: FS.lead, lineHeight: '24px', maxWidth: MAX_READ } }, "CodeCoach won't guess. When the answer isn't in your files, it says so instead of making one up.")
        )
      ),
      (result.closest || []).length
        ? h(
            'div',
            { style: { display: 'flex', flexDirection: 'column', gap: SP.sm }, 'data-ask-closest': String(result.closest.length) },
            h('div', { style: S.h3 }, ASK_CLOSEST),
            h(
              'div',
              { style: { display: 'flex', flexDirection: 'column', gap: SP.sm } },
              result.closest.map(c =>
                h(
                  'button',
                  { key: c.passage_id, type: 'button', className: 'ath-row', onClick: () => onOpen({ doc_id: c.doc_id, passage_id: c.passage_id }), 'data-ask-closest-item': c.passage_id, 'aria-label': `Open ${c.location} in Sources`, style: { all: 'unset', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: SP.xs, boxSizing: 'border-box', width: '100%', padding: `${SP.sm}px ${SP.md}px`, border: `1px solid ${C.line}`, borderRadius: 10, background: tint(C.text, 3) } },
                  h('div', { style: { color: C.accent, fontSize: FS.cap, lineHeight: '16px' } }, c.location),
                  h('div', { style: { fontSize: FS.body, lineHeight: '20px', color: C.t2, overflowWrap: 'anywhere' } }, c.text.length > 220 ? `${c.text.slice(0, 220).replace(/\s+\S*$/, '')} ...` : c.text)
                )
              )
            )
          )
        : null
    )
  }
  // answered: every cite gets one number, reused when the same quote backs another sentence
  const nums = new Map()
  const cards = []
  const numOf = c => {
    const k = askKey(c)
    if (!nums.has(k)) {
      nums.set(k, nums.size + 1)
      cards.push({ n: nums.size, cite: c })
    }
    return nums.get(k)
  }
  const writing = result.kind === 'writing_help'
  const sentences = result.sentences || []
  const removed = result.removed || []
  const extended = sentences.some(s => (s.cites || []).some(c => c.extended_by_code))
  const body = sentences.map((s, i) => {
    const seen = new Set()
    const chips = (s.cites || []).map(c => {
      const n = numOf(c)
      if (seen.has(n)) return null
      seen.add(n)
      return writing ? null : h(AskChip, { key: n, n, cite: c, onOpen: (cite, num) => (setFlash(num), jump(num)) })
    })
    return { s, i, chips }
  })
  const jump = n => {
    const el = typeof document !== 'undefined' ? document.getElementById(`codecoach-ask-card-${n}`) : null
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }
  const noteLines = (result.notes || []).map((t, i) => ({ text: t, attrs: { 'data-ask-note': String(i + 1) } }))
  if (extended) noteLines.push({ text: 'CodeCoach added a quote from the same passage that contains a name, date or number.', attrs: { 'data-ask-extended': '1' } })
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.lg }, 'data-ask-status': 'answered', 'data-ask-kind': writing ? 'writing_help' : 'answer' },
    h(AskNotes, { lines: noteLines }),
    writing
      ? null
      : h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.md, maxWidth: MAX_READ } },
          body.map(({ s, i, chips }) => h('p', { key: i, 'data-ask-sentence': String(i + 1), style: { margin: 0, fontSize: FS.lead, lineHeight: '26px', overflowWrap: 'anywhere' } }, s.text, chips))
        ),
    writing ? null : h('div', { style: { ...S.row, color: C.t3, fontSize: FS.cap, lineHeight: '16px' }, 'data-ask-badge': '1' }, h(Codicon, { name: 'verified', style: { color: C.green } }), ASK_BADGE),
    cards.length
      ? h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.sm } },
          h('div', { style: S.h3 }, writing ? 'Quotes from your materials' : cards.length === 1 ? 'Source' : 'Sources'),
          h('ol', { style: { margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: SP.sm }, 'data-ask-sources': String(cards.length) }, cards.map(c => h(AskCard, { key: c.n, n: c.n, cite: c.cite, flash: flash === c.n, onOpen })))
        )
      : null,
    removed.length
      ? h(
          'div',
          { style: { border: `1px solid ${C.line}`, background: tint(C.text, 3), borderRadius: 10 }, 'data-ask-removed': String(removed.length), 'data-open': openRemoved ? '1' : '0' },
          h(
            'button',
            { type: 'button', onClick: () => setOpenRemoved(o => !o), 'aria-expanded': openRemoved, 'data-ask-removed-toggle': '1', style: { all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: SP.sm, padding: `${SP.sm}px ${SP.md}px`, width: '100%', boxSizing: 'border-box', fontSize: FS.body, color: C.t2 } },
            h(Codicon, { name: openRemoved ? 'chevron-down' : 'chevron-right' }),
            `CodeCoach removed ${removed.length} sentence${removed.length === 1 ? '' : 's'} it couldn't match to your materials`
          ),
          openRemoved
            ? h(
                'div',
                { style: { padding: `0 ${SP.md}px ${SP.md}px ${SP.xl + SP.sm}px`, display: 'flex', flexDirection: 'column', gap: SP.md } },
                removed.map((r, i) =>
                  h('div', { key: i, 'data-ask-removed-item': String(i + 1), style: { display: 'flex', flexDirection: 'column', gap: SP.xs } }, h('div', { style: { textDecoration: 'line-through', color: C.t2, fontSize: FS.body, lineHeight: '20px', overflowWrap: 'anywhere' }, 'data-ask-struck': '1' }, r.text), h('div', { style: S.cap }, `Removed because it ${askWhy(r)}.`))
                )
              )
            : null
        )
      : null
  )
}

function AskView({ courseId, strict, reason, prefill, onLibrary }) {
  const [text, setText] = useState(prefill || '')
  const [job, setJob] = useState(null) // { id, question, t0 }
  const [stage, setStage] = useState('Finding passages')
  const [secs, setSecs] = useState(0)
  const [shown, setShown] = useState(null) // { question, result }
  const [released, setReleased] = useState(null)
  const [fail, setFail] = useState(null)
  const hist = useAsync(() => api(`/courses/${encodeURIComponent(courseId)}/asks`), [courseId])
  const mapSt = useAsync(() => api(`/courses/${encodeURIComponent(courseId)}/map`), [courseId])
  const asks = (hist.data && hist.data.asks) || []
  const busy = !!job
  const suggestions = useMemo(() => askSuggestions(mapSt.data), [mapSt.data])

  useEffect(() => {
    if (!job) return undefined
    let alive = true
    let cancel = null
    const tick = async () => {
      if (!alive) return
      setSecs(Math.floor((Date.now() - job.t0) / 1000))
      try {
        const r = await api(`/courses/${encodeURIComponent(courseId)}/ask/${encodeURIComponent(job.id)}`)
        if (!alive) return
        if (r.stage) setStage(r.stage)
        if (r.status === 'done' && r.result) {
          setShown({ question: job.question, result: r.result })
          setJob(null)
          hist.reload()
          return
        }
      } catch (e) {
        if (!alive) return
        setFail(errText(e))
        setJob(null)
        return
      }
      cancel = CTX.setTimeout(tick, 1000)
    }
    cancel = CTX.setTimeout(tick, 400)
    return () => {
      alive = false
      if (cancel) cancel()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job])

  const send = async q0 => {
    const q = (q0 === undefined ? text : q0).trim()
    if (!q || q.length > ASK_MAX || busy) return
    setFail(null)
    setReleased(null)
    setShown(null)
    try {
      const r = await api(`/courses/${encodeURIComponent(courseId)}/ask`, { method: 'POST', body: { question: q, open_item_id: openQuestion.id || undefined } })
      if (r.released_item && r.released_item.released) {
        openQuestion.id = null
        setReleased(r.released_item.message)
      }
      if (r.status === 'unavailable') {
        setShown({ question: q, result: { status: 'unavailable', reason: r.reason, sentences: [], removed: [], closest: [] } })
        return
      }
      setStage('Finding passages')
      setSecs(0)
      setJob({ id: r.job_id, question: q, t0: Date.now() })
      setText('')
    } catch (e) {
      setFail(errText(e))
    }
  }
  const open = c => bus.emit('source', { docId: c.doc_id, passageId: c.passage_id, span: c.quote_start === undefined ? undefined : { s: c.quote_start, e: c.quote_end, q: c.quote } })
  const off = !strict
  const tooLong = text.length > ASK_MAX
  const ask = q => {
    setText(q)
    send(q)
  }

  const heading = h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.xs } },
    h('h1', { style: { fontSize: FS.big, lineHeight: '32px', fontWeight: 600, margin: 0 } }, 'Ask your materials'),
    h('div', { style: { ...S.muted, fontSize: FS.lead, lineHeight: '24px' } }, 'Answers come only from your materials, each backed by a word-for-word quote.')
  )
  // No question box when Ask cannot search this course: it could only fail.
  if (off) return h(Wrap, { max: 820, gap: SP.xl, 'data-ask-view': courseId }, heading, h(AskOff, { reason, onLibrary }))
  return h(
    Wrap,
    { max: 820, gap: SP.xl, 'data-ask-view': courseId },
    heading,
    h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: SP.sm } },
      h(
        'div',
        { className: 'ath-askbox', style: { display: 'flex', alignItems: 'flex-end', gap: SP.md, padding: `${SP.md}px ${SP.md}px ${SP.md}px ${SP.lg}px`, border: `1px solid ${C.line}`, borderRadius: 16, background: tint(C.text, 4) } },
        h('textarea', {
          value: text,
          rows: 2,
          maxLength: ASK_MAX,
          disabled: off,
          placeholder: 'Ask a question about your materials',
          'aria-label': 'Your question',
          'data-testid': 'ask-input',
          onChange: e => setText(e.target.value),
          onKeyDown: e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          },
          style: { flex: 1, minWidth: 0, boxSizing: 'border-box', resize: 'none', border: 'none', outline: 'none', background: 'transparent', color: C.text, fontFamily: 'inherit', fontSize: FS.lead, lineHeight: '24px', padding: `${SP.xs}px 0` }
        }),
        h(Button, { onClick: () => send(), disabled: off || busy || !text.trim() || tooLong, 'data-testid': 'ask-send' }, h(Codicon, { name: 'send' }), 'Ask')
      ),
      h('div', { style: { ...S.row, justifyContent: 'space-between', padding: `0 ${SP.xs}px` } }, h('span', { style: S.cap }, 'Enter to ask. Shift+Enter for a new line.'), text.length > ASK_MAX - 60 ? h('span', { style: { ...S.cap, color: text.length > ASK_MAX - 30 ? C.yellow : C.t3 } }, `${text.length} / ${ASK_MAX}`) : null)
    ),
    released ? h(Banner, { tone: 'info' }, h('span', { 'data-ask-released': '1' }, 'Your open Practice question went back in the queue ungraded, so this answer cannot give it away.')) : null,
    fail ? h(Banner, { tone: 'bad' }, fail) : null,
    busy
      ? h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.lg } },
          h('div', { style: { fontSize: FS.lead, lineHeight: '24px', fontWeight: 600, overflowWrap: 'anywhere' } }, job.question),
          h('div', { style: { ...S.row, color: C.t2 }, role: 'status', 'aria-live': 'polite', 'data-ask-running': stage }, h(Codicon, { name: 'loading', spinning: true }), h('span', null, stage), h('span', { style: S.cap, 'data-ask-elapsed': String(secs) }, `${secs} s`)),
          h(AskSkeleton)
        )
      : null,
    shown
      ? h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.lg } },
          h('div', { style: { fontSize: FS.lead, lineHeight: '24px', fontWeight: 600, overflowWrap: 'anywhere' }, 'data-ask-question': '1' }, shown.question),
          h(AskAnswer, { key: shown.question + shown.result.status, result: shown.result, onOpen: open, onTry: () => send(shown.question) })
        )
      : null,
    !shown && !busy && !off
      ? h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.md }, 'data-ask-empty': '1' },
          h('div', { style: { fontSize: FS.lead, lineHeight: '24px', color: C.t2 } }, 'Ask anything about your materials. Every sentence in the answer shows where it came from.'),
          suggestions.length ? h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: SP.sm }, 'data-ask-suggestions': String(suggestions.length) }, suggestions.map(q => h('button', { key: q, type: 'button', className: 'ath-askchip', onClick: () => ask(q), 'data-ask-suggestion': '1', style: { cursor: 'pointer', font: 'inherit', fontSize: FS.body, lineHeight: '20px', textAlign: 'left', color: C.text, background: tint(C.text, 4), border: `1px solid ${C.line}`, borderRadius: 999, padding: `${SP.sm}px ${SP.lg}px`, maxWidth: '100%', overflowWrap: 'anywhere' } }, q))) : null
        )
      : null,
    h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: SP.sm }, 'data-ask-history': String(asks.length) },
      h('div', { style: S.h3 }, 'Earlier questions'),
      hist.error ? h(Banner, { tone: 'bad' }, `Could not load earlier questions: ${hist.error}`) : null,
      !asks.length && !hist.error ? h('div', { style: S.muted }, hist.loading ? 'Loading' : 'No questions yet.') : null,
      asks.length
        ? h(
            'div',
            { style: { border: `1px solid ${C.line}`, borderRadius: 10, overflow: 'hidden' } },
            asks.map((a, i) =>
              h(
                'button',
                { key: a.job_id, type: 'button', className: 'ath-row', onClick: () => (setFail(null), setReleased(null), setShown({ question: a.question, result: a.result })), 'data-ask-history-item': a.job_id, style: { ...S.row, gap: SP.md, width: '100%', boxSizing: 'border-box', cursor: 'pointer', textAlign: 'left', color: C.text, background: 'transparent', border: 'none', borderTop: i ? `1px solid ${C.line}` : 'none', padding: `${SP.sm}px ${SP.md}px`, font: 'inherit', fontSize: FS.body } },
                h('span', { style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, a.question),
                h(Chip, { tone: a.status === 'answered' ? 'good' : a.status === 'not_covered' ? 'accent' : a.status === 'error' ? 'bad' : undefined }, askStatusLabel(a.status)),
                h('span', { style: { ...S.cap, flex: 'none', minWidth: 64, textAlign: 'right' } }, askAgo(a.asked_at))
              )
            )
          )
        : null
    )
  )
}

// ---------------------------------------------------------------------------
// Learn: explain, try it, fix it, next. Nothing here is scored; progress counts in Review.
// ---------------------------------------------------------------------------

const LEARN_POLL_MS = 2000
const LEARN_POLL_MAX_MS = 120000
const learnUrl = (cid, oid, tail) => `/courses/${encodeURIComponent(cid)}/learn${oid ? '/' + encodeURIComponent(oid) : ''}${tail || ''}`
const LEARN_UNAVAILABLE = "CodeCoach couldn't check an explanation for this part, so here is your material itself."
const LEARN_STATUS = { new: 'Not started', in_progress: 'In progress', learned: 'Learned', not_in_materials: NOT_COVERED }

// The learn overview, or null when the backend has no Learn yet (older install): callers then keep the old screens.
function useLearnOverview(courseId, dep) {
  const st = useAsync(() => api(learnUrl(courseId)).catch(() => null), [courseId, dep])
  return { ov: st.data || null, loading: st.loading && !st.data }
}

// Explanation sentences in the Ask look: reading size, numbered chips, source cards below. Same components as Ask.
// The source cards start folded: each explanation sentence is close to its quote, so an open list read like the
// same text twice. A chip opens the list and lights its card.
function ExplainSentences({ sentences, heading, cta }) {
  const [flash, setFlash] = useState(null)
  const [showSrc, setShowSrc] = useState(false)
  useEffect(() => {
    if (!flash) return undefined
    jump(flash)
    return CTX.setTimeout(() => setFlash(null), 1800)
  }, [flash])
  const nums = new Map()
  const cards = []
  const numOf = c => {
    const k = askKey(c)
    if (!nums.has(k)) {
      nums.set(k, nums.size + 1)
      cards.push({ n: nums.size, cite: c })
    }
    return nums.get(k)
  }
  const open = c => bus.emit('source', { docId: c.doc_id, passageId: c.passage_id, span: c.quote_start === undefined ? undefined : { s: c.quote_start, e: c.quote_end, q: c.quote } })
  const jump = n => {
    const el = typeof document !== 'undefined' ? document.getElementById(`codecoach-ask-card-${n}`) : null
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }
  const body = sentences.map((s, i) => {
    const seen = new Set()
    const chips = (s.cites || []).map(c => {
      const n = numOf(c)
      if (seen.has(n)) return null
      seen.add(n)
      return h(AskChip, { key: n, n, cite: c, onOpen: (_cite, num) => (setShowSrc(true), setFlash(num)) })
    })
    return h('p', { key: i, 'data-learn-sentence': String(i + 1), style: { margin: 0, fontSize: FS.lead, lineHeight: '28px', overflowWrap: 'anywhere' } }, s.text, chips)
  })
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.lg } },
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.md, maxWidth: MAX_READ } }, body),
    h('div', { style: { ...S.row, color: C.t3, fontSize: FS.cap, lineHeight: '16px' }, 'data-ask-badge': '1' }, h(Codicon, { name: 'verified', style: { color: C.green } }), ASK_BADGE),
    // The next step sits right under the sentences, above the source cards, so it is never off screen.
    cta || null,
    cards.length
      ? h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.sm } },
          h('button', { type: 'button', onClick: () => setShowSrc(v => !v), 'aria-expanded': showSrc, 'data-testid': 'learn-sources-toggle', style: { all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: SP.xs, color: C.t2, fontSize: FS.body, fontWeight: 600, borderRadius: 4, alignSelf: 'flex-start' } }, h(Codicon, { name: showSrc ? 'chevron-down' : 'chevron-right' }), `${heading || (cards.length === 1 ? 'Source' : 'Sources')} (${cards.length})`),
          showSrc ? h('ol', { style: { margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: SP.sm }, 'data-ask-sources': String(cards.length) }, cards.map(c => h(AskCard, { key: c.n, n: c.n, cite: c.cite, flash: flash === c.n, onOpen: open }))) : null
        )
      : null
  )
}

function StepDots({ total, at, done }) {
  return h(
    'span',
    { role: 'img', 'aria-label': `Step ${at + 1} of ${total}`, 'data-learn-dots': String(total), style: { display: 'inline-flex', gap: 6, alignItems: 'center' } },
    Array.from({ length: total }, (_, i) =>
      h('span', { key: i, 'data-dot': i === at ? 'now' : i < done ? 'done' : 'todo', style: { width: i === at ? 18 : 8, height: 8, borderRadius: 999, background: i === at ? C.accent : i < done ? tint(C.accent, 55) : tint(C.text, 16), transition: 'width 200ms' } })
    )
  )
}

function OriginalText({ passages, open, onToggle, always }) {
  const blocks = h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.md }, 'data-learn-original': '1' }, passages.map(p => h(PassageBlock, { key: p.passage_id, location: p.location, text: p.text, label: 'From your materials', cite: { doc_id: p.doc_id, passage_id: p.passage_id } })))
  if (always) return blocks
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.md } },
    h('button', { type: 'button', onClick: onToggle, 'aria-expanded': open, 'data-testid': 'learn-original-toggle', style: { all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: SP.xs, color: C.accent, fontSize: FS.body, borderRadius: 4, alignSelf: 'flex-start' } }, h(Codicon, { name: open ? 'chevron-down' : 'chevron-right' }), open ? 'Hide the original text' : 'Show the original text'),
    open ? blocks : null
  )
}

function ExplainCard({ courseId, step, title, onAsk, cta }) {
  const [orig, setOrig] = useState(false)
  const ex = step.explain
  const status = ex.status
  const listen = h(ListenBar, { key: `listen-${step.index}`, courseId, passages: step.passages.map(p => ({ id: p.passage_id, doc_id: p.doc_id, text: p.text })), what: 'this part' })
  return h(
    'div',
    { style: { ...S.card, padding: SP.xl, display: 'flex', flexDirection: 'column', gap: SP.lg }, 'data-learn-explain': status },
    status === 'ready'
      ? h(ExplainSentences, { sentences: ex.sentences, cta: h('div', { style: { ...S.row, gap: SP.md, flexWrap: 'wrap' } }, cta, listen, h(Button, { size: 'sm', variant: 'ghost', onClick: () => onAsk(title), 'data-testid': 'learn-ask' }, h(Codicon, { name: 'comment-discussion' }), 'Ask about this')) })
      : status === 'pending'
        ? h(
            'div',
            { style: { display: 'flex', flexDirection: 'column', gap: SP.md } },
            h('div', { role: 'status', 'aria-live': 'polite', style: { ...S.row, color: C.t2, fontSize: FS.lead }, 'data-learn-pending': '1' }, h(Codicon, { name: 'loading', spinning: true }), 'Writing a plain-words explanation\u2026'),
            h(AskSkeleton, null)
          )
        : h('div', { style: { color: C.t2, fontSize: FS.body, lineHeight: '20px' }, 'data-learn-unavailable': '1' }, LEARN_UNAVAILABLE),
    status === 'ready' ? null : h('div', { style: { ...S.row, gap: SP.md, flexWrap: 'wrap' } }, cta, listen, h(Button, { size: 'sm', variant: 'ghost', onClick: () => onAsk(title), 'data-testid': 'learn-ask' }, h(Codicon, { name: 'comment-discussion' }), 'Ask about this')),
    h(OriginalText, { passages: step.passages, open: orig || status !== 'ready', onToggle: () => setOrig(o => !o), always: status !== 'ready' })
  )
}

function LearnQuestion({ courseId, oid, item, onResult, onNext }) {
  const [resp, setResp] = useState(() => initialResponse(item))
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const ready = isReady(item, resp)
  const submit = async request => {
    if (busy) return
    setBusy(true)
    setErr(null)
    try {
      const r = await api(learnUrl(courseId, oid, '/check'), { method: 'POST', body: { item_id: item.id, response: request } })
      setRes(r)
      if (onResult) onResult(r)
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(false)
    }
  }
  const again = () => {
    setRes(null)
    setResp(initialResponse(item))
  }
  const wrong = res && res.correct === false
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.lg }, 'data-learn-question': item.id },
    h(
      'div',
      { style: { ...S.card, padding: SP.xl, display: 'flex', flexDirection: 'column', gap: SP.lg } },
      item.format === 'cloze' ? null : h('div', { style: { fontSize: FS.title, lineHeight: '30px', fontWeight: 500 }, 'data-testid': 'question-prompt' }, item.prompt),
      h(FormatInput, { item, value: resp, onChange: setResp, disabled: !!res || busy, onEnter: () => ready && submit(toRequest(item, resp)), result: res, objectiveId: item.objective_id, courseId, onRate: v => submit(v) }),
      !res && item.format !== 'explain' ? h('div', { style: { ...S.row, gap: SP.md } }, h(Button, { disabled: !ready || busy, onClick: () => submit(toRequest(item, resp)) }, 'Check answer')) : null,
      err ? h(Banner, { tone: 'bad' }, err) : null
    ),
    res
      ? h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.lg }, 'data-learn-feedback': wrong ? 'wrong' : 'right' },
          h(Verdict, { res, item, learn: true }),
          res.citations && res.citations.length
            ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.md } }, h('h3', { style: S.h3 }, 'Where this comes from'), res.citations.map((c, i) => h(QuoteProof, { key: `${c.passage_id}-${i}`, citation: wrong ? c : { ...c, key_spans: [] } })))
            : null,
          wrong
            ? h('div', { style: { ...S.row, gap: SP.md } }, h(Button, { onClick: again, 'data-testid': 'learn-try-again' }, 'Try again'), h(Button, { variant: 'ghost', onClick: onNext, 'data-testid': 'learn-next' }, 'Next'))
            : h('div', { style: S.row }, h(Button, { onClick: onNext, 'data-testid': 'learn-next' }, 'Next'))
        )
      : null
  )
}

function LearnFinish({ topic, nextOid, allDone, onNextTopic, onCourse, onReview }) {
  const summary = topic.summary || []
  // nextOid is undefined until the overview answers; the buttons wait for it so "Next topic" never points nowhere.
  const buttons = nextOid === undefined ? null : h('div', { style: { ...S.row, gap: SP.md, flexWrap: 'wrap' } }, allDone ? h(Button, { onClick: onReview, 'data-testid': 'learn-to-review' }, 'Go to Review') : h(Button, { onClick: () => onNextTopic(nextOid), 'data-testid': 'learn-next-topic' }, 'Next topic'), h(Button, { variant: allDone ? 'ghost' : 'secondary', onClick: onCourse, 'data-testid': 'learn-back-course' }, 'Back to course'))
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.lg }, 'data-learn-finish': '1' },
    h(
      'div',
      { style: { ...S.card, padding: SP.xl, display: 'flex', flexDirection: 'column', gap: SP.lg } },
      h('div', { style: { ...S.row, gap: SP.sm } }, h(Codicon, { name: 'pass-filled', style: { color: C.green, fontSize: FS.title } }), h('h1', { style: { margin: 0, fontSize: FS.title, lineHeight: '28px', fontWeight: 600 } }, 'What you learned')),
      h('div', { style: { ...S.row, gap: SP.sm, color: C.t2, fontSize: FS.body }, 'data-learn-added': '1' }, h(Codicon, { name: 'history', style: { color: C.t3 } }), "Added to your review: you'll see these questions again tomorrow."),
      summary.length ? h(ExplainSentences, { sentences: summary, cta: buttons }) : h('div', { style: { color: C.t2, fontSize: FS.lead, maxWidth: MAX_READ } }, "You've been through every part of this topic."),
      summary.length ? null : buttons
    ),
    allDone ? h('div', { style: { color: C.t2, fontSize: FS.lead }, 'data-learn-all-done': '1' }, "You've learned every topic. Review keeps them fresh.") : null
  )
}

function LearnTopic({ courseId, oid, onNextTopic, onCourse, onAsk, onReview, onChanged }) {
  const [topic, setTopic] = useState(null)
  const [err, setErr] = useState(null)
  const [gaveUp, setGaveUp] = useState(false)
  const [idx, setIdx] = useState(null)
  const [phase, setPhase] = useState('explain') // explain | try
  const [showAgain, setShowAgain] = useState(false)
  const [finished, setFinished] = useState(false)
  const [nextOid, setNextOid] = useState(undefined) // undefined: not known yet; null: nothing left to learn
  const [busy, setBusy] = useState(false)
  const t0 = useRef(Date.now())
  const tickRef = useRef(0)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let alive = true
    let cancel = null
    const go = async () => {
      try {
        const t = await api(learnUrl(courseId, oid))
        if (!alive) return
        setTopic(t)
        setErr(null)
        setIdx(i => (i === null ? Math.min(t.steps_done || 0, Math.max(0, t.steps.length - 1)) : i))
        if (t.status === 'learned' && tickRef.current === 0) setFinished(true)
        tickRef.current += 1
        const waiting = t.steps.some(s => s.explain.status === 'pending')
        if (waiting && Date.now() - t0.current < LEARN_POLL_MAX_MS) cancel = CTX.setTimeout(go, LEARN_POLL_MS)
        else if (waiting) setGaveUp(true)
      } catch (e) {
        if (alive) setErr(errText(e))
      }
    }
    go()
    return () => {
      alive = false
      if (cancel) cancel()
    }
  }, [courseId, oid, reload])

  useEffect(() => {
    if (!finished) return undefined
    let alive = true
    api(learnUrl(courseId))
      .then(ov => {
        const left = ov.topics.find(x => x.status !== 'learned' && x.status !== 'not_in_materials' && x.objective_id !== oid)
        if (alive) setNextOid(left ? left.objective_id : null)
      })
      .catch(() => alive && setNextOid(null))
    return () => {
      alive = false
    }
  }, [finished, courseId, oid])

  if (err && !topic) return h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.md, maxWidth: 760, margin: '0 auto' } }, h(Banner, { tone: 'bad' }, `Could not open this topic: ${err}`), h('div', { style: S.row }, h(Button, { onClick: () => setReload(x => x + 1) }, 'Try again'), h(Button, { variant: 'ghost', onClick: onCourse }, 'Back to course')))
  if (!topic) return h(Spinner, { text: 'Opening your topic' })
  if (topic.status === 'not_in_materials' || !topic.steps.length) {
    return h('div', { style: { ...S.card, maxWidth: 760, margin: '0 auto', padding: SP.xl, display: 'flex', flexDirection: 'column', gap: SP.md } }, h('div', { style: { fontSize: FS.title, fontWeight: 600 } }, topic.title), h('div', { style: { color: C.t2 } }, topic.status === 'not_in_materials' ? "Your materials don't cover this topic, so CodeCoach won't teach it." : 'There is nothing to go through here yet.'), h('div', null, h(Button, { onClick: onCourse }, 'Back to course')))
  }

  // A step that was still pending after two minutes counts as unavailable.
  const steps = topic.steps.map(s => (gaveUp && s.explain.status === 'pending' ? { ...s, explain: { ...s.explain, status: 'unavailable' } } : s))
  const total = steps.length
  const at = Math.min(idx === null ? 0 : idx, total - 1)
  const step = steps[at]
  const item = step.check && step.check.item

  const leaveToFinish = async () => {
    try {
      const t = await api(learnUrl(courseId, oid))
      setTopic(t)
    } catch (_e) {
      /* The summary may be empty; the finish screen still works. */
    }
    setFinished(true)
    if (onChanged) onChanged()
  }
  const next = async () => {
    if (busy) return
    setBusy(true)
    try {
      const r = await api(learnUrl(courseId, oid, '/step'), { method: 'POST', body: { index: step.index } })
      if (at + 1 < total && r.status !== 'learned') {
        setIdx(at + 1)
        setPhase('explain')
        setShowAgain(false)
        if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo({ top: 0 })
      } else await leaveToFinish()
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(false)
    }
  }

  if (finished) return h('div', { style: { maxWidth: 820, margin: '0 auto', width: '100%' } }, h(LearnFinish, { topic, nextOid, allDone: nextOid === null, onNextTopic, onCourse, onReview }))

  const trying = phase === 'try' && item
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.lg, maxWidth: 820, margin: '0 auto', width: '100%' }, 'data-learn-topic': oid, 'data-learn-step': String(at + 1) },
    h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: SP.sm } },
      h('div', null, h(Button, { size: 'sm', variant: 'ghost', onClick: onCourse, 'data-testid': 'learn-back' }, h(Codicon, { name: 'arrow-left' }), 'Back to course')),
      h('h1', { style: { margin: 0, fontSize: FS.big, lineHeight: '32px', fontWeight: 600, overflowWrap: 'anywhere' }, 'data-testid': 'learn-title', 'data-full-title': topic.title }, topicTitle(topic.title)),
      h('div', { style: { ...S.row, gap: SP.md, color: C.t2, fontSize: FS.body } }, h('span', { 'data-testid': 'learn-step-label' }, `Step ${at + 1} of ${total}`), h(StepDots, { total, at, done: topic.steps_done || 0 }))
    ),
    trying && !showAgain
      ? h('button', { type: 'button', onClick: () => setShowAgain(true), 'data-testid': 'learn-show-again', style: { all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: SP.xs, color: C.accent, fontSize: FS.body, alignSelf: 'flex-start', borderRadius: 4 } }, h(Codicon, { name: 'chevron-right' }), 'Show the explanation again')
      : trying
        ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.sm } }, h('button', { type: 'button', onClick: () => setShowAgain(false), 'data-testid': 'learn-hide-again', style: { all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: SP.xs, color: C.accent, fontSize: FS.body, alignSelf: 'flex-start', borderRadius: 4 } }, h(Codicon, { name: 'chevron-down' }), 'Hide the explanation'), h(ExplainCard, { key: `again-${step.index}`, courseId, step, title: topic.title, onAsk }))
        : h(ExplainCard, { key: `step-${step.index}`, courseId, step, title: topic.title, onAsk, cta: item ? h(Button, { onClick: () => setPhase('try'), 'data-testid': 'learn-try' }, 'Got it, try a question') : h(Button, { disabled: busy, onClick: next, 'data-testid': 'learn-next' }, 'Next') }),
    trying ? h(LearnQuestion, { key: item.id, courseId, oid, item, onNext: next }) : null
  )
}

function LearnView({ courseId, objectiveId, token, onCourse, onAsk, onReview, onOpenTopic }) {
  const st = useAsync(() => api(learnUrl(courseId)), [courseId, token])
  return h(Async, { st, what: 'Learn' }, ov => {
    const wanted = objectiveId || ov.next_objective_id
    if (!ov.topics.length) return h(LearnNote, { title: 'No topics yet', text: 'Import some materials in the Library and CodeCoach will build the topics here.', onCourse })
    if (!wanted) return h(LearnNote, { title: "You've learned every topic.", text: 'Review keeps them fresh.', onCourse, onReview })
    const topic = h(LearnTopic, { key: `${courseId}:${wanted}`, courseId, oid: wanted, onCourse, onAsk, onReview, onNextTopic: id => onOpenTopic(id) })
    if (!ov.outline) return topic
    // An outline-only course has nothing to teach from: say so rather than present outline lines as a lesson.
    return h(
      'div',
      { style: { display: 'flex', flexDirection: 'column', gap: SP.lg } },
      h('div', { 'data-testid': 'learn-outline-note', style: { maxWidth: 820, margin: '0 auto', width: '100%' } },
        h(Banner, { tone: 'info' }, h('b', null, 'This course is only an outline. '), 'Learn teaches from your own study materials, and this course has none yet, so each step shows the outline\u2019s own words. ' + IMPORT_HINT)),
      topic
    )
  })
}

function LearnNote({ title, text, onCourse, onReview }) {
  return h(
    'div',
    { style: { ...S.card, maxWidth: 560, margin: '0 auto', padding: SP.xl, display: 'flex', flexDirection: 'column', gap: SP.sm, textAlign: 'center', alignItems: 'center' }, 'data-testid': 'learn-note' },
    h(Codicon, { name: 'pass-filled', style: { color: C.green, fontSize: 24 } }),
    h('div', { style: { fontSize: FS.title, fontWeight: 600 } }, title),
    h('div', { style: S.muted }, text),
    h('div', { style: { ...S.row, gap: SP.md, marginTop: SP.sm } }, onReview ? h(Button, { onClick: onReview }, 'Go to Review') : null, h(Button, { variant: onReview ? 'ghost' : 'secondary', onClick: onCourse }, 'Back to course'))
  )
}

// Review used to hand out a read-then-quiz lesson card for a topic nobody had taught yet. Now it points at Learn first;
// "Quiz me anyway" keeps the old path.
function LessonGate({ lesson, courseId, onStart, onRead, onLearn }) {
  const [quiz, setQuiz] = useState(false)
  const { ov, loading } = useLearnOverview(courseId, lesson.objective_id)
  const row = ov && ov.topics.find(t => t.objective_id === lesson.objective_id)
  const unlearned = row && (row.status === 'new' || row.status === 'in_progress')
  if (loading && !quiz) return h(Spinner, { text: 'Finding your next step' })
  if (quiz || !unlearned) return h(LessonCard, { lesson, courseId, onStart, onRead })
  return h(
    'div',
    { style: { ...S.card, maxWidth: 640, margin: '0 auto', padding: SP.xl, display: 'flex', flexDirection: 'column', gap: SP.md }, 'data-testid': 'not-learned-card' },
    h('div', { style: { fontSize: FS.title, lineHeight: '28px', fontWeight: 600 } }, "You haven't learned this topic yet"),
    h('div', { style: { color: C.text, fontSize: FS.lead, lineHeight: '24px', maxWidth: MAX_READ }, 'data-testid': 'not-learned-title', 'data-full-title': row.title }, topicTitle(row.title)),
    h('div', { style: { color: C.t2, fontSize: FS.lead, lineHeight: '24px', maxWidth: MAX_READ } }, 'Learn walks you through it one small step at a time. Review works best after that.'),
    h('div', { style: { ...S.row, gap: SP.md, flexWrap: 'wrap' } }, h(Button, { onClick: () => onLearn(lesson.objective_id), 'data-testid': 'learn-it' }, 'Learn it'), h(Button, { variant: 'ghost', onClick: () => setQuiz(true), 'data-testid': 'quiz-anyway' }, 'Quiz me anyway'))
  )
}

// ---------------------------------------------------------------------------
// Projects: name a small program, CodeCoach plans small steps, teaches each one, and you write the code.
// API: PROJECT-CONTRACT.md. Every run/check goes through the sandbox; the output shown is real output.
// ---------------------------------------------------------------------------

const PROJ_POLL_MS = 3000
const PROJ_WAIT = 'Planning your project. A local model can take a couple of minutes; you can leave this page and come back.'
const PROJ_NO_SANDBOX = 'CodeCoach runs your code in a sandbox, and the sandbox is not installed on this computer. Install bubblewrap (on Ubuntu: sudo apt install bubblewrap), restart CodeCoach, then come back. Nothing else is needed.'
const LANG_NAME = { c: 'C', python: 'Python' }
const LANG_EXT = { c: 'c', python: 'py' }

// The code the learner typed, kept per project and step, so it survives switching steps, views and reloads.
const draftKey = (pid, i) => `proj:code:${pid}:${i}`
const readDraft = (pid, i) => {
  const v = CTX.storage.get(draftKey(pid, i), '')
  return typeof v === 'string' ? v : ''
}

// The server's error code (for example "step_locked") from the host's "<status>: <json body>" error text.
function errCode(e) {
  const raw = e && e.message ? String(e.message) : ''
  const m = raw.match(/^\d{3}: (\{[\s\S]*\})$/)
  if (!m) return ''
  try {
    return String(JSON.parse(m[1]).code || '')
  } catch (_e) {
    return ''
  }
}

const slugOf = s => String(s || 'program').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'program'

// A client-side file download: nothing leaves the machine.
function downloadText(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  CTX.setTimeout(() => URL.revokeObjectURL(url), 2000)
}

const STEP_ICON = {
  passed: ['pass-filled', 'green'],
  ready: ['play', 'accent'],
  building: ['loading', 't3'],
  unavailable: ['warning', 'yellow'],
  locked: ['lock', 't4'],
  planned: ['circle-outline', 't4']
}

function ProjectsView({ openId, setOpenId }) {
  const [seed, setSeed] = useState(null) // an idea to put in the box when coming back from a refused project
  return openId
    ? h(ProjectScreen, { key: openId, pid: openId, onHome: () => setOpenId(null), onAnother: (idea, lang) => (setSeed(idea ? { idea, lang } : null), setOpenId(null)), onOpen: setOpenId })
    : h(ProjectHome, { seed, onOpen: setOpenId })
}

function ProjectHome({ seed, onOpen }) {
  const st = useAsync(() => api('/projects'), [])
  const [idea, setIdea] = useState(seed ? seed.idea : '')
  const [lang, setLang] = useState(seed && seed.lang ? seed.lang : 'c')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [del, setDel] = useState(null)
  const data = st.data
  const noSandbox = !!data && data.sandbox !== 'bwrap'
  const len = idea.trim().length
  const start = async () => {
    if (busy || len < 3 || len > 300 || noSandbox) return
    setBusy(true)
    setErr(null)
    try {
      const r = await api('/projects', { method: 'POST', body: { idea: idea.trim(), language: lang } })
      onOpen(r.project_id)
    } catch (e) {
      setErr(errText(e))
      if (errCode(e) === 'sandbox_unavailable') st.reload()
    } finally {
      setBusy(false)
    }
  }
  const remove = async id => {
    try {
      await api(`/projects/${encodeURIComponent(id)}?confirm=true`, { method: 'DELETE' })
    } catch (e) {
      setErr(errText(e))
    }
    setDel(null)
    st.reload()
  }
  const langBtn = l =>
    h(Button, { key: l, size: 'sm', variant: lang === l ? 'secondary' : 'ghost', role: 'radio', 'aria-checked': lang === l, onClick: () => setLang(l), 'data-testid': `project-lang-${l}` }, LANG_NAME[l])
  const statusLabel = p => (p.status === 'planning' ? 'Planning' : p.status === 'refused' ? 'Not built' : p.status === 'failed' ? 'Could not plan' : p.status === 'done' ? 'Built' : `Step ${Math.min(p.steps_passed + 1, p.steps_total)} of ${p.steps_total}`)
  return h(
    Wrap,
    { max: 820, gap: SP.xl, 'data-testid': 'projects-home' },
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.xs } }, h('h2', { style: { ...S.h2, fontSize: FS.big, lineHeight: '32px' } }, 'Build something'), h('div', { style: { ...S.muted, maxWidth: MAX_READ } }, 'Name a small program. CodeCoach splits it into small steps, teaches each one, and you write the code yourself. Every step is checked by really running your code.')),
    noSandbox ? h(Banner, { tone: 'warn' }, PROJ_NO_SANDBOX) : null,
    st.error ? h(Banner, { tone: 'bad' }, `Could not load your projects: ${st.error}`) : null,
    h(
      'div',
      { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: SP.md } },
      h(Textarea, { value: idea, rows: 3, 'aria-label': 'Your idea', 'data-testid': 'project-idea', placeholder: 'For example: a number-guessing game', maxLength: 300, onChange: e => setIdea(e.target.value), style: { fontSize: FS.lead } }),
      h('div', { style: { ...S.row, gap: SP.md, flexWrap: 'wrap' } }, h('span', { style: S.muted }, 'Language'), h('div', { role: 'radiogroup', 'aria-label': 'Language', style: { ...S.row, gap: SP.xs } }, langBtn('c'), langBtn('python'))),
      data && data.ideas && data.ideas.length
        ? h(
            'div',
            { style: { display: 'flex', flexDirection: 'column', gap: SP.sm } },
            h('div', { style: S.cap }, 'Or start from an idea'),
            h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: SP.sm } }, data.ideas.map(i => h(Button, { key: i.title + i.language, size: 'sm', variant: 'outline', title: i.pitch, 'data-testid': 'project-idea-chip', onClick: () => (setIdea(i.title), setLang(i.language)) }, i.title, h('span', { style: { ...S.cap, marginLeft: SP.xs } }, LANG_NAME[i.language]))))
          )
        : null,
      err ? h(Banner, { tone: 'bad' }, err) : null,
      h('div', { style: S.row }, h(Button, { onClick: start, disabled: busy || len < 3 || noSandbox, 'data-testid': 'project-start' }, h(Codicon, { name: busy ? 'loading' : 'tools', spinning: busy }), busy ? 'Starting' : 'Start'), len > 300 ? h('span', { style: { ...S.muted, color: C.red } }, 'Keep the idea under 300 characters.') : null)
    ),
    st.loading && !data ? h(Spinner, { text: 'Loading your projects' }) : null,
    data && data.projects.length
      ? h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: SP.sm }, 'data-testid': 'project-list' },
          h('h3', { style: S.h3 }, 'Your projects'),
          data.projects.map(p =>
            h(
              'div',
              { key: p.id, className: 'ath-row', 'data-project-id': p.id, style: { ...S.row, border: `1px solid ${C.line}`, borderRadius: 8, padding: `${SP.sm}px ${SP.md}px`, gap: SP.md } },
              h(Button, { variant: 'ghost', onClick: () => onOpen(p.id), 'data-testid': 'project-open', style: { flex: 1, justifyContent: 'flex-start', textAlign: 'left', minWidth: 0 } }, h(Codicon, { name: p.status === 'done' ? 'pass-filled' : 'tools', style: { color: p.status === 'done' ? C.green : C.t2 } }), h('span', { style: { fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis' } }, p.title || 'Untitled project')),
              h(Chip, { tone: 'accent' }, LANG_NAME[p.language] || p.language),
              h('span', { style: { ...S.muted, minWidth: 90 }, 'data-testid': 'project-progress' }, statusLabel(p)),
              del === p.id
                ? h('span', { style: S.row }, h('span', { style: S.muted }, 'Delete it?'), h(Button, { size: 'sm', variant: 'outline', onClick: () => remove(p.id), 'data-testid': 'project-delete-yes' }, 'Delete'), h(Button, { size: 'sm', variant: 'ghost', onClick: () => setDel(null) }, 'Keep'))
                : h(Button, { size: 'sm', variant: 'ghost', 'aria-label': `Delete ${p.title}`, title: 'Delete this project', onClick: () => setDel(p.id), 'data-testid': 'project-delete' }, h(Codicon, { name: 'trash' }))
            )
          )
        )
      : null
  )
}

function ProjectScreen({ pid, onHome, onAnother, onOpen }) {
  const [proj, setProj] = useState(null)
  const [err, setErr] = useState(null)
  const [sel, setSel] = useState(null)
  const timer = useRef(null)
  const alive = useRef(true)
  const load = useCallback(() => {
    return api(`/projects/${encodeURIComponent(pid)}`).then(
      p => {
        if (!alive.current) return
        setProj(p)
        setErr(null)
        // The open step is chosen once (the current one); passing a step must not whisk the learner away from "Step done".
        if (p.steps && p.steps.length) setSel(s => (s === null ? Math.min(p.current, p.steps.length - 1) : s))
        return p
      },
      e => {
        if (alive.current) setErr(errText(e))
      }
    )
  }, [pid])
  useEffect(() => {
    alive.current = true
    load()
    return () => {
      alive.current = false
      if (timer.current) timer.current()
    }
  }, [load])
  // Poll while the plan or any step is still being built. The server does the work; this only looks.
  const busyBuilding = !!proj && (proj.status === 'planning' || proj.steps.some(s => s.status === 'building'))
  useEffect(() => {
    if (!busyBuilding) return undefined
    timer.current = CTX.setTimeout(load, PROJ_POLL_MS)
    return () => timer.current && timer.current()
  }, [busyBuilding, proj, load])

  const back = h(Button, { size: 'sm', variant: 'ghost', onClick: onHome, 'data-testid': 'project-back' }, h(Codicon, { name: 'arrow-left' }), 'Your projects')
  if (err && !proj) return h(Wrap, { max: 820 }, back, h(Banner, { tone: 'bad' }, `Could not open this project: ${err}`), h('div', null, h(Button, { onClick: load }, 'Try again')))
  if (!proj) return h(Wrap, { max: 820 }, back, h(Spinner, { text: 'Opening your project' }))

  if (proj.status === 'planning')
    return h(Wrap, { max: 720, gap: SP.lg, 'data-testid': 'project-planning' }, back, h('h2', { style: { ...S.h2, fontSize: FS.title } }, proj.idea || proj.title), h('div', { style: { ...S.card, display: 'flex', gap: SP.md, alignItems: 'flex-start' }, role: 'status' }, h(Codicon, { name: 'loading', spinning: true }), h('div', { style: { maxWidth: MAX_READ } }, PROJ_WAIT)))

  if (proj.status === 'refused' || proj.status === 'failed') {
    const refused = proj.status === 'refused'
    return h(
      Wrap,
      { max: 720, gap: SP.lg, 'data-testid': `project-${proj.status}` },
      back,
      h('h2', { style: { ...S.h2, fontSize: FS.title } }, proj.idea || proj.title),
      h(Banner, { tone: refused ? 'warn' : 'bad' }, proj.reason || (refused ? 'CodeCoach cannot build that one as a small text program.' : 'CodeCoach could not plan this project.')),
      h(
        'div',
        null,
        refused
          ? h(Button, { onClick: () => onAnother(null), 'data-testid': 'project-another' }, 'Try another idea')
          : h(Button, { onClick: () => api('/projects', { method: 'POST', body: { idea: proj.idea, language: proj.language } }).then(r => onOpen(r.project_id), e => setErr(errText(e))), 'data-testid': 'project-again' }, 'Try again')
      ),
      err ? h(Banner, { tone: 'bad' }, err) : null
    )
  }

  const open = sel === null ? Math.min(proj.current, proj.steps.length - 1) : sel
  const done = proj.status === 'done'
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', gap: SP.lg, maxWidth: MAX_WIDE, margin: '0 auto' }, 'data-testid': 'project-screen', 'data-project-status': proj.status },
    h('div', { style: { ...S.row, justifyContent: 'space-between', flexWrap: 'wrap' } }, back, h('span', { style: S.row }, h(Chip, { tone: 'accent' }, LANG_NAME[proj.language] || proj.language), h('span', { style: S.cap }, `${proj.steps.filter(s => s.status === 'passed').length} of ${proj.steps.length} steps done`))),
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.xs } }, h('h2', { style: { ...S.h2, fontSize: FS.title, lineHeight: '28px' }, 'data-testid': 'project-title' }, proj.title), proj.summary ? h('div', { style: { ...S.muted, maxWidth: MAX_READ } }, proj.summary) : null),
    done ? h(ProjectDone, { proj, pid }) : null,
    h(
      'div',
      { style: { display: 'grid', gridTemplateColumns: 'minmax(180px, 240px) minmax(0, 1fr)', gap: SP.xl, alignItems: 'start' } },
      h(
        'nav',
        { 'aria-label': 'Project steps', 'data-testid': 'project-rail', style: { display: 'flex', flexDirection: 'column', gap: SP.xs } },
        proj.steps.map(s => {
          const [icon, col] = STEP_ICON[s.status] || STEP_ICON.planned
          // 'planned' = in the plan, not built yet. At or before `current` it is the next step and opens (the engine
          // starts its build when it is asked for it); after `current` it is locked.
          const locked = s.status === 'locked' || (s.status === 'planned' && s.index > (proj.current || 0))
          return h(
            Button,
            { key: s.index, variant: open === s.index ? 'secondary' : 'ghost', disabled: locked, onClick: () => setSel(s.index), 'aria-current': open === s.index ? 'step' : undefined, 'data-testid': 'project-step-btn', 'data-step-index': s.index, 'data-step-status': s.status, title: s.goal, style: { justifyContent: 'flex-start', textAlign: 'left', height: 'auto', padding: `${SP.sm}px ${SP.md}px`, whiteSpace: 'normal' } },
            h(Codicon, { name: icon, spinning: s.status === 'building', style: { color: C[col], flex: 'none' } }),
            h('span', { style: { display: 'flex', flexDirection: 'column', alignItems: 'flex-start', minWidth: 0 } }, h('span', { style: { fontWeight: 600 } }, `${s.index + 1}. ${s.title}`), s.with_help ? h('span', { style: S.cap }, 'done with help') : h('span', { style: S.cap }, s.concept))
          )
        })
      ),
      h(StepPane, { key: `${pid}:${open}`, pid, proj, index: open, onChanged: load, onGoto: setSel })
    )
  )
}

function ProjectDone({ proj, pid }) {
  const concepts = []
  for (const s of proj.steps) if (s.concept && !concepts.includes(s.concept)) concepts.push(s.concept)
  const code = proj.code || readDraft(pid, proj.steps.length - 1)
  const helped = proj.steps.filter(s => s.with_help).length
  const ref = useRef(null)
  useEffect(() => {
    if (ref.current && ref.current.scrollIntoView) ref.current.scrollIntoView({ block: 'nearest' })
  }, [])
  return h(
    'div',
    { ref, style: { ...S.card, display: 'flex', flexDirection: 'column', gap: SP.md, borderColor: tint(C.green, 45), background: tint(C.green, 8) }, 'data-testid': 'project-done' },
    h('div', { style: { ...S.row, gap: SP.md } }, h(Codicon, { name: 'pass-filled', style: { color: C.green, fontSize: FS.big } }), h('h2', { style: { ...S.h2, fontSize: FS.title } }, 'You built it')),
    h('div', { style: { color: C.t2 } }, `Every step passed${helped ? `, ${helped} with CodeCoach's help` : ''}. The program is yours.`),
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.xs } }, h('div', { style: S.cap }, 'Concepts you used'), h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: SP.xs }, 'data-testid': 'project-concepts' }, concepts.map(c => h(Chip, { key: c }, c)))),
    h('div', null, h(Button, { onClick: () => downloadText(`${slugOf(proj.title)}.${LANG_EXT[proj.language] || 'txt'}`, code), 'data-testid': 'project-download' }, h(Codicon, { name: 'cloud-download' }), 'Download your program'))
  )
}

function StepPane({ pid, proj, index, onChanged, onGoto }) {
  const row = proj.steps[index]
  const rowStatus = row ? row.status : 'locked'
  const listed = rowStatus === 'planned' && index > (proj.current || 0) ? 'locked' : rowStatus
  const st = useAsync(() => (listed === 'building' || listed === 'locked' ? Promise.resolve(null) : api(`/projects/${encodeURIComponent(pid)}/steps/${index}`)), [pid, index, listed])
  const [code, setCode] = useState(() => readDraft(pid, index))
  const [res, setRes] = useState(null) // { kind: 'run' | 'check', data }
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState(null)
  const [hints, setHints] = useState(null)
  const [hintsLeft, setHintsLeft] = useState(null)
  const [passedNow, setPassedNow] = useState(null)
  const [solutionShown, setSolutionShown] = useState(false)
  const [dirty, setDirty] = useState(false)
  const codeRef = useRef(code)
  const taRef = useRef(null)
  const escaped = useRef(false)
  const step = st.data
  // Asking for a 'planned' step starts its build: keep asking (and refresh the project) until it is ready.
  const stepBuilding = !!step && (step.status === 'building' || step.status === 'planned')
  useEffect(() => {
    if (!stepBuilding) return undefined
    const t = setTimeout(() => { st.reload(); if (onChanged) onChanged() }, 3000)
    return () => clearTimeout(t)
  }, [stepBuilding, st.data])

  // Load starting_code only when the editor is empty: the learner's own code always wins.
  useEffect(() => {
    if (step && step.starting_code && !codeRef.current.trim()) {
      codeRef.current = step.starting_code
      setCode(step.starting_code)
      CTX.storage.set(draftKey(pid, index), step.starting_code)
    }
    if (step) {
      setHints(h0 => h0 || step.hints_shown || [])
      setHintsLeft(n => (n === null ? step.hints_left : n))
    }
  }, [step])

  const onChange = v => {
    codeRef.current = v
    setCode(v)
    setDirty(true)
    CTX.storage.set(draftKey(pid, index), v)
  }
  const call = async (verb, body) => {
    if (busy) return null
    setBusy(verb)
    setErr(null)
    try {
      return await api(`/projects/${encodeURIComponent(pid)}/steps/${index}/${verb}`, { method: 'POST', body })
    } catch (e) {
      setErr(errText(e))
      return null
    } finally {
      setBusy(null)
    }
  }
  const doRun = async () => {
    const sent = codeRef.current
    const r = await call('run', { code: sent })
    if (r) {
      setRes({ kind: 'run', data: r })
      setDirty(false)
    }
  }
  const doCheck = async () => {
    const r = await call('check', { code: codeRef.current })
    if (!r) return
    setRes({ kind: 'check', data: r })
    setDirty(false)
    if (r.step_passed) {
      setPassedNow({ next: r.next_index, done: !!r.project_done })
      onChanged()
    } else st.reload() // a failed check can unlock the solution
  }
  const doHint = async () => {
    const r = await call('hint')
    if (r) {
      setHints(l => [...(l || []), r.hint])
      setHintsLeft(r.hints_left)
      st.reload()
    }
  }
  const doSolution = async () => {
    const r = await call('solution')
    if (r && typeof r.code === 'string') {
      onChange(r.code)
      setSolutionShown(true)
      onChanged()
    }
  }
  const doRetry = async () => {
    const r = await call('retry')
    if (r) onChanged()
  }
  const onKeyDown = e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      doRun()
      return
    }
    if (e.key === 'Escape') {
      escaped.current = true
      return
    }
    if (e.key === 'Tab' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !escaped.current) {
      e.preventDefault()
      const ta = e.target
      const a = ta.selectionStart
      const b = ta.selectionEnd
      onChange(ta.value.slice(0, a) + '    ' + ta.value.slice(b))
      CTX.setTimeout(() => {
        if (taRef.current) taRef.current.selectionStart = taRef.current.selectionEnd = a + 4
      }, 0)
      return
    }
    escaped.current = false
  }

  const head = h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.xs } }, h('div', { style: S.cap }, `Step ${index + 1} of ${proj.steps.length}`), h('h2', { style: { ...S.h2, fontSize: FS.title, lineHeight: '28px' }, 'data-testid': 'project-step-title' }, row ? row.title : ''), row ? h('div', { style: { ...S.muted, maxWidth: MAX_READ } }, row.goal) : null)
  const pane = kids => h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.lg, minWidth: 0 }, 'data-testid': 'project-step', 'data-step-index': index, 'data-step-status': listed }, head, ...(Array.isArray(kids) ? kids : [kids]))

  if (listed === 'building' || stepBuilding)
    return pane(h('div', { style: { ...S.card, display: 'flex', gap: SP.md, alignItems: 'flex-start' }, role: 'status', 'data-testid': 'project-step-building' }, h(Codicon, { name: 'loading', spinning: true }), h('div', { style: { maxWidth: MAX_READ } }, 'Building this step. CodeCoach writes the lesson, runs the example and checks its own answer first. A local model can take a minute; this page updates by itself.')))
  if (listed === 'locked') return pane(h(Banner, { tone: 'warn' }, 'Finish the earlier steps first. This one opens when you get there.'))
  if (st.loading && !step) return pane(h(Spinner, { text: 'Opening the step' }))
  if (st.error) return pane(h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.sm } }, h(Banner, { tone: 'bad' }, `Could not open this step: ${st.error}`), h('div', null, h(Button, { onClick: st.reload }, 'Try again'))))
  if (!step) return pane(h(Spinner, { text: 'Opening the step' }))
  if (step.status === 'unavailable')
    return pane(
      h(
        'div',
        { style: { display: 'flex', flexDirection: 'column', gap: SP.md }, 'data-testid': 'project-step-unavailable' },
        h(Banner, { tone: 'warn' }, step.reason || 'CodeCoach could not build this step.'),
        err ? h(Banner, { tone: 'bad' }, err) : null,
        h('div', null, h(Button, { onClick: doRetry, disabled: !!busy, 'data-testid': 'project-retry' }, busy ? 'Trying again' : 'Try again'))
      )
    )

  const nextRow = proj.steps[index + 1]
  const isPassed = listed === 'passed' || !!passedNow
  const shownHints = hints || []
  const left = hintsLeft === null ? step.hints_left : hintsLeft
  const canSolution = !!step.can_show_solution
  const results = res ? (res.data.results || []) : null
  const names = results || step.visible_tests.map(t => ({ name: t.name, passed: null }))
  const sum = res ? `${res.data.passed} of ${res.data.total} passing` : 'not run yet'
  const block = (title, kids, extra) => h('section', { style: { display: 'flex', flexDirection: 'column', gap: SP.sm, ...extra } }, h('h3', { style: S.h3 }, title), kids)

  return pane([
    block(
      "Tutor's explanation",
      h('div', { style: { display: 'flex', flexDirection: 'column', gap: SP.sm, maxWidth: MAX_READ, fontSize: FS.lead, lineHeight: '26px' }, 'data-testid': 'project-lesson' }, step.lesson.map((l, k) => h('p', { key: k, style: { margin: 0 } }, l)))
    ),
    step.example
      ? block(
          "Ran in CodeCoach's sandbox",
          h(
            'div',
            { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: SP.sm }, 'data-testid': 'project-example' },
            h('pre', { style: mono(), 'data-testid': 'project-example-code' }, step.example.code),
            step.example.stdin ? h('div', null, h('div', { style: S.cap }, 'Input'), h('pre', { style: mono() }, step.example.stdin)) : null,
            h('div', null, h('div', { style: S.cap }, 'Output'), h('pre', { style: mono(), 'data-testid': 'project-example-output' }, shown(step.example.stdout)))
          )
        )
      : null,
    block('Your task', h('div', { style: { maxWidth: MAX_READ, fontSize: FS.lead, lineHeight: '26px' }, 'data-testid': 'project-task' }, step.task)),
    block(
      'Tests your code must pass',
      h(
        'div',
        { style: { display: 'flex', flexDirection: 'column', gap: SP.sm }, 'data-testid': 'project-tests' },
        step.visible_tests.map(t =>
          h(
            'div',
            { key: t.name, style: { ...S.card, padding: SP.md, display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto minmax(0,1fr)', gap: SP.md, alignItems: 'start' }, 'data-testid': 'project-visible-test' },
            h('div', null, h('div', { style: S.cap }, `${t.name}: input`), h('pre', { style: mono() }, t.stdin ? t.stdin : '(no input)')),
            h(Codicon, { name: 'arrow-right', style: { color: C.t3, marginTop: SP.xl } }),
            h('div', null, h('div', { style: S.cap }, 'expected output'), h('pre', { style: mono() }, shown(t.expected_stdout)))
          )
        ),
        step.hidden_count ? h('div', { style: S.muted }, `${step.hidden_count} more tests stay hidden until you press Check step.`) : null
      )
    ),
    block(
      'Your code',
      h(
        'div',
        { style: { display: 'flex', flexDirection: 'column', gap: SP.sm } },
        h(Textarea, { ref: taRef, value: code, rows: 14, spellCheck: false, 'aria-label': `${LANG_NAME[proj.language] || ''} code editor`, 'data-testid': 'project-editor', onChange: e => onChange(e.target.value), onKeyDown, wrap: 'off', style: { fontFamily: MONO, fontSize: 13, lineHeight: '19px', tabSize: 4, whiteSpace: 'pre', overflow: 'auto', resize: 'vertical', minHeight: 260 } }),
        h('div', { style: S.muted }, 'Press Ctrl+Enter to run. Tab inserts spaces; press Esc then Tab to move focus out of the editor.'),
        h(
          'div',
          { style: { ...S.row, flexWrap: 'wrap', gap: SP.sm } },
          h(Button, { variant: 'outline', onClick: doRun, disabled: !!busy, 'data-testid': 'project-run' }, h(Codicon, { name: busy === 'run' ? 'loading' : 'play', spinning: busy === 'run' }), 'Run'),
          h(Button, { onClick: doCheck, disabled: !!busy, 'data-testid': 'project-check' }, h(Codicon, { name: busy === 'check' ? 'loading' : 'check', spinning: busy === 'check' }), 'Check step'),
          h(Button, { variant: 'ghost', onClick: doHint, disabled: !!busy || left <= 0, 'data-testid': 'project-hint' }, h(Codicon, { name: 'lightbulb' }), left > 0 ? `Hint (${left} left)` : 'No hints left'),
          canSolution && !solutionShown ? h(Button, { variant: 'ghost', onClick: doSolution, disabled: !!busy, 'data-testid': 'project-solution' }, h(Codicon, { name: 'eye' }), "Show CodeCoach's solution") : null
        ),
        canSolution && !solutionShown ? h('div', { style: { ...S.cap, color: C.yellow }, 'data-testid': 'project-solution-warning' }, 'This replaces your code with CodeCoach\'s and marks the step as done with help.') : null,
        err ? h(Banner, { tone: 'bad' }, err) : null
      )
    ),
    shownHints.length
      ? block('Hints', h('ol', { style: { margin: 0, paddingLeft: SP.xl, display: 'flex', flexDirection: 'column', gap: SP.xs, maxWidth: MAX_READ }, 'data-testid': 'project-hints' }, shownHints.map((x, k) => h('li', { key: k, 'data-project-hint': k + 1 }, x))))
      : null,
    res
      ? block(
          res.kind === 'check' ? 'Check result' : 'Run result',
          h(
            'div',
            { style: { display: 'flex', flexDirection: 'column', gap: SP.sm }, 'data-testid': 'project-result', 'data-result-kind': res.kind, 'data-result-ok': res.data.ok ? '1' : '0' },
            h('div', { style: { ...S.row, ...S.muted } }, sum, res.kind === 'check' && !res.data.step_passed ? ' (hidden tests only show pass or fail)' : ''),
            h(RunResults, { run: res.data, names, dirty })
          )
        )
      : null,
    isPassed
      ? h(
          'div',
          { style: { ...S.card, display: 'flex', flexDirection: 'column', gap: SP.sm, borderColor: tint(C.green, 45), background: tint(C.green, 8) }, 'data-testid': 'project-step-done' },
          h('div', { style: { ...S.row, fontWeight: 600 } }, h(Codicon, { name: 'pass-filled', style: { color: C.green } }), 'Step done'),
          nextRow
            ? nextRow.status === 'building'
              ? h('div', { style: { ...S.row, ...S.muted }, role: 'status', 'data-testid': 'project-next-building' }, h(Codicon, { name: 'loading', spinning: true }), 'Building the next step...')
              : h('div', null, h(Button, { onClick: () => onGoto(index + 1), 'data-testid': 'project-next' }, 'Next step', h(Codicon, { name: 'arrow-right' })))
            : h('div', { style: S.muted }, 'That was the last step.')
        )
      : null
  ])
}

const VIEWS = [
  ['projects', 'Projects', 'tools'],
  ['library', 'Library', 'library'],
  ['map', 'Course', 'type-hierarchy'],
  ['learn', 'Learn', 'lightbulb'],
  ['practice', 'Review', 'history'],
  ['read', 'Read', 'book'],
  ['ask', 'Ask', 'comment-discussion'],
  ['progress', 'Progress', 'graph']
]

function CodeCoachPage() {
  const [view, setView] = useState('projects')
  const [projOpen, setProjOpen] = useState(() => CTX.storage.get('project:open', null))
  const [courseId, setCourseIdState] = useState(() => CTX.storage.get('course', null))
  const [objective, setObjective] = useState(null)
  const [token, setToken] = useState(0)
  const [focus, setFocus] = useState(null)
  const [readNote, setReadNote] = useState(null)
  const courses = useAsync(() => api('/courses'), [])
  const [srcOpen, setSrcOpen] = useState(false)
  const [srcW, setSrcW] = useState(null)
  const [srcTarget, setSrcTarget] = useState(null)
  const [pageW, setPageW] = useState(1200)
  const pageRef = useRef(null)
  const srcTick = useRef(0)
  const srcHome = useRef(null)
  const srcOpenRef = useRef(false)
  const [mapSel, setMapSel] = useState(null)
  const [learnObj, setLearnObj] = useState(null)
  const [askPrefill, setAskPrefill] = useState('')

  const openLearn = id => {
    setLearnObj(id || null)
    setToken(t => t + 1)
    setView('learn')
  }
  const openProject = id => {
    setProjOpen(id || null)
    CTX.storage.set('project:open', id || null)
  }
  const openReview = () => (setObjective(null), setToken(t => t + 1), setView('practice'))
  const openAskAbout = topic => {
    setAskPrefill(topic ? `What does my material say about ${topic.replace(/[.?!]+$/, '')}?` : '')
    setView('ask')
  }

  const setCourseId = id => {
    setCourseIdState(id)
    CTX.storage.set('course', id)
    setObjective(null)
    setSrcTarget(null)
  }

  useEffect(() => {
    if (pending.next) {
      pending.next = false
      setView('practice')
      setObjective(null)
      setToken(t => t + 1)
    }
    return bus.on((type, payload) => {
      if (type === 'next') {
        pending.next = false
        setView('practice')
        setObjective(null)
        setToken(t => t + 1)
      }
      if (type === 'bank') courses.reload()
      if (type === 'source') {
        srcTick.current += 1
        setSrcTarget({ ...(payload || {}), tick: srcTick.current })
        setSrcOpen(true)
      }
      // Listen to a topic: light the passage being read, but only if Sources is already open.
      if (type === 'listening') {
        srcTick.current += 1
        const tick = srcTick.current
        if (payload && payload.stop) setSrcTarget(t => (t && t.hold ? { ...t, hold: false, tick } : t))
        else if (srcOpenRef.current && payload) setSrcTarget({ docId: payload.docId, passageId: payload.passageId, hold: true, tick })
      }
      if (type === 'topic') {
        setMapSel(payload)
        setObjective(null)
        setView('map')
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Opening Read while a question is open puts that question back, ungraded (server: POST /items/:id/release).
  const goView = async (id, passage) => {
    if (id === 'map') setMapSel(null)
    if (id === 'projects' && view === 'projects') openProject(null) // the tab again = back to the projects home
    if (id === 'learn') {
      setLearnObj(null)
      setToken(t => t + 1)
    }
    if (id === 'ask' && view !== 'ask') setAskPrefill('')
    if (id === 'read') {
      setFocus(passage || null)
      const open = openQuestion.id
      setReadNote(null)
      if (view === 'practice' && open) {
        openQuestion.id = null
        try {
          const r = await api(`/items/${encodeURIComponent(open)}/release`, { method: 'POST' })
          if (r && r.released) setReadNote(r.message)
        } catch (_e) {
          /* Nothing was graded either way; reading still opens. */
        }
      }
    }
    setView(id)
  }

  const list = (courses.data && courses.data.courses) || []
  const active = list.find(c => c.id === courseId) || list[0] || null
  const cid = active ? active.id : null

  // Sources: open/closed and width are remembered per course.
  useEffect(() => {
    if (!cid) return
    setSrcOpen(!!CTX.storage.get(`sources:open:${cid}`, false))
    setSrcW(CTX.storage.get(`sources:w:${cid}`, null))
    setSrcTarget(null)
  }, [cid])
  useEffect(() => {
    const el = pageRef.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(() => setPageW(Math.round(el.getBoundingClientRect().width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const showSources = !!cid && view !== 'library' && view !== 'projects' && srcOpen
  srcOpenRef.current = showSources
  const narrow = pageW < 820
  const minW = 280
  const maxW = Math.max(minW, Math.floor(pageW * 0.7))
  const panelW = Math.max(minW, Math.min(maxW, srcW || Math.round(pageW * 0.4)))
  const toggleSources = open => {
    setSrcOpen(open)
    if (cid) CTX.storage.set(`sources:open:${cid}`, open)
    if (!open) srcHome.current && srcHome.current.focus && srcHome.current.focus()
  }
  useEffect(() => {
    if (!showSources) return undefined
    const onKey = e => {
      if (e.key === 'Escape') toggleSources(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showSources, cid])
  useEffect(() => {
    if (srcOpen && cid) CTX.storage.set(`sources:open:${cid}`, true)
  }, [srcOpen, cid])
  const startDrag = e => {
    e.preventDefault()
    const move = ev => {
      const r = pageRef.current.getBoundingClientRect()
      const w = Math.round(Math.max(minW, Math.min(Math.floor(r.width * 0.7), r.right - ev.clientX)))
      setSrcW(w)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setSrcW(w => {
        if (w && cid) CTX.storage.set(`sources:w:${cid}`, w)
        return w
      })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  const nudge = e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    const w = Math.max(minW, Math.min(maxW, panelW + (e.key === 'ArrowLeft' ? 40 : -40)))
    setSrcW(w)
    if (cid) CTX.storage.set(`sources:w:${cid}`, w)
  }

  let body
  if (view === 'projects') body = h(ProjectsView, { openId: projOpen, setOpenId: openProject })
  else if (courses.loading && !courses.data) body = h(Spinner, { text: 'Loading your courses' })
  else if (courses.error) body = h(Banner, { tone: 'bad' }, `CodeCoach's backend did not answer: ${courses.error}. Is the plugin enabled and the gateway restarted?`)
  else if (view === 'library') body = h(LibraryView, { courses: list, courseId: cid, setCourseId, setView, reload: courses.reload })
  else if (!cid) body = h(EmptyState, { title: 'Pick a course first', description: 'Import programming notes in the Library.' })
  else if (view === 'map') body = h(CourseView, { key: `${cid}:${mapSel || ''}`, courseId: cid, course: active, initialSel: mapSel, onRead: () => goView('read'), startLearn: openLearn, startPractice: id => (setObjective(id), setToken(t => t + 1), setView('practice')) })
  else if (view === 'learn') body = h(LearnView, { courseId: cid, objectiveId: learnObj, token, onCourse: () => goView('map'), onAsk: openAskAbout, onReview: openReview, onOpenTopic: openLearn })
  else if (view === 'practice') body = h(PracticeView, { courseId: cid, objectiveId: objective, token, onProgressChange: () => {}, onRead: pid => goView('read', pid), onLearn: openLearn, paused: showSources, onCloseSources: () => toggleSources(false) })
  else if (view === 'read') body = h(ReadView, { key: cid, courseId: cid, focus, note: readNote, onTopic: id => (setObjective(id), setToken(t => t + 1), setView('practice')) })
  else if (view === 'ask') body = h(AskView, { key: `${cid}:${askPrefill}`, prefill: askPrefill, courseId: cid, strict: active.mode === 'strict', reason: active.mode_reason, onLibrary: () => setView('library') })
  else body = h(ProgressView, { courseId: cid })

  return h(
    'div',
    { style: S.page, ref: pageRef, className: 'ath' },
    h('style', null, PAGE_CSS),
    h(
      'div',
      { style: { ...S.row, padding: `${SP.sm}px ${SP.xl}px`, borderBottom: `1px solid ${C.line}`, gap: SP.lg, flexWrap: 'wrap' } },
      h('nav', { style: S.row, 'aria-label': 'CodeCoach sections' }, VIEWS.map(([id, label, icon]) => h(Button, { key: id, size: 'sm', variant: view === id ? 'secondary' : 'ghost', onClick: () => goView(id), 'aria-current': view === id ? 'page' : undefined }, h(Codicon, { name: icon }), label))),
      h('div', { style: { flex: 1 } }),
      list.length > 0 && view !== 'library' && view !== 'projects'
        ? h(
            'span',
            { style: { ...S.row, gap: SP.xs } },
            active && active.mode === 'strict'
              ? h('span', { title: 'Answers come only from your materials', role: 'img', 'aria-label': 'Answers come only from your materials', 'data-testid': 'mode-shield', style: { display: 'inline-flex' } }, h(Codicon, { name: 'shield', style: { color: C.green } }))
              : active
                ? h('span', { title: `Open mode: ${active.mode_reason || 'answers may use general knowledge'}`, role: 'img', 'aria-label': 'Open mode', 'data-testid': 'mode-shield', style: { display: 'inline-flex' } }, h(Codicon, { name: 'info', style: { color: C.yellow } }))
                : null,
            h('select', { value: cid || '', 'aria-label': 'Course', onChange: e => setCourseId(e.target.value), style: { ...S.field, width: 'auto', maxWidth: 360, fontSize: FS.lead, fontWeight: 600, background: 'transparent', border: `1px solid transparent`, paddingLeft: SP.sm } }, list.map(c => h('option', { key: c.id, value: c.id }, c.title)))
          )
        : null,
      active && view !== 'library' && view !== 'projects'
        ? h(Button, { size: 'sm', variant: showSources ? 'secondary' : 'ghost', onClick: e => ((srcHome.current = e.currentTarget), toggleSources(!showSources)), 'aria-pressed': showSources, 'aria-label': 'Sources', 'data-testid': 'sources-toggle' }, h(Codicon, { name: 'file-text' }), 'Sources')
        : null
    ),
    h(
      'div',
      { style: { flex: 1, minHeight: 0, display: 'flex', position: 'relative' } },
      h('div', { style: { ...S.body, flex: 1, minWidth: 0 }, 'data-view-body': view }, body),
      showSources
        ? [
            h('div', { key: 'grip', role: 'separator', 'aria-orientation': 'vertical', 'aria-label': 'Resize Sources', 'aria-valuemin': minW, 'aria-valuemax': maxW, 'aria-valuenow': panelW, tabIndex: 0, onPointerDown: startDrag, onKeyDown: nudge, 'data-testid': 'sources-grip', style: { width: 6, cursor: 'col-resize', background: C.line, flex: 'none', ...(narrow ? { position: 'absolute', top: 0, bottom: 0, right: panelW, zIndex: 6 } : {}) } }),
            h('div', { key: 'panel', 'data-sources-w': panelW, style: { width: panelW, flex: 'none', minHeight: 0, borderLeft: `1px solid ${C.line}`, ...(narrow ? { position: 'absolute', top: 0, bottom: 0, right: 0, zIndex: 5, boxShadow: `-6px 0 18px ${tint(C.text, 18)}` } : {}) } }, h(SourcesPanel, { key: cid, courseId: cid, target: srcTarget, onTopic: id => bus.emit('topic', id), onClose: () => toggleSources(false) }))
          ]
        : null
    )
  )
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

export default {
  id: 'codecoach',
  name: 'CodeCoach',
  defaultEnabled: false,
  register(ctx) {
    CTX = ctx
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/codecoach' }, render: () => h(CodeCoachPage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: '/codecoach', label: 'CodeCoach', codicon: 'mortar-board' } },
      {
        id: 'next',
        area: PALETTE_AREA,
        data: {
          id: 'codecoach.next',
          label: 'CodeCoach: next question',
          keywords: ['codecoach', 'practice', 'question', 'quiz', 'study'],
          run: () => {
            pending.next = true
            host.navigate('/codecoach')
            bus.emit('next')
          }
        }
      }
    ])
    // Push updates from the backend; polling in the views is the fallback.
    // ctx.onEvent is tracked by the host and removed on unload.
    ctx.onEvent('plugin.codecoach.job.progress', ev => bus.emit('job', ev && ev.payload ? ev.payload : ev))
    ctx.onEvent('plugin.codecoach.bank.updated', () => bus.emit('bank'))
    ctx.onDispose(() => {
      listeners.clear()
      CTX = null
    })
  }
}
