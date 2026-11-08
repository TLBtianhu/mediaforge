"""Validate batch logs, node summaries, retry and result-source filters in the UI."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:1448')
parser.add_argument('--output-dir', type=Path, default=Path('.tmp/workflow-batch-details'))
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
definition = {'version': 1, 'defaults': {'device': 'auto', 'output_format': 'wav'}, 'steps': [], 'ensembles': [{
    'id': 'phase', 'algorithm': 'invert', 'output_stem': 'Inverted', 'save': 'Default',
    'output_name': '%filename%_%stem%_%step%', 'inputs': [{'source': 'input', 'weight': 1}],
}]}
entry = {'id': 'batch-workflow', 'name': '音频处理', 'description': '', 'definition': definition,
         'format': 'simple', 'formatVersion': 1, 'createdAt': 1, 'updatedAt': 1}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=['--no-proxy-server'])
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.set_default_timeout(8000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    data = {'app-settings': {'version': 1, 'startupOnboardingSeen': True, 'locale': 'zh-CN', 'defaultDevice': 'cpu', 'outputDir': 'D:/outputs'},
            'workflow-state': {'selectedWorkflowId': entry['id'], 'workflows': [entry]}}
    page.add_init_script('for (const [name,value] of Object.entries(' + json.dumps(data) + ')) localStorage.setItem("pymss-studio:"+name,JSON.stringify(value));')
    page.goto(args.url + '/#/workflow-simple-editor?workflowId=batch-workflow')
    page.wait_for_load_state('networkidle')
    page.add_style_tag(content='.title-bar { display: none !important; }')
    page.evaluate('''definition => {
      const stores = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s;
      window.__batchStores = stores;
      const binding = {inputs:{},outputs:{},primaryInput:'',outputLayout:'flat'};
      const base = {jobId:'batch',model:'音频处理',output:'D:/outputs',message:'Done',createdAt:Date.now(),updatedAt:Date.now(),
        runConfig:{runMode:'workflow',workflowId:'batch-workflow',workflowName:'音频处理',workflowDefinition:definition,
          workbench:{version:1,sourceUpdatedAt:1,sourceDefinition:definition,bindings:binding,inputs:{},outputs:{'studio:ensemble-save:phase':'D:/outputs'}}},
      };
      const make = (id, status, progress) => ({...JSON.parse(JSON.stringify(base)), id, resultId:id, input:'D:/Music/'+id+'.wav',
        status, progress, logs:['info: '+id+' task log'], files:['D:/outputs/'+id+'.wav'],outputs:[{stem:'Inverted',path:'D:/outputs/'+id+'.wav'}],
        nodeStates:{input:{status:'done',progress:100},'studio:ensemble:phase':{status:status==='failed'?'failed':'done',progress},'studio:ensemble-save:phase':{status:'done',progress:100}},
        nodeLogs:{'studio:ensemble:phase':['info: '+id+' node log']},...(status==='failed'?{error:'Unable to process first.wav'}:{}),
      });
      const tasks=[make('first','failed',40),make('second','done',100),make('third','done',100)];
      tasks[0].outputs=[];tasks[0].files=[];delete tasks[0].nodeStates['studio:ensemble-save:phase'];
      stores.get('task').tasks=tasks;
      const session=stores.get('workbench').session('batch-workflow');session.selectedJobId='batch';session.selectedTaskId='third';session.bindings=binding;
    }''', definition)
    # The worker dispatch order and displayed order must agree for equal timestamps.
    original_tasks = page.evaluate('''() => {
      const store=window.__batchStores.get('task');
      const previous=store.tasks;
      const base=JSON.parse(JSON.stringify(previous[1]));
      const first={...base,id:'z',input:'D:/Music/z.wav',batchIndex:0,createdAt:100,status:'separating',progress:35,
        nodeStates:{input:{status:'done',progress:100},'studio:ensemble:phase':{status:'running',progress:35}}};
      const second={...base,id:'a',input:'D:/Music/a.wav',batchIndex:1,createdAt:100,status:'preparing',progress:0,nodeStates:{}};
      store.tasks=[second,first];
      return previous;
    }''')
    expect(page.locator('.simple-node-editor__topbar .workflow-execution-progress__file')).to_have_text('z.wav')
    expect(page.locator('.simple-node[data-node-status="running"]')).to_have_attribute('data-simple-node', 'phase')
    assert page.evaluate("window.__batchStores.get('task').allJobs[0].tasks.map(task=>task.id)") == ['z', 'a']
    page.screenshot(path=str(args.output_dir / 'batch-order-running.png'), animations='disabled')
    page.evaluate("tasks=>{window.__batchStores.get('task').tasks=tasks}", original_tasks)
    toolbar = page.locator('[data-workbench-run-panel]')
    assert [text.strip() for text in toolbar.get_by_role('button').all_text_contents()] == ['日志与结果', '运行']
    # A completed third file must not hide the first file's failed node on the canvas.
    expect(page.locator('.simple-editor-page > .simple-node-editor [data-node-log="phase"]')).to_have_attribute('data-node-status', 'failed')
    toolbar.get_by_role('button', name='日志与结果', exact=True).click()
    modal = page.locator('.workflow-run-details-modal')
    expect(modal.locator('.run-detail-selection')).to_have_count(0)
    expect(modal.locator('.run-failure')).to_have_count(1)
    for name in ['first', 'second', 'third']:
        expect(modal.locator('.log-console')).to_contain_text(name + '.wav')
        expect(modal.locator('.log-console')).to_contain_text(name + ' task log')
    page.screenshot(path=str(args.output_dir / 'batch-logs.png'), animations='disabled')
    modal.locator('.n-tabs-tab').filter(has_text='节点执行').click()
    node = modal.locator('[data-node-log="phase"]')
    expect(node).to_have_attribute('data-node-status', 'failed')
    expect(node.locator('summary')).to_contain_text('已完成 2/3')
    expect(node.locator('summary')).to_contain_text('失败 1')
    node.locator('summary').click()
    for name in ['first', 'second', 'third']:
        expect(node.locator('.log-console')).to_contain_text(name + '.wav')
        expect(node.locator('.log-console')).to_contain_text(name + ' node log')
    node.get_by_role('button', name='放大查看', exact=True).click()
    expect(page.locator('.workflow-node-log-modal')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('.workflow-node-log-modal')).not_to_be_visible()
    expect(modal).to_be_visible()
    page.screenshot(path=str(args.output_dir / 'batch-nodes.png'), animations='disabled')
    modal.locator('.n-tabs-tab').filter(has_text='结果 · 2').click()
    expect(modal.locator('.result-output-group')).to_have_count(2)
    expect(modal.locator('.result-output-group h4')).to_have_text(['second.wav', 'third.wav'])
    page.screenshot(path=str(args.output_dir / 'batch-results.png'), animations='disabled')
    modal.locator('.run-failure').get_by_role('button', name='重试', exact=True).click()
    expect(modal.locator('.run-failure').get_by_role('button', name='查看重试', exact=True)).to_be_visible()
    assert page.evaluate("window.__batchStores.get('task').tasks.length") == 4
    retry = page.evaluate("JSON.parse(JSON.stringify(window.__batchStores.get('task').tasks[0]))")
    assert retry['input'] == 'D:/Music/first.wav'
    assert retry['runConfig']['workbench']['sourceDefinition'] == definition
    # Original batch remains visible and keeps its failure after submitting a retry.
    expect(modal.locator('.run-failure')).to_contain_text('first.wav')
    expect(modal.locator('.run-detail-selection')).to_have_count(1)
    page.keyboard.press('Escape')
    # Running canvas follows the active file regardless of an old per-file selection.
    page.evaluate('''() => {
      const task=window.__batchStores.get('task').tasks.find(item=>item.id==='second');
      Object.assign(task, {status:'separating',progress:35});
      task.nodeStates={'input':{status:'done',progress:100},'studio:ensemble:phase':{status:'running',progress:35}};
    }''')
    expect(page.locator('.simple-node-editor__topbar .workflow-execution-progress__file')).to_have_text('second.wav')
    assert [text.strip() for text in toolbar.get_by_role('button').all_text_contents()] == ['日志与结果', '取消任务', '运行']
    expect(modal).not_to_be_visible()
    save_log = page.locator('[data-simple-node="save"] [data-node-log="save"]')
    save_log.locator('summary').click()
    expect(save_log.get_by_role('button', name='复制日志', exact=True)).to_be_disabled()
    expect(save_log.get_by_role('button', name='放大查看', exact=True)).to_be_disabled()
    save_log.locator('summary').click()
    running_node = page.locator('.simple-node[data-node-status="running"]')
    expect(running_node).to_have_count(1)
    expect(running_node).to_have_attribute('data-simple-node', 'phase')
    expect(running_node.locator('.simple-execution-state')).to_have_text('执行中')
    phase_bounds = running_node.bounding_box()
    # Log text, optional count and SVG chevron share a vertical center at every node.
    for label in page.locator('.simple-node .workflow-node-log__label').all():
        centers = label.evaluate('el=>Array.from(el.children).map(child=>{const r=child.getBoundingClientRect();return r.y+r.height/2})')
        assert max(centers) - min(centers) < 1, centers
    page.screenshot(path=str(args.output_dir / 'toolbar-running.png'), animations='disabled')
    page.evaluate("window.__batchStores.get('settings').themeMode='dark'")
    expect(page.locator('html')).to_have_class('dark-theme')
    page.screenshot(path=str(args.output_dir / 'nodes-running-dark.png'), animations='disabled')
    # Keyboard expansion stays local to the log; collapsing restores the node geometry.
    summary = running_node.locator('summary')
    summary.focus()
    page.keyboard.press('Enter')
    expect(running_node.locator('details')).to_have_attribute('open', '')
    page.evaluate('''() => {
      const task=window.__batchStores.get('task').tasks.find(item=>item.id==='second');
      task.nodeLogs['studio:ensemble:phase'].push('[info] Model path: D:/models/vocal/'+'melband_roformer_'.repeat(10)+'weights.ckpt', '[warning] 保留完整参数和路径');
      Object.defineProperty(navigator, 'clipboard', {configurable:true,value:{writeText:async text=>{
        if(window.__rejectLogCopy) throw new Error('Clipboard unavailable');
        window.__copiedNodeLogs=text;
      }}});
    }''')
    compact_log = running_node.locator('.log-console')
    expect(compact_log.locator('.log-line-number, .log-line-level')).to_have_count(0)
    assert compact_log.evaluate("el=>getComputedStyle(el).userSelect==='text' && el.scrollWidth>el.clientWidth")
    assert abs(running_node.bounding_box()['width'] - phase_bounds['width']) < 1
    running_node.get_by_role('button', name='复制日志', exact=True).click()
    expected_logs = page.evaluate("window.__batchStores.get('task').tasks.find(item=>item.id==='second').nodeLogs['studio:ensemble:phase'].join(String.fromCharCode(10))")
    assert page.evaluate('window.__copiedNodeLogs') == expected_logs
    page.screenshot(path=str(args.output_dir / 'node-logs-compact-dark.png'), animations='disabled')
    running_node.get_by_role('button', name='放大查看', exact=True).click()
    enlarged = page.locator('.workflow-node-log-modal')
    expect(enlarged).to_be_visible()
    expect(enlarged).to_contain_text('节点日志 · 音频反相 1')
    expect(enlarged.locator('.log-line-level')).not_to_have_count(0)
    page.evaluate("window.__batchStores.get('task').tasks.find(item=>item.id==='second').nodeLogs['studio:ensemble:phase'].push('[info] New node log')")
    expect(enlarged.locator('.log-console')).to_contain_text('New node log')
    enlarged.get_by_role('button', name='复制日志', exact=True).click()
    assert page.evaluate('window.__copiedNodeLogs') == expected_logs + '\n[info] New node log'
    page.screenshot(path=str(args.output_dir / 'node-logs-expanded-dark.png'), animations='disabled')
    page.evaluate('window.__rejectLogCopy=true')
    enlarged.get_by_role('button', name='复制日志', exact=True).click()
    expect(page.locator('.n-message').filter(has_text='复制失败')).to_be_visible()
    page.evaluate('window.__rejectLogCopy=false')
    page.keyboard.press('Escape')
    expect(enlarged).not_to_be_visible()
    expect(running_node.locator('details')).to_have_attribute('open', '')
    page.evaluate("window.__batchStores.get('settings').themeMode='light'")
    expect(page.locator('html')).to_have_class('light-theme')
    page.screenshot(path=str(args.output_dir / 'node-logs-compact-light.png'), animations='disabled')
    summary.focus()
    page.keyboard.press('Space')
    expect(running_node.locator('details')).not_to_have_attribute('open', '')
    page.evaluate('''() => {
      const task=window.__batchStores.get('task').tasks.find(item=>item.id==='second');
      task.nodeStates['studio:ensemble:phase']={status:'done',progress:100};
      task.nodeStates['studio:ensemble-save:phase']={status:'running',progress:40};
    }''')
    expect(running_node).to_have_attribute('data-simple-node', 'save')
    next_bounds = page.locator('[data-simple-node="phase"]').bounding_box()
    assert all(abs(phase_bounds[key] - next_bounds[key]) < 1 for key in phase_bounds)
    page.evaluate('''() => {
      const task=window.__batchStores.get('task').tasks.find(item=>item.id==='second');
      task.status='done';task.progress=100;task.nodeStates['studio:ensemble-save:phase']={status:'done',progress:100};
    }''')
    expect(running_node).to_have_count(0)
    page.evaluate("window.__batchStores.get('settings').themeMode='light'")

    page.evaluate('''() => {
      const base=JSON.parse(JSON.stringify(window.__batchStores.get('task').tasks.find(item=>item.id==='second')));
      const graph={nodes:[],links:[],extra:{studioEnsemble:{outputStem:'Vocals'}}};
      const kinds=[['model',{runMode:'model'}],['ensemble',{runMode:'workflow',workflowId:'temporary-ensemble',workflowDefinition:graph}],
        ['simple',base.runConfig],['advanced',{runMode:'workflow',workflowDefinition:graph,workbench:{sourceDefinition:graph,nativeGraph:true}}]];
      window.__batchStores.get('task').tasks=kinds.map(([kind,runConfig],index)=>({...JSON.parse(JSON.stringify(base)),id:kind,jobId:kind,resultId:kind,
        createdAt:Date.now()+index,updatedAt:Date.now()+index,status:'done',input:'D:/Music/'+kind+'.wav',runConfig,
      }));
      location.hash='/results';
    }''')
    page.wait_for_load_state('networkidle')
    expect(page.get_by_role('heading', name='处理结果', exact=True)).to_be_visible()
    expect(page.locator('.result-row')).to_have_count(4)
    for label in ['单模型', 'Ensemble', '简易工作流', '高级工作流']:
        page.locator('.results-toolbar__type').click()
        page.locator('.n-base-select-option').filter(has_text=label).click()
        expect(page.locator('.result-row')).to_have_count(1)
        expect(page.locator('.result-row__type')).to_have_text(label)
    page.locator('.results-toolbar__search input').fill('no matching input')
    expect(page.locator('.result-row')).to_have_count(0)
    page.locator('.results-toolbar__search input').fill('advanced')
    expect(page.locator('.result-row')).to_have_count(1)
    page.screenshot(path=str(args.output_dir / 'results-type-filter.png'), animations='disabled')
    page.locator('.results-toolbar__search input').fill('')
    page.locator('.results-toolbar__type').click()
    page.locator('.n-base-select-option').filter(has_text='全部类型').click()
    for width in [1440, 1100, 960]:
        page.set_viewport_size({'width': width, 'height': 900})
        assert page.locator('.results-toolbar').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
        page.screenshot(path=str(args.output_dir / f'results-{width}.png'), animations='disabled')
    assert not errors, errors
    browser.close()
    print('PASS: batch logs/results, retry, active nodes, compact/expanded logs, clipboard success/failure, modal focus, empty logs, source filters and search')
