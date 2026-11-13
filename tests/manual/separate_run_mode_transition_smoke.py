"""Headless UI smoke for the Separate page model/workflow transition."""

import json

from playwright.sync_api import expect, sync_playwright


model_state = {
    "models": [{
        "name": "model-a.ckpt",
        "aliases": [],
        "modelType": "mss",
        "architecture": "mel_band_roformer",
        "supported": True,
        "unsupportedReason": "",
        "category": "vocal",
        "categoryCn": "人声",
        "primaryCategory": "vocal",
        "primaryCategoryCn": "人声",
        "secondaryCategory": "",
        "secondaryCategoryCn": "",
        "targetStem": "Vocals",
        "configInstruments": "Vocals,Instrumental",
        "configTargetInstrument": "Vocals",
        "classificationConfidence": "high",
        "classificationBasis": "catalog",
        "sizeBytes": 1,
        "sha256": "",
        "downloaded": True,
        "missingPaths": [],
        "modelPath": "D:/Models/model-a.ckpt",
        "configPath": None,
        "auxiliaryPaths": [],
    }],
    "categories": ["vocal"],
    "categoriesCn": ["人声"],
    "count": 1,
    "modelDir": "D:/Models",
}
workflow_state = {
    "selectedWorkflowId": "simple",
    "workflows": [{
        "id": "simple",
        "name": "Simple vocal split",
        "description": "Transition smoke fixture.",
        "definition": {
            "version": 1,
            "defaults": {"device": "auto", "output_format": "wav"},
            "steps": [{
                "id": "step1",
                "model": "model-a.ckpt",
                "input": "input",
                "stems": ["Vocals", "Instrumental"],
                "save": {"Vocals": "Default"},
                "output_names": {"Vocals": "%filename%_%stem%_%model%"},
            }],
        },
        "format": "simple",
        "formatVersion": 1,
        "createdAt": 1,
        "updatedAt": 1,
    }],
}


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(
        f"""(() => {{
          localStorage.setItem(
            'pymss-studio:app-settings',
            JSON.stringify({{ version: 1, startupOnboardingSeen: true, locale: 'en' }}),
          )
          localStorage.setItem('pymss-studio:model-state', JSON.stringify({json.dumps(model_state)}))
          localStorage.setItem('pymss-studio:workflow-state', JSON.stringify({json.dumps(workflow_state)}))
        }})()"""
    )
    page.goto("http://localhost:1420/#/")
    page.wait_for_load_state("networkidle")

    expect(page.locator(".target-pane")).to_be_visible()
    expect(page.locator(".mode-switch")).to_contain_text("Single Model")
    page.evaluate(
        """(() => {
          window.__stageTransitionClasses = []
          const root = document.querySelector('.console__stage')
          const remember = node => {
            if (!(node instanceof Element)) return
            const value = node.getAttribute('class') || ''
            if (value.includes('stage-swap-')) window.__stageTransitionClasses.push(value)
          }
          new MutationObserver(records => {
            records.forEach(record => {
              remember(record.target)
              record.addedNodes.forEach(remember)
            })
          }).observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] })
        })()"""
    )

    page.locator(".mode-switch").get_by_text("Workflow", exact=True).click()
    page.wait_for_timeout(700)

    transition_classes = page.evaluate("window.__stageTransitionClasses")
    assert any("stage-swap-leave-active" in value for value in transition_classes), transition_classes
    assert any("stage-swap-enter-active" in value for value in transition_classes), transition_classes
    expect(page.locator(".target-toolbar--single")).to_be_visible()
    expect(page.locator(".target-pane")).to_contain_text("Simple vocal split")
    assert not errors, errors

    page.screenshot(path="data/outputs/separate-run-mode-transition.png", full_page=True)

    page.set_viewport_size({"width": 1100, "height": 760})
    page.wait_for_timeout(120)
    grid_columns = page.locator(".console").evaluate("node => getComputedStyle(node).gridTemplateColumns")
    assert len(grid_columns.split()) == 1, grid_columns
    content_metrics = page.locator(".app-content").evaluate(
        "node => ({ clientWidth: node.clientWidth, scrollWidth: node.scrollWidth })"
    )
    assert content_metrics["scrollWidth"] <= content_metrics["clientWidth"] + 1, content_metrics
    page.screenshot(path="data/outputs/separate-compact-layout.png", full_page=True)

    page.set_viewport_size({"width": 720, "height": 520})
    recovery = page.locator(".viewport-recovery")
    expect(recovery).to_be_visible()
    expect(recovery.locator(".viewport-recovery__corner")).to_have_count(4)
    expect(page.locator(".title-bar")).to_be_visible()
    expect(recovery).to_contain_text("720 × 520")
    assert page.locator(".app-body").evaluate("node => node.inert === true")
    assert recovery.evaluate("node => node.contains(document.activeElement)")
    recovery.get_by_role("button", name="Reduce interface scale", exact=True).click()
    expect(recovery).to_contain_text("95%")
    page.screenshot(path="data/outputs/viewport-recovery-overlay.png", full_page=True)

    page.set_viewport_size({"width": 650, "height": 450})
    expect(recovery).to_be_visible()
    expect(recovery).to_contain_text("650 × 450")
    expect(recovery.get_by_role("button", name="Reduce interface scale", exact=True)).to_be_visible()
    page.screenshot(path="data/outputs/viewport-recovery-overlay-extreme.png", full_page=True)

    page.set_viewport_size({"width": 900, "height": 700})
    expect(recovery).to_be_hidden()
    assert page.locator(".app-body").evaluate("node => node.inert === false")
    assert not errors, errors

    print(json.dumps({
        "transitionEvents": len(transition_classes),
        "mode": "workflow",
        "compactColumns": 1,
        "recoveryScale": 95,
    }))
    print("Separate run-mode transition smoke passed")
    browser.close()
