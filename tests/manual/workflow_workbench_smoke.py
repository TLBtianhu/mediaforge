import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description='Verify workbench controls with isolated browser stores and the real Python worker.')
parser.add_argument('--url', default='http://127.0.0.1:1435')
parser.add_argument('--output-dir', type=Path, default=Path('.tmp/workbench-ui'))
parser.add_argument('--runtime', type=Path)
args = parser.parse_args()
artifacts = args.output_dir.resolve()
artifacts.mkdir(parents=True, exist_ok=True)
inputs = artifacts / 'inputs'
inputs.mkdir(exist_ok=True)
for name, value in [('one', .3), ('two', .6)]:
    sf.write(inputs / f'{name}.wav', np.full((4410, 2), value, dtype=np.float32), 44100, subtype='FLOAT')
sf.write(artifacts / 'extra.wav', np.full((4410, 2), .1, dtype=np.float32), 44100, subtype='FLOAT')
simple = {'version': 1, 'defaults': {'device': 'cpu', 'output_format': 'wav'}, 'steps': [], 'ensembles': [{
    'id': 'phase', 'algorithm': 'invert', 'output_stem': 'Inverted', 'save': 'Default', 'inputs': [{'source': 'input', 'weight': 1}],
}]}
graph = {'nodes': [
    {'id': 1, 'type': 'pymss_load_audio', 'pos': [70, 160], 'size': [290, 170], 'widgets_values': ['input.wav', 'lead'], 'outputs': [{'name': 'audio', 'type': 'AUDIO', 'links': [1]}]},
    {'id': 2, 'type': 'pymss_load_audio_batch', 'pos': [70, 390], 'size': [290, 170], 'widgets_values': ['', True, False, 'reference'], 'outputs': [{'name': 'audio', 'type': 'AUDIO', 'links': [2]}]},
    {'id': 3, 'type': 'pymss_save_audio', 'pos': [470, 160], 'size': [300, 270], 'widgets_values': ['wav', 'single', '0', 'FLOAT', 'PCM_24', '320k'], 'inputs': [{'name': 'audio', 'type': 'AUDIO', 'link': 1}]},
    {'id': 4, 'type': 'pymss_save_audio', 'pos': [830, 390], 'size': [300, 270], 'widgets_values': ['wav', 'batch', '0', 'FLOAT', 'PCM_24', '320k'], 'inputs': [{'name': 'audio', 'type': 'AUDIO', 'link': 2}]},
], 'links': [[1, 1, 0, 3, 0, 'AUDIO'], [2, 2, 0, 4, 0, 'AUDIO']], 'version': .4, 'last_node_id': 4, 'last_link_id': 2}


def entry(id, name, definition):
    return {'id': id, 'name': name, 'description': '保持采样率和声道，生成反相输出。' if id == 'phase' else '', 'definition': definition, 'format': 'simple' if 'steps' in definition else 'graph', 'formatVersion': 1, 'createdAt': 1, 'updatedAt': 1}


state = {'selectedWorkflowId': 'phase', 'workflows': [entry('phase', '批量反相处理', simple), entry('stereo', '独立音频输出', graph)]}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(error.stack))
    page.add_init_script('localStorage.setItem("pymss-studio:app-settings", JSON.stringify(' + json.dumps({'startupOnboardingSeen': True, 'locale': 'zh-CN', 'themeMode': 'light', 'outputDir': str(artifacts / 'output')}, ensure_ascii=False) + ')); localStorage.setItem("pymss-studio:workflow-state", JSON.stringify(' + json.dumps(state, ensure_ascii=False) + '));')
    page.goto(args.url.rstrip('/') + '/#/workflow-simple-editor?workflowId=phase')
    page.wait_for_load_state('networkidle')
    # The browser fixture owns tasks like the main window; hide its chrome
    # when displaying the editor route used by native auxiliary windows.
    page.add_style_tag(content='.title-bar { display: none !important; }')
    expect(page.locator('.simple-node-editor__topbar').get_by_role('button', name='保存', exact=True)).to_have_count(0)
    page.locator('.simple-node-editor__topbar').get_by_role('button',name='工作流设置',exact=True).click()
    expect(page.locator('.simple-workflow-settings-modal')).to_be_visible()
    expect(page.locator('.simple-workflow-settings-modal .n-select')).to_have_count(3)
    page.locator('.simple-workflow-settings-modal').get_by_role('button',name='关闭',exact=True).click()
    title = page.locator('.simple-node-editor__title')
    expect(title.locator('input')).to_have_count(0)
    title.get_by_role('button').click()
    title.locator('input').fill('反相处理（备选）')
    title.locator('input').press('Escape')
    expect(title.get_by_role('button')).to_have_text('批量反相处理')
    title.get_by_role('button').click()
    title.locator('input').fill('   ')
    title.locator('input').press('Enter')
    expect(title.get_by_role('button')).to_have_text('批量反相处理')
    page.screenshot(path=str(artifacts / 'initial.png'), full_page=True)
    expect(page.locator('[data-workbench-input="input"]')).to_be_visible()
    page.screenshot(path=str(artifacts / 'simple-before.png'), full_page=True)
    page.locator('.simple-editor-page > .simple-node-editor').evaluate('element=>window.__simpleEditorRoot=element')
    page.evaluate('''() => {
      window.__workerPayloads=[]; window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
      window.__TAURI_INTERNALS__={metadata:{currentWindow:{label:'main'},currentWebview:{label:'main'}},transformCallback:()=>1,convertFileSrc:path=>location.origin+'/@fs/'+path.replaceAll(String.fromCharCode(92),'/'),
        invoke: async(cmd,args) => {
          if(cmd==='pick_media_files') return window.__inputFiles;
          if(cmd==='pick_input_folder') return window.__inputFolder;
          if(cmd==='scan_media_paths') return {files:window.__inputFiles,warnings:[]};
          if(cmd==='pick_output_folder') return window.__outputFolder;
          if(cmd==='save_app_store') {localStorage.setItem('pymss-studio:'+args.name,JSON.stringify(args.data));return null;}
          if(cmd==='load_app_store') return JSON.parse(localStorage.getItem('pymss-studio:'+args.name)||'null');
          if(cmd==='mutate_workflow_store') {const state=JSON.parse(localStorage.getItem('pymss-studio:workflow-state'));const v=args.payload.entry;const index=state.workflows.findIndex(w=>w.id===v.id);if(index<0)state.workflows.push(v);else state.workflows[index]=v;state.selectedWorkflowId=v.id;localStorage.setItem('pymss-studio:workflow-state',JSON.stringify(state));return {state};}
          if(cmd==='start_workflow_inference') {window.__workerPayloads.push(args.payload);return {taskId:args.payload.taskId,started:true};}
          if(cmd==='plugin:event|listen') return 1;
          if(cmd==='cancel_task') return true;
          if(cmd==='is_maximized') return false;
          return null;
        }};
    }''')
    page.evaluate('(value)=>{window.__inputFiles=value.files;window.__inputFolder=value.folder;window.__outputFolder=value.output}', {'files': [str(inputs / 'one.wav'), str(inputs / 'two.wav')], 'folder': str(inputs), 'output': str(artifacts / 'selected-output')})
    input_group = page.locator('[data-workbench-input="input"]')
    page.evaluate('(files)=>window.__inputFiles=files', [str(artifacts / 'extra.wav')])
    input_group.get_by_role('button', name='添加文件', exact=True).click()
    expect(input_group.locator('.binding-file')).to_have_count(1)
    page.evaluate('(files)=>window.__inputFiles=files', [str(inputs / 'one.wav'), str(inputs / 'two.wav')])
    input_group.get_by_role('button', name='选择文件夹', exact=True).click()
    expect(input_group.locator('.binding-file')).to_have_count(3)
    position = page.locator('[data-simple-node="input"]').get_attribute('style')
    input_group.locator('.binding-file').filter(has_text='extra.wav').get_by_role('button').click()
    expect(input_group.locator('.binding-file')).to_have_count(2)
    assert page.locator('[data-simple-node="input"]').get_attribute('style') == position
    input_group.locator('.binding-file').filter(has_text='one.wav').get_by_role('button').click()
    expect(input_group.locator('.binding-file')).to_have_count(1)
    input_group.get_by_role('button', name='添加文件', exact=True).click()
    expect(input_group.locator('.binding-file')).to_have_count(2)
    input_group.get_by_role('button', name='添加文件', exact=True).click()
    expect(input_group.locator('.binding-file')).to_have_count(2)
    long_filename = '长文件名_' + 'very_long_video_filename_' * 5 + '.mp4'
    page.evaluate('(files)=>window.__inputFiles=files', [str(inputs / long_filename), *[str(inputs / ('long_filename_' + str(index) + '.wav')) for index in range(39)]])
    input_group.get_by_role('button', name='添加文件', exact=True).click()
    expect(input_group.locator('.binding-file')).to_have_count(42)
    long_name = input_group.locator('.binding-file').filter(has_text=long_filename).locator('strong')
    assert long_name.get_attribute('title') == str(inputs / long_filename)
    assert long_name.evaluate('element=>element.getBoundingClientRect().height <= parseFloat(getComputedStyle(element).lineHeight)*2.1')
    assert input_group.locator('.n-scrollbar-container').evaluate('element=>element.clientHeight <= 180 && element.scrollHeight > element.clientHeight')
    input_group.get_by_role('button', name='清空列表', exact=True).click()
    expect(input_group.locator('.binding-file')).to_have_count(0)
    page.evaluate('(files)=>window.__inputFiles=files', [str(inputs / 'one.wav'), str(inputs / 'two.wav')])
    input_group.get_by_role('button', name='选择文件夹', exact=True).click()
    expect(input_group).to_contain_text('2 个文件')
    expect(input_group.locator('.binding-file')).to_have_count(2)
    save_node = page.locator('[data-simple-node="save"]')
    expect(save_node.locator('[data-workbench-output]')).to_have_count(0)
    save_node.get_by_role('button', name='输出设置', exact=True).click()
    output_settings = page.locator('.simple-output-settings-modal')
    expect(output_settings).to_be_visible()
    expect(output_settings.locator('input')).to_have_value(str(artifacts / 'output'))
    output_settings.get_by_role('button', name='选择输出目录', exact=True).click()
    expect(output_settings.locator('input')).to_have_value(str(artifacts / 'selected-output'))
    output_settings.get_by_role('button', name='默认目录', exact=True).click()
    expect(output_settings.locator('input')).to_have_value(str(artifacts / 'output'))
    output_settings.get_by_role('button', name='选择输出目录', exact=True).click()
    page.screenshot(path=str(artifacts / 'output-settings.png'), full_page=True)
    output_settings.get_by_role('button', name='关闭', exact=True).click()
    expect(output_settings).not_to_be_visible()
    page.locator('[data-workbench-run-panel]').get_by_role('button', name='运行', exact=True).click()
    page.wait_for_timeout(1000)
    page.wait_for_function('window.__workerPayloads.length===1')
    payload = page.evaluate('window.__workerPayloads[0]')
    assert len(payload['tasks']) == 2, payload
    assert payload['workbench'] is True, payload
    assert all('workbenchOutputs' in task for task in payload['tasks'])
    input_group.locator('.binding-file').filter(has_text='one.wav').get_by_role('button').click()
    expect(input_group.locator('.binding-file')).to_have_count(1)
    frozen_inputs = page.evaluate("async()=>{const {useTaskStore}=await import('/src/stores/task.ts');return useTaskStore().tasks[0].runConfig.workbench.bindings.inputs.input.files}")
    assert len(frozen_inputs) == 2
    page.screenshot(path=str(artifacts / 'simple-execution.png'), full_page=True)
    assert page.locator('.simple-editor-page > .simple-node-editor').evaluate('element=>element===window.__simpleEditorRoot')
    stem_field=page.locator('[data-simple-node="phase"] input').first
    stem_field.fill('NextRun')
    stem_field.press('Tab')
    page.wait_for_timeout(150)
    expect(page.locator('.simple-editor-page > .simple-node-editor').get_by_role('button',name='撤销',exact=True)).to_be_enabled()
    title.get_by_role('button').click()
    title.locator('input').fill('批量反相与后续处理')
    title.locator('input').press('Enter')
    page.wait_for_function("async()=>{const {useWorkflowStore}=await import('/src/stores/workflow.ts');const entry=useWorkflowStore().workflows.find(item=>item.id==='phase');return entry.name==='批量反相与后续处理' && entry.definition.ensembles[0].output_stem==='NextRun'}")
    expect(page.locator('.simple-editor-page > .simple-node-editor .simple-save-status')).to_have_text('已保存')
    assert page.evaluate("async()=>{const {useWorkflowStore}=await import('/src/stores/workflow.ts');return useWorkflowStore().workflows.find(item=>item.id==='phase').description}") == '保持采样率和声道，生成反相输出。'
    frozen = page.evaluate("async()=>{const {useTaskStore}=await import('/src/stores/task.ts');return useTaskStore().tasks[0].runConfig.workbench.sourceDefinition}")
    assert frozen['ensembles'][0]['algorithm'] == 'invert'
    payload_file = artifacts / 'request.json'
    payload_file.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    env = {**os.environ, 'PYTHONPATH': str(args.runtime.resolve()) if args.runtime else os.environ.get('PYTHONPATH', ''), 'PYTHONIOENCODING': 'utf-8'}
    result = subprocess.run([sys.executable, str(root / 'python/worker.py'), 'infer_workflow', '--payload', str(payload_file)], capture_output=True, text=True, encoding='utf-8', env=env, timeout=60)
    (artifacts / 'worker-stdout.log').write_text(result.stdout, encoding='utf-8')
    (artifacts / 'worker-stderr.log').write_text(result.stderr, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    for event in events:
        page.evaluate("async(event)=>{const {useTaskStore}=await import('/src/stores/task.ts');useTaskStore().handleWorkerEvent(event)}", event)
    assert sum(event.get('type') == 'task.done' for event in events) == 2
    page.locator('[data-workbench-run-panel]').get_by_role('button', name='日志与结果').click()
    expect(page.locator('[data-workbench-details] .log-console')).to_be_visible()
    page.locator('.workflow-run-details-modal .n-tabs-tab').filter(has_text='节点执行').click()
    expect(page.locator('.run-details .run-node-detail')).to_have_count(3)
    expect(page.locator('.run-details .simple-node-editor')).to_have_count(0)
    page.locator('.run-details [data-node-log="phase"] summary').click()
    expect(page.locator('.run-details [data-node-log="phase"] .log-console')).to_be_visible()
    expect(page.locator('.run-details [data-node-log="phase"] .log-line-level')).not_to_have_count(0)
    page.screenshot(path=str(artifacts / 'simple-node-logs.png'), full_page=True)
    page.locator('.workflow-run-details-modal .n-tabs-tab').filter(has_text='结果 · 2').click()
    expect(page.locator('.result-track')).to_have_count(2)
    assert page.locator('.result-track__title strong').first.inner_text() == Path(page.locator('.result-track__title strong').first.get_attribute('title')).name
    page.wait_for_timeout(250)
    page.screenshot(path=str(artifacts / 'simple-results.png'), full_page=True)
    page.keyboard.press('Escape')
    expect(page.locator('[data-workbench-details]')).to_have_count(0)
    main_editor=page.locator('.simple-editor-page > .simple-node-editor')
    assert main_editor.evaluate('element=>element===window.__simpleEditorRoot')
    main_editor.get_by_role('button',name='撤销',exact=True).focus()
    page.keyboard.press('Control+z')
    expect(stem_field).to_have_value('Inverted')
    page.keyboard.press('Control+Shift+z')
    expect(stem_field).to_have_value('NextRun')
    page.keyboard.press('Escape')
    expect(title.get_by_role('button')).to_have_text('批量反相与后续处理')
    page.once('dialog', lambda dialog: dialog.accept())
    page.evaluate("delete window.__TAURI_INTERNALS__;location.hash='/workflow-node-editor?workflowId=stereo'")
    page.wait_for_load_state('networkidle')
    expect(page.locator('canvas.lg-canvas')).to_be_visible()
    page.evaluate('''() => {
      window.__workerPayloads ||= []; window.__nativePickerCalls=[]; window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
      window.__TAURI_INTERNALS__={metadata:{currentWindow:{label:'main'},currentWebview:{label:'main'}},transformCallback:()=>1,convertFileSrc:path=>location.origin+'/@fs/'+path.replaceAll(String.fromCharCode(92),'/'),
        invoke: async(cmd,args) => {
          if(cmd==='pick_single_audio_file') {window.__nativePickerCalls.push(cmd);return window.__inputFiles[0];}
          if(cmd==='pick_input_folder') {window.__nativePickerCalls.push(cmd);return window.__inputFolder;}
          if(cmd==='scan_media_paths') throw Error('Native graph folders must be scanned by pymss');
          if(cmd==='pick_output_folder') return window.__outputFolder;
          if(cmd==='save_app_store') {localStorage.setItem('pymss-studio:'+args.name,JSON.stringify(args.data));return null;}
          if(cmd==='load_app_store') return JSON.parse(localStorage.getItem('pymss-studio:'+args.name)||'null');
          if(cmd==='mutate_workflow_store') {const state=JSON.parse(localStorage.getItem('pymss-studio:workflow-state'));const v=args.payload.entry;const index=state.workflows.findIndex(w=>w.id===v.id);if(index<0)state.workflows.push(v);else state.workflows[index]=v;state.selectedWorkflowId=v.id;localStorage.setItem('pymss-studio:workflow-state',JSON.stringify(state));return {state};}
          if(cmd==='start_workflow_inference') {window.__workerPayloads.push(args.payload);return {taskId:args.payload.taskId,started:true};}
          if(cmd==='plugin:event|listen') return 1;
          if(cmd==='cancel_task') return true;
          if(cmd==='is_maximized') return false;
          return null;
        }};
    }''')
    page.screenshot(path=str(artifacts / 'advanced-before.png'), full_page=True)
    page.locator('canvas.lg-canvas').evaluate('element=>{window.__editingCanvas=element;window.__editingGraph=element.data.graph}')
    def choose_native_path(node_id):
        point = page.locator('canvas.lg-canvas').evaluate('''(element,id)=>{
          const canvas=element.data,node=canvas.graph.getNodeById(id);
          const widget=node.widgets.find(widget=>widget.__nativePathPicker);
          if(!widget)throw Error('Missing native path picker');
          const rect=element.getBoundingClientRect();
          const point=canvas.ds.convertOffsetToCanvas([node.pos[0]+node.size[0]/2,node.pos[1]+widget.y+widget.height/2]);
          return [rect.left+point[0],rect.top+point[1]];
        }''', node_id)
        page.mouse.click(*point)
        expect(page.locator('.n-drawer')).to_have_count(0)

    page.evaluate('(value)=>{window.__inputFiles=[value.file];window.__inputFolder=value.folder}', {'file':str(inputs / 'one.wav'),'folder':str(inputs)})
    choose_native_path(1)
    choose_native_path(2)
    page.wait_for_function('(folder)=>document.querySelector("canvas.lg-canvas").data.graph.getNodeById(2).widgets.find(widget=>widget.name==="folder").value===folder', arg=str(inputs))
    assert page.evaluate('window.__nativePickerCalls') == ['pick_single_audio_file','pick_input_folder']
    page.locator('[data-workbench-run-panel]').get_by_role('button', name='运行', exact=True).click()
    page.wait_for_function('window.__workerPayloads.length===2')
    advanced = page.evaluate('window.__workerPayloads[1]')
    assert advanced['input'] is None
    assert not any(key in advanced for key in ['inputs','tasks','workbench','workbenchOutputs'])
    assert page.locator('.workflow-node-editor-page > .litegraph-editor canvas').evaluate('element=>element===window.__editingCanvas && element.data.graph===window.__editingGraph')
    native_nodes = {str(node['id']): node for node in advanced['workflow']['nodes']}
    assert native_nodes['1']['widgets_values'][:2]==[str(inputs / 'one.wav'),''], native_nodes['1']
    assert native_nodes['2']['widgets_values']==[str(inputs),True,False,''], native_nodes['2']
    authored=page.evaluate("async()=>{const {useTaskStore}=await import('/src/stores/task.ts');return useTaskStore().tasks.find(task=>task.runConfig?.workflowId==='stereo').runConfig.workbench.sourceDefinition}")
    assert authored == advanced['workflow']
    main_canvas = page.locator('.workflow-node-editor-page > .litegraph-editor canvas')
    before_position = main_canvas.evaluate('element=>[...element.data.graph.getNodeById(1).pos]')
    drag_point = main_canvas.evaluate('''element=>{
      const canvas=element.data,node=canvas.graph.getNodeById(1),rect=element.getBoundingClientRect();
      const point=canvas.ds.convertOffsetToCanvas([node.pos[0]+node.size[0]/2,node.pos[1]-12]);
      return [rect.left+point[0],rect.top+point[1]];
    }''')
    page.mouse.move(*drag_point)
    page.mouse.down()
    page.mouse.move(drag_point[0]+40,drag_point[1]+25,steps=8)
    page.mouse.up()
    page.wait_for_timeout(250)
    moved_position = main_canvas.evaluate('element=>[...element.data.graph.getNodeById(1).pos]')
    assert moved_position != before_position, (before_position, moved_position)
    main_toolbar = page.locator('.workflow-node-editor-page > .litegraph-editor > .toolbar')
    expect(main_toolbar.get_by_role('button',name='撤销',exact=True)).to_be_enabled()
    canvas_state = main_canvas.evaluate('element=>({scale:element.data.ds.scale,offset:[...element.data.ds.offset],selected:Object.keys(element.data.selected_nodes)})')
    payload_file.write_text(json.dumps(advanced, ensure_ascii=False), encoding='utf-8')
    result = subprocess.run([sys.executable, str(root / 'python/worker.py'), 'infer_workflow', '--payload', str(payload_file)], capture_output=True, text=True, encoding='utf-8', env=env, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    advanced_events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    for event in advanced_events:
        page.evaluate("async(event)=>{const {useTaskStore}=await import('/src/stores/task.ts');useTaskStore().handleWorkerEvent(event)}", event)
    completed = next(event for event in advanced_events if event['type'] == 'task.done')
    assert len(completed['payload']['outputs']) == 3
    paths = [Path(output['path']) for output in completed['payload']['outputs']]
    assert sum(path.parent == artifacts / 'output/single' for path in paths) == 1
    assert sum(path.parent == artifacts / 'output/batch' for path in paths) == 2
    for path in paths:
        audio, rate = sf.read(path,always_2d=True)
        assert rate == 44100 and audio.shape == (4410,2) and np.isfinite(audio).all()
    page.locator('[data-workbench-run-panel]').get_by_role('button', name='日志与结果').click()
    expect(page.locator('[data-workflow-progress]')).not_to_have_count(0)
    expect(page.locator('.run-details .log-console')).to_be_visible()
    expect(page.locator('.run-details canvas')).to_have_count(0)
    page.locator('.workflow-run-details-modal .n-tabs-tab').filter(has_text='结果 · 3').click()
    expect(page.locator('.run-details .result-track')).to_have_count(3)
    page.wait_for_timeout(250)
    page.screenshot(path=str(artifacts / 'advanced-results.png'), full_page=True)
    page.locator('.workflow-run-details-modal .n-tabs-tab').filter(has_text='节点执行').click()
    expect(page.locator('.run-details .run-node-detail')).to_have_count(4)
    page.keyboard.press('Escape')
    expect(page.locator('[data-workbench-details]')).to_have_count(0)
    expect(page.locator('.workflow-node-editor-page .workflow-node-log')).to_have_count(0)
    assert page.locator('.workflow-node-editor-page > .litegraph-editor canvas').evaluate('element=>element===window.__editingCanvas && element.data.graph===window.__editingGraph')
    assert main_canvas.evaluate('element=>({scale:element.data.ds.scale,offset:[...element.data.ds.offset],selected:Object.keys(element.data.selected_nodes)})') == canvas_state
    assert main_canvas.evaluate('element=>[...element.data.graph.getNodeById(1).pos]') == moved_position
    main_toolbar.get_by_role('button',name='撤销',exact=True).click()
    assert main_canvas.evaluate('element=>[...element.data.graph.getNodeById(1).pos]') == before_position
    main_toolbar.get_by_role('button',name='重做',exact=True).click()
    assert main_canvas.evaluate('element=>[...element.data.graph.getNodeById(1).pos]') == moved_position
    choose_native_path(1)
    assert main_canvas.evaluate('element=>element.data.graph.getNodeById(1).widgets.find(widget=>widget.name==="audio").value') == str(inputs / 'one.wav')
    invariants = page.evaluate("""async(files)=>{
      const {useTaskStore}=await import('/src/stores/task.ts');
      const {useSettingsStore}=await import('/src/stores/settings.ts');
      const {useWorkflowStore}=await import('/src/stores/workflow.ts');
      const task=useTaskStore(), settings=useSettingsStore();
      const original=task.tasks.find(item=>item.runConfig?.workflowId==='stereo');
      const captured=JSON.stringify(original.runConfig);
      settings.outputDir='D:/changed-default'; settings.defaultFormat='flac';
      const retry=await task.retryTask(original.id);
      if(JSON.stringify(retry.runConfig)!==captured) throw Error('Retry changed its frozen configuration');
      await task.cancelWorkbenchJob(retry.jobId||retry.id);
      const flow=useWorkflowStore().workflows.find(flow=>flow.id==='phase');
      const binding={inputs:{input:{files}},outputs:{},primaryInput:'input',outputLayout:'flat'};
      const active=await task.startWorkbenchInference(flow,binding);
      const queued=await task.startWorkbenchInference(flow,binding);
      const before=window.__workerPayloads.length;
      if(queued.tasks.some(item=>item.status!=='queued'))throw Error('Expected a queued batch');
      await task.cancelWorkbenchJob(queued.jobId);
      if(queued.tasks.some(item=>item.status!=='cancelled'))throw Error('Not all queued files were cancelled');
      if(window.__workerPayloads.length!==before)throw Error('Cancellation started a remaining file');
      await task.cancelWorkbenchJob(active.jobId);
      if(active.tasks.some(item=>item.status!=='cancelled'))throw Error('Not all active files were cancelled');
      return {retryFrozen:true,queuedBatchCancelled:true,activeBatchCancelled:true};
    }""", [str(inputs / 'one.wav'), str(inputs / 'two.wav')])
    print('INVARIANTS', json.dumps(invariants), flush=True)
    print(json.dumps({'events': len(events), 'completed': 2, 'errors': errors, 'payload': str(payload_file)}, ensure_ascii=False))
    assert not errors, errors
    browser.close()
