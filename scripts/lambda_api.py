#!/usr/bin/env python3
"""Lambda Labs Cloud API wrapper."""

import os
import time
from pathlib import Path

import requests

# Load config
def _load_config():
    """Load configuration from config.env file."""
    config_path = Path(__file__).parent.parent / "config.env"
    config = {}
    if config_path.exists():
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    config[key.strip()] = value.strip()
    return config


_config = _load_config()
API_KEY = _config.get("LAMBDA_API_KEY", os.environ.get("LAMBDA_API_KEY", ""))
BASE_URL = "https://cloud.lambda.ai/api/v1"

# Rate limiting: track last request time
_last_request_time = 0


def _rate_limit():
    """Ensure we don't exceed 1 request per second."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_request_time = time.time()


def _request(method: str, endpoint: str, **kwargs) -> dict:
    """Make an API request with authentication and rate limiting."""
    if not API_KEY:
        raise ValueError("LAMBDA_API_KEY not set in config.env or environment")
    
    _rate_limit()
    
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {API_KEY}"
    headers["Accept"] = "application/json"
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.request(method, url, headers=headers, **kwargs)
    response.raise_for_status()
    
    return response.json()


def list_instances() -> list[dict]:
    """
    Get all running instances.
    
    Returns list of instance dicts with keys:
        - id, name, ip, private_ip, status, hostname
        - ssh_key_names, region, instance_type, etc.
    """
    result = _request("GET", "/instances")
    return result.get("data", [])


def get_instance(instance_id: str) -> dict | None:
    """Get details for a specific instance."""
    try:
        result = _request("GET", f"/instances/{instance_id}")
        return result.get("data")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise


def terminate_instance(instance_ids: list[str]) -> list[str]:
    """
    Terminate one or more instances.
    
    Args:
        instance_ids: List of instance IDs to terminate
        
    Returns:
        List of successfully terminated instance IDs
    """
    if not instance_ids:
        return []
    
    result = _request(
        "POST",
        "/instance-operations/terminate",
        json={"instance_ids": instance_ids}
    )
    
    terminated = result.get("data", {}).get("terminated_instances", [])
    return [inst["id"] for inst in terminated]


def list_ssh_keys() -> list[dict]:
    """
    Get all SSH keys on the account.
    
    Returns list of dicts with keys:
        - id, name, public_key
    """
    result = _request("GET", "/ssh-keys")
    return result.get("data", [])


def list_instance_types() -> list[dict]:
    """
    Get available instance types and their specs/pricing.
    
    Returns list of dicts with keys:
        - name, price_cents_per_hour, specs (gpus, memory_gib, etc.)
    """
    result = _request("GET", "/instance-types")
    return result.get("data", {})


if __name__ == "__main__":
    # Test API connection
    print("Testing Lambda Labs API connection...")
    try:
        instances = list_instances()
        print(f"Found {len(instances)} running instances")
        for inst in instances:
            print(f"  - {inst.get('name', 'unnamed')} ({inst.get('id')[:8]}...): {inst.get('ip')}")
        
        keys = list_ssh_keys()
        print(f"Found {len(keys)} SSH keys")
        for key in keys:
            print(f"  - {key.get('name')}")
    except Exception as e:
        print(f"Error: {e}")
