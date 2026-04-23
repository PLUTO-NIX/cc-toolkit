#!/usr/bin/env python3
"""cc-restore: cmux 재시작 후 세션 복원.

사용법:
    cc-restore                  # 현재 워크스페이스 복원 + 나머지 안내
    cc-restore "SAZO_Project"   # 특정 워크스페이스 복원
    cc-restore --list           # 복원 가능 목록
    cc-restore --dry-run        # 계획만 출력
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import mux_run, mux_notify

PROGRESS_DIR = Path("/tmp")
MAPPING_FILE = Path.home() / ".cc-toolkit" / "cache" / "progress-mapping.json"


def find_progress_file(session_id: str) -> Path | None:
    """세션의 progress 파일을 찾는다. 항상 ID 기반."""
    id_file = PROGRESS_DIR / f"cmux-progress-{session_id}.md"
    if id_file.exists():
        return id_file
    return None


def attach_progress_tab(target_pane: str, session_id: str) -> None:
    """progress 마크다운을 해당 pane에 탭으로 추가."""
    pf = find_progress_file(session_id)
    if not pf:
        return
    # 1. 마크다운 열기 (새 pane에 생성)
    result = mux_run(["markdown", "open", str(pf)], check=False)
    if result.returncode != 0:
        return
    # 2. surface ref 추출
    sf_match = re.search(r"surface=(\S+)", result.stdout)
    if not sf_match:
        return
    md_surface = sf_match.group(1)
    # 3. target pane으로 이동 (탭으로)
    mux_run(["move-surface", "--surface", md_surface, "--pane", target_pane], check=False)

LAYOUT_PATH = Path.home() / ".cc-toolkit" / "cache" / "cmux-layout.json"


# ── cmux 정보 ──────────────────────────────────────────────

def get_all_workspace_names() -> list[str]:
    result = mux_run(["tree", "--all"], check=False)
    if result.returncode != 0:
        return []
    names = []
    for line in result.stdout.splitlines():
        if re.search(r"workspace:\d+", line):
            m = re.search(r'"([^"]+)"', line)
            if m:
                names.append(m.group(1))
    return names


def get_pane_refs_for_workspace(workspace_ref: str) -> list[str]:
    """cmux tree에서 특정 워크스페이스의 pane ref 목록 (순서대로)."""
    result = mux_run(["tree", "--all"], check=False)
    if result.returncode != 0:
        return []
    panes, in_target = [], False
    for line in result.stdout.splitlines():
        ws_match = re.search(r"(workspace:\d+)", line)
        if ws_match:
            in_target = (ws_match.group(1) == workspace_ref)
            continue
        if not in_target:
            continue
        pane_match = re.search(r"(pane:\d+)", line)
        if pane_match and "surface" not in line:
            panes.append(pane_match.group(1))
    return panes


def get_surface_refs_for_workspace(workspace_ref: str) -> list[str]:
    result = mux_run(["tree", "--all"], check=False)
    if result.returncode != 0:
        return []
    surfaces, in_target = [], False
    for line in result.stdout.splitlines():
        ws_match = re.search(r"(workspace:\d+)", line)
        if ws_match:
            in_target = (ws_match.group(1) == workspace_ref)
            continue
        if not in_target:
            continue
        sf_match = re.search(r"(surface:\d+)", line)
        if sf_match:
            surfaces.append(sf_match.group(1))
    return surfaces


def get_workspace_ref_by_name(name: str) -> str:
    result = mux_run(["tree", "--all"], check=False)
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if f'"{name}"' in line:
            m = re.search(r"(workspace:\d+)", line)
            if m:
                return m.group(1)
    return ""


def get_my_surface() -> str:
    result = mux_run(["identify"], check=False)
    if result.returncode == 0:
        try:
            return json.loads(result.stdout).get("caller", {}).get("surface_ref", "")
        except Exception:
            pass
    return ""


def get_my_workspace_name() -> str:
    result = mux_run(["tree", "--all"], check=False)
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if "[selected]" in line and re.search(r"workspace:\d+", line):
            m = re.search(r'"([^"]+)"', line)
            if m:
                return m.group(1)
    return ""


# ── 데이터 ─────────────────────────────────────────────────

def load_layout() -> dict:
    if LAYOUT_PATH.exists():
        try:
            return json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _parse_ts(ts_str: str) -> float:
    try:
        return datetime.fromisoformat(ts_str).timestamp()
    except Exception:
        return 0


# ── 표시 ───────────────────────────────────────────────────

def list_restorable(layout: dict) -> None:
    workspaces = layout.get("workspaces", {})
    cmux_ws_names = get_all_workspace_names()
    if not workspaces:
        print("복원 가능한 워크스페이스 없음")
        return
    print("\n복원 가능한 워크스페이스:\n")
    for ws_name, ws_data in workspaces.items():
        panes = ws_data.get("panes", [])
        match = "✓" if ws_name in cmux_ws_names else "✗"
        print(f"  [{match}] {ws_name} ({len(panes)} panes)")
        for p in panes:
            idx = p.get("index", "?")
            title = p.get("title", "")[:35]
            short_cwd = p.get("cwd", "").replace(str(Path.home()), "~")
            print(f"       [{idx}] {title:<35}  {short_cwd}")
    print(f"\n  [✓] = cmux에 워크스페이스 존재, [✗] = 없음 (건너뜀)\n")


# ── 복원 ───────────────────────────────────────────────────

def restore(layout: dict, target_ws: str | None = None, dry_run: bool = False) -> None:
    workspaces = layout.get("workspaces", {})
    cmux_ws_names = get_all_workspace_names()
    my_surface = get_my_surface()
    my_ws = get_my_workspace_name()
    deferred_cmd: str | None = None
    deferred_title: str = ""
    total = 0

    # 복원 대상: 지정된 워크스페이스 또는 현재 워크스페이스
    ws_name = target_ws or my_ws

    if ws_name not in workspaces:
        print(f"'{ws_name}'의 레이아웃 데이터가 없습니다.")
        available = [n for n in workspaces if n in cmux_ws_names]
        if available:
            print(f"  사용 가능: {', '.join(available)}")
        return

    if ws_name not in cmux_ws_names:
        print(f"'{ws_name}'이 cmux에 없습니다. 해당 워크스페이스로 전환 후 다시 실행하세요.")
        return

    # pane 데이터
    panes = workspaces[ws_name].get("panes", [])
    cutoff = datetime.now(tz=timezone.utc).timestamp() - (7 * 86400)
    panes = [p for p in panes if _parse_ts(p.get("last_active", "")) > cutoff]
    panes.sort(key=lambda p: (p.get("index") is None, p.get("index", 999)))

    if not panes:
        print(f"'{ws_name}'에 최근 7일 내 세션이 없습니다.")
        return

    if dry_run:
        print(f"\n  📂 {ws_name} ({len(panes)} panes)")
        for p in panes:
            idx = p.get("index", "?")
            title = p.get("title", "")[:35]
            cwd_short = p.get("cwd", "").replace(str(Path.home()), "~")
            print(f"     [{idx}] {title:<35}  {cwd_short}")
        # 나머지 안내
        others = [n for n in workspaces if n in cmux_ws_names and n != ws_name]
        if others:
            print(f"\n📋 나머지 워크스페이스:")
            for name in others:
                count = len(workspaces[name].get("panes", []))
                print(f"   {name} ({count} panes) → cc-restore \"{name}\"")
        return

    # surface ref + pane ref 매핑
    ws_ref = get_workspace_ref_by_name(ws_name)
    surface_refs = get_surface_refs_for_workspace(ws_ref)
    pane_refs = get_pane_refs_for_workspace(ws_ref)

    if not surface_refs:
        print(f"'{ws_name}'의 surface를 가져올 수 없습니다.")
        return

    restore_panes = panes[:len(surface_refs)]
    deferred_session_id = ""

    for i, pane in enumerate(restore_panes):
        session_id = pane.get("session_id", "")
        cwd = pane.get("cwd", "")
        title = pane.get("title", "")
        if not session_id:
            continue

        cmd = f"cd {cwd} && cc-resume {session_id}" if cwd else f"cc-resume {session_id}"
        target = surface_refs[i]
        target_pane = pane_refs[i] if i < len(pane_refs) else ""

        if target == my_surface:
            deferred_cmd = cmd
            deferred_title = title
            deferred_session_id = session_id
            total += 1
            continue

        mux_run(["send", "--surface", target, cmd + "\n"], check=False)
        if title:
            mux_run(["rename-tab", "--surface", target, title[:40]], check=False)
        # progress 탭 추가
        if target_pane:
            attach_progress_tab(target_pane, session_id)
        total += 1

    # 나머지 워크스페이스 안내
    others = [n for n in workspaces if n in cmux_ws_names and n != ws_name]
    if others:
        print(f"\n📋 나머지 워크스페이스는 전환 후 실행:")
        for name in others:
            count = len(workspaces[name].get("panes", []))
            print(f"   {name} ({count} panes) → cc-restore")

    # 자기 pane은 마지막에 exec
    if deferred_cmd:
        if deferred_title:
            mux_run(["rename-tab", deferred_title[:40]], check=False)
        # 자기 pane에도 progress 탭 추가
        my_pane = ""
        try:
            ident = json.loads(mux_run(["identify"], check=False).stdout)
            my_pane = ident.get("caller", {}).get("pane_ref", "")
        except Exception:
            pass
        if my_pane and deferred_session_id:
            attach_progress_tab(my_pane, deferred_session_id)
        print(f"\n✅ {ws_name}: {total}개 pane 복원")
        os.execvp("bash", ["bash", "-c", deferred_cmd])
    else:
        print(f"\n✅ {ws_name}: {total}개 pane 복원 완료")
        if total:
            mux_notify("cc-restore", f"{ws_name}: {total}개 pane 복원")


# ── main ───────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="cmux 세션 복원")
    parser.add_argument("workspace", nargs="?", help="워크스페이스 이름 (기본: 현재)")
    parser.add_argument("--list", action="store_true", help="복원 가능 목록")
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력")
    args = parser.parse_args()

    layout = load_layout()

    if args.list:
        list_restorable(layout)
        return

    if not layout.get("workspaces"):
        print("cmux-layout.json에 저장된 레이아웃 없음")
        print("  Claude 세션을 cmux에서 실행하면 자동 기록됩니다.")
        sys.exit(1)

    restore(layout, target_ws=args.workspace, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
