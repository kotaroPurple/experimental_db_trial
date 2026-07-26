-- POC用SQLiteスキーマ。docs/experiment_db_ddl_v2.sql (PostgreSQL) のSQLite移植版。
--
-- 本設計の13テーブルをすべて持つ。方言上の差分は以下のとおり(demo/README.md にも記載):
--   - jsonb -> TEXT (JSON文字列)。GINインデックス/@>演算子は使わない
--   - timestamptz -> TEXT (ISO8601)。tstzrange の重なり判定(&&)は
--     「a.started_at < b.ended_at AND a.ended_at > b.started_at」の文字列比較で代替する。
--     書式を揃えている限り辞書順比較が時系列順と一致することに依存している
--   - トリガーは作らず、検証はすべて Python 側 (repository.py / pipeline.py) に一本化 (DD-13)
--   - operation_log は本設計に存在しないPOC専用の可視化テーブル

-- ============================================================
-- マスタ: 条件キー / 選択肢 / 軸
-- ============================================================
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

-- number_array型キーの軸定義 (DD-19)。
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

-- ============================================================
-- マスタ: センサー種別 / アルゴリズム (役割の区別と表記揺れ防止、DD-16)
-- ============================================================
CREATE TABLE IF NOT EXISTS sensor_types (
    sensor_type  TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'target' CHECK (role IN ('target','reference')),
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

CREATE TABLE IF NOT EXISTS algorithms (
    algorithm_name TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'estimation'
                   CHECK (role IN ('estimation','ground_truth','evaluation')),
    -- 評価runの入力となるセンサー種別/アルゴリズム。POCの自動化ワーカーが
    -- 「何を入力に何を起動すべきか」を決めるために持つ(本設計にはない補助列)
    input_sensor_type TEXT REFERENCES sensor_types(sensor_type),
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

-- ============================================================
-- 実体: 計測セッション (一日中の記録行為、DD-02)
-- ============================================================
CREATE TABLE IF NOT EXISTS recording_sessions (
    session_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    record_date TEXT NOT NULL,
    recorder_id TEXT NOT NULL,
    session_no  INTEGER NOT NULL CHECK (session_no >= 0),
    setup       TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (record_date, recorder_id, session_no)
);

-- ============================================================
-- 実体: 生データファイル (セッション : ファイル = 1 : N, DD-05)
-- started_at/ended_at はCSVの先頭・末尾から自動抽出する。手入力させない
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_files (
    raw_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES recording_sessions(session_id),
    sensor_type TEXT NOT NULL REFERENCES sensor_types(sensor_type),
    seq_no      INTEGER NOT NULL CHECK (seq_no >= 0),
    file_uri    TEXT NOT NULL,
    started_at  TEXT,
    ended_at    TEXT,
    UNIQUE (session_id, sensor_type, seq_no)
);

CREATE INDEX IF NOT EXISTS idx_raw_session ON raw_files (session_id);
CREATE INDEX IF NOT EXISTS idx_raw_time ON raw_files (session_id, sensor_type, started_at);

-- ============================================================
-- 実体: 切り出し区間 (実験条件の付与単位、DD-02)
-- 区間同士の重なりは許容する (DD-11)
-- ============================================================
CREATE TABLE IF NOT EXISTS segments (
    segment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES recording_sessions(session_id),
    label       TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    conditions  TEXT NOT NULL DEFAULT '{}',
    creator_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (started_at < ended_at)
);

CREATE INDEX IF NOT EXISTS idx_segments_session ON segments (session_id);

-- ============================================================
-- 実体: 整形データ (区間 x センサー)
-- 再整形を許容するため UNIQUE(segment_id, sensor_type) は張らない
-- ============================================================
CREATE TABLE IF NOT EXISTS formatted_data (
    formatted_id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id   INTEGER NOT NULL REFERENCES segments(segment_id),
    sensor_type  TEXT NOT NULL REFERENCES sensor_types(sensor_type),
    data_uri     TEXT NOT NULL,
    formatter_id TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_formatted_segment ON formatted_data (segment_id);

-- ============================================================
-- 実体: アルゴリズム試行 (推定・真値変換・評価をすべてこの1テーブルで表す、DD-16/DD-18)
-- segment_id は意図的な非正規化 (DD-06)
-- ============================================================
CREATE TABLE IF NOT EXISTS algorithm_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id      INTEGER NOT NULL REFERENCES segments(segment_id),
    algorithm_name  TEXT NOT NULL REFERENCES algorithms(algorithm_name),
    runner_id       TEXT NOT NULL,
    run_no          INTEGER NOT NULL CHECK (run_no >= 0),
    algo_conditions TEXT NOT NULL DEFAULT '{}',
    output_uri      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (segment_id, algorithm_name, runner_id, run_no)
);

CREATE INDEX IF NOT EXISTS idx_runs_segment ON algorithm_runs (segment_id);

-- ============================================================
-- 関連: 試行の入力 (run x 整形データ, N:M)
-- 入力が同一区間に閉じることは pipeline.py 側で検証する
-- ============================================================
CREATE TABLE IF NOT EXISTS run_inputs (
    run_id       INTEGER NOT NULL REFERENCES algorithm_runs(run_id),
    formatted_id INTEGER NOT NULL REFERENCES formatted_data(formatted_id),
    PRIMARY KEY (run_id, formatted_id)
);

-- ============================================================
-- 関連: run間の入力依存 (N:M) — 処理のDAG(系譜)を構成する (DD-18)
-- 評価runは推定run・真値runの出力を入力にとる
-- ============================================================
CREATE TABLE IF NOT EXISTS run_input_runs (
    run_id       INTEGER NOT NULL REFERENCES algorithm_runs(run_id),
    input_run_id INTEGER NOT NULL REFERENCES algorithm_runs(run_id),
    PRIMARY KEY (run_id, input_run_id),
    CHECK (run_id <> input_run_id)
);

-- ============================================================
-- 実体: 評価指標 (スカラーのみDB。非スカラー成果物は output_uri のファイル側、DD-18)
-- ============================================================
CREATE TABLE IF NOT EXISTS run_metrics (
    run_id      INTEGER NOT NULL REFERENCES algorithm_runs(run_id),
    metric_name TEXT NOT NULL,
    value       REAL NOT NULL,
    PRIMARY KEY (run_id, metric_name)
);

CREATE INDEX IF NOT EXISTS idx_metrics_name ON run_metrics (metric_name);

-- ============================================================
-- POC専用: 操作ログ (本設計には存在しない)
-- 「どの操作で、どのテーブルの何行が作られたか」を人・ワーカーの区別付きで記録する
-- ============================================================
CREATE TABLE IF NOT EXISTS operation_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL,          -- '人' または 'ワーカー'
    operation   TEXT NOT NULL,          -- 例 'generate_raw_files'
    summary     TEXT NOT NULL,          -- 人間向けの1行要約
    detail      TEXT NOT NULL DEFAULT '{}',  -- {"changes":[{"table":..,"action":..,"row_ids":[..]}], ...}
    status      TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error'))
);

CREATE INDEX IF NOT EXISTS idx_oplog_time ON operation_log (occurred_at DESC);
