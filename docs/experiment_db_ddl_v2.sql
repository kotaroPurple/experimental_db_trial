-- 実験データベース DDL v2 (PostgreSQL)
-- 変更点: 「実験試行」を「計測セッション」と「切り出し区間」に分離
--   - recording_sessions: 一日中回す計測行為。条件は付かない(セットアップ情報のみ)
--   - segments: 計測からの切り出し区間。実験条件はここに付与される
--   - formatted_data: 区間 x センサーの実体ファイル
-- ER図 er.md に対応

-- ============================================================
-- マスタ: 条件キー / 選択肢
-- ============================================================
CREATE TABLE condition_keys (
    key_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key_name     text NOT NULL UNIQUE,
    display_name text NOT NULL,
    value_type   text NOT NULL CHECK (value_type IN ('enum','enum_array','number','number_array','text','boolean')),
    scope        text NOT NULL DEFAULT 'segment'
                 CHECK (scope IN ('session','segment','both')),
                 -- session: 計測セットアップ用 / segment: 実験条件用
    min_value    numeric,   -- value_type='number' の範囲下限 (NULL=制限なし)
    max_value    numeric,   -- value_type='number' の範囲上限 (NULL=制限なし)
    description  text,
    is_active    boolean NOT NULL DEFAULT true
);

CREATE TABLE condition_values (
    value_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key_id       bigint NOT NULL REFERENCES condition_keys(key_id),
    value        jsonb NOT NULL,   -- 型を保持して列挙 (例: "supine", 500, true)
    display_name text NOT NULL,
    is_active    boolean NOT NULL DEFAULT true,
    merged_into  bigint REFERENCES condition_values(value_id),
    UNIQUE (key_id, value)
);

-- value_type='number_array' の軸定義(軸数・ラベル・軸ごとの範囲、DD-19)。
-- condition_values と異なり列挙ではなく連続値の型パラメータを持つため、is_active/merged_into は設けない。
-- axis_label はアプリ側でフォームのフィールド名にも使う識別子のため、短いASCII文字列を想定する。
CREATE TABLE condition_key_axes (
    axis_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key_id     bigint NOT NULL REFERENCES condition_keys(key_id),
    axis_index int NOT NULL CHECK (axis_index >= 0),   -- 0始まり。配列内の位置と一致
    axis_label text NOT NULL,                          -- 例 'x'
    min_value  numeric,   -- 軸ごとの下限 (NULL=制限なし)
    max_value  numeric,   -- 軸ごとの上限 (NULL=制限なし)
    UNIQUE (key_id, axis_index),
    UNIQUE (key_id, axis_label)
);

-- ============================================================
-- マスタ: センサー種別 / アルゴリズム (役割の区別と表記揺れ防止)
-- ============================================================
CREATE TABLE sensor_types (
    sensor_type  text PRIMARY KEY,              -- 例: 'radar', 'psg_reference'
    display_name text NOT NULL,
    role         text NOT NULL DEFAULT 'target'
                 CHECK (role IN ('target','reference')),
                 -- target: 評価対象のセンサー / reference: 真値取得デバイス
    is_active    boolean NOT NULL DEFAULT true
);

CREATE TABLE algorithms (
    algorithm_name text PRIMARY KEY,            -- 例: 'hr_music', 'truth_hr_from_psg'
    display_name   text NOT NULL,
    role           text NOT NULL DEFAULT 'estimation'
                   CHECK (role IN ('estimation','ground_truth','evaluation')),
                   -- estimation: 評価対象の推定 / ground_truth: 生データ->真値の変換
                   -- evaluation: 推定と真値の比較・誤差指標の算出
    is_active      boolean NOT NULL DEFAULT true
);

-- ============================================================
-- 実体: 計測セッション (一日中の記録行為)
-- ============================================================
CREATE TABLE recording_sessions (
    session_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_date date NOT NULL,
    recorder_id text NOT NULL,
    session_no  int  NOT NULL CHECK (session_no >= 0),
    setup       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- センサー配置・機材構成など
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (record_date, recorder_id, session_no)
);

-- ============================================================
-- 実体: 生データファイル (セッション : ファイル = 1 : N, N>=1)
-- センサーごとに時間順の複数CSVが生成される
-- ============================================================
CREATE TABLE raw_files (
    raw_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id  bigint NOT NULL REFERENCES recording_sessions(session_id),
    sensor_type text NOT NULL REFERENCES sensor_types(sensor_type),
    seq_no      int  NOT NULL CHECK (seq_no >= 0),
    file_uri    text NOT NULL,
    started_at  timestamptz,   -- ファイル先頭のデータ時刻
    ended_at    timestamptz,   -- ファイル末尾のデータ時刻
    CHECK (started_at IS NULL OR ended_at IS NULL OR started_at < ended_at),
    UNIQUE (session_id, sensor_type, seq_no)
);

CREATE INDEX idx_raw_session ON raw_files (session_id);
-- 区間 -> 該当ファイルの逆引き用
CREATE INDEX idx_raw_time ON raw_files (session_id, sensor_type, started_at);

-- ============================================================
-- 実体: 切り出し区間 (実験条件の付与単位)
-- ============================================================
CREATE TABLE segments (
    segment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id bigint NOT NULL REFERENCES recording_sessions(session_id),
    started_at timestamptz NOT NULL,
    ended_at   timestamptz NOT NULL,
    conditions jsonb NOT NULL DEFAULT '{}'::jsonb,   -- マスタ検証あり
    creator_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (started_at < ended_at)
);

CREATE INDEX idx_segments_session ON segments (session_id);
CREATE INDEX idx_segments_conditions ON segments
    USING GIN (conditions jsonb_path_ops);

-- ============================================================
-- 実体: 整形データ (区間 x センサー, 0個以上)
-- ============================================================
CREATE TABLE formatted_data (
    formatted_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    segment_id   bigint NOT NULL REFERENCES segments(segment_id),
    sensor_type  text NOT NULL REFERENCES sensor_types(sensor_type),
    data_uri     text NOT NULL,
    formatter_id text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
    -- 同一区間・同一センサーの再整形(バージョン)を許容するため
    -- UNIQUE(segment_id, sensor_type) は張らない。最新版は created_at で判断
);

CREATE INDEX idx_formatted_segment ON formatted_data (segment_id);

-- ============================================================
-- 実体: アルゴリズム試行 (区間 : 試行 = 1 : K, K>=0)
-- ============================================================
CREATE TABLE algorithm_runs (
    run_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    segment_id      bigint NOT NULL REFERENCES segments(segment_id),
    algorithm_name  text NOT NULL REFERENCES algorithms(algorithm_name),
    runner_id       text NOT NULL,
    run_no          int  NOT NULL CHECK (run_no >= 0),
    algo_conditions jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_uri      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (segment_id, algorithm_name, runner_id, run_no)
);

CREATE INDEX idx_runs_segment ON algorithm_runs (segment_id);
CREATE INDEX idx_runs_conditions ON algorithm_runs
    USING GIN (algo_conditions jsonb_path_ops);

-- ============================================================
-- 関連: アルゴリズム試行の入力 (N:M)
-- ============================================================
CREATE TABLE run_inputs (
    run_id       bigint NOT NULL REFERENCES algorithm_runs(run_id),
    formatted_id bigint NOT NULL REFERENCES formatted_data(formatted_id),
    PRIMARY KEY (run_id, formatted_id)
);

-- ============================================================
-- 関連: run間の入力依存 (N:M) — 処理のDAG(系譜)を構成する
-- 評価runは推定run・真値runの出力を入力にとる
-- ============================================================
CREATE TABLE run_input_runs (
    run_id       bigint NOT NULL REFERENCES algorithm_runs(run_id),
    input_run_id bigint NOT NULL REFERENCES algorithm_runs(run_id),
    PRIMARY KEY (run_id, input_run_id),
    CHECK (run_id <> input_run_id)
);

CREATE OR REPLACE FUNCTION check_run_input_run_same_segment() RETURNS trigger AS $$
BEGIN
    IF (SELECT segment_id FROM algorithm_runs WHERE run_id = NEW.run_id)
       <> (SELECT segment_id FROM algorithm_runs WHERE run_id = NEW.input_run_id)
    THEN
        RAISE EXCEPTION 'run_input_runs: 異なる区間のrunは入力にできません (run_id=%, input_run_id=%)',
            NEW.run_id, NEW.input_run_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_run_input_run_same_segment
    BEFORE INSERT OR UPDATE ON run_input_runs
    FOR EACH ROW EXECUTE FUNCTION check_run_input_run_same_segment();

-- ============================================================
-- 実体: 評価指標 (スカラー指標のみDBに置き、横断集計を可能にする)
-- 非スカラーの成果物(誤差時系列、図表)は output_uri のファイル側
-- ============================================================
CREATE TABLE run_metrics (
    run_id      bigint NOT NULL REFERENCES algorithm_runs(run_id),
    metric_name text   NOT NULL,       -- 例: 'mae', 'rmse', 'corr'
    value       double precision NOT NULL,
    PRIMARY KEY (run_id, metric_name)
);

CREATE INDEX idx_metrics_name ON run_metrics (metric_name);

-- ============================================================
-- トリガー: run_inputs は同一区間内に閉じること
-- ============================================================
CREATE OR REPLACE FUNCTION check_run_input_same_segment() RETURNS trigger AS $$
BEGIN
    IF (SELECT segment_id FROM algorithm_runs WHERE run_id = NEW.run_id)
       <> (SELECT segment_id FROM formatted_data WHERE formatted_id = NEW.formatted_id)
    THEN
        RAISE EXCEPTION 'run_inputs: 異なる区間のデータは入力にできません (run_id=%, formatted_id=%)',
            NEW.run_id, NEW.formatted_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_run_input_same_segment
    BEFORE INSERT OR UPDATE ON run_inputs
    FOR EACH ROW EXECUTE FUNCTION check_run_input_same_segment();

-- ============================================================
-- トリガー: 区間はセッションの計測時間内に収まること(生データがある範囲)
-- 注: raw_files の時刻が未登録(NULL)の間はチェックをスキップ
-- ============================================================
CREATE OR REPLACE FUNCTION check_segment_within_session() RETURNS trigger AS $$
DECLARE
    s_min timestamptz;
    s_max timestamptz;
BEGIN
    SELECT min(started_at), max(ended_at) INTO s_min, s_max
    FROM raw_files WHERE session_id = NEW.session_id;

    IF s_min IS NOT NULL AND s_max IS NOT NULL
       AND (NEW.started_at < s_min OR NEW.ended_at > s_max)
    THEN
        RAISE EXCEPTION '区間 [%, %] はセッションの計測範囲 [%, %] を超えています',
            NEW.started_at, NEW.ended_at, s_min, s_max;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_segment_within_session
    BEFORE INSERT OR UPDATE ON segments
    FOR EACH ROW EXECUTE FUNCTION check_segment_within_session();

-- ============================================================
-- トリガー: 条件のマスタ検証 (segments.conditions / sessions.setup 共用)
-- ============================================================
CREATE OR REPLACE FUNCTION validate_jsonb_against_master(cond jsonb, target_scope text)
RETURNS void AS $$
DECLARE
    k          text;
    v          jsonb;          -- 型を保ったまま扱う (jsonb_each_text は使わない)
    ck_row     condition_keys%ROWTYPE;
    axis_row   record;
    axis_count int;
BEGIN
    FOR k, v IN SELECT key, value FROM jsonb_each(cond)
    LOOP
        SELECT * INTO ck_row FROM condition_keys
        WHERE key_name = k AND is_active
          AND (scope = target_scope OR scope = 'both');

        IF NOT FOUND THEN
            RAISE EXCEPTION '未登録の条件キー (scope=%): %', target_scope, k;
        END IF;

        CASE ck_row.value_type
        WHEN 'enum' THEN
            -- jsonb同士の等価比較: 数値はnumericとして比較されるため 100 = 100.0 も正しく一致
            IF NOT EXISTS (
                SELECT 1 FROM condition_values cv
                WHERE cv.key_id = ck_row.key_id AND cv.value = v AND cv.is_active
            ) THEN
                RAISE EXCEPTION 'キー % に未登録の値: %', k, v;
            END IF;

        WHEN 'enum_array' THEN
            -- 可変個数の選択 (例: subjects=["S001","S002"], 0人は空配列 [])
            IF jsonb_typeof(v) <> 'array' THEN
                RAISE EXCEPTION 'キー % は配列である必要があります: %', k, v;
            END IF;
            IF EXISTS (
                SELECT 1 FROM jsonb_array_elements(v) AS e(elem)
                WHERE NOT EXISTS (
                    SELECT 1 FROM condition_values cv
                    WHERE cv.key_id = ck_row.key_id AND cv.value = e.elem AND cv.is_active
                )
            ) THEN
                RAISE EXCEPTION 'キー % の配列に未登録の値が含まれます: %', k, v;
            END IF;

        WHEN 'number' THEN
            IF jsonb_typeof(v) <> 'number' THEN
                RAISE EXCEPTION 'キー % は数値である必要があります: %', k, v;
            END IF;
            IF ck_row.min_value IS NOT NULL AND (v)::numeric < ck_row.min_value THEN
                RAISE EXCEPTION 'キー % の値 % は下限 % 未満です', k, v, ck_row.min_value;
            END IF;
            IF ck_row.max_value IS NOT NULL AND (v)::numeric > ck_row.max_value THEN
                RAISE EXCEPTION 'キー % の値 % は上限 % 超過です', k, v, ck_row.max_value;
            END IF;

        WHEN 'number_array' THEN
            -- 多次元の数値条件 (例: position=[2.5, 1.0])。軸ごとの定義は condition_key_axes (DD-19)。
            IF jsonb_typeof(v) <> 'array' THEN
                RAISE EXCEPTION 'キー % は配列である必要があります: %', k, v;
            END IF;

            SELECT count(*) INTO axis_count
            FROM condition_key_axes WHERE key_id = ck_row.key_id;

            IF jsonb_array_length(v) <> axis_count THEN
                RAISE EXCEPTION 'キー % の配列長(%)は登録軸数(%)と一致しません: %',
                    k, jsonb_array_length(v), axis_count, v;
            END IF;

            FOR axis_row IN
                SELECT axis_index, axis_label, min_value, max_value
                FROM condition_key_axes
                WHERE key_id = ck_row.key_id
                ORDER BY axis_index
            LOOP
                IF jsonb_typeof(v -> axis_row.axis_index) <> 'number' THEN
                    RAISE EXCEPTION 'キー % の軸 %(位置%)は数値である必要があります: %',
                        k, axis_row.axis_label, axis_row.axis_index, v -> axis_row.axis_index;
                END IF;
                IF axis_row.min_value IS NOT NULL
                   AND (v -> axis_row.axis_index)::numeric < axis_row.min_value THEN
                    RAISE EXCEPTION 'キー % の軸 % の値 % は下限 % 未満です',
                        k, axis_row.axis_label, v -> axis_row.axis_index, axis_row.min_value;
                END IF;
                IF axis_row.max_value IS NOT NULL
                   AND (v -> axis_row.axis_index)::numeric > axis_row.max_value THEN
                    RAISE EXCEPTION 'キー % の軸 % の値 % は上限 % 超過です',
                        k, axis_row.axis_label, v -> axis_row.axis_index, axis_row.max_value;
                END IF;
            END LOOP;

        WHEN 'boolean' THEN
            IF jsonb_typeof(v) <> 'boolean' THEN
                RAISE EXCEPTION 'キー % は真偽値である必要があります: %', k, v;
            END IF;

        WHEN 'text' THEN
            IF jsonb_typeof(v) <> 'string' THEN
                RAISE EXCEPTION 'キー % は文字列である必要があります: %', k, v;
            END IF;
        END CASE;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_validate_segment_conditions() RETURNS trigger AS $$
BEGIN
    PERFORM validate_jsonb_against_master(NEW.conditions, 'segment');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_segments_conditions
    BEFORE INSERT OR UPDATE ON segments
    FOR EACH ROW EXECUTE FUNCTION trg_validate_segment_conditions();

CREATE OR REPLACE FUNCTION trg_validate_session_setup() RETURNS trigger AS $$
BEGIN
    PERFORM validate_jsonb_against_master(NEW.setup, 'session');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sessions_setup
    BEFORE INSERT OR UPDATE ON recording_sessions
    FOR EACH ROW EXECUTE FUNCTION trg_validate_session_setup();
