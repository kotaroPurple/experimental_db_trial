# テーブル一覧・関連リファレンス

各テーブルが何を記録し、他のどのテーブルとどうつながっているかを一覧するための技術者向けリファレンス。
「なぜそう設計したか」は `design-decisions.md`、ER図全体(mermaid)は `er.md`、業務フローは
`sequence_diagrams.md` を参照。本書はPostgreSQL本設計(`experiment_db_ddl_v2.sql`)のみを対象とし、
ローカルデモ(`demo/`)の簡易スキーマは対象外。

## 全体像

データは「マスタ(統制語彙)」と「ライフサイクル実体」の2系統からなる。

1. `recording_sessions`(1日の計測) → `raw_files`(生データファイル、1:N)
2. `recording_sessions` → `segments`(切り出し区間、0:N)。実験条件はここに付く
3. `segments` → `formatted_data`(区間×センサーの整形データ、0:N)
4. `segments` → `algorithm_runs`(アルゴリズム試行、0:N)。入力は `run_inputs`(整形データ)や
   `run_input_runs`(他run。推定/真値/評価の依存関係)で表され、結果指標は `run_metrics` に入る

`condition_keys` / `condition_values` / `condition_key_axes`(実験条件・セットアップの統制語彙)と
`sensor_types` / `algorithms`(センサー種別・アルゴリズムの表記統制)が、この一連の実体を横断的に統制する。

---

## マスタ(統制語彙)

### condition_keys

**役割**: 実験条件・セットアップとして使用可能なキーを事前登録するマスタ(DD-08)。

**主なカラム**:
- `key_id` bigint PK
- `key_name` text NOT NULL UNIQUE — 業務キー(例 `posture`)
- `display_name` text NOT NULL — 表示名(例「姿勢」)
- `value_type` text NOT NULL CHECK(`enum`/`enum_array`/`number`/`number_array`/`text`/`boolean`) —
  検証方式を決める型システム(DD-08b, DD-19)
- `scope` text NOT NULL DEFAULT `'segment'` CHECK(`session`/`segment`/`both`) — `setup`用か`conditions`用か
  共用か(DD-08a)
- `min_value` / `max_value` numeric — `value_type='number'`の範囲(DD-08b)
- `description` text
- `is_active` boolean NOT NULL DEFAULT true — 論理削除(DD-10)

**つながり**:
- `condition_values.key_id` → 本テーブル(N:1)。enum/enum_array型の選択肢
- `condition_key_axes.key_id` → 本テーブル(N:1)。number_array型の軸定義(DD-19)
- FKではないが、`segments.conditions` / `recording_sessions.setup` の jsonb キーは
  `validate_jsonb_against_master()` トリガー経由で `key_name` を参照する(DD-08, DD-09)

### condition_values

**役割**: `enum` / `enum_array` 型キーの選択肢を列挙するマスタ(DD-08)。

**主なカラム**:
- `value_id` bigint PK
- `key_id` bigint NOT NULL FK → `condition_keys.key_id`
- `value` jsonb NOT NULL — 型を保持したまま列挙(例 `"supine"`, `500`, `true`。DD-08b)
- `display_name` text NOT NULL
- `is_active` boolean NOT NULL DEFAULT true — 論理削除(DD-10)
- `merged_into` bigint FK → `condition_values.value_id`(自己参照、NULL可)— 表記揺れの統合先(DD-10)
- UNIQUE(`key_id`, `value`)

**つながり**:
- `key_id` → `condition_keys`(N:1)
- `merged_into` → `condition_values` 自身(0..1:1)。統合済みの値から統合先を辿る

### condition_key_axes

**役割**: `value_type='number_array'` キーの軸(次元)ごとのラベル・範囲を定義するマスタ(DD-19、例:
被験者位置の x, y)。列挙ではなく連続値の型パラメータを持つため、`condition_values` とは別テーブル。

**主なカラム**:
- `axis_id` bigint PK
- `key_id` bigint NOT NULL FK → `condition_keys.key_id`
- `axis_index` int NOT NULL CHECK(`>= 0`) — 0始まり、配列内の位置と一致
- `axis_label` text NOT NULL — 例 `'x'`(アプリのフォームフィールド名にも使う識別子)
- `min_value` / `max_value` numeric — 軸ごとの範囲
- UNIQUE(`key_id`, `axis_index`), UNIQUE(`key_id`, `axis_label`)

**つながり**:
- `key_id` → `condition_keys`(N:1)

### sensor_types

**役割**: センサー種別を事前登録し、表記揺れを防ぐとともに役割(評価対象/真値取得)を区別するマスタ(DD-16)。

**主なカラム**:
- `sensor_type` text PK — サロゲートキーを持たない業務キーそのままのPK(例 `radar`, `psg_reference`)
- `display_name` text NOT NULL
- `role` text NOT NULL DEFAULT `'target'` CHECK(`target`/`reference`) — `target`=評価対象センサー /
  `reference`=真値取得デバイス
- `is_active` boolean NOT NULL DEFAULT true

**つながり**:
- `raw_files.sensor_type` → 本テーブル(N:1)
- `formatted_data.sensor_type` → 本テーブル(N:1)

### algorithms

**役割**: アルゴリズムを事前登録し、役割(推定/真値変換/評価)を区別するマスタ(DD-16, DD-18)。

**主なカラム**:
- `algorithm_name` text PK
- `display_name` text NOT NULL
- `role` text NOT NULL DEFAULT `'estimation'` CHECK(`estimation`/`ground_truth`/`evaluation`) —
  評価対象の推定 / 生データ→真値の変換 / 推定と真値の比較
- `is_active` boolean NOT NULL DEFAULT true

**つながり**:
- `algorithm_runs.algorithm_name` → 本テーブル(N:1)

---

## ライフサイクル実体

### recording_sessions

**役割**: 1日の連続した計測行為そのもの。実験条件は付かず、センサー配置などのセットアップ情報のみを持つ
(DD-02)。

**主なカラム**:
- `session_id` bigint PK
- `record_date` date NOT NULL
- `recorder_id` text NOT NULL
- `session_no` int NOT NULL CHECK(`>= 0`) — 同一日・同一担当者内の連番
- `setup` jsonb NOT NULL DEFAULT `{}` — マスタ検証トリガーあり(scope=`session`)
- `created_at` timestamptz NOT NULL DEFAULT `now()`
- UNIQUE(`record_date`, `recorder_id`, `session_no`) — 業務キー(DD-12。PKはサロゲートキー)

**つながり**:
- `raw_files.session_id` → 本テーブル(N:1、1回のセッションに複数ファイル)
- `segments.session_id` → 本テーブル(N:1、0個以上の区間切り出し)
- トリガー `trg_sessions_setup`: `setup` の内容を `condition_keys`/`condition_values` に対して検証

### raw_files

**役割**: センサーごとに時間順で生成される生データCSVを1ファイル1行で記録する(DD-05)。

**主なカラム**:
- `raw_id` bigint PK
- `session_id` bigint NOT NULL FK → `recording_sessions.session_id`
- `sensor_type` text NOT NULL FK → `sensor_types.sensor_type`
- `seq_no` int NOT NULL CHECK(`>= 0`) — 同一センサー内のファイル連番
- `file_uri` text NOT NULL — 実体はストレージ側、DBはURIのみ(DD-04)
- `started_at` / `ended_at` timestamptz — ファイルの実データ時刻範囲(ツールが自動抽出。DD-05)
- CHECK(`started_at < ended_at` または どちらかNULL)
- UNIQUE(`session_id`, `sensor_type`, `seq_no`)
- INDEX `idx_raw_session`(`session_id`)、`idx_raw_time`(`session_id, sensor_type, started_at`)

**つながり**:
- `session_id` → `recording_sessions`(N:1)
- `sensor_type` → `sensor_types`(N:1)
- `segments` との直接のFKはない。区間との対応は `tstzrange(started_at, ended_at) &&` による
  **時刻の重なり判定**で論理的に導出する(DD-05, DD-17)。トリガー `trg_segment_within_session` が
  `segments` 側からこのテーブルの `min(started_at)`/`max(ended_at)` を参照して範囲チェックに使う

### segments

**役割**: 計測からの切り出し区間。実験条件(jsonb)はこのテーブルに付与される、条件付与の単位(DD-02)。

**主なカラム**:
- `segment_id` bigint PK
- `session_id` bigint NOT NULL FK → `recording_sessions.session_id`
- `started_at` / `ended_at` timestamptz NOT NULL, CHECK(`started_at < ended_at`)
- `conditions` jsonb NOT NULL DEFAULT `{}` — マスタ検証トリガーあり(scope=`segment`)
- `creator_id` text NOT NULL
- `created_at` timestamptz NOT NULL DEFAULT `now()`
- INDEX `idx_segments_session`、GIN `idx_segments_conditions`(`conditions jsonb_path_ops`、`@>`検索用)
- 重なり禁止のEXCLUDE制約は**意図的に設けていない**(区間の重複を許容。DD-11)

**つながり**:
- `session_id` → `recording_sessions`(N:1)
- `formatted_data.segment_id` → 本テーブル(N:1、区間×センサーで0個以上)
- `algorithm_runs.segment_id` → 本テーブル(N:1、0回以上の試行)
- トリガー `trg_segment_within_session`: `started_at`/`ended_at` が `raw_files` の計測範囲内かチェック
- トリガー `trg_segments_conditions`: `conditions` を `condition_keys`/`condition_values`/
  `condition_key_axes` に対して検証(`validate_jsonb_against_master`)

### formatted_data

**役割**: 区間×センサーごとの整形済みデータファイル(DD-01)。

**主なカラム**:
- `formatted_id` bigint PK
- `segment_id` bigint NOT NULL FK → `segments.segment_id`
- `sensor_type` text NOT NULL FK → `sensor_types.sensor_type`
- `data_uri` text NOT NULL
- `formatter_id` text NOT NULL
- `created_at` timestamptz NOT NULL DEFAULT `now()`
- UNIQUE制約は**意図的に設けていない**(同一区間・同一センサーの再整形/バージョン違いを許容し、
  最新版は `created_at` で判断)
- INDEX `idx_formatted_segment`(`segment_id`)

**つながり**:
- `segment_id` → `segments`(N:1)
- `sensor_type` → `sensor_types`(N:1)
- `run_inputs.formatted_id` → 本テーブル(N:1。1件の整形データが複数runの入力になりうる)

### algorithm_runs

**役割**: アルゴリズムの1回の試行。推定・真値変換・評価はいずれも `algorithms.role` の違いとして
同じこのテーブルの1レコードで表される(DD-16, DD-18)。

**主なカラム**:
- `run_id` bigint PK
- `segment_id` bigint NOT NULL FK → `segments.segment_id` — **意図的な非正規化**(`run_inputs`経由でも
  辿れるが、条件からの検索を1段のJOINで済ませるために直接持つ。DD-06)
- `algorithm_name` text NOT NULL FK → `algorithms.algorithm_name`
- `runner_id` text NOT NULL
- `run_no` int NOT NULL CHECK(`>= 0`) — 試行番号(0始まり)
- `algo_conditions` jsonb NOT NULL DEFAULT `{}` — アルゴリズムのパラメータ。**注意**: `segments.conditions`
  と異なり、現行DDLでは `algo_conditions` に対するマスタ検証トリガーは設定されていない
- `output_uri` text — 出力データURI(NULL可)
- `created_at` timestamptz NOT NULL DEFAULT `now()`
- UNIQUE(`segment_id`, `algorithm_name`, `runner_id`, `run_no`) — 業務キー(DD-12)
- INDEX `idx_runs_segment`、GIN `idx_runs_conditions`(`algo_conditions jsonb_path_ops`)

**つながり**:
- `segment_id` → `segments`(N:1、非正規化。DD-06)
- `algorithm_name` → `algorithms`(N:1)
- `run_inputs.run_id` → 本テーブル(N:1)
- `run_input_runs.run_id` / `input_run_id` → 本テーブル(それぞれN:1。run同士の自己参照N:M関連)
- `run_metrics.run_id` → 本テーブル(N:1)
- トリガー `trg_run_input_same_segment`(`run_inputs`側)・`trg_run_input_run_same_segment`
  (`run_input_runs`側)が、入力元が同一 `segment_id` に閉じていることをこのテーブル経由で検証する

### run_inputs

**役割**: アルゴリズム試行の入力となる整形データの組を表す中間テーブル(N:M。DD-06)。

**主なカラム**:
- `run_id` bigint NOT NULL FK → `algorithm_runs.run_id`
- `formatted_id` bigint NOT NULL FK → `formatted_data.formatted_id`
- PRIMARY KEY(`run_id`, `formatted_id`) — 複合PK(サロゲートキーなし)

**つながり**:
- `run_id` → `algorithm_runs`(N:1)
- `formatted_id` → `formatted_data`(N:1)
- トリガー `trg_run_input_same_segment`: `formatted_data.segment_id` と `algorithm_runs.segment_id` が
  一致しない組み合わせを拒否(「入力は同一区間内に閉じる」という業務制約、DD-06)

### run_input_runs

**役割**: run同士の入力依存を表す中間テーブル(N:M)。評価runが推定run・真値runの出力を入力にとる関係を
表し、処理全体を「整形データを葉、runをノードとするDAG(処理系譜)」にする(DD-18)。

**主なカラム**:
- `run_id` bigint NOT NULL FK → `algorithm_runs.run_id`
- `input_run_id` bigint NOT NULL FK → `algorithm_runs.run_id`
- PRIMARY KEY(`run_id`, `input_run_id`)
- CHECK(`run_id <> input_run_id`) — 自分自身を入力にはできない

**つながり**:
- `run_id` / `input_run_id` → ともに `algorithm_runs`(N:1)。`algorithm_runs` を自己参照するN:M関連
- トリガー `trg_run_input_run_same_segment`: 2つのrunの `segment_id` が一致しない組み合わせを拒否

### run_metrics

**役割**: MAE・RMSEなどのスカラー評価指標をDBに格納し、姿勢別・アルゴリズム別などの横断集計をSQLで
可能にする(誤差時系列や図表などの非スカラー成果物は従来通り `output_uri` のファイル側。DD-18)。

**主なカラム**:
- `run_id` bigint NOT NULL FK → `algorithm_runs.run_id`
- `metric_name` text NOT NULL — 例 `mae`, `rmse`, `corr`
- `value` double precision NOT NULL
- PRIMARY KEY(`run_id`, `metric_name`)
- INDEX `idx_metrics_name`(`metric_name`)

**つながり**:
- `run_id` → `algorithm_runs`(N:1)

---

## 関係サマリ表(外部キー)

| 参照元テーブル.カラム | 参照先テーブル.カラム | カーディナリティ | 意味 |
|---|---|---|---|
| `condition_values.key_id` | `condition_keys.key_id` | N:1 | enum/enum_array型キーの選択肢 |
| `condition_values.merged_into` | `condition_values.value_id`(自己参照) | 0..1:1 | 表記揺れの統合先(DD-10) |
| `condition_key_axes.key_id` | `condition_keys.key_id` | N:1 | number_array型キーの軸定義(DD-19) |
| `raw_files.session_id` | `recording_sessions.session_id` | N:1 | 計測1回の生ファイル群 |
| `raw_files.sensor_type` | `sensor_types.sensor_type` | N:1 | センサー種別の表記統制 |
| `segments.session_id` | `recording_sessions.session_id` | N:1 | セッションからの切り出し区間 |
| `formatted_data.segment_id` | `segments.segment_id` | N:1 | 区間×センサーの整形データ |
| `formatted_data.sensor_type` | `sensor_types.sensor_type` | N:1 | センサー種別の表記統制 |
| `algorithm_runs.segment_id` | `segments.segment_id` | N:1 | 意図的な非正規化(DD-06) |
| `algorithm_runs.algorithm_name` | `algorithms.algorithm_name` | N:1 | アルゴリズム名の表記統制 |
| `run_inputs.run_id` | `algorithm_runs.run_id` | N:1 | run側 |
| `run_inputs.formatted_id` | `formatted_data.formatted_id` | N:1 | 入力データ側 |
| `run_input_runs.run_id` | `algorithm_runs.run_id` | N:1 | 評価run等の側 |
| `run_input_runs.input_run_id` | `algorithm_runs.run_id` | N:1 | 入力となるrun側(推定/真値) |
| `run_metrics.run_id` | `algorithm_runs.run_id` | N:1 | 指標の算出元run |

## 関係サマリ表(トリガーによる非FKの整合性制約)

| トリガー | 対象テーブル | 検証内容 |
|---|---|---|
| `trg_sessions_setup` | `recording_sessions` | `setup` の各キー・値を `condition_keys`/`condition_values`(scope=session/both)に対して検証 |
| `trg_segments_conditions` | `segments` | `conditions` の各キー・値を `condition_keys`/`condition_values`/`condition_key_axes`(scope=segment/both)に対して検証 |
| `trg_segment_within_session` | `segments` | `started_at`/`ended_at` が同一 `session_id` の `raw_files` の計測範囲内に収まっているか |
| `trg_run_input_same_segment` | `run_inputs` | `formatted_data.segment_id` と `algorithm_runs.segment_id` の一致 |
| `trg_run_input_run_same_segment` | `run_input_runs` | 2つの `algorithm_runs.segment_id` の一致 |
