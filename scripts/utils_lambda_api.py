#!/usr/bin/env python3
"""Lambda Labs Cloud API wrapper with multi-account support."""

import os
import time
from pathlib import Path

import requests

BASE_URL = "https://cloud.lambda.ai/api/v1"

# Rate limiting: track last request time per API key
_last_request_times = {}


def _rate_limit(api_key: str):
    """Ensure we don't exceed 1 request per second per API key."""
    global _last_request_times
    last = _last_request_times.get(api_key, 0)
    elapsed = time.time() - last
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_request_times[api_key] = time.time()


def _request(method: str, endpoint: str, api_key: str, **kwargs) -> dict:
    """Make an API request with authentication and rate limiting."""
    if not api_key:
        raise ValueError("API key is required")
    
    _rate_limit(api_key)
    
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {api_key}"
    headers["Accept"] = "application/json"
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.request(method, url, headers=headers, **kwargs)
    response.raise_for_status()
    
    return response.json()


def list_instances(api_key: str) -> list[dict]:
    """
    Get all running instances.
    
    Returns list of instance dicts with keys:
        - id, name, ip, private_ip, status, hostname
        - ssh_key_names, region, instance_type, etc.
    """
    result = _request("GET", "/instances", api_key)
    return result.get("data", [])


def get_instance(api_key: str, instance_id: str) -> dict | None:
    """Get details for a specific instance."""
    try:
        result = _request("GET", f"/instances/{instance_id}", api_key)
        return result.get("data")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise


def terminate_instance(api_key: str, instance_ids: list[str]) -> list[str]:
    """
    Terminate one or more instances.
    
    Args:
        api_key: Lambda Labs API key
        instance_ids: List of instance IDs to terminate
        
    Returns:
        List of successfully terminated instance IDs
    """
    if not instance_ids:
        return []
    
    result = _request(
        "POST",
        "/instance-operations/terminate",
        api_key,
        json={"instance_ids": instance_ids}
    )
    
    terminated = result.get("data", {}).get("terminated_instances", [])
    return [inst["id"] for inst in terminated]


def list_ssh_keys(api_key: str) -> list[dict]:
    """
    Get all SSH keys on the account.
    
    Returns list of dicts with keys:
        - id, name, public_key
    """
    result = _request("GET", "/ssh-keys", api_key)
    return result.get("data", [])


def list_instance_types(api_key: str) -> list[dict]:
    """
    Get available instance types and their specs/pricing.
    
    Returns list of dicts with keys:
        - name, price_cents_per_hour, specs (gpus, memory_gib, etc.)
    """
    result = _request("GET", "/instance-types", api_key)
    return result.get("data", {})


def list_filesystems(api_key: str) -> list[dict]:
    """
    Get all filesystems.
    
    Returns list of dicts with keys:
        - id, name, mount_point, region, is_in_use, bytes_used
    """
    result = _request("GET", "/file-systems", api_key)
    return result.get("data", [])


if __name__ == "__main__":
    # Test with accounts config
    import utils_accounts
    
    print("Testing Lambda Labs API connection...")
    data = utils_accounts.load_accounts()
    accounts = utils_accounts.get_account_list(data)
    
    if not accounts:
        print("No accounts configured. Add accounts to data/accounts.yaml")
    else:
        for acc in accounts:
            print(f"\n=== Account: {acc['name']} ===")
            try:
                instances = list_instances(acc["api_key"])
                print(f"Found {len(instances)} running instances")
                for inst in instances:
                    print(f"  - {inst.get('name', 'unnamed')} ({inst.get('id')[:8]}...): {inst.get('ip')}")
                
                keys = list_ssh_keys(acc["api_key"])
                print(f"Found {len(keys)} SSH keys")
                for key in keys:
                    print(f"  - {key.get('name')}")
            except Exception as e:
                print(f"Error: {e}")
