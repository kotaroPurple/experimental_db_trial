"""業務操作の実体 (DD-13 ライブラリ・ファースト)。

storage / processing / repository を束ねた「1つの操作」をここに置く。
**GUI (main.py) と自動化ワーカー (worker.py) はどちらもこのモジュールの同じ関数を呼ぶ。**
自動化とは呼び出す主体が人からプログラムに変わるだけ、という DD-13 の主張をコード構造で表している。

各関数は書き込んだ行を oplog.ChangeSet に積んで返し、operation_log に記録する。
どの操作がどのテーブルを更新するかの宣言 (OPERATION_EFFECTS) も、実装とズレないよう同じファイルに置く。
"""

import sqlite3
from datetime import datetime, timedelta

from demo.app import dummy_data, oplog, processing, repository, storage
from demo.app.oplog import ACTOR_HUMAN, ChangeSet

DEFAULT_SESSION_START = "09:00"


class PipelineError(Exception):
    """業務ルール違反。本設計ではDBトリガーが担う検証をPython側で行っている (DD-09)。"""


# ---------------------------------------------------------------------------
# 事前予告: 各操作が何を更新するかの宣言
# 画面に「この操作で更新されるもの」として表示する。実装と離すとズレるためここに置く。
# ---------------------------------------------------------------------------

OPERATION_EFFECTS: dict[str, dict] = {
    "create_session": {
        "title": "計測セッションの登録",
        "storage": [],
        "tables": [
            {"table": "recording_sessions", "action": "INSERT", "rows": "1行",
             "columns": "session_id(採番), record_date, recorder_id, session_no(自動採番), setup, created_at"},
            {"table": "operation_log", "action": "INSERT", "rows": "1行", "columns": "操作の記録"},
        ],
        "note": "setup は condition_keys の scope='session'/'both' のキーだけが許可される (DD-08)。",
    },
    "generate_raw_files": {
        "title": "ダミー生データの生成と登録",
        "storage": ["s3://experiment-poc/{計測日}/{担当者}/{session_no}/raw/{センサー}/{seq}.csv"],
        "tables": [
            {"table": "raw_files", "action": "INSERT", "rows": "センサー数 × 分割ファイル数",
             "columns": "raw_id(採番), session_id, sensor_type, seq_no(自動採番), file_uri, "
                        "started_at/ended_at(CSVから自動抽出)"},
            {"table": "operation_log", "action": "INSERT", "rows": "1行", "columns": "操作の記録"},
        ],
        "note": "started_at/ended_at は手入力させず、CSVの先頭行・末尾行から抽出する (DD-05)。",
    },
    "create_segment": {
        "title": "区間の切り出しと実験条件の付与",
        "storage": [],
        "tables": [
            {"table": "segments", "action": "INSERT", "rows": "1行",
             "columns": "segment_id(採番), session_id, label, started_at, ended_at, conditions, creator_id"},
            {"table": "operation_log", "action": "INSERT", "rows": "1行", "columns": "操作の記録"},
        ],
        "note": "人の判断が本質的に必要な唯一の工程 (DD-02/DD-13)。"
                "条件はマスタ照合、区間はセッションの計測範囲内かを検証する。",
    },
    "format_segment": {
        "title": "整形 (区間で切り出す)",
        "storage": ["s3://experiment-poc/.../segments/{segment_id}/formatted/{センサー}.csv"],
        "tables": [
            {"table": "formatted_data", "action": "INSERT", "rows": "1行",
             "columns": "formatted_id(採番), segment_id, sensor_type, data_uri, formatter_id"},
            {"table": "operation_log", "action": "INSERT", "rows": "1行", "columns": "操作の記録"},
        ],
        "note": "入力の生ファイルは時刻の重なりで自動的に逆引きされる (DD-05/DD-17)。",
    },
    "run_algorithm": {
        "title": "アルゴリズム実行 (推定 / 真値変換)",
        "storage": ["s3://experiment-poc/.../segments/{segment_id}/runs/{run_id}/output.csv"],
        "tables": [
            {"table": "algorithm_runs", "action": "INSERT", "rows": "1行",
             "columns": "run_id(採番), segment_id, algorithm_name, runner_id, run_no(自動採番), "
                        "algo_conditions, output_uri"},
            {"table": "run_inputs", "action": "INSERT", "rows": "入力の整形データ数",
             "columns": "run_id, formatted_id"},
            {"table": "operation_log", "action": "INSERT", "rows": "1行", "columns": "操作の記録"},
        ],
        "note": "真値変換も推定と同じ algorithm_runs の1行として扱う (DD-16)。",
    },
    "run_evaluation": {
        "title": "評価 (推定と真値の比較)",
        "storage": ["s3://experiment-poc/.../segments/{segment_id}/runs/{run_id}/output.csv (誤差時系列)"],
        "tables": [
            {"table": "algorithm_runs", "action": "INSERT", "rows": "1行", "columns": "評価run本体"},
            {"table": "run_input_runs", "action": "INSERT", "rows": "2行",
             "columns": "run_id, input_run_id (推定runと真値runへの依存 = 処理のDAG)"},
            {"table": "run_metrics", "action": "INSERT", "rows": "指標の数",
             "columns": "run_id, metric_name, value (mae / rmse / max_abs_error / n_windows)"},
            {"table": "operation_log", "action": "INSERT", "rows": "1行", "columns": "操作の記録"},
        ],
        "note": "スカラー指標だけをDBに入れ、誤差時系列はストレージに置く (DD-18)。",
    },
}


# ---------------------------------------------------------------------------
# セッション
# ---------------------------------------------------------------------------

def create_session(conn: sqlite3.Connection, *, record_date: str, recorder_id: str,
                   setup: dict, actor: str = ACTOR_HUMAN) -> tuple[int, ChangeSet]:
    session_no = repository.next_session_no(conn, record_date, recorder_id)
    try:
        session_id = repository.create_session(
            conn, record_date=record_date, recorder_id=recorder_id,
            session_no=session_no, setup=setup,
        )
    except repository.ConditionValidationError as exc:
        raise PipelineError(str(exc)) from exc

    cs = ChangeSet()
    cs.add("recording_sessions", "INSERT", [session_id],
           record_date=record_date, recorder_id=recorder_id, session_no=session_no, setup=setup)
    oplog.log_operation(
        conn, actor=actor, operation="create_session",
        summary=f"計測セッションを登録 (session_id={session_id}, {record_date} / {recorder_id} / 第{session_no}回)",
        changeset=cs,
    )
    return session_id, cs


# ---------------------------------------------------------------------------
# 生データ
# ---------------------------------------------------------------------------

def generate_raw_files(conn: sqlite3.Connection, *, session_id: int, sensors: list[str],
                       duration_minutes: int = 60, start_time: str = DEFAULT_SESSION_START,
                       actor: str = ACTOR_HUMAN) -> ChangeSet:
    """ダミーCSVを生成してストレージに置き、raw_files に登録する。

    started_at/ended_at は生成した値を使い回さず、**書き出したCSVから読み直して**抽出する。
    実ファイルのアップロード経路と同じ処理を通すため。
    """
    session = repository.get_session(conn, session_id)
    if session is None:
        raise PipelineError(f"セッションが見つかりません: session_id={session_id}")

    start = datetime.fromisoformat(f"{session['record_date']}T{start_time}:00")
    cs = ChangeSet()
    raw_ids = []

    for sensor_type in sensors:
        if sensor_type not in dummy_data.SENSOR_SPECS:
            raise PipelineError(f"ダミー生成に未対応のセンサー種別です: {sensor_type}")

        base_seq = repository.next_seq_no(conn, session_id, sensor_type)
        for offset, csv_text in dummy_data.generate_files(sensor_type, start, duration_minutes):
            seq_no = base_seq + offset
            uri = storage.raw_uri(session["record_date"], session["recorder_id"],
                                  session["session_no"], sensor_type, seq_no)
            storage.put_text(uri, csv_text)
            cs.add_file(uri)

            started_at, ended_at = dummy_data.extract_time_range(storage.get_text(uri))
            raw_id = repository.create_raw_file(
                conn, session_id=session_id, sensor_type=sensor_type, seq_no=seq_no,
                file_uri=uri, started_at=started_at, ended_at=ended_at,
            )
            raw_ids.append(raw_id)

    files_by_sensor = {}
    for sensor_type in sensors:
        files_by_sensor[sensor_type] = len(
            [f for f in cs.files if f"/raw/{sensor_type}/" in f]
        )

    cs.add("raw_files", "INSERT", raw_ids, sensors=files_by_sensor,
           note="started_at/ended_at はCSVの先頭行・末尾行から自動抽出 (DD-05)")
    oplog.log_operation(
        conn, actor=actor, operation="generate_raw_files",
        summary=f"生データを生成・登録 (session_id={session_id}, {len(raw_ids)}ファイル)",
        changeset=cs,
    )
    return cs


def upload_raw_file(conn: sqlite3.Connection, *, session_id: int, sensor_type: str,
                    csv_text: str, actor: str = ACTOR_HUMAN) -> ChangeSet:
    """実ファイルのアップロード。ダミー生成と同じ時刻抽出経路を通る。"""
    session = repository.get_session(conn, session_id)
    if session is None:
        raise PipelineError(f"セッションが見つかりません: session_id={session_id}")

    started_at, ended_at = dummy_data.extract_time_range(csv_text)
    if started_at is None:
        raise PipelineError("CSVからデータ行を読み取れませんでした (ヘッダ行のみ、または空ファイル)")

    seq_no = repository.next_seq_no(conn, session_id, sensor_type)
    uri = storage.raw_uri(session["record_date"], session["recorder_id"],
                          session["session_no"], sensor_type, seq_no)
    storage.put_text(uri, csv_text)

    raw_id = repository.create_raw_file(
        conn, session_id=session_id, sensor_type=sensor_type, seq_no=seq_no,
        file_uri=uri, started_at=started_at, ended_at=ended_at,
    )

    cs = ChangeSet()
    cs.add_file(uri)
    cs.add("raw_files", "INSERT", [raw_id], sensor_type=sensor_type, seq_no=seq_no,
           started_at=started_at, ended_at=ended_at)
    oplog.log_operation(
        conn, actor=actor, operation="generate_raw_files",
        summary=f"生ファイルをアップロード (session_id={session_id}, {sensor_type} #{seq_no})",
        changeset=cs,
    )
    return cs


# ---------------------------------------------------------------------------
# 区間
# ---------------------------------------------------------------------------

def create_segment(conn: sqlite3.Connection, *, session_id: int, label: str | None,
                   started_at: str, ended_at: str, conditions: dict, creator_id: str,
                   actor: str = ACTOR_HUMAN) -> tuple[int, ChangeSet]:
    session = repository.get_session(conn, session_id)
    if session is None:
        raise PipelineError(f"セッションが見つかりません: session_id={session_id}")
    if started_at >= ended_at:
        raise PipelineError(f"開始時刻は終了時刻より前である必要があります: {started_at} >= {ended_at}")

    # 本設計の trg_segment_within_session 相当の検証
    s_min, s_max = repository.session_time_range(conn, session_id)
    if s_min is not None and s_max is not None and (started_at < s_min or ended_at > s_max):
        raise PipelineError(
            f"区間 [{started_at}, {ended_at}] はセッションの計測範囲 [{s_min}, {s_max}] を超えています"
        )

    try:
        segment_id = repository.create_segment(
            conn, session_id=session_id, label=label, started_at=started_at,
            ended_at=ended_at, conditions=conditions, creator_id=creator_id,
        )
    except repository.ConditionValidationError as exc:
        raise PipelineError(str(exc)) from exc

    cs = ChangeSet()
    cs.add("segments", "INSERT", [segment_id], label=label, started_at=started_at,
           ended_at=ended_at, conditions=conditions)
    oplog.log_operation(
        conn, actor=actor, operation="create_segment",
        summary=f"区間を登録 (segment_id={segment_id}, {started_at}〜{ended_at})",
        changeset=cs,
    )
    return segment_id, cs


# ---------------------------------------------------------------------------
# 整形
# ---------------------------------------------------------------------------

def format_segment(conn: sqlite3.Connection, *, segment_id: int, sensor_type: str,
                   formatter_id: str = "demo_user", actor: str = ACTOR_HUMAN) -> ChangeSet:
    segment = repository.get_segment(conn, segment_id)
    if segment is None:
        raise PipelineError(f"区間が見つかりません: segment_id={segment_id}")

    formatter = processing.FORMATTERS.get(sensor_type)
    if formatter is None:
        raise PipelineError(f"整形実装が登録されていないセンサー種別です: {sensor_type}")

    raw_files = repository.find_raw_files_overlapping(
        conn, segment["session_id"], sensor_type, segment["started_at"], segment["ended_at"]
    )
    if not raw_files:
        raise PipelineError(
            f"区間と時刻の重なる生ファイルがありません (segment_id={segment_id}, {sensor_type})"
        )

    texts = [storage.get_text(r["file_uri"]) for r in raw_files]
    result = formatter(texts, segment["started_at"], segment["ended_at"])

    uri = storage.formatted_uri(segment["record_date"], segment["recorder_id"],
                                segment["session_no"], segment_id, sensor_type)
    storage.put_text(uri, result.csv_text)

    formatted_id = repository.create_formatted_data(
        conn, segment_id=segment_id, sensor_type=sensor_type,
        data_uri=uri, formatter_id=formatter_id,
    )

    cs = ChangeSet()
    cs.add_file(uri)
    cs.add("formatted_data", "INSERT", [formatted_id], sensor_type=sensor_type,
           data_uri=uri, note=result.note,
           input_raw_ids=[r["raw_id"] for r in raw_files])
    oplog.log_operation(
        conn, actor=actor, operation="format_segment",
        summary=f"整形 (segment_id={segment_id}, {sensor_type}) — 生ファイル{len(raw_files)}件から。{result.note}",
        changeset=cs,
    )
    return cs


# ---------------------------------------------------------------------------
# アルゴリズム実行
# ---------------------------------------------------------------------------

def run_algorithm(conn: sqlite3.Connection, *, segment_id: int, algorithm_name: str,
                  runner_id: str = "demo_user", actor: str = ACTOR_HUMAN) -> ChangeSet:
    segment = repository.get_segment(conn, segment_id)
    if segment is None:
        raise PipelineError(f"区間が見つかりません: segment_id={segment_id}")

    algo = repository.get_algorithm(conn, algorithm_name)
    impl = processing.ALGORITHMS.get(algorithm_name)
    if algo is None or impl is None:
        raise PipelineError(f"未登録のアルゴリズムです: {algorithm_name}")
    if algo["role"] == "evaluation":
        raise PipelineError("評価runは run_evaluation() から実行してください")

    sensor_type = algo["input_sensor_type"]
    formatted = repository.latest_formatted_for(conn, segment_id, sensor_type)
    if formatted is None:
        raise PipelineError(
            f"入力となる整形データがありません (segment_id={segment_id}, {sensor_type})。先に整形してください"
        )
    # 本設計の trg_run_input_same_segment 相当の検証
    if formatted["segment_id"] != segment_id:
        raise PipelineError("異なる区間の整形データは入力にできません")

    result = impl["func"]({sensor_type: storage.get_text(formatted["data_uri"])})

    run_no = repository.next_run_no(conn, segment_id, algorithm_name, runner_id)
    run_id = repository.create_run(
        conn, segment_id=segment_id, algorithm_name=algorithm_name, runner_id=runner_id,
        run_no=run_no, algo_conditions={"window_seconds": processing.WINDOW_SECONDS},
        output_uri=None,
    )
    # output_uri は run_id を含むため、run作成後に確定して更新する
    uri = storage.run_output_uri(segment["record_date"], segment["recorder_id"],
                                 segment["session_no"], segment_id, run_id)
    storage.put_text(uri, result.csv_text)
    repository.set_run_output_uri(conn, run_id, uri)
    repository.add_run_input(conn, run_id, formatted["formatted_id"])

    cs = ChangeSet()
    cs.add_file(uri)
    cs.add("algorithm_runs", "INSERT", [run_id], algorithm_name=algorithm_name,
           role=algo["role"], run_no=run_no, output_uri=uri, note=result.note)
    cs.add("run_inputs", "INSERT", [f"({run_id}, {formatted['formatted_id']})"],
           formatted_id=formatted["formatted_id"], sensor_type=sensor_type)
    oplog.log_operation(
        conn, actor=actor, operation="run_algorithm",
        summary=f"{algo['display_name']} を実行 (segment_id={segment_id}, run_id={run_id})。{result.note}",
        changeset=cs,
    )
    return cs


# ---------------------------------------------------------------------------
# 評価
# ---------------------------------------------------------------------------

def run_evaluation(conn: sqlite3.Connection, *, segment_id: int, est_run_id: int, gt_run_id: int,
                   algorithm_name: str = "hr_eval", runner_id: str = "demo_user",
                   actor: str = ACTOR_HUMAN) -> ChangeSet:
    segment = repository.get_segment(conn, segment_id)
    if segment is None:
        raise PipelineError(f"区間が見つかりません: segment_id={segment_id}")

    est = repository.get_run(conn, est_run_id)
    gt = repository.get_run(conn, gt_run_id)
    if est is None or gt is None:
        raise PipelineError(f"入力となるrunが見つかりません (est={est_run_id}, gt={gt_run_id})")
    # 本設計の trg_run_input_run_same_segment 相当の検証
    if est["segment_id"] != segment_id or gt["segment_id"] != segment_id:
        raise PipelineError("異なる区間のrunは入力にできません")

    impl = processing.ALGORITHMS.get(algorithm_name)
    algo = repository.get_algorithm(conn, algorithm_name)
    if impl is None or algo is None:
        raise PipelineError(f"未登録のアルゴリズムです: {algorithm_name}")

    result = impl["func"](storage.get_text(est["output_uri"]), storage.get_text(gt["output_uri"]))

    run_no = repository.next_run_no(conn, segment_id, algorithm_name, runner_id)
    run_id = repository.create_run(
        conn, segment_id=segment_id, algorithm_name=algorithm_name, runner_id=runner_id,
        run_no=run_no, algo_conditions={"est_run_id": est_run_id, "gt_run_id": gt_run_id},
        output_uri=None,
    )
    uri = storage.run_output_uri(segment["record_date"], segment["recorder_id"],
                                 segment["session_no"], segment_id, run_id)
    storage.put_text(uri, result.csv_text)
    repository.set_run_output_uri(conn, run_id, uri)

    repository.add_run_input_run(conn, run_id, est_run_id)
    repository.add_run_input_run(conn, run_id, gt_run_id)
    for metric_name, value in result.metrics.items():
        repository.add_run_metric(conn, run_id, metric_name, value)

    cs = ChangeSet()
    cs.add_file(uri)
    cs.add("algorithm_runs", "INSERT", [run_id], algorithm_name=algorithm_name,
           role="evaluation", run_no=run_no, output_uri=uri)
    cs.add("run_input_runs", "INSERT",
           [f"({run_id}, {est_run_id})", f"({run_id}, {gt_run_id})"],
           note="評価runが推定run・真値runに依存することを表す (処理のDAG、DD-18)")
    cs.add("run_metrics", "INSERT", [f"({run_id}, {m})" for m in result.metrics],
           **{k: round(v, 4) for k, v in result.metrics.items()})

    metric_text = ", ".join(f"{k}={v:.3f}" for k, v in result.metrics.items())
    oplog.log_operation(
        conn, actor=actor, operation="run_evaluation",
        summary=f"評価を実行 (segment_id={segment_id}, run_id={run_id}) — {metric_text}",
        changeset=cs,
    )
    return cs
