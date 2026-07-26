"""自動化ワーカー (ダミーEC2)。

DD-13 の差分検出ループ(reconciliation)。メッセージキューもファイル監視も使わず、
「やるべきことの一覧」を毎回ゼロからDBに問い合わせて導出する(DD-03 存在=状態)。
途中で失敗しても次のパスが拾うため、冪等性が自然に得られる。

新しいロジックはここにはない。GUI が呼ぶのと同じ pipeline の関数を、人の代わりに呼ぶだけ。

使い方:
    uv run python demo/scripts/worker.py --once            # 進行可能な段を1つだけ処理
    uv run python demo/scripts/worker.py --until-idle      # 残作業がなくなるまで繰り返す
    uv run python demo/scripts/worker.py --interval 10     # 10秒ごとに常駐実行
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demo.app import db, pipeline, repository, storage
from demo.app.oplog import ACTOR_WORKER

WORKER_ID = "worker"


def run_once(conn) -> dict:
    """進行可能な最も早い段を1つだけ処理する。

    1パスで1段だけ進めるのは、ダッシュボードで「未整形→未処理→未評価」と残作業が
    移っていく様子を見せるため。全部やりきりたい場合は run_until_idle() を使う。
    """
    result = {"stage": None, "done": 0, "errors": []}

    unformatted = repository.find_unformatted(conn)
    if unformatted:
        result["stage"] = "整形"
        for item in unformatted:
            try:
                pipeline.format_segment(
                    conn, segment_id=item["segment_id"], sensor_type=item["sensor_type"],
                    formatter_id=WORKER_ID, actor=ACTOR_WORKER,
                )
                result["done"] += 1
            except (pipeline.PipelineError, storage.StorageError) as exc:
                result["errors"].append(
                    f"整形失敗 (segment_id={item['segment_id']}, {item['sensor_type']}): {exc}"
                )
        return result

    unprocessed = repository.find_unprocessed(conn)
    if unprocessed:
        result["stage"] = "アルゴリズム実行"
        for item in unprocessed:
            try:
                pipeline.run_algorithm(
                    conn, segment_id=item["segment_id"], algorithm_name=item["algorithm_name"],
                    runner_id=WORKER_ID, actor=ACTOR_WORKER,
                )
                result["done"] += 1
            except (pipeline.PipelineError, storage.StorageError) as exc:
                result["errors"].append(
                    f"実行失敗 (segment_id={item['segment_id']}, {item['algorithm_name']}): {exc}"
                )
        return result

    unevaluated = repository.find_unevaluated(conn)
    if unevaluated:
        result["stage"] = "評価"
        for item in unevaluated:
            try:
                pipeline.run_evaluation(
                    conn, segment_id=item["segment_id"], est_run_id=item["est_run_id"],
                    gt_run_id=item["gt_run_id"], runner_id=WORKER_ID, actor=ACTOR_WORKER,
                )
                result["done"] += 1
            except (pipeline.PipelineError, storage.StorageError) as exc:
                result["errors"].append(
                    f"評価失敗 (segment_id={item['segment_id']}): {exc}"
                )
        return result

    return result   # stage=None: 残作業なし


def run_until_idle(conn, max_passes: int = 20) -> list[dict]:
    results = []
    for _ in range(max_passes):
        r = run_once(conn)
        if r["stage"] is None:
            break
        results.append(r)
        # 同じ段でエラーばかりが返る場合に無限ループしないよう、進捗0なら打ち切る
        if r["done"] == 0:
            break
    return results


def _print(result: dict) -> None:
    if result["stage"] is None:
        print("残作業なし (何もしませんでした)")
        return
    print(f"[{result['stage']}] 完了 {result['done']} 件 / 失敗 {len(result['errors'])} 件")
    for err in result["errors"]:
        print(f"  ! {err}")


def main():
    parser = argparse.ArgumentParser(description="差分検出ループのワーカー")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="進行可能な段を1つだけ処理して終了")
    group.add_argument("--until-idle", action="store_true", help="残作業がなくなるまで繰り返す")
    group.add_argument("--interval", type=int, metavar="SEC", help="指定秒ごとに常駐実行する")
    args = parser.parse_args()

    conn = db.get_connection()
    try:
        if args.interval:
            print(f"{args.interval}秒間隔で監視します (Ctrl-C で終了)")
            while True:
                result = run_once(conn)
                if result["stage"] is not None:
                    _print(result)
                time.sleep(args.interval)
        elif args.until_idle:
            results = run_until_idle(conn)
            if not results:
                print("残作業なし (何もしませんでした)")
            for r in results:
                _print(r)
        else:
            _print(run_once(conn))
    except KeyboardInterrupt:
        print("\n終了しました")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
