"""Playwright smoke test for /support against the frontend spec."""
import asyncio
import re
import sys
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/support"


def count_cards(html: str, section_id: str) -> int:
    m = re.search(rf'(?s)<section id="{section_id}".*?</section>', html)
    if not m:
        return -1
    return len(re.findall(r'<article class="support-info-card', m.group(0)))


def count_list(html: str, section_id: str, tag: str) -> int:
    m = re.search(rf'(?s)<section id="{section_id}".*?</section>', html)
    if not m:
        return -1
    return len(re.findall(rf'<{tag} class="support-terms-list"', m.group(0)))


def count_security(html: str, section_id: str) -> int:
    m = re.search(rf'(?s)<section id="{section_id}".*?</section>', html)
    if not m:
        return -1
    return len(re.findall(r'class="support-warning"', m.group(0)))


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        resp = await page.goto(URL, wait_until="networkidle")
        status = resp.status if resp else None
        html = await page.content()

        # Desktop assertions
        syarat_cards = count_cards(html, "syarat")
        refund_cards = count_cards(html, "refund")
        syarat_ol = count_list(html, "syarat", "ol")
        refund_ol = count_list(html, "refund", "ol")
        refund_ul = count_list(html, "refund", "ul")
        sec_panels = count_security(html, "refund")
        no_num_h2 = bool(re.search(r'<h2 id="support-terms-heading">Syarat', html)) and \
                     bool(re.search(r'<h2 id="support-refund-heading"><span>Kebijakan', html))
        tangankami = "Raja Voting Digital adalah platform voting digital" in html
        faq = "Pertanyaan yang sering diajukan" in html
        refund_html = re.search(r'(?s)<section id="refund".*?</section>', html).group(0)
        has_gagal = "Pembayaran gagal atau kedaluwarsa" in refund_html
        has_langkah = len(re.findall(r'<li>', re.search(r'(?s)<ol class="support-terms-list">(.*?)</ol>', refund_html).group(1))) == 4

        results = {
            "status_200": status == 200,
            "syarat_cards_4": syarat_cards == 4,
            "refund_cards_0": refund_cards == 0,
            "syarat_ol_2": syarat_ol == 2,
            "refund_ol_1": refund_ol == 1,
            "refund_ul_1": refund_ul == 1,
            "security_panel_1": sec_panels == 1,
            "h2_no_number": no_num_h2,
            "tentang_kami_intact": tangankami,
            "faq_intact": faq,
            "refund_gagal_condition": has_gagal,
            "refund_4_steps": has_langkah,
        }

        # Mobile viewport
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.goto(URL, wait_until="networkidle")
        mobile_html = await page.content()
        m_syarat_cards = count_cards(mobile_html, "syarat")
        m_refund_cards = count_cards(mobile_html, "refund")
        m_sec = count_security(mobile_html, "refund")
        results["mobile_syarat_cards_4"] = m_syarat_cards == 4
        results["mobile_refund_cards_0"] = m_refund_cards == 0
        results["mobile_security_1"] = m_sec == 1

        # Dark mode
        await page.set_viewport_size({"width": 1440, "height": 900})
        await page.goto(URL, wait_until="networkidle")
        await page.evaluate("document.documentElement.classList.add('dark')")
        await page.wait_for_timeout(200)
        dark_html = await page.content()
        d_syarat_cards = count_cards(dark_html, "syarat")
        d_refund_cards = count_cards(dark_html, "refund")
        d_sec = count_security(dark_html, "refund")
        results["dark_syarat_cards_4"] = d_syarat_cards == 4
        results["dark_refund_cards_0"] = d_refund_cards == 0
        results["dark_security_1"] = d_sec == 1

        await browser.close()

    all_pass = all(results.values())
    for k, v in results.items():
        print(f"{'PASS' if v else 'FAIL'}  {k}")
    print(f"\nOVERALL: {'ALL PASS' if all_pass else 'SOME FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))