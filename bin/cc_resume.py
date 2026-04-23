#!/usr/bin/env python3
"""cc-resume: JSONL 히스토리를 터미널에 덤프한 후 claude --resume 실행.

사용법:
    cc-resume                      # 현재 프로젝트의 최신 세션
    cc-resume <session-id>         # 특정 세션
    cc-resume --list               # 세션 목록
    cc-resume --pick               # cmux-layout.json에서 세션 선택
    cc-resume --tail 30            # 마지막 30개 메시지만
    cc-resume --dump-only          # 히스토리만 출력 (resume 안 함)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    extract_messages,
    find_project_root,
    hash_to_jsonl_dir,
    list_sessions,
    mux_run,
    parse_jsonl,
    relative_time,
    Message,
)

LAYOUT_PATH = Path.home() / ".cc-toolkit" / "cache" / "cmux-layout.json"


# ── ANSI 색상 ──────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
GRAY = "\033[90m"


def render_messages(messages: list[Message], tail: int | None = None) -> None:
    """메시지를 컬러 포맷으로 출력."""
    if tail:
        messages = messages[-tail:]

    for msg in messages:
        if msg.role == "user":
            print(f"\n{CYAN}{BOLD}👤 User:{RESET}")
            for line in msg.text.split("\n"):
                print(f"{CYAN}  {line}{RESET}")

        elif msg.role == "assistant":
            print(f"\n{GREEN}{BOLD}🤖 Claude:{RESET}")
            for line in msg.text.split("\n"):
                print(f"{GREEN}  {line}{RESET}")

        elif msg.role == "tool_use":
            print(f"{GRAY}  🔧 {msg.text}{RESET}")

    print()


def show_list(project_path: Path) -> str | None:
    """세션 목록 표시 + 사용자 선택 → session_id 반환."""
    sessions = list_sessions(project_path)
    if not sessions:
        print("이 프로젝트에 세션이 없습니다.")
        return None

    print(f"\n{BOLD}📂 {project_path.name} 세션 목록{RESET}\n")
    print(f"  {'#':>3}  {'최종 수정':<10}  {'메시지':>5}  첫 메시지")
    print(f"  {'─'*3}  {'─'*10}  {'─'*5}  {'─'*40}")

    for i, s in enumerate(sessions, 1):
        first = s.first_message[:50] or "(빈 세션)"
        time_str = relative_time(s.last_modified)
        print(f"  {i:3d}  {time_str:<10}  {s.message_count:5d}  {first}")

    print()

    # 대화형 모드 (tty일 때만)
    if sys.stdin.isatty():
        try:
            choice = input(f"resume할 세션 번호 (1-{len(sessions)}, q=취소): ").strip()
            if choice.lower() in ("q", ""):
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx].session_id
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        return None
    else:
        return None


def show_pick() -> tuple[str, str] | None:
    """cmux-layout.json에서 모든 워크스페이스의 세션을 보여주고 선택 → (session_id, cwd) 반환."""
    if not LAYOUT_PATH.exists():
        print("cmux-layout.json이 없습니다. Claude 세션을 cmux에서 실행하면 자동 기록됩니다.")
        return None

    try:
        layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        print("cmux-layout.json을 읽을 수 없습니다.")
        return None

    workspaces = layout.get("workspaces", {})
    if not workspaces:
        print("저장된 세션이 없습니다.")
        return None

    all_panes: list[dict] = []
    print(f"\n{BOLD}📋 최근 세션 목록{RESET}\n")

    num = 1
    for ws_name, ws_data in workspaces.items():
        panes = ws_data.get("panes", [])
        if not panes:
            continue
        panes.sort(key=lambda p: (p.get("index") is None, p.get("index", 999)))
        print(f"  {DIM}── {ws_name} ──{RESET}")
        for p in panes:
            title = p.get("title", "(제목 없음)")[:40]
            cwd = p.get("cwd", "").replace(str(Path.home()), "~")
            print(f"  {num:3d}  {title:<40}  {GRAY}{cwd}{RESET}")
            all_panes.append(p)
            num += 1
        print()

    if not all_panes:
        print("세션이 없습니다.")
        return None

    if sys.stdin.isatty():
        try:
            choice = input(f"복원할 세션 번호 (1-{len(all_panes)}, q=취소): ").strip()
            if choice.lower() in ("q", ""):
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(all_panes):
                p = all_panes[idx]
                return (p.get("session_id", ""), p.get("cwd", ""))
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
    return None


def find_session_jsonl(project_path: Path, session_id: str) -> Path | None:
    """세션 ID로 JSONL 파일 찾기. cwd 해시 → 상위 폴더 해시 순으로 탐색."""
    # cwd 자체 + 상위 폴더들에서 탐색 (cc-link가 상위 폴더로 등록된 경우 대응)
    search_paths = [project_path]
    for parent in project_path.parents:
        search_paths.append(parent)
        if parent == Path.home() or len(search_paths) > 5:
            break

    for p in search_paths:
        jsonl_dir = hash_to_jsonl_dir(p)
        if not jsonl_dir.exists():
            continue

        exact = jsonl_dir / f"{session_id}.jsonl"
        if exact.exists():
            return exact

        for f in jsonl_dir.glob("*.jsonl"):
            if f.stem.startswith(session_id):
                return f

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="JSONL 히스토리 덤프 + claude --resume")
    parser.add_argument("session_id", nargs="?", help="세션 ID (생략 시 최신)")
    parser.add_argument("--list", action="store_true", help="세션 목록")
    parser.add_argument("--pick", action="store_true", help="cmux-layout에서 세션 선택 (닫힌 탭 복원)")
    parser.add_argument("--tail", type=int, help="마지막 N개 메시지만")
    parser.add_argument("--dump-only", action="store_true", help="히스토리만 출력")
    parser.add_argument("--project", help="프로젝트 경로 (기본: cwd)")
    args = parser.parse_args()

    project_path = Path(args.project).resolve() if args.project else find_project_root()

    # --list 모드
    if args.list:
        show_list(project_path)
        return

    # --pick 모드: cmux-layout.json에서 세션 선택
    if args.pick:
        result = show_pick()
        if not result:
            sys.exit(0)
        session_id, cwd = result
        if cwd:
            project_path = Path(cwd).resolve()
            # cwd로 이동 후 resume
            title = ""
            try:
                layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
                for ws_data in layout.get("workspaces", {}).values():
                    for p in ws_data.get("panes", []):
                        if p.get("session_id") == session_id:
                            title = p.get("title", "")
                            break
            except Exception:
                pass
            if title:
                mux_run(["rename-tab", title[:40]], check=False)
            os.chdir(cwd)

        # 아래에서 JSONL 찾기 + 히스토리 덤프 + resume 진행
        args.session_id = session_id

    # 세션 결정
    session_id = args.session_id
    if not session_id:
        sessions = list_sessions(project_path)
        if not sessions:
            print("이 프로젝트에 세션이 없습니다.")
            sys.exit(1)
        if len(sessions) == 1:
            session_id = sessions[0].session_id
        else:
            session_id = show_list(project_path)
            if not session_id:
                sys.exit(0)

    # JSONL 찾기
    jsonl_path = find_session_jsonl(project_path, session_id)
    if not jsonl_path:
        print(f"세션을 찾을 수 없습니다: {session_id}")
        sys.exit(1)

    # 히스토리 덤프
    entries = parse_jsonl(jsonl_path)
    messages = extract_messages(entries)

    if messages:
        print(f"\n{DIM}{'─' * 60}{RESET}")
        print(f"{DIM}  세션: {jsonl_path.stem[:20]}...  메시지: {len(messages)}개{RESET}")
        print(f"{DIM}{'─' * 60}{RESET}")
        render_messages(messages, tail=args.tail)
        print(f"{YELLOW}{'═' * 20} 이전 대화 ↑ {'═' * 7} 이어서 ↓ {'═' * 20}{RESET}")
        print()

    if args.dump_only:
        return

    # claude --resume 실행 (exec로 프로세스 교체)
    full_session_id = jsonl_path.stem
    os.execvp("claude", ["claude", "--resume", full_session_id])


if __name__ == "__main__":
    main()
