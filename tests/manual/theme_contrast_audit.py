"""Headless WCAG contrast audit for light/dark themes.

Manual tool, not part of CI: it needs a running frontend and Playwright. It walks
every element with its own text, composites the effective background, honours
inherited `opacity`, skips disabled controls (WCAG 1.4.3 exempts them) and
reports pairs below AA.

Environment:
  PYMSS_SMOKE_URL   frontend base URL (default http://localhost:1420/)
  PYMSS_CHROMIUM    optional Chromium executable when the bundled one is missing

Usage:
  python tests/manual/theme_contrast_audit.py                  # report only
  python tests/manual/theme_contrast_audit.py --fail-on-findings
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
    # Plugins is excluded: the marketplace page needs Tauri IPC and fails in a
    # plain browser, so its styling cannot be judged here.
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

AUDIT = r"""
() => {
  const parse = (value) => {
    const match = String(value || '').match(/rgba?\(([^)]+)\)/);
    if (!match) return null;
    const parts = match[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  };
  const blend = (top, bottom) => {
    const a = top.a + bottom.a * (1 - top.a);
    if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
    return {
      r: (top.r * top.a + bottom.r * bottom.a * (1 - top.a)) / a,
      g: (top.g * top.a + bottom.g * bottom.a * (1 - top.a)) / a,
      b: (top.b * top.a + bottom.b * bottom.a * (1 - top.a)) / a,
      a,
    };
  };
  const lum = (color) => {
    const channel = (value) => {
      const c = value / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
  };
  const ratio = (a, b) => {
    const first = lum(a), second = lum(b);
    return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
  };
  const background = (element) => {
    let color = { r: 0, g: 0, b: 0, a: 1 };
    const stack = [];
    for (let node = element; node; node = node.parentElement) {
      const layer = parse(getComputedStyle(node).backgroundColor);
      if (layer && layer.a > 0) stack.push(layer);
      if (layer && layer.a >= 0.999) break;
      if (node === document.documentElement) break;
    }
    for (const layer of stack.reverse()) color = blend(layer, color);
    return color;
  };
  const selector = (element) => {
    const parts = [];
    for (let node = element; node && node.nodeType === 1 && parts.length < 4; node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      if (node.className && typeof node.className === 'string') {
        part += '.' + node.className.trim().split(/\s+/).slice(0, 2).join('.');
      }
      parts.unshift(part);
    }
    return parts.join(' > ');
  };
  const findings = [];
  const seen = new Set();
  for (const element of document.querySelectorAll('body *')) {
    const ownText = [...element.childNodes]
      .filter(node => node.nodeType === 3 && node.textContent.trim())
      .map(node => node.textContent.trim()).join(' ');
    if (!ownText) continue;
    const rect = element.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    const style = getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    // Disabled controls are exempt from WCAG contrast (1.4.3).
    if (element.closest('button:disabled, .n-button--disabled, [aria-disabled="true"], [disabled]')) continue;
    // Accumulate inherited opacity; a translucent label is genuinely dimmer.
    let opacity = 1;
    for (let node = element; node; node = node.parentElement) {
      opacity *= Number(getComputedStyle(node).opacity) || 1;
      if (node === document.documentElement) break;
    }
    if (opacity < 0.1) continue;
    const foreground = parse(style.color);
    if (!foreground) continue;
    const bg = background(element);
    const alpha = Math.min(1, foreground.a * opacity);
    const effective = alpha < 0.999 ? blend({ ...foreground, a: alpha }, bg) : foreground;
    const value = ratio(effective, bg);
    const size = parseFloat(style.fontSize) || 12;
    const bold = Number(style.fontWeight) || 400;
    const large = size >= 24 || (size >= 18.66 && bold >= 700);
    const required = large ? 3 : 4.5;
    if (value + 0.05 < required) {
      const key = selector(element) + '|' + ownText.slice(0, 12) + '|' + Math.round(value * 10);
      if (seen.has(key)) continue;
      seen.add(key);
      findings.push({
        ratio: Number(value.toFixed(2)), required,
        color: style.color,
        background: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`,
        opacity: Number(opacity.toFixed(2)),
        size: Number(size.toFixed(1)),
        selector: selector(element),
        text: ownText.slice(0, 42),
      });
    }
  }
  findings.sort((a, b) => a.ratio - b.ratio);
  return findings.slice(0, 20);
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".tmp/theme-contrast.json", help="JSON report path")
    parser.add_argument("--only", nargs="*", help="route names to audit (default: all)")
    parser.add_argument("--accents", nargs="*", default=["blue", "pink", "sky", "teal"],
                        help="accents to audit (default: all four)")
    parser.add_argument("--fail-on-findings", action="store_true", help="exit 1 when anything is below AA")
    arguments = parser.parse_args()
    output = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=CHROMIUM or None)
        for accent in arguments.accents:
            for theme in ("dark", "light"):
                settings = {**BASE_SETTINGS, "themeMode": theme, "themeAccent": accent}
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                context.add_init_script(
                    "try {"
                    f"localStorage.setItem('pymss-studio:app-settings', {json.dumps(json.dumps(settings))});"
                    f"localStorage.setItem('pymss:theme_mode', {json.dumps(theme)});"
                    "localStorage.removeItem('pymss-studio:separate-state');"
                    "} catch (error) {}"
                )
                page = context.new_page()
                for name, route in ROUTES:
                    if arguments.only and name not in arguments.only:
                        continue
                    page.goto(BASE + route, wait_until="domcontentloaded")
                    try:
                        page.wait_for_selector(".app-shell", timeout=15000)
                    except Exception:  # noqa: BLE001
                        pass
                    page.wait_for_timeout(1800)
                    output.append({"accent": accent, "theme": theme, "route": name,
                                   "findings": page.evaluate(AUDIT)})
                context.close()
        browser.close()
    Path(arguments.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(item["findings"]) for item in output)
    print(f"pages={len(output)} findings={total}")
    for item in output:
        for finding in item["findings"][:5]:
            print(f"  {item['accent']}/{item['theme']}/{item['route']} {finding['ratio']}:1 (need {finding['required']}) "
                  f"opacity={finding['opacity']} {finding['selector']} 「{finding['text']}」 "
                  f"fg={finding['color']} bg={finding['background']}")
    if arguments.fail_on_findings and total:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
