// One frontend build entry for package scripts and Tauri's release hook.
// Execute the already installed compilers directly; a package manager must not
// implicitly reinstall dependencies while running a noninteractive build.
import { spawn } from 'node:child_process'
import { existsSync, lstatSync, realpathSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = realpathSync(resolve(dirname(fileURLToPath(import.meta.url)), '..'))
const output = join(root, 'dist')
if (existsSync(output) && (lstatSync(output).isSymbolicLink() || realpathSync(output) !== output)) {
  throw new Error('Frontend build refuses a linked output directory')
}
if (dirname(output) !== root) throw new Error('Frontend build output escaped the workspace')
const compilers = [
  ['TypeScript', join(root, 'node_modules/vue-tsc/bin/vue-tsc.js'), ['--noEmit']],
  ['Vite', join(root, 'node_modules/vite/bin/vite.js'), ['build']],
]
for (const [label, file] of compilers) {
  if (!existsSync(file)) throw new Error(`${label} is missing; install the locked dependencies explicitly before building`)
}
rmSync(output, { recursive: true, force: true })
for (const [label, file, args] of compilers) {
  const code = await new Promise((done, fail) => {
    const child = spawn(process.execPath, [file, ...args], { cwd: root, windowsHide: true, stdio: 'inherit' })
    child.once('error', fail)
    child.once('close', value => done(value ?? 1))
  })
  if (code !== 0) {
    console.error(`${label} build failed with exit code ${code}`)
    process.exit(code)
  }
}
