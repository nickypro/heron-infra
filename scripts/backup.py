#!/usr/bin/env python3
"""
Backup Lambda instances and volumes to local storage.
Run via cron every 30 minutes.

Backups are organized as:
    ./backup/instances/{instance-name}/     - home directories
    ./backup/volumes/{region}/{volume-name}/ - shared filesystems
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import utils_db as db
import utils_lambda_api as api

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

# Instance backups go to ./backup/instances/
INSTANCE_BACKUP_DIR = BACKUP_DIR / "instances"
# Volume backups go to ./backup/volumes/{region}/
VOLUME_BACKUP_DIR = BACKUP_DIR / "volumes"

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
    
    dest_dir = INSTANCE_BACKUP_DIR / name
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


def backup_volume(volume_name: str, mount_point: str, region: str, instance: dict) -> bool:
    """
    Backup a volume/filesystem via an instance that has it mounted.
    
    Args:
        volume_name: Name of the filesystem
        mount_point: Where it's mounted on the instance (e.g., /lambda/nfs/my-volume)
        region: Region of the filesystem (e.g., us-east-1)
        instance: Instance dict with ip, ssh_key_names, etc.
    
    Returns True on success.
    """
    ip = instance.get("ip")
    inst_name = instance.get("name") or instance.get("hostname") or f"lambda-{instance['id'][:8]}"
    
    if not ip:
        log(f"Skipping volume {volume_name}: instance {inst_name} has no IP")
        return False
    
    # Sanitize names for filesystem
    safe_region = region.replace(" ", "-").lower()
    safe_name = volume_name.replace(" ", "-").replace("/", "-").lower()
    
    dest_dir = VOLUME_BACKUP_DIR / safe_region / safe_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # Get the right SSH key for this instance
    key_path = get_ssh_key_for_instance(instance)
    
    log(f"Backing up volume {volume_name} ({region}) via {inst_name} to {dest_dir}...")
    
    # Build rsync command
    rsync_cmd = [
        "rsync",
        "-avz",
        "--delete",
        "--timeout=300",
        f"--max-size={BACKUP_MAX_FILE_SIZE_MB}M",
        "-e", f'ssh -F /dev/null -i "{key_path}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30',
    ]
    
    # Use same exclusions as instance backups
    exclusions = [
        ".*",
        "wandb/",
        "*.pyc",
        "__pycache__/",
        ".cache/",
        "venv/",
        ".venv/",
        "node_modules/",
        "*.log",
    ]
    
    if BACKUP_EXCLUDE_PATTERNS:
        for pattern in BACKUP_EXCLUDE_PATTERNS.split(","):
            pattern = pattern.strip()
            if pattern and pattern not in exclusions:
                exclusions.append(pattern)
    
    for excl in exclusions:
        rsync_cmd.extend(["--exclude", excl])
    
    # Ensure mount_point ends with / for rsync
    source_path = mount_point.rstrip("/") + "/"
    rsync_cmd.append(f"{SSH_USER}@{ip}:{source_path}")
    rsync_cmd.append(str(dest_dir) + "/")
    
    try:
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout for volumes (can be large)
        )
        
        if result.returncode == 0:
            log(f"Successfully backed up volume {volume_name}")
            return True
        elif result.returncode == 24:
            log(f"Backed up volume {volume_name} (some files changed during transfer)")
            return True
        else:
            log(f"Backup failed for volume {volume_name}: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        log(f"Backup timed out for volume {volume_name}")
        return False
    except Exception as e:
        log(f"Backup error for volume {volume_name}: {e}")
        return False


def main():
    log("Starting backup run...")
    
    INSTANCE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    VOLUME_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = db.get_db()
    
    try:
        # Get active instances from API (fresher data with filesystem mounts)
        instances = api.list_instances()
        log(f"Found {len(instances)} active instances")
        
        instance_success = 0
        instance_fail = 0
        
        # Track which volumes we've already backed up (by filesystem id)
        # Since volumes are shared, we only need to backup once
        backed_up_volumes = set()
        volume_success = 0
        volume_fail = 0
        
        for inst in instances:
            # Backup instance home directory
            if backup_instance(inst):
                instance_success += 1
            else:
                instance_fail += 1
            
            # Backup any mounted volumes via this instance
            file_system_mounts = inst.get("file_system_mounts", [])
            file_system_names = inst.get("file_system_names", [])
            region = inst.get("region", {})
            region_name = region.get("name", "unknown") if isinstance(region, dict) else str(region)
            
            for i, mount in enumerate(file_system_mounts):
                fs_id = mount.get("file_system_id")
                mount_point = mount.get("mount_point")
                
                if not fs_id or not mount_point:
                    continue
                
                # Skip if we've already backed up this volume
                if fs_id in backed_up_volumes:
                    continue
                
                # Get volume name (from file_system_names if available, or extract from mount_point)
                if i < len(file_system_names):
                    volume_name = file_system_names[i]
                else:
                    # Extract name from mount point (e.g., /lambda/nfs/my-volume -> my-volume)
                    volume_name = mount_point.rstrip("/").split("/")[-1]
                
                if backup_volume(volume_name, mount_point, region_name, inst):
                    volume_success += 1
                else:
                    volume_fail += 1
                
                backed_up_volumes.add(fs_id)
        
        log(f"Backup complete:")
        log(f"  Instances: {instance_success} succeeded, {instance_fail} failed")
        log(f"  Volumes: {volume_success} succeeded, {volume_fail} failed")
        
    except Exception as e:
        log(f"Error during backup run: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
