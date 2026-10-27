"""Playwright check for /support against the frontend spec.

Requires the app running at BASE (uvicorn on 127.0.0.1:8765).
Covers content structure, responsive layout (no horizontal overflow, no clipped
text, card grid columns, security panel inside viewport) and light/dark contrast.
"""
import asyncio
import re
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/support"
SHOTS = "tests/browser/artifacts"


def section(html: str, section_id: str) -> str:
    match = re.search(rf'(?s)<section id="{section_id}".*?</section>', html)
    return match.group(0) if match else ""


def count_cards(html: str, section_id: str) -> int:
    body = section(html, section_id)
    return len(re.findall(r'<article class="support-info-card', body)) if body else -1


def count_list(html: str, section_id: str, variant: str) -> int:
    body = section(html, section_id)
    return len(re.findall(rf'<ul class="support-terms-list support-terms-list--{variant}"', body)) if body else -1


def count_security(html: str, section_id: str) -> int:
    body = section(html, section_id)
    return len(re.findall(r'class="support-warning"', body)) if body else -1


def has_ordered_list(html: str, section_id: str) -> bool:
    body = section(html, section_id)
    return "<ol" in body if body else True


LAYOUT_JS = """
() => {
  const doc = document.documentElement;
  const limit = doc.clientWidth;
  const section = id => document.getElementById(id);
  const offenders = [...document.querySelectorAll('#syarat *, #refund *')].filter(el => {
    const r = el.getBoundingClientRect();
    return (r.width || r.height) && (r.right > limit + 1 || r.left < -1);
  }).map(el => ({ tag: el.tagName, cls: String(el.className).slice(0, 40) }));
  const clipped = [...document.querySelectorAll('#syarat li, #refund li, #syarat h3, #refund h3, .support-info-card__title')]
    .filter(el => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)
    .map(el => el.textContent.trim().slice(0, 40));
  const grid = section('syarat').querySelector('.support-card-grid');
  const columns = grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0;
  const warning = section('refund').querySelector('.support-warning');
  const warningBox = warning.getBoundingClientRect();
  const badge = section('syarat').querySelector('.support-terms-sub__icon');
  return {
    pageOverflow: doc.scrollWidth > limit + 1,
    offenders,
    clipped,
    columns,
    warningInside: warningBox.left >= -0.5 && warningBox.right <= limit + 0.5,
    headingIconCount: document.querySelectorAll('#syarat .support-terms-sub__icon, #refund .support-terms-sub__icon').length,
    visibleNumbering: [...document.querySelectorAll('#syarat h3, #refund h3')]
      .some(h => /^\\s*\\d+\\./.test(h.textContent)),
    markerContent: [...new Set([...document.querySelectorAll('#syarat .support-terms-list li, #refund .support-terms-list li')]
      .map(li => getComputedStyle(li, '::before').content))],
    listStyle: getComputedStyle(section('refund').querySelector('.support-terms-list')).listStyleType,
  };
}
"""

CONTRAST_JS = """
() => {
  const parse = value => (value.match(/[\\d.]+/g) || []).map(Number);
  const luminance = ([r, g, b]) => {
    const c = [r, g, b].map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  };
  const bgOf = el => {
    for (let node = el; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      const bg = style.backgroundColor;
      const parts = parse(bg);
      if (parts.length >= 3 && (parts[3] === undefined || parts[3] > 0.5)) return parts.slice(0, 3);
      const image = style.backgroundImage;
      if (image && image !== 'none') {
        const colors = (image.match(/rgba?\\([^)]+\\)/g) || []).map(parse).filter(c => c.length === 3 || c[3] > 0.5);
        if (colors.length) return colors[0].slice(0, 3);
      }
    }
    const parts = parse(getComputedStyle(document.body).backgroundColor);
    return parts.slice(0, 3);
  };
  const ratio = el => {
    const fg = luminance(parse(getComputedStyle(el).color));
    const bg = luminance(bgOf(el));
    const [hi, lo] = fg > bg ? [fg, bg] : [bg, fg];
    return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
  };
  const pick = (root, selector) => root.querySelector(selector);
  const targets = [
    ['terms heading', pick(document, '#support-terms-heading')],
    ['terms list item', pick(document, '#syarat .support-terms-list li')],
    ['terms note', pick(document, '#syarat .support-terms-note')],
    ['card title', pick(document, '#syarat .support-info-card__title')],
    ['card text', pick(document, '#syarat .support-info-card__text')],
    ['heading icon', pick(document, '#syarat .support-terms-sub__icon svg')],
    ['refund paragraph', pick(document, '#refund .support-refund-lead')],
    ['warning title', pick(document, '#refund .support-warning__title')],
    ['warning text', pick(document, '#refund .support-warning__text')],
  ];
  return targets.filter(([, el]) => el).map(([name, el]) => ({ name, ratio: ratio(el) }));
}
"""


async def main() -> int:
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(error.message))

        resp = await page.goto(URL, wait_until="networkidle")
        results["status_200"] = bool(resp and resp.status == 200)
        html = await page.content()
        results["syarat_cards_4"] = count_cards(html, "syarat") == 4
        results["refund_cards_0"] = count_cards(html, "refund") == 0
        results["lists_use_symbols"] = (
            count_list(html, "syarat", "checks") == 2
            and count_list(html, "refund", "conditions") == 1
            and count_list(html, "refund", "steps") == 1
        )
        results["no_ordered_lists"] = not has_ordered_list(html, "syarat") and not has_ordered_list(html, "refund")
        results["security_panel_1"] = count_security(html, "refund") == 1
        results["tentang_kami_intact"] = "Raja Voting Digital adalah platform voting digital" in html
        results["kontak_intact"] = 'href="https://wa.me/6285602623817"' in html and "Klaten, Jawa Tengah" in html
        results["faq_intact"] = "Pertanyaan yang sering diajukan" in html
        refund_html = section(html, "refund")
        results["refund_gagal_condition"] = "Pembayaran gagal atau kedaluwarsa" in refund_html
        results["refund_5_conditions"] = refund_html.count("<li><strong>") == 5
        results["refund_4_steps"] = len(re.findall(r"<li>", refund_html.split("support-terms-list--steps\">")[1].split("</ul>")[0])) == 4

        # Layout across widths in light and dark mode.
        for mode in ("light", "dark"):
            for width in (320, 390, 820, 1440):
                await page.set_viewport_size({"width": width, "height": 900})
                await page.goto(URL, wait_until="networkidle")
                if mode == "dark":
                    await page.evaluate("document.documentElement.classList.add('dark')")
                    await page.wait_for_timeout(150)
                data = await page.evaluate(LAYOUT_JS)
                label = f"{mode}-{width}"
                results[f"{label} no overflow"] = not data["pageOverflow"] and not data["offenders"], data["offenders"]
                results[f"{label} no clipped text"] = not data["clipped"], data["clipped"]
                results[f"{label} columns"] = data["columns"] == (1 if width <= 700 else 2 if width <= 1100 else 4), data["columns"]
                results[f"{label} panel inside"] = data["warningInside"]
                results[f"{label} symbolic markers"] = all(
                    marker not in ("none", "normal", '""') for marker in data["markerContent"]
                ) and data["listStyle"] == "none", data["markerContent"]
                results[f"{label} no numeric headings"] = not data["visibleNumbering"]
                results[f"{label} heading icons 6"] = data["headingIconCount"] == 6, data["headingIconCount"]
                if mode == "light" and width in (390, 1440):
                    await page.screenshot(path=f"{SHOTS}/support-{width}.png", full_page=True)

        # Contrast in both themes.
        for mode in ("light", "dark"):
            await page.set_viewport_size({"width": 1440, "height": 900})
            await page.goto(URL, wait_until="networkidle")
            await page.evaluate(f"document.documentElement.classList.toggle('dark', {str(mode == 'dark').lower()})")
            await page.wait_for_timeout(150)
            for item in await page.evaluate(CONTRAST_JS):
                threshold = 3 if "icon" in item["name"] else 4.5
                results[f"{mode} contrast {item['name']}"] = item["ratio"] >= threshold, item["ratio"]
        results["no js errors"] = not errors, errors
        await browser.close()

    all_pass = True
    for key, value in results.items():
        ok = value[0] if isinstance(value, tuple) else value
        all_pass = all_pass and ok
        detail = f"  -> {value[1]}" if isinstance(value, tuple) and not ok else ""
        print(f"{'PASS' if ok else 'FAIL'}  {key}{detail}")
    print(f"\nOVERALL: {'ALL PASS' if all_pass else 'SOME FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
