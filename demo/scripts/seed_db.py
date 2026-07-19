"""SQLiteデモDBの初期化・シード投入スクリプト。

使い方:
    uv run python demo/scripts/seed_db.py           # 既存DBに未投入分だけ追加
    uv run python demo/scripts/seed_db.py --reset   # DBファイルを作り直してから投入
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demo.app import db
from demo.app.seed_data import CONDITION_KEYS, SAMPLE_SEGMENTS


def seed(conn):
    key_id_by_name = {}
    for key in CONDITION_KEYS:
        conn.execute(
            """
            INSERT OR IGNORE INTO condition_keys
                (key_name, display_name, value_type, scope, min_value, max_value, description, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                key["key_name"],
                key["display_name"],
                key["value_type"],
                key["scope"],
                key["min_value"],
                key["max_value"],
                key["description"],
            ),
        )
        row = conn.execute(
            "SELECT key_id FROM condition_keys WHERE key_name = ?", (key["key_name"],)
        ).fetchone()
        key_id_by_name[key["key_name"]] = row["key_id"]

        for raw_value, display_name in key["values"]:
            conn.execute(
                """
                INSERT OR IGNORE INTO condition_values (key_id, value, display_name, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (row["key_id"], json.dumps(raw_value, ensure_ascii=False), display_name),
            )

    existing = conn.execute("SELECT COUNT(*) AS n FROM segments").fetchone()["n"]
    if existing == 0:
        for seg in SAMPLE_SEGMENTS:
            conn.execute(
                """
                INSERT INTO segments (label, record_date, started_at, ended_at, conditions, creator_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    seg["label"],
                    seg["record_date"],
                    seg["started_at"],
                    seg["ended_at"],
                    json.dumps(seg["conditions"], ensure_ascii=False),
                    seg["creator_id"],
                ),
            )

    conn.commit()

    counts = {
        "condition_keys": conn.execute("SELECT COUNT(*) AS n FROM condition_keys").fetchone()["n"],
        "condition_values": conn.execute("SELECT COUNT(*) AS n FROM condition_values").fetchone()["n"],
        "segments": conn.execute("SELECT COUNT(*) AS n FROM segments").fetchone()["n"],
    }
    return counts


def main():
    parser = argparse.ArgumentParser(description="デモ用SQLite DBの初期化・シード投入")
    parser.add_argument("--reset", action="store_true", help="既存のDBファイルを削除してから作り直す")
    args = parser.parse_args()

    if args.reset and db.DB_PATH.exists():
        db.DB_PATH.unlink()
        print(f"既存DBを削除しました: {db.DB_PATH}")

    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db.get_connection()
    try:
        db.init_db(conn)
        counts = seed(conn)
    finally:
        conn.close()

    print(f"DB: {db.DB_PATH}")
    print(f"condition_keys={counts['condition_keys']} condition_values={counts['condition_values']} "
          f"segments={counts['segments']}")


if __name__ == "__main__":
    main()
