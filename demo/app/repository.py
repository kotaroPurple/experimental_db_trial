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


def list_axes_for_key(conn: sqlite3.Connection, key_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM condition_key_axes WHERE key_id = ? ORDER BY axis_index", (key_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def _key_index(conn: sqlite3.Connection) -> dict[str, dict]:
    return {row["key_name"]: row for row in list_condition_keys(conn, active_only=False)}


def _axes_index(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """value_type='number_array' のキーについてのみ key_name -> 軸一覧(axis_index順) を返す。"""
    return {
        key_row["key_name"]: list_axes_for_key(conn, key_row["key_id"])
        for key_row in _key_index(conn).values()
        if key_row["value_type"] == "number_array"
    }


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

        elif value_type == "number_array":
            axes = list_axes_for_key(conn, key_row["key_id"])
            if not isinstance(v, list):
                raise ConditionValidationError(f"キー {k} は配列である必要があります: {v!r}")
            if len(v) != len(axes):
                raise ConditionValidationError(
                    f"キー {k} の配列長({len(v)})は登録軸数({len(axes)})と一致しません: {v!r}"
                )
            for axis, elem in zip(axes, v):
                if isinstance(elem, bool) or not isinstance(elem, Number):
                    raise ConditionValidationError(
                        f"キー {k} の軸 {axis['axis_label']} は数値である必要があります: {elem!r}"
                    )
                if axis["min_value"] is not None and elem < axis["min_value"]:
                    raise ConditionValidationError(
                        f"キー {k} の軸 {axis['axis_label']} の値 {elem} は下限 {axis['min_value']} 未満です"
                    )
                if axis["max_value"] is not None and elem > axis["max_value"]:
                    raise ConditionValidationError(
                        f"キー {k} の軸 {axis['axis_label']} の値 {elem} は上限 {axis['max_value']} 超過です"
                    )

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
    session_id: int,
    label: str | None,
    started_at: str,
    ended_at: str,
    conditions: dict,
    creator_id: str,
) -> int:
    validate_conditions(conn, conditions, scope="segment")
    cur = conn.execute(
        """
        INSERT INTO segments (session_id, label, started_at, ended_at, conditions, creator_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, label, started_at, ended_at, json.dumps(conditions, ensure_ascii=False), creator_id),
    )
    conn.commit()
    return cur.lastrowid


def _row_to_segment(
    row: sqlite3.Row,
    value_display: dict[tuple[str, str], str],
    keys: dict[str, dict],
    axes_by_key: dict[str, list[dict]],
) -> dict:
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
        elif value_type == "number_array":
            axes = axes_by_key.get(k, [])
            value_text = "、".join(
                f"{axis['axis_label']}={v[axis['axis_index']]}"
                for axis in axes
                if isinstance(v, list) and axis["axis_index"] < len(v)
            )
        else:
            value_text = str(v)
        display_items.append((display_name, value_text))
    seg["condition_display"] = display_items
    return seg


def list_segments(conn: sqlite3.Connection, session_id: int | None = None) -> list[dict]:
    query = """
        SELECT s.*, rs.record_date, rs.recorder_id, rs.session_no
        FROM segments s
        JOIN recording_sessions rs ON rs.session_id = s.session_id
    """
    params: list[Any] = []
    if session_id is not None:
        query += " WHERE s.session_id = ?"
        params.append(session_id)
    query += " ORDER BY s.segment_id DESC"

    rows = conn.execute(query, params).fetchall()
    value_display = _value_display_index(conn)
    keys = _key_index(conn)
    axes_by_key = _axes_index(conn)
    return [_row_to_segment(row, value_display, keys, axes_by_key) for row in rows]


def get_segment(conn: sqlite3.Connection, segment_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT s.*, rs.record_date, rs.recorder_id, rs.session_no
        FROM segments s
        JOIN recording_sessions rs ON rs.session_id = s.session_id
        WHERE s.segment_id = ?
        """,
        (segment_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_segment(row, _value_display_index(conn), _key_index(conn), _axes_index(conn))


def search_segments(conn: sqlite3.Connection, filters: dict) -> list[dict]:
    """filters: フィールド名 -> フィルタ値 の辞書。空/Noneのフィルタは無視する。

    - enum/text/boolean: 完全一致
    - number: "<key>_min" / "<key>_max" による範囲一致
    - enum_array: 指定値が配列に含まれるかどうか (包含検索)
    - number_array: "<key>_<axis_label>_min" / "_max" による軸ごとの独立した範囲一致 (AND結合)
    """
    segments = list_segments(conn)
    keys = _key_index(conn)
    axes_by_key = _axes_index(conn)

    result = []
    for seg in segments:
        conditions = seg["conditions"]
        if _matches(conditions, keys, axes_by_key, filters):
            result.append(seg)
    return result


def _matches(conditions: dict, keys: dict[str, dict], axes_by_key: dict[str, list[dict]], filters: dict) -> bool:
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

        if value_type == "number_array":
            actual = conditions.get(key_name)
            for axis in axes_by_key.get(key_name, []):
                min_v = filters.get(f"{key_name}_{axis['axis_label']}_min")
                max_v = filters.get(f"{key_name}_{axis['axis_label']}_max")
                if min_v is None and max_v is None:
                    continue
                if not isinstance(actual, list) or axis["axis_index"] >= len(actual):
                    return False
                axis_value = actual[axis["axis_index"]]
                if min_v is not None and axis_value < min_v:
                    return False
                if max_v is not None and axis_value > max_v:
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


# ---------------------------------------------------------------------------
# マスタ: センサー種別 / アルゴリズム
# ---------------------------------------------------------------------------

def list_sensor_types(conn: sqlite3.Connection, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM sensor_types"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY role, sensor_type"
    return [dict(r) for r in conn.execute(query).fetchall()]


def list_algorithms(conn: sqlite3.Connection, role: str | None = None,
                    active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM algorithms"
    clauses, params = [], []
    if active_only:
        clauses.append("is_active = 1")
    if role is not None:
        clauses.append("role = ?")
        params.append(role)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY role, algorithm_name"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_algorithm(conn: sqlite3.Connection, algorithm_name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM algorithms WHERE algorithm_name = ?", (algorithm_name,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# recording_sessions
# ---------------------------------------------------------------------------

def next_session_no(conn: sqlite3.Connection, record_date: str, recorder_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(session_no) + 1, 0) AS n FROM recording_sessions"
        " WHERE record_date = ? AND recorder_id = ?",
        (record_date, recorder_id),
    ).fetchone()
    return row["n"]


def create_session(conn: sqlite3.Connection, *, record_date: str, recorder_id: str,
                   session_no: int, setup: dict) -> int:
    validate_conditions(conn, setup, scope="session")
    cur = conn.execute(
        """
        INSERT INTO recording_sessions (record_date, recorder_id, session_no, setup)
        VALUES (?, ?, ?, ?)
        """,
        (record_date, recorder_id, session_no, json.dumps(setup, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def _row_to_session(row: sqlite3.Row) -> dict:
    s = dict(row)
    s["setup"] = json.loads(s["setup"])
    return s


def list_sessions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM recording_sessions ORDER BY record_date DESC, session_no DESC"
    ).fetchall()
    return [_row_to_session(r) for r in rows]


def get_session(conn: sqlite3.Connection, session_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM recording_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return _row_to_session(row) if row else None


def session_time_range(conn: sqlite3.Connection, session_id: int) -> tuple[str | None, str | None]:
    """そのセッションの生ファイルが覆う時刻範囲。区間の範囲チェックに使う。"""
    row = conn.execute(
        "SELECT MIN(started_at) AS s, MAX(ended_at) AS e FROM raw_files WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return row["s"], row["e"]


# ---------------------------------------------------------------------------
# raw_files
# ---------------------------------------------------------------------------

def next_seq_no(conn: sqlite3.Connection, session_id: int, sensor_type: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq_no) + 1, 0) AS n FROM raw_files"
        " WHERE session_id = ? AND sensor_type = ?",
        (session_id, sensor_type),
    ).fetchone()
    return row["n"]


def create_raw_file(conn: sqlite3.Connection, *, session_id: int, sensor_type: str, seq_no: int,
                    file_uri: str, started_at: str | None, ended_at: str | None) -> int:
    cur = conn.execute(
        """
        INSERT INTO raw_files (session_id, sensor_type, seq_no, file_uri, started_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, sensor_type, seq_no, file_uri, started_at, ended_at),
    )
    conn.commit()
    return cur.lastrowid


def list_raw_files(conn: sqlite3.Connection, session_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM raw_files"
    params: list[Any] = []
    if session_id is not None:
        query += " WHERE session_id = ?"
        params.append(session_id)
    query += " ORDER BY sensor_type, seq_no"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def find_raw_files_overlapping(conn: sqlite3.Connection, session_id: int, sensor_type: str,
                               started_at: str, ended_at: str) -> list[dict]:
    """区間と時刻が重なる生ファイルを逆引きする (DD-05/DD-17)。

    本設計では tstzrange(...) && tstzrange(...) にあたる部分。SQLiteには範囲型がないため、
    ISO8601の書式を揃えたうえで文字列比較で重なりを判定している。
    """
    rows = conn.execute(
        """
        SELECT * FROM raw_files
        WHERE session_id = ? AND sensor_type = ?
          AND started_at < ? AND ended_at > ?
        ORDER BY seq_no
        """,
        (session_id, sensor_type, ended_at, started_at),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# formatted_data
# ---------------------------------------------------------------------------

def create_formatted_data(conn: sqlite3.Connection, *, segment_id: int, sensor_type: str,
                          data_uri: str, formatter_id: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO formatted_data (segment_id, sensor_type, data_uri, formatter_id)
        VALUES (?, ?, ?, ?)
        """,
        (segment_id, sensor_type, data_uri, formatter_id),
    )
    conn.commit()
    return cur.lastrowid


def list_formatted_data(conn: sqlite3.Connection, segment_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM formatted_data"
    params: list[Any] = []
    if segment_id is not None:
        query += " WHERE segment_id = ?"
        params.append(segment_id)
    query += " ORDER BY formatted_id"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def latest_formatted_for(conn: sqlite3.Connection, segment_id: int, sensor_type: str) -> dict | None:
    """同一区間・同一センサーの再整形を許容しているため、最新版を created_at で選ぶ。"""
    row = conn.execute(
        """
        SELECT * FROM formatted_data
        WHERE segment_id = ? AND sensor_type = ?
        ORDER BY created_at DESC, formatted_id DESC LIMIT 1
        """,
        (segment_id, sensor_type),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# algorithm_runs とその関連
# ---------------------------------------------------------------------------

def next_run_no(conn: sqlite3.Connection, segment_id: int, algorithm_name: str, runner_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(run_no) + 1, 0) AS n FROM algorithm_runs"
        " WHERE segment_id = ? AND algorithm_name = ? AND runner_id = ?",
        (segment_id, algorithm_name, runner_id),
    ).fetchone()
    return row["n"]


def create_run(conn: sqlite3.Connection, *, segment_id: int, algorithm_name: str, runner_id: str,
               run_no: int, algo_conditions: dict, output_uri: str | None) -> int:
    cur = conn.execute(
        """
        INSERT INTO algorithm_runs
            (segment_id, algorithm_name, runner_id, run_no, algo_conditions, output_uri)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (segment_id, algorithm_name, runner_id, run_no,
         json.dumps(algo_conditions, ensure_ascii=False), output_uri),
    )
    conn.commit()
    return cur.lastrowid


def set_run_output_uri(conn: sqlite3.Connection, run_id: int, output_uri: str) -> None:
    conn.execute("UPDATE algorithm_runs SET output_uri = ? WHERE run_id = ?", (output_uri, run_id))
    conn.commit()


def add_run_input(conn: sqlite3.Connection, run_id: int, formatted_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO run_inputs (run_id, formatted_id) VALUES (?, ?)",
        (run_id, formatted_id),
    )
    conn.commit()


def add_run_input_run(conn: sqlite3.Connection, run_id: int, input_run_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO run_input_runs (run_id, input_run_id) VALUES (?, ?)",
        (run_id, input_run_id),
    )
    conn.commit()


def add_run_metric(conn: sqlite3.Connection, run_id: int, metric_name: str, value: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO run_metrics (run_id, metric_name, value) VALUES (?, ?, ?)",
        (run_id, metric_name, value),
    )
    conn.commit()


def _row_to_run(row: sqlite3.Row) -> dict:
    r = dict(row)
    r["algo_conditions"] = json.loads(r["algo_conditions"])
    return r


def list_runs(conn: sqlite3.Connection, segment_id: int | None = None) -> list[dict]:
    query = """
        SELECT ar.*, a.display_name AS algorithm_display_name, a.role
        FROM algorithm_runs ar
        JOIN algorithms a ON a.algorithm_name = ar.algorithm_name
    """
    params: list[Any] = []
    if segment_id is not None:
        query += " WHERE ar.segment_id = ?"
        params.append(segment_id)
    query += " ORDER BY ar.run_id"
    return [_row_to_run(r) for r in conn.execute(query, params).fetchall()]


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT ar.*, a.display_name AS algorithm_display_name, a.role
        FROM algorithm_runs ar
        JOIN algorithms a ON a.algorithm_name = ar.algorithm_name
        WHERE ar.run_id = ?
        """,
        (run_id,),
    ).fetchone()
    return _row_to_run(row) if row else None


def list_run_inputs(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ri.formatted_id, f.sensor_type, f.data_uri
        FROM run_inputs ri
        JOIN formatted_data f ON f.formatted_id = ri.formatted_id
        WHERE ri.run_id = ?
        ORDER BY ri.formatted_id
        """,
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_run_input_runs(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT rir.input_run_id, ar.algorithm_name, a.display_name, a.role, ar.output_uri
        FROM run_input_runs rir
        JOIN algorithm_runs ar ON ar.run_id = rir.input_run_id
        JOIN algorithms a ON a.algorithm_name = ar.algorithm_name
        WHERE rir.run_id = ?
        ORDER BY rir.input_run_id
        """,
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_run_metrics(conn: sqlite3.Connection, run_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM run_metrics"
    params: list[Any] = []
    if run_id is not None:
        query += " WHERE run_id = ?"
        params.append(run_id)
    query += " ORDER BY run_id, metric_name"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


# ---------------------------------------------------------------------------
# 差分検出 (DD-03: 存在=状態。LEFT JOIN ... IS NULL で「やるべきこと」を導出する)
# ---------------------------------------------------------------------------

def find_unformatted(conn: sqlite3.Connection) -> list[dict]:
    """時刻の重なる生ファイルがあるのに整形データがない (区間 x センサー) の組。

    全センサーとの直積ではなく、そのセッションに実際に生データがあるセンサーだけが対象になる。
    """
    rows = conn.execute(
        """
        SELECT DISTINCT s.segment_id, r.sensor_type
        FROM segments s
        JOIN raw_files r
          ON r.session_id = s.session_id
         AND r.started_at < s.ended_at
         AND r.ended_at   > s.started_at
        LEFT JOIN formatted_data f
          ON f.segment_id = s.segment_id AND f.sensor_type = r.sensor_type
        WHERE f.formatted_id IS NULL
        ORDER BY s.segment_id, r.sensor_type
        """
    ).fetchall()
    return [dict(r) for r in rows]


def find_unprocessed(conn: sqlite3.Connection) -> list[dict]:
    """入力の整形データが揃っているのに、そのアルゴリズムのrunがない (区間 x アルゴリズム) の組。"""
    rows = conn.execute(
        """
        SELECT DISTINCT f.segment_id, a.algorithm_name, a.role
        FROM algorithms a
        JOIN formatted_data f ON f.sensor_type = a.input_sensor_type
        LEFT JOIN algorithm_runs ar
          ON ar.segment_id = f.segment_id AND ar.algorithm_name = a.algorithm_name
        WHERE a.is_active = 1
          AND a.role IN ('estimation','ground_truth')
          AND ar.run_id IS NULL
        ORDER BY f.segment_id, a.algorithm_name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def find_unevaluated(conn: sqlite3.Connection) -> list[dict]:
    """推定runと真値runが揃っていて、両方を入力に持つ評価runがまだない組 (DD-18)。"""
    rows = conn.execute(
        """
        SELECT est.segment_id, est.run_id AS est_run_id, gt.run_id AS gt_run_id
        FROM algorithm_runs est
        JOIN algorithms a_est
          ON a_est.algorithm_name = est.algorithm_name AND a_est.role = 'estimation'
        JOIN algorithm_runs gt
          ON gt.segment_id = est.segment_id
        JOIN algorithms a_gt
          ON a_gt.algorithm_name = gt.algorithm_name AND a_gt.role = 'ground_truth'
        WHERE NOT EXISTS (
            SELECT 1 FROM algorithm_runs ev
            JOIN algorithms a_ev
              ON a_ev.algorithm_name = ev.algorithm_name AND a_ev.role = 'evaluation'
            JOIN run_input_runs ri1 ON ri1.run_id = ev.run_id AND ri1.input_run_id = est.run_id
            JOIN run_input_runs ri2 ON ri2.run_id = ev.run_id AND ri2.input_run_id = gt.run_id
        )
        ORDER BY est.segment_id, est.run_id, gt.run_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def pending_summary(conn: sqlite3.Connection) -> dict:
    """ダッシュボードの「残作業」表示用。"""
    return {
        "unformatted": find_unformatted(conn),
        "unprocessed": find_unprocessed(conn),
        "unevaluated": find_unevaluated(conn),
    }
