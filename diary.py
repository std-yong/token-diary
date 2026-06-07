#!/usr/bin/env python3
import json
import os
import sqlite3
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from google import genai
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WATCH_DIRS = [
    Path(d.strip().replace("~", str(Path.home())))
    for d in os.getenv("WATCH_DIRS", "").split(",")
    if d.strip()
]

TODAY = datetime.now().strftime("%Y-%m-%d")
CLAUDE_DIR = Path.home() / ".claude"
REPO_DIR = Path(__file__).parent
DIARY_REPO_DIR = Path.home() / "my-token-diary"
LOGS_DIR = DIARY_REPO_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

PROMPT_TEMPLATE = """오늘 Claude Code와 함께 진행한 작업 내역이야.
아래 대화 내용을 보고 핵심 작업을 3~5줄로 간결하게 요약해줘.

[규칙]
- 한국어로 작성
- 각 항목은 "- " 로 시작
- 기술적인 내용 위주로, 잡담은 제외
- 완료/진행 중 구분 불필요

[오늘의 대화 내역]
{session_content}"""


def tokens_to_commits(tokens: int) -> int:
    if tokens < 50_000:
        return 1
    elif tokens < 200_000:
        return 3
    elif tokens < 500_000:
        return 5
    elif tokens < 800_000:
        return 7
    else:
        return 10


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


def load_claude_stats(date: str):
    """JSONL 세션 파일에서 날짜별 토큰을 직접 읽는다. stats-cache.json 갱신 여부와 무관하게 동작."""
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return 0, {}

    model_tokens: dict[str, int] = defaultdict(int)

    for proj in projects_dir.iterdir():
        for jsonl_file in proj.glob("*.jsonl"):
            try:
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    if not line:
                        continue
                    obj = json.loads(line)
                    ts = obj.get("timestamp", "")
                    if not ts or ts[:10] != date:
                        continue
                    model = obj.get("message", {}).get("model", "")
                    usage = obj.get("message", {}).get("usage", {})
                    tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    if tokens > 0 and model:
                        model_tokens[model] += tokens
            except Exception:
                continue

    total = sum(model_tokens.values())
    return total, dict(model_tokens)


def collect_all_jsonl_dates() -> set[str]:
    """JSONL 파일에 기록된 모든 날짜를 수집한다."""
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return set()

    dates: set[str] = set()
    for proj in projects_dir.iterdir():
        for jsonl_file in proj.glob("*.jsonl"):
            try:
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    if not line:
                        continue
                    obj = json.loads(line)
                    ts = obj.get("timestamp", "")
                    if ts and len(ts) >= 10:
                        dates.add(ts[:10])
            except Exception:
                continue
    return dates


def load_codex_stats(date: str) -> int:
    db_path = Path.home() / ".codex" / "state_5.sqlite"
    if not db_path.exists():
        return 0
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(tokens_used), 0) FROM threads "
            "WHERE date(created_at, 'unixepoch', 'localtime') = ?",
            (date,),
        )
        result = cur.fetchone()
        con.close()
        return result[0] if result else 0
    except Exception:
        return 0


def find_today_projects() -> list[Path]:
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return []
    matched = []
    for watch_dir in WATCH_DIRS:
        prefix = str(watch_dir).replace("/", "-")
        for project_dir in projects_dir.iterdir():
            if project_dir.name == prefix or project_dir.name.startswith(prefix + "-"):
                matched.append(project_dir)
    return matched


def extract_session_messages(project_dir: Path, date: str) -> list[str]:
    messages = []
    skip_prefixes = ("<", "[", "Note:", "IMPORTANT", "Caveat:")

    for jsonl_file in project_dir.glob("*.jsonl"):
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
            is_today = False
            for line in lines:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = obj.get("snapshot", {}).get("timestamp", "")
                if ts and ts[:10] == date:
                    is_today = True
                if not is_today:
                    continue
                if obj.get("type") != "user":
                    continue
                content = obj.get("message", {}).get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text and len(text) > 10 and not any(text.startswith(p) for p in skip_prefixes):
                                messages.append(text[:300])
                                break
                elif isinstance(content, str):
                    text = content.strip()
                    if text and len(text) > 10 and not any(text.startswith(p) for p in skip_prefixes):
                        messages.append(text[:300])
        except Exception:
            continue
    return messages[:30]


def summarize(messages: list[str]) -> str:
    if not messages or not GEMINI_API_KEY:
        return "- (작업 내역 없음)"
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        content = "\n\n".join(messages)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=PROMPT_TEMPLATE.format(session_content=content[:10000]),
        )
        return response.text.strip()
    except Exception as e:
        return f"- (요약 실패: {e})"


def write_diary(date: str, claude_tokens: int, model_breakdown: dict, codex_tokens: int, summary: str) -> Path:
    total_tokens = claude_tokens + codex_tokens
    n_commits = tokens_to_commits(total_tokens)

    claude_breakdown = "\n".join(
        f"  - {m.replace('claude-','').replace('-20251001','').replace('-20250929','')}: {fmt_tokens(v)}"
        for m, v in model_breakdown.items()
    ) or "  - (없음)"

    codex_line = f"  - Codex: {fmt_tokens(codex_tokens)}" if codex_tokens > 0 else "  - (없음)"

    content = f"""# {date}

| | |
|---|---|
| 총 토큰 | {fmt_tokens(total_tokens)} |
| 커밋 수 | {n_commits} |

**Claude Code**
{claude_breakdown}

**Codex**
{codex_line}

## 작업 내역

{summary}
"""
    log_file = LOGS_DIR / f"{date}.md"
    log_file.write_text(content, encoding="utf-8")
    return log_file


def git_commit(message: str, date: str):
    date_str = f"{date}T23:50:00"
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date_str
    env["GIT_COMMITTER_DATE"] = date_str
    subprocess.run(["git", "add", "-A"], cwd=DIARY_REPO_DIR, check=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=DIARY_REPO_DIR)
    if result.returncode != 0:
        subprocess.run(["git", "commit", "-m", message], cwd=DIARY_REPO_DIR, env=env, check=True)


def push():
    remote_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
    subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=DIARY_REPO_DIR)
    subprocess.run(["git", "push", "origin", "main"], cwd=DIARY_REPO_DIR, check=True)


def process_date(date: str, backfill: bool = False):
    claude_tokens, model_breakdown = load_claude_stats(date)
    codex_tokens = load_codex_stats(date)
    total_tokens = claude_tokens + codex_tokens

    if total_tokens == 0 and not backfill:
        print(f"[{date}] 토큰 데이터 없음, 스킵")
        return

    if backfill:
        summary = f"- Claude {fmt_tokens(claude_tokens)} / Codex {fmt_tokens(codex_tokens)} 토큰 사용 (상세 내역 없음)"
    else:
        projects = find_today_projects()
        all_messages = []
        for p in projects:
            all_messages.extend(extract_session_messages(p, date))
        summary = summarize(all_messages)

    write_diary(date, claude_tokens, model_breakdown, codex_tokens, summary)
    n_commits = tokens_to_commits(total_tokens)

    commit_label = f"Claude {fmt_tokens(claude_tokens)}"
    if codex_tokens > 0:
        commit_label += f" + Codex {fmt_tokens(codex_tokens)}"

    git_commit(f"📅 {date} | {commit_label}", date)

    activity_file = DIARY_REPO_DIR / "activity.log"
    for i in range(2, n_commits + 1):
        with open(activity_file, "a") as f:
            f.write(f"{date} [{i}/{n_commits}]\n")
        git_commit(f"📅 {date} [{i}/{n_commits}]", date)

    print(f"[{date}] Claude {fmt_tokens(claude_tokens)} + Codex {fmt_tokens(codex_tokens)} → {n_commits}커밋 ✅")


def backfill():
    past_dates = set()

    # JSONL에서 직접 날짜 수집 (stats-cache.json 불필요)
    for date in collect_all_jsonl_dates():
        if date < TODAY:
            past_dates.add(date)

    # Codex 데이터에서도 날짜 수집
    db_path = Path.home() / ".codex" / "state_5.sqlite"
    if db_path.exists():
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            cur.execute(
                "SELECT DISTINCT date(created_at, 'unixepoch', 'localtime') FROM threads "
                "WHERE tokens_used > 0 AND date(created_at, 'unixepoch', 'localtime') < ?",
                (TODAY,),
            )
            for row in cur.fetchall():
                past_dates.add(row[0])
            con.close()
        except Exception:
            pass

    past_dates = [d for d in past_dates if not (LOGS_DIR / f"{d}.md").exists()]
    if not past_dates:
        return
    print(f"과거 데이터 {len(past_dates)}일 소급 적용 중...")
    for date in sorted(past_dates):
        process_date(date, backfill=True)


def main():
    import sys

    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("❌ .env에 GITHUB_TOKEN, GITHUB_REPO가 필요합니다")
        return

    if "--backfill" in sys.argv:
        backfill()

    process_date(TODAY)

    push()
    print("✅ GitHub 푸시 완료")


if __name__ == "__main__":
    main()
