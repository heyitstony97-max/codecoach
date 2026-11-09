// Loads desktop/plugin.js against a fake ctx whose rest() serves tests/fixtures/api/*.json.
import { createElement } from 'react'
import { createRoot } from 'react-dom/client'
import plugin from '/plugin.js'

const fx = {}
// ?data=real (history, from the Qwen e2e store) layers captured REAL
// responses (make_real.py) over the fictional fixtures; anything not captured falls back to a fixture.
const data = new URLSearchParams(location.search).get('data')
const names = ['courses', 'courses_import', 'job', 'map', 'next_lesson', 'next_item_mc', 'next_item_code', 'answer_mc',
  'run', 'hint', 'report', 'progress', 'drops', 'passage', 'timeline', 'plot']
await Promise.all(names.map(async n => (fx[n] = await (await fetch(`/fixtures/${n}.json`)).json())))
const real = {}
if (data) {
  const files = ['courses', 'map', 'next_lesson', 'next_item_mc', 'next_item_code', 'hint', 'progress', 'drops', 'timeline', 'passages', 'runs',
    'answer_right', 'answer_wrong', 'answer_code', 'meta']
  await Promise.all(files.map(async n => {
    const r = await fetch(`/${data}/${n}.json`)
    if (r.ok) real[n] = await r.json()
  }))
  for (const n of ['courses', 'map', 'next_lesson', 'next_item_mc', 'next_item_code', 'hint', 'progress', 'drops', 'timeline']) if (real[n]) fx[n] = real[n]
}

// No math fixture exists for /next, so synthesise one from the mc item (the plot endpoint has a fixture).
fx.next_item_math = structuredClone(fx.next_item_mc)
Object.assign(fx.next_item_math.item, {
  id: 'it_math_01', format: 'math', prompt: 'Differentiate the function, then check the graph.',
  payload: { op: 'diff', expr: 'x**2 * sin(x)', var: 'x' }
})

const qs = new URLSearchParams(location.search)
const state = { next: qs.get('next') || 'next_item_mc', runs: 0, log: [] }
window.__state = state
window.__fx = fx
const sleep = ms => new Promise(r => setTimeout(r, ms))

// Mimics the code the learner types: fewer passing tests once the starter is replaced.
function fakeRun(code) {
  if (real.runs) {
    const hit = real.runs.find(r => r.code === code) || (/#\s*include/.test(code) ? null : real.runs[0])
    return structuredClone((hit || real.runs[1]).result)
  }
  const ok = /sum|total|\+=|ord\(|int\(/.test(code) && !/#\s*TODO/.test(code)
  const run = structuredClone(fx.run)
  if (ok) run.results.forEach(r => Object.assign(r, { passed: true, detail: '', stdout: r.expected }))
  run.passed = run.results.filter(r => r.passed).length
  run.ok = run.passed === run.total
  return run
}

function rest(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase()
  const p = path.split('?')[0]
  state.log.push(`${method} ${path}`)
  const done = async v => (await sleep(30), structuredClone(v))
  let m
  if (method === 'GET') {
    if (p === '/courses') return done(fx.courses)
    if ((m = p.match(/^\/courses\/[^/]+\/(map|progress|drops|timeline)$/))) return done(fx[m[1]])
    if (p.match(/^\/courses\/[^/]+\/next$/)) return done(fx[state.next])
    if (p.match(/^\/jobs\//)) return done({ ...fx.job, pct: 100, status: 'done', stage: 'done' })
    if ((m = p.match(/^\/passages\/(.+)$/)) && real.passages && real.passages[decodeURIComponent(m[1])]) return done(real.passages[decodeURIComponent(m[1])])
    if ((m = p.match(/^\/passages\/(.+)$/))) {
      // Timeline events cite passages the fixture lacks: build one around the event's quote.
      const id = decodeURIComponent(m[1])
      const ev = fx.timeline.events.find(x => x.passage_id === id)
      if (!ev || id === fx.passage.passage.id) return done(fx.passage)
      const out = structuredClone(fx.passage)
      Object.assign(out.passage, { id, location: ev.location, text: `As the reading sets the scene, ${ev.quote} The paragraph goes on to describe what followed in the colonies.` })
      return done(out)
    }
    if (p.match(/^\/items\/[^/]+\/plot$/)) return done(fx.plot)
  } else if (method === 'PATCH' && (m = p.match(/^\/courses\/([^/]+)$/))) {
    const c = fx.courses.courses.find(x => x.id === decodeURIComponent(m[1]))
    if (!c) return Promise.reject(new Error('harness: unknown course'))
    c.subject = (opts.body && opts.body.subject ? String(opts.body.subject).trim().slice(0, 40) : '') || null
    return done(c)
  } else {
    if (p === '/courses/import') return done(fx.courses_import)
    if (p.endsWith('/answer')) {
      if (real.answer_code && opts.body && typeof opts.body.response === 'string') return done(real.answer_code)
      if (real.answer_right) return done(opts.body && opts.body.response === real.meta.mc_correct_index ? real.answer_right : real.answer_wrong)
      return done(fx.answer_mc)
    }
    if (p.endsWith('/run')) { state.runs++; return done(fakeRun(opts.body.code)) }
    if (p.endsWith('/hint')) return done(fx.hint)
    if (p.endsWith('/report')) return done(fx.report)
  }
  return Promise.reject(new Error(`harness: no fixture for ${method} ${path}`))
}

const reg = []
const store = new Map()
const ctx = {
  rest,
  storage: { get: (k, d) => (store.has(k) ? store.get(k) : d), set: (k, v) => store.set(k, v) },
  registerMany: list => reg.push(...list),
  onEvent: () => () => {},
  onDispose: () => {},
  setTimeout: (fn, ms) => { const t = setTimeout(fn, ms); return () => clearTimeout(t) },
  setInterval: (fn, ms) => { const t = setInterval(fn, ms); return () => clearInterval(t) }
}
plugin.register(ctx)
window.__reg = reg

const page = reg.find(c => c.area === 'routes')
createRoot(document.getElementById('root')).render(page.render())
window.__ready = true
