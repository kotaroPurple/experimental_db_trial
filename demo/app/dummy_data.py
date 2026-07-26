"""合成センサーデータの生成 (POC用)。

実センサーがない状態で全工程を通すためのダミー生成器。radar と psg_reference は
**同一の「真の心拍数」から作られる**ため、後段の推定 (hr_estimate) と真値 (truth_hr_from_psg) を
突き合わせた評価 (hr_eval) が意味のある数値になる。

- 真の心拍数: HR(t) = 60 + 8*sin(2πt / 600)  (10分周期でゆっくり変動)
- radar        : 10Hz。HR に対応する周波数の正弦波 + ノイズ (推定はここから心拍を復元する)
- psg_reference: 1Hz。真の心拍数そのもの + わずかなノイズ

DD-05 の「1センサーでも時間分割された複数CSVが生成される」を実際に踏むため、
指定分数ごとにファイルを分割する。
"""

import io
import math
import random
from datetime import datetime, timedelta

# センサー種別ごとの生成パラメータ
SENSOR_SPECS = {
    "radar": {"hz": 10.0, "column": "amplitude"},
    "psg_reference": {"hz": 1.0, "column": "heart_rate"},
}

CHUNK_MINUTES = 20      # このぶんごとに1ファイルへ分割する
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"


def true_hr(elapsed_sec: float) -> float:
    """真の心拍数 [bpm]。radar/psg 双方がこれを基に作られる。"""
    return 60.0 + 8.0 * math.sin(2.0 * math.pi * elapsed_sec / 600.0)


def format_ts(dt: datetime) -> str:
    """ミリ秒までのISO8601。書式を固定しているため辞書順比較が時系列順と一致する。"""
    return dt.strftime(TS_FORMAT)[:-3]


def generate_files(sensor_type: str, start: datetime, duration_minutes: int,
                   chunk_minutes: int = CHUNK_MINUTES) -> list[tuple[int, str]]:
    """(seq_no, CSVテキスト) のリストを返す。"""
    if sensor_type not in SENSOR_SPECS:
        raise ValueError(f"未対応のセンサー種別です: {sensor_type}")

    spec = SENSOR_SPECS[sensor_type]
    hz = spec["hz"]
    column = spec["column"]
    rng = random.Random(f"{sensor_type}:{start.isoformat()}")   # 再実行しても同じデータになる

    files: list[tuple[int, str]] = []
    total_sec = duration_minutes * 60
    chunk_sec = chunk_minutes * 60
    step = 1.0 / hz
    phase = 0.0     # radar用: 瞬時周波数を積分した位相

    seq_no = 0
    elapsed = 0.0
    while elapsed < total_sec:
        chunk_end = min(elapsed + chunk_sec, total_sec)
        buf = io.StringIO()
        buf.write(f"timestamp,{column}\n")

        while elapsed < chunk_end:
            ts = format_ts(start + timedelta(seconds=elapsed))
            hr = true_hr(elapsed)

            if sensor_type == "radar":
                # 心拍数に対応する瞬時周波数 [Hz] で位相を進める
                phase += 2.0 * math.pi * (hr / 60.0) * step
                value = math.sin(phase) + rng.gauss(0.0, 0.03)
            else:
                value = hr + rng.gauss(0.0, 0.2)

            buf.write(f"{ts},{value:.4f}\n")
            elapsed += step

        files.append((seq_no, buf.getvalue()))
        seq_no += 1

    return files


# ---------------------------------------------------------------------------
# CSVの読み取り (時刻の自動抽出、DD-05)
# ---------------------------------------------------------------------------

def parse_rows(csv_text: str) -> list[tuple[str, float]]:
    """(timestamp, value) のリスト。ヘッダ行は読み飛ばす。"""
    rows = []
    for i, line in enumerate(csv_text.splitlines()):
        line = line.strip()
        if not line or i == 0:
            continue
        ts, _, value = line.partition(",")
        rows.append((ts, float(value)))
    return rows


def extract_time_range(csv_text: str) -> tuple[str | None, str | None]:
    """CSVの先頭行・末尾行から時刻範囲を取り出す。

    DD-05 が「手入力は誤りが必発のため自動抽出を必須」としている部分の実体。
    利用者に started_at/ended_at を入力させないための関数。
    """
    rows = parse_rows(csv_text)
    if not rows:
        return None, None
    return rows[0][0], rows[-1][0]
