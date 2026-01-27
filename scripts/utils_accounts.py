#!/usr/bin/env python3
"""
Account configuration loader for multi-account Lambda Labs support.

Loads account credentials and budget settings from data/accounts.yaml.
Falls back to config.env LAMBDA_API_KEY for single-account setups.
"""

import os
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
ACCOUNTS_FILE = DATA_DIR / "accounts.yaml"


def _load_config_env():
    """Load configuration from config.env file."""
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


_CONFIG = _load_config_env()
DEFAULT_BUDGET_LIMIT = int(_CONFIG.get("BUDGET_LIMIT_DEFAULT", "500000"))
DEFAULT_MILESTONE_INTERVAL = int(_CONFIG.get("BUDGET_MILESTONE_INTERVAL", "100000"))


def load_accounts() -> dict:
    """
    Load accounts configuration.
    
    Returns dict with:
        - accounts: dict of {name: {api_key, limit_cents, discord_webhook}}
        - defaults: {limit_cents, milestone_interval}
    
    If accounts.yaml doesn't exist but LAMBDA_API_KEY is set in config.env,
    creates a single "default" account.
    """
    DATA_DIR.mkdir(exist_ok=True)
    
    if ACCOUNTS_FILE.exists():
        with open(ACCOUNTS_FILE) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    
    # Ensure defaults structure
    if "defaults" not in data:
        data["defaults"] = {}
    
    data["defaults"].setdefault("limit_cents", DEFAULT_BUDGET_LIMIT)
    data["defaults"].setdefault("milestone_interval", DEFAULT_MILESTONE_INTERVAL)
    
    # Ensure accounts structure
    if "accounts" not in data:
        data["accounts"] = {}
    
    # Fallback: if no accounts but LAMBDA_API_KEY exists, create default account
    if not data["accounts"]:
        api_key = _CONFIG.get("LAMBDA_API_KEY") or os.environ.get("LAMBDA_API_KEY")
        if api_key and api_key != "your_api_key_here":
            data["accounts"]["default"] = {
                "api_key": api_key,
                "limit_cents": "default",
                "discord_webhook": None,
            }
    
    return data


def save_accounts(data: dict):
    """Save accounts configuration to accounts.yaml."""
    DATA_DIR.mkdir(exist_ok=True)
    
    with open(ACCOUNTS_FILE, "w") as f:
        f.write("# Lambda Labs accounts configuration\n")
        f.write("# Each account has a name, API key, and optional budget settings\n")
        f.write("#\n")
        f.write("# limit_cents: budget limit in cents, or 'default' to use defaults.limit_cents\n")
        f.write("# discord_webhook: optional URL for spending notifications\n")
        f.write("\n")
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def get_account_list(data: dict) -> list[dict]:
    """
    Get list of accounts with resolved settings.
    
    Returns list of dicts with:
        - name: account name
        - api_key: API key
        - limit_cents: resolved budget limit (int)
        - discord_webhook: webhook URL or None
    """
    default_limit = data["defaults"]["limit_cents"]
    accounts = []
    
    for name, config in data.get("accounts", {}).items():
        api_key = config.get("api_key")
        if not api_key:
            continue
        
        limit = config.get("limit_cents", "default")
        if limit == "default" or limit is None:
            limit = default_limit
        
        accounts.append({
            "name": name,
            "api_key": api_key,
            "limit_cents": int(limit),
            "discord_webhook": config.get("discord_webhook"),
        })
    
    return accounts


def get_account_by_name(data: dict, name: str) -> dict | None:
    """Get a single account by name with resolved settings."""
    accounts = get_account_list(data)
    for acc in accounts:
        if acc["name"] == name:
            return acc
    return None


if __name__ == "__main__":
    # Test loading accounts
    data = load_accounts()
    accounts = get_account_list(data)
    
    print(f"Loaded {len(accounts)} account(s):")
    for acc in accounts:
        key_preview = acc["api_key"][:8] + "..." if len(acc["api_key"]) > 8 else acc["api_key"]
        print(f"  - {acc['name']}: key={key_preview}, limit=${acc['limit_cents']/100:.0f}")
