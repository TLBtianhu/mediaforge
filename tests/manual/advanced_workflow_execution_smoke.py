"""Run the Studio worker against a local pymss checkout, then inspect the real UI store."""

import argparse
import json
import math
import os
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--python', required=True, type=Path)
parser.add_argument('--pymss-source', required=True, type=Path)
parser.add_argument('--dependency-overlay', type=Path)
parser.add_argument('--model-dir', type=Path)
parser.add_argument('--model', default='melband_roformer_instvox_duality_v2.ckpt')
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
artifacts = Path(tempfile.mkdtemp(prefix='advanced-execution-', dir=root / '.tmp'))
source = artifacts / 'input.wav'
with wave.open(str(source), 'wb') as audio:
    audio.setnchannels(2)
    audio.setsampwidth(2)
    audio.setframerate(44100)
    audio.writeframes(b''.join(struct.pack('<hh', *(2 * [int(3000 * math.sin(i * math.tau * 220 / 44100))]))
                               for i in range(44100 * 2)))

paths = [str(args.pymss_source.resolve())]
if args.dependency_overlay:
    paths.append(str(args.dependency_overlay.resolve()))
env = {**os.environ, 'PYTHONPATH': os.pathsep.join(paths), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
probe = subprocess.run([str(args.python), '-c',
    'import json,pymss,pymss.graph as g;print(json.dumps({"source":pymss.__file__,"version":g.EXECUTION_EVENT_VERSION}))'],
    env=env, capture_output=True, text=True, encoding='utf-8', check=True)
runtime = json.loads(probe.stdout)
assert Path(runtime['source']).resolve().is_relative_to(args.pymss_source.resolve()), runtime
print('Runtime source:', runtime['source'], flush=True)


def graph(input_path, model=None):
    nodes = [{'id': 1, 'type': 'pymss_load_audio', 'pos': [60, 140],
              'widgets_values': [str(input_path), ''], 'inputs': [], 'outputs': [{'name': 'audio', 'type': 'AUDIO', 'links': [1]}]}]
    if model:
        nodes += [
            {'id': 2, 'type': 'pymss_mss_params', 'pos': [60, 360], 'widgets_values': [1, 'Default', 'Default', False, False, False],
             'outputs': [{'name': 'params', 'type': 'PYMSS_MSS_PARAMS', 'links': [2]}]},
            {'id': 3, 'type': 'mss_separate', 'pos': [440, 140], 'widgets_values': [model, 'cuda', False, 'modelscope', '0', False],
             'inputs': [{'name': 'audio', 'type': 'AUDIO', 'link': 1}, {'name': 'params', 'type': 'PYMSS_MSS_PARAMS', 'link': 2}],
             'outputs': [{'name': 'Vocals (Audio)', 'type': 'AUDIO', 'links': [3]}, {'name': 'Vocals (String)', 'type': 'STRING'},
                         {'name': 'Instrumental (Audio)', 'type': 'AUDIO', 'links': [4]}, {'name': 'Instrumental (String)', 'type': 'STRING'}]},
        ]
        links = [[1, 1, 0, 3, 0, 'AUDIO'], [2, 2, 0, 3, 1, 'PYMSS_MSS_PARAMS']]
        outputs = [(4, 3, 0, 3), (5, 3, 2, 4)]
    else:
        nodes.append({'id': 2, 'type': 'pymss_audio_invert_phase', 'pos': [400, 140],
                      'inputs': [{'name': 'audio', 'type': 'AUDIO', 'link': 1}],
                      'outputs': [{'name': 'audio', 'type': 'AUDIO', 'links': [2]}]})
        links = [[1, 1, 0, 2, 0, 'AUDIO']]
        outputs = [(3, 2, 0, 2)]
    for node_id, origin, slot, link in outputs:
        nodes.append({'id': node_id, 'type': 'pymss_save_audio', 'pos': [820, 140 + (node_id - outputs[0][0]) * 250],
                      'widgets_values': ['wav', f'output-{node_id}', '44100', 'FLOAT', 'PCM_24', '320k'],
                      'inputs': [{'name': 'audio', 'type': 'AUDIO', 'link': link}], 'outputs': []})
        links.append([link, origin, slot, node_id, 0, 'AUDIO'])
    # Editing can move the input to the end of the serialized node array.
    return {'version': 0.4, 'nodes': nodes[1:] + nodes[:1], 'links': links}


cases = [('audio', graph(source), 0), ('missing', graph(artifacts / 'missing.wav'), 1)]
if args.model_dir:
    cases.append(('cuda', graph(source, args.model), 0))

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=['--no-proxy-server'])
    for case, definition, expected_exit in cases:
        payload = {'taskId': case, 'workflowName': case, 'workflow': definition, 'output': str(artifacts / case),
                   'outputLayout': 'flat', 'device': 'cuda' if case == 'cuda' else 'cpu', 'downloadMethod': 'never',
                   'modelDir': str(args.model_dir) if args.model_dir else None, 'audioParams': {'wav_bit_depth': 'FLOAT'}}
        payload_file = artifacts / (case + '-payload.json')
        payload_file.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        result = subprocess.run([str(args.python), str(root / 'python/worker.py'), 'infer_workflow', '--payload', str(payload_file)],
                                env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
        (artifacts / (case + '-stdout.jsonl')).write_text(result.stdout, encoding='utf-8')
        (artifacts / (case + '-stderr.log')).write_text(result.stderr, encoding='utf-8')
        assert result.returncode == expected_exit, result.stdout[-3000:] + result.stderr[-1500:]
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        execution = [event['payload'] for event in events if event['type'] == 'workflow_execution']
        assert execution and execution[0]['event'] == 'execution_started'
        assert not any(event['type'] == 'task.progress' for event in events)
        assert execution[-1]['event'] == ('execution_failed' if expected_exit else 'execution_completed')
        if expected_exit:
            assert execution[-1]['nodeId'] == '1'
            assert not any(event['type'] == 'task.result' for event in events)
        else:
            done = next(event['payload'] for event in events if event['type'] == 'task.result')
            assert len(done['outputs']) == (2 if case == 'cuda' else 1)
            assert all(Path(item['path']).is_file() and Path(item['path']).stat().st_size > 44 for item in done['outputs'])
            if case == 'cuda':
                assert {item['stem'].lower() for item in done['outputs']} == {'vocals', 'instrumental'}
                assert any(item['event'] == 'node_progress' and item['nodeId'] == '3' for item in execution)

        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        entry = {'id': case, 'name': case, 'description': '', 'definition': definition, 'format': 'graph',
                 'formatVersion': 1, 'createdAt': 1, 'updatedAt': 1}
        state = {'app-settings': {'startupOnboardingSeen': True, 'locale': 'zh-CN', 'themeMode': 'light'},
                 'workflow-state': {'workflows': [entry], 'selectedWorkflowId': case}}
        page.add_init_script('for(const [key,value] of Object.entries('+json.dumps(state)+')) localStorage.setItem("pymss-studio:"+key,JSON.stringify(value));')
        page.goto(args.url + '/#/workflow-node-editor?workflowId=' + case)
        page.wait_for_load_state('networkidle')
        page.add_style_tag(content='.title-bar{display:none!important}')
        page.locator('canvas.lg-canvas').focus()
        page.keyboard.press('Control+s')
        expect(page.locator('.workflow-save-status')).to_have_text('已保存')
        entry = page.evaluate("document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('workflow').workflows[0]")
        page.evaluate('''({entry,payload})=>{
          const stores=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s;
          window.__taskStore=stores.get('task');
          window.__taskStore.tasks=[{id:entry.id,jobId:entry.id,input:'',model:entry.name,output:payload.output,status:'queued',
            progress:0,createdAt:1,updatedAt:1,logs:[],outputs:[],files:[],
            runConfig:{runMode:'workflow',workflowId:entry.id,workflowDefinition:entry.definition,
              workbench:{nativeGraph:true,sourceDefinition:entry.definition,bindings:{inputs:{},outputs:{},primaryInput:'',outputLayout:'flat'}}}}];
        }''', {'entry': entry, 'payload': payload})
        active_index = next(index for index, event in enumerate(events) if event['type'] == 'workflow_execution'
                            and event['payload']['event'] == 'node_started'
                            and event['payload']['nodeId'] == ('3' if case == 'cuda' else '1'))
        canvas = page.locator('canvas.lg-canvas')
        node_id = events[active_index]['payload']['nodeId']
        def pixel():
            return canvas.evaluate('''(el,id)=>{
              el.data.draw(true,true);
              const node=el.data.graph.nodes.find(n=>String(n.id)===id),[x,y,w,h]=node.boundingRect;
              const point=el.data.ds.convertOffsetToCanvas([x-4/el.data.ds.scale,y+h/2]);
              return Array.from(el.data.ctx.getImageData(Math.round(point[0]*devicePixelRatio),Math.round(point[1]*devicePixelRatio),1,1).data);
            }''', node_id)
        before = pixel()
        page.evaluate('events=>events.forEach(event=>window.__taskStore.handleWorkerEvent(event))', events[:active_index + 1])
        during = pixel()
        assert during[1] > during[0] + 50 and during != before, (case, before, during)
        page.screenshot(path=str(artifacts / (case + '-running.png')), animations='disabled')
        page.evaluate('events=>events.forEach(event=>window.__taskStore.handleWorkerEvent(event))', events[active_index + 1:])
        assert pixel() == before
        task = page.evaluate('JSON.parse(JSON.stringify(window.__taskStore.tasks[0]))')
        observed_order = list(dict.fromkeys(item['nodeId'] for item in execution if item['event'].startswith('node_')))
        assert task['nodeExecutionOrder'] == observed_order
        assert task['status'] == ('failed' if expected_exit else 'done'), task
        if expected_exit:
            assert task['nodeStates']['1']['status'] == 'failed'
        else:
            assert all(item['status'] == 'done' for item in task['nodeStates'].values()), task['nodeStates']
        expect(page.locator('.litegraph-editor .workflow-node-log')).to_have_count(0)
        page.get_by_role('button', name='日志与结果', exact=True).click()
        modal = page.locator('.workflow-run-details-modal')
        expect(modal).to_be_visible()
        modal.locator('.n-tabs-tab').filter(has_text='节点执行').click()
        expected_order = observed_order + [str(node['id']) for node in entry['definition']['nodes']
                                           if str(node['id']) not in observed_order]
        assert modal.locator('[data-node-log]').evaluate_all(
            "elements => elements.map(el => el.dataset.nodeLog)") == expected_order
        expect(modal.locator('[data-node-log="1"]')).to_have_attribute('data-node-status', 'failed' if expected_exit else 'done')
        page.screenshot(path=str(artifacts / (case + '-nodes.png')), animations='disabled')
        page.wait_for_function("JSON.parse(localStorage.getItem('pymss-studio:task-history') || '{}').tasks?.[0]?.nodeExecutionOrder?.length > 0")
        page.reload()
        page.wait_for_load_state('networkidle')
        page.add_style_tag(content='.title-bar{display:none!important}')
        restored_order = page.evaluate("document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('task').tasks[0].nodeExecutionOrder")
        assert restored_order == observed_order
        page.get_by_role('button', name='日志与结果', exact=True).click()
        modal.locator('.n-tabs-tab').filter(has_text='节点执行').click()
        assert modal.locator('[data-node-log]').evaluate_all(
            "elements => elements.map(el => el.dataset.nodeLog)") == expected_order
        assert not errors, errors
        page.close()
        print('PASS:', case, len(execution), 'native lifecycle events', flush=True)
    browser.close()
print('Artifacts:', artifacts)
