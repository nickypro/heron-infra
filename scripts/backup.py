#!/usr/bin/env python3
"""
Backup Lambda instances to local storage.
Run via cron every 30 minutes.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import db

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
BACKUP_DIR = Path(CONFIG.get("BACKUP_DIR", "./backup"))
if not BACKUP_DIR.is_absolute():
    BACKUP_DIR = PROJECT_DIR / BACKUP_DIR
BACKUP_DIR = BACKUP_DIR.expanduser()

BACKUP_EXCLUDE_PATTERNS = CONFIG.get("BACKUP_EXCLUDE_PATTERNS", ".*,wandb,*.pyc,__pycache__")
BACKUP_MAX_FILE_SIZE_MB = int(CONFIG.get("BACKUP_MAX_FILE_SIZE_MB", "100"))
SSH_USER = CONFIG.get("SSH_USER", "ubuntu")
SSH_KEYS_DIR = Path(CONFIG.get("SSH_KEYS_DIR", "./keys"))
if not SSH_KEYS_DIR.is_absolute():
    SSH_KEYS_DIR = PROJECT_DIR / SSH_KEYS_DIR
SSH_KEYS_DIR = SSH_KEYS_DIR.expanduser()

SSH_KEY_DEFAULT = Path(CONFIG.get("SSH_KEY_DEFAULT", "~/.ssh/id_rsa")).expanduser()


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


def backup_instance(instance: dict) -> bool:
    """
    Backup an instance's home directory using rsync.
    Returns True on success.
    """
    ip = instance.get("ip")
    name = instance.get("name") or instance.get("hostname") or f"lambda-{instance['id'][:8]}"
    # Sanitize name for filesystem
    name = name.replace(" ", "-").replace("/", "-").lower()
    
    if not ip:
        log(f"Skipping {name}: no IP address")
        return False
    
    dest_dir = BACKUP_DIR / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # Get the right SSH key for this instance
    key_path = get_ssh_key_for_instance(instance)
    
    log(f"Backing up {name} ({ip}) to {dest_dir} using key {key_path.name}...")
    
    # Build rsync command as a proper list (no shell=True needed)
    rsync_cmd = [
        "rsync",
        "-avz",
        "--delete",
        "--timeout=300",
        f"--max-size={BACKUP_MAX_FILE_SIZE_MB}M",
        # SSH options passed via -e (quote key path for spaces)
        "-e", f'ssh -F /dev/null -i "{key_path}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30',
    ]
    
    # Add exclude patterns
    # Default exclusions: hidden folders, wandb, large files handled by max-size
    exclusions = [
        ".*",           # Hidden files and folders
        "wandb/",       # W&B logs
        "*.pyc",        # Python bytecode
        "__pycache__/", # Python cache
        ".cache/",      # Generic cache
        ".local/",      # Local user data
        "venv/",        # Virtual environments
        ".venv/",
        "node_modules/",
        "*.log",        # Log files
    ]
    
    # Add custom exclusions from config
    if BACKUP_EXCLUDE_PATTERNS:
        for pattern in BACKUP_EXCLUDE_PATTERNS.split(","):
            pattern = pattern.strip()
            if pattern and pattern not in exclusions:
                exclusions.append(pattern)
    
    for excl in exclusions:
        rsync_cmd.extend(["--exclude", excl])
    
    # Source and destination
    rsync_cmd.append(f"{SSH_USER}@{ip}:~/")
    rsync_cmd.append(str(dest_dir) + "/")
    
    try:
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            timeout=1800  # 30 minute timeout
        )
        
        if result.returncode == 0:
            log(f"Successfully backed up {name}")
            return True
        elif result.returncode == 24:
            # rsync exit code 24: some files vanished during transfer (common, not an error)
            log(f"Backed up {name} (some files changed during transfer)")
            return True
        else:
            log(f"Backup failed for {name}: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        log(f"Backup timed out for {name}")
        return False
    except Exception as e:
        log(f"Backup error for {name}: {e}")
        return False


def main():
    log("Starting backup run...")
    
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = db.get_db()
    
    try:
        # Get active instances
        instances = db.get_active_instances(conn)
        log(f"Found {len(instances)} active instances to backup")
        
        success_count = 0
        fail_count = 0
        
        for inst in instances:
            if backup_instance(inst):
                success_count += 1
            else:
                fail_count += 1
        
        log(f"Backup complete: {success_count} succeeded, {fail_count} failed")
        
    except Exception as e:
        log(f"Error during backup run: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
