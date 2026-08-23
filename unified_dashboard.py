#!/usr/bin/env python3
"""
Unified dashboard for both IDX and US screeners.

Reads both SQLite history databases and serves one web UI with a
market toggle button (IDX 🇮🇩 / US 🇺🇸).

Run:  python3 unified_dashboard.py [--port 5000]
Open: http://localhost:5000  (default) or any port via --port / PORT env
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, render_template_string, request

app = Flask(__name__)

US_DIR = Path(__file__).parent
IDX_DIR = US_DIR.parent / "idx-screener"

# ---------------------------------------------------------------------------
# Market registry
# ---------------------------------------------------------------------------
MARKETS = {
    "idx": {
        "label": "IDX",
        "flag": "🇮🇩",
        "title": "IDX Screener",
        "db": IDX_DIR / "screener_history.db",
        "currency": "Rp",
        "price_decimals": 0,
        "subtitle": "Histori kandidat swing trade harian — Bursa Efek Indonesia",
        "timing": "Data closing H-1, dirilis hari H jam 08:30 WIB sebelum bursa buka",
        "no_candidate": "Tidak ada kandidat yang lolos filter hari ini",
        "empty": "Belum ada data. Jalankan screener IDX terlebih dahulu.",
    },
    "us": {
        "label": "US",
        "flag": "🇺🇸",
        "title": "US Screener",
        "db": US_DIR / "screener_history.db",
        "currency": "$",
        "price_decimals": 2,
        "subtitle": "Daily swing-trade candidate history — S&P 500 + Nasdaq-100 + ETFs",
        "timing": "Previous-day closing data, published 09:00 ET before the US market open",
        "no_candidate": "No candidates passed the filter this day",
        "empty": "No data yet. Run the US screener first.",
    },
}


@dataclass
class ScreeningStats:
    total_days: int
    total_candidates: int
    latest_date: str | None
    avg_candidates_per_day: float


def get_recent_screenings(db: Path, days: int = 30, ticker: str | None = None) -> list[dict]:
    if not db.exists():
        return []

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    sql = "SELECT * FROM screenings WHERE date >= ?"
    params: list = [cutoff]

    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker)

    sql += " ORDER BY date DESC, volume_spike DESC"

    cursor = conn.execute(sql, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_daily_counts(db: Path, days: int = 30) -> list[dict]:
    if not db.exists():
        return []

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    cursor = conn.execute(
        "SELECT date, COUNT(*) as count FROM screenings WHERE date >= ? GROUP BY date ORDER BY date",
        (cutoff,),
    )
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_ticker_volume_spike(db: Path, days: int = 30) -> list[dict]:
    if not db.exists():
        return []

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    cursor = conn.execute(
        """
        SELECT ticker, AVG(volume_spike) as avg_spike, COUNT(*) as count
        FROM screenings
        WHERE date >= ? AND ticker != 'NO_CANDIDATE'
        GROUP BY ticker
        ORDER BY avg_spike DESC
        """,
        (cutoff,),
    )
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def _count_no_candidate_rows(db: Path) -> int:
    if not db.exists():
        return 0
    conn = sqlite3.connect(db)
    cursor = conn.execute("SELECT COUNT(*) FROM screenings WHERE ticker = 'NO_CANDIDATE'")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_stats(db: Path) -> ScreeningStats:
    if not db.exists():
        return ScreeningStats(0, 0, None, 0.0)

    conn = sqlite3.connect(db)
    cursor = conn.execute(
        "SELECT COUNT(DISTINCT date) as days, COUNT(*) as total, MAX(date) as latest FROM screenings"
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return ScreeningStats(0, 0, None, 0.0)

    days = row[0]
    total = row[1]
    latest = row[2]
    no_candidates = _count_no_candidate_rows(db)
    total_candidates = max(0, total - no_candidates)
    avg = total_candidates / days if days else 0.0

    return ScreeningStats(days, total_candidates, latest, avg)


def get_unique_tickers(db: Path) -> list[str]:
    if not db.exists():
        return []

    conn = sqlite3.connect(db)
    cursor = conn.execute("SELECT DISTINCT ticker FROM screenings ORDER BY ticker")
    tickers = [row[0] for row in cursor.fetchall() if row[0] != "NO_CANDIDATE"]
    conn.close()
    return tickers


DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Screener Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
    <style>
        :root {
            --bg: #0f172a;
            --card: #1e293b;
            --text: #f8fafc;
            --muted: #94a3b8;
            --accent: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --border: #334155;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
        }
        .container { max-width: 1200px; margin: 0 auto; padding: 2rem; }
        h1 { margin-bottom: 0.5rem; }
        .subtitle { color: var(--muted); margin-bottom: 0.25rem; }

        .market-toggle {
            display: inline-flex;
            gap: 0;
            margin: 1rem 0 0.5rem 0;
            border: 1px solid var(--border);
            border-radius: 10px;
            overflow: hidden;
        }
        .market-toggle a {
            padding: 0.6rem 1.5rem;
            text-decoration: none;
            color: var(--muted);
            background: var(--card);
            font-weight: 600;
            font-size: 1rem;
            transition: background 0.15s, color 0.15s;
        }
        .market-toggle a:not(:last-child) { border-right: 1px solid var(--border); }
        .market-toggle a:hover { background: rgba(255,255,255,0.05); color: var(--text); }
        .market-toggle a.active {
            background: var(--accent);
            color: #000;
        }

        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin: 1.5rem 0 2rem 0;
        }
        .stat-card {
            background: var(--card);
            padding: 1.5rem;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .stat-card h3 { margin: 0; color: var(--muted); font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em; }
        .stat-card .value { font-size: 2rem; font-weight: 700; margin-top: 0.5rem; }
        .controls {
            display: flex;
            gap: 1rem;
            margin-bottom: 1.5rem;
            flex-wrap: wrap;
            align-items: center;
        }
        .controls label { color: var(--muted); }
        .controls select, .controls input {
            background: var(--card);
            color: var(--text);
            border: 1px solid var(--border);
            padding: 0.5rem 0.75rem;
            border-radius: 6px;
        }
        .controls button {
            background: var(--accent);
            color: #000;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 6px;
            font-weight: 600;
            cursor: pointer;
        }
        .charts {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        .chart-card {
            background: var(--card);
            padding: 1.5rem;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .chart-card h3 { margin: 0 0 1rem 0; color: var(--muted); font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em; }
        .table-wrap { overflow-x: auto; }
        table {
            width: 100%;
            border-collapse: collapse;
            background: var(--card);
            border-radius: 12px;
            overflow: hidden;
        }
        th, td { padding: 1rem; text-align: left; white-space: nowrap; }
        th { background: var(--border); color: var(--muted); font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em; }
        tr:not(:last-child) td { border-bottom: 1px solid var(--border); }
        tr:hover { background: rgba(255,255,255,0.03); }
        .up { color: var(--accent); }
        .down { color: var(--danger); }
        .muted { color: var(--muted); }
        @media (max-width: 768px) {
            .container { padding: 1rem; }
            .charts { grid-template-columns: 1fr; }
            table { font-size: 0.875rem; }
            th, td { padding: 0.75rem 0.5rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 {{ market.title }} Dashboard</h1>
        <p class="subtitle">{{ market.subtitle }}</p>
        <p class="subtitle">{{ market.timing }}</p>

        <div class="market-toggle">
            {% for key, m in markets.items() %}
            <a href="/?market={{ key }}&days={{ days }}{% if ticker %}&ticker={{ ticker }}{% endif %}"
               class="{{ 'active' if key == market_key else '' }}">{{ m.flag }} {{ m.label }}</a>
            {% endfor %}
        </div>

        <div class="stats">
            <div class="stat-card">
                <h3>Days Recorded</h3>
                <div class="value">{{ stats.total_days }}</div>
            </div>
            <div class="stat-card">
                <h3>Total Candidates</h3>
                <div class="value">{{ stats.total_candidates }}</div>
            </div>
            <div class="stat-card">
                <h3>Avg / Day</h3>
                <div class="value">{{ "%.1f" % stats.avg_candidates_per_day }}</div>
            </div>
            <div class="stat-card">
                <h3>Latest Date</h3>
                <div class="value">{{ stats.latest_date or "-" }}</div>
            </div>
        </div>

        <form method="GET" class="controls">
            <input type="hidden" name="market" value="{{ market_key }}">
            <label for="days">Period:</label>
            <select name="days" id="days">
                <option value="7" {{ "selected" if days == 7 else "" }}>7 days</option>
                <option value="30" {{ "selected" if days == 30 else "" }}>30 days</option>
                <option value="60" {{ "selected" if days == 60 else "" }}>60 days</option>
                <option value="90" {{ "selected" if days == 90 else "" }}>90 days</option>
            </select>

            <label for="ticker">Ticker filter:</label>
            <select name="ticker" id="ticker">
                <option value="">All</option>
                {% for t in tickers %}
                <option value="{{ t }}" {{ "selected" if ticker == t else "" }}>{{ t }}</option>
                {% endfor %}
            </select>

            <button type="submit">Apply</button>
        </form>

        <div class="charts">
            <div class="chart-card">
                <h3>Candidates per Day</h3>
                <canvas id="dailyChart"></canvas>
            </div>
            <div class="chart-card">
                <h3>Volume Spike per Ticker</h3>
                <canvas id="spikeChart"></canvas>
            </div>
        </div>

        <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Ticker</th>
                    <th>Close</th>
                    <th>Vol Spike</th>
                    <th>MA20</th>
                    <th>RSI14</th>
                    <th>Entry Zone</th>
                    <th>SL</th>
                    <th>TP</th>
                    <th>R:R</th>
                </tr>
            </thead>
            <tbody>
                {% for row in screenings %}
                {% if row.ticker == 'NO_CANDIDATE' %}
                <tr style="opacity: 0.7; background: rgba(255,255,255,0.02);">
                    <td>{{ row.date }}</td>
                    <td colspan="9" style="text-align: center; color: var(--muted); font-style: italic;">
                        {{ market.no_candidate }}
                    </td>
                </tr>
                {% else %}
                <tr>
                    <td>{{ row.date }}</td>
                    <td><strong>{{ row.ticker }}</strong></td>
                    <td>{{ fmt_price(row.close) }}</td>
                    <td class="{{ 'up' if row.volume_spike >= 1.5 else 'muted' }}">{{ "{:.2f}x".format(row.volume_spike) }}</td>
                    <td>{{ fmt_price(row.ma20) }}</td>
                    <td>{{ "{:.1f}".format(row.rsi14) if row.rsi14 else "-" }}</td>
                    <td>
                        {% if row.entry_zone_low and row.entry_zone_high %}
                        {{ fmt_price(row.entry_zone_low) }} - {{ fmt_price(row.entry_zone_high) }}
                        {% else %}
                        -
                        {% endif %}
                    </td>
                    <td class="down">{{ fmt_price(row.stop_loss) if row.stop_loss else "-" }}</td>
                    <td class="up">{{ fmt_price(row.target_profit) if row.target_profit else "-" }}</td>
                    <td>{{ "1:{:.1f}".format(row.risk_reward) if row.risk_reward else "-" }}</td>
                </tr>
                {% endif %}
                {% else %}
                <tr>
                    <td colspan="10" style="text-align:center; color: var(--muted);">{{ market.empty }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        </div>
    </div>

    <script>
        const dailyLabels = {{ daily_labels | tojson }};
        const dailyCounts = {{ daily_counts | tojson }};
        const spikeLabels = {{ spike_labels | tojson }};
        const spikeValues = {{ spike_values | tojson }};

        new Chart(document.getElementById('dailyChart'), {
            type: 'bar',
            data: {
                labels: dailyLabels,
                datasets: [{
                    label: 'Candidates',
                    data: dailyCounts,
                    backgroundColor: 'rgba(16, 185, 129, 0.7)',
                    borderColor: '#10b981',
                    borderWidth: 1,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    y: { beginAtZero: true, ticks: { color: '#94a3b8' }, grid: { color: '#334155' } },
                    x: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } }
                }
            }
        });

        new Chart(document.getElementById('spikeChart'), {
            type: 'bar',
            data: {
                labels: spikeLabels,
                datasets: [{
                    label: 'Avg Volume Spike',
                    data: spikeValues,
                    backgroundColor: 'rgba(245, 158, 11, 0.7)',
                    borderColor: '#f59e0b',
                    borderWidth: 1,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    y: { beginAtZero: true, ticks: { color: '#94a3b8' }, grid: { color: '#334155' } },
                    x: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } }
                }
            }
        });
    </script>
</body>
</html>
"""


def _resolve_market(key: str | None) -> tuple[str, dict]:
    key = (key or "us").lower()
    if key not in MARKETS:
        key = "us"
    return key, MARKETS[key]


@app.route("/")
def index():
    market_key, market = _resolve_market(request.args.get("market"))

    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30

    ticker = request.args.get("ticker", "").strip().upper() or None
    db = market["db"]

    screenings = get_recent_screenings(db, days=days, ticker=ticker)
    stats = get_stats(db)
    tickers = get_unique_tickers(db)

    daily = get_daily_counts(db, days=days)
    daily_labels = [d["date"][-5:] for d in daily]  # MM-DD
    daily_counts = [d["count"] for d in daily]

    spike_data = get_ticker_volume_spike(db, days=days)
    spike_labels = [d["ticker"] for d in spike_data]
    spike_values = [round(d["avg_spike"], 2) for d in spike_data]

    currency = market["currency"]
    decimals = market["price_decimals"]

    def fmt_price(value: float) -> str:
        return f"{currency}{value:,.{decimals}f}"

    return render_template_string(
        DASHBOARD_TEMPLATE,
        markets=MARKETS,
        market_key=market_key,
        market=market,
        stats=stats,
        screenings=screenings,
        tickers=tickers,
        days=days,
        ticker=ticker or "",
        daily_labels=daily_labels,
        daily_counts=daily_counts,
        spike_labels=spike_labels,
        spike_values=spike_values,
        fmt_price=fmt_price,
    )


@app.route("/api/screenings")
def api_screenings():
    market_key, market = _resolve_market(request.args.get("market"))

    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30

    ticker = request.args.get("ticker", "").strip().upper() or None
    screenings = get_recent_screenings(market["db"], days=days, ticker=ticker)
    return {"market": market_key, "screenings": screenings, "count": len(screenings)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified IDX + US screener dashboard")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
