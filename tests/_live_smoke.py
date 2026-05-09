"""Live smoke for CL #1 — manual run, requires real WHOOING_AI_TOKEN.

Two purposes:
  1. **Validate** that auth + GET /sections.json + GET /entries.json + the audit
     tool all work end-to-end against the real whooing API.
  2. **Inspect shape** of real responses (stderr only — never written to disk)
     so we can hand-write **fully synthetic** fixtures with the right field
     names. Real values (money, item, memo, account names, webhook tokens,
     totals) are NEVER persisted.

Usage:
    python tests/_live_smoke.py [--section s133178] [--days 90]

Outputs:
  - stderr: shape diagnosis + tool result counts (NOT real values)
  - tests/fixtures/sections_sample.json  (synthetic, hand-written below)
  - tests/fixtures/entries_sample.json   (synthetic, hand-written below)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from whooing_mcp.dates import days_ago_yyyymmdd, today_yyyymmdd  # noqa: E402
from whooing_mcp.server import _build_client  # noqa: E402
from whooing_mcp.tools.audit import audit_recent_ai_entries  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"

# ---- SYNTHETIC fixture content (hand-written, no real data) -----------------

SYNTHETIC_SECTIONS = [
    {
        "section_id": "s_FAKE_1",
        "title": "<test-section-1>",
        "currency": "KRW",
        "decimal_places": 0,
        "date_format": "YMD",
        "use_ai": "y",
    },
    {
        "section_id": "s_FAKE_2",
        "title": "<test-section-2>",
        "currency": "KRW",
        "decimal_places": 0,
        "date_format": "YMD",
        "use_ai": "y",
    },
]

SYNTHETIC_ENTRIES = [
    {
        "entry_id": "e_fake_001",
        "section_id": "s_FAKE_1",
        "entry_date": "20260507",
        "money": 6200,
        "item": "<fake-merchant-A>",
        "memo": "[ai] LLM 음성 위임 테스트",
        "l_account": "<fake-acct-expense>",
        "r_account": "<fake-acct-card>",
    },
    {
        "entry_id": "e_fake_002",
        "section_id": "s_FAKE_1",
        "entry_date": "20260506",
        "money": 12500,
        "item": "<fake-merchant-B>",
        "memo": "",
        "l_account": "<fake-acct-expense>",
        "r_account": "<fake-acct-card>",
    },
    {
        "entry_id": "e_fake_003",
        "section_id": "s_FAKE_1",
        "entry_date": "20260505",
        "money": 3500,
        "item": "[ai] starts in item not memo",
        "memo": "",
        "l_account": "<fake-acct-expense>",
        "r_account": "<fake-acct-cash>",
    },
]


# ---- shape inspection (stderr only) -----------------------------------------


def _shape_only(d: dict) -> dict[str, str]:
    """Return {key: type-name} only — no values, safe to print."""
    return {k: type(v).__name__ for k, v in d.items()}


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--section", help="override section_id; default uses .env or first")
    p.add_argument("--days", type=int, default=90, help="days back for entries shape probe")
    args = p.parse_args()

    client, env_section_id = _build_client()
    print("== client built ==", file=sys.stderr)
    print(f"   env section_id: {env_section_id!r}", file=sys.stderr)

    # 1) sections — keys only, no values
    print("\n== GET /sections.json (shape) ==", file=sys.stderr)
    sections = await client.list_sections()
    print(f"   count: {len(sections)}", file=sys.stderr)
    if sections:
        print(f"   [0] keys-and-types: {_shape_only(sections[0])}", file=sys.stderr)
    if not sections:
        print("ERROR: no sections", file=sys.stderr)
        return 2

    sid = args.section or env_section_id or sections[0].get("section_id") or sections[0].get("id")
    print(f"   chosen section_id: {sid!r}", file=sys.stderr)

    # 2) entries shape — wider range to maximize chance of seeing one
    start, end = days_ago_yyyymmdd(args.days - 1), today_yyyymmdd()
    print(f"\n== GET /entries.json (shape probe; start={start} end={end}) ==", file=sys.stderr)
    entries = await client.list_entries(section_id=sid, start_date=start, end_date=end)
    print(f"   count: {len(entries)}", file=sys.stderr)
    if entries:
        print(f"   [0] keys-and-types: {_shape_only(entries[0])}", file=sys.stderr)
    else:
        print("   (no entries — synthetic fixture has assumed schema; revisit if real differs)",
              file=sys.stderr)

    # 3) audit tool dry-run on real range
    print("\n== audit_recent_ai_entries(days=7) ==", file=sys.stderr)
    result = await audit_recent_ai_entries(client, section_id=sid, days=7, marker="[ai]")
    print(f"   total matching: {result['total']}", file=sys.stderr)
    print(f"   scanned_total: {result['scanned_total']}", file=sys.stderr)
    print(f"   date_range: {result['date_range']}", file=sys.stderr)

    # ---- write SYNTHETIC fixtures (no real values from API) ----
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURE_DIR / "sections_sample.json").write_text(
        json.dumps(SYNTHETIC_SECTIONS, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (FIXTURE_DIR / "entries_sample.json").write_text(
        json.dumps(SYNTHETIC_ENTRIES, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n   wrote SYNTHETIC fixtures to {FIXTURE_DIR}/ (no real API values)",
          file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
