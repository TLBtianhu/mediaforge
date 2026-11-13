"""Exercise workflow result controls with real browser audio and isolated stores."""

import argparse
import io
import json
import math
import re
import struct
import wave
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:1448')
parser.add_argument('--output-dir', type=Path, default=Path('.tmp/workflow-result-preview'))
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
audio_bytes = io.BytesIO()
with wave.open(audio_bytes, 'wb') as audio_file:
    audio_file.setnchannels(1)
    audio_file.setsampwidth(2)
    audio_file.setframerate(22050)
    audio_file.writeframes(b''.join(struct.pack('<h', int(math.sin(index * 2 * math.pi * 220 / 22050) * 1200)) for index in range(22050 * 12)))

range_responses = []


def serve_preview_audio(route):
    if 'missing' in route.request.url:
        route.fulfill(status=404, body='')
        return
    data = audio_bytes.getvalue()
    size = len(data)
    status = 200
    headers = {'Accept-Ranges': 'bytes'}
    requested_range = route.request.headers.get('range')
    if requested_range:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested_range)
        if not match or not any(match.groups()):
            route.fulfill(status=416, headers={'Content-Range': f'bytes */{size}'}, body='')
            return
        left, right = match.groups()
        start = int(left) if left else max(0, size - int(right))
        end = min(int(right), size - 1) if left and right else size - 1
        if start > end:
            route.fulfill(status=416, headers={'Content-Range': f'bytes */{size}'}, body='')
            return
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        data = data[start:end + 1]
        status = 206
        range_responses.append((start, end))
    headers['Content-Length'] = str(len(data))
    route.fulfill(status=status, content_type='audio/wav', headers=headers, body=data)


definition = {'version': 1, 'steps': [], 'ensembles': [{
    'id': 'phase', 'algorithm': 'invert', 'output_stem': 'Inverted', 'save': 'Default',
    'inputs': [{'source': 'input', 'weight': 1}],
}]}
workflow = {'id': 'result-preview', 'name': '人声提取', 'description': '', 'definition': definition,
            'format': 'simple', 'formatVersion': 1, 'createdAt': 1, 'updatedAt': 1}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=['--no-proxy-server'])
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.set_default_timeout(8000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/preview-audio/**', serve_preview_audio)
    page.add_init_script('localStorage.setItem("pymss-studio:app-settings", JSON.stringify(' + json.dumps({
        'version': 1, 'startupOnboardingSeen': True, 'locale': 'zh-CN', 'themeMode': 'light', 'outputDir': 'D:/Music/outputs',
    }) + ')); localStorage.setItem("pymss-studio:workflow-state", JSON.stringify(' + json.dumps({
        'selectedWorkflowId': workflow['id'], 'workflows': [workflow],
    }, ensure_ascii=False) + '));')
    page.goto(args.url + '/#/workflow-simple-editor?workflowId=result-preview', timeout=60000)
    page.wait_for_load_state('networkidle')
    # Standalone editors use native window chrome; the browser fixture owns tasks as main.
    page.add_style_tag(content='.title-bar { display: none !important; }')
    page.evaluate('''async definition => {
      const NativeAudio = window.Audio;
      window.__previewAudios = [];
      window.Audio = function(...args) { const audio = new NativeAudio(...args); window.__previewAudios.push(audio); return audio; };
      window.Audio.prototype = NativeAudio.prototype;
      window.__revealed = [];
      window.__TAURI_EVENT_PLUGIN_INTERNALS__ = {unregisterListener() {}};
      window.__TAURI_INTERNALS__ = {
        metadata: {currentWindow: {label: 'main'}, currentWebview: {label: 'main'}},
        transformCallback: () => 1,
        convertFileSrc: path => location.origin + '/preview-audio/' + encodeURIComponent(path),
        invoke: async (command, args) => {
          if (command === 'reveal_path') { window.__revealed.push(args.path); return; }
          if (command === 'save_app_store') { localStorage.setItem('pymss-studio:' + args.name, JSON.stringify(args.data)); return; }
          if (command === 'load_app_store') return JSON.parse(localStorage.getItem('pymss-studio:' + args.name) || 'null');
          if (command.startsWith('plugin:')) return 1;
          throw new Error('Unexpected command: ' + command);
        },
      };
      const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
      const useTaskStore = () => pinia._s.get('task');
      const useWorkbenchStore = () => pinia._s.get('workbench');
      const outputs = [
        {stem: 'Vocals', path: 'D:/Music/outputs/晨间现场_Vocals_melband_roformer_instvox_duality_v2_4.wav'},
        {stem: 'Instrumental', path: 'D:/Music/outputs/晨间现场_Instrumental_melband_roformer_instvox_duality_v2_4.wav'},
      ];
      window.__originalOutputs = outputs;
      const task = { id: 'preview-task', jobId: 'preview-job', resultId: 'preview-result', model: '人声提取',
        input: 'D:/Music/晨间现场.wav', output: 'D:/Music/outputs', status: 'done', message: 'Done',
        createdAt: Date.now(), updatedAt: Date.now(), progress: 100, files: outputs.map(item => item.path), outputs,
        logs: ['info: Processing completed'], runConfig: {runMode: 'workflow', workflowId: 'result-preview', workflowDefinition: definition,
          workbench: {version: 1, sourceUpdatedAt: 1, sourceDefinition: definition,
            bindings: {inputs: {}, outputs: {}, primaryInput: '', outputLayout: 'flat'}, inputs: {}, outputs: {}}},
      };
      window.__previewTasks = useTaskStore();
      window.__previewWorkbench = useWorkbenchStore();
      window.__previewTasks.tasks = [task, {...structuredClone(task), id: 'preview-second', input: 'D:/Music/晚间现场.wav', outputs: [{stem: 'Vocals', path: 'D:/Music/outputs/晚间现场_Vocals.wav'}]}];
      window.__previewWorkbench.session('result-preview').selectedJobId = 'preview-job';
      window.__previewWorkbench.session('result-preview').selectedTaskId = 'preview-task';
    }''', definition)
    page.get_by_role('button', name='日志与结果', exact=True).click()
    modal = page.locator('.workflow-run-details-modal')
    modal.locator('.n-tabs-tab').filter(has_text='结果 · 3').click()
    tracks = modal.locator('.result-track')
    expect(tracks).to_have_count(3)
    expect(modal.locator('.run-detail-selection')).to_have_count(0)
    expect(modal.locator('.result-output-group')).to_have_count(2)
    expect(tracks.first.locator('.result-track__time')).to_have_text('00:00 / 00:12')
    expect(modal.locator('audio[controls]')).to_have_count(0)
    first_name = page.evaluate("window.__originalOutputs[0].path.split('/').pop()")
    second_name = page.evaluate("window.__originalOutputs[1].path.split('/').pop()")
    handle = tracks.first.get_by_role('slider', name='播放进度 · ' + first_name, exact=True)
    expect(handle).to_be_visible()
    expect(tracks.nth(1).get_by_role('slider', name='播放进度 · ' + second_name, exact=True)).to_be_visible()
    page.evaluate("document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('settings').locale = 'en'")
    expect(tracks.first.get_by_role('slider', name='Playback position · ' + first_name, exact=True)).to_be_visible()
    page.evaluate("document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('settings').locale = 'zh-CN'")
    expect(handle).to_be_visible()
    page.screenshot(path=str(args.output_dir / 'results-light-1440.png'), animations='disabled')
    tracks.first.locator('.result-track__play').click()
    expect(tracks.first).to_have_class('result-track result-track--playing')
    page.wait_for_function('window.__previewAudios.at(-1).currentTime > .2')
    tracks.first.locator('.result-track__play').click()
    assert page.evaluate('window.__previewAudios.at(-1).paused')
    assert range_responses, 'The browser must exercise byte-range audio responses'
    bounds = tracks.first.locator('.n-slider-rail').bounding_box()
    thumb = handle.bounding_box()
    page.mouse.move(thumb['x'] + thumb['width'] / 2, thumb['y'] + thumb['height'] / 2)
    page.mouse.down()
    page.mouse.move(bounds['x'] + bounds['width'] * .55, bounds['y'] + bounds['height'] / 2, steps=6)
    page.mouse.up()
    page.wait_for_function('''() => {
      const audio = window.__previewAudios.at(-1);
      return audio.paused && !audio.seeking && Math.abs(audio.currentTime - 6.6) < .2;
    }''', timeout=1000)
    position = page.evaluate('window.__previewAudios.at(-1).currentTime')
    handle.focus()
    handle.press('ArrowRight')
    page.wait_for_function('''position => {
      const audio = window.__previewAudios.at(-1);
      return audio.paused && !audio.seeking && Math.abs(audio.currentTime - position - .1) < .03;
    }''', arg=position, timeout=1000)
    tracks.nth(2).locator('.result-track__play').click()
    expect(tracks.nth(2)).to_have_class('result-track result-track--playing')
    assert page.evaluate('window.__previewAudios.filter(audio => !audio.paused).length') == 1
    assert page.evaluate('window.__previewAudios[0].getAttribute("src")') is None
    tracks.nth(2).locator('.result-track__play').click()
    assert page.evaluate('window.__previewAudios.every(audio => audio.paused)')
    tracks.first.locator('.result-track__open').click()
    modal.locator('.result-folder button').click()
    assert page.evaluate('window.__revealed') == [
        'D:/Music/outputs/晨间现场_Vocals_melband_roformer_instvox_duality_v2_4.wav', 'D:/Music/outputs']
    for width in [960, 760]:
        page.set_viewport_size({'width': width, 'height': 900})
        assert tracks.evaluate_all('items => items.every(item => item.scrollWidth <= item.clientWidth + 1)')
        page.screenshot(path=str(args.output_dir / f'results-light-{width}.png'), animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.evaluate("document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('settings').themeMode = 'dark'")
    page.wait_for_function("parseInt(getComputedStyle(document.querySelector('.workflow-run-details-modal')).backgroundColor.match(/[0-9]+/)[0]) < 100")
    page.screenshot(path=str(args.output_dir / 'results-dark-1440.png'), animations='disabled')
    tracks.first.locator('.result-track__play').click()
    modal.locator('.n-tabs-tab').filter(has_text='日志').click()
    assert page.evaluate('window.__previewAudios.every(audio => audio.paused && !audio.getAttribute("src"))')
    modal.locator('.n-tabs-tab').filter(has_text='结果 · 3').click()
    tracks.first.locator('.result-track__play').click()
    page.evaluate("window.__previewWorkbench.session('result-preview').selectedTaskId = 'preview-second'")
    expect(tracks).to_have_count(3)
    # Legacy per-file selection must not filter batch results or interrupt playback.
    expect(tracks.first).to_have_class('result-track result-track--playing')
    page.evaluate("window.__previewTasks.tasks = [window.__previewTasks.tasks[0]]")
    page.evaluate("window.__previewTasks.tasks[0].outputs = [{stem: 'Vocals', path: 'D:/Music/outputs/missing.wav'}]")
    expect(modal.locator('.result-track__error')).to_have_text('音频播放失败')
    page.evaluate("window.__previewTasks.tasks[0].outputs = []")
    expect(modal.get_by_text('尚未生成输出文件')).to_be_visible()
    page.evaluate('window.__previewTasks.tasks[0].outputs = window.__originalOutputs')
    tracks.first.locator('.result-track__play').click()
    page.keyboard.press('Escape')
    expect(modal).not_to_be_visible()
    assert page.evaluate('window.__previewAudios.every(audio => audio.paused && !audio.getAttribute("src"))')
    assert not errors, errors
    print('PASS: byte-range audio, named sliders and locale updates, paused drag/keyboard seeking, single active player, file/folder actions, responsive layout, dark theme, tab/task/close cleanup, missing audio and empty results')
    browser.close()
