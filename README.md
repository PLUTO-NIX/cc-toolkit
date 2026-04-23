---
created: 2026-04-23 13:16:17 +09:00
updated: 2026-04-23 13:16:17 +09:00
---
# cc-toolkit

cmux에서 여러 Claude Code 세션을 관리하는 도구 모음.
재부팅 후 세션 복원, 대화 히스토리 표시, Dropbox 기반 크로스 디바이스 싱크를 지원한다.

## 왜 필요한가

cmux는 재시작 후 워크스페이스/pane 레이아웃은 보존하지만, **셸 상태(cwd, 실행 중인 프로세스)는 복원하지 않는다.** Claude Code 세션은 전부 죽어있고, 어떤 세션이 어디서 돌고 있었는지 기억이 안 남는다.

cc-toolkit은 이 문제를 해결한다:
- **세션 복원** — `cc-restore` 한 번으로 모든 pane에 cd + Claude resume
- **히스토리 표시** — 터미널 스크롤백에 이전 대화를 덤프한 후 resume
- **pane 순서 보존** — 어떤 pane에서 어떤 세션이 돌았는지 index로 기록
- **크로스 디바이스** — Dropbox로 JSONL 동기화 (블록 레벨 델타 싱크)
- **탭 제목 동기화** — cmux 탭 ↔ `claude --resume` 목록 ↔ 세션 데이터 양방향

## 설치

```bash
git clone https://github.com/PLUTO-NIX/cc-toolkit.git
cd cc-toolkit
chmod +x install.sh
./install.sh
```

### 요구 사항
- macOS (cmux) 또는 Windows (wmux)
- Python 3.11+
- [cmux](https://github.com/manaflow-ai/cmux) 설치 + 소켓 접근 `allowAll`
- (선택) [Dropbox](https://www.dropbox.com/) — 크로스 디바이스 싱크

### Hook 등록

Claude Code 전역 `~/.claude/settings.json`에 추가:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command",
      "command": "CC_HOOK_EVENT=start python3 $HOME/.cc-toolkit/hooks/workspace_tracker.py",
      "async": true}]}],
    "Stop": [{"hooks": [{"type": "command",
      "command": "CC_HOOK_EVENT=stop python3 $HOME/.cc-toolkit/hooks/workspace_tracker.py",
      "async": true}]}]
  }
}
```

## 사용법

### 재부팅 후 세션 복원

```bash
cc-restore              # 현재 워크스페이스의 모든 pane 복원
cc-restore "SAZO"       # 특정 워크스페이스 지정
cc-restore --list       # 복원 가능한 워크스페이스 목록
```

cmux 재시작 후 아무 pane에서 실행하면, 각 pane에:
- 해당 폴더로 `cd`
- 이전 대화 히스토리를 터미널에 출력
- `claude --resume`으로 세션 이어가기
- 탭 제목을 세션 이름으로 설정

워크스페이스가 여러 개면 전환 후 다시 `cc-restore`.

### 세션 히스토리 + resume

```bash
cc-resume                      # 현재 프로젝트의 최신 세션
cc-resume <session-id>         # 특정 세션
cc-resume --list               # 세션 목록
cc-resume --pick               # 전체 워크스페이스에서 선택 (닫힌 탭 복원)
cc-resume --dump-only          # 히스토리만 출력 (resume 안 함)
cc-resume --tail 30            # 마지막 30개 메시지만
```

### Dropbox 크로스 디바이스 싱크

```bash
cd ~/Code/myproject
cc-link                        # 프로젝트를 Dropbox 싱크에 등록
cc-link --list                 # 등록된 프로젝트 목록
cc-link --unlink               # 싱크 해제
```

한 번 등록하면 해당 폴더의 모든 Claude 세션이 Dropbox를 통해 자동 동기화된다.
다른 기기에서 같은 프로젝트 폴더에서 `cc-link`를 실행하면 양쪽이 연결된다.

## 데이터 파일

| 파일 | 위치 | 역할 |
| --- | --- | --- |
| `cmux-layout.json` | `~/.cc-toolkit/cache/` | cmux 워크스페이스·pane 레이아웃 (cc-restore용) |
| `sessions.json` | `~/Dropbox/claude-sync/<slug>/` | 프로젝트별 세션 목록 (크로스 디바이스) |
| `meta.json` | `~/Dropbox/claude-sync/<slug>/` | OS별 프로젝트 경로 매핑 |

## Hook 동작

### SessionStart
- `cmux-layout.json`에 pane 위치(워크스페이스, index) 즉시 기록
- `sessions.json`에 세션 등록
- 강제 종료(Cmd+Q, 셧다운)에도 레이아웃 보존

### Stop
- 세션 제목 결정: cmux 탭 수동 수정 > `claude /rename` > JSONL 첫 메시지
- `claude --resume` 목록에 custom-title 동기화
- cmux 탭 제목 자동 설정
- `cmux-layout.json` + `sessions.json` 갱신

## 알려진 제한사항

| 제한 | 이유 | 우회 |
| --- | --- | --- |
| 비활성 워크스페이스 원격 복원 불가 | cmux가 비활성 워크스페이스의 terminal을 초기화하지 않음 | 워크스페이스 전환 후 `cc-restore` |
| pane 추가/삭제 후 복원 시 매칭 어긋남 | cmux 재시작 시 pane ID 리셋, 위치 기반 매칭 | 껐다 켜는 사이에 pane 구조 변경 안 하기 |
| 탭 이름 변경 후 `/exit` 시 미반영 | Stop Hook이 Claude 응답 후에만 실행 | 한 턴 주고받으면 즉시 반영 |

## 구조

```
cc-toolkit/
├── bin/
│   ├── common.py          # 공통 유틸 (경로, JSONL 파싱, cmux 추상화)
│   ├── cc_link.py         # Dropbox 싱크 슬롯 등록
│   ├── cc_resume.py       # 히스토리 덤프 + claude --resume
│   ├── cc_restore.py      # cmux 레이아웃 복원
│   ├── cc-link            # shim (bash → python)
│   ├── cc-resume           # shim
│   └── cc-restore          # shim
├── hooks/
│   └── workspace_tracker.py  # SessionStart + Stop Hook
├── install.sh
└── README.md
```

## 라이선스

MIT
