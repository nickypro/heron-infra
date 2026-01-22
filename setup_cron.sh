#!/bin/bash
# Setup cron jobs for Lambda infrastructure monitoring and backup

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON=$(which python3)

# Create logs directory
mkdir -p "$SCRIPT_DIR/logs"

# Cron job definitions
MONITOR_CRON="* * * * * cd $SCRIPT_DIR && $PYTHON monitor.py >> logs/monitor.log 2>&1"
BACKUP_CRON="*/30 * * * * cd $SCRIPT_DIR && $PYTHON backup.py >> logs/backup.log 2>&1"

# Marker for our cron jobs
MARKER="# heron-infra"

echo "Setting up cron jobs for heron-infra..."
echo "Script directory: $SCRIPT_DIR"
echo "Python: $PYTHON"
echo ""

# Check if config.env exists
if [ ! -f "$SCRIPT_DIR/config.env" ]; then
    echo "WARNING: config.env not found!"
    echo "Please copy config.env.example to config.env and add your Lambda API key:"
    echo "  cp $SCRIPT_DIR/config.env.example $SCRIPT_DIR/config.env"
    echo ""
fi

# Get current crontab (or empty if none)
CURRENT_CRON=$(crontab -l 2>/dev/null || echo "")

# Remove any existing heron-infra entries
CLEANED_CRON=$(echo "$CURRENT_CRON" | grep -v "$MARKER" || true)

# Add new entries
NEW_CRON="$CLEANED_CRON
$MARKER - monitor (every minute)
$MONITOR_CRON
$MARKER - backup (every 30 minutes)
$BACKUP_CRON"

# Install new crontab
echo "$NEW_CRON" | crontab -

echo "Cron jobs installed successfully!"
echo ""
echo "Scheduled jobs:"
echo "  - Monitor: every minute"
echo "  - Backup:  every 30 minutes"
echo ""
echo "Logs will be written to:"
echo "  - $SCRIPT_DIR/logs/monitor.log"
echo "  - $SCRIPT_DIR/logs/backup.log"
echo ""
echo "To view current cron jobs:"
echo "  crontab -l"
echo ""
echo "To remove cron jobs:"
echo "  crontab -l | grep -v 'heron-infra' | crontab -"
