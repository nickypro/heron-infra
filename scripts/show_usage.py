#!/usr/bin/env python3
"""
Check usage/cost per SSH key over different time periods.
"""

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import yaml

import utils_db as db

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


def load_budgets() -> dict:
    """Load budgets.yaml if it exists."""
    if BUDGETS_FILE.exists():
        with open(BUDGETS_FILE) as f:
            return yaml.safe_load(f) or {}
    return {}


def get_limit_for_key(data: dict, ssh_key: str) -> int:
    """Get the effective limit for an SSH key (custom or default)."""
    default_limit = data.get("defaults", {}).get("limit_cents", DEFAULT_LIMIT)
    key_config = data.get("keys", {}).get(ssh_key, {})
    limit = key_config.get("limit_cents", "default")
    if limit == "default" or limit is None:
        return default_limit
    return int(limit)


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


def get_usage_by_key(conn, since_timestamp: float) -> dict:
    """
    Calculate usage per SSH key since a given timestamp.
    Returns dict of {ssh_key: {cost_cents, hours, instances}}.
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
    
    # Get instance info (hourly cost, ssh keys) - includes terminated instances
    instances = {}
    for row in conn.execute("SELECT * FROM instances").fetchall():
        inst = dict(row)
        ssh_keys = inst.get("ssh_key_names", "[]")
        if isinstance(ssh_keys, str):
            ssh_keys = json.loads(ssh_keys)
        instances[inst["id"]] = {
            "hourly_cents": inst.get("hourly_cost_cents", 0),
            "ssh_key": ssh_keys[0] if ssh_keys else "unknown",
            "name": inst.get("hostname") or inst.get("name") or inst["id"][:8],
            "type": inst.get("instance_type", "unknown"),
            "status": inst.get("status", "unknown"),
        }
    
    # Group samples by instance and count time slots (each sample = ~1 minute)
    instance_minutes = defaultdict(int)
    for sample in samples:
        instance_minutes[sample["instance_id"]] += 1
    
    # Calculate cost per SSH key
    usage_by_key = defaultdict(lambda: {"cost_cents": 0, "hours": 0, "instances": {}})
    
    for instance_id, minutes in instance_minutes.items():
        if instance_id not in instances:
            # Instance not in DB (shouldn't happen, but handle gracefully)
            continue
        
        inst = instances[instance_id]
        ssh_key = inst["ssh_key"]
        hours = minutes / 60
        cost_cents = (inst["hourly_cents"] * minutes) / 60
        
        usage_by_key[ssh_key]["cost_cents"] += cost_cents
        usage_by_key[ssh_key]["hours"] += hours
        # Store instance with its hours and status
        usage_by_key[ssh_key]["instances"][inst["name"]] = {
            "hours": hours,
            "status": inst["status"],
        }
    
    # Convert instance dicts to lists for JSON serialization
    for key in usage_by_key:
        usage_by_key[key]["instances"] = dict(usage_by_key[key]["instances"])
    
    return dict(usage_by_key)


def get_all_time_usage(conn) -> dict:
    """Get all-time usage from the costs table."""
    costs = db.get_all_costs(conn)
    return {c["ssh_key"]: {"cost_cents": c["total_cents"], "last_updated": c["last_updated"]} for c in costs}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Check usage per SSH key")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    conn = db.get_db()
    
    try:
        now = time.time()
        
        # Calculate usage for different time periods
        periods = {
            "1h": now - 3600,
            "24h": now - 86400,
            "7d": now - 7 * 86400,
        }
        
        usage_data = {}
        for period_name, since in periods.items():
            usage_data[period_name] = get_usage_by_key(conn, since)
        
        # Get all-time totals
        all_time = get_all_time_usage(conn)
        
        # Get all SSH keys
        all_keys = set()
        for period_usage in usage_data.values():
            all_keys.update(period_usage.keys())
        all_keys.update(all_time.keys())
        
        # Load budget config
        budget_data = load_budgets()
        
        if args.json:
            output = {}
            for key in sorted(all_keys):
                limit = get_limit_for_key(budget_data, key)
                total = all_time.get(key, {}).get("cost_cents", 0)
                output[key] = {
                    "1h": usage_data["1h"].get(key, {}).get("cost_cents", 0),
                    "24h": usage_data["24h"].get(key, {}).get("cost_cents", 0),
                    "7d": usage_data["7d"].get(key, {}).get("cost_cents", 0),
                    "total": total,
                    "limit": limit,
                    "remaining": limit - total,
                }
            print(json.dumps(output, indent=2))
        else:
            default_limit = budget_data.get("defaults", {}).get("limit_cents", DEFAULT_LIMIT)
            print(f"\n  Usage by SSH Key  │  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Default budget: {format_cost(default_limit)}\n")
            
            if not all_keys:
                print("  No usage data found.\n")
                return
            
            # Header
            print(f"  {'SSH Key':<20} │ {'24 Hours':>9} │ {'Total':>10} │ {'Limit':>10} │ {'Remaining':>10}")
            print(f"  {'-'*20}-┼-{'-'*9}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}")
            
            # Sort by total cost descending
            sorted_keys = sorted(all_keys, key=lambda k: all_time.get(k, {}).get("cost_cents", 0), reverse=True)
            
            totals = {"24h": 0, "total": 0}
            
            for key in sorted_keys:
                cost_24h = usage_data["24h"].get(key, {}).get("cost_cents", 0)
                cost_total = all_time.get(key, {}).get("cost_cents", 0)
                limit = get_limit_for_key(budget_data, key)
                remaining = limit - cost_total
                
                totals["24h"] += cost_24h
                totals["total"] += cost_total
                
                # Truncate long key names
                display_key = key[:20] if len(key) <= 20 else key[:17] + "..."
                
                # Show limit as "*" if using default
                key_config = budget_data.get("keys", {}).get(key, {})
                limit_val = key_config.get("limit_cents", "default")
                limit_str = format_cost(limit) if limit_val != "default" and limit_val is not None else f"{format_cost(limit)}*"
                
                # Remaining with status
                if remaining < 0:
                    remaining_str = f"{format_cost(remaining)} ⚠"
                elif remaining < limit * 0.2:
                    remaining_str = f"{format_cost(remaining)} !"
                else:
                    remaining_str = format_cost(remaining)
                
                print(f"  {display_key:<20} │ {format_cost(cost_24h):>9} │ {format_cost(cost_total):>10} │ {limit_str:>10} │ {remaining_str:>10}")
            
            # Totals row
            print(f"  {'-'*20}-┼-{'-'*9}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}")
            print(f"  {'TOTAL':<20} │ {format_cost(totals['24h']):>9} │ {format_cost(totals['total']):>10} │ {'-':>10} │ {'-':>10}")
            print(f"\n  * = using default limit, ! = <20% left, ⚠ = over budget")
            
            print()
            
            # Show hours breakdown for recent period
            if usage_data["24h"]:
                print("  Hours by instance (24h):")
                for key in sorted_keys:
                    data = usage_data["24h"].get(key, {})
                    instances = data.get("instances", {})
                    if instances:
                        print(f"    {key}:")
                        for inst_name, inst_data in sorted(instances.items(), key=lambda x: x[1]["hours"], reverse=True):
                            status = inst_data.get("status", "unknown")
                            status_icon = "●" if status == "active" else "○"
                            print(f"      {status_icon} {inst_name}: {format_duration(inst_data['hours'])}")
                print()
        
    finally:
        conn.close()


if __name__ == "__main__":
    main()
