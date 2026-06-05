# 🌱 Claude Token Diary

> Visualize your Claude Code & Codex usage as GitHub contributions

Claude Code와 OpenAI Codex 사용량을 GitHub 잔디로 자동 시각화합니다

![badge](https://img.shields.io/badge/Claude_Code-Token_Diary-green)
![badge](https://img.shields.io/badge/Codex-supported-blue)

---

## 어떻게 동작하나요

```
매일 밤 11:50 자동 실행
→ ~/.claude/stats-cache.json 에서 Claude 토큰 사용량 읽기
→ ~/.codex/state_5.sqlite 에서 Codex 토큰 사용량 읽기
→ 지정한 폴더의 Claude 세션 내역 추출
→ Gemini API로 작업 내용 자동 요약
→ 합산 토큰 사용량에 비례해 N번 커밋 → GitHub 잔디 반영
```

### 잔디 기준 (Claude + Codex 합산)

| 하루 토큰 사용량 | 커밋 수 |
|---|---|
| ~ 50K | 1 |
| 50K ~ 200K | 3 |
| 200K ~ 500K | 5 |
| 500K ~ 800K | 7 |
| 800K+ | 10 |

### 커밋 예시

```
📅 2026-06-05 | Claude 492K + Codex 10.6M

## 작업 내역
- Next.js 프로젝트 API 라우트 구현
- PostgreSQL 스키마 설계 및 마이그레이션
- 인증 미들웨어 디버깅
```

---

## 사전 준비

- [Claude Code](https://claude.ai/code) 또는 [OpenAI Codex](https://openai.com/codex) 사용 중
- Python 3.8+
- GitHub 계정
- Gemini API 키 (무료) — [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

---

## 설치

### 1. 잔디 심을 GitHub 레포 생성

GitHub에서 `token-diary` 이름으로 빈 레포 생성 (공개/비공개 무관)

### 2. GitHub Personal Access Token 발급

[github.com/settings/tokens](https://github.com/settings/tokens) → `New token` → `repo` 권한 체크

### 3. 이 레포 클론

```bash
git clone https://github.com/std-yong/claude-token-diary
cd claude-token-diary
```

### 4. 의존성 설치

```bash
pip3 install -r requirements.txt
```

### 5. .env 파일 작성

```bash
cp .env.example .env
```

```env
GITHUB_TOKEN=ghp_xxxxxxxxxxxx          # GitHub PAT
GITHUB_REPO=your-username/token-diary  # 잔디 심을 레포
WATCH_DIRS=~/Desktop/my-project,~/study  # Claude로 작업하는 폴더
GEMINI_API_KEY=AIzaSy...               # Gemini API 키
```

> `WATCH_DIRS`에 지정한 폴더에서 Claude를 사용한 날만 작업 내역이 기록됩니다

### 6. 실행

```bash
python3 diary.py
```

### 7. 자동 실행 등록 (Mac)

```bash
python3 setup.py
```

매일 밤 11:50에 자동으로 실행됩니다. 최초 1회만 실행하면 됩니다

---

## 과거 데이터 소급 적용 (선택)

> ⚠️ 기존 GitHub 잔디와 겹칠 수 있으므로 신중하게 사용하세요

```bash
python3 diary.py --backfill
```

`~/.claude/stats-cache.json` 및 `~/.codex/state_5.sqlite`의 과거 데이터를 소급 적용합니다

---

## 파일 구조

```
claude-token-diary/      ← 이 레포 (코드)
├── diary.py             # 메인 스크립트
├── setup.py             # 자동 실행 등록 (Mac launchd)
├── requirements.txt
├── .env.example
├── .gitignore
└── logs/                # 날짜별 작업 일기 (자동 생성)

token-diary/             ← 잔디 레포 (별도 생성, 비공개 가능)
```

> `claude-token-diary`(코드)와 `token-diary`(잔디 데이터)를 분리해서 개인 작업 내역을 공개하지 않아도 됩니다

---

## 주의사항

- `~/.claude/stats-cache.json`은 Claude Code 세션 종료 후 업데이트됩니다
- `~/.codex/state_5.sqlite`는 Codex CLI 사용 시 자동 생성됩니다
- Mac 전용입니다 (Windows는 추후 지원 예정)
- 맥북이 꺼져 있는 날은 다음 실행 시 당일 데이터만 반영됩니다
