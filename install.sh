#!/usr/bin/env bash
# ============================================================
#  BlueCloud — EK COMMAND me install (VPS pe)
#
#  Option A — repo clone karke:
#    git clone https://github.com/siddhantpashte123-hub/cloud.git
#    cd cloud && bash install.sh
#
#  Option B — bina clone, seedha ek command:
#    curl -fsSL https://raw.githubusercontent.com/siddhantpashte123-hub/cloud/main/install.sh | bash
#
#  Docker NAHI chahiye. Sirf Python 3.
# ============================================================
set -e

REPO_RAW="https://raw.githubusercontent.com/siddhantpashte123-hub/cloud/main"
if [ "$(id -u)" -eq 0 ]; then
  INSTALL_DIR="${MYCLOUD_DIR:-/opt/mycloud}"
else
  INSTALL_DIR="${MYCLOUD_DIR:-$HOME/.mycloud}"
fi

echo "========================================"
echo "  ☁️  BlueCloud Installer — 1 Command Setup"
echo "========================================"
echo ""

# Python check
if ! command -v python3 >/dev/null 2>&1; then
  echo "⚠  Python3 nahi mila — install kar raha hoon..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq python3 curl
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 curl
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache python3 curl
  else
    echo "✘ Python3 install nahi ho paya. Khud install karo: https://www.python.org/downloads/"
    exit 1
  fi
fi
echo "✔ Python3 mil gaya: $(python3 --version)"

mkdir -p "$INSTALL_DIR/data"

# Files lao: local (clone) ya GitHub se
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo ".")"
if [ -f "$SCRIPT_DIR/server.py" ]; then
  cp "$SCRIPT_DIR/server.py" "$INSTALL_DIR/server.py"
  echo "✔ server.py copy ho gaya (local)"
elif [ -f "./server.py" ]; then
  cp "./server.py" "$INSTALL_DIR/server.py"
  echo "✔ server.py copy ho gaya"
else
  echo "ℹ GitHub se files download kar raha hoon..."
  curl -fsSL "$REPO_RAW/server.py" -o "$INSTALL_DIR/server.py"
  echo "✔ server.py download ho gaya"
fi

if [ -f "$SCRIPT_DIR/cloud" ]; then
  cp "$SCRIPT_DIR/cloud" "$INSTALL_DIR/cloud" 2>/dev/null || true
elif [ -f "./cloud" ]; then
  cp "./cloud" "$INSTALL_DIR/cloud" 2>/dev/null || true
else
  curl -fsSL "$REPO_RAW/cloud" -o "$INSTALL_DIR/cloud" 2>/dev/null || true
fi
chmod +x "$INSTALL_DIR/cloud" 2>/dev/null || true
chmod +x "$INSTALL_DIR/server.py"

# Ab asli install (password + start) cloud CLI ko de do
echo ""
exec bash "$INSTALL_DIR/cloud" install
