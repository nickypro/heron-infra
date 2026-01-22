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

- `monitor.py` - collects GPU stats, updates `~/.ssh/config`, tracks costs (every minute)
- `terminate_idle_instances.py` - shuts down instances at 0% GPU for too long (every 5 min)
- `monitor_availability.py` - tracks instance type availability by region (every 10 min)
- `backup.py` - rsyncs home directories to `./backup/` (every 30 min)
- `show_instances.py` - shows current instance status (manual)
- `show_usage.py` - shows cost per SSH key by time period (manual)

## show_instances.py

```
  Lambda Instance Status  │  2026-01-22 16:32:38  │  Idle threshold: 2.0h

┌─ my-instance ───────────────────────────────────────────────────────┐
│  🟢 ACTIVE      IP: 192.0.2.1        Type: gpu_8x_a100              │
│  Key: alice-key           Cost: $12.50   Samples: 120 (24h)        │
│  GPUs(8): 75%  now  68%  1h avg   Idle: -       Term: -            │
│  First: Jan 22 10:00   Last: Jan 22 16:32                          │
└────────────────────────────────────────────────────────────────────┘
```

Use `--json` for JSON output.

## monitor_availability.py

See what instance types are available right now:

```bash
python3 scripts/monitor_availability.py
```

```
  Current Availability  │  2026-01-22 17:22:21
  9 types available, 15 unavailable

  AVAILABLE:
    1x A100 (40 GB SXM4)           $1.29/hr  │  us-east-1, us-west-2, asia-south-1
    8x H100 (80 GB SXM5)           $23.92/hr  │  us-west-3
    ...
```

Track availability over time:

```bash
python3 scripts/monitor_availability.py --record     # record current state
python3 scripts/monitor_availability.py --history 24 # show last 24 hours
```

## Config

See `config.env.example`. Main options:

- `LAMBDA_API_KEY` - from cloud.lambda.ai/api-keys
- `IDLE_SHUTDOWN_HOURS` - hours at 0% before termination (default: 2)
- `SSH_KEYS_DIR` - where your SSH keys live (default: ./keys)

## Cron

```bash
./setup_cron.sh          # install
crontab -l               # view
# remove:
crontab -l | sed '/# BEGIN HERON-INFRA/,/# END HERON-INFRA/d' | crontab -
```

## Data

SQLite db at `data/state.db`, plus JSON exports in `data/` for easy inspection. Logs in `logs/`.
