"""操作ログとテーブル行数スナップショット (POC専用の可視化基盤)。

このPOCの主眼は「どの操作で DB の何が更新されるか」を見えるようにすること。
そのために、pipeline.py の各操作は書き込んだ行を ChangeSet に積み、最後に log_operation() で
operation_log に記録する。人の操作もワーカーの操作も同じログに残るため、
「どこまでが人で、どこからが自動か」が一覧で追える。
"""

import json
import sqlite3
from dataclasses import dataclass, field

ACTOR_HUMAN = "人"
ACTOR_WORKER = "ワーカー"

# ダッシュボードに並べるテーブル。(テーブル名, 表示名, グループ)
TABLES: list[tuple[str, str, str]] = [
    ("condition_keys", "条件キー", "マスタ"),
    ("condition_values", "条件の選択肢", "マスタ"),
    ("condition_key_axes", "条件の軸定義", "マスタ"),
    ("sensor_types", "センサー種別", "マスタ"),
    ("algorithms", "アルゴリズム", "マスタ"),
    ("recording_sessions", "計測セッション", "実体"),
    ("raw_files", "生データファイル", "実体"),
    ("segments", "切り出し区間", "実体"),
    ("formatted_data", "整形データ", "実体"),
    ("algorithm_runs", "アルゴリズム試行", "実体"),
    ("run_inputs", "試行の入力(整形データ)", "関連"),
    ("run_input_runs", "試行の入力(run間依存)", "関連"),
    ("run_metrics", "評価指標", "関連"),
    ("operation_log", "操作ログ", "POC専用"),
]

TABLE_LABELS = {name: label for name, label, _ in TABLES}


@dataclass
class Change:
    """1テーブルへの書き込み1件分。"""

    table: str
    action: str          # 'INSERT' / 'UPDATE'
    row_ids: list        # 主キーの値(複合PKの場合は文字列表現)
    detail: dict = field(default_factory=dict)   # 画面に出す代表的な列の値

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "label": TABLE_LABELS.get(self.table, self.table),
            "action": self.action,
            "row_ids": self.row_ids,
            "count": len(self.row_ids),
            "detail": self.detail,
        }


@dataclass
class ChangeSet:
    """1操作が書き込んだ行の集まり。pipeline の各関数がこれを組み立てて返す。"""

    changes: list[Change] = field(default_factory=list)
    files: list[str] = field(default_factory=list)   # ストレージに置いたURI

    def add(self, table: str, action: str, row_ids: list, **detail) -> None:
        self.changes.append(Change(table=table, action=action, row_ids=list(row_ids), detail=detail))

    def add_file(self, uri: str) -> None:
        self.files.append(uri)

    def is_empty(self) -> bool:
        return not self.changes and not self.files

    def to_dict(self) -> dict:
        return {"changes": [c.to_dict() for c in self.changes], "files": self.files}


# ---------------------------------------------------------------------------
# 行数スナップショットと差分
# ---------------------------------------------------------------------------

def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {}
    for name, _, _ in TABLES:
        counts[name] = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
    return counts


def diff_counts(before: dict[str, int], after: dict[str, int]) -> list[dict]:
    """増減のあったテーブルだけを [{table, label, before, after, delta}] で返す。"""
    rows = []
    for name, label, _ in TABLES:
        b, a = before.get(name, 0), after.get(name, 0)
        if b != a:
            rows.append({"table": name, "label": label, "before": b, "after": a, "delta": a - b})
    return rows


def counts_by_group(conn: sqlite3.Connection) -> list[dict]:
    """ダッシュボード用: グループ順にテーブル名・表示名・行数を返す。"""
    counts = table_counts(conn)
    return [
        {"table": name, "label": label, "group": group, "count": counts[name]}
        for name, label, group in TABLES
    ]


# ---------------------------------------------------------------------------
# 操作ログ
# ---------------------------------------------------------------------------

def log_operation(
    conn: sqlite3.Connection,
    *,
    actor: str,
    operation: str,
    summary: str,
    changeset: ChangeSet | None = None,
    status: str = "ok",
) -> int:
    detail = changeset.to_dict() if changeset else {"changes": [], "files": []}
    cur = conn.execute(
        """
        INSERT INTO operation_log (actor, operation, summary, detail, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (actor, operation, summary, json.dumps(detail, ensure_ascii=False), status),
    )
    conn.commit()
    return cur.lastrowid


def recent_operations(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM operation_log ORDER BY log_id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_op(r) for r in rows]


def list_operations(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM operation_log ORDER BY log_id DESC").fetchall()
    return [_row_to_op(r) for r in rows]


def _row_to_op(row: sqlite3.Row) -> dict:
    op = dict(row)
    op["detail"] = json.loads(op["detail"])
    return op
