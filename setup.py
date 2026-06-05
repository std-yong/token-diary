#!/usr/bin/env python3
"""Mac 자동 실행 등록 (매일 밤 11:50)"""
import subprocess
from pathlib import Path

REPO_DIR = Path(__file__).parent.absolute()
PLIST_PATH = Path.home() / "Library/LaunchAgents/com.claude-token-diary.plist"

plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-token-diary</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>{REPO_DIR}/diary.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>23</integer>
        <key>Minute</key>
        <integer>50</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{REPO_DIR}/diary.log</string>
    <key>StandardErrorPath</key>
    <string>{REPO_DIR}/diary.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>"""

PLIST_PATH.write_text(plist)
subprocess.run(["launchctl", "load", str(PLIST_PATH)], check=True)
print(f"✅ 자동 실행 등록 완료 — 매일 밤 11:50")
print(f"   로그: {REPO_DIR}/diary.log")
