#!/usr/bin/env python3
"""
Simple web dashboard for US Screener candidate history.
Run: python3 dashboard.py
Open: http://localhost:5000
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, render_template_string, request

app = Flask(__name__)

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "screener_history.db"


@dataclass
class ScreeningStats:
    total_days: int
    total_candidates: int
    latest_date: str | None
    avg_candidates_per_day: float


def get_recent_screenings(days: int = 30, ticker: str | None = None) -> list[dict]:
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
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


def get_daily_counts(days: int = 30) -> list[dict]:
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    cursor = conn.execute(
        "SELECT date, COUNT(*) as count FROM screenings WHERE date >= ? GROUP BY date ORDER BY date",
        (cutoff,),
    )
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_ticker_volume_spike(days: int = 30) -> list[dict]:
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
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


def get_stats() -> ScreeningStats:
    if not DB_PATH.exists():
        return ScreeningStats(0, 0, None, 0.0)

    conn = sqlite3.connect(DB_PATH)
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
    no_candidates = _count_no_candidate_rows()
    total_candidates = max(0, total - no_candidates)
    avg = total_candidates / days if days else 0.0

    return ScreeningStats(days, total_candidates, latest, avg)


def _count_no_candidate_rows() -> int:
    if not DB_PATH.exists():
        return 0
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("SELECT COUNT(*) FROM screenings WHERE ticker = 'NO_CANDIDATE'")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_unique_tickers() -> list[str]:
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
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
    <title>US Screener Dashboard</title>
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
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin: 2rem 0;
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
            border: 1px solid #334155;
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
        th { background: #334155; color: var(--muted); font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em; }
        tr:not(:last-child) td { border-bottom: 1px solid #334155; }
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
        <h1>📊 US Screener Dashboard</h1>
        <p class="subtitle">Daily swing-trade candidate history — S&P 500 + Nasdaq-100 + ETFs</p>
        <p class="subtitle">Previous-day closing data, published 09:00 ET before the US market open</p>

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
                        No candidates passed the filter this day
                    </td>
                </tr>
                {% else %}
                <tr>
                    <td>{{ row.date }}</td>
                    <td><strong>{{ row.ticker }}</strong></td>
                    <td>${{ "{:,.2f}".format(row.close) }}</td>
                    <td class="{{ 'up' if row.volume_spike >= 1.5 else 'muted' }}">{{ "{:.2f}x".format(row.volume_spike) }}</td>
                    <td>${{ "{:,.2f}".format(row.ma20) }}</td>
                    <td>{{ "{:.1f}".format(row.rsi14) if row.rsi14 else "-" }}</td>
                    <td>
                        {% if row.entry_zone_low and row.entry_zone_high %}
                        ${{ "{:,.2f}".format(row.entry_zone_low) }} - ${{ "{:,.2f}".format(row.entry_zone_high) }}
                        {% else %}
                        -
                        {% endif %}
                    </td>
                    <td class="down">{{ "${:,.2f}".format(row.stop_loss) if row.stop_loss else "-" }}</td>
                    <td class="up">{{ "${:,.2f}".format(row.target_profit) if row.target_profit else "-" }}</td>
                    <td>{{ "1:{:.1f}".format(row.risk_reward) if row.risk_reward else "-" }}</td>
                </tr>
                {% endif %}
                {% else %}
                <tr>
                    <td colspan="10" style="text-align:center; color: var(--muted);">No data yet. Run the screener first.</td>
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


@app.route("/")
def index():
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30

    ticker = request.args.get("ticker", "").strip().upper() or None
    screenings = get_recent_screenings(days=days, ticker=ticker)
    stats = get_stats()
    tickers = get_unique_tickers()

    daily = get_daily_counts(days=days)
    daily_labels = [d["date"][-5:] for d in daily]  # MM-DD
    daily_counts = [d["count"] for d in daily]

    spike_data = get_ticker_volume_spike(days=days)
    spike_labels = [d["ticker"] for d in spike_data]
    spike_values = [round(d["avg_spike"], 2) for d in spike_data]

    return render_template_string(
        DASHBOARD_TEMPLATE,
        stats=stats,
        screenings=screenings,
        tickers=tickers,
        days=days,
        ticker=ticker or "",
        daily_labels=daily_labels,
        daily_counts=daily_counts,
        spike_labels=spike_labels,
        spike_values=spike_values,
    )


@app.route("/api/screenings")
def api_screenings():
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30

    ticker = request.args.get("ticker", "").strip().upper() or None
    screenings = get_recent_screenings(days=days, ticker=ticker)
    return {"screenings": screenings, "count": len(screenings)}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
