"""サンプルシナリオ: セッション登録から評価まで一括で流す (seed_db.py --with-sample)。

GUI で1画面ずつ操作するのと同じ pipeline 関数を、同じ順序で呼んでいるだけ。
人がやる部分(セッション登録・生データ投入・区間の切り出しと条件付与)を先に流し、
残りはワーカー(差分検出ループ)に任せる、という本来の役割分担をそのまま再現している。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demo.app import pipeline, seed_data
from demo.scripts.worker import run_until_idle


def run_sample_scenario(conn) -> int:
    s = seed_data.SAMPLE_SESSION

    # --- ここまでが人の仕事 (docs/automation-plan.md §1) ---
    session_id, _ = pipeline.create_session(
        conn, record_date=s["record_date"], recorder_id=s["recorder_id"], setup=s["setup"],
    )
    print(f"セッションを登録しました: session_id={session_id}")

    pipeline.generate_raw_files(
        conn, session_id=session_id, sensors=s["sensors"],
        duration_minutes=s["duration_minutes"],
    )
    print(f"ダミー生データを生成しました: {', '.join(s['sensors'])} / {s['duration_minutes']}分")

    start = datetime.fromisoformat(f"{s['record_date']}T{pipeline.DEFAULT_SESSION_START}:00")
    for seg in seed_data.SAMPLE_SEGMENTS:
        started = start + timedelta(minutes=seg["offset_minutes"])
        ended = started + timedelta(minutes=seg["duration_minutes"])
        segment_id, _ = pipeline.create_segment(
            conn, session_id=session_id, label=seg["label"],
            started_at=started.isoformat(), ended_at=ended.isoformat(),
            conditions=seg["conditions"], creator_id=seg["creator_id"],
        )
        print(f"区間を登録しました: segment_id={segment_id} ({seg['label']})")

    # --- ここから先はワーカーが自動で進める ---
    for result in run_until_idle(conn):
        print(f"[ワーカー/{result['stage']}] 完了 {result['done']} 件 / 失敗 {len(result['errors'])} 件")
        for err in result["errors"]:
            print(f"  ! {err}")

    return session_id
