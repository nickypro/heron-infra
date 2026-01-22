# heron-infra

Manages Lambda Labs GPU instances from a proxy machine. Tracks GPU usage, shuts down idle instances, keeps SSH config updated, and backs up data.

## Setup

```bash
pip install -r requirements.txt
cp config.env.example config.env
# add your LAMBDA_API_KEY to config.env
```

Put your SSH keys in `./keys/`, named to match the key names in Lambda:

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
| `monitor.py` | every 1 min | Collects GPU stats, updates `~/.ssh/config`, tracks costs |
| `terminate_idle_instances.py` | every 5 min | Terminates instances that are idle too long |
| `backup.py` | every 30 min | Backs up `~/` to `./backup/instances/`, volumes to `./backup/volumes/{region}/` |
| `monitor_availability.py` | every 10 min | Records instance type availability by region |
| `show_instances.py` | manual | Shows current instance status |
| `show_usage.py` | manual | Shows cost per SSH key |

## Termination Policy

Instances are terminated when **both** conditions are met:
- Running for at least `MIN_RUNTIME_HOURS` (default: 4h)
- Idle (0% GPU) for at least `IDLE_SHUTDOWN_HOURS` (default: 2h)

### Whitelisting

To prevent an instance from being auto-terminated, include **"whitelist"** in the instance name on Lambda Labs (case-insensitive):

- `my-training-whitelist` ✓
- `whitelist-experiment` ✓
- `WHITELIST-prod` ✓

Whitelisted instances show 🔒 status and are never terminated by the script.

## show_instances.py

```
  Lambda Instance Status  │  2026-01-22 18:41:05
  Termination: ≥4.0h runtime AND ≥2.0h idle
  2 active instance(s)

┌─ my-instance ───────────────────────────────────────────────────────────┐
│  🟢 ACTIVE      IP: 192.0.2.1        Type: gpu_8x_a100                  │
│  Key: alice-key           Cost: $12.50    Samples: 120 (24h)           │
│  GPUs(8): 75%  now, 68%  1h avg                                        │
│  Runtime: 3h13m  (min 4.0h) (46m to go)                                │
│  Idle:    0m     (max 2.0h) (not idle)                                 │
│  First: Jan 22 10:00   Last: Jan 22 16:32                              │
└─────────────────────────────────────────────────────────────────────────┘
```

Status indicators:
- 🟢 ACTIVE - GPU in use
- 🟡 IDLE - GPU at 0%
- 🟠 PROTECTED - Would terminate but runtime protection active
- 🔴 TERMINATE - Will be terminated
- 🔒 WHITELIST - Never auto-terminated

## monitor_availability.py

```bash
python3 scripts/monitor_availability.py              # show current availability
python3 scripts/monitor_availability.py --record     # record to database
python3 scripts/monitor_availability.py --history 24 # show last 24 hours
```

## Config

See `config.env.example`:

| Variable | Default | Description |
|----------|---------|-------------|
| `LAMBDA_API_KEY` | - | From cloud.lambdalabs.com/api-keys |
| `MIN_RUNTIME_HOURS` | 4 | Min runtime before termination allowed |
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
- JSON exports: `data/*.json` (for easy inspection)
- Logs: `logs/`
- Backups: `backup/instances/` and `backup/volumes/{region}/`
