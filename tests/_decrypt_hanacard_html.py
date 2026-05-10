"""하나카드 보안메일 HTML 복호화 일회용 스크립트 (feasibility 검증).

본 스크립트가 검증되면 정식 도구 (whooing_decrypt_hanacard_html /
whooing_import_html_statement) 로 일반화 가능.

사용:
    1. .env 에 WHOOING_CARD_HTML_PASSWORD=... 추가
       (옛 WHOOING_HANACARD_PASSWORD 도 fallback 으로 인식)
    2. python tests/_decrypt_hanacard_html.py /Users/.../hanacard_xxx.html

  → /tmp/hanacard-decrypted.html 에 평문 HTML 저장
  → stderr 에 거래 테이블 요약 출력
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


async def decrypt(html_path: str, password: str) -> str:
    """Playwright 로 HTML 열어 password 입력 → 복호화된 DOM 반환."""
    from playwright.async_api import async_playwright

    file_url = f"file://{Path(html_path).resolve()}"
    print(f"loading {file_url}", file=sys.stderr)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # alert dialog handling — 패스워드 틀리면 alert 뜸
        alerts: list[str] = []

        async def on_dialog(dialog):
            alerts.append(dialog.message)
            await dialog.dismiss()
        page.on("dialog", on_dialog)

        await page.goto(file_url, wait_until="load")
        print("page loaded", file=sys.stderr)

        # 패스워드 입력
        await page.fill("#password", password)
        print(f"password filled (len={len(password)})", file=sys.stderr)

        # UserFunc() 호출 — 폼 submit 대신 직접 trigger
        await page.evaluate("UserFunc()")
        print("UserFunc() called", file=sys.stderr)

        # 복호화 → DOM 업데이트 잠시 대기
        await page.wait_for_timeout(2000)

        if alerts:
            print(f"⚠️  alert(s): {alerts!r}", file=sys.stderr)
            if any("일치하지 않습니다" in a for a in alerts):
                raise SystemExit("패스워드 틀림 — alert 메시지 확인")

        # 복호화 후 DOM 추출
        full_html = await page.content()
        await browser.close()

    return full_html


def summarize(decrypted_html: str) -> None:
    """간단 요약 — 거래 테이블 추정."""
    import re
    # 금액 패턴
    won_amounts = re.findall(r'\d{1,3}(?:,\d{3})+(?:\s*원)?', decrypted_html)
    print(f"\n  금액 패턴 매칭: {len(won_amounts)}건", file=sys.stderr)
    if won_amounts:
        print(f"  예시: {won_amounts[:10]}", file=sys.stderr)

    # 카드 keyword
    for kw in ["하나카드", "VISA", "MASTER", "이용일", "가맹점", "이용금액", "결제일"]:
        cnt = decrypted_html.count(kw)
        if cnt:
            print(f"  '{kw}': {cnt}회", file=sys.stderr)


async def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: python _decrypt_hanacard_html.py <html_path>", file=sys.stderr)
        return 2

    html_path = argv[0]
    if not Path(html_path).exists():
        print(f"file not found: {html_path}", file=sys.stderr)
        return 2

    password = (
        os.getenv("WHOOING_CARD_HTML_PASSWORD", "").strip()
        or os.getenv("WHOOING_HANACARD_PASSWORD", "").strip()  # legacy fallback
    )
    if not password:
        print(
            "WHOOING_CARD_HTML_PASSWORD 미설정 — .env 에 추가 후 재시도",
            file=sys.stderr,
        )
        return 2

    decrypted = await decrypt(html_path, password)
    out_path = "/tmp/hanacard-decrypted.html"
    Path(out_path).write_text(decrypted, encoding="utf-8")
    print(f"\n  wrote {out_path} ({len(decrypted):,} chars)", file=sys.stderr)

    summarize(decrypted)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
