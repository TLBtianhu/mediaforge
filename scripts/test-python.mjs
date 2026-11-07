// Use the declared Python and a disposable profile, independently of the shell's PATH.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn, spawnSync } from 'node:child_process'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'python/runtime-manifest.json'), 'utf8'))
const assets = JSON.parse(fs.readFileSync(path.join(root, 'manifest/runtime.json'), 'utf8'))
const entry = process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'
const developmentPython = path.join(root, '.dev-data/runtime-envs', assets.fallbackAssetId, entry)
const python = process.env.MEDIAFORGE_RUNTIME_PYTHON || process.env.PYMSS_STUDIO_PYTHON
  || (fs.existsSync(developmentPython) ? developmentPython : process.platform === 'win32' ? 'python' : 'python3')
const baseEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !/^(MEDIAFORGE_|PYMSS_|TAURI_|VERSIONS_SIGNING_|SIGNPATH_)/i.test(key)
  && !/KEY|PASSWORD|TOKEN|SECRET/i.test(key) && !['PYTHONHOME', 'PYTHONPATH'].includes(key)))
const probe = spawnSync(python, ['-I', '-B', '-c', 'import sys; print(".".join(map(str,sys.version_info[:2])))'],
  { cwd: root, env: baseEnv, encoding: 'utf8', windowsHide: true, timeout: 15000 })
if (probe.status !== 0 || probe.stdout.trim() !== manifest.python) {
  console.error(`Python tests require ${manifest.python}. Set MEDIAFORGE_RUNTIME_PYTHON to the matching isolated interpreter.`)
  process.exit(1)
}
const tempParent = path.join(root, '.tmp')
fs.mkdirSync(tempParent, { recursive: true })
const work = fs.mkdtempSync(path.join(tempParent, 'python-tests-'))
const env = { ...baseEnv, PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
  HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1', MODELSCOPE_OFFLINE: '1' }
const directories = {
  MEDIAFORGE_DATA_ROOT: 'data', MEDIAFORGE_DATA_DIR: 'data', MEDIAFORGE_MODEL_DIR: 'models',
  PYMSS_MODEL_DIR: 'models', MEDIAFORGE_RUNTIME_ENVS_DIR: 'runtime-envs',
  HOME: 'profile', USERPROFILE: 'profile', APPDATA: 'profile/AppData/Roaming', LOCALAPPDATA: 'profile/AppData/Local',
  XDG_CACHE_HOME: 'cache', XDG_CONFIG_HOME: 'config', XDG_DATA_HOME: 'state', WEBVIEW2_USER_DATA_FOLDER: 'webview',
  HF_HOME: 'cache/hf', HF_HUB_CACHE: 'cache/hf/hub', HUGGINGFACE_HUB_CACHE: 'cache/hf/hub',
  HF_MODULES_CACHE: 'cache/hf/modules', HF_ASSETS_CACHE: 'cache/hf/assets', HF_XET_CACHE: 'cache/hf/xet',
  TRANSFORMERS_CACHE: 'cache/transformers', MODELSCOPE_CACHE: 'cache/modelscope', TORCH_HOME: 'cache/torch',
  TORCH_EXTENSIONS_DIR: 'cache/torch-extensions', TRITON_CACHE_DIR: 'cache/triton',
  MPLCONFIGDIR: 'cache/matplotlib', NUMBA_CACHE_DIR: 'cache/numba', YOLO_CONFIG_DIR: 'cache/ultralytics',
  PIP_CACHE_DIR: 'cache/pip', TEMP: 'tmp', TMP: 'tmp', TMPDIR: 'tmp',
}
for (const [key, relative] of Object.entries(directories)) {
  env[key] = path.join(work, relative)
  fs.mkdirSync(env[key], { recursive: true })
}
if (process.platform === 'win32') {
  env.HOMEDRIVE = path.parse(env.USERPROFILE).root.slice(0, 2)
  env.HOMEPATH = env.USERPROFILE.slice(2)
}
// Optional signing-test tools stay outside the processing runtime asset.
const testTools = path.join(root, '.dev-data/python-test-tools-312')
if (fs.existsSync(testTools)) env.PYTHONPATH = testTools
const args = process.argv.slice(2)
const tests = args.length ? args : ['discover', '-s', 'tests/python', '-t', 'tests', '-p', 'test_*.py']
console.log(`Python ${manifest.python} tests: ${python}`)
const child = spawn(python, ['-B', '-m', 'unittest', ...tests], { cwd: root, env, stdio: 'inherit', windowsHide: true })
child.once('error', error => { console.error(error.message); process.exitCode = 1 })
child.once('close', (code, signal) => {
  // mkdtemp created this exact workspace child; no caller supplies the cleanup target.
  const resolved = fs.realpathSync(work)
  if (path.dirname(resolved) !== fs.realpathSync(tempParent) || !path.basename(resolved).startsWith('python-tests-')) {
    console.error('Refusing to clean an unexpected Python test directory')
    process.exitCode = 1
    return
  }
  fs.rmSync(resolved, { recursive: true, force: true })
  process.exitCode = signal ? 1 : code ?? 1
})
