#!/usr/bin/env python3
"""
Health check for the US Screener.
Verifies: systemd timer active, SQLite has recent data, Telegram reachable.
Runnable manually or from cron/systemd timer.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "screener_history.db"
LOG_PATH = PROJECT_DIR / "health_check.log"


def log(message: str) -> None:
    timestamp = dt.datetime.now().isoformat()
    entry = f"[{timestamp}] {message}"
    print(entry)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry + "\n")


def check_systemd_timer() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "us-screener.timer"],
            capture_output=True,
            text=True,
            check=False,
        )
        active = result.stdout.strip() == "active"
        log(f"systemd timer: {'active' if active else 'INACTIVE'}")
        return active
    except Exception as e:
        log(f"systemd timer check failed: {e}")
        return False


def check_sqlite_recent(days: int = 3) -> bool:
    if not DB_PATH.exists():
        log("SQLite database not found.")
        return False

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute(
            "SELECT date FROM screenings ORDER BY date DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            log("No screenings recorded yet.")
            return False

        latest = dt.date.fromisoformat(row[0])
        today = dt.date.today()
        delta = (today - latest).days
        ok = delta <= days
        log(f"Latest screening: {latest} (delta={delta} days, threshold={days})")
        return ok
    except Exception as e:
        log(f"SQLite check failed: {e}")
        return False


def check_telegram() -> bool:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        log("Telegram credentials not configured, skipping Telegram check.")
        return True

    try:
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        ok = response.json().get("ok", False)
        log(f"Telegram bot check: {'OK' if ok else 'FAILED'}")
        return ok
    except Exception as e:
        log(f"Telegram bot check failed: {e}")
        return False


def main() -> int:
    log("=" * 60)
    log("Starting health check...")

    results = {
        "systemd_timer": check_systemd_timer(),
        "sqlite_recent": check_sqlite_recent(days=3),
        "telegram": check_telegram(),
    }

    all_ok = all(results.values())

    if all_ok:
        log("Health check PASSED.")
    else:
        log(f"Health check FAILED: {results}")

    log("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
