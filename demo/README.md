# 実験データ管理 POC (SQLite + FastAPI + htmx)

本実装(PostgreSQL、`docs/` 参照)に入る前に、**ライフサイクル全体を一通り動かして完成イメージを共有する**
ためのローカルPOC。

```
計測セッション登録 → 生データ投入 → 区間の切り出しと条件付与 → 整形 → 推定/真値 → 評価
```

このうち**人がやるのは「区間の切り出しと条件付与」まで**で、そこから先は差分検出ループのワーカーが自動で進む
(DD-02 / DD-13)。POCの目的は動くこと自体より、**どの操作でDBの何が更新されるかを目で見て理解できること**にある。

このディレクトリは丸ごと削除しても本設計(`docs/`)には影響しない。

本書は**動かし方**を扱う。各ファイルの役割・データの流れ・コードの構造は
[ARCHITECTURE.md](ARCHITECTURE.md) を参照(デモを説明するときの資料も兼ねる)。

## 起動

```
uv run python demo/scripts/seed_db.py --reset      # マスタのみ投入(実体テーブルは空)
uv run uvicorn demo.app.main:app --reload --port 8000
```

ブラウザで `http://127.0.0.1:8000/` を開く。実体テーブルが空の状態から、画面を操作するたびに
テーブルが埋まっていく様子を追える。

サンプル一式(セッション→生データ→区間→整形→処理→評価)を一括で流したい場合:

```
uv run python demo/scripts/seed_db.py --reset --with-sample
```

自動化ワーカーはGUIのボタンからも、CLIからも同じ関数を呼べる:

```
uv run python demo/scripts/worker.py --once         # 進行可能な段を1つだけ処理
uv run python demo/scripts/worker.py --until-idle   # 残作業がなくなるまで
uv run python demo/scripts/worker.py --interval 10  # 10秒ごとに常駐 (ダミーEC2)
```

DBは `demo/data/demo.db`、ダミーS3の実体は `demo/data/storage/`(どちらもgitignore対象)。

## DBの更新を見るしくみ

1. **事前予告** — 各操作フォームの上部に「この操作で更新されるテーブル・項目」を表示する。
   定義は実装とズレないよう `demo/app/pipeline.py` の `OPERATION_EFFECTS` に置いている。
2. **事後の差分** — 実行前後で全テーブルの行数を取り、`raw_files +6` のような差分と、
   実際に書き込まれた行の主キー・値・置かれたファイルURIを結果画面に出す。
3. **ダッシュボード**(`/`) — 全テーブルの行数と、**残作業**(未整形 / 未処理 / 未評価)を常時表示する。
   残作業は状態カラムではなく `LEFT JOIN ... IS NULL` で導出している(DD-03 存在=状態)。
4. **操作履歴**(`/operations`) — 人の操作もワーカーの操作も同じ `operation_log` に残るため、
   どこまでが人でどこからが自動かが一覧で追える。失敗した操作も記録される。

## 構成

| ファイル | 役割 |
|---|---|
| `app/schema.sql` | 本設計13テーブル + POC専用の `operation_log` |
| `app/repository.py` | DBアクセスのみ。条件検証と差分検出クエリを含む |
| `app/storage.py` | ダミーS3(ローカルFS)。URIは `s3://` 形式を保つ |
| `app/dummy_data.py` | 合成センサーCSVの生成と、CSVからの時刻抽出 |
| `app/processing.py` | 整形・アルゴリズムのレジストリ(DD-14) |
| `app/pipeline.py` | 上記を束ねた業務操作。**GUIとワーカーが共に呼ぶ** |
| `app/oplog.py` | 操作ログとテーブル行数スナップショット |
| `app/main.py` | FastAPIルート(薄いラッパ) |
| `scripts/worker.py` | 差分検出ループ(ダミーEC2) |
| `scripts/sample_scenario.py` | サンプル一式を流す |

`repository` はDBだけ、`storage` はファイルだけ、`processing` は計算だけを見る。
自動化とは「pipeline の関数を呼ぶ主体が人からプログラムに変わるだけ」という DD-13 の主張を、
`main.py` と `worker.py` が同じ関数を呼ぶ構造で示している。

## 本設計との違い

- 例外: `condition_keys.value_type='number_array'`(被験者位置の例)と `condition_key_axes` は、他の項目と
  異なり**本設計とスキーマを1:1で一致させている**(DD-19)。簡略化ではない。
- `jsonb` の代わりに `TEXT` にJSON文字列を保存する。GINインデックス/`@>` 演算子は使わない。
- `timestamptz` の代わりに ISO8601 の `TEXT`。`tstzrange` の重なり判定(`&&`)は
  「`a.started_at < b.ended_at AND a.ended_at > b.started_at`」の文字列比較で代替している。
  書式を揃えている限り辞書順比較が時系列順と一致することに依存している。
- `condition_values.value` は型保持のため `json.dumps` した値を保存・比較する。
- 検索は全件ロードしてPython側でフィルタする(`repository.search_segments`)。件数が小さいデモだから
  許容される簡略化であり、本番では `conditions @> '{...}'` + GINインデックスを使う想定。
- **DBトリガーは作らない**。本設計がトリガーで担う検証(マスタ照合・区間の計測範囲内チェック・
  run入力の同一区間チェック)は `repository.validate_conditions()` と `pipeline.py` にPythonとして
  一本化している(DD-13)。本番ではDD-09のとおりDB側にも防衛線を置くべき。
- `operation_log` は本設計に存在しないPOC専用テーブル。
- `algorithms.input_sensor_type` も本設計にはないPOC用の補助列(ワーカーが何を起動すべきか決めるため)。
- `condition_keys` / `condition_values` マスタはシード投入のみで、その場での追加UI(シナリオ4)はない。
- 認証がないため、担当者IDは画面で手入力する。

## ダミー処理の中身

`radar` と `psg_reference` は**同一の「真の心拍数」**(60 + 8·sin、10分周期)から生成される。
そのため後段の評価が意味のある数値になる。

- `hr_estimate`(推定) — radar波形の上向きゼロ交差を分ごとに数えて心拍数を推定
- `truth_hr_from_psg`(真値) — psgの値を分ごとに平均
- `hr_eval`(評価) — 両者を突き合わせて MAE / RMSE / max_abs_error を算出

サンプルシナリオでの実測は MAE ≈ 0.4 bpm 程度になる。
