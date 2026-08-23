#!/usr/bin/env python3
"""
Send US screener results to Telegram with log/email fallback.
Can be invoked directly or from cron/n8n.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

FALLBACK_LOG = Path(__file__).parent / "fallback_notifications.log"


def _format_value(value: float) -> str:
    if value >= 1_000_000_000_000:
        return f"${value/1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value/1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    return f"${value:,.0f}"


def build_message(result: dict) -> str:
    date = result.get("date") or "Today"
    candidates = result.get("candidates", [])
    universe_size = result.get("universe_size")

    if not candidates:
        return (
            f"📊 *US Swing Screener — {date}*\n\n"
            "No candidates passed the filter today.\n"
            "_Stay flat. Wait for a better setup._"
        )

    header = f"📊 *US Swing Screener — {date}*"
    if universe_size:
        header += f"\nUniverse: {universe_size} tickers | Candidates: {len(candidates)}\n"
    else:
        header += f"\nCandidates: {len(candidates)}\n"

    lines = [header]
    for c in candidates:
        value_str = _format_value(c["value_traded"])
        rsi = f" | RSI14: {c['rsi14']:.1f}" if c.get("rsi14") else ""
        macd = f" | MACD: {c['macd']:.2f}" if c.get("macd") else ""

        lines.append(
            f"*{c['ticker']}* — close ${c['close']:,.2f}\n"
            f"vol: {value_str} | spike: {c['volume_spike']:.2f}x | MA20: ${c['ma20']:,.2f}{rsi}{macd}"
        )

        rec = c.get("recommendation")
        if rec:
            lines.append(
                f"💡 Entry: ${rec['entry_zone_low']:,.2f} – ${rec['entry_zone_high']:,.2f} | "
                f"SL: ${rec['stop_loss']:,.2f} | TP: ${rec['target_profit']:,.2f} | "
                f"R:R 1:{rec['risk_reward']:.2f}"
            )
            lines.append(f"📝 {rec['rationale']}")
            lines.append("")

    lines.append("\n⚠️ _Not a buy signal. Do your own due diligence before entering._")
    return "\n".join(lines)


def send_to_telegram(message: str, bot_token: str | None = None, chat_id: str | None = None) -> dict:
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def _fallback_email(subject: str, body: str) -> None:
    """Send a fallback email if SMTP is configured in the environment."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    email_to = os.environ.get("FALLBACK_EMAIL")

    if not all([smtp_host, smtp_port, smtp_user, smtp_pass, email_to]):
        return

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = email_to

        with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [email_to], msg.as_string())

        logger.info("Fallback email sent to %s", email_to)
    except Exception as e:
        logger.error("Failed to send fallback email: %s", e)


def fallback_notify(message: str, error: str) -> None:
    """Persist notification to a log file and/or send fallback email."""
    timestamp = dt.datetime.now().isoformat()
    entry = f"[{timestamp}] Telegram failed: {error}\n{message}\n{'-'*60}\n"

    with open(FALLBACK_LOG, "a", encoding="utf-8") as f:
        f.write(entry)

    logger.warning("Telegram notification failed, saved to %s", FALLBACK_LOG)
    _fallback_email("[US Screener] Telegram Fallback", message)


def send_result(result: dict) -> dict:
    """Build message and send to Telegram with fallback."""
    message = build_message(result)
    try:
        return send_to_telegram(message)
    except Exception as e:
        fallback_notify(message, str(e))
        raise


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: telegram_sender.py '<json_result>'", file=sys.stderr)
        return 1

    try:
        result = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print("Invalid JSON input.", file=sys.stderr)
        return 1

    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.", file=sys.stderr)
        print("See README.md for how to obtain your chat_id.", file=sys.stderr)
        return 1

    try:
        send_result(result)
        print("Message sent to Telegram.")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
