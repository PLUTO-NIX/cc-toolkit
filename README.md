---
created: 2026-04-23 13:16:17 +09:00
updated: 2026-04-23 13:21:03 +09:00
---
# cc-toolkit

cmux에서 여러 Claude Code 세션을 관리하는 도구 모음.
재부팅 후 세션 복원, 대화 히스토리 표시, Dropbox 기반 크로스 디바이스 싱크를 지원한다.

## 왜 필요한가

cmux는 재시작 후 워크스페이스·pane 레이아웃은 보존하지만, **셸 상태(cwd, 실행 중인 프로세스)는 복원하지 않는다.**
Claude Code 세션은 전부 죽어있고, 어떤 세션이 어디서 돌고 있었는지 기억이 안 남는다.

cc-toolkit은 이 문제를 해결한다:
- **세션 복원** — `cc-restore` 한 번으로 모든 pane에 cd + Claude resume
- **히스토리 표시** — 터미널 스크롤백에 이전 대화를 덤프한 후 resume
- **pane 순서 보존** — 어떤 pane에서 어떤 세션이 돌았는지 index로 기록
- **강제 종료 대비** — SessionStart Hook이 즉시 기록하여 Cmd+Q/셧다운에도 복원 가능
- **크로스 디바이스** — Dropbox로 JSONL 동기화 (블록 레벨 델타 싱크)
- **탭 제목 동기화** — cmux 탭 ↔ `claude --resume` 목록 ↔ 세션 데이터 양방향

## 설치

### 요구 사항
- macOS + [cmux](https://github.com/manaflow-ai/cmux) (소켓 접근 `allowAll` 설정)
- Python 3.11+
- (선택) [Dropbox](https://www.dropbox.com/) — 크로스 디바이스 싱크

### 설치 방법

```bash
git clone https://github.com/PLUTO-NIX/cc-toolkit.git
cd cc-toolkit
chmod +x install.sh
./install.sh
```

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

## 시나리오별 사용법

### 아침에 Mac 켜고 작업 시작

cmux를 열면 어제의 워크스페이스·pane 구조가 그대로 보인다. 셸만 빈 상태.

```bash
cc-restore
```

현재 워크스페이스의 모든 pane에 어제 작업하던 Claude 세션이 복원된다:
- 이전 대화 히스토리가 스크롤백에 출력됨
- Claude가 `--resume`으로 시작되어 컨텍스트 유지
- 탭 제목이 세션 내용으로 설정됨

워크스페이스가 여러 개면 안내가 나온다:

```
✅ Personal: 4개 pane 복원

📋 나머지 워크스페이스는 전환 후 실행:
   SAZO_Project (3 panes) → cc-restore
```

### 새 프로젝트에서 처음 Claude 사용

```bash
cd ~/Code/new-project
cc-link
```

이걸로 끝. 이후 이 폴더에서 `claude`를 실행하면 세션 데이터가 자동으로 Dropbox를 통해 동기화된다.
다른 기기에서도 같은 프로젝트 폴더에서 `cc-link`를 한 번 실행하면 양쪽에서 세션을 이어갈 수 있다.

### 실수로 탭을 닫았을 때

cmux에서 새 pane을 열고:

```bash
cc-resume --pick
```

```
📋 최근 세션 목록

  ── Personal ──
    1  API 리팩토링                    ~/Code/frontend
    2  버그 수정                       ~/Code/backend

  ── SAZO_Project ──
    3  CSS 수정                       ~/Code/sazo-client

복원할 세션 번호 (1-3, q=취소):
```

번호를 선택하면 해당 폴더로 이동 + 히스토리 출력 + Claude resume.

### 과거 세션 열람

```bash
cc-resume --list --project ~/Code/frontend
```

```
📂 frontend 세션 목록

    #  최종 수정       메시지  첫 메시지
  ───  ──────────  ─────  ────────────────────
    1  2시간 전       125  API 리팩토링해줘
    2  어제            31  버그 수정
    3  3일 전          82  테스트 커버리지 올리자

resume할 세션 번호 (1-3):
```

히스토리만 보고 resume은 하지 않으려면:

```bash
cc-resume --dump-only --tail 30 --project ~/Code/frontend
```

### Cmd+Q / 갑자기 전원이 꺼졌을 때

SessionStart Hook이 Claude 시작 시점에 레이아웃을 즉시 기록하므로, 정상 종료(`/exit`)를 하지 않아도 레이아웃 데이터는 보존되어 있다.

```bash
cc-restore
```

동일하게 동작한다. 세션 제목이 "(진행 중)"으로 표시될 수 있다 (Stop Hook 미실행).

### 세션 제목 관리

세 곳의 제목이 자동으로 양방향 동기화된다:
- **cmux 탭 제목** — cmux 앱에서 보이는 탭 이름
- **`claude --resume` 목록** — Claude Code 세션 선택 화면의 제목
- **cc-toolkit 데이터** — `cmux-layout.json`, `sessions.json`

제목 결정 우선순위:
1. cmux 탭 제목을 수동 변경 → 최우선
2. `claude /rename`으로 설정 → 해당 제목
3. 둘 다 없으면 → JSONL 첫 user 메시지에서 자동 추출

제목 바꾸는 방법:
- **cmux 탭**: 우클릭 또는 `Cmd+Shift+R`
- **Claude 안에서**: `/rename 새 제목`

어느 쪽에서 바꾸든 다음 Claude 응답 시 나머지에 자동 반영된다.

## 명령어 레퍼런스

### cc-restore

cmux 재시작 후 세션 복원.

```bash
cc-restore                    # 현재 워크스페이스 복원 + 나머지 안내
cc-restore "SAZO_Project"     # 특정 워크스페이스 지정
cc-restore --list             # 복원 가능한 워크스페이스 목록
cc-restore --dry-run          # 실제 복원 없이 계획만 출력
```

### cc-resume

Claude 세션 히스토리를 보고 이어가기.

```bash
cc-resume                       # 현재 프로젝트의 최신 세션
cc-resume <session-id>          # 특정 세션 ID
cc-resume --list                # 현재 프로젝트 세션 목록
cc-resume --pick                # 전체 워크스페이스에서 세션 선택
cc-resume --tail 30             # 마지막 30개 메시지만 표시
cc-resume --dump-only           # 히스토리만 출력 (Claude 시작 안 함)
cc-resume --project ~/Code/foo  # 다른 프로젝트 지정
```

### cc-link

프로젝트를 Dropbox 크로스 디바이스 싱크에 등록.

```bash
cc-link                       # cwd 기반 자동 등록
cc-link --slug my-proj        # slug 직접 지정
cc-link --list                # 등록된 프로젝트 목록
cc-link --unlink              # 싱크 해제
cc-link --dry-run             # 계획만 출력
```

## Hook 동작

백그라운드에서 자동 실행되는 것들.

### SessionStart Hook

Claude가 시작될 때 자동 실행:
- `cmux-layout.json`에 pane 위치(워크스페이스, index) 즉시 기록
- `sessions.json`에 세션 등록
- 강제 종료 대비: 이 시점에 레이아웃이 이미 저장됨

### Stop Hook

Claude가 응답을 완료할 때마다 자동 실행:
- 세션 제목 결정 (cmux 탭 수동 수정 > `claude /rename` > JSONL 첫 메시지)
- `claude --resume` 목록에 custom-title 동기화
- cmux 탭 제목 자동 설정
- `cmux-layout.json` + `sessions.json` 갱신

## 데이터 파일

| 파일 | 위치 | 역할 |
| --- | --- | --- |
| `cmux-layout.json` | `~/.cc-toolkit/cache/` | cmux 워크스페이스·pane 레이아웃 (cc-restore용) |
| `sessions.json` | `~/Dropbox/claude-sync/<slug>/` | 프로젝트별 세션 목록 (크로스 디바이스) |
| `meta.json` | `~/Dropbox/claude-sync/<slug>/` | OS별 프로젝트 경로 매핑 |

## 크로스 디바이스 싱크 구조

`cc-link`로 프로젝트를 등록하면 Claude Code의 JSONL 저장소를 Dropbox 슬롯에 심링크로 연결한다.

```
Dropbox/claude-sync/<slug>/
  ├── jsonl/*.jsonl    ← Dropbox가 양방향 동기화
  └── sessions.json    ← 세션 목록

~/.claude/projects/<hash>/  ──symlink──>  ~/Dropbox/claude-sync/<slug>/jsonl/
```

OS마다 경로 해시가 다르므로, 양쪽에서 `cc-link`를 한 번씩 실행하면 같은 Dropbox 슬롯을 가리키게 된다.

Dropbox를 선택한 이유: **블록 레벨 델타 싱크**를 지원하는 유일한 클라우드 서비스. JSONL은 append-only라서 추가된 부분만 업로드하면 되는데, Dropbox만 이를 지원한다 (나머지는 파일 전체 재업로드).

## 알려진 제한사항

| 제한 | 이유 | 우회 |
| --- | --- | --- |
| 비활성 워크스페이스 원격 복원 불가 | cmux가 비활성 워크스페이스의 terminal을 초기화하지 않음 | 워크스페이스 전환 후 `cc-restore` |
| pane 추가/삭제 후 복원 시 매칭 어긋남 | cmux 재시작 시 pane ID 리셋, 위치 기반 매칭 | 껐다 켜는 사이에 pane 구조 변경 안 하기 |
| cmux 밖에서 탭 제목·pane index 미기록 | cmux CLI 없으면 `cmux identify` 불가 | 세션 자체는 정상 기록, 레이아웃만 누락 |
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
│   ├── cc-resume          # shim
│   └── cc-restore         # shim
├── hooks/
│   └── workspace_tracker.py  # SessionStart + Stop Hook
├── install.sh
└── README.md
```

## 라이선스

MIT
