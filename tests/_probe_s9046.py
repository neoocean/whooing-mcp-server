"""s9046 (Default 가계부) 의 accounts + 2026-03-15~04-14 entries 조회.

PDF import 작업 준비. 자격증명은 .env (auto-discovery).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from whooing_mcp.server import _build_client


SECTION = "s9046"
START = "20260315"
END = "20260414"


async def main() -> int:
    client, _ = _build_client()

    # accounts via direct GET (client.py 에는 method 없음 — 본 probe 한정 inline)
    print(f"== /accounts.json?section_id={SECTION} ==", file=sys.stderr)
    raw = await client._get("/accounts.json", params={"section_id": SECTION})
    print(f"   results type: {type(raw).__name__}", file=sys.stderr)
    if isinstance(raw, dict):
        # whooing 은 보통 type 별 dict — assets/liabilities/capital/income/expenses
        for type_key, type_val in raw.items():
            if isinstance(type_val, list):
                print(f"\n   [{type_key}] {len(type_val)} accounts:", file=sys.stderr)
                for a in type_val[:30]:
                    aid = a.get("account_id") or a.get("id") or "?"
                    title = a.get("title") or a.get("name") or "?"
                    print(f"     {aid}  {title}", file=sys.stderr)

    # entries
    print(f"\n== /entries.json?section_id={SECTION}&{START}~{END} ==", file=sys.stderr)
    entries = await client.list_entries(section_id=SECTION, start_date=START, end_date=END)
    print(f"   count: {len(entries)}", file=sys.stderr)
    # show first 3 + last 3 keys/shape
    for i, e in enumerate(entries[:3]):
        print(f"\n   [{i}] keys: {sorted(e.keys())}", file=sys.stderr)
        print(f"        date={e.get('entry_date')} money={e.get('money')} "
              f"item={(e.get('item') or '')[:30]!r} "
              f"l={(e.get('l_account') or '')[:15]!r}/{(e.get('r_account') or '')[:15]!r}",
              file=sys.stderr)

    # for dedup planning: dict by (date, money)
    by_date_money: dict[tuple, list] = {}
    for e in entries:
        d = e.get("entry_date")
        m = e.get("money")
        if d and m is not None:
            by_date_money.setdefault((d, int(m)), []).append(e.get("item"))
    print(f"\n   unique (date, money) keys: {len(by_date_money)}", file=sys.stderr)

    # save raw entries to /tmp for dedup work (not commit)
    Path("/tmp/whooing-s9046-entries.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n   wrote /tmp/whooing-s9046-entries.json ({len(entries)} entries)",
          file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
