// Tiny static server for the harness. Usage: node serve.mjs [port]
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const repo = path.resolve(here, '../..')
const port = Number(process.argv[2] || 5199)
const types = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.css': 'text/css', '.ttf': 'font/ttf' }

function resolve(url) {
  const u = decodeURIComponent(url.split('?')[0])
  if (u === '/') return path.join(here, 'index.html')
  if (u === '/plugin.js') return path.join(repo, 'desktop/plugin.js')
  if (u.startsWith('/real/')) return path.join(here, u)
  if (u.startsWith('/fixtures/')) return path.join(repo, 'tests/fixtures/api', path.basename(u))
  const f = path.join(here, u)
  return f.startsWith(here) ? f : null
}

http.createServer((req, res) => {
  const f = resolve(req.url)
  fs.readFile(f || '', (err, buf) => {
    if (err) { res.writeHead(404); res.end('not found'); return }
    res.writeHead(200, { 'content-type': types[path.extname(f)] || 'text/plain', 'cache-control': 'no-store' })
    res.end(buf)
  })
}).listen(port, '127.0.0.1', () => console.log(`harness on http://127.0.0.1:${port}`))
