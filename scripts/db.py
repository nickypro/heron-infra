#!/usr/bin/env python3
"""SQLite database layer for Lambda infrastructure state."""

import json
import os
import sqlite3
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = DATA_DIR / "state.db"


def get_db() -> sqlite3.Connection:
    """Get database connection, creating schema if needed."""
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    """Initialize database schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS instances (
            id TEXT PRIMARY KEY,
            name TEXT,
            ip TEXT,
            private_ip TEXT,
            status TEXT,
            hostname TEXT,
            region TEXT,
            instance_type TEXT,
            gpu_count INTEGER,
            hourly_cost_cents INTEGER,
            ssh_key_names TEXT,  -- JSON array
            first_seen REAL,
            last_seen REAL,
            initialized INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS gpu_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id TEXT NOT NULL,
            gpu_index INTEGER DEFAULT 0,
            utilization INTEGER,
            timestamp REAL,
            FOREIGN KEY (instance_id) REFERENCES instances(id)
        );

        CREATE INDEX IF NOT EXISTS idx_gpu_samples_instance_time 
            ON gpu_samples(instance_id, timestamp);

        CREATE TABLE IF NOT EXISTS costs (
            ssh_key TEXT PRIMARY KEY,
            total_cents INTEGER DEFAULT 0,
            last_updated REAL
        );
    """)
    conn.commit()


def upsert_instance(conn: sqlite3.Connection, instance: dict):
    """Insert or update an instance record."""
    now = time.time()
    
    # Check if instance exists to preserve first_seen
    existing = conn.execute(
        "SELECT first_seen, initialized FROM instances WHERE id = ?",
        (instance["id"],)
    ).fetchone()
    
    first_seen = existing["first_seen"] if existing else now
    initialized = existing["initialized"] if existing else 0
    
    # Extract instance type info
    itype = instance.get("instance_type", {})
    hourly_cost = itype.get("price_cents_per_hour", 0)
    gpu_count = itype.get("specs", {}).get("gpus", 0)
    
    conn.execute("""
        INSERT OR REPLACE INTO instances 
        (id, name, ip, private_ip, status, hostname, region, instance_type,
         gpu_count, hourly_cost_cents, ssh_key_names, first_seen, last_seen, initialized)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        instance["id"],
        instance.get("name"),
        instance.get("ip"),
        instance.get("private_ip"),
        instance.get("status"),
        instance.get("hostname"),
        instance.get("region", {}).get("name"),
        itype.get("name"),
        gpu_count,
        hourly_cost,
        json.dumps(instance.get("ssh_key_names", [])),
        first_seen,
        now,
        initialized
    ))
    conn.commit()


def mark_initialized(conn: sqlite3.Connection, instance_id: str):
    """Mark an instance as initialized."""
    conn.execute(
        "UPDATE instances SET initialized = 1 WHERE id = ?",
        (instance_id,)
    )
    conn.commit()


def get_uninitialized_instances(conn: sqlite3.Connection) -> list[dict]:
    """Get instances that haven't been initialized yet."""
    rows = conn.execute("""
        SELECT * FROM instances 
        WHERE initialized = 0 AND status = 'active'
    """).fetchall()
    return [dict(row) for row in rows]


def get_active_instances(conn: sqlite3.Connection) -> list[dict]:
    """Get all active instances."""
    rows = conn.execute("""
        SELECT * FROM instances WHERE status = 'active'
    """).fetchall()
    return [dict(row) for row in rows]


def add_gpu_sample(conn: sqlite3.Connection, instance_id: str, utilization: int, gpu_index: int = 0):
    """Record a GPU utilization sample."""
    conn.execute(
        "INSERT INTO gpu_samples (instance_id, gpu_index, utilization, timestamp) VALUES (?, ?, ?, ?)",
        (instance_id, gpu_index, utilization, time.time())
    )
    conn.commit()


def get_gpu_samples_since(conn: sqlite3.Connection, instance_id: str, since_timestamp: float) -> list[dict]:
    """Get GPU samples for an instance since a given timestamp."""
    rows = conn.execute("""
        SELECT * FROM gpu_samples 
        WHERE instance_id = ? AND timestamp > ?
        ORDER BY timestamp
    """, (instance_id, since_timestamp)).fetchall()
    return [dict(row) for row in rows]


def update_cost(conn: sqlite3.Connection, ssh_key: str, cents_to_add: int):
    """Add cost to an SSH key's running total."""
    now = time.time()
    conn.execute("""
        INSERT INTO costs (ssh_key, total_cents, last_updated)
        VALUES (?, ?, ?)
        ON CONFLICT(ssh_key) DO UPDATE SET
            total_cents = total_cents + excluded.total_cents,
            last_updated = excluded.last_updated
    """, (ssh_key, cents_to_add, now))
    conn.commit()


def get_all_costs(conn: sqlite3.Connection) -> list[dict]:
    """Get cost totals for all SSH keys."""
    rows = conn.execute("SELECT * FROM costs ORDER BY total_cents DESC").fetchall()
    return [dict(row) for row in rows]


def cleanup_old_samples(conn: sqlite3.Connection, older_than_hours: int = 24):
    """Remove GPU samples older than specified hours."""
    cutoff = time.time() - (older_than_hours * 3600)
    conn.execute("DELETE FROM gpu_samples WHERE timestamp < ?", (cutoff,))
    conn.commit()


def export_to_json(conn: sqlite3.Connection):
    """Export all tables to JSON files for inspection."""
    # Instances
    instances = [dict(row) for row in conn.execute("SELECT * FROM instances").fetchall()]
    for inst in instances:
        if inst.get("ssh_key_names"):
            inst["ssh_key_names"] = json.loads(inst["ssh_key_names"])
    
    with open(DATA_DIR / "instances.json", "w") as f:
        json.dump(instances, f, indent=2)
    
    # GPU history (last 24 hours only for readability)
    cutoff = time.time() - 86400
    samples = [dict(row) for row in conn.execute(
        "SELECT * FROM gpu_samples WHERE timestamp > ? ORDER BY timestamp DESC",
        (cutoff,)
    ).fetchall()]
    
    with open(DATA_DIR / "gpu_history.json", "w") as f:
        json.dump(samples, f, indent=2)
    
    # Costs
    costs = get_all_costs(conn)
    with open(DATA_DIR / "costs.json", "w") as f:
        json.dump(costs, f, indent=2)


if __name__ == "__main__":
    # Test database setup
    conn = get_db()
    print(f"Database initialized at {DB_PATH}")
    conn.close()
