"""2026-04.pdf (하나카드 4월 명세서) import 분석 + 실행.

이 스크립트는 일회용이지만 본 워크플로우의 reference 구현 — 향후 정식
whooing_import_pdf_statement 도구로 일반화될 것.

작업:
  1. PDF 데이터 (Claude vision 으로 사전 추출) — 하드코딩
  2. s9046 의 2026-03-15 ~ 2026-04-14 entries fetch (89건)
  3. dedup: PDF 항목 vs 기존 ledger
     매칭 기준: same date (YYYYMMDD prefix) + same money + 유사 item
  4. 카테고리별 요약 + 누락 entries 보고
  5. (--insert 플래그) 누락 entries 를 REST POST 로 입력
  6. (--insert 플래그) sqlite pdf_import_log 테이블에 기록
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path

from dotenv import load_dotenv
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from whooing_mcp.server import _build_client


SECTION = "s9046"

# ---- PDF 데이터 (Claude vision read 결과) -------------------------------


@dataclass
class PdfEntry:
    date: str             # YYYYMMDD
    merchant: str         # 가맹점 (PDF '이용가맹점' 컬럼)
    amount: int           # KRW 이용금액 (할인/취소 음수)
    fee: int = 0          # KRW 해외이용수수료 (있으면)
    is_foreign: bool = False
    notes: str = ""

    @property
    def total(self) -> int:
        """후잉에 입력할 최종 KRW 금액 (원금 + 수수료)."""
        return self.amount + self.fee


# Card 1: CLUB SK VISA3698 (모두 KRW, 22건)
CARD1_VISA3698 = [
    PdfEntry("20260315", "SKT통신요금할인", -15000, notes="할인 (음수)"),
    PdfEntry("20260315", "조원관광진흥주식회사", 41200),
    PdfEntry("20260315", "조원관광진흥주식회사", 2000),
    PdfEntry("20260315", "바소", 75000),
    PdfEntry("20260317", "씨유(CU) 수지동천점", 4200),
    PdfEntry("20260321", "선데이치과의원", 410900),
    PdfEntry("20260321", "버거킹수지대경GS", 13400),
    PdfEntry("20260328", "닭칼국수", 21000),
    PdfEntry("20260328", "씨유(CU) 수지동천점", 21480),
    PdfEntry("20260329", "Nook coffee", 11000),
    PdfEntry("20260331", "향촌", 49000),
    PdfEntry("20260401", "닭칼국수", 20000),
    PdfEntry("20260403", "온유 파스타", 33800),
    PdfEntry("20260404", "닭칼국수", 20000),
    PdfEntry("20260407", "서울 수 정신과", 7200),
    PdfEntry("20260407", "온누리길약국", 20800),
    PdfEntry("20260407", "카카오T일반택시_0", 26200),
    PdfEntry("20260407", "카카오T일반택시(법인)_4", 500),
    PdfEntry("20260407", "카카오T일반택시_0", 25200),
    PdfEntry("20260411", "닭칼국수", 20000),
    PdfEntry("20260411", "씨유(CU) 수지동천점", 6000),
    PdfEntry("20260414", "교통-지하철06건", 147400),
]

# Card 2: CLUB SK MASTER2991 (모바일, 44건; 6건 USD 외화)
CARD2_MASTER2991 = [
    PdfEntry("20260201", "SMS이용요금(면제) 02월분28일치", -300, notes="환불"),
    PdfEntry("20260319", "PAYPAL *ARLOTECHNOL", 30476, fee=60, is_foreign=True),
    PdfEntry("20260328", "WWW.PERPLEXITY AI", 84591, fee=167, is_foreign=True),
    PdfEntry("20260329", "ATLASSIAN", 22339, fee=44, is_foreign=True),
    PdfEntry("20260404", "WWW.PERPLEXITY AI", 25376, fee=50, is_foreign=True),
    PdfEntry("20260405", "PAYPAL *CLOUDFLARE", 15569, fee=30, is_foreign=True),
    PdfEntry("20260406", "WWW.PERPLEXITY AI", 25363, fee=50, is_foreign=True),
    PdfEntry("20260315", "네이버페이", 33050),
    # Page 2 (continued)
    PdfEntry("20260315", "쿠팡이스_쿠팡이스_KCC", 21000),
    PdfEntry("20260315", "LG U+ 통신요금 자동", 15000),
    PdfEntry("20260315", "541992_SK텔레콤주식회사납부", 44200),
    PdfEntry("20260315", "쿠팡(쿠팡)L 면세이서면자", 31400),
    PdfEntry("20260316", "쿠팡이스_쿠팡이스_KCC", 21000),
    PdfEntry("20260317", "쿠팡(쿠팡)L 면세이서면자", 49160),
    PdfEntry("20260318", "쿠팡(쿠팡)L 면세이서면자", 22970),
    PdfEntry("20260319", "쿠팡이스_쿠팡이스_KCP", 29300),
    PdfEntry("20260320", "ACT 티클러스_KCT 티클러스", 2000),
    PdfEntry("20260321", "쿠팡이스_쿠팡이스_KCP", 22600),
    PdfEntry("20260321", "쿠팡이스_쿠팡이스_KCP", 22600),
    PdfEntry("20260321", "APPLE_KCP_면세이서면자", 12000),
    PdfEntry("20260322", "쿠팡(쿠팡)L 면세이서면자", 19420),
    PdfEntry("20260324", "쿠팡이스_사스소망복심신", 29300),
    PdfEntry("20260326", "쿠팡이스_사스소망복심신", 29300),
    PdfEntry("20260327", "쿠팡(쿠팡)L 면세이서면자", 39400),
    PdfEntry("20260328", "쿠팡이스_사스소망복심신", 30000),
    PdfEntry("20260328", "쿠팡이스_쿠팡이스_KCC", 25300),
    PdfEntry("20260329", "쿠팡이스_쿠팡이스_KCP", 32800),
    PdfEntry("20260329", "쿠팡(쿠팡)L 사스소장보부", 31780),
    PdfEntry("20260331", "쿠팡(쿠팡)L 면세이서면자", 23400),
    PdfEntry("20260401", "쿠팡(쿠팡)L 면세이서면자", 39400),
    PdfEntry("20260401", "APPLE_KCP_면세이서면자", 11100),
    # Page 3
    PdfEntry("20260403", "쿠팡(쿠팡)L 면세이서면자", 43170),
    PdfEntry("20260404", "쿠팡이스_쿠팡이스_KCC", 17800),
    PdfEntry("20260404", "쿠팡이스_쿠팡이스_KCC", 16800),
    PdfEntry("20260405", "쿠팡(쿠팡)L 쿠팡(쿠팡)L_ICC", 35380),
    PdfEntry("20260406", "쿠팡(쿠팡)L 쿠팡(쿠팡)L_ICC", 19870),
    PdfEntry("20260406", "쿠팡이스_사스소장보부", 28800),
    PdfEntry("20260406", "APPLE_KCP_면세이서면자", 8900),
    PdfEntry("20260406", "쿠팡이스_쿠팡이스_KCC", 8900),
    PdfEntry("20260408", "쿠팡이스_사스소장보부", 28800),
    PdfEntry("20260411", "쿠팡(쿠팡)L_사스소장보부", 25100),
    PdfEntry("20260411", "쿠팡(쿠팡)L_사스소장보부", 20780),
    PdfEntry("20260412", "유튜브브리미엄_가입 미천신", 4990),
    PdfEntry("20260413", "G3-9000_googleplay", 4990),
    PdfEntry("20260413", "쿠팡(쿠팡)L_쿠팡(쿠팡)L", 34800),
    PdfEntry("20260414", "쿠팡(쿠팡)L_쿠팡(쿠팡)L", 219680),
]

ALL_PDF = [(e, "VISA3698") for e in CARD1_VISA3698] + [(e, "MASTER2991") for e in CARD2_MASTER2991]


# ---- Dedup analysis -----------------------------------------------------


def normalize_date(raw) -> str:
    """ledger 의 'YYYYMMDD.NNNN' → 'YYYYMMDD'."""
    s = str(raw).split(".")[0]
    return s


def find_match(pdf: PdfEntry, ledger: list[dict]) -> tuple[dict, float] | None:
    """동일 date + 동일 amount(또는 amount+fee=total) 후보 중 item 유사도 최고."""
    candidates = []
    for L in ledger:
        ldate = normalize_date(L.get("entry_date"))
        if ldate != pdf.date:
            continue
        lmoney = L.get("money")
        if lmoney is None:
            continue
        try:
            lmoney = int(lmoney)
        except (TypeError, ValueError):
            continue
        # match against amount, total, or |amount| (할인 음수)
        targets = {pdf.amount, pdf.total, abs(pdf.amount), abs(pdf.total)}
        if lmoney not in targets:
            continue
        # item similarity (token_set_ratio for Korean merchant names)
        sim = fuzz.token_set_ratio(pdf.merchant, L.get("item") or "") / 100.0
        candidates.append((sim, L))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    sim, best = candidates[0]
    return best, sim


async def main(argv) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--insert", action="store_true", help="실제 입력 (default: dry-run)")
    p.add_argument("--out", default="/tmp/pdf-import-2026-04-report.json")
    args = p.parse_args(argv)

    print(f"== fetch s9046 entries 20260315 ~ 20260414 ==", file=sys.stderr)
    client, _ = _build_client()
    ledger = await client.list_entries(
        section_id=SECTION, start_date="20260315", end_date="20260414"
    )
    print(f"   ledger entries: {len(ledger)}", file=sys.stderr)

    # also fetch 02/01 separately (one PDF entry is from Feb)
    feb = await client.list_entries(
        section_id=SECTION, start_date="20260201", end_date="20260201"
    )
    ledger_all = ledger + feb
    print(f"   + 20260201: {len(feb)} → total {len(ledger_all)}", file=sys.stderr)

    # ---- dedup ----
    matched = []
    missing = []
    for pdf, card in ALL_PDF:
        m = find_match(pdf, ledger_all)
        if m:
            ledger_e, sim = m
            matched.append({
                "card": card,
                "pdf": asdict(pdf),
                "ledger_entry_id": ledger_e.get("entry_id"),
                "ledger_item": ledger_e.get("item"),
                "ledger_l_account_id": ledger_e.get("l_account_id"),
                "ledger_r_account_id": ledger_e.get("r_account_id"),
                "similarity": round(sim, 2),
            })
        else:
            missing.append({"card": card, "pdf": asdict(pdf)})

    print(f"\n== dedup result ==", file=sys.stderr)
    print(f"   matched : {len(matched):>3} / {len(ALL_PDF)}", file=sys.stderr)
    print(f"   missing : {len(missing):>3} / {len(ALL_PDF)}", file=sys.stderr)

    # missing 항목 카테고리별 요약
    print(f"\n== MISSING entries ({len(missing)}) ==", file=sys.stderr)
    for m in missing:
        pdf = m["pdf"]
        flag = "💱" if pdf["is_foreign"] else "  "
        fee_str = f" + {pdf['fee']:>3} fee" if pdf["fee"] else ""
        total = pdf["amount"] + pdf["fee"]
        print(f"   {flag} {pdf['date']}  [{m['card']}]  "
              f"{pdf['amount']:>8}{fee_str}  → {total:>8}  {pdf['merchant'][:35]}",
              file=sys.stderr)

    # save full report
    report = {
        "section_id": SECTION,
        "pdf_total_entries": len(ALL_PDF),
        "ledger_entries_in_range": len(ledger_all),
        "matched_count": len(matched),
        "missing_count": len(missing),
        "matched": matched,
        "missing": missing,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n   wrote {args.out}", file=sys.stderr)

    if args.insert:
        await insert_missing(missing, client)

    return 0


# ---- account mapping (사용자 결정, 2026-05-09) ----------------------------

R_ACCOUNT_HANA_CARD = "x80"  # [우진]하나카드 — VISA3698 + MASTER2991 둘 다

# l_account_id 제안 (사용자 과거 ledger 패턴 + 사용자 승인)
PROPOSED_L_ACCOUNT: dict[tuple[str, int], str] = {
    # (date, money) → l_account_id  (특수 케이스 매핑)
    # SKT통신요금할인 -15000
    ("20260315", -15000): "x77",
    # 교통-지하철06건
    ("20260414", 147400): "x51",
    # SMS이용요금 환불 -300
    ("20260201", -300): "x77",
    # LG U+ 통신요금
    ("20260315", 15000): "x77",
    # SK텔레콤
    ("20260315", 44200): "x77",
    # 쿠팡 / 사스소망복심신 / 사스소장보부 / 면세이서면자 → 식비 default
    # 유튜브 프리미엄
    ("20260412", 4990): "x58",
    # APPLE_KCP → 소프트웨어
    ("20260401", 11100): "x99",
    ("20260406", 8900): "x99",  # 단 같은 날 8900 두 건 — 첫 번째 (APPLE) 만 x99, 두 번째 (쿠팡) x50
    # 외화 6건
    ("20260319", 30536): "x99",   # PAYPAL ARLOTECHNOL
    ("20260328", 84758): "x120",  # PERPLEXITY
    ("20260329", 22383): "x99",   # ATLASSIAN
    ("20260404", 25426): "x120",  # PERPLEXITY
    ("20260405", 15599): "x77",   # PAYPAL CLOUDFLARE
    ("20260406", 25413): "x120",  # PERPLEXITY
}

# l_account_id 의 type 매핑 (POST 시 l_account 필드용)
L_ACCOUNT_TYPE = {
    "x50": "expenses", "x51": "expenses", "x52": "expenses", "x53": "expenses",
    "x54": "expenses", "x57": "expenses", "x58": "expenses", "x77": "expenses",
    "x79": "expenses", "x92": "expenses", "x94": "expenses", "x99": "expenses",
    "x105": "expenses", "x106": "expenses", "x107": "expenses",
    "x120": "expenses", "x130": "expenses", "x134": "expenses",
    "x150": "expenses", "x151": "expenses", "x152": "expenses",
    "x154": "expenses", "x155": "expenses", "x60": "expenses", "x56": "expenses", "x59": "expenses",
    "x80": "liabilities",  # 하나카드
}


def resolve_l_account(pdf: PdfEntry, total: int) -> str:
    """제안 l_account_id. 특수 매핑 우선, 그 외 default x50 (식비)."""
    key = (pdf.date, total)
    if key in PROPOSED_L_ACCOUNT:
        return PROPOSED_L_ACCOUNT[key]
    # default by merchant keyword
    m = pdf.merchant
    if "유튜브" in m or "youtube" in m.lower():
        return "x58"
    if "APPLE_KCP" in m.upper():
        return "x99"
    # default = 식비 (대부분 쿠팡/CU/식당/음식)
    return "x50"


# ---- POST + tracking ----------------------------------------------------


async def insert_missing(missing: list[dict], client) -> None:
    import os, sqlite3, time, httpx
    from datetime import datetime
    from whooing_mcp.dates import KST
    from whooing_mcp.queue import open_db

    token = os.environ["WHOOING_AI_TOKEN"]
    headers = {"X-API-Key": token}

    def now_iso() -> str:
        return datetime.now(KST).isoformat(timespec="seconds")

    print(f"\n== INSERT mode — {len(missing)} entries ==", file=sys.stderr)

    success = 0
    fail = 0
    rate_window: list[float] = []
    RPM_CAP = 18  # 보수 (server 한도 20 — buffer)

    # 직전 probe 에서 이미 입력된 entry — 중복 방지
    ALREADY_INSERTED_PROBE = {
        ("20260201", -300, "SMS이용요금(면제) 02월분28일치"): "1710735",
    }

    async with httpx.AsyncClient(timeout=15.0) as http:
        for i, m in enumerate(missing, 1):
            pdf_dict = m["pdf"]
            card = m["card"]
            pdf = PdfEntry(**pdf_dict)
            total = pdf.amount + pdf.fee

            # probe 입력본 skip
            probe_key = (pdf.date, total, pdf.merchant)
            if probe_key in ALREADY_INSERTED_PROBE:
                eid = ALREADY_INSERTED_PROBE[probe_key]
                print(f"   [{i:02}/{len(missing)}] SKIP (probe 으로 이미 입력됨, "
                      f"entry_id={eid})  {pdf.merchant[:25]}", file=sys.stderr)
                # tracking 만 기록
                with open_db() as conn:
                    conn.execute(
                        """INSERT INTO statement_import_log
                           (source_file, source_kind, statement_period_start, statement_period_end,
                            issuer, card_label, entry_date, merchant, original_amount, fee_amount,
                            total_amount, currency, foreign_amount, exchange_rate,
                            section_id, l_account_id, r_account_id,
                            whooing_entry_id, status, imported_at, error_message, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            "/Users/neoocean/Desktop/2026-04.pdf", "pdf",
                            "20260315", "20260414",
                            "hana_card", card,
                            pdf.date, pdf.merchant, pdf.amount, pdf.fee, total,
                            "USD" if pdf.is_foreign else "KRW",
                            None, None,
                            SECTION,
                            resolve_l_account(pdf, total), R_ACCOUNT_HANA_CARD,
                            eid, "inserted_via_probe", now_iso(),
                            "probe inserted before main loop — same entry, just memo had '(probe)' suffix",
                            pdf.notes,
                        ),
                    )
                success += 1
                continue

            l_account_id = resolve_l_account(pdf, total)
            l_account = L_ACCOUNT_TYPE.get(l_account_id, "expenses")
            r_account_id = R_ACCOUNT_HANA_CARD
            r_account = "liabilities"

            # memo: 추적용 + 사용자가 카드/명세서 알아보기 위함
            memo = f"PDF: 2026-04 {card}"
            if pdf.is_foreign:
                memo += f" (USD ${pdf_dict.get('amount')/pdf.amount if pdf.amount else 0:.2f}? — 카드사 환율)"
            if pdf.notes:
                memo = f"{memo} — {pdf.notes}"

            # rate limit (sliding window)
            now_t = time.monotonic()
            rate_window = [t for t in rate_window if now_t - t < 60]
            if len(rate_window) >= RPM_CAP:
                wait = 60 - (now_t - rate_window[0]) + 0.5
                print(f"   [{i:02}/{len(missing)}] rate limit — sleeping {wait:.1f}s...",
                      file=sys.stderr)
                await asyncio.sleep(wait)
                rate_window = []

            payload = {
                "section_id": SECTION,
                "entry_date": pdf.date,
                "l_account": l_account,
                "l_account_id": l_account_id,
                "r_account": r_account,
                "r_account_id": r_account_id,
                "money": total,
                "item": pdf.merchant,
                "memo": memo,
            }

            print(f"   [{i:02}/{len(missing)}] POST {pdf.date} {total:>+8}  "
                  f"l={l_account_id} r={r_account_id}  {pdf.merchant[:25]}",
                  file=sys.stderr, end="")

            try:
                r = await http.post(
                    "https://whooing.com/api/entries.json",
                    headers=headers,
                    data=payload,  # form-encoded (whooing API 표준)
                )
                rate_window.append(time.monotonic())
                body = r.json()
                code = body.get("code", r.status_code)
                if code == 200:
                    # POST /entries 응답: results 가 list (created entries) — 1개만 보내면 [obj]
                    results = body.get("results") or []
                    if isinstance(results, list) and results:
                        new_entry_id = str(results[0].get("entry_id") or "(unknown)")
                    elif isinstance(results, dict):
                        new_entry_id = str(results.get("entry_id") or "(unknown)")
                    else:
                        new_entry_id = "(unknown)"
                    print(f"  ✓ entry_id={new_entry_id}", file=sys.stderr)
                    success += 1
                    status = "inserted"
                    error_msg = None
                else:
                    new_entry_id = None
                    print(f"  ✗ code={code} msg={body.get('message')!r}", file=sys.stderr)
                    fail += 1
                    status = "failed"
                    error_msg = body.get("message", "")
            except Exception as ex:
                new_entry_id = None
                print(f"  ✗ {ex}", file=sys.stderr)
                fail += 1
                status = "failed"
                error_msg = str(ex)

            # tracking sqlite
            with open_db() as conn:
                conn.execute(
                    """INSERT INTO statement_import_log
                       (source_file, source_kind, statement_period_start, statement_period_end,
                        issuer, card_label, entry_date, merchant, original_amount, fee_amount,
                        total_amount, currency, foreign_amount, exchange_rate,
                        section_id, l_account_id, r_account_id,
                        whooing_entry_id, status, imported_at, error_message, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "/Users/neoocean/Desktop/2026-04.pdf",
                        "pdf",
                        "20260315", "20260414",
                        "hana_card",
                        card,
                        pdf.date,
                        pdf.merchant,
                        pdf.amount,
                        pdf.fee,
                        total,
                        "USD" if pdf.is_foreign else "KRW",
                        None, None,  # 카드사 환율은 PDF page 4 에서 확인됐지만 본 import 에선 KRW 만 기록
                        SECTION,
                        l_account_id,
                        r_account_id,
                        new_entry_id,
                        status,
                        now_iso(),
                        error_msg,
                        pdf.notes,
                    ),
                )

    print(f"\n   summary: success={success} fail={fail}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
