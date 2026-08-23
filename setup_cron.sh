#!/usr/bin/env bash
# Install the daily US screener cron job or systemd timer.
# Schedule: 09:00 America/New_York, Monday-Friday
# (30 minutes before the US market opens at 09:30 ET).
# Run once: bash setup_cron.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Prefer virtualenv python if available
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

echo "Detecting init system..."

if command -v systemctl &>/dev/null && systemctl --user status &>/dev/null; then
    echo "✅ Using systemd timer..."

    mkdir -p "$HOME/.config/systemd/user"

    cat > "$HOME/.config/systemd/user/us-screener.service" <<EOF
[Unit]
Description=US Daily Stock Screener (S&P 500 + Nasdaq-100 + ETFs)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$SCRIPT_DIR
Environment=TZ=America/New_York
ExecStart=$SCRIPT_DIR/run_screener.sh
StandardOutput=append:$SCRIPT_DIR/screener.log
StandardError=append:$SCRIPT_DIR/screener.log

[Install]
WantedBy=default.target
EOF

    cat > "$HOME/.config/systemd/user/us-screener.timer" <<EOF
[Unit]
Description=Run US Daily Stock Screener on weekdays at 09:00 America/New_York

[Timer]
# 09:00 US Eastern = 30 minutes before the 09:30 ET market open
OnCalendar=Mon..Fri *-*-* 09:00:00 America/New_York
Persistent=true

[Install]
WantedBy=timers.target
EOF

    # Dashboard: unified IDX+US if idx-screener exists alongside, else standalone US
    DASHBOARD_SCRIPT="dashboard.py"
    DASHBOARD_DESC="US Screener Dashboard (Flask)"
    if [ -d "$SCRIPT_DIR/../idx-screener" ]; then
        DASHBOARD_SCRIPT="unified_dashboard.py"
        DASHBOARD_DESC="Unified IDX+US Screener Dashboard (Flask)"
    fi

    cat > "$HOME/.config/systemd/user/us-screener-dashboard.service" <<EOF
[Unit]
Description=$DASHBOARD_DESC
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/$DASHBOARD_SCRIPT --port 5000
Restart=on-failure
RestartSec=5
StandardOutput=append:$SCRIPT_DIR/screener_dashboard.log
StandardError=append:$SCRIPT_DIR/screener_dashboard.log

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now us-screener.timer
    systemctl --user enable --now us-screener-dashboard.service
    systemctl --user status us-screener.timer --no-pager

    echo ""
    echo "✅ Systemd timer installed: weekdays at 09:00 America/New_York"
    echo "✅ Dashboard service installed: http://localhost:5000"
    echo "   Timer status:     systemctl --user status us-screener.timer"
    echo "   Dashboard status: systemctl --user status us-screener-dashboard"
else
    echo "✅ Using cron job..."

    # CRON_TZ pins the schedule to US Eastern regardless of host timezone
    CRON_LINE="CRON_TZ=America/New_York
0 9 * * 1-5 $SCRIPT_DIR/run_screener.sh >> $SCRIPT_DIR/screener.log 2>&1"

    (crontab -l 2>/dev/null | grep -v "us-screener\|run_screener.sh" || true; echo "$CRON_LINE") | crontab -

    echo "Cron job installed: weekdays at 09:00 America/New_York (30 min before open)"
    echo "$CRON_LINE"
    echo ""
    echo "Current cron jobs:"
    crontab -l
    echo ""
    echo "⚠️  Dashboard is not set up automatically via cron. Run manually:"
    echo "   python3 dashboard.py &"
fi
