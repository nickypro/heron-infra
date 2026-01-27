#!/usr/bin/env python3
"""
Show GPU availability patterns by region, day of week, and time block.
Analyzes historical data collected by monitor_availability.py.
"""

import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import utils_db as db

PROJECT_DIR = Path(__file__).parent.parent

# Day names
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# 4-hour time blocks
TIME_BLOCKS = [
    ("00-04", 0, 4),
    ("04-08", 4, 8),
    ("08-12", 8, 12),
    ("12-16", 12, 16),
    ("16-20", 16, 20),
    ("20-24", 20, 24),
]


def get_block_index(hour: int) -> int:
    """Get the time block index (0-5) for a given hour."""
    return hour // 4


def analyze_availability_patterns(conn, days: int = 7) -> dict:
    """
    Analyze availability data to find patterns by day/time.
    
    Returns:
        (results, total_checks, checks_per_slot) where results is:
        {
            region: {
                instance_type: {
                    (day_idx, block_idx): {
                        "available_count": int,
                        "total_checks": int,
                        "pct": float
                    }
                }
            }
        }
    """
    history = db.get_availability_history(conn, hours=days * 24)
    
    if not history:
        return {}, 0, {}
    
    # Track when we had data (to know total possible checks per slot)
    check_times = set()
    
    # Count availability per (region, type, day, block)
    # Structure: region -> type -> (day, block) -> count
    availability = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    
    for record in history:
        ts = record["timestamp"]
        dt = datetime.fromtimestamp(ts)
        day_idx = dt.weekday()  # 0=Monday
        block_idx = get_block_index(dt.hour)
        
        # Round to 10-minute slot for counting unique checks
        slot = int(ts // 600)
        check_times.add((slot, day_idx, block_idx))
        
        region = record["region"]
        itype = record["instance_type"]
        availability[region][itype][(day_idx, block_idx)] += 1
    
    # Count checks per (day, block)
    checks_per_slot = defaultdict(int)
    for _, day_idx, block_idx in check_times:
        checks_per_slot[(day_idx, block_idx)] += 1
    
    # Calculate percentages
    results = {}
    for region, types in availability.items():
        results[region] = {}
        for itype, slots in types.items():
            results[region][itype] = {}
            for (day_idx, block_idx), count in slots.items():
                total = checks_per_slot[(day_idx, block_idx)]
                pct = round(100 * count / total, 1) if total > 0 else 0
                results[region][itype][(day_idx, block_idx)] = {
                    "available_count": count,
                    "total_checks": total,
                    "pct": pct
                }
    
    return results, len(check_times), dict(checks_per_slot)


def pct_to_indicator(pct: float, has_data: bool = True) -> str:
    """Convert availability percentage to a visual indicator."""
    if not has_data:
        return "-"  # No data collected for this time slot
    if pct >= 80:
        return "█"  # Very high
    elif pct >= 60:
        return "▓"  # High
    elif pct >= 40:
        return "▒"  # Medium
    elif pct >= 20:
        return "░"  # Low
    elif pct > 0:
        return "·"  # Rare
    else:
        return "×"  # Checked but never available


def pct_to_color_indicator(pct: float, has_data: bool = True) -> str:
    """Convert availability percentage to colored indicator (with ANSI)."""
    if not has_data:
        return "\033[90m-\033[0m"  # Dark gray - No data collected
    if pct >= 80:
        return "\033[92m█\033[0m"  # Green - Very high
    elif pct >= 60:
        return "\033[93m▓\033[0m"  # Yellow - High
    elif pct >= 40:
        return "\033[33m▒\033[0m"  # Orange - Medium
    elif pct >= 20:
        return "\033[91m░\033[0m"  # Red - Low
    elif pct > 0:
        return "\033[90m·\033[0m"  # Gray - Rare
    else:
        return "\033[31m×\033[0m"  # Red × - Checked but never available


def print_region_table(region: str, types_data: dict, checks_per_slot: dict, use_color: bool = True):
    """Print availability table for a single region."""
    if not types_data:
        return
    
    indicator_fn = pct_to_color_indicator if use_color else pct_to_indicator
    
    # Header
    print(f"\n  ┌─ {region} {'─' * (71 - len(region))}┐")
    
    # Column headers: days across top (each day column is 7 chars including separator)
    header = "  │ GPU Type                │"
    for day in DAYS:
        header += f" {day}  │"  # 6 chars + separator = 7 total
    print(header)
    
    # Subheader: time blocks (6 chars + separator = 7 total per day)
    subheader = "  │                         │"
    for _ in DAYS:
        subheader += "012345│"
    print(subheader)
    
    print(f"  ├─────────────────────────┼" + "──────┼" * 7)
    
    # Sort GPU types by name
    sorted_types = sorted(types_data.keys())
    
    for itype in sorted_types:
        slots = types_data[itype]
        
        # Shorten type name if needed
        display_name = itype[:23] if len(itype) <= 23 else itype[:20] + "..."
        row = f"  │ {display_name:<23} │"
        
        for day_idx in range(7):
            day_cells = ""
            for block_idx in range(6):
                has_data = checks_per_slot.get((day_idx, block_idx), 0) > 0
                data = slots.get((day_idx, block_idx), {})
                pct = data.get("pct", 0)
                day_cells += indicator_fn(pct, has_data)
            row += f"{day_cells}│"
        
        print(row)
    
    print(f"  └─────────────────────────┴" + "──────┴" * 7)


def print_legend(use_color: bool = True):
    """Print the legend explaining the symbols."""
    print("\n  Legend: ", end="")
    if use_color:
        print("\033[92m█\033[0m ≥80%  ", end="")
        print("\033[93m▓\033[0m ≥60%  ", end="")
        print("\033[33m▒\033[0m ≥40%  ", end="")
        print("\033[91m░\033[0m ≥20%  ", end="")
        print("\033[90m·\033[0m >0%  ", end="")
        print("\033[31m×\033[0m never  ", end="")
        print("\033[90m-\033[0m no data", end="")
    else:
        print("█ ≥80%  ▓ ≥60%  ▒ ≥40%  ░ ≥20%  · >0%  × never  - no data", end="")
    print()
    print("  Time blocks: 0=00-04, 1=04-08, 2=08-12, 3=12-16, 4=16-20, 5=20-24 (UTC)\n")


def print_summary_by_gpu(data: dict, use_color: bool = True):
    """Print a summary showing best times for each GPU type across all regions."""
    # Aggregate across regions
    type_summary = defaultdict(lambda: defaultdict(list))
    
    for region, types in data.items():
        for itype, slots in types.items():
            for (day_idx, block_idx), info in slots.items():
                type_summary[itype][(day_idx, block_idx)].append({
                    "region": region,
                    "pct": info["pct"]
                })
    
    if not type_summary:
        return
    
    print("\n  ┌─ Best Times per GPU ─────────────────────────────────────────────┐")
    print("  │                                                                   │")
    
    for itype in sorted(type_summary.keys()):
        slots = type_summary[itype]
        
        # Find best slots (highest availability)
        best_slots = []
        for (day_idx, block_idx), regions_data in slots.items():
            max_pct = max(r["pct"] for r in regions_data)
            best_region = max(regions_data, key=lambda r: r["pct"])["region"]
            if max_pct > 0:
                best_slots.append({
                    "day": DAYS[day_idx],
                    "block": TIME_BLOCKS[block_idx][0],
                    "pct": max_pct,
                    "region": best_region
                })
        
        if not best_slots:
            continue
        
        # Sort by pct descending, take top 3
        best_slots.sort(key=lambda x: x["pct"], reverse=True)
        top_slots = best_slots[:3]
        
        display_name = itype[:25] if len(itype) <= 25 else itype[:22] + "..."
        slots_str = ", ".join(f"{s['day']} {s['block']} ({s['pct']:.0f}%)" for s in top_slots)
        
        print(f"  │ {display_name:<25} {slots_str:<39}│")
    
    print("  │                                                                   │")
    print("  └───────────────────────────────────────────────────────────────────┘")


def print_summary_by_time(data: dict, use_color: bool = True):
    """Print a summary showing best GPUs available at each time of day (aggregated across all days)."""
    # Aggregate: block_idx -> {gpu_type: [pct values across days]}
    time_summary = defaultdict(lambda: defaultdict(list))
    
    for region, types in data.items():
        for itype, slots in types.items():
            for (day_idx, block_idx), info in slots.items():
                if info["pct"] > 0:
                    time_summary[block_idx][itype].append(info["pct"])
    
    if not time_summary:
        return
    
    print("\n  ┌─ Best GPUs by Time of Day (UTC) ─────────────────────────────────┐")
    print("  │                                                                   │")
    
    for block_idx in range(6):
        gpu_data = time_summary.get(block_idx, {})
        if not gpu_data:
            time_label = TIME_BLOCKS[block_idx][0]
            print(f"  │ {time_label:<8} (no data)                                          │")
            continue
        
        # Calculate average pct for each GPU across all days
        gpu_avgs = {}
        for gpu, pcts in gpu_data.items():
            gpu_avgs[gpu] = sum(pcts) / len(pcts)
        
        # Sort by avg pct descending, take top 4
        sorted_gpus = sorted(gpu_avgs.items(), key=lambda x: x[1], reverse=True)[:4]
        
        time_label = TIME_BLOCKS[block_idx][0]
        gpus_str = ", ".join(f"{gpu.replace('gpu_', '')} ({pct:.0f}%)" for gpu, pct in sorted_gpus)
        
        # Truncate if too long
        if len(gpus_str) > 56:
            gpus_str = gpus_str[:53] + "..."
        
        print(f"  │ {time_label:<8} {gpus_str:<58}│")
    
    print("  │                                                                   │")
    print("  └───────────────────────────────────────────────────────────────────┘")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Show GPU availability patterns")
    parser.add_argument("--days", type=int, default=7, help="Days of history to analyze (default: 7)")
    parser.add_argument("--region", type=str, help="Show only this region")
    parser.add_argument("--gpu", type=str, help="Filter by GPU type (substring match)")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    parser.add_argument("--summary", action="store_true", help="Show only summary tables (no per-region grids)")
    parser.add_argument("--by-gpu", action="store_true", help="Show best times per GPU (default in summary)")
    parser.add_argument("--by-time", action="store_true", help="Show best GPUs per time slot")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    use_color = not args.no_color
    
    conn = db.get_db()
    
    try:
        data, total_checks, checks_per_slot = analyze_availability_patterns(conn, args.days)
        
        if not data:
            print(f"\n  No availability data found for the last {args.days} days.")
            print("  Run: python3 monitor_availability.py --record")
            print("  Or wait for cron to collect data.\n")
            return
        
        # Filter by region if specified
        if args.region:
            data = {k: v for k, v in data.items() if args.region.lower() in k.lower()}
        
        # Filter by GPU type if specified
        if args.gpu:
            for region in data:
                data[region] = {k: v for k, v in data[region].items() if args.gpu.lower() in k.lower()}
            # Remove empty regions
            data = {k: v for k, v in data.items() if v}
        
        if not data:
            print(f"\n  No matching data found for filters.")
            return
        
        if args.json:
            # Convert tuple keys to strings for JSON
            json_data = {}
            for region, types in data.items():
                json_data[region] = {}
                for itype, slots in types.items():
                    json_data[region][itype] = {
                        f"{DAYS[d]}_{TIME_BLOCKS[b][0]}": info
                        for (d, b), info in slots.items()
                    }
            print(json.dumps(json_data, indent=2))
            return
        
        # Print header
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n  GPU Availability Patterns  │  {now}")
        print(f"  Based on {total_checks} checks over {args.days} days")
        
        if args.summary:
            # Summary only mode
            if args.by_time:
                print_summary_by_time(data, use_color)
            if args.by_gpu or not args.by_time:
                print_summary_by_gpu(data, use_color)
        else:
            # Full output: tables + summaries
            for region in sorted(data.keys()):
                if data[region]:
                    print_region_table(region, data[region], checks_per_slot, use_color)
            
            print_legend(use_color)
            
            # Show both summaries by default, or specific ones if requested
            if args.by_time or not args.by_gpu:
                print_summary_by_time(data, use_color)
            if args.by_gpu or not args.by_time:
                print_summary_by_gpu(data, use_color)
        
    finally:
        conn.close()


if __name__ == "__main__":
    main()
