---
created: 2026-04-23 14:04:36 +09:00
updated: 2026-04-23 14:04:37 +09:00
---
# Changelog

## [0.2.0] - 2026-04-23

### 새 기능
- SessionEnd Hook 추가: 세션 종료(`/exit`) 시 cmux 탭 제목을 경로로 자동 초기화
- cmux 탭 제목 양방향 동기화: cmux 탭 ↔ `claude --resume` 목록 ↔ 세션 데이터

### 변경
- cmux 내장 claude-hook과의 탭 제목 충돌 해결 (스피너 문자 "⠐ Claude Code" 패턴 감지)
- Stop Hook에서 탭 제목 설정 시 2초 지연 추가 (cmux 내장 hook 이후 실행 보장)
- cc-progress 관련 코드 전면 제거 (cc_progress.py, progress_updater.py, cc-statusline.sh)
- cc-restore에서 progress 탭 자동 첨부 로직 제거

### 수정
- `_is_system_title`이 cmux의 "⠐ Claude Code" 제목을 사용자 수정으로 오인하던 버그

## [0.1.0] - 2026-04-22

### 초기 릴리즈
- cc-restore: cmux 재시작 후 워크스페이스 전체 복원
- cc-resume: JSONL 히스토리 덤프 + claude --resume
- cc-link: Dropbox 기반 크로스 디바이스 JSONL 싱크
- workspace_tracker: SessionStart + Stop Hook (pane 순서·제목 기록)
