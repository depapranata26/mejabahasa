"""Runtime configuration for MejaBahasa — environment only, no secrets.

Every value here is read from the environment with a safe default.
Copy .env.example to .env and fill in your own key; .env is git-ignored
and must never be committed (this repo is public).
"""

import os

# Model provider selection: 'baseten' (primary) | 'fireworks' | 'meta'.
PROVIDER = os.environ.get("PROVIDER", "baseten")

# Baseten (OpenAI-compatible). The key itself lives only in the environment.
BASETEN_API_KEY = os.environ.get("BASETEN_API_KEY", "")
BASETEN_BASE_URL = os.environ.get(
    "BASETEN_BASE_URL", "https://inference.baseten.co/v1"
)

# Hackathon model: the Baseten key only works with this exact model name.
MODEL = os.environ.get("MODEL", "meta-models/muse-spark-open-1026")

# Local SQLite database file (created at runtime from data/schema.sql).
DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/mejabahasa.db")

# Guest-facing languages for the tourist door (QR web chat).
SUPPORTED_LANGUAGES = ("zh", "ko", "ja", "id")
