"""デモ用のシードデータ。

posture / subjects は docs/design-decisions.md (DD-08, DD-15) / docs/er.md の例をそのまま流用している。
room_temperature / is_night / notes_free は number/boolean/text の value_type を実演するために
このデモ用に追加したものであり、本設計docに登場する項目ではない。
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
    },
]

SAMPLE_SEGMENTS = [
    {
        "label": "デモ実験1",
        "record_date": "2026-07-10",
        "started_at": "2026-07-10T09:00:00",
        "ended_at": "2026-07-10T09:30:00",
        "conditions": {"posture": "supine", "subjects": ["S001"], "room_temperature": 22.5},
        "creator_id": "demo_user",
    },
    {
        "label": "デモ実験2",
        "record_date": "2026-07-11",
        "started_at": "2026-07-11T14:00:00",
        "ended_at": "2026-07-11T14:45:00",
        "conditions": {
            "posture": "lateral",
            "subjects": ["S001", "S002"],
            "room_temperature": 24.0,
            "is_night": False,
        },
        "creator_id": "demo_user",
    },
    {
        "label": "デモ実験3(夜間・無人)",
        "record_date": "2026-07-12",
        "started_at": "2026-07-12T23:00:00",
        "ended_at": "2026-07-12T23:40:00",
        "conditions": {
            "posture": "supine",
            "subjects": [],
            "room_temperature": 26.5,
            "is_night": True,
            "notes_free": "真値デバイスのみで実施",
        },
        "creator_id": "demo_user",
    },
]
