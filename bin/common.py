"""cc-toolkit 공통 유틸리티.

경로 해결, JSONL 파싱, OS 감지, 멀티플렉서 추상화를 제공한다.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


# ── OS / 경로 ──────────────────────────────────────────────

def current_os() -> Literal["mac", "windows"]:
    return "mac" if platform.system() == "Darwin" else "windows"


def mux_command() -> str:
    """cmux (mac) 또는 wmux (windows)."""
    return "cmux" if current_os() == "mac" else "wmux"


def claude_projects_dir() -> Path:
    """~/.claude/projects/"""
    return Path.home() / ".claude" / "projects"


def dropbox_sync_dir() -> Path:
    """~/Dropbox/claude-sync/"""
    return Path.home() / "Dropbox" / "claude-sync"


def project_hash(project_path: str | Path) -> str:
    """절대 경로를 Claude Code의 해시 폴더명으로 변환.

    규칙: / \\ : 공백 → 모두 하이픈(-)으로 치환.
    예: /Users/plutonix/Code/proj → -Users-plutonix-Code-proj
        F:\\Code\\proj            → F--Code-proj
    """
    s = str(Path(project_path).resolve())
    return re.sub(r"[/\\: ]", "-", s)


def hash_to_jsonl_dir(project_path: str | Path) -> Path:
    """프로젝트 경로 → 해당 JSONL 디렉토리."""
    return claude_projects_dir() / project_hash(project_path)


def find_project_root(start: Path | None = None) -> Path:
    """cwd에서 위로 올라가며 프로젝트 루트(.git 기준)를 찾는다.

    못 찾으면 start 자체를 반환.
    """
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return p


# ── JSONL 파싱 ─────────────────────────────────────────────

@dataclass
class Message:
    role: str           # "user" | "assistant" | "tool_use" | "tool_result" | "system"
    text: str           # 표시할 텍스트
    timestamp: str = ""
    tool_name: str = ""


@dataclass
class SessionInfo:
    session_id: str
    jsonl_path: Path
    first_message: str = ""
    message_count: int = 0
    last_modified: datetime = field(default_factory=lambda: datetime.min)


def parse_jsonl(jsonl_path: Path) -> list[dict]:
    """JSONL 파일을 읽어 dict 리스트로 반환."""
    entries = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def extract_messages(entries: list[dict]) -> list[Message]:
    """JSONL 엔트리에서 user/assistant/tool_use 메시지를 추출."""
    messages: list[Message] = []
    for entry in entries:
        msg_type = entry.get("type", "")
        ts = entry.get("timestamp", "")

        if msg_type in ("human", "user"):
            # user turn: message.content
            content = entry.get("message", {}).get("content", "")
            if isinstance(content, list):
                text_parts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                text = "\n".join(text_parts)
            elif isinstance(content, str):
                text = content
            else:
                text = str(content)
            # "User: " 접두사 제거
            text = re.sub(r"^User:\s*", "", text.strip())
            if text.strip():
                messages.append(Message(role="user", text=text.strip(), timestamp=ts))

        elif msg_type == "assistant":
            content = entry.get("message", {}).get("content", "")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        t = block.get("text", "").strip()
                        if t:
                            messages.append(Message(role="assistant", text=t, timestamp=ts))
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        inp = block.get("input", {})
                        summary = _tool_summary(name, inp)
                        messages.append(Message(
                            role="tool_use", text=summary,
                            timestamp=ts, tool_name=name,
                        ))
            elif isinstance(content, str) and content.strip():
                messages.append(Message(role="assistant", text=content.strip(), timestamp=ts))

    return messages


def _tool_summary(name: str, inp: dict) -> str:
    """도구 호출을 1줄 요약."""
    if name in ("Read", "read"):
        return f"Read {inp.get('file_path', '?')}"
    if name in ("Edit", "edit"):
        return f"Edit {inp.get('file_path', '?')}"
    if name in ("Write", "write"):
        return f"Write {inp.get('file_path', '?')}"
    if name in ("Bash", "bash"):
        cmd = inp.get("command", "?")
        return f"Bash: {cmd[:80]}{'…' if len(cmd) > 80 else ''}"
    if name in ("Glob", "glob"):
        return f"Glob {inp.get('pattern', '?')}"
    if name in ("Grep", "grep"):
        return f"Grep {inp.get('pattern', '?')}"
    # 기타
    return f"{name}({json.dumps(inp, ensure_ascii=False)[:60]})"


def session_summary(jsonl_path: Path) -> SessionInfo:
    """세션 JSONL에서 요약 정보를 추출."""
    sid = jsonl_path.stem  # 파일명 = session ID
    mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime, tz=timezone.utc)
    entries = parse_jsonl(jsonl_path)
    msgs = extract_messages(entries)

    first_msg = ""
    for m in msgs:
        if m.role == "user":
            first_msg = m.text[:80]
            break

    return SessionInfo(
        session_id=sid,
        jsonl_path=jsonl_path,
        first_message=first_msg,
        message_count=len(msgs),
        last_modified=mtime,
    )


def list_sessions(project_path: str | Path | None = None) -> list[SessionInfo]:
    """프로젝트의 세션 목록을 최신순으로 반환."""
    if project_path:
        jsonl_dir = hash_to_jsonl_dir(project_path)
    else:
        jsonl_dir = hash_to_jsonl_dir(find_project_root())

    if not jsonl_dir.exists():
        return []

    sessions = []
    for f in jsonl_dir.glob("*.jsonl"):
        try:
            sessions.append(session_summary(f))
        except Exception:
            continue

    sessions.sort(key=lambda s: s.last_modified, reverse=True)
    return sessions


# ── 멀티플렉서 추상화 ─────────────────────────────────────

def mux_run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """cmux/wmux CLI를 실행."""
    cmd = [mux_command()] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def mux_new_workspace(name: str, cwd: str | None = None) -> str | None:
    """새 워크스페이스 생성, ID 반환."""
    args = ["new-workspace", "--name", name]
    if cwd:
        args.extend(["--cwd", cwd])
    result = mux_run(args, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def mux_send(text: str, surface_id: str | None = None,
             workspace_id: str | None = None) -> bool:
    """터미널에 텍스트 전송.

    workspace_id를 명시하면 cross-workspace send가 가능하다.
    Agent Teams 환경에서 caller와 다른 워크스페이스의 surface에
    보내려면 반드시 --workspace를 지정해야 한다.
    """
    args = ["send"]
    if workspace_id:
        args.extend(["--workspace", workspace_id])
    if surface_id:
        args.extend(["--surface", surface_id])
    args.append(text)
    result = mux_run(args, check=False)
    return result.returncode == 0


def mux_notify(title: str, body: str = "") -> None:
    """알림 표시."""
    args = ["notify", "--title", title]
    if body:
        args.extend(["--body", body])
    mux_run(args, check=False)


# ── 유틸 ───────────────────────────────────────────────────

def relative_time(dt: datetime) -> str:
    """datetime을 '2시간 전', '어제' 등으로 변환."""
    now = datetime.now(tz=timezone.utc)
    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 60:
        return "방금"
    if seconds < 3600:
        return f"{int(seconds // 60)}분 전"
    if seconds < 86400:
        return f"{int(seconds // 3600)}시간 전"
    if seconds < 172800:
        return "어제"
    if seconds < 604800:
        return f"{int(seconds // 86400)}일 전"
    return dt.strftime("%Y-%m-%d")


if __name__ == "__main__":
    # 간단 테스트
    print(f"OS: {current_os()}")
    print(f"Mux: {mux_command()}")
    print(f"Projects dir: {claude_projects_dir()}")
    print(f"Dropbox sync: {dropbox_sync_dir()}")
    print(f"Hash test: {project_hash('/Users/plutonix/Documents/Default')}")
    print(f"Project root: {find_project_root()}")
