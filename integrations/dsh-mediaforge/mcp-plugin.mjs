import { realpathSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'mediaforge-mcp'
export const inject = ['tools']

function isFile(path) {
  return statSync(path, { throwIfNoEntry: false })?.isFile() === true
}

export function bridgeConnection() {
  // DSH resolves the relative row beside this bundle's patch. Anchor the
  // executable to that same bundle even when several installs exist.
  const root = resolve(dirname(realpathSync(fileURLToPath(import.meta.url))), '..', '..')
  const portable = isFile(join(root, 'mediaforge.portable'))
  const installed = isFile(join(root, 'app', 'MediaForge.exe'))
  const development = !portable && !installed && isFile(join(root, 'src-tauri', 'Cargo.toml'))
  const app = portable || development ? root : join(root, 'app')
  const bridge = join(app, 'python', 'agent_bridge.py')
  const interpreter = join(app, 'python-runtime', 'python.exe')
  if (!isFile(bridge) || !isFile(interpreter)) {
    throw new Error(`MediaForge MCP: ${app} must contain python/agent_bridge.py and python-runtime/python.exe`)
  }

  const data = join(root, development ? '.dev-data' : 'data')
  const assets = development ? data : root
  const models = join(assets, 'models')
  return {
    serverName: 'mediaforge',
    transport: 'stdio',
    command: interpreter,
    args: ['-I', '-B', bridge, '--stdio', '--data-root', data],
    cwd: app,
    env: {
      MEDIAFORGE_DATA_ROOT: data,
      MEDIAFORGE_DATA_DIR: data,
      MEDIAFORGE_MODEL_DIR: models,
      PYMSS_MODEL_DIR: models,
      MEDIAFORGE_RUNTIME_ENVS_DIR: join(assets, 'runtime-envs'),
    },
    failOnStartupError: false,
    toolCallTimeoutMs: 120000,
  }
}

export async function apply(ctx) {
  const client = await import('@deepseek-ai/dsh-mcp-client')
  await ctx.plugin(client, client.Config(bridgeConnection())).await()
}
