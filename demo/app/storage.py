"""ダミーS3 (ローカルフォルダ)。

DBに保存するURIは本物のS3と同じ `s3://bucket/key` 形式に保つ。実体は demo/data/storage/ 配下の
ローカルファイルだが、将来 boto3 実装に差し替えるときに変更が必要なのはこのファイルだけになる。

URI規約は DD-04 と docs/sequence_diagrams.md のシナリオ1〜3に合わせている:
    生     s3://experiment-poc/{record_date}/{recorder_id}/{session_no}/raw/{sensor_type}/{seq_no}.csv
    整形   s3://experiment-poc/.../{session_no}/segments/{segment_id}/formatted/{sensor_type}.csv
    出力   s3://experiment-poc/.../{session_no}/segments/{segment_id}/runs/{run_id}/output.csv
"""

import os
from pathlib import Path

BUCKET = "experiment-poc"
URI_PREFIX = f"s3://{BUCKET}/"

APP_DIR = Path(__file__).resolve().parent
DEFAULT_STORAGE_ROOT = APP_DIR.parent / "data" / "storage"
STORAGE_ROOT = Path(os.environ.get("DEMO_STORAGE_ROOT", DEFAULT_STORAGE_ROOT))


class StorageError(Exception):
    pass


# ---------------------------------------------------------------------------
# URI <-> ローカルパス
# ---------------------------------------------------------------------------

def uri_to_path(uri: str) -> Path:
    if not uri.startswith(URI_PREFIX):
        raise StorageError(f"想定外のURI形式です (先頭が {URI_PREFIX} ではない): {uri}")
    key = uri[len(URI_PREFIX):]
    if not key or ".." in key.split("/"):
        raise StorageError(f"不正なキーです: {uri}")
    return STORAGE_ROOT / key


# ---------------------------------------------------------------------------
# 基本操作 (boto3 の put_object/get_object 相当)
# ---------------------------------------------------------------------------

def put(uri: str, data: bytes) -> str:
    path = uri_to_path(uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return uri


def put_text(uri: str, text: str) -> str:
    return put(uri, text.encode("utf-8"))


def get(uri: str) -> bytes:
    path = uri_to_path(uri)
    if not path.exists():
        raise StorageError(f"ストレージに存在しません: {uri}")
    return path.read_bytes()


def get_text(uri: str) -> str:
    return get(uri).decode("utf-8")


def exists(uri: str) -> bool:
    return uri_to_path(uri).exists()


def size(uri: str) -> int:
    path = uri_to_path(uri)
    return path.stat().st_size if path.exists() else 0


def list_uris(prefix: str = URI_PREFIX) -> list[str]:
    """prefix 配下のURIを列挙する。"""
    if not STORAGE_ROOT.exists():
        return []
    uris = []
    for path in sorted(STORAGE_ROOT.rglob("*")):
        if path.is_file():
            uri = URI_PREFIX + str(path.relative_to(STORAGE_ROOT))
            if uri.startswith(prefix):
                uris.append(uri)
    return uris


def basename(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# URI組み立て
# ---------------------------------------------------------------------------

def _session_prefix(record_date: str, recorder_id: str, session_no: int) -> str:
    return f"{URI_PREFIX}{record_date}/{recorder_id}/{session_no}"


def raw_uri(record_date: str, recorder_id: str, session_no: int, sensor_type: str, seq_no: int) -> str:
    return f"{_session_prefix(record_date, recorder_id, session_no)}/raw/{sensor_type}/{seq_no}.csv"


def formatted_uri(record_date: str, recorder_id: str, session_no: int,
                  segment_id: int, sensor_type: str) -> str:
    # 注: 同一区間・同一センサーを再整形すると、このパスは上書きされる。
    # DBには formatted_data の行が複数残る(最新は created_at で判断)が、実体は最新版のみになる。
    # 本設計でも同じ論点があり、バージョン付きパスにするかは未決。
    return (f"{_session_prefix(record_date, recorder_id, session_no)}"
            f"/segments/{segment_id}/formatted/{sensor_type}.csv")


def run_output_uri(record_date: str, recorder_id: str, session_no: int,
                   segment_id: int, run_id: int) -> str:
    return (f"{_session_prefix(record_date, recorder_id, session_no)}"
            f"/segments/{segment_id}/runs/{run_id}/output.csv")
