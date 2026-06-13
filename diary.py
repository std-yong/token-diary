#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types
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

SYSTEM_INSTRUCTION = """너는 개발자의 일일 작업 일기를 작성하는 어시스턴트야.
사용자가 Claude Code/Codex와 나눈 대화 발췌와 메타데이터를 보고,
그날 한 작업을 한국어 마크다운 불릿으로 압축 정리해.

[원칙]
- 기술 작업 중심. 잡담/메타 대화/AI에게 한 단순 요청은 제외
- 각 불릿은 "- " 로 시작, 명사형 또는 동사 종결형 ("X 기능 구현", "Y 버그 수정")
- 추측 금지: 대화에 나오지 않은 결과/완료 여부를 단정하지 말 것
- 토큰량·모델 분포는 작업 규모 가늠 단서로만 활용하고, 요약 본문에 직접 언급하지 말 것
- 출력은 불릿 3~5개만. 헤더·서론·맺음말 없이 불릿만 출력"""

USER_PROMPT = """[날짜] {date}
[총 토큰] {total_tokens}
[Claude 모델별 사용량] {model_breakdown}
[Codex 토큰] {codex_tokens}
[작업한 프로젝트] {projects}

[사용자 메시지 발췌 (시간순, 일부 잘림)]
{session_content}

위 정보를 바탕으로 오늘 한 핵심 작업을 3~5개 불릿으로 요약해."""


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


def _parse_retry_delay(err_str: str):
    m = re.search(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", err_str)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in ([\d.]+)s", err_str)
    if m:
        return float(m.group(1))
    return None


def _readable_project_names(project_dirs: list[Path]) -> list[str]:
    names: set[str] = set()
    for p in project_dirs:
        for w in WATCH_DIRS:
            prefix = str(w).replace("/", "-")
            if p.name == prefix or p.name.startswith(prefix + "-"):
                names.add(w.name)
                break
    return sorted(names)


def _fallback_summary(claude_tokens: int, model_breakdown: dict, codex_tokens: int, projects: list[str]) -> str:
    lines = []
    if projects:
        lines.append(f"- 작업 프로젝트: {', '.join(projects)}")
    parts = []
    if claude_tokens:
        parts.append(f"Claude {fmt_tokens(claude_tokens)}")
    if codex_tokens:
        parts.append(f"Codex {fmt_tokens(codex_tokens)}")
    if parts:
        lines.append(f"- {' / '.join(parts)} 토큰 사용")
    lines.append("- (AI 요약 미생성)")
    return "\n".join(lines)


def summarize(
    messages: list[str],
    *,
    date: str,
    claude_tokens: int,
    model_breakdown: dict,
    codex_tokens: int,
    project_dirs: list[Path],
) -> str:
    projects = _readable_project_names(project_dirs)
    if not messages or not GEMINI_API_KEY:
        if not GEMINI_API_KEY:
            return _fallback_summary(claude_tokens, model_breakdown, codex_tokens, projects)
        return "- (작업 내역 없음)"

    model_str = ", ".join(
        f"{m.replace('claude-','').replace('-20251001','').replace('-20250929','')} {fmt_tokens(v)}"
        for m, v in model_breakdown.items()
    ) or "없음"
    content = "\n\n".join(messages)[:10000]
    user_prompt = USER_PROMPT.format(
        date=date,
        total_tokens=fmt_tokens(claude_tokens + codex_tokens),
        model_breakdown=model_str,
        codex_tokens=fmt_tokens(codex_tokens) if codex_tokens else "0",
        projects=", ".join(projects) if projects else "(미상)",
        session_content=content,
    )

    client = genai.Client(api_key=GEMINI_API_KEY)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.3,
    )

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_prompt,
                config=config,
            )
            text = (response.text or "").strip()
            return text or _fallback_summary(claude_tokens, model_breakdown, codex_tokens, projects)
        except Exception as e:
            err_str = str(e)
            is_429 = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
            if is_429 and attempt < max_attempts - 1:
                delay = _parse_retry_delay(err_str) or (5 * (2 ** attempt))
                delay = min(delay, 60)
                print(f"[{date}] 429, {delay:.0f}s 대기 후 재시도 ({attempt+1}/{max_attempts})")
                time.sleep(delay + 1)
                continue
            print(f"[{date}] 요약 실패: {err_str[:120]}")
            return _fallback_summary(claude_tokens, model_breakdown, codex_tokens, projects)


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
        summary = summarize(
            all_messages,
            date=date,
            claude_tokens=claude_tokens,
            model_breakdown=model_breakdown,
            codex_tokens=codex_tokens,
            project_dirs=projects,
        )

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


def regenerate_broken() -> list[str]:
    """깨진 AI 요약을 가진 기존 로그 파일을 다시 요약해서 덮어쓴다."""
    broken_patterns = ("(요약 실패", "(작업 내역 없음)")
    targets: list[str] = []
    for log_file in sorted(LOGS_DIR.glob("*.md")):
        text = log_file.read_text(encoding="utf-8")
        if any(p in text for p in broken_patterns):
            targets.append(log_file.stem)

    if not targets:
        print("재생성할 깨진 로그 없음")
        return []

    print(f"깨진 로그 {len(targets)}개 재생성: {', '.join(targets)}")
    projects = find_today_projects()

    regenerated: list[str] = []
    for date in targets:
        claude_tokens, model_breakdown = load_claude_stats(date)
        codex_tokens = load_codex_stats(date)
        all_messages: list[str] = []
        for p in projects:
            all_messages.extend(extract_session_messages(p, date))
        all_messages = all_messages[:30]

        summary = summarize(
            all_messages,
            date=date,
            claude_tokens=claude_tokens,
            model_breakdown=model_breakdown,
            codex_tokens=codex_tokens,
            project_dirs=projects,
        )
        write_diary(date, claude_tokens, model_breakdown, codex_tokens, summary)
        regenerated.append(date)
        print(f"[{date}] ✅ 재생성 완료")
        time.sleep(2)  # 분당 호출 여유

    return regenerated


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

    if "--regenerate-broken" in sys.argv:
        regen = regenerate_broken()
        if regen:
            env = os.environ.copy()
            subprocess.run(["git", "add", "-A"], cwd=DIARY_REPO_DIR, check=True)
            result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=DIARY_REPO_DIR)
            if result.returncode != 0:
                msg = f"🔄 regenerate AI summaries ({len(regen)} days)"
                subprocess.run(["git", "commit", "-m", msg], cwd=DIARY_REPO_DIR, env=env, check=True)
        push()
        print("✅ GitHub 푸시 완료")
        return

    if "--backfill" in sys.argv:
        backfill()

    process_date(TODAY)

    push()
    print("✅ GitHub 푸시 완료")


if __name__ == "__main__":
    main()
