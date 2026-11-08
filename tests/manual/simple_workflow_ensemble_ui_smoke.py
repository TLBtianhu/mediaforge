"""Headless UI smoke for adding an Ensemble node to a simple workflow."""

import json

from playwright.sync_api import expect, sync_playwright


def model(name: str):
    return {
        "name": name,
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
        "modelPath": f"D:/Models/{name}",
        "configPath": None,
        "auxiliaryPaths": [],
    }


definition = {
    "version": 1,
    "defaults": {"device": "cuda", "output_format": "wav", "inference_params": {"normalize": False}},
    "steps": [
        {
            "id": "modelA",
            "model": "model-a.ckpt",
            "input": "input",
            "stems": ["Vocals", "Instrumental"],
            "save": {},
            "output_names": {},
        },
        {
            "id": "modelB",
            "model": "model-b.ckpt",
            "input": "input",
            "stems": ["Vocals", "Instrumental"],
            "save": {},
            "output_names": {},
        },
    ],
}
workflow_state = {
    "selectedWorkflowId": "ensemble-simple",
    "workflows": [{
        "id": "ensemble-simple",
        "name": "Simple Ensemble",
        "description": "Blend two vocal estimates.",
        "definition": definition,
        "format": "simple",
        "formatVersion": 1,
        "createdAt": 1,
        "updatedAt": 1,
    }],
}
model_state = {
    "models": [model("model-a.ckpt"), model("model-b.ckpt")],
    "categories": ["vocal"],
    "categoriesCn": ["人声"],
    "count": 2,
    "modelDir": "D:/Models",
}


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(
        f"""(() => {{
          localStorage.setItem(
            'pymss-studio:app-settings',
            JSON.stringify({{ version: 1, startupOnboardingSeen: true, locale: 'en' }}),
          )
          localStorage.setItem('pymss-studio:workflow-state', JSON.stringify({json.dumps(workflow_state)}))
          localStorage.setItem('pymss-studio:model-state', JSON.stringify({json.dumps(model_state)}))
        }})()"""
    )
    page.goto("http://localhost:1420/#/workflow-simple-editor?workflowId=ensemble-simple")
    page.wait_for_load_state("networkidle")

    canvas = page.locator(".simple-node-editor__canvas")
    expect(page.locator(".simple-node--step")).to_have_count(2)
    page.locator(".simple-node--step").first.locator(".n-select").first.click()
    select_menu = page.locator(".n-select-menu")
    expect(select_menu).to_be_visible()
    canvas.hover(position={"x": 1450, "y": 720})
    page.mouse.wheel(0, -120)
    expect(select_menu).to_be_hidden()

    expect(page.get_by_role("button", name="Add Ensemble", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Add Step", exact=True).click()
    chooser = page.locator(".simple-node-type-modal")
    expect(chooser).to_be_visible()
    page.wait_for_timeout(220)
    page.screenshot(path="data/outputs/simple-workflow-node-type-modal.png", full_page=True)
    chooser.locator(".simple-node-type-modal__choices button", has_text="Ensemble").click()
    ensemble = page.locator(".simple-node--ensemble")
    expect(ensemble).to_have_count(1)
    expect(ensemble.locator(".simple-ensemble-input-row")).to_have_count(2)
    expect(ensemble.locator(".simple-ensemble-input-row .n-select")).to_have_count(0)
    expect(ensemble.locator(".simple-ensemble-input-row__source")).to_have_count(2)
    ensemble_box = ensemble.bounding_box()
    output_socket_box = ensemble.locator(".simple-ensemble-output-port i").bounding_box()
    assert ensemble_box and output_socket_box
    assert abs(
        (output_socket_box["x"] + output_socket_box["width"] / 2)
        - (ensemble_box["x"] + ensemble_box["width"])
    ) < 8
    expect(ensemble).to_contain_text("model-a.ckpt · Vocals")
    expect(ensemble).to_contain_text("model-b.ckpt · Vocals")
    expect(ensemble).to_contain_text("Weighted Wave Average")

    source_port = page.locator(".simple-node--input .simple-port--output")
    target_port = ensemble.locator(".simple-ensemble-input-row__port").first
    source_box = source_port.bounding_box()
    target_box = target_port.bounding_box()
    assert source_box and target_box
    source_port.dispatch_event("pointerdown", {
        "button": 0,
        "pointerId": 1,
        "clientX": source_box["x"] + source_box["width"] / 2,
        "clientY": source_box["y"] + source_box["height"] / 2,
    })
    target_port.dispatch_event("pointerup", {
        "button": 0,
        "pointerId": 1,
        "clientX": target_box["x"] + target_box["width"] / 2,
        "clientY": target_box["y"] + target_box["height"] / 2,
    })
    expect(ensemble).to_contain_text("Original input")

    save = page.locator(".simple-node--save")
    expect(save).to_contain_text("Ensemble")
    expect(save.locator(".simple-save-row")).to_have_count(1)
    expect(page.get_by_role("button", name="Save", exact=True)).to_be_enabled()

    ensemble.get_by_role("button", name="Add Input", exact=True).click()
    expect(ensemble.locator(".simple-ensemble-input-row")).to_have_count(3)
    expect(ensemble).to_contain_text("model-a.ckpt · Vocals")
    expect(page.get_by_role("button", name="Save", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".n-message__content", has_text="Workflow saved")).to_be_visible()
    stored = page.evaluate("JSON.parse(localStorage.getItem('pymss-studio:workflow-state'))")
    saved_definition = stored["workflows"][0]["definition"]
    assert saved_definition["ensembles"][0]["output_stem"] == "Vocals"
    assert [item["source"] for item in saved_definition["ensembles"][0]["inputs"]] == [
        "input",
        "modelB.Vocals",
        "modelA.Vocals",
    ]

    canvas.click(button="right", position={"x": 1200, "y": 100})
    context_menu = page.locator(".n-dropdown-menu")
    expect(context_menu).to_be_visible()
    context_menu.locator(".n-dropdown-option", has_text="Ensemble").click()
    expect(page.locator(".simple-node--ensemble")).to_have_count(2)
    page.wait_for_timeout(180)
    expect(context_menu).to_be_hidden()
    canvas_box = canvas.bounding_box()
    ensemble_boxes = [item.bounding_box() for item in page.locator(".simple-node--ensemble").all()]
    inserted_box = max((box for box in ensemble_boxes if box), key=lambda box: box["x"])
    assert canvas_box and inserted_box
    assert abs((inserted_box["x"] + inserted_box["width"] / 2) - (canvas_box["x"] + 1200)) < 6

    page.screenshot(path="data/outputs/simple-workflow-ensemble.png", full_page=True)
    assert not errors, errors
    print(json.dumps({"steps": 2, "ensembles": 2, "ensembleInputs": 3, "savedOutputs": 1}))
    print("simple workflow Ensemble UI smoke passed")
    browser.close()
