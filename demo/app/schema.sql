-- SQLiteデモ用スキーマ。docs/experiment_db_ddl_v2.sql の縮小版。
-- recording_sessions/raw_files/formatted_data/algorithm_runs は作らない(demo/README.md参照)。

CREATE TABLE IF NOT EXISTS condition_keys (
    key_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    key_name     TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    value_type   TEXT NOT NULL CHECK (value_type IN ('enum','enum_array','number','number_array','text','boolean')),
    scope        TEXT NOT NULL DEFAULT 'segment' CHECK (scope IN ('session','segment','both')),
    min_value    REAL,
    max_value    REAL,
    description  TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

-- value は JSON エンコードしたスカラー値を保存する (例 '"supine"', '22.5', 'true')。
-- 素の文字列で保存すると数値/真偽値/文字列の区別が失われるため。
CREATE TABLE IF NOT EXISTS condition_values (
    value_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id       INTEGER NOT NULL REFERENCES condition_keys(key_id),
    value        TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    merged_into  INTEGER REFERENCES condition_values(value_id),
    UNIQUE (key_id, value)
);

-- number_array型キーの軸定義。docs/experiment_db_ddl_v2.sql の condition_key_axes と1:1対応(DD-19)。
-- axis_label はアプリ側でフォームのフィールド名にも使う識別子のため、短いASCII文字列を想定する。
CREATE TABLE IF NOT EXISTS condition_key_axes (
    axis_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id      INTEGER NOT NULL REFERENCES condition_keys(key_id),
    axis_index  INTEGER NOT NULL CHECK (axis_index >= 0),
    axis_label  TEXT NOT NULL,
    min_value   REAL,
    max_value   REAL,
    UNIQUE (key_id, axis_index),
    UNIQUE (key_id, axis_label)
);

-- 本設計の recording_sessions/segments 分離を省略したフラットテーブル。
CREATE TABLE IF NOT EXISTS segments (
    segment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT,
    record_date TEXT NOT NULL,
    started_at  TEXT,
    ended_at    TEXT,
    conditions  TEXT NOT NULL DEFAULT '{}',
    creator_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_condition_values_key ON condition_values(key_id);
CREATE INDEX IF NOT EXISTS idx_condition_key_axes_key ON condition_key_axes(key_id);
