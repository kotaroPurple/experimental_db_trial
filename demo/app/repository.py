"""ライブラリ層 (DD-13): DBアクセスと検証ロジックを純粋関数として提供する。

FastAPIのルート層(main.py)はこのモジュールの関数を呼び出すだけの薄いラッパとする。
"""

import json
import sqlite3
from numbers import Number
from typing import Any


class ConditionValidationError(Exception):
    """docs/experiment_db_ddl_v2.sql の validate_jsonb_against_master() 相当の検証エラー。"""


# ---------------------------------------------------------------------------
# マスタ参照
# ---------------------------------------------------------------------------

def list_condition_keys(conn: sqlite3.Connection, scope: str | None = None, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM condition_keys"
    clauses = []
    params: list[Any] = []
    if active_only:
        clauses.append("is_active = 1")
    if scope is not None:
        clauses.append("(scope = ? OR scope = 'both')")
        params.append(scope)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY key_id"
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def list_condition_values_for_key(conn: sqlite3.Connection, key_id: int, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM condition_values WHERE key_id = ?"
    params: list[Any] = [key_id]
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY value_id"
    rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    for row in rows:
        row["value"] = json.loads(row["value"])
    return rows


def _key_index(conn: sqlite3.Connection) -> dict[str, dict]:
    return {row["key_name"]: row for row in list_condition_keys(conn, active_only=False)}


def _value_display_index(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """(key_name, json値文字列) -> display_name。有効な値のみ。"""
    rows = conn.execute(
        """
        SELECT ck.key_name AS key_name, cv.value AS value, cv.display_name AS display_name
        FROM condition_values cv
        JOIN condition_keys ck ON ck.key_id = cv.key_id
        WHERE cv.is_active = 1
        """
    ).fetchall()
    return {(row["key_name"], row["value"]): row["display_name"] for row in rows}


# ---------------------------------------------------------------------------
# 検証 (validate_jsonb_against_master 相当)
# ---------------------------------------------------------------------------

def validate_conditions(conn: sqlite3.Connection, conditions: dict, scope: str = "segment") -> None:
    keys = _key_index(conn)
    for k, v in conditions.items():
        key_row = keys.get(k)
        if key_row is None or not key_row["is_active"] or key_row["scope"] not in (scope, "both"):
            raise ConditionValidationError(f"未登録の条件キー (scope={scope}): {k}")

        value_type = key_row["value_type"]

        if value_type == "enum":
            if not _value_registered(conn, k, v):
                raise ConditionValidationError(f"キー {k} に未登録の値: {v!r}")

        elif value_type == "enum_array":
            if not isinstance(v, list):
                raise ConditionValidationError(f"キー {k} は配列である必要があります: {v!r}")
            for elem in v:
                if not _value_registered(conn, k, elem):
                    raise ConditionValidationError(f"キー {k} の配列に未登録の値が含まれます: {elem!r}")

        elif value_type == "number":
            if isinstance(v, bool) or not isinstance(v, Number):
                raise ConditionValidationError(f"キー {k} は数値である必要があります: {v!r}")
            if key_row["min_value"] is not None and v < key_row["min_value"]:
                raise ConditionValidationError(f"キー {k} の値 {v} は下限 {key_row['min_value']} 未満です")
            if key_row["max_value"] is not None and v > key_row["max_value"]:
                raise ConditionValidationError(f"キー {k} の値 {v} は上限 {key_row['max_value']} 超過です")

        elif value_type == "boolean":
            if not isinstance(v, bool):
                raise ConditionValidationError(f"キー {k} は真偽値である必要があります: {v!r}")

        elif value_type == "text":
            if not isinstance(v, str):
                raise ConditionValidationError(f"キー {k} は文字列である必要があります: {v!r}")


def _value_registered(conn: sqlite3.Connection, key_name: str, value: Any) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM condition_values cv
        JOIN condition_keys ck ON ck.key_id = cv.key_id
        WHERE ck.key_name = ? AND cv.value = ? AND cv.is_active = 1
        """,
        (key_name, json.dumps(value, ensure_ascii=False)),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# segments CRUD
# ---------------------------------------------------------------------------

def create_segment(
    conn: sqlite3.Connection,
    *,
    label: str | None,
    record_date: str,
    started_at: str | None,
    ended_at: str | None,
    conditions: dict,
    creator_id: str,
) -> int:
    validate_conditions(conn, conditions, scope="segment")
    cur = conn.execute(
        """
        INSERT INTO segments (label, record_date, started_at, ended_at, conditions, creator_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (label, record_date, started_at, ended_at, json.dumps(conditions, ensure_ascii=False), creator_id),
    )
    conn.commit()
    return cur.lastrowid


def _row_to_segment(row: sqlite3.Row, value_display: dict[tuple[str, str], str], keys: dict[str, dict]) -> dict:
    seg = dict(row)
    conditions = json.loads(seg["conditions"])
    seg["conditions"] = conditions
    display_items = []
    for k, v in conditions.items():
        key_row = keys.get(k)
        display_name = key_row["display_name"] if key_row else k
        value_type = key_row["value_type"] if key_row else None
        if value_type == "enum_array":
            value_display_names = [
                value_display.get((k, json.dumps(el, ensure_ascii=False)), str(el)) for el in v
            ]
            value_text = "、".join(value_display_names) if value_display_names else "(0件)"
        elif value_type == "enum":
            value_text = value_display.get((k, json.dumps(v, ensure_ascii=False)), str(v))
        elif value_type == "boolean":
            value_text = "はい" if v else "いいえ"
        else:
            value_text = str(v)
        display_items.append((display_name, value_text))
    seg["condition_display"] = display_items
    return seg


def list_segments(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM segments ORDER BY created_at DESC, segment_id DESC").fetchall()
    value_display = _value_display_index(conn)
    keys = _key_index(conn)
    return [_row_to_segment(row, value_display, keys) for row in rows]


def search_segments(conn: sqlite3.Connection, filters: dict) -> list[dict]:
    """filters: フィールド名 -> フィルタ値 の辞書。空/Noneのフィルタは無視する。

    - enum/text/boolean: 完全一致
    - number: "<key>_min" / "<key>_max" による範囲一致
    - enum_array: 指定値が配列に含まれるかどうか (包含検索)
    """
    segments = list_segments(conn)
    keys = _key_index(conn)

    result = []
    for seg in segments:
        conditions = seg["conditions"]
        if _matches(conditions, keys, filters):
            result.append(seg)
    return result


def _matches(conditions: dict, keys: dict[str, dict], filters: dict) -> bool:
    for key_name, key_row in keys.items():
        value_type = key_row["value_type"]

        if value_type == "number":
            min_v = filters.get(f"{key_name}_min")
            max_v = filters.get(f"{key_name}_max")
            if min_v is None and max_v is None:
                continue
            actual = conditions.get(key_name)
            if actual is None:
                return False
            if min_v is not None and actual < min_v:
                return False
            if max_v is not None and actual > max_v:
                return False
            continue

        filter_v = filters.get(key_name)
        if filter_v is None or filter_v == "":
            continue

        actual = conditions.get(key_name)
        if value_type == "enum_array":
            if actual is None or filter_v not in actual:
                return False
        elif value_type == "text":
            if actual is None or filter_v.lower() not in actual.lower():
                return False
        elif value_type == "boolean":
            if actual is None or bool(actual) != bool(filter_v):
                return False
        else:  # enum
            if actual is None or actual != filter_v:
                return False

    return True
