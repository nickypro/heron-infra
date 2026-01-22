#!/usr/bin/env python3
"""
Check status of Lambda instances: GPU usage, idle time, time until termination.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import utils_db as db

PROJECT_DIR = Path(__file__).parent.parent


def load_config():
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
IDLE_SHUTDOWN_HOURS = float(CONFIG.get("IDLE_SHUTDOWN_HOURS", "2"))


def format_duration(hours: float) -> str:
    """Format duration in hours to human-readable string."""
    if hours is None or hours < 0:
        return "-"
    total_minutes = int(hours * 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}h{m:02d}m" if h > 0 else f"{m}m"


def format_timestamp(ts: float | None) -> str:
    """Format timestamp to readable string."""
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%b %d %H:%M")


def format_cost(cents: int) -> str:
    """Format cents to dollar string."""
    return f"${cents / 100:.2f}"


def group_samples_by_timestamp(samples: list[dict], tolerance: float = 30.0) -> list[dict]:
    """
    Group samples by timestamp (within tolerance seconds) and average across GPUs.
    Returns list of {timestamp, avg_utilization, all_zero, gpu_count}.
    """
    if not samples:
        return []
    
    # Sort by timestamp
    sorted_samples = sorted(samples, key=lambda s: s["timestamp"])
    
    grouped = []
    current_group = [sorted_samples[0]]
    
    for sample in sorted_samples[1:]:
        if sample["timestamp"] - current_group[0]["timestamp"] <= tolerance:
            current_group.append(sample)
        else:
            # Finalize current group
            utils = [s["utilization"] for s in current_group]
            grouped.append({
                "timestamp": current_group[0]["timestamp"],
                "avg_utilization": sum(utils) / len(utils),
                "all_zero": all(u == 0 for u in utils),
                "gpu_count": len(current_group),
            })
            current_group = [sample]
    
    # Don't forget last group
    if current_group:
        utils = [s["utilization"] for s in current_group]
        grouped.append({
            "timestamp": current_group[0]["timestamp"],
            "avg_utilization": sum(utils) / len(utils),
            "all_zero": all(u == 0 for u in utils),
            "gpu_count": len(current_group),
        })
    
    return grouped


def get_instance_stats(conn, instance: dict) -> dict:
    """Get GPU usage stats for an instance (handles multi-GPU)."""
    instance_id = instance["id"]
    gpu_count = instance.get("gpu_count", 1)
    
    # Get samples from last 24 hours
    cutoff_24h = time.time() - (24 * 3600)
    samples_24h = db.get_gpu_samples_since(conn, instance_id, cutoff_24h)
    
    # Get samples from idle window
    cutoff_idle = time.time() - (IDLE_SHUTDOWN_HOURS * 3600)
    samples_idle_window = db.get_gpu_samples_since(conn, instance_id, cutoff_idle)
    
    # Group samples by timestamp (averaging across GPUs)
    grouped_24h = group_samples_by_timestamp(samples_24h)
    grouped_idle = group_samples_by_timestamp(samples_idle_window)
    
    stats = {
        "samples_24h": len(grouped_24h),  # Count of time points, not individual GPU samples
        "samples_idle_window": len(grouped_idle),
        "gpu_count": gpu_count,
        "current_gpu": None,
        "avg_gpu_1h": None,
        "idle_duration_hours": None,
        "time_until_termination_hours": None,
        "will_terminate": False,
        "is_active": False,
    }
    
    if not grouped_24h:
        return stats
    
    # Sort by timestamp (most recent first)
    grouped_24h.sort(key=lambda s: s["timestamp"], reverse=True)
    
    # Current GPU (average of all GPUs at most recent timestamp)
    stats["current_gpu"] = grouped_24h[0]["avg_utilization"]
    stats["is_active"] = stats["current_gpu"] > 0
    
    # Average GPU last hour
    cutoff_1h = time.time() - 3600
    samples_1h = [s for s in grouped_24h if s["timestamp"] > cutoff_1h]
    if samples_1h:
        stats["avg_gpu_1h"] = sum(s["avg_utilization"] for s in samples_1h) / len(samples_1h)
    
    # Calculate idle duration (continuous ALL GPUs at 0% from now backwards)
    idle_start = None
    for sample in grouped_24h:
        if sample["all_zero"]:
            idle_start = sample["timestamp"]
        else:
            break
    
    if idle_start is not None:
        stats["idle_duration_hours"] = (time.time() - idle_start) / 3600
        time_until = IDLE_SHUTDOWN_HOURS - stats["idle_duration_hours"]
        stats["time_until_termination_hours"] = max(0, time_until)
        
        # Check if will be terminated (all time points in window have all GPUs at 0%)
        min_samples = int(IDLE_SHUTDOWN_HOURS * 60 * 0.8)
        if len(grouped_idle) >= min_samples:
            all_idle = all(s["all_zero"] for s in grouped_idle)
            stats["will_terminate"] = all_idle
    
    return stats


def get_cost_for_key(conn, ssh_key: str) -> int:
    """Get total cost in cents for an SSH key."""
    costs = db.get_all_costs(conn)
    for c in costs:
        if c["ssh_key"] == ssh_key:
            return c["total_cents"]
    return 0


def get_status_indicator(stats: dict) -> str:
    """Get status emoji and text."""
    if stats["will_terminate"]:
        return "🔴 TERMINATE"
    elif stats["idle_duration_hours"] is not None and stats["idle_duration_hours"] > IDLE_SHUTDOWN_HOURS * 0.5:
        return "🟡 IDLE-WARN"
    elif stats["current_gpu"] is not None and stats["current_gpu"] > 0:
        return "🟢 ACTIVE"
    elif stats["current_gpu"] == 0:
        return "🟡 IDLE"
    else:
        return "⚪ UNKNOWN"


def print_instance_status(instance: dict, stats: dict, cost_cents: int):
    """Print compact formatted status for an instance."""
    name = instance.get("hostname") or instance.get("name") or f"lambda-{instance['id'][:8]}"
    ip = instance.get("ip", "-")
    itype = instance.get("instance_type", "?")
    
    # SSH key
    ssh_keys = instance.get("ssh_key_names", [])
    if isinstance(ssh_keys, str):
        ssh_keys = json.loads(ssh_keys)
    ssh_key = ssh_keys[0] if ssh_keys else "-"
    
    # Times
    first_seen = format_timestamp(instance.get("first_seen"))
    last_seen = format_timestamp(instance.get("last_seen"))
    
    # GPU info
    gpu_count = stats.get("gpu_count", 1)
    gpu_label = f"GPU" if gpu_count == 1 else f"GPUs({gpu_count})"
    gpu_now = f"{stats['current_gpu']:.0f}%" if stats['current_gpu'] is not None else "-"
    gpu_1h = f"{stats['avg_gpu_1h']:.0f}%" if stats['avg_gpu_1h'] is not None else "-"
    
    # Idle info
    idle_str = format_duration(stats["idle_duration_hours"]) if stats["idle_duration_hours"] else "-"
    if stats["will_terminate"]:
        term_str = "NOW!"
    elif stats["time_until_termination_hours"] is not None and stats["idle_duration_hours"]:
        term_str = format_duration(stats["time_until_termination_hours"])
    else:
        term_str = "-"
    
    status = get_status_indicator(stats)
    cost = format_cost(cost_cents)
    
    W = 68  # inner width
    print(f"┌─ {name} {'─' * (W - len(name) - 2)}┐")
    line1 = f"  {status:<12}  IP: {ip:<15}  Type: {itype}"
    print(f"│{line1:<{W}}│")
    line2 = f"  Key: {ssh_key:<18}  Cost: {cost:<7}  Samples: {stats['samples_24h']} (24h)"
    print(f"│{line2:<{W}}│")
    line3 = f"  {gpu_label}: {gpu_now:<4} now  {gpu_1h:<4} 1h avg   Idle: {idle_str:<6}  Term: {term_str}"
    print(f"│{line3:<{W}}│")
    line4 = f"  First: {first_seen}   Last: {last_seen}"
    print(f"│{line4:<{W}}│")
    print(f"└{'─' * W}┘")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Check Lambda instance status")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    conn = db.get_db()
    
    try:
        active = db.get_active_instances(conn)
        
        if not active:
            if args.json:
                print(json.dumps({"instances": [], "message": "No active instances"}))
            else:
                print("No active instances found.")
            return
        
        # Build results with stats
        results = []
        for inst in active:
            stats = get_instance_stats(conn, inst)
            ssh_keys = inst.get("ssh_key_names", [])
            if isinstance(ssh_keys, str):
                ssh_keys = json.loads(ssh_keys)
            cost = get_cost_for_key(conn, ssh_keys[0]) if ssh_keys else 0
            results.append({
                "instance": inst,
                "stats": stats,
                "cost_cents": cost,
            })
        
        # Sort: active first (is_active=True), then by GPU usage descending
        results.sort(key=lambda r: (not r["stats"]["is_active"], -(r["stats"]["current_gpu"] or 0)))
        
        if args.json:
            output = []
            for r in results:
                inst = r["instance"]
                stats = r["stats"]
                ssh_keys = inst.get("ssh_key_names", [])
                if isinstance(ssh_keys, str):
                    ssh_keys = json.loads(ssh_keys)
                output.append({
                    "id": inst["id"],
                    "name": inst.get("hostname") or inst.get("name"),
                    "ip": inst.get("ip"),
                    "instance_type": inst.get("instance_type"),
                    "ssh_key": ssh_keys[0] if ssh_keys else None,
                    "first_seen": inst.get("first_seen"),
                    "last_seen": inst.get("last_seen"),
                    "cost_cents": r["cost_cents"],
                    "current_gpu_pct": stats["current_gpu"],
                    "avg_gpu_1h_pct": stats["avg_gpu_1h"],
                    "idle_hours": stats["idle_duration_hours"],
                    "hours_until_termination": stats["time_until_termination_hours"],
                    "will_terminate": stats["will_terminate"],
                })
            print(json.dumps(output, indent=2))
        else:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n  Lambda Instance Status  │  {now}  │  Idle threshold: {IDLE_SHUTDOWN_HOURS}h")
            print(f"  {len(active)} active instance(s)\n")
            
            for r in results:
                print_instance_status(r["instance"], r["stats"], r["cost_cents"])
                print()
            
    finally:
        conn.close()


if __name__ == "__main__":
    main()
