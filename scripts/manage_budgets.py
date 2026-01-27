#!/usr/bin/env python3
"""
CLI tool to manage per-SSH-key budget limits and Discord webhooks.

Usage:
    python3 manage_budgets.py list              # Show all keys and budgets
    python3 manage_budgets.py set KEY           # Add/edit a key (interactive)
    python3 manage_budgets.py set KEY --limit 10000 --webhook URL
    python3 manage_budgets.py reset KEY         # Remove custom settings for key
"""

import argparse
import sys
from pathlib import Path

import yaml

import utils_db as db

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
BUDGETS_FILE = DATA_DIR / "budgets.yaml"


def load_config():
    """Load config.env for defaults."""
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
DEFAULT_LIMIT = int(CONFIG.get("BUDGET_LIMIT_DEFAULT", "500000"))
MILESTONE_INTERVAL = int(CONFIG.get("BUDGET_MILESTONE_INTERVAL", "100000"))


def load_budgets() -> dict:
    """Load budgets.yaml, creating default if it doesn't exist."""
    DATA_DIR.mkdir(exist_ok=True)
    
    if BUDGETS_FILE.exists():
        with open(BUDGETS_FILE) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    
    # Ensure structure
    if "defaults" not in data:
        data["defaults"] = {
            "limit_cents": DEFAULT_LIMIT,
            "milestone_interval": MILESTONE_INTERVAL,
        }
    if "keys" not in data:
        data["keys"] = {}
    
    return data


def save_budgets(data: dict):
    """Save budgets.yaml with helpful comments."""
    DATA_DIR.mkdir(exist_ok=True)
    
    # Write with a header comment
    with open(BUDGETS_FILE, "w") as f:
        f.write("# Budget configuration per SSH key\n")
        f.write("# Edit this file directly or use: python3 scripts/manage_budgets.py\n")
        f.write("#\n")
        f.write("# For each key under 'keys:', you can set:\n")
        f.write("#   limit_cents: <number> or 'default' to use the default limit\n")
        f.write("#   discord_webhook: <url> for notifications (optional)\n")
        f.write("#\n")
        f.write(f"# Current default limit: ${data['defaults']['limit_cents'] / 100:.0f}\n")
        f.write(f"# Milestone interval: ${data['defaults']['milestone_interval'] / 100:.0f}\n")
        f.write("\n")
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def get_limit_for_key(data: dict, ssh_key: str) -> int:
    """Get the effective limit for an SSH key (custom or default)."""
    key_config = data.get("keys", {}).get(ssh_key, {})
    limit = key_config.get("limit_cents", "default")
    if limit == "default" or limit is None:
        return data["defaults"]["limit_cents"]
    return int(limit)


def format_money(cents: int | float) -> str:
    """Format cents as dollar string."""
    return f"${cents / 100:,.0f}"


def cmd_list(args):
    """List all SSH keys with their budget status."""
    data = load_budgets()
    conn = db.get_db()
    
    try:
        # Get all costs from DB
        costs = {c["ssh_key"]: c["total_cents"] for c in db.get_all_costs(conn)}
        
        # Collect all keys (from config + from costs)
        all_keys = set(data.get("keys", {}).keys()) | set(costs.keys())
        
        if not all_keys:
            print("\nNo SSH keys found. Keys will appear here once they have spending.\n")
            print(f"Default budget limit: {format_money(data['defaults']['limit_cents'])}")
            return
        
        print(f"\n  Budget Status  │  Default limit: {format_money(data['defaults']['limit_cents'])}\n")
        
        # Header
        print(f"  {'SSH Key':<20} │ {'Limit':>10} │ {'Spent':>10} │ {'Remaining':>10} │ Discord")
        print(f"  {'-'*20}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*8}")
        
        # Sort by spent descending
        sorted_keys = sorted(all_keys, key=lambda k: costs.get(k, 0), reverse=True)
        
        for ssh_key in sorted_keys:
            key_config = data.get("keys", {}).get(ssh_key, {})
            limit = get_limit_for_key(data, ssh_key)
            spent = costs.get(ssh_key, 0)
            remaining = limit - spent
            
            # Check if using custom or default limit
            limit_display = key_config.get("limit_cents", "default")
            if limit_display == "default" or limit_display is None:
                limit_str = f"{format_money(limit)} *"
            else:
                limit_str = format_money(limit)
            
            # Remaining with warning
            if remaining < 0:
                remaining_str = f"{format_money(remaining)} ⚠"
            elif remaining < limit * 0.2:
                remaining_str = f"{format_money(remaining)} !"
            else:
                remaining_str = format_money(remaining)
            
            # Discord configured?
            discord = "✓" if key_config.get("discord_webhook") else "-"
            
            # Truncate long key names
            display_key = ssh_key[:20] if len(ssh_key) <= 20 else ssh_key[:17] + "..."
            
            print(f"  {display_key:<20} │ {limit_str:>10} │ {format_money(spent):>10} │ {remaining_str:>10} │ {discord:^8}")
        
        print(f"\n  * = using default limit")
        print(f"  ! = <20% remaining, ⚠ = over budget\n")
        
    finally:
        conn.close()


def cmd_set(args):
    """Set budget configuration for an SSH key."""
    data = load_budgets()
    ssh_key = args.key
    
    # Initialize key config if not exists
    if ssh_key not in data["keys"]:
        data["keys"][ssh_key] = {
            "limit_cents": "default",
            "discord_webhook": None,
        }
    
    key_config = data["keys"][ssh_key]
    
    # Interactive mode if no options provided
    if args.limit is None and args.webhook is None:
        print(f"\nConfiguring budget for: {ssh_key}")
        print(f"Current settings:")
        print(f"  limit_cents: {key_config.get('limit_cents', 'default')}")
        print(f"  discord_webhook: {key_config.get('discord_webhook') or '(not set)'}")
        print()
        
        # Get limit
        current_limit = key_config.get("limit_cents", "default")
        limit_input = input(f"Budget limit in dollars (or 'default') [{current_limit if current_limit == 'default' else format_money(current_limit)}]: ").strip()
        if limit_input:
            if limit_input.lower() == "default":
                key_config["limit_cents"] = "default"
            else:
                try:
                    # Parse as dollars, convert to cents
                    limit_input = limit_input.replace("$", "").replace(",", "")
                    key_config["limit_cents"] = int(float(limit_input) * 100)
                except ValueError:
                    print(f"Invalid limit: {limit_input}")
                    return 1
        
        # Get webhook
        current_webhook = key_config.get("discord_webhook") or ""
        webhook_input = input(f"Discord webhook URL (or 'none' to clear) [{current_webhook[:40] + '...' if len(current_webhook) > 40 else current_webhook or '(none)'}]: ").strip()
        if webhook_input:
            if webhook_input.lower() == "none":
                key_config["discord_webhook"] = None
            else:
                key_config["discord_webhook"] = webhook_input
    
    else:
        # Non-interactive mode
        if args.limit is not None:
            if args.limit.lower() == "default":
                key_config["limit_cents"] = "default"
            else:
                try:
                    limit = args.limit.replace("$", "").replace(",", "")
                    key_config["limit_cents"] = int(float(limit) * 100)
                except ValueError:
                    print(f"Invalid limit: {args.limit}")
                    return 1
        
        if args.webhook is not None:
            if args.webhook.lower() == "none":
                key_config["discord_webhook"] = None
            else:
                key_config["discord_webhook"] = args.webhook
    
    save_budgets(data)
    print(f"\nUpdated {ssh_key}:")
    print(f"  limit_cents: {key_config['limit_cents']}")
    print(f"  discord_webhook: {key_config.get('discord_webhook') or '(not set)'}")
    print(f"\nSaved to {BUDGETS_FILE}\n")
    return 0


def cmd_reset(args):
    """Remove custom settings for an SSH key."""
    data = load_budgets()
    ssh_key = args.key
    
    if ssh_key in data["keys"]:
        del data["keys"][ssh_key]
        save_budgets(data)
        print(f"\nRemoved custom settings for {ssh_key}")
        print(f"Will now use default limit: {format_money(data['defaults']['limit_cents'])}\n")
    else:
        print(f"\nNo custom settings found for {ssh_key}\n")
    
    return 0


def cmd_init(args):
    """Initialize budgets.yaml with all known SSH keys."""
    data = load_budgets()
    conn = db.get_db()
    
    try:
        costs = db.get_all_costs(conn)
        
        added = 0
        for cost in costs:
            ssh_key = cost["ssh_key"]
            if ssh_key not in data["keys"]:
                data["keys"][ssh_key] = {
                    "limit_cents": "default",
                    "discord_webhook": None,
                }
                added += 1
        
        save_budgets(data)
        print(f"\nInitialized {BUDGETS_FILE}")
        print(f"Added {added} new key(s), {len(data['keys'])} total\n")
        
    finally:
        conn.close()
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Manage per-SSH-key budget limits and Discord webhooks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list                     Show all keys and their budget status
  %(prog)s set nicky                Interactive setup for 'nicky' key
  %(prog)s set nicky --limit 10000  Set $10,000 limit for 'nicky'
  %(prog)s set nicky --limit default  Use default limit for 'nicky'
  %(prog)s set nicky --webhook URL  Set Discord webhook for 'nicky'
  %(prog)s reset nicky              Remove custom settings for 'nicky'
  %(prog)s init                     Add all known keys to config file
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # list command
    list_parser = subparsers.add_parser("list", help="List all SSH keys and budgets")
    list_parser.set_defaults(func=cmd_list)
    
    # set command
    set_parser = subparsers.add_parser("set", help="Set budget for an SSH key")
    set_parser.add_argument("key", help="SSH key name")
    set_parser.add_argument("--limit", help="Budget limit in dollars (e.g. 5000) or 'default'")
    set_parser.add_argument("--webhook", help="Discord webhook URL or 'none' to clear")
    set_parser.set_defaults(func=cmd_set)
    
    # reset command
    reset_parser = subparsers.add_parser("reset", help="Remove custom settings for an SSH key")
    reset_parser.add_argument("key", help="SSH key name")
    reset_parser.set_defaults(func=cmd_reset)
    
    # init command
    init_parser = subparsers.add_parser("init", help="Initialize config with all known SSH keys")
    init_parser.set_defaults(func=cmd_init)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return 1
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
