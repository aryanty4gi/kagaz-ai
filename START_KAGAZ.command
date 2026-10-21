#!/bin/bash
# Kagaz AI one-click setup + run (macOS). Double-click this file.
cd "$(dirname "$0")"
echo "=============================================="
echo "  Kagaz AI - setup and start"
echo "=============================================="

# 1. Find Python 3.10 or newer
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v $c >/dev/null 2>&1; then
    if $c -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then PY=$c; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo ""
  echo "Python 3.10+ is not installed. Opening the download page..."
  echo "Install it (click Download Python 3.12), then double-click START_KAGAZ again."
  open "https://www.python.org/downloads/macos/"
  read -p "Press Enter to close."
  exit 1
fi
echo "Using $($PY --version)"

# 2. Virtual environment + packages (first run takes 1-2 minutes)
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  $PY -m venv .venv || { echo "Could not create venv"; read -p "Press Enter."; exit 1; }
fi
source .venv/bin/activate
echo "Installing packages (only slow the first time)..."
pip install -q --upgrade pip >/dev/null 2>&1
pip install -q -r requirements.txt || { echo "Package install failed. Check internet."; read -p "Press Enter."; exit 1; }

# 3. .env with your ASI:One key and private random seeds (created once)
if [ ! -f ".env" ] || grep -q "your-asi1-api-key" .env; then
  echo ""
  echo "Get your key at https://asi1.ai (log in -> Developer section -> Create New)"
  echo "Paste your ASI:One API key below and press Enter (it stays hidden):"
  read -s ASIKEY
  echo ""
  if [ -z "$ASIKEY" ]; then echo "No key entered."; read -p "Press Enter."; exit 1; fi
  python - "$ASIKEY" <<'PYEOF'
import os, secrets, sys
key = sys.argv[1].strip()
lines = open(".env").read().splitlines() if os.path.exists(".env") else []
if lines:
    # Keep the existing seeds (they fix the agent addresses); only set the key.
    lines = [f"ASI1_API_KEY={key}" if l.startswith("ASI1_API_KEY=") else l for l in lines]
else:
    roles = ["COORDINATOR", "READER", "VERIFY", "RULES", "ACTION", "EXPERT"]
    lines = [f"ASI1_API_KEY={key}"]
    lines += [f"KAGAZ_{r}_SEED=macieguy-{r.lower()}-{secrets.token_hex(24)}" for r in roles]
    lines += ["KAGAZ_USE_MAILBOX=true", "KAGAZ_ONLINE_CHECKS=true",
              "KAGAZ_EXPERT_FEE=0.5", "KAGAZ_EXPERT_CURRENCY=FET",
              "KAGAZ_EXPERT_FEE_INR=99", "KAGAZ_REAL_FET=false"]
open(".env", "w").write("\n".join(lines) + "\n")
print("Saved .env (never upload this file).")
PYEOF
fi

# 4. Quick self-test
echo ""
echo "Running self-test..."
KAGAZ_ONLINE_CHECKS=false python -m pytest -q tests/test_core.py 2>&1 | tail -1

# 5. Show addresses and start
echo ""
echo "=============================================="
echo " YOUR AGENT ADDRESSES (copy into README + slide 7)"
echo "=============================================="
python -c "
from kagaz import config
for r in config.AGENTS: print(f'{r:<12} {config.address_for(r)}')
" 2>/dev/null | tee agent_addresses.txt
echo ""
echo "NEXT: in the logs below, find the line for kagaz_coordinator:"
echo "  'Agent inspector available at https://agentverse.ai/inspect/...'"
echo "  Cmd+double-click that link -> Connect -> Mailbox."
echo "Keep this window OPEN while you demo. Press Ctrl+C to stop."
echo "=============================================="
python run_all.py
