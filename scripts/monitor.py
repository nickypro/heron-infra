#!/usr/bin/env python3
"""
Monitor Lambda instances: track state, GPU usage, costs, and manage SSH config.
Supports multiple Lambda Labs accounts.
Run via cron every minute.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import utils_accounts
import utils_db as db
import utils_lambda_api as lambda_api

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
SSH_CONFIG_PATH = Path(CONFIG.get("SSH_CONFIG_PATH", "~/.ssh/config")).expanduser()
SSH_USER = CONFIG.get("SSH_USER", "ubuntu")
SSH_KEYS_DIR = Path(CONFIG.get("SSH_KEYS_DIR", "./keys"))
if not SSH_KEYS_DIR.is_absolute():
    SSH_KEYS_DIR = PROJECT_DIR / SSH_KEYS_DIR
SSH_KEYS_DIR = SSH_KEYS_DIR.expanduser()

SSH_KEY_DEFAULT = Path(CONFIG.get("SSH_KEY_DEFAULT", "~/.ssh/id_rsa")).expanduser()
INIT_SCRIPT_PATH = CONFIG.get("INIT_SCRIPT_PATH", "")


def log(msg: str):
    """Print timestamped log message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def get_ssh_key_for_instance(instance: dict) -> Path:
    """
    Find the appropriate SSH key for an instance.
    Looks in SSH_KEYS_DIR for a key matching one of the instance's ssh_key_names.
    
    Supports two structures:
        ./keys/chen-sabotage              (direct file)
        ./keys/chen-sabotage/chen-sabotage.pem  (subfolder with .pem)
    
    Falls back to SSH_KEY_DEFAULT if not found.
    """
    ssh_key_names = instance.get("ssh_key_names", [])
    if isinstance(ssh_key_names, str):
        ssh_key_names = json.loads(ssh_key_names)
    
    # Try to find a matching key in the keys directory
    if SSH_KEYS_DIR.exists():
        for key_name in ssh_key_names:
            # Structure 1: Direct file (./keys/chen-sabotage)
            key_path = SSH_KEYS_DIR / key_name
            if key_path.is_file():
                return key_path
            
            # Structure 1 with extensions
            for ext in [".pem", ".key"]:
                key_path = SSH_KEYS_DIR / f"{key_name}{ext}"
                if key_path.is_file():
                    return key_path
            
            # Structure 2: Subfolder (./keys/chen-sabotage/chen-sabotage.pem)
            key_dir = SSH_KEYS_DIR / key_name
            if key_dir.is_dir():
                for ext in [".pem", ".key", ""]:
                    key_path = key_dir / f"{key_name}{ext}"
                    if key_path.is_file():
                        return key_path
                # Also check for any .pem file in the subfolder
                pem_files = list(key_dir.glob("*.pem"))
                if pem_files:
                    return pem_files[0]
    
    return SSH_KEY_DEFAULT


def ssh_command(ip: str, command: str, key_path: Path, timeout: int = 30) -> tuple[int, str]:
    """
    Run a command on a remote machine via SSH.
    Returns (exit_code, output).
    """
    ssh_opts = [
        "-F", "/dev/null",  # Ignore SSH config to avoid path issues
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-i", str(key_path),
    ]
    
    cmd = ["ssh"] + ssh_opts + [f"{SSH_USER}@{ip}", command]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


def get_gpu_utilization(instance: dict) -> list[int]:
    """
    Get GPU utilization percentages from a machine.
    Returns list of utilization values (one per GPU), or empty list on failure.
    """
    ip = instance.get("ip")
    if not ip:
        return []
    
    key_path = get_ssh_key_for_instance(instance)
    cmd = "nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits"
    exit_code, output = ssh_command(ip, cmd, key_path)
    
    if exit_code != 0:
        log(f"  Failed to get GPU stats from {ip}: {output}")
        return []
    
    try:
        return [int(line.strip()) for line in output.split("\n") if line.strip()]
    except ValueError:
        log(f"  Failed to parse GPU stats from {ip}: {output}")
        return []


def get_storage_usage(instance: dict) -> list[dict]:
    """
    Get disk storage usage from a machine.
    Returns list of dicts with {mount_point, total_gb, used_gb, available_gb, use_percent}.
    """
    ip = instance.get("ip")
    if not ip:
        return []
    
    key_path = get_ssh_key_for_instance(instance)
    # Get disk usage for relevant mount points (exclude tmpfs, devtmpfs, etc.)
    cmd = "df -BG --output=target,size,used,avail,pcent 2>/dev/null | grep -E '^(/|/home|/lambda)' | head -10"
    exit_code, output = ssh_command(ip, cmd, key_path)
    
    if exit_code != 0:
        return []
    
    results = []
    try:
        for line in output.split("\n"):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 5:
                mount_point = parts[0]
                # Parse sizes (remove 'G' suffix)
                total_gb = float(parts[1].rstrip('G'))
                used_gb = float(parts[2].rstrip('G'))
                available_gb = float(parts[3].rstrip('G'))
                use_percent = int(parts[4].rstrip('%'))
                results.append({
                    "mount_point": mount_point,
                    "total_gb": total_gb,
                    "used_gb": used_gb,
                    "available_gb": available_gb,
                    "use_percent": use_percent,
                })
    except (ValueError, IndexError) as e:
        log(f"  Failed to parse storage stats from {ip}: {e}")
    
    return results


def initialize_machine(instance: dict) -> bool:
    """Run init script on a new machine. Returns True on success."""
    if not INIT_SCRIPT_PATH:
        log(f"No init script configured, skipping initialization for {instance.get('name')}")
        return True
    
    init_script = Path(INIT_SCRIPT_PATH)
    if not init_script.is_absolute():
        init_script = PROJECT_DIR / init_script
    
    if not init_script.exists():
        log(f"Init script not found: {init_script}")
        return False
    
    ip = instance["ip"]
    key_path = get_ssh_key_for_instance(instance)
    log(f"Initializing machine {instance.get('name')} ({ip}) with key {key_path.name}...")
    
    # Copy init script to remote
    scp_opts = [
        "-F", "/dev/null",  # Ignore SSH config to avoid path issues
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-i", str(key_path),
    ]
    
    scp_cmd = ["scp"] + scp_opts + [str(init_script), f"{SSH_USER}@{ip}:/tmp/init_machine.sh"]
    
    try:
        result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            log(f"Failed to copy init script: {result.stderr}")
            return False
    except Exception as e:
        log(f"Failed to copy init script: {e}")
        return False
    
    # Run init script
    exit_code, output = ssh_command(ip, "chmod +x /tmp/init_machine.sh && /tmp/init_machine.sh", key_path, timeout=300)
    
    if exit_code != 0:
        log(f"Init script failed on {ip}: {output}")
        return False
    
    log(f"Successfully initialized {instance.get('name')}")
    return True


def update_ssh_config(instances: list[dict]):
    """Update SSH config with current Lambda instances."""
    # Read existing config
    existing_content = ""
    if SSH_CONFIG_PATH.exists():
        existing_content = SSH_CONFIG_PATH.read_text()
    
    # Find and remove existing Lambda-managed section
    marker_start = "# BEGIN LAMBDA-MANAGED"
    marker_end = "# END LAMBDA-MANAGED"
    
    if marker_start in existing_content:
        before = existing_content.split(marker_start)[0].rstrip()
        after_parts = existing_content.split(marker_end)
        after = after_parts[1].lstrip() if len(after_parts) > 1 else ""
        existing_content = before + ("\n\n" if before and after else "\n" if before else "") + after
    
    # Generate new Lambda section
    lambda_section = f"{marker_start}\n"
    lambda_section += "# Auto-generated by heron-infra monitor.py\n"
    lambda_section += f"# Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    
    for inst in instances:
        if inst.get("status") != "active" or not inst.get("ip"):
            continue
        
        host_name = inst.get("name") or inst.get("hostname") or f"lambda-{inst['id'][:8]}"
        # Sanitize hostname for SSH config
        host_name = host_name.replace(" ", "-").lower()
        
        # Prepend account name if not "default" to avoid name collisions
        account = inst.get("account")
        if account and account != "default":
            host_name = f"{account}-{host_name}"
        
        # Get the right SSH key for this instance
        key_path = get_ssh_key_for_instance(inst)
        
        lambda_section += f"Host {host_name}\n"
        lambda_section += f"    HostName {inst['ip']}\n"
        lambda_section += f"    User {SSH_USER}\n"
        lambda_section += f'    IdentityFile "{key_path}"\n'
        lambda_section += f"    StrictHostKeyChecking no\n"
        lambda_section += f"    UserKnownHostsFile /dev/null\n"
        lambda_section += f"    # Instance ID: {inst['id']}\n"
        lambda_section += f"    # Account: {account or 'default'}\n"
        lambda_section += f"    # Type: {inst.get('instance_type', 'unknown')}\n"
        lambda_section += "\n"
    
    lambda_section += marker_end
    
    # Write updated config
    SSH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    if existing_content.strip():
        new_content = existing_content.rstrip() + "\n\n" + lambda_section
    else:
        new_content = lambda_section
    
    SSH_CONFIG_PATH.write_text(new_content)
    SSH_CONFIG_PATH.chmod(0o600)
    log(f"Updated SSH config with {len([i for i in instances if i.get('status') == 'active'])} instances")


def update_costs(conn, instances: list[dict], account: str):
    """Update cost tracking for the account."""
    # Cost per minute = hourly_cost / 60
    total_cost_per_minute = 0
    
    for inst in instances:
        if inst.get("status") != "active":
            continue
        
        hourly_cents = inst.get("hourly_cost_cents", 0)
        if not hourly_cents:
            continue
        
        cost_per_minute = hourly_cents / 60
        total_cost_per_minute += cost_per_minute
        
        # Also track per-SSH-key for backward compatibility
        ssh_keys = inst.get("ssh_key_names")
        if isinstance(ssh_keys, str):
            ssh_keys = json.loads(ssh_keys)
        
        if ssh_keys:
            db.update_cost(conn, ssh_keys[0], int(cost_per_minute))
    
    # Update account cost
    if total_cost_per_minute > 0:
        db.update_account_cost(conn, account, int(total_cost_per_minute))


def process_account(conn, account: dict) -> list[dict]:
    """Process a single account and return its instances."""
    account_name = account["name"]
    api_key = account["api_key"]
    
    log(f"Processing account: {account_name}")
    
    # Fetch instances from API
    instances = lambda_api.list_instances(api_key)
    log(f"  Found {len(instances)} instances")
    
    # Update instance records in DB (with account name)
    # This captures hourly_cost_cents from the API response
    for inst in instances:
        db.upsert_instance(conn, inst, account=account_name)
    
    # Get active instances from DB (now have hourly_cost_cents)
    active = db.get_active_instances(conn, account=account_name)
    
    # ALWAYS update costs for active instances (regardless of SSH success)
    # This ensures costs are tracked even if we can't connect to the machines
    update_costs(conn, active, account_name)
    
    # Check for new (uninitialized) instances
    uninitialized = db.get_uninitialized_instances(conn, account=account_name)
    for inst in uninitialized:
        if inst.get("ip"):
            log(f"  New instance detected: {inst.get('name')} ({inst['ip']})")
            if initialize_machine(inst):
                db.mark_initialized(conn, inst["id"])
    
    # Get GPU and storage stats for active instances (may fail if SSH unavailable)
    for inst in active:
        if not inst.get("ip"):
            continue
        
        name = inst.get("name") or inst.get("hostname") or inst["id"][:8]
        
        # GPU utilization
        gpu_utils = get_gpu_utilization(inst)
        for gpu_idx, util in enumerate(gpu_utils):
            db.add_gpu_sample(conn, inst["id"], util, gpu_idx)
        
        # Storage utilization
        storage_stats = get_storage_usage(inst)
        for storage in storage_stats:
            db.add_storage_sample(
                conn, inst["id"], 
                storage["mount_point"],
                storage["total_gb"],
                storage["used_gb"],
                storage["available_gb"],
                storage["use_percent"]
            )
        
        # Log summary
        if gpu_utils or storage_stats:
            parts = []
            if gpu_utils:
                parts.append(f"GPU={gpu_utils}")
            if storage_stats:
                root_storage = next((s for s in storage_stats if s["mount_point"] == "/"), None)
                if root_storage:
                    parts.append(f"Disk={root_storage['use_percent']}%")
            log(f"  {name}: {', '.join(parts)}")
    
    return active


def main():
    log("Starting monitor run...")
    
    # Load accounts
    accounts_data = utils_accounts.load_accounts()
    accounts = utils_accounts.get_account_list(accounts_data)
    
    if not accounts:
        log("No accounts configured. Add accounts to data/accounts.yaml or set LAMBDA_API_KEY in config.env")
        return
    
    conn = db.get_db()
    
    try:
        all_active_instances = []
        
        # Process each account
        for account in accounts:
            try:
                active = process_account(conn, account)
                # Add account info to instances for SSH config
                for inst in active:
                    inst["account"] = account["name"]
                all_active_instances.extend(active)
            except Exception as e:
                log(f"Error processing account {account['name']}: {e}")
        
        # Update SSH config with instances from all accounts
        update_ssh_config(all_active_instances)
        
        # Export to JSON for inspection
        db.export_to_json(conn)
        
        # Cleanup old samples (keep 24 hours)
        db.cleanup_old_samples(conn, older_than_hours=24)
        
        log(f"Monitor run complete ({len(accounts)} accounts, {len(all_active_instances)} instances)")
        
    except Exception as e:
        log(f"Error during monitor run: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
