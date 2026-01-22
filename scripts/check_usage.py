#!/usr/bin/env python3
"""
Check usage/cost per SSH key over different time periods.
"""

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import db

PROJECT_DIR = Path(__file__).parent.parent


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
    
    # Get instance info (hourly cost, ssh keys)
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
        }
    
    # Group samples by instance and count time slots (each sample = ~1 minute)
    instance_minutes = defaultdict(int)
    for sample in samples:
        instance_minutes[sample["instance_id"]] += 1
    
    # Calculate cost per SSH key
    usage_by_key = defaultdict(lambda: {"cost_cents": 0, "hours": 0, "instances": set()})
    
    for instance_id, minutes in instance_minutes.items():
        if instance_id not in instances:
            continue
        
        inst = instances[instance_id]
        ssh_key = inst["ssh_key"]
        hours = minutes / 60
        cost_cents = (inst["hourly_cents"] * minutes) / 60
        
        usage_by_key[ssh_key]["cost_cents"] += cost_cents
        usage_by_key[ssh_key]["hours"] += hours
        usage_by_key[ssh_key]["instances"].add(inst["name"])
    
    # Convert sets to lists for JSON serialization
    for key in usage_by_key:
        usage_by_key[key]["instances"] = list(usage_by_key[key]["instances"])
    
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
        
        if args.json:
            output = {}
            for key in sorted(all_keys):
                output[key] = {
                    "1h": usage_data["1h"].get(key, {}).get("cost_cents", 0),
                    "24h": usage_data["24h"].get(key, {}).get("cost_cents", 0),
                    "7d": usage_data["7d"].get(key, {}).get("cost_cents", 0),
                    "total": all_time.get(key, {}).get("cost_cents", 0),
                }
            print(json.dumps(output, indent=2))
        else:
            print(f"\n  Usage by SSH Key  │  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            if not all_keys:
                print("  No usage data found.\n")
                return
            
            # Header
            print(f"  {'SSH Key':<24} │ {'1 Hour':>10} │ {'24 Hours':>10} │ {'7 Days':>10} │ {'Total':>10}")
            print(f"  {'-'*24}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}")
            
            # Sort by total cost descending
            sorted_keys = sorted(all_keys, key=lambda k: all_time.get(k, {}).get("cost_cents", 0), reverse=True)
            
            totals = {"1h": 0, "24h": 0, "7d": 0, "total": 0}
            
            for key in sorted_keys:
                cost_1h = usage_data["1h"].get(key, {}).get("cost_cents", 0)
                cost_24h = usage_data["24h"].get(key, {}).get("cost_cents", 0)
                cost_7d = usage_data["7d"].get(key, {}).get("cost_cents", 0)
                cost_total = all_time.get(key, {}).get("cost_cents", 0)
                
                totals["1h"] += cost_1h
                totals["24h"] += cost_24h
                totals["7d"] += cost_7d
                totals["total"] += cost_total
                
                # Truncate long key names
                display_key = key[:24] if len(key) <= 24 else key[:21] + "..."
                
                print(f"  {display_key:<24} │ {format_cost(cost_1h):>10} │ {format_cost(cost_24h):>10} │ {format_cost(cost_7d):>10} │ {format_cost(cost_total):>10}")
            
            # Totals row
            print(f"  {'-'*24}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}")
            print(f"  {'TOTAL':<24} │ {format_cost(totals['1h']):>10} │ {format_cost(totals['24h']):>10} │ {format_cost(totals['7d']):>10} │ {format_cost(totals['total']):>10}")
            
            print()
            
            # Show hours breakdown for recent period
            if usage_data["24h"]:
                print("  Hours by instance (24h):")
                for key in sorted_keys:
                    data = usage_data["24h"].get(key, {})
                    if data.get("hours", 0) > 0:
                        instances = ", ".join(data.get("instances", []))
                        print(f"    {key}: {format_duration(data['hours'])} ({instances})")
                print()
        
    finally:
        conn.close()


if __name__ == "__main__":
    main()
