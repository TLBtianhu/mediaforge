"""Headless light/dark screenshots for theme review.

Manual tool, not part of CI: it needs a running frontend (`pnpm dev` or a preview
build) and Playwright. It only drives a headless browser; it never launches the
desktop app or touches the user's windows.

Environment:
  PYMSS_SMOKE_URL   frontend base URL (default http://localhost:1420/)
  PYMSS_CHROMIUM    optional Chromium executable when the bundled one is missing
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("PYMSS_SMOKE_URL", "http://localhost:1420/").rstrip("/") + "/"
CHROMIUM = os.environ.get("PYMSS_CHROMIUM", "")

ROUTES = [
    ("separate", "#/separate"),
    ("image", "#/image"),
    ("video", "#/video"),
    ("media-workflow", "#/media-workflow"),
    ("factory", "#/factory"),
    ("models", "#/models"),
    ("results", "#/results"),
    ("queue", "#/queue"),
    ("settings", "#/settings"),
    ("assets", "#/assets"),
    ("tools", "#/tools"),
    ("workflows", "#/workflows"),
    ("debug", "#/debug"),
    ("editor", "#/editor"),
]

BASE_SETTINGS = {
    "version": 1, "themeAccent": "blue", "locale": "zh-CN",
    "startupOnboardingSeen": True, "beginnerGuideInvitationSeen": True,
    "animationsEnabled": False, "modelDir": "D:/models", "outputDir": "D:/outputs",
    "defaultDevice": "auto", "defaultFormat": "wav", "downloadSource": "modelscope",
    "downloadMethod": "aria2c", "maxConcurrentSeparations": 1, "clearInputAfterSubmit": True,
    "wavBitDepth": "FLOAT", "flacBitDepth": "PCM_24", "mp3BitRate": "320k",
    "m4aBitRate": "512k", "m4aCodec": "aac", "proxyMode": "system", "proxyUrl": "",
    "proxyBypass": "", "updateChannel": "stable", "updateSource": "auto",
    "beginnerGuideProgress": {"status": "completed", "step": 0},
}


def init_script(theme: str, accent: str = "blue") -> str:
    settings = {**BASE_SETTINGS, "themeMode": theme, "themeAccent": accent}
    return (
        "try {"
        f"localStorage.setItem('pymss-studio:app-settings', {json.dumps(json.dumps(settings))});"
        f"localStorage.setItem('pymss:theme_mode', {json.dumps(theme)});"
        "localStorage.removeItem('pymss-studio:separate-state');"
        "} catch (error) {}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".tmp/theme-shots", help="screenshot directory")
    parser.add_argument("--only", nargs="*", help="route names to capture (default: all)")
    parser.add_argument("--accents", nargs="*", default=["blue"],
                        help="accents to capture (default: blue)")
    arguments = parser.parse_args()
    output = Path(arguments.out)
    output.mkdir(parents=True, exist_ok=True)
    report = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=CHROMIUM or None)
        for accent in arguments.accents:
          for theme in ("dark", "light"):
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_init_script(init_script(theme, accent))
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
            page.on("console", lambda message: errors.append(f"console.{message.type}: {message.text}")
                    if message.type == "error" else None)
            for name, route in ROUTES:
                if arguments.only and name not in arguments.only:
                    continue
                errors.clear()
                page.goto(BASE + route, wait_until="domcontentloaded")
                try:
                    page.wait_for_selector(".app-shell", timeout=15000)
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(2200)
                page.screenshot(path=str(output / f"{theme}-{accent}-{name}.png"))
                report.append({
                    "theme": theme,
                    "route": name,
                    **page.evaluate(
                        """() => {
                            const root = getComputedStyle(document.documentElement);
                            return {
                                cls: document.documentElement.className,
                                surface: root.getPropertyValue('--surface').trim(),
                                onSurface: root.getPropertyValue('--on-surface').trim(),
                                primary: root.getPropertyValue('--primary').trim(),
                                onPrimary: root.getPropertyValue('--on-primary').trim(),
                            };
                        }"""
                    ),
                    "errors": errors[:4],
                })
            context.close()
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"screenshots: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
