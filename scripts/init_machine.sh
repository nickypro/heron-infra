#!/bin/bash
# Init script for new Lambda machines
# This gets copied to machines and run on startup via monitor.py

set -e

echo "Initializing machine..."

# Setup SSH keys (from public_keys.txt, copied by monitor.py)
if [ -f /tmp/setup_ssh_keys.sh ]; then
    chmod +x /tmp/setup_ssh_keys.sh
    /tmp/setup_ssh_keys.sh /tmp/public_keys.txt
fi

cd ~

# Clone dotfiles
if [ ! -d ~/.vps-dotfiles ]; then
  git clone https://github.com/nickypro/.vps-dotfiles ~/.vps-dotfiles
fi

# Run zsh setup
bash ~/.vps-dotfiles/zsh_install.sh || true

# Clone heron-infra
if [ ! -d ~/.heron-infra ]; then
  git clone https://github.com/nickypro/heron-infra ~/.heron-infra
fi

# Copy examples to Lambda NFS (find the mounted volume)
if [ -d /lambda/nfs ]; then
  NFS_DIR=$(find /lambda/nfs -maxdepth 1 -mindepth 1 -type d | head -1)
  if [ -n "$NFS_DIR" ]; then
    echo "Copying examples to $NFS_DIR/"
    cp -r ~/.heron-infra/examples "$NFS_DIR/"
  else
    echo "No NFS volume found in /lambda/nfs"
  fi
fi

# Install uv
if ! command -v uv &> /dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# Ensure uv is in PATH (needed for non-interactive SSH sessions)
export PATH="$HOME/.local/bin:$PATH"

# Create virtual environment
mkdir -p ~/.venv
cd ~/.venv
uv venv --allow-existing

echo "Initialization complete!"
