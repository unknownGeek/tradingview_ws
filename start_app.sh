#!/bin/zsh
# Initialise Python venv, install dependencies, and launch the app with uvicorn

# Step 1: Create venv if not exists
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "[+] Created virtual environment in .venv"
fi

# Step 2: Activate venv
source .venv/bin/activate

# Step 3: Upgrade pip
pip install --upgrade pip

# Step 4: Install required packages
pip install -r requirements.txt

# Step 5: Launch the app with uvicorn and logging
# (You can adjust log level as needed)
LOGFILE="uvicorn.log"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload --log-level info | tee $LOGFILE
