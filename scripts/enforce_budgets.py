#!/usr/bin/env python3
"""
Enforce budget limits per Lambda account.
- Terminate instances when account exceeds budget (unless "OVERBUDGET" in name)
- Send Discord notifications at spending milestones

Supports multiple Lambda Labs accounts.
Run via cron every 5 minutes.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import requests

import utils_accounts
import utils_db as db
import utils_lambda_api as lambda_api

PROJECT_DIR = Path(__file__).parent.parent


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


def format_money(cents: int | float) -> str:
    """Format cents as dollar string."""
    return f"${cents / 100:,.2f}"


def is_overbudget_allowed(instance: dict) -> bool:
    """Check if instance has 'OVERBUDGET' in name (allows running over budget)."""
    custom_name = instance.get("name") or ""
    return "overbudget" in custom_name.lower()


def send_discord_notification(webhook_url: str, account_name: str, spent_cents: int, limit_cents: int, is_over_budget: bool = False):
    """Send a Discord webhook notification."""
    if not webhook_url:
        return False
    
    try:
        if is_over_budget:
            color = 0xFF0000  # Red
            title = f"⚠️ Budget Exceeded: {account_name}"
            description = (
                f"**Account:** {account_name}\n"
                f"**Spent:** {format_money(spent_cents)}\n"
                f"**Limit:** {format_money(limit_cents)}\n"
                f"**Over by:** {format_money(spent_cents - limit_cents)}\n\n"
                "Instances without 'OVERBUDGET' in name will be terminated."
            )
        else:
            color = 0xFFA500  # Orange
            title = f"💰 Spending Milestone: {account_name}"
            remaining = limit_cents - spent_cents
            description = (
                f"**Account:** {account_name}\n"
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
        log(f"    Failed to send Discord notification: {e}")
        return False


def check_milestone_notification(conn, account: dict, spent_cents: int, accounts_data: dict) -> bool:
    """Check if we should send a milestone notification and send it if needed."""
    account_name = account["name"]
    webhook_url = account.get("discord_webhook")
    if not webhook_url:
        return False
    
    limit_cents = account["limit_cents"]
    milestone_interval = accounts_data.get("defaults", {}).get("milestone_interval", MILESTONE_INTERVAL)
    
    # Get last notification info
    notif = db.get_account_notification(conn, account_name)
    last_notified = notif["last_notified_cents"] if notif else 0
    
    # Calculate current milestone level
    current_milestone = (spent_cents // milestone_interval) * milestone_interval
    last_milestone = (last_notified // milestone_interval) * milestone_interval
    
    # Check if we crossed a milestone
    if current_milestone > last_milestone and current_milestone > 0:
        log(f"    Crossed milestone {format_money(current_milestone)}")
        
        is_over_budget = spent_cents > limit_cents
        if send_discord_notification(webhook_url, account_name, spent_cents, limit_cents, is_over_budget):
            db.update_account_notification(conn, account_name, spent_cents)
            return True
    
    return False


def enforce_budget_for_account(conn, account: dict, accounts_data: dict, dry_run: bool = False) -> int:
    """
    Enforce budget for a single account.
    Returns number of instances terminated.
    """
    account_name = account["name"]
    api_key = account["api_key"]
    limit_cents = account["limit_cents"]
    webhook_url = account.get("discord_webhook")
    
    # Get current spending for this account
    spent_cents = db.get_account_cost(conn, account_name)
    
    log(f"  Account: {account_name} - {format_money(spent_cents)}/{format_money(limit_cents)}")
    
    # Check milestone notifications first (even if under budget)
    check_milestone_notification(conn, account, spent_cents, accounts_data)
    
    if spent_cents <= limit_cents:
        # Under budget, nothing to enforce
        remaining = limit_cents - spent_cents
        remaining_pct = (remaining / limit_cents) * 100 if limit_cents > 0 else 100
        if remaining_pct < 20:
            log(f"    ⚠ Only {remaining_pct:.0f}% remaining ({format_money(remaining)})")
        return 0
    
    # Over budget!
    over_by = spent_cents - limit_cents
    log(f"    OVER BUDGET by {format_money(over_by)}")
    
    # Get active instances for this account
    active_instances = db.get_active_instances(conn, account=account_name)
    
    if not active_instances:
        log(f"    No active instances to terminate")
        return 0
    
    # Send over-budget notification (only once when first going over)
    if webhook_url:
        notif = db.get_account_notification(conn, account_name)
        last_notified = notif["last_notified_cents"] if notif else 0
        # Only send over-budget alert if we haven't notified at this level
        if last_notified < limit_cents:
            send_discord_notification(webhook_url, account_name, spent_cents, limit_cents, is_over_budget=True)
            db.update_account_notification(conn, account_name, spent_cents)
    
    # Terminate instances (except those with OVERBUDGET in name)
    terminated = 0
    for inst in active_instances:
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
            result = lambda_api.terminate_instance(api_key, [inst["id"]])
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
    parser = argparse.ArgumentParser(description="Enforce budget limits per account")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without terminating")
    args = parser.parse_args()
    
    if args.dry_run:
        log("DRY RUN - no instances will be terminated")
    
    log("Checking account budget limits...")
    
    # Load accounts
    accounts_data = utils_accounts.load_accounts()
    accounts = utils_accounts.get_account_list(accounts_data)
    
    if not accounts:
        log("No accounts configured")
        return
    
    conn = db.get_db()
    
    try:
        total_terminated = 0
        
        for account in accounts:
            try:
                terminated = enforce_budget_for_account(conn, account, accounts_data, dry_run=args.dry_run)
                total_terminated += terminated
            except Exception as e:
                log(f"  Error processing account {account['name']}: {e}")
        
        if total_terminated > 0:
            action = "would terminate" if args.dry_run else "terminated"
            log(f"Done: {action} {total_terminated} instance(s)")
        else:
            log("Done: All account budgets OK")
            
    except Exception as e:
        log(f"Error: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
