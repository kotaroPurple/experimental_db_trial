import os
import sqlite3
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = APP_DIR / "schema.sql"
DEFAULT_DB_PATH = APP_DIR.parent / "data" / "demo.db"
DB_PATH = Path(os.environ.get("DEMO_DB_PATH", DEFAULT_DB_PATH))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
