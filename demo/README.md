# 実験条件デモ (SQLite + FastAPI + htmx)

本実装(PostgreSQL、`docs/` 参照)に入る前に、関係者へ完成イメージを共有するための**使い捨てのローカルデモ**。
「実験条件」の閲覧・登録・検索の3機能だけを、SQLite + FastAPI + htmx の最小構成で動かす。

このディレクトリは丸ごと削除しても本設計(`docs/`)には影響しない。

## 本設計との違い

- `recording_sessions` / `raw_files` / `formatted_data` / `algorithm_runs` は作らない。`segments` 相当の
  フラットな1テーブルのみ(`session_id` FKなし、DD-11のセッション範囲内チェックも省略)。
- `jsonb` の代わりに `TEXT` にJSON文字列を保存する(`demo/app/schema.sql`)。
- `condition_values.value` は型保持のため `json.dumps` した値を保存・比較する(素の文字列比較ではない)。
- GINインデックス/`@>` 演算子は使わず、検索は全件ロードしてPython側でフィルタする
  (`demo/app/repository.py: search_segments`)。データ件数が小さいデモだからこそ許容される簡略化であり、
  本番PostgreSQL実装では `conditions @> '{...}'` + GINインデックスを使う想定。
- DBトリガーは作らず、マスタ検証(`validate_jsonb_against_master()` 相当)は
  `demo/app/repository.py: validate_conditions()` にPython関数として一本化している(DD-13のライブラリ・
  ファースト方針をそのままデモの正とする)。
- `condition_keys` / `condition_values` マスタはシード投入のみで、その場での追加UI(設計docのシナリオ4)はない。

## セットアップ・起動

```
uv add fastapi "uvicorn[standard]" jinja2 python-multipart   # 初回のみ(pyproject.tomlに反映済みなら不要)
uv run python demo/scripts/seed_db.py --reset                # SQLiteファイルを作り直してシード投入
uv run uvicorn demo.app.main:app --reload --port 8000
```

ブラウザで `http://127.0.0.1:8000/` を開く。

DBファイルは `demo/data/demo.db`(gitignore対象)。作り直したい場合は `--reset` 付きで再実行する。

## 画面

- `/segments` — 閲覧: 登録済みの実験条件を一覧表示(条件はマスタの表示名で表示、生JSONは出さない)
- `/segments/new` — 登録: マスタで統制された条件キー・選択肢からフォームを組み立てて登録
- `/segments/search` — 検索: 条件キーで絞り込み(複数指定でAND)。htmxでページ遷移なしに結果を更新
