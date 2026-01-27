#!/usr/bin/env python3
"""
Check usage/cost per Lambda account over different time periods.
Supports multiple Lambda Labs accounts.
"""

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import utils_accounts
import utils_db as db

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


def format_cost(cents: float) -> str:
    """Format cents to dollar string."""
    return f"${cents / 100:.2f}"


def format_duration(hours: float) -> str:
    """Format hours to readable string."""
    if hours < 1:
        return f"{int(hours * 60)}m"
    elif hours < 24:
        return f"{hours:.1f}h"
    else:
        days = hours / 24
        return f"{days:.1f}d"


def get_usage_by_account(conn, since_timestamp: float) -> dict:
    """
    Calculate usage per account since a given timestamp.
    Returns dict of {account: {cost_cents, hours, instances}}.
    """
    # Get all GPU samples in the time range, grouped by instance
    samples = conn.execute("""
        SELECT DISTINCT instance_id, timestamp 
        FROM gpu_samples 
        WHERE timestamp > ?
        ORDER BY instance_id, timestamp
    """, (since_timestamp,)).fetchall()
    
    if not samples:
        return {}
    
    # Get instance info (hourly cost, account) - includes terminated instances
    instances = {}
    for row in conn.execute("SELECT * FROM instances").fetchall():
        inst = dict(row)
        instances[inst["id"]] = {
            "hourly_cents": inst.get("hourly_cost_cents", 0),
            "account": inst.get("account") or "default",
            "name": inst.get("hostname") or inst.get("name") or inst["id"][:8],
            "type": inst.get("instance_type", "unknown"),
            "status": inst.get("status", "unknown"),
        }
    
    # Group samples by instance and count time slots (each sample = ~1 minute)
    instance_minutes = defaultdict(int)
    for sample in samples:
        instance_minutes[sample["instance_id"]] += 1
    
    # Calculate cost per account
    usage_by_account = defaultdict(lambda: {"cost_cents": 0, "hours": 0, "instances": {}})
    
    for instance_id, minutes in instance_minutes.items():
        if instance_id not in instances:
            continue
        
        inst = instances[instance_id]
        account = inst["account"]
        hours = minutes / 60
        cost_cents = (inst["hourly_cents"] * minutes) / 60
        
        usage_by_account[account]["cost_cents"] += cost_cents
        usage_by_account[account]["hours"] += hours
        usage_by_account[account]["instances"][inst["name"]] = {
            "hours": hours,
            "status": inst["status"],
        }
    
    for acct in usage_by_account:
        usage_by_account[acct]["instances"] = dict(usage_by_account[acct]["instances"])
    
    return dict(usage_by_account)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Check usage per Lambda account")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    conn = db.get_db()
    
    try:
        now = time.time()
        
        # Load accounts config
        accounts_data = utils_accounts.load_accounts()
        accounts_list = utils_accounts.get_account_list(accounts_data)
        account_budgets = {acc["name"]: acc for acc in accounts_list}
        default_limit = accounts_data.get("defaults", {}).get("limit_cents", DEFAULT_LIMIT)
        
        # Calculate usage for different time periods
        periods = {
            "24h": now - 86400,
        }
        
        usage_data = {}
        for period_name, since in periods.items():
            usage_data[period_name] = get_usage_by_account(conn, since)
        
        # Get all-time totals from account_costs table
        all_time = {c["account"]: {"cost_cents": c["total_cents"]} for c in db.get_all_account_costs(conn)}
        
        # Get all accounts (from config + from costs)
        all_accounts = set(account_budgets.keys()) | set(all_time.keys())
        
        if args.json:
            output = {}
            for acct in sorted(all_accounts):
                acc_config = account_budgets.get(acct)
                limit = acc_config["limit_cents"] if acc_config else default_limit
                total = all_time.get(acct, {}).get("cost_cents", 0)
                output[acct] = {
                    "24h": usage_data["24h"].get(acct, {}).get("cost_cents", 0),
                    "total": total,
                    "limit": limit,
                    "remaining": limit - total,
                }
            print(json.dumps(output, indent=2))
        else:
            print(f"\n  Usage by Account  │  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Default budget: {format_cost(default_limit)}\n")
            
            if not all_accounts:
                print("  No usage data found.\n")
                return
            
            # Header
            print(f"  {'Account':<20} │ {'24 Hours':>9} │ {'Total':>10} │ {'Limit':>10} │ {'Remaining':>10}")
            print(f"  {'-'*20}-┼-{'-'*9}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}")
            
            # Sort by total cost descending
            sorted_accounts = sorted(all_accounts, key=lambda a: all_time.get(a, {}).get("cost_cents", 0), reverse=True)
            
            totals = {"24h": 0, "total": 0}
            
            for acct in sorted_accounts:
                cost_24h = usage_data["24h"].get(acct, {}).get("cost_cents", 0)
                cost_total = all_time.get(acct, {}).get("cost_cents", 0)
                
                acc_config = account_budgets.get(acct)
                limit = acc_config["limit_cents"] if acc_config else default_limit
                remaining = limit - cost_total
                
                totals["24h"] += cost_24h
                totals["total"] += cost_total
                
                # Truncate long names
                display_name = acct[:20] if len(acct) <= 20 else acct[:17] + "..."
                
                # Show limit with indicator if using default
                is_custom_limit = acc_config is not None
                limit_str = format_cost(limit) if is_custom_limit else f"{format_cost(limit)}*"
                
                # Remaining with status
                if remaining < 0:
                    remaining_str = f"{format_cost(remaining)} ⚠"
                elif remaining < limit * 0.2:
                    remaining_str = f"{format_cost(remaining)} !"
                else:
                    remaining_str = format_cost(remaining)
                
                print(f"  {display_name:<20} │ {format_cost(cost_24h):>9} │ {format_cost(cost_total):>10} │ {limit_str:>10} │ {remaining_str:>10}")
            
            # Totals row
            print(f"  {'-'*20}-┼-{'-'*9}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}")
            print(f"  {'TOTAL':<20} │ {format_cost(totals['24h']):>9} │ {format_cost(totals['total']):>10} │ {'-':>10} │ {'-':>10}")
            print(f"\n  * = using default limit, ! = <20% left, ⚠ = over budget")
            
            print()
            
            # Show hours breakdown for recent period
            if usage_data["24h"]:
                print("  Hours by instance (24h):")
                for acct in sorted_accounts:
                    data = usage_data["24h"].get(acct, {})
                    instances = data.get("instances", {})
                    if instances:
                        print(f"    {acct}:")
                        for inst_name, inst_data in sorted(instances.items(), key=lambda x: x[1]["hours"], reverse=True):
                            status = inst_data.get("status", "unknown")
                            status_icon = "●" if status == "active" else "○"
                            print(f"      {status_icon} {inst_name}: {format_duration(inst_data['hours'])}")
                print()
        
    finally:
        conn.close()


if __name__ == "__main__":
    main()
