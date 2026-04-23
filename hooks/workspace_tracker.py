#!/usr/bin/env python3
"""workspace_tracker: SessionStart/Stop Hook으로 세션 기록 + cmux 레이아웃을 갱신한다.

SessionStart: pane 위치 즉시 기록 + cmux 탭 제목 "⏳ 새 세션"
Stop:         제목 업데이트 (첫 user 메시지 또는 cmux 탭 제목) + cmux 탭 제목 반영

두 파일을 관리:
1. sessions.json (Dropbox, 프로젝트별) — 세션 목록, 크로스 디바이스 동기화
2. cmux-layout.json (로컬, 기기별) — cmux 워크스페이스/pane 레이아웃 복원용

입력 (stdin JSON):
    SessionStart: {"session_id": "...", "cwd": "..."}
    Stop:         {"session_id": "...", "transcript_path": "...", "cwd": "..."}

환경변수로 모드 구분:
    CC_HOOK_EVENT=start → SessionStart 모드
    CC_HOOK_EVENT=stop  → Stop 모드 (기본)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
from common import (
    dropbox_sync_dir,
    extract_messages,
    find_project_root,
    parse_jsonl,
    project_hash,
)

LAYOUT_PATH = Path.home() / ".cc-toolkit" / "cache" / "cmux-layout.json"


# ── 입력 ───────────────────────────────────────────────────

def read_hook_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        return {}


def extract_title(jsonl_path: Path) -> str:
    """JSONL에서 첫 user 메시지를 제목으로 추출."""
    try:
        entries = parse_jsonl(jsonl_path)
        messages = extract_messages(entries)
        for m in messages:
            if m.role == "user":
                return re.sub(r"\s+", " ", m.text.strip())[:50]
    except Exception:
        pass
    return ""


def read_custom_title(jsonl_path: Path) -> str:
    """JSONL에서 custom-title 엔트리(claude /rename)를 읽는다. 마지막 것 우선."""
    title = ""
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("type") == "custom-title":
                    title = d.get("customTitle", "")
    except Exception:
        pass
    return title


def write_custom_title(jsonl_path: Path, session_id: str, title: str) -> None:
    """JSONL에 custom-title 엔트리를 추가. claude --resume 목록에 반영된다."""
    entry = json.dumps({
        "type": "custom-title",
        "customTitle": title,
        "sessionId": session_id,
    }, ensure_ascii=False)
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


# ── cmux 정보 ──────────────────────────────────────────────

def get_cmux_caller() -> dict | None:
    try:
        result = subprocess.run(
            ["cmux", "identify"], capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout).get("caller", {})
    except Exception:
        return None


def get_pane_index(pane_ref: str, workspace_ref: str) -> int | None:
    try:
        result = subprocess.run(
            ["cmux", "tree", "--all"], capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return None
        in_ws = False
        idx = 0
        for line in result.stdout.splitlines():
            ws_match = re.search(r"(workspace:\d+)", line)
            if ws_match:
                in_ws = (ws_match.group(1) == workspace_ref)
                idx = 0
                continue
            if not in_ws:
                continue
            pane_match = re.search(r"(pane:\d+)", line)
            if pane_match:
                if pane_match.group(1) == pane_ref:
                    return idx
                idx += 1
    except Exception:
        pass
    return None


def get_workspace_name(workspace_ref: str) -> str:
    try:
        result = subprocess.run(
            ["cmux", "tree", "--all"], capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return workspace_ref
        for line in result.stdout.splitlines():
            if workspace_ref in line:
                m = re.search(r'"([^"]+)"', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return workspace_ref


def get_cmux_tab_title(surface_ref: str) -> str:
    """cmux tree에서 surface의 현재 탭 제목을 읽는다."""
    try:
        result = subprocess.run(
            ["cmux", "tree", "--all"], capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return ""
        for line in result.stdout.splitlines():
            if surface_ref in line:
                # surface surface:1 [terminal] "탭 제목" ...
                m = re.search(r'"([^"]+)"', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ""


def set_cmux_tab_title(surface_ref: str, title: str) -> None:
    """cmux 탭 제목 설정."""
    try:
        subprocess.run(
            ["cmux", "rename-tab", "--surface", surface_ref, title],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        pass


# ── Dropbox 슬롯 ──────────────────────────────────────────

def find_dropbox_slot(cwd: str) -> Path | None:
    sync_root = dropbox_sync_dir()
    if not sync_root.exists():
        return None
    for slot in sync_root.iterdir():
        meta_path = slot / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for device_info in meta.get("devices", {}).values():
                if device_info.get("project_path") == cwd:
                    return slot
        except Exception:
            continue
    return None


# ── sessions.json (프로젝트별) ─────────────────────────────

def update_sessions(sessions_path: Path, session_id: str, title: str, cwd: str) -> None:
    data = {}
    if sessions_path.exists():
        try:
            data = json.loads(sessions_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    sessions = data.get("sessions", [])
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    found = False
    for s in sessions:
        if s.get("session_id") == session_id:
            if title:
                s["title"] = title
            s["last_active"] = now_iso
            found = True
            break
    if not found:
        sessions.append({
            "session_id": session_id,
            "title": title or "(진행 중)",
            "cwd": cwd,
            "last_active": now_iso,
        })

    cutoff = datetime.now(tz=timezone.utc).timestamp() - (30 * 86400)
    sessions = [s for s in sessions if _parse_ts(s.get("last_active", "")) > cutoff]

    data["sessions"] = sessions
    data["updated_at"] = now_iso
    sessions_path.parent.mkdir(parents=True, exist_ok=True)
    sessions_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── cmux-layout.json (기기별) ─────────────────────────────

def update_layout(
    session_id: str, title: str, cwd: str,
    workspace_name: str = "", pane_index: int | None = None,
) -> None:
    data = {}
    if LAYOUT_PATH.exists():
        try:
            data = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    workspaces = data.get("workspaces", {})
    ws = workspaces.get(workspace_name, {"panes": []})
    panes = ws.get("panes", [])
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    if pane_index is not None:
        # 같은 index 교체
        existing = [p for p in panes if p.get("index") == pane_index]
        if existing:
            p = existing[0]
            p["session_id"] = session_id
            if title:
                p["title"] = title
            p["cwd"] = cwd
            p["last_active"] = now_iso
        else:
            panes.append({
                "index": pane_index,
                "session_id": session_id,
                "title": title or "(진행 중)",
                "cwd": cwd,
                "last_active": now_iso,
            })
    else:
        found = False
        for p in panes:
            if p.get("session_id") == session_id:
                if title:
                    p["title"] = title
                p["cwd"] = cwd
                p["last_active"] = now_iso
                found = True
                break
        if not found:
            panes.append({
                "session_id": session_id,
                "title": title or "(진행 중)",
                "cwd": cwd,
                "last_active": now_iso,
            })

    panes.sort(key=lambda p: (p.get("index") is None, p.get("index", 999)))
    ws["panes"] = panes
    workspaces[workspace_name] = ws
    data["workspaces"] = workspaces
    data["updated_at"] = now_iso

    LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAYOUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")



def _is_system_title(title: str) -> bool:
    """cmux/터미널이 자동으로 설정하는 제목인지 판별."""
    if not title:
        return True
    # 경로
    if title.startswith(("~/", "/", ".")):
        return True
    # 명령어/프로세스 이름 (소문자 비교)
    lower = title.lower().strip()
    if lower.startswith(("cmux", "claude", "cc-", "python", "bash", "zsh", "vim", "nvim")):
        return True
    # cmux 기본
    if title in ("~", "새 세션", "(제목 없음)", "(진행 중)"):
        return True
    # cmux claude-hook이 설정하는 제목 (스피너 문자 + "Claude Code")
    if "Claude Code" in title or "claude code" in lower:
        return True
    return False


def _parse_ts(ts_str: str) -> float:
    try:
        return datetime.fromisoformat(ts_str).timestamp()
    except Exception:
        return 0


# ── main ───────────────────────────────────────────────────

def main() -> None:
    hook_input = read_hook_input()
    session_id = hook_input.get("session_id", "")
    cwd = hook_input.get("cwd", "")
    transcript_path = hook_input.get("transcript_path", "")
    event = os.environ.get("CC_HOOK_EVENT", "stop")

    if not session_id or not cwd:
        return

    # cmux 정보
    cmux_info = get_cmux_caller()
    pane_ref = cmux_info.get("pane_ref", "") if cmux_info else ""
    workspace_ref = cmux_info.get("workspace_ref", "") if cmux_info else ""
    surface_ref = cmux_info.get("surface_ref", "") if cmux_info else ""
    pane_index = get_pane_index(pane_ref, workspace_ref) if pane_ref else None
    workspace_name = get_workspace_name(workspace_ref) if workspace_ref else "default"

    if event == "end":
        # ── SessionEnd ── 세션 종료 시 탭 제목을 cmux 기본값으로 초기화
        if surface_ref:
            import time
            time.sleep(1.5)
            # 빈 문자열로 초기화 (cmux가 기본값으로 돌림)
            set_cmux_tab_title(surface_ref, cwd.replace(str(Path.home()), "~"))
        return

    if event == "start":
        # ── SessionStart ──
        # 1. 레이아웃 즉시 기록 (강제 종료 대비)
        update_layout(session_id, "", cwd, workspace_name, pane_index)

        # 2. sessions.json에 등록
        project_root = find_project_root(Path(cwd))
        dropbox_slot = find_dropbox_slot(str(project_root))
        sessions_path = (dropbox_slot / "sessions.json") if dropbox_slot else (project_root / ".claude-sessions.json")
        update_sessions(sessions_path, session_id, "", cwd)

        # 탭 제목은 Stop Hook에서 설정 (cc-restore가 미리 설정한 제목을 덮어쓰지 않기 위해)


    else:
        # ── Stop ──
        from common import hash_to_jsonl_dir

        # JSONL 경로 확정
        jsonl_path = None
        if transcript_path and Path(transcript_path).exists():
            jsonl_path = Path(transcript_path)
        else:
            jsonl_dir = hash_to_jsonl_dir(cwd)
            candidate = jsonl_dir / f"{session_id}.jsonl"
            if candidate.exists():
                jsonl_path = candidate

        # 1. 제목 결정 (우선순위: cmux 탭 수동 수정 > custom-title > JSONL 첫 메시지)
        jsonl_title = extract_title(jsonl_path) if jsonl_path else ""
        custom_title = read_custom_title(jsonl_path) if jsonl_path else ""

        title = custom_title or jsonl_title or "(제목 없음)"

        # cmux 탭 제목이 위와 다르면 사용자가 수동 수정한 것 → 최우선
        user_renamed = False
        if surface_ref:
            tab_title = get_cmux_tab_title(surface_ref)
            if (tab_title
                and tab_title != jsonl_title
                and tab_title != custom_title
                and not _is_system_title(tab_title)):
                title = tab_title
                user_renamed = True

        # 2. 양방향 동기화: 최종 제목을 JSONL custom-title로 쓰기
        #    (claude --resume 목록에 반영)
        if jsonl_path and title and title != custom_title:
            write_custom_title(jsonl_path, session_id, title)

        # 3. 레이아웃 업데이트
        update_layout(session_id, title, cwd, workspace_name, pane_index)

        # 4. sessions.json 업데이트
        project_root = find_project_root(Path(cwd))
        dropbox_slot = find_dropbox_slot(str(project_root))
        sessions_path = (dropbox_slot / "sessions.json") if dropbox_slot else (project_root / ".claude-sessions.json")
        update_sessions(sessions_path, session_id, title, cwd)

        # 5. cmux 탭 제목 동기화 (cmux 내장 hook이 "Claude Code"로 덮어쓰므로 지연)
        if surface_ref and title:
            import time
            time.sleep(2)
            set_cmux_tab_title(surface_ref, title[:40])


if __name__ == "__main__":
    main()
