#!/usr/bin/env python3
"""
CLI tool to manage Lambda account budgets and Discord webhooks.

Usage:
    python3 manage_budgets.py list              # Show all accounts and budgets
    python3 manage_budgets.py set ACCOUNT       # Edit an account (interactive)
    python3 manage_budgets.py set ACCOUNT --limit 10000 --webhook URL
"""

import argparse
import sys
from pathlib import Path

import utils_accounts
import utils_db as db

PROJECT_DIR = Path(__file__).parent.parent


def format_money(cents: int | float) -> str:
    """Format cents as dollar string."""
    return f"${cents / 100:,.0f}"


def cmd_list(args):
    """List all accounts with their budget status."""
    data = utils_accounts.load_accounts()
    accounts = utils_accounts.get_account_list(data)
    conn = db.get_db()
    
    try:
        # Get all costs from DB
        costs = {c["account"]: c["total_cents"] for c in db.get_all_account_costs(conn)}
        
        if not accounts:
            print("\nNo accounts configured.")
            print("Add accounts to data/accounts.yaml or set LAMBDA_API_KEY in config.env\n")
            return
        
        default_limit = data.get("defaults", {}).get("limit_cents", 500000)
        print(f"\n  Account Budget Status  │  Default limit: {format_money(default_limit)}\n")
        
        # Header
        print(f"  {'Account':<20} │ {'Limit':>10} │ {'Spent':>10} │ {'Remaining':>10} │ Discord")
        print(f"  {'-'*20}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*10}-┼-{'-'*8}")
        
        # Sort by spent descending
        sorted_accounts = sorted(accounts, key=lambda a: costs.get(a["name"], 0), reverse=True)
        
        for acc in sorted_accounts:
            name = acc["name"]
            limit = acc["limit_cents"]
            spent = costs.get(name, 0)
            remaining = limit - spent
            
            # Check if using custom or default limit
            acc_config = data.get("accounts", {}).get(name, {})
            limit_val = acc_config.get("limit_cents", "default")
            if limit_val == "default" or limit_val is None:
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
            discord = "✓" if acc.get("discord_webhook") else "-"
            
            # Truncate long names
            display_name = name[:20] if len(name) <= 20 else name[:17] + "..."
            
            print(f"  {display_name:<20} │ {limit_str:>10} │ {format_money(spent):>10} │ {remaining_str:>10} │ {discord:^8}")
        
        print(f"\n  * = using default limit")
        print(f"  ! = <20% remaining, ⚠ = over budget\n")
        
    finally:
        conn.close()


def cmd_set(args):
    """Set budget configuration for an account."""
    data = utils_accounts.load_accounts()
    account_name = args.account
    
    # Check if account exists
    if account_name not in data.get("accounts", {}):
        print(f"\nAccount '{account_name}' not found in accounts.yaml")
        print("Available accounts:", ", ".join(data.get("accounts", {}).keys()) or "(none)")
        return 1
    
    acc_config = data["accounts"][account_name]
    
    # Interactive mode if no options provided
    if args.limit is None and args.webhook is None:
        print(f"\nConfiguring budget for account: {account_name}")
        print(f"Current settings:")
        print(f"  limit_cents: {acc_config.get('limit_cents', 'default')}")
        print(f"  discord_webhook: {acc_config.get('discord_webhook') or '(not set)'}")
        print()
        
        # Get limit
        current_limit = acc_config.get("limit_cents", "default")
        limit_input = input(f"Budget limit in dollars (or 'default') [{current_limit if current_limit == 'default' else format_money(current_limit)}]: ").strip()
        if limit_input:
            if limit_input.lower() == "default":
                acc_config["limit_cents"] = "default"
            else:
                try:
                    limit_input = limit_input.replace("$", "").replace(",", "")
                    acc_config["limit_cents"] = int(float(limit_input) * 100)
                except ValueError:
                    print(f"Invalid limit: {limit_input}")
                    return 1
        
        # Get webhook
        current_webhook = acc_config.get("discord_webhook") or ""
        webhook_input = input(f"Discord webhook URL (or 'none' to clear) [{current_webhook[:40] + '...' if len(current_webhook) > 40 else current_webhook or '(none)'}]: ").strip()
        if webhook_input:
            if webhook_input.lower() == "none":
                acc_config["discord_webhook"] = None
            else:
                acc_config["discord_webhook"] = webhook_input
    
    else:
        # Non-interactive mode
        if args.limit is not None:
            if args.limit.lower() == "default":
                acc_config["limit_cents"] = "default"
            else:
                try:
                    limit = args.limit.replace("$", "").replace(",", "")
                    acc_config["limit_cents"] = int(float(limit) * 100)
                except ValueError:
                    print(f"Invalid limit: {args.limit}")
                    return 1
        
        if args.webhook is not None:
            if args.webhook.lower() == "none":
                acc_config["discord_webhook"] = None
            else:
                acc_config["discord_webhook"] = args.webhook
    
    utils_accounts.save_accounts(data)
    print(f"\nUpdated {account_name}:")
    print(f"  limit_cents: {acc_config['limit_cents']}")
    print(f"  discord_webhook: {acc_config.get('discord_webhook') or '(not set)'}")
    print(f"\nSaved to {utils_accounts.ACCOUNTS_FILE}\n")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Manage Lambda account budgets and Discord webhooks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list                        Show all accounts and their budget status
  %(prog)s set personal                Interactive setup for 'personal' account
  %(prog)s set personal --limit 10000  Set $10,000 limit for 'personal'
  %(prog)s set personal --limit default  Use default limit for 'personal'
  %(prog)s set personal --webhook URL  Set Discord webhook for 'personal'
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # list command
    list_parser = subparsers.add_parser("list", help="List all accounts and budgets")
    list_parser.set_defaults(func=cmd_list)
    
    # set command
    set_parser = subparsers.add_parser("set", help="Set budget for an account")
    set_parser.add_argument("account", help="Account name")
    set_parser.add_argument("--limit", help="Budget limit in dollars (e.g. 5000) or 'default'")
    set_parser.add_argument("--webhook", help="Discord webhook URL or 'none' to clear")
    set_parser.set_defaults(func=cmd_set)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return 1
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
