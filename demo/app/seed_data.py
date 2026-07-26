"""POC用のシードデータ。

posture / subjects は docs/design-decisions.md (DD-08, DD-15) / docs/er.md の例をそのまま流用している。
room_temperature / is_night / notes_free は number/boolean/text の value_type を実演するために
このデモ用に追加したものであり、本設計docに登場する項目ではない。
position は value_type='number_array'(DD-19)の実演で、本設計(condition_key_axes)と1:1で対応させている。

seed_db.py は既定ではこのファイルのマスタのみを投入する。実体テーブル(セッション・生ファイル・区間…)は
空のまま始まり、GUI から操作するたびに埋まっていく様子を見られるようにするため。
--with-sample を付けると SAMPLE_SESSION / SAMPLE_SEGMENTS のシナリオが pipeline 経由で流し込まれる。
"""

CONDITION_KEYS = [
    {
        "key_name": "posture",
        "display_name": "姿勢",
        "value_type": "enum",
        "scope": "segment",
        "min_value": None,
        "max_value": None,
        "description": "実験時の姿勢",
        "values": [
            ("supine", "仰臥位"),
            ("lateral", "側臥位"),
            ("prone", "腹臥位"),
        ],
        "axes": [],
    },
    {
        "key_name": "subjects",
        "display_name": "被験者",
        "value_type": "enum_array",
        "scope": "segment",
        "min_value": None,
        "max_value": None,
        "description": "被験者ID一覧。0人は空配列",
        "values": [
            ("S001", "被験者S001"),
            ("S002", "被験者S002"),
            ("S003", "被験者S003"),
        ],
        "axes": [],
    },
    {
        "key_name": "room_temperature",
        "display_name": "室温",
        "value_type": "number",
        "scope": "both",
        "min_value": 15,
        "max_value": 35,
        "description": "実験室の室温(℃)",
        "values": [],
        "axes": [],
    },
    {
        "key_name": "is_night",
        "display_name": "夜間実施",
        "value_type": "boolean",
        "scope": "segment",
        "min_value": None,
        "max_value": None,
        "description": "夜間に実施したか",
        "values": [],
        "axes": [],
    },
    {
        "key_name": "notes_free",
        "display_name": "備考",
        "value_type": "text",
        "scope": "segment",
        "min_value": None,
        "max_value": None,
        "description": "自由記述の補足条件",
        "values": [],
        "axes": [],
    },
    {
        "key_name": "position",
        "display_name": "位置",
        "value_type": "number_array",
        "scope": "segment",
        "min_value": None,
        "max_value": None,
        "description": "被験者位置(部屋内座標)",
        "values": [],
        # (axis_label, min_value, max_value)。axis_labelは demo/app/main.py・テンプレートが
        # フォームのフィールド名(position_x等)にも使う識別子のため、短いASCII文字列にすること。
        "axes": [
            ("x", 0, 5),
            ("y", 0, 3),
        ],
    },
]

# DD-16: 真値デバイスもセンサーの一種。role で区別する。
SENSOR_TYPES = [
    ("radar", "レーダー", "target"),
    ("psg_reference", "PSG(真値デバイス)", "reference"),
]

# DD-16/DD-18: 推定・真値変換・評価をすべて algorithm_runs の1レコードとして扱う。
# input_sensor_type は「このアルゴリズムがどのセンサーの整形データを入力にとるか」を表すPOC用の補助情報で、
# 自動化ワーカーが何を起動すべきかを決めるために使う。
ALGORITHMS = [
    ("hr_estimate", "心拍推定(レーダー)", "estimation", "radar"),
    ("truth_hr_from_psg", "真値抽出(PSG)", "ground_truth", "psg_reference"),
    ("hr_eval", "推定と真値の比較", "evaluation", None),
]

# --with-sample 用のシナリオ。時刻はセッション開始からの相対分で指定する。
SAMPLE_SESSION = {
    "record_date": "2026-07-10",
    "recorder_id": "demo_user",
    "setup": {"room_temperature": 22.5},
    "duration_minutes": 60,
    "sensors": ["radar", "psg_reference"],
}

SAMPLE_SEGMENTS = [
    {
        "label": "仰臥位トライアル",
        "offset_minutes": 5,
        "duration_minutes": 15,
        "conditions": {
            "posture": "supine",
            "subjects": ["S001"],
            "position": [2.5, 1.0],
            "is_night": False,
        },
        "creator_id": "demo_user",
    },
    {
        "label": "側臥位トライアル",
        "offset_minutes": 25,
        "duration_minutes": 15,
        "conditions": {
            "posture": "lateral",
            "subjects": ["S001", "S002"],
            "position": [1.0, 2.0],
            "notes_free": "被験者2名で実施",
        },
        "creator_id": "demo_user",
    },
]
