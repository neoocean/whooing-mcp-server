"""SQLite db → Perforce 자동 sync.

사용자 정책 (2026-05-09):
  * SQLite db 는 매 변경마다 P4 에 submit
  * default changelist 사용 X — 매번 별도 numbered CL
  * description 에 변경 내용 상세 기입
  * GitHub 으로는 가지 않음 (.gitignore 차단)

본 모듈은 도구가 db 를 변경한 직후 호출. 실패는 silent (P4 환경 없는
사용자도 도구 사용 가능) — 결과는 호출자가 도구 응답에 포함.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from whooing_mcp.queue import default_queue_path

log = logging.getLogger(__name__)

# 일부 환경에선 p4 가 없을 수 있음. 그땐 silent skip.
_P4_AVAILABLE: bool | None = None


def is_p4_available() -> bool:
    """`p4` CLI 가 PATH 에 있고 동작 가능한지. 첫 호출 시 캐시."""
    global _P4_AVAILABLE
    if _P4_AVAILABLE is not None:
        return _P4_AVAILABLE
    if shutil.which("p4") is None:
        _P4_AVAILABLE = False
        return False
    try:
        r = subprocess.run(
            ["p4", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _P4_AVAILABLE = r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        _P4_AVAILABLE = False
    return _P4_AVAILABLE


def is_db_in_depot(db_path: Path) -> bool:
    """db 파일이 depot 에 등록돼 있는지. 등록 안 됐으면 sync 의미 없음 — 사용자가
    먼저 `p4 add` 해야 함 (별도 setup CL)."""
    try:
        r = subprocess.run(
            ["p4", "fstat", str(db_path)],
            capture_output=True, text=True, timeout=5,
        )
        # depotFile 또는 headRev 가 있으면 등록된 것
        return r.returncode == 0 and ("depotFile" in r.stdout or "headRev" in r.stdout)
    except (subprocess.TimeoutExpired, OSError):
        return False


def sync_db_to_p4(action_summary: str) -> dict:
    """db 가 변경됐다면 새 numbered CL 만들고 submit.

    Returns:
      { ok: bool, skipped: bool, cl?: int, message: str }
      ok=True && skipped=True 면 변경 없거나 P4 환경 없음 (정상).
      ok=False 면 사용자에게 알릴 가치 있는 실패.
    """
    db_path = default_queue_path()

    if not db_path.exists():
        return {"ok": True, "skipped": True, "message": "db 파일 없음 (sync 불필요)"}

    if not is_p4_available():
        return {"ok": True, "skipped": True, "message": "p4 CLI 없음 (sync skip)"}

    if not is_db_in_depot(db_path):
        return {
            "ok": True,
            "skipped": True,
            "message": (
                f"{db_path.name} 가 P4 depot 에 미등록 — `p4 add` 별도 setup 필요. "
                "본 도구는 db 가 등록된 후부터 자동 sync."
            ),
        }

    # 변경 감지: p4 reconcile -n (preview, not opened — DESIGN §13.2 정책 검증)
    # p4 diff 는 _opened_ 파일만 비교하므로 db 가 아직 안 열렸을 땐 항상 빈 출력.
    # reconcile -n 은 closed 파일도 depot digest 와 비교해 변경 감지.
    try:
        recon = subprocess.run(
            ["p4", "reconcile", "-n", str(db_path)],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "skipped": False, "message": f"p4 reconcile 실패: {e}"}

    # reconcile -n 출력 패턴 (p4 버전마다 약간 다름):
    #   변경됨: "... - opened for edit" / "reconcile to edit"
    #   추가됨: "... - opened for add"   / "reconcile to add"
    #   삭제됨: "... - opened for delete" / "reconcile to delete"
    #   변경 없음: "...no file(s) to reconcile" 또는 빈 출력
    combined = (recon.stdout + recon.stderr).lower()
    has_change = any(
        marker in combined
        for marker in (
            "opened for edit", "opened for add", "opened for delete",
            "reconcile to edit", "reconcile to add", "reconcile to delete",
        )
    )
    if not has_change:
        return {"ok": True, "skipped": True, "message": "db 변경 없음"}

    # 별도 numbered CL 생성
    desc = _build_description(action_summary, recon.stdout)
    try:
        change_form = (
            "Change: new\n"
            f"Description:\n\t{desc.replace(chr(10), chr(10) + chr(9))}\n"
            "Files:\n"
        )
        r = subprocess.run(
            ["p4", "change", "-i"],
            input=change_form, capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return {"ok": False, "skipped": False, "message": f"p4 change -i 실패: {r.stderr}"}
        # 출력: "Change <N> created."
        cl_num = None
        for word in r.stdout.split():
            if word.isdigit():
                cl_num = int(word)
                break
        if cl_num is None:
            return {"ok": False, "skipped": False, "message": f"CL 번호 파싱 실패: {r.stdout!r}"}

        # p4 edit -c
        e = subprocess.run(
            ["p4", "edit", "-c", str(cl_num), str(db_path)],
            capture_output=True, text=True, timeout=10,
        )
        if e.returncode != 0:
            return {"ok": False, "skipped": False, "message": f"p4 edit 실패: {e.stderr}"}

        # p4 submit
        s = subprocess.run(
            ["p4", "submit", "-c", str(cl_num)],
            capture_output=True, text=True, timeout=30,
        )
        if s.returncode != 0:
            return {"ok": False, "skipped": False, "message": f"p4 submit 실패: {s.stderr}"}

        # 'Change N renamed change M and submitted.' 가능 — 새 번호 추출
        final_cl = cl_num
        for line in s.stdout.splitlines():
            if "submitted" in line.lower():
                for word in line.split():
                    if word.isdigit():
                        final_cl = int(word)
        return {
            "ok": True,
            "skipped": False,
            "cl": final_cl,
            "message": f"CL {final_cl} submitted ({action_summary})",
        }
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "skipped": False, "message": f"p4 명령 실패: {e}"}


def _build_description(action_summary: str, diff_output: str) -> str:
    """detailed CL description (사용자 정책 — 무엇이 변경되었는지 상세 기입)."""
    db_path = default_queue_path()
    diff_summary = (diff_output[:500] + "...") if len(diff_output) > 500 else diff_output
    return (
        f"whooing-mcp-server-wrapper: SQLite db auto-sync — {action_summary}\n"
        f"\n"
        f"본 CL 은 wrapper 가 사용자 도구 호출 직후 자동으로 생성한 sync CL.\n"
        f"\n"
        f"Action: {action_summary}\n"
        f"DB file: {db_path.name}\n"
        f"\n"
        f"p4 diff -ds 요약:\n"
        f"  {diff_summary.strip()}\n"
        f"\n"
        f"Notes\n"
        f"-----\n"
        f"* 본 CL 은 default 가 아닌 자동 생성 numbered CL.\n"
        f"* GitHub 으로는 가지 않음 (.gitignore 가 *.sqlite 차단).\n"
        f"* 본 CL 에 추가 파일 변경이 있으면 사용자가 수동 검토 권장."
    )
