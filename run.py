#!/usr/bin/env python3
"""
Cross-platform runner for the US Screener.
Runs the screener and (optionally) sends results to Telegram.
Callable from cron, systemd, Task Scheduler, or a terminal.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

PROJECT_DIR = Path(__file__).parent
PYTHON = sys.executable


def run_screener_json(preset: str = "default", universe: str = "all") -> dict:
    """Run the screener and return parsed JSON results."""
    result = subprocess.run(
        [PYTHON, str(PROJECT_DIR / "us_screener.py"),
         "--preset", preset, "--universe", universe, "--output", "json"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Screener failed:\n{result.stderr}")

    stdout = result.stdout.strip()
    # The screener emits pretty-printed JSON at the end of stdout;
    # find the first opening brace of the JSON document.
    brace_idx = stdout.find("{")
    if brace_idx >= 0:
        try:
            return json.loads(stdout[brace_idx:])
        except json.JSONDecodeError:
            pass

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    raise RuntimeError(f"No valid JSON output from screener.\nOutput: {stdout[:200]}")


def send_telegram(payload: dict) -> None:
    """Send results to Telegram if credentials are configured."""
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        return

    result = subprocess.run(
        [PYTHON, str(PROJECT_DIR / "telegram_sender.py"), json.dumps(payload)],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        print(f"Telegram failed: {result.stderr}", file=sys.stderr)
    else:
        print(result.stdout.strip())


def main() -> int:
    preset = os.environ.get("SCREENER_PRESET", "default")
    universe = os.environ.get("SCREENER_UNIVERSE", "all")

    try:
        payload = run_screener_json(preset=preset, universe=universe)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    send_telegram(payload)

    # Single-line JSON on stdout for easy parsing by bash/systemd
    print(json.dumps(payload, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
