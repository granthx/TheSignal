import os
import sys
from pathlib import Path

# Add project root and backend to python path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend"))
sys.path.insert(0, str(ROOT_DIR))

from backend.app.main import create_app
from backend.app.config import Settings

# On serverless (Vercel), local disk writes must go to /tmp
if os.environ.get("VERCEL") or not os.environ.get("DB_PATH"):
    os.environ.setdefault("DB_PATH", "/tmp/signal.db")

# Disable in-memory background scheduler daemon on serverless
os.environ.setdefault("SCHEDULER_ENABLED", "false")

settings = Settings.from_env()
app = create_app(settings)
