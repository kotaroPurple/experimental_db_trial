"""FastAPIルート層。パース→repository呼び出し→テンプレート描画のみを行う薄いレイヤ。"""

import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from demo.app import db, repository

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="実験条件デモ")


def get_db():
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.close()


DbDep = Annotated[sqlite3.Connection, Depends(get_db)]


def _condition_widgets(conn: sqlite3.Connection) -> list[dict]:
    """登録/検索フォーム共通: 条件キーごとに、value_type に応じたウィジェット情報を組み立てる。"""
    widgets = []
    for key in repository.list_condition_keys(conn, scope="segment"):
        widget = dict(key)
        if key["value_type"] in ("enum", "enum_array"):
            widget["choices"] = repository.list_condition_values_for_key(conn, key["key_id"])
        else:
            widget["choices"] = []
        widgets.append(widget)
    return widgets


@app.get("/")
def root():
    return RedirectResponse(url="/segments")


# ---------------------------------------------------------------------------
# 閲覧
# ---------------------------------------------------------------------------

@app.get("/segments")
def segments_list(request: Request, conn: DbDep):
    segments = repository.list_segments(conn)
    return templates.TemplateResponse(
        request, "segments_list.html", {"segments": segments}
    )


# ---------------------------------------------------------------------------
# 登録
# ---------------------------------------------------------------------------

@app.get("/segments/new")
def segment_new_form(request: Request, conn: DbDep):
    return templates.TemplateResponse(
        request, "segment_register.html", {"widgets": _condition_widgets(conn), "result": None}
    )


@app.post("/segments")
def segment_create(
    request: Request,
    conn: DbDep,
    label: Annotated[str, Form()] = "",
    record_date: Annotated[str, Form()] = "",
    started_at: Annotated[str, Form()] = "",
    ended_at: Annotated[str, Form()] = "",
    creator_id: Annotated[str, Form()] = "",
    posture: Annotated[str, Form()] = "",
    subjects: Annotated[list[str], Form()] = [],
    subjects_none: Annotated[str, Form()] = "",
    room_temperature: Annotated[str, Form()] = "",
    is_night: Annotated[str, Form()] = "",
    notes_free: Annotated[str, Form()] = "",
):
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

    error = None
    segment_id = None
    try:
        segment_id = repository.create_segment(
            conn,
            label=label or None,
            record_date=record_date,
            started_at=started_at or None,
            ended_at=ended_at or None,
            conditions=conditions,
            creator_id=creator_id,
        )
    except repository.ConditionValidationError as exc:
        error = str(exc)

    return templates.TemplateResponse(
        request,
        "_register_result.html",
        {"error": error, "segment_id": segment_id},
    )


# ---------------------------------------------------------------------------
# 検索
# ---------------------------------------------------------------------------

@app.get("/segments/search")
def segment_search_form(request: Request, conn: DbDep):
    segments = repository.list_segments(conn)
    return templates.TemplateResponse(
        request,
        "segment_search.html",
        {"widgets": _condition_widgets(conn), "segments": segments},
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

    segments = repository.search_segments(conn, filters)
    return templates.TemplateResponse(
        request, "_segments_table.html", {"segments": segments}
    )
