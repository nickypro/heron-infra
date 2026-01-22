#!/usr/bin/env python3
"""
Check and track Lambda instance type availability by region.
Run periodically to build up historical data on availability patterns.
"""

import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import utils_db as db
import utils_lambda_api as lambda_api

PROJECT_DIR = Path(__file__).parent.parent


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def fetch_and_record_availability(conn):
    """Fetch current availability and record to database."""
    types = lambda_api.list_instance_types()
    
    recorded = 0
    for type_name, data in types.items():
        regions = [r["name"] for r in data.get("regions_with_capacity_available", [])]
        if regions:
            db.record_availability(conn, type_name, regions)
            recorded += len(regions)
    
    return recorded


def get_current_availability():
    """Get current availability (live from API)."""
    types = lambda_api.list_instance_types()
    
    # Group by availability
    available = {}
    unavailable = []
    
    for type_name, data in types.items():
        info = data.get("instance_type", {})
        regions = data.get("regions_with_capacity_available", [])
        
        entry = {
            "name": type_name,
            "description": info.get("description", ""),
            "price_per_hour": info.get("price_cents_per_hour", 0) / 100,
            "gpus": info.get("specs", {}).get("gpus", 0),
        }
        
        if regions:
            entry["regions"] = [r["name"] for r in regions]
            available[type_name] = entry
        else:
            unavailable.append(entry)
    
    return available, unavailable


def analyze_history(conn, hours: int = 24):
    """Analyze availability history to find patterns."""
    history = db.get_availability_history(conn, hours)
    
    if not history:
        return {}
    
    # Count availability per (type, region) pair
    counts = defaultdict(lambda: defaultdict(int))
    total_checks = defaultdict(int)
    
    # Group by approximate time slots (every 10 minutes)
    time_slots = defaultdict(set)
    for record in history:
        slot = int(record["timestamp"] // 600)  # 10-minute slots
        time_slots[slot].add(record["timestamp"])
        counts[record["instance_type"]][record["region"]] += 1
    
    num_checks = len(time_slots)
    
    # Calculate availability percentage
    results = {}
    for itype, regions in counts.items():
        results[itype] = {
            region: {
                "count": count,
                "checks": num_checks,
                "pct": round(100 * count / num_checks, 1) if num_checks > 0 else 0
            }
            for region, count in regions.items()
        }
    
    return results, num_checks


def print_current_availability(available, unavailable):
    """Print current availability in a nice format."""
    print(f"\n  Current Availability  │  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {len(available)} types available, {len(unavailable)} unavailable\n")
    
    if available:
        # Sort by GPU count then name
        sorted_avail = sorted(available.values(), key=lambda x: (x["gpus"], x["name"]))
        
        print("  AVAILABLE:")
        for item in sorted_avail:
            regions_str = ", ".join(item["regions"])
            print(f"    {item['description']:<30} ${item['price_per_hour']:.2f}/hr  │  {regions_str}")
        print()
    
    if unavailable:
        print("  UNAVAILABLE:")
        sorted_unavail = sorted(unavailable, key=lambda x: (x["gpus"], x["name"]))
        names = [f"{x['description']}" for x in sorted_unavail]
        # Print in columns
        for i in range(0, len(names), 3):
            row = names[i:i+3]
            print(f"    {', '.join(row)}")
        print()


def print_history_analysis(analysis, num_checks, hours):
    """Print historical availability analysis."""
    if not analysis:
        print(f"  No availability history in the last {hours} hours.")
        print("  Run with --record to start collecting data.\n")
        return
    
    print(f"\n  Availability History  │  Last {hours} hours  │  {num_checks} checks\n")
    
    # Sort by most frequently available
    sorted_types = sorted(
        analysis.items(),
        key=lambda x: max(r["pct"] for r in x[1].values()),
        reverse=True
    )
    
    for itype, regions in sorted_types:
        sorted_regions = sorted(regions.items(), key=lambda x: x[1]["pct"], reverse=True)
        region_strs = [f"{r}: {data['pct']}%" for r, data in sorted_regions]
        print(f"  {itype:<28} │ {', '.join(region_strs)}")
    
    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Check Lambda instance availability")
    parser.add_argument("--record", action="store_true", help="Record current availability to database")
    parser.add_argument("--history", type=int, metavar="HOURS", help="Show availability history for N hours")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    conn = db.get_db()
    
    try:
        if args.record:
            log("Recording availability...")
            recorded = fetch_and_record_availability(conn)
            log(f"Recorded {recorded} availability entries")
            db.cleanup_old_availability(conn, older_than_hours=168)  # Keep 1 week
        
        if args.history:
            analysis, num_checks = analyze_history(conn, args.history)
            if args.json:
                print(json.dumps(analysis, indent=2))
            else:
                print_history_analysis(analysis, num_checks, args.history)
        elif not args.record or args.json:
            # Show current availability
            available, unavailable = get_current_availability()
            
            if args.json:
                print(json.dumps({"available": available, "unavailable": [u["name"] for u in unavailable]}, indent=2))
            else:
                print_current_availability(available, unavailable)
        
    finally:
        conn.close()


if __name__ == "__main__":
    main()
