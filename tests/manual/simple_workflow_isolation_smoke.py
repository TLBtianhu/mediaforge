"""Verify simple workflow isolation and chosen output roots with a real Worker."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:1448')
parser.add_argument('--runtime', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
definition = {'version': 1, 'defaults': {'device': 'cpu', 'output_format': 'wav'}, 'steps': [], 'ensembles': [{
    'id': 'phase', 'algorithm': 'invert', 'output_stem': 'Inverted', 'save': 'Default',
    'inputs': [{'source': 'input', 'weight': 1}],
}]}


def entry(identifier, value):
    return {'id': identifier, 'name': identifier, 'description': '', 'definition': value,
            'format': 'simple', 'formatVersion': 1, 'createdAt': 1, 'updatedAt': 1}


with tempfile.TemporaryDirectory(prefix='pymss-isolation-') as temporary, sync_playwright() as playwright:
    directory = Path(temporary)
    blocked = directory / 'unavailable-default'
    blocked.write_text('keep', encoding='utf-8')
    sources = [directory / 'first.wav', directory / 'second.wav']
    for source in sources:
        sf.write(source, np.full((4410, 2), .2, dtype=np.float32), 44100, subtype='FLOAT')
    workflows = {'selectedWorkflowId': 'unfinished', 'workflows': [
        entry('unfinished', {'version': 1, 'steps': [{'id': 's', 'model': '', 'input': 'input', 'stems': [], 'save': {}}]}),
        entry('workbench', definition),
    ]}
    browser = playwright.chromium.launch(headless=True, args=['--no-proxy-server'])
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.set_default_timeout(8000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script('localStorage.setItem("pymss-studio:app-settings", JSON.stringify(' + json.dumps({
        'startupOnboardingSeen': True, 'locale': 'zh-CN', 'defaultDevice': 'cpu', 'outputDir': str(blocked),
    }) + ')); localStorage.setItem("pymss-studio:workflow-state", JSON.stringify(' + json.dumps(workflows) + '));')
    page.goto(args.url + '/#/workflows')
    page.wait_for_load_state('networkidle')
    page.wait_for_function("document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('task').initialized")
    page.evaluate('''() => {
      window.__stores = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s;
      window.__requests = [];
      window.__TAURI_EVENT_PLUGIN_INTERNALS__ = {unregisterListener() {}};
      window.__TAURI_INTERNALS__ = {
        metadata: {currentWindow: {label: 'main'}, currentWebview: {label: 'main'}}, transformCallback: () => 1,
        invoke: async (command, args) => {
          if (command === 'start_workflow_inference' || command === 'start_separation') {
            window.__requests.push(JSON.parse(JSON.stringify(args.payload))); return {taskId: args.payload.taskId, started: true};
          }
          if (command === 'save_app_store') {localStorage.setItem('pymss-studio:' + args.name, JSON.stringify(args.data)); return;}
          if (command === 'load_app_store') return JSON.parse(localStorage.getItem('pymss-studio:' + args.name) || 'null');
          if (command.startsWith('plugin:')) return 1;
          return null;
        },
      };
    }''')

    def execute(payload):
        payload_path = directory / 'request.json'
        payload_path.write_text(json.dumps(payload), encoding='utf-8')
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8',
               'PYTHONPATH': str(args.runtime.resolve()) if args.runtime else os.environ.get('PYTHONPATH', '')}
        completed = subprocess.run([sys.executable, str(root / 'python/worker.py'), 'infer_workflow', '--payload', str(payload_path)],
                                   capture_output=True, text=True, encoding='utf-8', env=env, timeout=60)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith('{')]
        outputs = [output for event in events if event.get('type') == 'task.done' for output in event['payload']['outputs']]
        for output in outputs:
            audio, rate = sf.read(output['path'], always_2d=True)
            assert rate == 44100 and audio.shape == (4410, 2)
            np.testing.assert_allclose(audio, -.2, atol=1e-6)
        for event in events:
            page.evaluate("event => window.__stores.get('task').handleWorkerEvent(event)", event)
        return outputs

    for layout, inputs in [('flat', sources[:1]), ('folders', sources)]:
        target = directory / layout
        request_count = page.evaluate('window.__requests.length')
        page.evaluate('''async ({layout, files, target}) => {
          const task = window.__stores.get('task');
          task.inputFiles = ['D:/SeparateOnly.wav']; task.separateTemporaryOutputDir = 'E:/SeparateOnly'; task.useTta = true;
          await task.startWorkbenchInference(window.__stores.get('workflow').workflows.find(item => item.id === 'workbench'), {
            inputs: {input: {files}}, outputs: {}, outputDirectory: target, primaryInput: 'input', outputLayout: layout,
          });
        }''', {'layout': layout, 'files': [str(path) for path in inputs], 'target': str(target)})
        page.wait_for_function('count => window.__requests.length > count', arg=request_count)
        payload = page.evaluate('window.__requests.at(-1)')
        assert payload['output'] == str(target) and payload['useTta'] is False
        if layout == 'flat':
            page.evaluate("location.hash = '/'")
            page.wait_for_load_state('networkidle')
            expect(page.locator('.stage-view--running')).to_have_count(0)
            assert page.evaluate("window.__stores.get('workflow').selectedWorkflowId") == 'unfinished'
            page.get_by_role('button', name=re.compile('任务队列')).click()
            expect(page.locator('.batch-queue-row')).to_have_count(0)
            page.keyboard.press('Escape')
        outputs = execute(payload)
        assert len(outputs) == len(inputs)
        for output in outputs:
            parent = Path(output['path']).parent
            assert parent == target if layout == 'flat' else parent.parent == target and parent.name in {'first', 'second'}
        assert blocked.read_text(encoding='utf-8') == 'keep'
        assert page.evaluate("window.__stores.get('task').inputFiles") == ['D:/SeparateOnly.wav']
        assert page.evaluate("window.__stores.get('task').separateTemporaryOutputDir") == 'E:/SeparateOnly'

    page.evaluate('''async () => {
      const task = window.__stores.get('task');
      window.__stores.get('model').selectedModel = 'single-model';
      await task.startSeparation();
      const model = task.tasks[0]; task.handleWorkerEvent({type:'task.done',taskId:model.id,payload:{files:[],outputs:[]}});
      const quick = await task.startWorkflowInference({...window.__stores.get('workflow').workflows.find(item => item.id === 'workbench'),id:'quick',name:'Quick Ensemble'});
      task.handleWorkerEvent({type:'task.done',taskId:quick.tasks[0].id,payload:{files:[],outputs:[]}});
    }''')
    page.get_by_role('button', name=re.compile('任务队列')).click()
    expect(page.locator('.batch-queue-row')).to_have_count(2)
    expect(page.locator('.batch-queue-list')).to_contain_text('single-model')
    expect(page.locator('.batch-queue-list')).to_contain_text('Quick Ensemble')
    page.locator('.batch-queue-modal').get_by_role('button', name='清空已结束', exact=True).click()
    expect(page.locator('.batch-queue-row')).to_have_count(0)
    remaining = page.evaluate("window.__stores.get('task').sessionJobs.map(job => Boolean(job.primary.runConfig?.workbench))")
    assert remaining == [True, True], remaining
    assert not errors, errors
    browser.close()
    print('PASS: separate-page task/queue/selection isolation, quick Ensemble retention, scoped cleanup, real flat/single and folders/batch output with an unavailable default directory')
