#!/bin/bash
# setup.sh — Bootstrap for a fresh EC2 instance (Amazon Linux 2023)
# Usage: bash setup.sh

set -e

echo "===== 1. Python + pip ====="
sudo dnf install -y python3 python3-pip

echo "===== 2. PATH ====="
echo 'export PATH=$PATH:/home/ssm-user/.local/bin' >> ~/.bashrc
source ~/.bashrc

echo "===== 3. Python dependencies ====="
pip3 install -r requirements.txt

echo "===== 4. Env ====="
cp .env.example .env
echo ""
echo "IMPORTANT: edit .env with your credentials before starting."
echo "  nano .env"
echo ""
echo "===== Setup complete ====="
echo "To start: bash start.sh"
