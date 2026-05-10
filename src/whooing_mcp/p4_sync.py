"""SQLite db + 첨부파일 → Perforce 자동 sync.

사용자 정책 (2026-05-09):
  * SQLite db / 첨부파일 등 wrapper 가 만든 변경은 매번 별도 numbered CL 로 submit
  * default changelist 사용 X
  * description 에 변경 내용 상세 기입
  * GitHub 으로는 가지 않음 (.gitignore 차단 / attachments/* 차단)

본 모듈은 도구가 db / 파일 을 변경한 직후 호출. 실패는 silent — 결과는
호출자가 도구 응답에 포함.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from whooing_mcp.config import load_config
from whooing_mcp.queue import default_queue_path

log = logging.getLogger(__name__)

# 일부 환경에선 p4 가 없을 수 있음. 그땐 silent skip.
_P4_AVAILABLE: bool | None = None

# 확장자 → p4 file type. binary 가 안전한 default.
_BINARY_EXTS = {".sqlite", ".sqlite3", ".db", ".pdf", ".png", ".jpg", ".jpeg",
                ".gif", ".webp", ".heic", ".zip", ".docx", ".xlsx", ".pptx"}


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
            ["p4", "info"], capture_output=True, text=True, timeout=5,
        )
        _P4_AVAILABLE = r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        _P4_AVAILABLE = False
    return _P4_AVAILABLE


def is_db_in_depot(path: Path) -> bool:
    """파일이 depot 에 등록돼 있는지 (이름은 historical — 임의 path 에 동작)."""
    try:
        r = subprocess.run(
            ["p4", "fstat", str(path)],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and (
            "depotFile" in r.stdout or "headRev" in r.stdout
        )
    except (subprocess.TimeoutExpired, OSError):
        return False


def _p4_filetype(path: Path) -> str:
    """확장자 기반 file type — binary 가 안전한 default."""
    if path.suffix.lower() in _BINARY_EXTS:
        return "binary"
    return "text"


def _detect_p4_action(path: Path) -> str | None:
    """p4 reconcile -n 으로 'add' / 'edit' / None (변경 없음) 판단.

    P4IGNORE 우회 (attachments/* 가 워크스페이스 ignore 에 안 잡혀도 안전).
    """
    if not is_db_in_depot(path):
        # depot 미등록 → add 해야
        # (단 reconcile -n 도 'opened for add' 를 보고하면 일치)
        try:
            recon = subprocess.run(
                ["p4", "reconcile", "-n", "-a", str(path)],
                capture_output=True, text=True, timeout=10,
                env={**__import__("os").environ, "P4IGNORE": "/dev/null"},
            )
            combined = (recon.stdout + recon.stderr).lower()
            if "opened for add" in combined or "reconcile to add" in combined:
                return "add"
            # reconcile 가 아무 것도 보고 안 했지만 fstat 가 등록 안 됐다고 함:
            # 안전하게 add 시도
            return "add"
        except (subprocess.TimeoutExpired, OSError):
            return "add"

    # 등록됨 → edit 가 필요한지 확인
    try:
        recon = subprocess.run(
            ["p4", "reconcile", "-n", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        combined = (recon.stdout + recon.stderr).lower()
        if any(m in combined for m in (
            "opened for edit", "reconcile to edit",
            "opened for delete", "reconcile to delete",
        )):
            return "edit"
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None


def sync_db_to_p4(action_summary: str) -> dict:
    """db 만 sync (backwards compat). 내부적으로 sync_paths_to_p4 호출."""
    return sync_paths_to_p4(action_summary, paths=[default_queue_path()])


def sync_paths_to_p4(action_summary: str, paths: list[Path]) -> dict:
    """주어진 path 들의 변경 (add/edit) 을 한 CL 로 묶어 submit.

    각 path 는:
      * 존재하지 않으면 skip
      * depot 미등록 + 존재 → add
      * depot 등록 + 변경 있음 → edit
      * 변경 없음 → skip

    Config (whooing-mcp.toml [p4_sync] enabled) false 면 silent skip.

    Returns:
      { ok, skipped, cl?, message, files?: [{path, action}] }
    """
    cfg = load_config()
    if not cfg.p4_sync_enabled:
        return {
            "ok": True, "skipped": True,
            "message": "config: p4_sync 비활성화 (whooing-mcp.toml [p4_sync] enabled=true 로 켜기)",
        }
    if not is_p4_available():
        return {"ok": True, "skipped": True, "message": "p4 CLI 없음 (sync skip)"}

    # 각 path 의 action 결정
    files_to_open: list[tuple[Path, str]] = []
    for p in paths:
        if not p.exists():
            log.debug("sync_paths_to_p4: %s 존재 X — skip", p)
            continue
        action = _detect_p4_action(p)
        if action:
            files_to_open.append((p, action))

    if not files_to_open:
        return {"ok": True, "skipped": True, "message": "sync 대상 파일 변경 없음"}

    # CL description 빌드
    desc = _build_description(action_summary, files_to_open)

    cl_num: int | None = None
    opened_paths: list[Path] = []  # 성공적으로 open 된 파일 (cleanup 용)
    try:
        # 1) p4 change -i (CL 생성 — 이 시점에는 attached file 이 없음)
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
        for word in r.stdout.split():
            if word.isdigit():
                cl_num = int(word)
                break
        if cl_num is None:
            return {"ok": False, "skipped": False, "message": f"CL 번호 파싱 실패: {r.stdout!r}"}

        # 2) 각 파일 add 또는 edit. 하나라도 실패하면 cleanup 으로 진입.
        opened_files: list[dict] = []
        import os as _os
        env = {**_os.environ, "P4IGNORE": "/dev/null"}
        for path, action in files_to_open:
            if action == "add":
                cmd = ["p4", "add", "-c", str(cl_num),
                       "-t", _p4_filetype(path), str(path)]
            else:  # edit
                cmd = ["p4", "edit", "-c", str(cl_num), str(path)]
            sp = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)
            if sp.returncode != 0:
                _cleanup_failed_cl(cl_num, opened_paths)
                return {
                    "ok": False, "skipped": False,
                    "message": f"p4 {action} 실패 ({path}): {sp.stderr.strip()} — 빈 CL 정리됨",
                }
            opened_files.append({"path": str(path), "action": action})
            opened_paths.append(path)

        # 3) submit
        s = subprocess.run(
            ["p4", "submit", "-c", str(cl_num)],
            capture_output=True, text=True, timeout=60,
        )
        if s.returncode != 0:
            _cleanup_failed_cl(cl_num, opened_paths)
            return {
                "ok": False, "skipped": False,
                "message": f"p4 submit 실패: {s.stderr.strip()} — 빈 CL 정리됨",
            }

        # CL renamed 처리
        final_cl = cl_num
        for line in s.stdout.splitlines():
            if "submitted" in line.lower():
                for word in line.split():
                    if word.isdigit():
                        final_cl = int(word)
        return {
            "ok": True, "skipped": False,
            "cl": final_cl,
            "files": opened_files,
            "message": f"CL {final_cl} submitted ({len(opened_files)} files: {action_summary})",
        }
    except (subprocess.TimeoutExpired, OSError) as e:
        if cl_num is not None:
            _cleanup_failed_cl(cl_num, opened_paths)
        return {"ok": False, "skipped": False, "message": f"p4 명령 실패: {e}"}


def _cleanup_failed_cl(cl_num: int, opened_paths: list[Path]) -> None:
    """add/edit/submit 실패 시 — open 된 파일 revert + CL 삭제.

    Without this, 빈 (또는 부분 채워진) numbered CL 이 P4 서버에 영원히 남는다
    (서버 leak — 검증 2026-05-10: 60+ 빈 CL 누적). best-effort: cleanup 자체가
    실패해도 silent skip — 본 함수는 caller 의 에러 응답을 가리지 않는다.
    """
    import os as _os
    env = {**_os.environ, "P4IGNORE": "/dev/null"}
    # 1) open 된 파일이 있으면 revert
    for p in opened_paths:
        try:
            subprocess.run(
                ["p4", "revert", "-c", str(cl_num), str(p)],
                capture_output=True, text=True, timeout=10, env=env,
            )
        except (subprocess.TimeoutExpired, OSError):
            log.warning("cleanup: revert failed for %s (cl=%d)", p, cl_num)
    # 2) CL 삭제
    try:
        subprocess.run(
            ["p4", "change", "-d", str(cl_num)],
            capture_output=True, text=True, timeout=10, env=env,
        )
        log.info("cleanup: empty CL %d deleted", cl_num)
    except (subprocess.TimeoutExpired, OSError):
        log.warning("cleanup: failed to delete CL %d", cl_num)


def _build_description(action_summary: str, files_to_open: list[tuple[Path, str]]) -> str:
    """auto-sync CL description (사용자 정책 — 변경 내용 상세 기입)."""
    db_path = default_queue_path()
    files_summary = "\n".join(
        f"  {act:>5}  {path}" for path, act in files_to_open
    )
    return (
        f"whooing-mcp-server-wrapper: auto-sync — {action_summary}\n"
        f"\n"
        f"본 CL 은 wrapper 가 사용자 도구 호출 직후 자동으로 생성한 sync CL.\n"
        f"\n"
        f"Action: {action_summary}\n"
        f"\n"
        f"Files\n"
        f"-----\n"
        f"{files_summary}\n"
        f"\n"
        f"Notes\n"
        f"-----\n"
        f"* 본 CL 은 default 가 아닌 자동 생성 numbered CL.\n"
        f"* db ({db_path.name}) 와 첨부파일은 .gitignore 가 GitHub 미러를 차단.\n"
        f"* 본 CL 에 의도치 않은 파일이 포함됐으면 사용자가 수동 검토 권장."
    )
