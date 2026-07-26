"""SQLite POC DBの初期化・マスタ投入スクリプト。

使い方:
    uv run python demo/scripts/seed_db.py                  # 既存DBに未投入のマスタだけ追加
    uv run python demo/scripts/seed_db.py --reset          # DBとストレージを作り直してマスタ投入
    uv run python demo/scripts/seed_db.py --reset --with-sample
        # さらにサンプルシナリオ(セッション→生データ→区間→整形→処理→評価)を一括で流す

既定では実体テーブル(セッション・生ファイル・区間…)は空のまま始まる。
GUI から操作するたびにテーブルが埋まっていく様子を見せるのがこのPOCの目的のため。
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demo.app import db, oplog, storage
from demo.app.seed_data import ALGORITHMS, CONDITION_KEYS, SENSOR_TYPES


def seed_masters(conn):
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

        for raw_value, display_name in key["values"]:
            conn.execute(
                """
                INSERT OR IGNORE INTO condition_values (key_id, value, display_name, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (row["key_id"], json.dumps(raw_value, ensure_ascii=False), display_name),
            )

        for axis_index, (axis_label, min_v, max_v) in enumerate(key.get("axes", [])):
            conn.execute(
                """
                INSERT OR IGNORE INTO condition_key_axes (key_id, axis_index, axis_label, min_value, max_value)
                VALUES (?, ?, ?, ?, ?)
                """,
                (row["key_id"], axis_index, axis_label, min_v, max_v),
            )

    for sensor_type, display_name, role in SENSOR_TYPES:
        conn.execute(
            """
            INSERT OR IGNORE INTO sensor_types (sensor_type, display_name, role, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (sensor_type, display_name, role),
        )

    for algorithm_name, display_name, role, input_sensor_type in ALGORITHMS:
        conn.execute(
            """
            INSERT OR IGNORE INTO algorithms
                (algorithm_name, display_name, role, input_sensor_type, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (algorithm_name, display_name, role, input_sensor_type),
        )

    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="POC用SQLite DBの初期化・マスタ投入")
    parser.add_argument("--reset", action="store_true",
                        help="既存のDBファイルとダミーストレージを削除してから作り直す")
    parser.add_argument("--with-sample", action="store_true",
                        help="マスタ投入後にサンプルシナリオを流し、評価まで到達した状態にする")
    args = parser.parse_args()

    if args.reset:
        if db.DB_PATH.exists():
            db.DB_PATH.unlink()
            print(f"既存DBを削除しました: {db.DB_PATH}")
        if storage.STORAGE_ROOT.exists():
            shutil.rmtree(storage.STORAGE_ROOT)
            print(f"既存ストレージを削除しました: {storage.STORAGE_ROOT}")

    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db.get_connection()
    try:
        db.init_db(conn)
        seed_masters(conn)

        if args.with_sample:
            from demo.scripts.sample_scenario import run_sample_scenario
            run_sample_scenario(conn)

        counts = oplog.table_counts(conn)
    finally:
        conn.close()

    print(f"DB: {db.DB_PATH}")
    for name, label, group in oplog.TABLES:
        print(f"  {group:6s} {label:20s} {name:20s} {counts[name]:5d}")


if __name__ == "__main__":
    main()
