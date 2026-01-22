#!/usr/bin/env python3
"""
Backup Lambda instances to local storage.
Run via cron every 30 minutes.
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import db

# Load config
def load_config():
    config_path = Path(__file__).parent / "config.env"
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
BACKUP_DIR = Path(CONFIG.get("BACKUP_DIR", "./backup")).expanduser()
BACKUP_EXCLUDE_PATTERNS = CONFIG.get("BACKUP_EXCLUDE_PATTERNS", ".*,wandb,*.pyc,__pycache__")
BACKUP_MAX_FILE_SIZE_MB = int(CONFIG.get("BACKUP_MAX_FILE_SIZE_MB", "100"))
SSH_USER = CONFIG.get("SSH_USER", "ubuntu")
SSH_KEY_PATH = Path(CONFIG.get("SSH_KEY_PATH", "~/.ssh/id_rsa")).expanduser()


def log(msg: str):
    """Print timestamped log message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


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
    
    log(f"Backing up {name} ({ip}) to {dest_dir}...")
    
    # Build rsync command
    rsync_cmd = [
        "rsync",
        "-avz",
        "--delete",
        "--timeout=300",
        f"--max-size={BACKUP_MAX_FILE_SIZE_MB}M",
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
    
    # SSH options for rsync
    ssh_opts = (
        f"-e 'ssh -i {SSH_KEY_PATH} "
        f"-o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=30'"
    )
    rsync_cmd.append(ssh_opts)
    
    # Source and destination
    rsync_cmd.append(f"{SSH_USER}@{ip}:~/")
    rsync_cmd.append(str(dest_dir) + "/")
    
    # rsync needs shell=True because of the ssh options quoting
    cmd_str = " ".join(rsync_cmd)
    
    try:
        result = subprocess.run(
            cmd_str,
            shell=True,
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
