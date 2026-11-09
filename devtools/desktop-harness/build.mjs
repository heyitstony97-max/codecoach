// Dev-only: bundle React 19 (CJS only upstream) into ESM files for the harness importmap.
// Run: ~/.hermes/node/bin/node devtools/desktop-harness/build.mjs
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import os from 'node:os'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.join(os.homedir(), '.hermes/hermes-agent/node_modules')
const require = createRequire(path.join(root, 'x.js'))
const esbuild = require('esbuild')

// CJS has no static named exports, so write them out from the real module's keys.
const names = mod => Object.keys(require(mod)).filter(k => /^[A-Za-z_$][\w$]*$/.test(k) && k !== 'default')
const wrapper = (mod, file) =>
  `import m from ${JSON.stringify(path.join(root, file))}\nexport default m\nexport const { ${names(mod).join(', ')} } = m\n`

await esbuild.build({
  stdin: undefined,
  entryPoints: {
    react: 'react-entry',
    'react-jsx-runtime': 'jsx-entry',
    'react-dom-client': 'dom-entry'
  },
  plugins: [
    {
      name: 'wrappers',
      setup(b) {
        const table = {
          'react-entry': wrapper('react', 'react/index.js'),
          'jsx-entry': wrapper('react/jsx-runtime', 'react/jsx-runtime.js'),
          'dom-entry': wrapper('react-dom/client', 'react-dom/client.js')
        }
        b.onResolve({ filter: /-entry$/ }, a => ({ path: a.path, namespace: 'wrap' }))
        b.onLoad({ filter: /.*/, namespace: 'wrap' }, a => ({ contents: table[a.path], resolveDir: root }))
      }
    }
  ],
  bundle: true,
  splitting: true,
  format: 'esm',
  outdir: path.join(here, 'vendor'),
  define: { 'process.env.NODE_ENV': '"development"' },
  logLevel: 'info'
})
