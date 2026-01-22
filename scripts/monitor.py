#!/usr/bin/env python3
"""
Monitor Lambda instances: track state, GPU usage, costs, and manage SSH config.
Run via cron every minute.
"""

import json
import os
import subprocess
import sys
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
        log(f"Failed to get GPU stats from {ip}: {output}")
        return []
    
    try:
        return [int(line.strip()) for line in output.split("\n") if line.strip()]
    except ValueError:
        log(f"Failed to parse GPU stats from {ip}: {output}")
        return []


def initialize_machine(instance: dict) -> bool:
    """Run init script on a new machine. Returns True on success."""
    if not INIT_SCRIPT_PATH:
        log(f"No init script configured, skipping initialization for {instance['name']}")
        return True
    
    init_script = Path(INIT_SCRIPT_PATH)
    if not init_script.is_absolute():
        init_script = PROJECT_DIR / init_script
    
    if not init_script.exists():
        log(f"Init script not found: {init_script}")
        return False
    
    ip = instance["ip"]
    key_path = get_ssh_key_for_instance(instance)
    log(f"Initializing machine {instance['name']} ({ip}) with key {key_path.name}...")
    
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
    
    log(f"Successfully initialized {instance['name']}")
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
        
        # Get the right SSH key for this instance
        key_path = get_ssh_key_for_instance(inst)
        
        lambda_section += f"Host {host_name}\n"
        lambda_section += f"    HostName {inst['ip']}\n"
        lambda_section += f"    User {SSH_USER}\n"
        lambda_section += f'    IdentityFile "{key_path}"\n'
        lambda_section += f"    StrictHostKeyChecking no\n"
        lambda_section += f"    UserKnownHostsFile /dev/null\n"
        lambda_section += f"    # Instance ID: {inst['id']}\n"
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


def update_costs(conn, instances: list[dict]):
    """Update cost tracking for each SSH key."""
    # Cost per minute = hourly_cost / 60
    for inst in instances:
        if inst.get("status") != "active":
            continue
        
        hourly_cents = inst.get("hourly_cost_cents", 0)
        if not hourly_cents:
            continue
        
        cost_per_minute = hourly_cents / 60
        
        # Attribute cost to first SSH key
        ssh_keys = inst.get("ssh_key_names")
        if isinstance(ssh_keys, str):
            ssh_keys = json.loads(ssh_keys)
        
        if ssh_keys:
            db.update_cost(conn, ssh_keys[0], int(cost_per_minute))


def main():
    log("Starting monitor run...")
    
    conn = db.get_db()
    
    try:
        # 1. Fetch instances from API
        log("Fetching instances from API...")
        instances = lambda_api.list_instances()
        log(f"Found {len(instances)} instances")
        
        # 2. Update instance records in DB
        for inst in instances:
            db.upsert_instance(conn, inst)
        
        # 3. Check for new (uninitialized) instances
        uninitialized = db.get_uninitialized_instances(conn)
        for inst in uninitialized:
            if inst.get("ip"):
                log(f"New instance detected: {inst['name']} ({inst['ip']})")
                if initialize_machine(inst):
                    db.mark_initialized(conn, inst["id"])
        
        # 4. Get GPU utilization for active instances
        active = db.get_active_instances(conn)
        for inst in active:
            if not inst.get("ip"):
                continue
            
            gpu_utils = get_gpu_utilization(inst)
            for gpu_idx, util in enumerate(gpu_utils):
                db.add_gpu_sample(conn, inst["id"], util, gpu_idx)
            
            if gpu_utils:
                log(f"{inst['name']}: GPU utilization = {gpu_utils}")
        
        # 5. Update costs
        update_costs(conn, active)
        
        # 6. Update SSH config
        update_ssh_config(active)
        
        # 7. Export to JSON for inspection
        db.export_to_json(conn)
        
        # 8. Cleanup old samples (keep 24 hours)
        db.cleanup_old_samples(conn, older_than_hours=24)
        
        log("Monitor run complete")
        
    except Exception as e:
        log(f"Error during monitor run: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
