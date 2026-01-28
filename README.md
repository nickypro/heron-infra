# heron-infra

Manages Lambda Labs GPU instances from a proxy machine. Tracks GPU usage, shuts down idle instances, keeps SSH config updated, backs up data, and monitors availability patterns.

## Setup

```bash
pip install -r requirements.txt
cp data/accounts.yaml.example data/accounts.yaml
# add your Lambda API keys to data/accounts.yaml
```

Put SSH keys in `./keys/`, named to match the key names in Lambda:

```
keys/
├── alice-key
├── bob-key.pem
└── charlie-key/
    └── charlie-key.pem
```

Test it:

```bash
python3 scripts/monitor.py
python3 scripts/show_instances.py
```

Install cron jobs:

```bash
./setup_cron.sh
```

## Scripts

| Script | Cron | Description |
|--------|------|-------------|
| `monitor.py` | 1 min | Collects GPU stats, updates `~/.ssh/config`, tracks costs |
| `terminate_idle_instances.py` | 5 min | Terminates instances idle too long |
| `enforce_budgets.py` | 5 min | Terminates instances when account exceeds budget |
| `backup.py` | 30 min | Backs up `~/` and persistent volumes |
| `monitor_availability.py` | 10 min | Records GPU availability by region |
| `show_instances.py` | manual | Shows instance status |
| `show_availability.py` | manual | Shows availability patterns |
| `show_usage.py` | manual | Shows cost per SSH key |

## Multi-Account Setup

Configure accounts in `data/accounts.yaml`:

```yaml
defaults:
  limit_cents: 500000           # $5000 default budget
  milestone_interval: 100000    # notify every $1000

accounts:
  team-research:
    api_key: "secret_xxx"
    limit_cents: 1000000        # $10,000
    discord_webhook: "https://discord.com/api/webhooks/..."
  
  team-prod:
    api_key: "secret_yyy"
    limit_cents: default        # uses $5000 default
```

## Termination Policy

Instances are terminated when **both** conditions are met:
- Running for at least `MIN_RUNTIME_HOURS` (default: 4h)
- Idle (0% GPU) for at least `IDLE_SHUTDOWN_HOURS` (default: 2h)

### Whitelisting

Include **"whitelist"** in the instance name to prevent auto-termination:

- `my-training-whitelist` ✓
- `WHITELIST-prod` ✓

## Budget Management

Set per-account budgets in `accounts.yaml`. When an account exceeds its budget:
1. Discord notification sent (if webhook configured)
2. All instances terminated

Override by including **"OVERBUDGET"** in instance name to keep it running.

## Availability Monitoring

```bash
# Show availability patterns (last 7 days)
python3 scripts/show_availability.py

# Filter by region or GPU type
python3 scripts/show_availability.py --region us-west-1
python3 scripts/show_availability.py --gpu a100

# Show best times to launch
python3 scripts/show_availability.py --summary --by-time
python3 scripts/show_availability.py --summary --by-gpu
```

Output shows a heatmap of when each GPU type was available:

```
  GPU Availability Patterns  │  2026-01-28 15:00:00
  Based on 716 checks over 7 days

  ┌─ us-west-1 ──────────────────────────────────────────────────────────────┐
  │            Mon    Tue    Wed    Thu    Fri    Sat    Sun                 │
  │ 1x_gh200   ██████ ██████ ██████ ██████ ██████ ██████ ██████  95%         │
  │ 8x_a100    ▓▓▓▓▓▓ ▓▓▓▓▓▓ ██████ ██████ ▓▓▓▓▓▓ ▒▒▒▒▒▒ ░░░░░░  62%         │
  └──────────────────────────────────────────────────────────────────────────┘
```

Legend: `██` >75% | `▓▓` >50% | `▒▒` >25% | `░░` >0% | `··` never | `  ` no data

## Config

`config.env` settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `MIN_RUNTIME_HOURS` | 4 | Min runtime before termination |
| `IDLE_SHUTDOWN_HOURS` | 2 | Hours at 0% GPU before termination |
| `SSH_KEYS_DIR` | ./keys | Directory containing SSH keys |
| `BACKUP_DIR` | ./backup | Where backups are stored |

## Cron Management

```bash
./setup_cron.sh          # install/update cron jobs
crontab -l               # view current crontab

# remove heron-infra jobs:
crontab -l | sed '/# BEGIN HERON-INFRA/,/# END HERON-INFRA/d' | crontab -
```

## Data

- SQLite database: `data/state.db`
- Account config: `data/accounts.yaml`
- Logs: `logs/`
- Backups: `backup/instances/` and `backup/volumes/{region}/`
