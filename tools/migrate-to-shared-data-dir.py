#!/usr/bin/env python3
"""v0.1.x → v0.2.0 마이그레이션 — db + attachments 를 공유 path 로 이동.

  옛 위치:  <project>/whooing-data.sqlite  +  <project>/attachments/
  새 위치:  $WHOOING_DATA_DIR (기본 ~/.whooing/) /whooing-data.sqlite  + /attachments/

멱등 — 이미 새 위치에 데이터가 있으면 noop. dry-run 으로 미리 확인 권장.

Usage:
  python tools/migrate-to-shared-data-dir.py            # dry-run
  python tools/migrate-to-shared-data-dir.py --confirm  # 실제 mv
  WHOOING_DATA_DIR=~/somewhere/else python tools/migrate-...py  # custom root
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--confirm", action="store_true",
        help="실제 mv 수행. 없으면 dry-run (어떻게 옮길지 보여만 줌).",
    )
    args = p.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    legacy_db = project_root / "whooing-data.sqlite"
    legacy_attach = project_root / "attachments"

    data_dir = Path(os.getenv("WHOOING_DATA_DIR", "~/.whooing")).expanduser()
    new_db = data_dir / "whooing-data.sqlite"
    new_attach = data_dir / "attachments"

    print(f"\n=== whooing-mcp-server-wrapper v0.1.x → v0.2.0 migration ===")
    print(f"  legacy db:        {legacy_db}")
    print(f"  legacy attach:    {legacy_attach}/")
    print(f"  → new db:         {new_db}")
    print(f"  → new attach:     {new_attach}/")
    print()

    actions: list[tuple[str, Path, Path]] = []

    # db
    if legacy_db.exists():
        if new_db.exists():
            print(f"[skip] {new_db} 이미 존재 — 옛 db 는 그대로 둠.")
        else:
            actions.append(("mv-db", legacy_db, new_db))
    else:
        print("[skip] 옛 db 없음 — 본 머신은 v0.1.x 사용 안 한 것으로 추정.")

    # attachments dir
    if legacy_attach.exists() and any(legacy_attach.iterdir()):
        if new_attach.exists() and any(new_attach.iterdir()):
            print(f"[skip] {new_attach}/ 이미 비어있지 않음 — 충돌 위험으로 자동 mv X.")
            print(f"       수동 병합 권장: rsync -a {legacy_attach}/ {new_attach}/")
        else:
            actions.append(("mv-attach", legacy_attach, new_attach))
    else:
        print("[skip] 옛 attachments 비어 있음.")

    if not actions:
        print("\n할 일 없음 — 마이그레이션 완료 또는 불필요.")
        return 0

    print(f"\n예정 작업 ({len(actions)}):")
    for op, src, dst in actions:
        print(f"  [{op}] {src} → {dst}")

    if not args.confirm:
        print("\n--confirm 로 재실행하면 실 수행. 본 출력은 dry-run.")
        return 0

    print("\n실행 중...")
    data_dir.mkdir(parents=True, exist_ok=True)
    for op, src, dst in actions:
        try:
            shutil.move(str(src), str(dst))
            print(f"  [ok] {op}: {src} → {dst}")
        except OSError as e:
            print(f"  [FAIL] {op}: {e}", file=sys.stderr)
            return 2

    print(f"\n완료.")
    print(f"이제 .env 에 다음 추가 권장 (또는 default {data_dir} 그대로 사용):")
    print(f"  WHOOING_DATA_DIR={data_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
