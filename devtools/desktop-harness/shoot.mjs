// Render -> screenshot loop (server must be running on :5199).
//   node shoot.mjs [outdir]        fixture shots (a1's fictional American Revolution data) -> shots/
//   node shoot.mjs real [outdir]   shots from REAL e2e data (?data=real) -> shots-real/
import { createRequire } from 'node:module'
import path from 'node:path'
import os from 'node:os'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(path.join(os.homedir(), '.hermes/hermes-agent/node_modules/x.js'))
const { chromium } = require('playwright')
const REAL = process.argv[2] === 'real'
const out = (REAL ? process.argv[3] : process.argv[2]) || path.join(here, REAL ? 'shots-real' : 'shots')
fs.mkdirSync(out, { recursive: true })
const base = `http://127.0.0.1:${process.env.HARNESS_PORT || 5199}/`

const shellDir = path.join(os.homedir(), '.cache/ms-playwright/chromium_headless_shell-1243')
const exe = fs.readdirSync(shellDir).map(d => path.join(shellDir, d, 'chrome-headless-shell')).find(p => fs.existsSync(p))
const browser = await chromium.launch({ executablePath: exe })
const errors = []

async function open(query = '') {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  page.on('pageerror', e => errors.push(String(e)))
  page.on('console', m => m.type() === 'error' && !m.text().startsWith('Failed to load resource') && errors.push(m.text()))
  await page.goto(base + query)
  await page.waitForFunction(() => window.__ready)
  await page.waitForSelector('text=The American Revolution')
  return page
}
const nav = (page, label) => page.locator('nav button', { hasText: label }).click()
const shot = (page, name) => page.screenshot({ path: path.join(out, name + '.png') })

async function fixtureShots() {
  // 1 library
  let p = await open()
  await shot(p, '01-library')
  // 2 map (40 nodes) + clicked node
  await nav(p, 'Course map')
  await p.waitForSelector('svg[role=img] g[role=button]')
  await shot(p, '02-map')
  await p.locator('svg g[role=button]').nth(8).click()
  await p.waitForSelector('text=From your materials')
  await p.evaluate(() => document.querySelector('[role=img]').scrollIntoView())
  await p.mouse.wheel(0, 320)
  await p.waitForTimeout(200)
  await shot(p, '03-map-lesson')
  await p.close()

  // 3 practice MC, after answer
  p = await open()
  await nav(p, 'Practice')
  await p.waitForSelector('text=According to the reading')
  await shot(p, '04-practice-question')
  await p.locator('[role=radio]').first().click()
  await p.getByRole('button', { name: 'Check answer' }).click()
  await p.waitForSelector('text=Where this comes from')
  await p.waitForSelector('mark')
  await p.waitForTimeout(150)
  await shot(p, '05-practice-mc-answered')
  await p.close()

  // 4 code box
  p = await open('?next=next_item_code')
  await nav(p, 'Practice')
  await p.waitForSelector('textarea')
  const ta = p.locator('textarea')
  await ta.click()
  await ta.press('End')
  await p.keyboard.type('  ')
  await p.waitForSelector('text=1 of 2 passing', { timeout: 5000 })
  await shot(p, '06-code-box-mixed')
  await p.close()

  // 5 timeline
  p = await open()
  await nav(p, 'Timeline')
  await p.waitForSelector('svg g[role=button]')
  await p.locator('svg g[role=button]').nth(2).click()
  await p.waitForSelector('mark')
  await shot(p, '07-timeline')
  await p.close()

  // 6 plot
  p = await open('?next=next_item_math')
  await nav(p, 'Practice')
  await p.waitForSelector('input[aria-label="Your expression"]')
  await p.fill('input[aria-label="Your expression"]', '2*x*sin(x) + x**2*cos(x)')
  await p.getByRole('button', { name: 'Check answer' }).click()
  await p.getByRole('button', { name: 'Show the graph' }).click()
  await p.waitForSelector('polyline')
  await p.mouse.wheel(0, 600)
  await p.waitForTimeout(200)
  await shot(p, '08-plot')
  await p.close()

  // 7 progress
  p = await open()
  await nav(p, 'Progress')
  await p.waitForSelector('text=Questions thrown out')
  await shot(p, '09-progress')
  await p.close()


}

async function realShots() {
  const get = async f => JSON.parse(fs.readFileSync(path.join(here, f), 'utf8'))
  const meta = await get('real/meta.json')
  const runs = await get('real/runs.json')
  const TITLE = 'The First Transcontinental Railroad'
  const openReal = async (data, extra = '', wait = TITLE) => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
    page.on('pageerror', e => errors.push(String(e)))
    page.on('console', m => m.type() === 'error' && !m.text().startsWith('Failed to load resource') && errors.push(m.text()))
    await page.goto(`${base}?data=${data}${extra}`)
    await page.waitForFunction(() => window.__ready)
    await page.waitForSelector(`text=${wait}`)
    return page
  }
  // 1 library
  let p = await openReal('real')
  await shot(p, 'r01-library')
  // 2 map
  await nav(p, 'Course map')
  await p.waitForSelector('svg[role=img] g[role=button]')
  await shot(p, 'r02-map')
  await p.locator('svg g[role=button]', { hasText: 'length, weight' }).first().click()
  await p.waitForSelector('text=From your materials')
  await p.evaluate(() => document.querySelector('[role=img]').scrollIntoView())
  await p.mouse.wheel(0, 320)
  await p.waitForTimeout(250)
  await shot(p, 'r03-map-lesson')
  await p.close()
  // 3 Golden Spike MC: question, then answered with the highlighted quote
  p = await openReal('real')
  await nav(p, 'Practice')
  await p.waitForSelector('text=Golden Spike')
  await shot(p, 'r04-practice-question')
  await p.locator('[role=radio]').nth(meta.mc_wrong_index).click()
  await p.getByRole('button', { name: 'Check answer' }).click()
  await p.waitForSelector('mark')
  await p.waitForTimeout(150)
  await shot(p, 'r05-practice-wrong')
  await p.close()
  p = await openReal('real')
  await nav(p, 'Practice')
  await p.waitForSelector('text=Golden Spike')
  await p.locator('[role=radio]').nth(meta.mc_correct_index).click()
  await p.getByRole('button', { name: 'Check answer' }).click()
  await p.waitForSelector('text=Where this comes from')
  await p.waitForSelector('mark')
  await p.waitForTimeout(150)
  await shot(p, 'r06-practice-mc-answered')
  await p.close()
  // 4 timeline
  p = await openReal('real')
  await nav(p, 'Timeline')
  await p.waitForSelector('svg g[role=button]')
  await p.locator('svg g[role=button]').nth(2).click()
  await p.waitForSelector('mark')
  await shot(p, 'r07-timeline')
  await p.close()
  // 5 C code box with real test results (compile + run in bwrap; recorded by make_real.py)
  const codeShot = async (label, name) => {
    const q = await openReal('real', '&next=next_item_code')
    await nav(q, 'Practice')
    await q.waitForSelector('textarea')
    await q.locator('textarea').fill(runs.find(r => r.label === label).code)
    await q.waitForSelector(label === 'compile error' ? 'text=Compile error' : 'text=passing', { timeout: 6000 })
    await q.waitForTimeout(250)
    await shot(q, name)
    await q.close()
  }
  await codeShot('partly passing', 'r08-code-box-mixed')
  await codeShot('all passing', 'r09-code-box-passing')
  await codeShot('compile error', 'r10-code-compile-error')
  // 6 progress with the real drop log
  p = await openReal('real')
  await nav(p, 'Progress')
  await p.waitForSelector('text=Questions thrown out')
  await shot(p, 'r11-progress')
  await p.evaluate(() => { for (const e of document.querySelectorAll('*')) if (e.scrollHeight > e.clientHeight + 50 && getComputedStyle(e).overflowY === 'auto') e.scrollTop = 460 })
  await p.waitForTimeout(200)
  await shot(p, 'r12-progress-drops')
  await p.close()
}

if (REAL) await realShots()
else await fixtureShots()
await browser.close()
console.log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors')
