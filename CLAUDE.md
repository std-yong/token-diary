# Claude Token Diary — 개발 컨텍스트

## 레포 구조

| 레포 | 로컬 경로 | GitHub | 용도 |
|---|---|---|---|
| 코드 | `~/token-diary` | `std-yong/token-diary` (공개) | diary.py 소스 |
| 데이터 | `~/my-token-diary` | `std-yong/my-token-diary` (프라이빗) | 잔디 커밋 + 일기 로그 |

## 동작 방식

```
매일 밤 11:50 launchd 자동 실행 (com.claude-token-diary)
→ ~/.claude/projects/**/*.jsonl 에서 날짜별 input+output 토큰 직접 읽기
→ ~/.codex/state_5.sqlite 에서 Codex 토큰 읽기
→ WATCH_DIRS 폴더의 JSONL에서 오늘 대화 내역 추출
→ Gemini API(google-genai)로 작업 요약
→ 토큰량 기준 N커밋 → my-token-diary에 push
```

## 잔디 기준

| 토큰 | 커밋 수 |
|---|---|
| ~50K | 1 |
| 50K~200K | 3 |
| 200K~500K | 5 |
| 500K~800K | 7 |
| 800K+ | 10 |

## 주요 파일

- `diary.py` — 메인 스크립트
- `setup.py` — launchd 등록 (Mac)
- `.env` — GITHUB_TOKEN, GITHUB_REPO(=std-yong/my-token-diary), WATCH_DIRS, GEMINI_API_KEY
- `~/Library/LaunchAgents/com.claude-token-diary.plist` — 자동 실행 설정

## 2026-06-07~08 작업 이력

### 버그 수정
- **stats-cache.json 미갱신 문제**: Claude Code 세션이 열려있으면 stats-cache.json이 업데이트 안 돼서 잔디가 안 심어지던 문제
  - 해결: `load_claude_stats()`를 stats-cache.json 대신 JSONL 파일에서 직접 읽도록 변경
  - `backfill()`도 JSONL 기반 날짜 수집으로 전환
  - 누락된 06-02~06-06 데이터 수동 소급 적용 완료

### 패키지 교체
- `google-generativeai` (deprecated) → `google-genai` 교체
- `summarize()` 함수: `genai.configure` + `GenerativeModel` → `genai.Client` + `client.models.generate_content`

### 레포/디렉토리 이름 변경
- 코드 레포: `claude-token-diary` → `token-diary`
- 데이터 레포: `token-diary` → `my-token-diary`
- 로컬 디렉토리, git remote, .env, launchd plist 모두 일괄 업데이트

## 남은 과제

- Python 3.9 → 3.10+ 업그레이드 (현재 경고만 뜨고 동작은 함)
- Windows 지원 (launchd 대신 Task Scheduler)
