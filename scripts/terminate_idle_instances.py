#!/usr/bin/env python3
"""
Terminate Lambda instances that have been idle (0% GPU) for too long.
Run via cron separately from monitor.py for independent control.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import db
import lambda_api

PROJECT_DIR = Path(__file__).parent.parent


# Load config
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


def log(msg: str):
    """Print timestamped log message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def group_samples_by_timestamp(samples: list[dict], tolerance: float = 30.0) -> list[dict]:
    """
    Group samples by timestamp (within tolerance seconds) and check if all GPUs are idle.
    Returns list of {timestamp, all_zero, gpu_count}.
    """
    if not samples:
        return []
    
    sorted_samples = sorted(samples, key=lambda s: s["timestamp"])
    grouped = []
    current_group = [sorted_samples[0]]
    
    for sample in sorted_samples[1:]:
        if sample["timestamp"] - current_group[0]["timestamp"] <= tolerance:
            current_group.append(sample)
        else:
            utils = [s["utilization"] for s in current_group]
            grouped.append({
                "timestamp": current_group[0]["timestamp"],
                "all_zero": all(u == 0 for u in utils),
                "gpu_count": len(current_group),
            })
            current_group = [sample]
    
    if current_group:
        utils = [s["utilization"] for s in current_group]
        grouped.append({
            "timestamp": current_group[0]["timestamp"],
            "all_zero": all(u == 0 for u in utils),
            "gpu_count": len(current_group),
        })
    
    return grouped


def check_and_terminate_idle(conn, instance: dict, dry_run: bool = False) -> bool:
    """
    Check if instance should be terminated due to idle GPU.
    For multi-GPU instances, ALL GPUs must be at 0% to be considered idle.
    Returns True if instance was (or would be) terminated.
    """
    cutoff = time.time() - (IDLE_SHUTDOWN_HOURS * 3600)
    samples = db.get_gpu_samples_since(conn, instance["id"], cutoff)
    
    # Group samples by timestamp (handles multi-GPU)
    grouped = group_samples_by_timestamp(samples)
    
    # Need at least some time points to make a decision (80% coverage)
    min_samples = int(IDLE_SHUTDOWN_HOURS * 60 * 0.8)
    if len(grouped) < min_samples:
        log(f"  {instance['hostname'] or instance['id'][:8]}: Not enough samples ({len(grouped)}/{min_samples})")
        return False
    
    # Check if all time points have all GPUs at 0%
    all_idle = all(s["all_zero"] for s in grouped)
    
    if not all_idle:
        return False
    
    name = instance["hostname"] or instance["id"][:8]
    
    if dry_run:
        log(f"  {name}: WOULD TERMINATE (idle for {IDLE_SHUTDOWN_HOURS}+ hours)")
        return True
    
    log(f"  {name}: Terminating (idle for {IDLE_SHUTDOWN_HOURS}+ hours)...")
    try:
        terminated = lambda_api.terminate_instance([instance["id"]])
        if terminated:
            log(f"  {name}: Successfully terminated")
            return True
        else:
            log(f"  {name}: Failed to terminate")
    except Exception as e:
        log(f"  {name}: Error terminating: {e}")
    
    return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Terminate idle Lambda instances")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be terminated without actually terminating")
    args = parser.parse_args()
    
    if args.dry_run:
        log("DRY RUN - no instances will be terminated")
    
    log(f"Checking for idle instances (threshold: {IDLE_SHUTDOWN_HOURS} hours at 0% GPU)...")
    
    conn = db.get_db()
    
    try:
        active = db.get_active_instances(conn)
        log(f"Found {len(active)} active instances")
        
        terminated_count = 0
        for inst in active:
            if check_and_terminate_idle(conn, inst, dry_run=args.dry_run):
                terminated_count += 1
        
        if terminated_count > 0:
            action = "would terminate" if args.dry_run else "terminated"
            log(f"Done: {action} {terminated_count} instance(s)")
        else:
            log("Done: No idle instances to terminate")
            
    except Exception as e:
        log(f"Error: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
