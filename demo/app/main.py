"""FastAPIルート層。パース→pipeline/repository呼び出し→テンプレート描画のみを行う薄いレイヤ。

書き込み系のルートはすべて _run_operation() を通す。これにより、どの操作でも
「実行前後のテーブル行数の差分」と「実際に作られた行」が同じ形式で画面に出る。
"""

import sqlite3
import urllib.parse
from pathlib import Path
from typing import Annotated, Callable

from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from demo.app import db, oplog, pipeline, repository, storage
from demo.app.oplog import ACTOR_HUMAN, ACTOR_WORKER

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="実験データ管理 POC")


def get_db():
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.close()


DbDep = Annotated[sqlite3.Connection, Depends(get_db)]


# ---------------------------------------------------------------------------
# 書き込み操作の共通ラッパ: 実行 → 差分算出 → 結果パーシャル
# ---------------------------------------------------------------------------

def _run_operation(request: Request, conn: sqlite3.Connection, operation: str,
                   func: Callable[[], oplog.ChangeSet | None]) -> Response:
    """操作を実行し、DBの変化を差分として返す。このPOCの可視化の中核。"""
    before = oplog.table_counts(conn)
    changeset, error = None, None
    try:
        changeset = func()
    except (pipeline.PipelineError, storage.StorageError, ValueError) as exc:
        error = str(exc)
        # 失敗も履歴に残す(「失敗を必ず見せる」)
        oplog.log_operation(
            conn, actor=ACTOR_HUMAN, operation=operation,
            summary=f"失敗: {error}", status="error",
        )
    after = oplog.table_counts(conn)

    return templates.TemplateResponse(
        request,
        "_op_result.html",
        {
            "error": error,
            "effects": pipeline.OPERATION_EFFECTS.get(operation),
            "changeset": changeset.to_dict() if changeset else None,
            "diff": oplog.diff_counts(before, after),
        },
    )


# ---------------------------------------------------------------------------
# ダッシュボード
# ---------------------------------------------------------------------------

@app.get("/")
def dashboard(request: Request, conn: DbDep):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "counts": oplog.counts_by_group(conn),
            "pending": repository.pending_summary(conn),
            "operations": oplog.recent_operations(conn, limit=10),
        },
    )


@app.get("/dashboard/panel")
def dashboard_panel(request: Request, conn: DbDep):
    """htmxでダッシュボード本体だけを差し替えるためのパーシャル。"""
    return templates.TemplateResponse(
        request,
        "_dashboard_panel.html",
        {
            "counts": oplog.counts_by_group(conn),
            "pending": repository.pending_summary(conn),
            "operations": oplog.recent_operations(conn, limit=10),
        },
    )


@app.post("/worker/run-once")
def worker_run_once(request: Request, conn: DbDep):
    """ワーカーを1回だけ実行する。CLIの worker.py --once と同じ関数を呼ぶ。"""
    from demo.scripts.worker import run_once

    before = oplog.table_counts(conn)
    result = run_once(conn)
    after = oplog.table_counts(conn)

    return templates.TemplateResponse(
        request,
        "_worker_result.html",
        {"result": result, "diff": oplog.diff_counts(before, after)},
    )


# ---------------------------------------------------------------------------
# 計測セッション
# ---------------------------------------------------------------------------

@app.get("/sessions")
def sessions_list(request: Request, conn: DbDep):
    sessions = repository.list_sessions(conn)
    for s in sessions:
        s["raw_count"] = len(repository.list_raw_files(conn, s["session_id"]))
        s["segment_count"] = len(repository.list_segments(conn, s["session_id"]))
        s["time_range"] = repository.session_time_range(conn, s["session_id"])
    return templates.TemplateResponse(request, "sessions_list.html", {"sessions": sessions})


@app.get("/sessions/new")
def session_new_form(request: Request, conn: DbDep):
    return templates.TemplateResponse(
        request,
        "session_new.html",
        {
            "setup_keys": repository.list_condition_keys(conn, scope="session"),
            "effects": pipeline.OPERATION_EFFECTS["create_session"],
        },
    )


@app.post("/sessions")
def session_create(
    request: Request,
    conn: DbDep,
    record_date: Annotated[str, Form()] = "",
    recorder_id: Annotated[str, Form()] = "",
    room_temperature: Annotated[str, Form()] = "",
):
    def op():
        setup = {}
        if room_temperature.strip():
            setup["room_temperature"] = float(room_temperature)
        _, cs = pipeline.create_session(
            conn, record_date=record_date, recorder_id=recorder_id, setup=setup,
        )
        return cs

    return _run_operation(request, conn, "create_session", op)


@app.get("/sessions/{session_id}")
def session_detail(request: Request, conn: DbDep, session_id: int):
    session = repository.get_session(conn, session_id)
    if session is None:
        return Response("セッションが見つかりません", status_code=404)
    return templates.TemplateResponse(
        request,
        "session_detail.html",
        {
            "session": session,
            "time_range": repository.session_time_range(conn, session_id),
            "raw_files": repository.list_raw_files(conn, session_id),
            "segments": repository.list_segments(conn, session_id),
            "sensor_types": repository.list_sensor_types(conn),
            "effects": pipeline.OPERATION_EFFECTS["generate_raw_files"],
        },
    )


@app.post("/sessions/{session_id}/generate-raw")
def session_generate_raw(
    request: Request,
    conn: DbDep,
    session_id: int,
    sensors: Annotated[list[str], Form()] = [],
    duration_minutes: Annotated[int, Form()] = 60,
):
    def op():
        if not sensors:
            raise pipeline.PipelineError("センサーを1つ以上選択してください")
        return pipeline.generate_raw_files(
            conn, session_id=session_id, sensors=sensors, duration_minutes=duration_minutes,
        )

    return _run_operation(request, conn, "generate_raw_files", op)


@app.post("/sessions/{session_id}/upload")
async def session_upload_raw(
    request: Request,
    conn: DbDep,
    session_id: int,
    sensor_type: Annotated[str, Form()] = "",
    file: UploadFile | None = None,
):
    content = await file.read() if file is not None else b""

    def op():
        if not content:
            raise pipeline.PipelineError("ファイルが選択されていません")
        return pipeline.upload_raw_file(
            conn, session_id=session_id, sensor_type=sensor_type,
            csv_text=content.decode("utf-8"),
        )

    return _run_operation(request, conn, "generate_raw_files", op)


# ---------------------------------------------------------------------------
# 区間 (切り出しと実験条件の付与)
# ---------------------------------------------------------------------------

def _condition_widgets(conn: sqlite3.Connection) -> list[dict]:
    """登録/検索フォーム共通: 条件キーごとに、value_type に応じたウィジェット情報を組み立てる。"""
    widgets = []
    for key in repository.list_condition_keys(conn, scope="segment"):
        widget = dict(key)
        if key["value_type"] in ("enum", "enum_array"):
            widget["choices"] = repository.list_condition_values_for_key(conn, key["key_id"])
        else:
            widget["choices"] = []
        if key["value_type"] == "number_array":
            widget["axes"] = repository.list_axes_for_key(conn, key["key_id"])
        else:
            widget["axes"] = []
        widgets.append(widget)
    return widgets


@app.get("/segments")
def segments_list(request: Request, conn: DbDep):
    return templates.TemplateResponse(
        request, "segments_list.html", {"segments": repository.list_segments(conn)}
    )


@app.get("/segments/new")
def segment_new_form(request: Request, conn: DbDep):
    sessions = repository.list_sessions(conn)
    for s in sessions:
        s["time_range"] = repository.session_time_range(conn, s["session_id"])
    return templates.TemplateResponse(
        request,
        "segment_register.html",
        {
            "widgets": _condition_widgets(conn),
            "sessions": sessions,
            "effects": pipeline.OPERATION_EFFECTS["create_segment"],
        },
    )


@app.post("/segments")
def segment_create(
    request: Request,
    conn: DbDep,
    session_id: Annotated[int, Form()] = 0,
    label: Annotated[str, Form()] = "",
    started_at: Annotated[str, Form()] = "",
    ended_at: Annotated[str, Form()] = "",
    creator_id: Annotated[str, Form()] = "",
    posture: Annotated[str, Form()] = "",
    subjects: Annotated[list[str], Form()] = [],
    subjects_none: Annotated[str, Form()] = "",
    room_temperature: Annotated[str, Form()] = "",
    is_night: Annotated[str, Form()] = "",
    notes_free: Annotated[str, Form()] = "",
    position_x: Annotated[str, Form()] = "",
    position_y: Annotated[str, Form()] = "",
):
    def op():
        conditions: dict = {}
        if posture:
            conditions["posture"] = posture
        if subjects:
            conditions["subjects"] = subjects
        elif subjects_none == "on":
            conditions["subjects"] = []
        if room_temperature.strip():
            conditions["room_temperature"] = float(room_temperature)
        if is_night in ("true", "false"):
            conditions["is_night"] = is_night == "true"
        if notes_free.strip():
            conditions["notes_free"] = notes_free.strip()

        # position(number_array)はx,yを1つの値として扱うため、片方だけの入力は弾く(DD-19)
        filled = [bool(position_x.strip()), bool(position_y.strip())]
        if any(filled) and not all(filled):
            raise pipeline.PipelineError("位置(x, y)は両方入力するか、両方空欄にしてください。")
        if all(filled):
            conditions["position"] = [float(position_x), float(position_y)]

        _, cs = pipeline.create_segment(
            conn, session_id=session_id, label=label or None,
            started_at=started_at, ended_at=ended_at,
            conditions=conditions, creator_id=creator_id,
        )
        return cs

    return _run_operation(request, conn, "create_segment", op)


@app.get("/segments/search")
def segment_search_form(request: Request, conn: DbDep):
    return templates.TemplateResponse(
        request,
        "segment_search.html",
        {"widgets": _condition_widgets(conn), "segments": repository.list_segments(conn)},
    )


@app.get("/segments/search/results")
def segment_search_results(
    request: Request,
    conn: DbDep,
    posture: str = "",
    subjects: str = "",
    room_temperature_min: str = "",
    room_temperature_max: str = "",
    is_night: str = "",
    notes_free: str = "",
    position_x_min: str = "",
    position_x_max: str = "",
    position_y_min: str = "",
    position_y_max: str = "",
):
    filters: dict = {}
    if posture:
        filters["posture"] = posture
    if subjects:
        filters["subjects"] = subjects
    if room_temperature_min.strip():
        filters["room_temperature_min"] = float(room_temperature_min)
    if room_temperature_max.strip():
        filters["room_temperature_max"] = float(room_temperature_max)
    if is_night in ("true", "false"):
        filters["is_night"] = is_night == "true"
    if notes_free.strip():
        filters["notes_free"] = notes_free.strip()
    if position_x_min.strip():
        filters["position_x_min"] = float(position_x_min)
    if position_x_max.strip():
        filters["position_x_max"] = float(position_x_max)
    if position_y_min.strip():
        filters["position_y_min"] = float(position_y_min)
    if position_y_max.strip():
        filters["position_y_max"] = float(position_y_max)

    return templates.TemplateResponse(
        request, "_segments_table.html", {"segments": repository.search_segments(conn, filters)}
    )


@app.get("/segments/{segment_id}")
def segment_detail(request: Request, conn: DbDep, segment_id: int):
    segment = repository.get_segment(conn, segment_id)
    if segment is None:
        return Response("区間が見つかりません", status_code=404)

    runs = repository.list_runs(conn, segment_id)
    for r in runs:
        r["metrics"] = repository.list_run_metrics(conn, r["run_id"])

    return templates.TemplateResponse(
        request,
        "segment_detail.html",
        {
            "segment": segment,
            "formatted": repository.list_formatted_data(conn, segment_id),
            "runs": runs,
            "sensor_types": repository.list_sensor_types(conn),
            "algorithms": repository.list_algorithms(conn),
            "format_effects": pipeline.OPERATION_EFFECTS["format_segment"],
            "run_effects": pipeline.OPERATION_EFFECTS["run_algorithm"],
        },
    )


@app.post("/segments/{segment_id}/format")
def segment_format(
    request: Request,
    conn: DbDep,
    segment_id: int,
    sensor_type: Annotated[str, Form()] = "",
):
    return _run_operation(
        request, conn, "format_segment",
        lambda: pipeline.format_segment(conn, segment_id=segment_id, sensor_type=sensor_type),
    )


@app.post("/segments/{segment_id}/run")
def segment_run(
    request: Request,
    conn: DbDep,
    segment_id: int,
    algorithm_name: Annotated[str, Form()] = "",
):
    def op():
        algo = repository.get_algorithm(conn, algorithm_name)
        if algo is None:
            raise pipeline.PipelineError(f"未登録のアルゴリズムです: {algorithm_name}")
        if algo["role"] == "evaluation":
            # 評価は推定runと真値runが揃っている必要があるため、揃っている組を探して実行する
            candidates = [c for c in repository.find_unevaluated(conn)
                          if c["segment_id"] == segment_id]
            if not candidates:
                raise pipeline.PipelineError(
                    "評価できる組がありません(推定runと真値runの両方が必要、または既に評価済み)"
                )
            c = candidates[0]
            return pipeline.run_evaluation(
                conn, segment_id=segment_id,
                est_run_id=c["est_run_id"], gt_run_id=c["gt_run_id"],
            )
        return pipeline.run_algorithm(conn, segment_id=segment_id, algorithm_name=algorithm_name)

    operation = "run_evaluation" if (repository.get_algorithm(conn, algorithm_name) or {}).get(
        "role") == "evaluation" else "run_algorithm"
    return _run_operation(request, conn, operation, op)


# ---------------------------------------------------------------------------
# run詳細
# ---------------------------------------------------------------------------

@app.get("/runs/{run_id}")
def run_detail(request: Request, conn: DbDep, run_id: int):
    run = repository.get_run(conn, run_id)
    if run is None:
        return Response("runが見つかりません", status_code=404)
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run": run,
            "inputs": repository.list_run_inputs(conn, run_id),
            "input_runs": repository.list_run_input_runs(conn, run_id),
            "metrics": repository.list_run_metrics(conn, run_id),
        },
    )


# ---------------------------------------------------------------------------
# 操作履歴 / ダウンロード
# ---------------------------------------------------------------------------

@app.get("/operations")
def operations(request: Request, conn: DbDep):
    return templates.TemplateResponse(
        request, "operations.html", {"operations": oplog.list_operations(conn)}
    )


@app.get("/download")
def download(uri: str):
    try:
        data = storage.get(uri)
    except storage.StorageError as exc:
        return Response(str(exc), status_code=404)
    filename = urllib.parse.quote(storage.basename(uri))
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/preview")
def preview(request: Request, uri: str, lines: int = 20):
    """ストレージ上のCSVの先頭数行を画面で確認する。"""
    try:
        text = storage.get_text(uri)
    except storage.StorageError as exc:
        return Response(str(exc), status_code=404)
    rows = text.splitlines()
    return templates.TemplateResponse(
        request,
        "_preview.html",
        {"uri": uri, "head": rows[:lines], "total": len(rows), "shown": min(lines, len(rows))},
    )
