"""Minimal settings for running Person 3's agents on their own."""

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
UPLOADS_DIR = ROOT / "data" / "untrusted_uploads"
DB_PATH = ROOT / "store" / "standalone.db"
_as_of = os.getenv("AS_OF_DATE", "2026-10-24")         # the dataset is built around 24 Oct 2026
AS_OF_DATE = date.today() if _as_of == "today" else date.fromisoformat(_as_of)
