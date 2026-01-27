#!/usr/bin/env python3
"""
Enforce budget limits per SSH key.
- Terminate instances when SSH key exceeds budget (unless "OVERBUDGET" in name)
- Send Discord notifications at spending milestones

Run via cron every 5 minutes.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

import utils_db as db
import utils_lambda_api as lambda_api

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
BUDGETS_FILE = DATA_DIR / "budgets.yaml"


def load_config():
    """Load config.env for defaults."""
    config_path = PROJECT_DIR / "config.env"
    config = {}
    if config_path.exists():
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    config[key.strip()] = value.strip()
    return config


CONFIG = load_config()
DEFAULT_LIMIT = int(CONFIG.get("BUDGET_LIMIT_DEFAULT", "500000"))
MILESTONE_INTERVAL = int(CONFIG.get("BUDGET_MILESTONE_INTERVAL", "100000"))


def log(msg: str):
    """Print timestamped log message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def load_budgets() -> dict:
    """Load budgets.yaml, creating default if it doesn't exist."""
    DATA_DIR.mkdir(exist_ok=True)
    
    if BUDGETS_FILE.exists():
        with open(BUDGETS_FILE) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    
    # Ensure structure
    if "defaults" not in data:
        data["defaults"] = {
            "limit_cents": DEFAULT_LIMIT,
            "milestone_interval": MILESTONE_INTERVAL,
        }
    if "keys" not in data:
        data["keys"] = {}
    
    return data


def get_limit_for_key(data: dict, ssh_key: str) -> int:
    """Get the effective limit for an SSH key (custom or default)."""
    key_config = data.get("keys", {}).get(ssh_key, {})
    limit = key_config.get("limit_cents", "default")
    if limit == "default" or limit is None:
        return data["defaults"]["limit_cents"]
    return int(limit)


def get_webhook_for_key(data: dict, ssh_key: str) -> str | None:
    """Get the Discord webhook URL for an SSH key."""
    key_config = data.get("keys", {}).get(ssh_key, {})
    return key_config.get("discord_webhook")


def format_money(cents: int | float) -> str:
    """Format cents as dollar string."""
    return f"${cents / 100:,.2f}"


def is_overbudget_allowed(instance: dict) -> bool:
    """Check if instance has 'OVERBUDGET' in name (allows running over budget)."""
    custom_name = instance.get("name") or ""
    return "overbudget" in custom_name.lower()


def send_discord_notification(webhook_url: str, ssh_key: str, spent_cents: int, limit_cents: int, is_over_budget: bool = False):
    """Send a Discord webhook notification."""
    if not webhook_url:
        return False
    
    try:
        if is_over_budget:
            color = 0xFF0000  # Red
            title = f"⚠️ Budget Exceeded: {ssh_key}"
            description = (
                f"**Spent:** {format_money(spent_cents)}\n"
                f"**Limit:** {format_money(limit_cents)}\n"
                f"**Over by:** {format_money(spent_cents - limit_cents)}\n\n"
                "Instances without 'OVERBUDGET' in name will be terminated."
            )
        else:
            color = 0xFFA500  # Orange
            title = f"💰 Spending Milestone: {ssh_key}"
            remaining = limit_cents - spent_cents
            description = (
                f"**Spent:** {format_money(spent_cents)}\n"
                f"**Limit:** {format_money(limit_cents)}\n"
                f"**Remaining:** {format_money(remaining)}"
            )
        
        payload = {
            "embeds": [{
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {"text": "Heron Infra Budget Monitor"}
            }]
        }
        
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        return True
        
    except Exception as e:
        log(f"  Failed to send Discord notification: {e}")
        return False


def check_milestone_notification(conn, data: dict, ssh_key: str, spent_cents: int, limit_cents: int) -> bool:
    """Check if we should send a milestone notification and send it if needed."""
    webhook_url = get_webhook_for_key(data, ssh_key)
    if not webhook_url:
        return False
    
    milestone_interval = data["defaults"].get("milestone_interval", MILESTONE_INTERVAL)
    
    # Get last notification info
    notif = db.get_budget_notification(conn, ssh_key)
    last_notified = notif["last_notified_cents"] if notif else 0
    
    # Calculate current milestone level
    current_milestone = (spent_cents // milestone_interval) * milestone_interval
    last_milestone = (last_notified // milestone_interval) * milestone_interval
    
    # Check if we crossed a milestone
    if current_milestone > last_milestone and current_milestone > 0:
        log(f"  {ssh_key}: Crossed milestone {format_money(current_milestone)}")
        
        is_over_budget = spent_cents > limit_cents
        if send_discord_notification(webhook_url, ssh_key, spent_cents, limit_cents, is_over_budget):
            db.update_budget_notification(conn, ssh_key, spent_cents)
            return True
    
    return False


def enforce_budget_for_key(conn, data: dict, ssh_key: str, spent_cents: int, active_instances: list[dict], dry_run: bool = False) -> int:
    """
    Enforce budget for a single SSH key.
    Returns number of instances terminated.
    """
    limit = get_limit_for_key(data, ssh_key)
    
    # Check milestone notifications first (even if under budget)
    check_milestone_notification(conn, data, ssh_key, spent_cents, limit)
    
    if spent_cents <= limit:
        # Under budget, nothing to enforce
        remaining = limit - spent_cents
        remaining_pct = (remaining / limit) * 100 if limit > 0 else 100
        if remaining_pct < 20:
            log(f"  {ssh_key}: {format_money(spent_cents)}/{format_money(limit)} (⚠ {remaining_pct:.0f}% remaining)")
        return 0
    
    # Over budget!
    over_by = spent_cents - limit
    log(f"  {ssh_key}: OVER BUDGET by {format_money(over_by)} ({format_money(spent_cents)}/{format_money(limit)})")
    
    # Find instances for this SSH key
    key_instances = []
    for inst in active_instances:
        ssh_keys = inst.get("ssh_key_names", [])
        if isinstance(ssh_keys, str):
            ssh_keys = json.loads(ssh_keys)
        if ssh_key in ssh_keys:
            key_instances.append(inst)
    
    if not key_instances:
        log(f"  {ssh_key}: No active instances to terminate")
        return 0
    
    # Send over-budget notification (only once when first going over)
    webhook_url = get_webhook_for_key(data, ssh_key)
    if webhook_url:
        notif = db.get_budget_notification(conn, ssh_key)
        last_notified = notif["last_notified_cents"] if notif else 0
        # Only send over-budget alert if we haven't notified at this level
        if last_notified < limit:
            send_discord_notification(webhook_url, ssh_key, spent_cents, limit, is_over_budget=True)
            db.update_budget_notification(conn, ssh_key, spent_cents)
    
    # Terminate instances (except those with OVERBUDGET in name)
    terminated = 0
    for inst in key_instances:
        name = inst.get("hostname") or inst.get("name") or inst["id"][:8]
        
        if is_overbudget_allowed(inst):
            log(f"    {name}: Has 'OVERBUDGET' in name - skipping")
            continue
        
        if dry_run:
            log(f"    {name}: WOULD TERMINATE (over budget)")
            terminated += 1
            continue
        
        log(f"    {name}: Terminating (over budget)...")
        try:
            result = lambda_api.terminate_instance([inst["id"]])
            if result:
                log(f"    {name}: Successfully terminated")
                terminated += 1
            else:
                log(f"    {name}: Failed to terminate")
        except Exception as e:
            log(f"    {name}: Error terminating: {e}")
    
    return terminated


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Enforce budget limits per SSH key")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without terminating")
    args = parser.parse_args()
    
    if args.dry_run:
        log("DRY RUN - no instances will be terminated")
    
    log("Checking budget limits...")
    
    data = load_budgets()
    conn = db.get_db()
    
    try:
        # Get current costs per SSH key
        costs = {c["ssh_key"]: c["total_cents"] for c in db.get_all_costs(conn)}
        
        if not costs:
            log("No spending data found")
            return
        
        # Get all active instances
        active_instances = db.get_active_instances(conn)
        
        total_terminated = 0
        for ssh_key, spent_cents in costs.items():
            terminated = enforce_budget_for_key(conn, data, ssh_key, spent_cents, active_instances, dry_run=args.dry_run)
            total_terminated += terminated
        
        if total_terminated > 0:
            action = "would terminate" if args.dry_run else "terminated"
            log(f"Done: {action} {total_terminated} instance(s)")
        else:
            log("Done: All budgets OK")
            
    except Exception as e:
        log(f"Error: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
