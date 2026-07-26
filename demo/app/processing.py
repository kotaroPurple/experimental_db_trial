"""整形とアルゴリズムのレジストリ (DD-14)。

「センサー種別 → 整形実装」「アルゴリズム名 → 実装」の対応表を持ち、実装を1つ増やしても
pipeline.py / worker.py の骨格には触れない構造にしている。

処理内容自体はPOC用に素朴だが、ダミーではあっても実際に計算している:
  hr_estimate       radar の波形からゼロ交差を数えて心拍数を推定する
  truth_hr_from_psg psg の値を分ごとに平均して真値とする
  hr_eval           推定と真値を突き合わせて MAE / RMSE を出す (DD-18)
"""

import io
import math
from dataclasses import dataclass, field
from datetime import datetime

from demo.app.dummy_data import parse_rows

WINDOW_SECONDS = 60


@dataclass
class ProcessResult:
    csv_text: str
    metrics: dict[str, float] = field(default_factory=dict)   # 評価run以外は空
    note: str = ""


# ---------------------------------------------------------------------------
# 整形: 区間で切り出す
# ---------------------------------------------------------------------------

def format_by_time_range(raw_csv_texts: list[str], started_at: str, ended_at: str) -> ProcessResult:
    """複数の生CSVを結合し、区間内の行だけを残す。「決まった処理」の代表例。"""
    header = None
    kept: list[str] = []
    total = 0
    for text in raw_csv_texts:
        lines = text.splitlines()
        if not lines:
            continue
        if header is None:
            header = lines[0]
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            total += 1
            ts = line.split(",", 1)[0]
            # 書式が揃っているため文字列比較で時刻比較になる
            if started_at <= ts <= ended_at:
                kept.append(line)

    kept.sort()
    buf = io.StringIO()
    buf.write((header or "timestamp,value") + "\n")
    for line in kept:
        buf.write(line + "\n")
    return ProcessResult(
        csv_text=buf.getvalue(),
        note=f"生データ {total} 行のうち区間内の {len(kept)} 行を採用",
    )


FORMATTERS = {
    "radar": format_by_time_range,
    "psg_reference": format_by_time_range,
}


# ---------------------------------------------------------------------------
# 窓分割のユーティリティ
# ---------------------------------------------------------------------------

def _windows(rows: list[tuple[str, float]]) -> dict[str, list[tuple[datetime, float]]]:
    """行を分単位の窓にまとめる。キーは窓の開始時刻(秒精度のISO8601)。"""
    buckets: dict[str, list[tuple[datetime, float]]] = {}
    for ts, value in rows:
        dt = datetime.fromisoformat(ts)
        key = dt.replace(second=0, microsecond=0).isoformat()
        buckets.setdefault(key, []).append((dt, value))
    return buckets


# ---------------------------------------------------------------------------
# アルゴリズム
# ---------------------------------------------------------------------------

def estimate_hr_from_radar(formatted: dict[str, str]) -> ProcessResult:
    """radar の波形から、窓ごとの上向きゼロ交差数を数えて心拍数を推定する。"""
    rows = parse_rows(formatted["radar"])
    buf = io.StringIO()
    buf.write("window_start,estimated_hr\n")

    buckets = _windows(rows)
    count = 0
    for key in sorted(buckets):
        samples = buckets[key]
        if len(samples) < 2:
            continue
        crossings = 0
        for (_, prev), (_, cur) in zip(samples, samples[1:]):
            if prev < 0.0 <= cur:
                crossings += 1
        span = (samples[-1][0] - samples[0][0]).total_seconds()
        if span <= 0:
            continue
        buf.write(f"{key},{crossings / span * 60.0:.2f}\n")
        count += 1

    return ProcessResult(csv_text=buf.getvalue(), note=f"{count} 窓ぶんの推定値を算出")


def truth_hr_from_psg(formatted: dict[str, str]) -> ProcessResult:
    """psg の値を窓ごとに平均して真値とする (DD-16: 真値変換もアルゴリズム試行の一種)。"""
    rows = parse_rows(formatted["psg_reference"])
    buf = io.StringIO()
    buf.write("window_start,true_hr\n")

    buckets = _windows(rows)
    for key in sorted(buckets):
        values = [v for _, v in buckets[key]]
        buf.write(f"{key},{sum(values) / len(values):.2f}\n")

    return ProcessResult(csv_text=buf.getvalue(), note=f"{len(buckets)} 窓ぶんの真値を算出")


def evaluate_hr(estimation_csv: str, ground_truth_csv: str) -> ProcessResult:
    """推定と真値を窓で突き合わせ、誤差時系列(ファイル)とスカラー指標(DB)を返す。

    DD-18 の「集計に使う数値はDB、眺める成果物はストレージ」をそのまま実装している。
    """
    est = {ts: v for ts, v in parse_rows(estimation_csv)}
    gt = {ts: v for ts, v in parse_rows(ground_truth_csv)}
    shared = sorted(set(est) & set(gt))

    buf = io.StringIO()
    buf.write("window_start,estimated_hr,true_hr,error\n")
    errors = []
    for key in shared:
        error = est[key] - gt[key]
        errors.append(error)
        buf.write(f"{key},{est[key]:.2f},{gt[key]:.2f},{error:.2f}\n")

    if errors:
        metrics = {
            "mae": sum(abs(e) for e in errors) / len(errors),
            "rmse": math.sqrt(sum(e * e for e in errors) / len(errors)),
            "max_abs_error": max(abs(e) for e in errors),
            "n_windows": float(len(errors)),
        }
    else:
        metrics = {"n_windows": 0.0}

    return ProcessResult(
        csv_text=buf.getvalue(),
        metrics=metrics,
        note=f"{len(shared)} 窓で比較",
    )


# アルゴリズム名 → 実装。role は algorithms マスタと一致させること。
ALGORITHMS = {
    "hr_estimate": {"role": "estimation", "func": estimate_hr_from_radar},
    "truth_hr_from_psg": {"role": "ground_truth", "func": truth_hr_from_psg},
    "hr_eval": {"role": "evaluation", "func": evaluate_hr},
}
