import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const text = readFileSync(new URL('../src/main.ts', import.meta.url), 'utf8')
const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true)
const bootstrapSource = source.statements
  .filter(statement => !ts.isImportDeclaration(statement) && !ts.isExpressionStatement(statement))
  .map(statement => statement.getText(source)).join('\n')
const code = ts.transpileModule(bootstrapSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  transformers: { before: [context => root => {
    const visit = node => {
      if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
        return ts.factory.updateCallExpression(node, ts.factory.createIdentifier('loadModule'), node.typeArguments, node.arguments)
      }
      return ts.visitEachChild(node, visit, context)
    }
    return ts.visitNode(root, visit)
  }] },
}).outputText

async function bootstrap(capabilities) {
  const calls = []
  const application = {
    config: {}, use() {}, mount() { calls.push('mount') }, onUnmount() {},
  }
  const appState = { checkRuntimeInfo: async () => ({ ready: false }) }
  const stores = { initialize: async () => {} }
  const context = vm.createContext({
    createLogger: () => ({ warn() {}, error() {} }), createPinia: () => ({}),
    createApp: () => application, App: {}, router: {}, i18n: {},
    window: { addEventListener() {} }, document: { querySelector: () => ({ hasChildNodes: () => false }) },
    useSettingsStore: () => ({ initialize: async () => {} }),
    useAppStore: () => appState,
    isTauriRuntime: () => false, isMainTaskOwner: () => true,
    pluginHostCapabilities: async () => capabilities,
    initializeWorkbenchBridge: async () => {}, connectWorkerEvents: async () => {},
    initTheme() {}, activeRuntimeEnvironment: () => undefined,
    async loadModule(name) {
      if (name === '@/stores/task') return { useTaskStore: () => stores }
      if (name === '@/stores/model') return { useModelStore: () => stores }
      if (name === '@/stores/workflow') return { useWorkflowStore: () => stores }
      if (name === '@tauri-apps/api/core') return { invoke: async () => ({ version: '0.0.12', updateSupported: false }) }
      throw new Error(`Unexpected startup dependency: ${name}`)
    },
  })
  vm.runInContext(code, context)
  await vm.runInContext('bootstrap()', context)
  return calls
}
