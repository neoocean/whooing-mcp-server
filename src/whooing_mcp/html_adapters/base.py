"""HTML adapter 공통 — Playwright 헤드리스 복호화 + DOM 추출 helper."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class HTMLDetectResult:
    detected_issuer: str | None
    confidence: float
    head_excerpt: str


class HtmlDecryptError(Exception):
    """복호화 실패 — 잘못된 password / 형식 변경 / Playwright 미설치 등."""


async def decrypt_html_with_playwright(
    html_path: str,
    password: str,
    *,
    password_input_selector: str = "#password",
    submit_function: str = "UserFunc()",
    wait_after_submit_ms: int = 2000,
    expected_alert_substrings: list[str] | None = None,
) -> str:
    """JS 가 client-side 복호화하는 HTML 을 헤드리스 Chromium 으로 처리.

    Returns:
      복호화 후 page.content() (전체 평문 HTML)

    Raises:
      HtmlDecryptError: 패스워드 틀림 (alert 발생) 또는 Playwright 실패.
    """
    try:
        from playwright.async_api import async_playwright  # lazy import
    except ImportError as ex:
        raise HtmlDecryptError(
            "Playwright 미설치. `pip install playwright && playwright install chromium`"
        ) from ex

    file_url = f"file://{Path(html_path).resolve()}"
    log.info("decrypting %s", html_path)

    expected_alerts = expected_alert_substrings or [
        "비밀번호가 일치하지 않습니다",
        "암호가 틀렸",
        "incorrect",
    ]
    captured_alerts: list[str] = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            async def on_dialog(dialog):
                captured_alerts.append(dialog.message)
                await dialog.dismiss()
            page.on("dialog", on_dialog)

            await page.goto(file_url, wait_until="load")
            await page.fill(password_input_selector, password)
            await page.evaluate(submit_function)
            await page.wait_for_timeout(wait_after_submit_ms)

            # 패스워드 틀림 detection
            for alert_msg in captured_alerts:
                if any(s in alert_msg for s in expected_alerts):
                    raise HtmlDecryptError(
                        f"password rejected: {alert_msg!r} "
                        f"(captured alerts: {captured_alerts})"
                    )

            decrypted = await page.content()
            await browser.close()
            return decrypted
    except HtmlDecryptError:
        raise
    except Exception as ex:
        raise HtmlDecryptError(f"Playwright 복호화 실패: {ex}") from ex
