#!/usr/bin/env python3
"""cc-link: 프로젝트를 Dropbox 싱크 슬롯에 등록한다.

사용법:
    cc-link                    # cwd 기반 slug 자동 추론
    cc-link --slug my-proj     # slug 직접 지정
    cc-link --list             # 등록된 프로젝트 목록
    cc-link --unlink           # 현재 프로젝트 싱크 해제
    cc-link --dry-run          # 실제 변경 없이 계획만 출력
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    claude_projects_dir,
    current_os,
    dropbox_sync_dir,
    find_project_root,
    project_hash,
)


def slug_from_path(project_path: Path) -> str:
    """프로젝트 경로에서 slug 생성."""
    return project_path.name.lower().replace(" ", "-")


def list_linked() -> None:
    """등록된 프로젝트 목록 출력."""
    sync_root = dropbox_sync_dir()
    if not sync_root.exists():
        print("등록된 프로젝트 없음 (claude-sync 폴더 없음)")
        return

    found = False
    for slot in sorted(sync_root.iterdir()):
        meta_path = slot / "meta.json"
        if not meta_path.exists():
            continue
        found = True
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        slug = meta.get("slug", slot.name)
        devices = meta.get("devices", {})
        jsonl_count = len(list((slot / "jsonl").glob("*.jsonl"))) if (slot / "jsonl").exists() else 0
        print(f"  {slug:<24} sessions={jsonl_count:3d}  devices={', '.join(devices.keys())}")

    if not found:
        print("등록된 프로젝트 없음")


def link(project_path: Path, slug: str, dry_run: bool = False) -> None:
    """프로젝트를 Dropbox 싱크 슬롯에 등록."""
    sync_root = dropbox_sync_dir()
    slot = sync_root / slug
    jsonl_slot = slot / "jsonl"
    meta_path = slot / "meta.json"

    hash_name = project_hash(project_path)
    hash_dir = claude_projects_dir() / hash_name

    print(f"프로젝트:    {project_path}")
    print(f"slug:        {slug}")
    print(f"해시 폴더:   {hash_name}")
    print(f"싱크 슬롯:   {jsonl_slot}")
    print(f"심링크:      {hash_dir} → {jsonl_slot}")
    print()

    if dry_run:
        print("[dry-run] 실제 변경 없음")
        return

    # 1. 싱크 슬롯 생성
    jsonl_slot.mkdir(parents=True, exist_ok=True)
    print(f"✅ 싱크 슬롯 생성: {jsonl_slot}")

    # 2. 기존 JSONL 마이그레이션
    if hash_dir.exists() and not hash_dir.is_symlink():
        jsonl_files = list(hash_dir.glob("*.jsonl"))
        if jsonl_files:
            print(f"📦 기존 JSONL {len(jsonl_files)}개 마이그레이션...")
            for f in jsonl_files:
                dest = jsonl_slot / f.name
                if not dest.exists():
                    shutil.move(str(f), str(dest))
                    print(f"   {f.name} → 싱크 슬롯")
                else:
                    print(f"   {f.name} (이미 존재, 건너뜀)")
        # 나머지 비-JSONL 파일도 이동
        remaining = [f for f in hash_dir.iterdir() if f.is_file()]
        for f in remaining:
            dest = jsonl_slot / f.name
            if not dest.exists():
                shutil.move(str(f), str(dest))
        # 빈 디렉토리 삭제
        try:
            hash_dir.rmdir()
        except OSError:
            # 하위 디렉토리가 남아있으면 전체 삭제
            shutil.rmtree(hash_dir)
        print(f"✅ 기존 해시 폴더 정리 완료")
    elif hash_dir.is_symlink():
        current_target = hash_dir.resolve()
        if current_target == jsonl_slot.resolve():
            print(f"ℹ️  이미 올바른 심링크 (변경 없음)")
        else:
            hash_dir.unlink()
            print(f"🔄 기존 심링크 제거 (대상: {current_target})")

    # 3. 심링크 생성
    if not hash_dir.exists():
        hash_dir.symlink_to(jsonl_slot)
        print(f"✅ 심링크 생성: {hash_dir.name} → {jsonl_slot}")

    # 4. meta.json 업데이트
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        meta = {"slug": slug, "devices": {}}

    os_key = current_os()
    meta["devices"][os_key] = {
        "project_path": str(project_path),
        "hash_folder": hash_name,
        "linked_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ meta.json 업데이트")

    print(f"\n🎉 완료! 이제 이 프로젝트의 Claude Code 세션은 Dropbox를 통해 동기화됩니다.")


def unlink(project_path: Path) -> None:
    """현재 프로젝트의 싱크 해제 (심링크 → 일반 폴더로 복원)."""
    hash_name = project_hash(project_path)
    hash_dir = claude_projects_dir() / hash_name

    if not hash_dir.is_symlink():
        print(f"ℹ️  {hash_name}은 심링크가 아닙니다 (이미 해제됨)")
        return

    target = hash_dir.resolve()
    hash_dir.unlink()
    hash_dir.mkdir(parents=True, exist_ok=True)

    # 심링크 대상에서 파일 복사
    for f in target.glob("*"):
        shutil.copy2(str(f), str(hash_dir / f.name))

    print(f"✅ 싱크 해제: {hash_name} (파일을 로컬로 복사)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dropbox 싱크 슬롯 등록")
    parser.add_argument("--slug", help="프로젝트 slug (기본: 디렉토리명)")
    parser.add_argument("--list", action="store_true", help="등록 목록 출력")
    parser.add_argument("--unlink", action="store_true", help="싱크 해제")
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력")
    parser.add_argument("path", nargs="?", help="프로젝트 경로 (기본: cwd)")
    args = parser.parse_args()

    if args.list:
        list_linked()
        return

    project_path = Path(args.path).resolve() if args.path else find_project_root()

    if args.unlink:
        unlink(project_path)
        return

    slug = args.slug or slug_from_path(project_path)

    # slug 충돌 확인
    slot = dropbox_sync_dir() / slug
    if slot.exists() and (slot / "meta.json").exists():
        meta = json.loads((slot / "meta.json").read_text(encoding="utf-8"))
        existing_devices = meta.get("devices", {})
        os_key = current_os()
        if os_key in existing_devices:
            existing_path = existing_devices[os_key].get("project_path", "")
            if existing_path != str(project_path):
                print(f"⚠️  slug '{slug}'는 이미 다른 프로젝트에 사용 중: {existing_path}")
                print(f"   --slug 옵션으로 다른 이름을 지정하세요.")
                sys.exit(1)

    link(project_path, slug, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
