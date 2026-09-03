# portable-rag

API 不可・Google ドライブのみ・インストール不要という制約の業務PC向けに、HTML 約5,000件をローカルで検索し、AI（ブラウザの Gemini、スプレッドシートの AI 関数）に渡しやすい形で出力する Python 製 RAG 検索ツール。

- 標準ライブラリのみで動作（BM25 / 文字 bigram / SQLite 転置索引 / 差分更新）
- `sentence-transformers` 導入時のみベクトル検索を自動併用（RRF 統合）
- 出力：TSV（スプレッドシート）/ Gemini 貼付用プロンプト / HTML / JSON。TSV と HTML には BM25 の生スコア（一致度）を出す
- `python rag_app.py` で localhost の Web UI
- `python bundle_app.py`：系列まとめのブラウザ画面版（分け方を選ぶ → グループ数を確認 → 作る → フォルダを開く）。初心者はこちら
- `python bundle.py`：同じ処理のコマンド版。系列（フォルダ / ファイル名規則 / 文書内項目 / 検索結果）ごとに NotebookLM・Gemini 用の結合ファイルと要約依頼文を作る
- `python launcher.py`：任意の .py / .bat をボタン登録して実行するコントロールUI（大きさ3段階・1〜3列）。検索対象フォルダ（`config.json` の `source_dirs`）の追加・削除もここから行う


## 索引作成が遅いときに最初に見る2つ

1. **`config.json` の `index_dir` がクラウド同期フォルダの中にないか。** Google ドライブ同期配下だと実測で約9.5倍遅い（100件で400秒 → 42秒）。`build_index.py` 実行時に自動で警告が出る。
2. **`sqlite_cache_mb`（既定256）を上げる。** SQLite の既定は2MBしかなく、書き込みが全体を通して遅くなる。実測では300件103MBの構築が128秒 → 61秒（2.1倍）。メモリの少ないPCでは64まで下げてよい。

**所要が不規則に見えるとき**は、進捗ログの「秒/千チャンク」を見る。**所要時間は件数ではなくチャンク数でほぼ決まる**（実測で相関 +0.99）。10件で236秒の回と21秒の回があっても、秒/千チャンクが一定なら大きい文書が入っていただけで、異常ではない。索引が育つことによる悪化は1,333万行の範囲で26〜33%にとどまる。

なお HTML を Markdown に変換しても速くならない（実測で14.8%短縮のみ）。HTML 解析は全体の約1割で、支配的なのは SQLite への書き込み。詳細は [振り返り.html](振り返り.html) の 2-6 / 2-7。

初心者向けの導入手順は [導入手順書.html](導入手順書.html)（A4想定）。詳細は [README.html](README.html)（導入手順）、[要件定義.html](要件定義.html)、[振り返り.html](振り返り.html) を参照。

```
python build_index.py            # 索引の作成・差分更新
python search.py "返品 送料" --format prompt
python rag_app.py                # http://127.0.0.1:8765
python launcher.py               # コントロールUI http://127.0.0.1:8766
python bundle_app.py             # 系列まとめ（ブラウザ画面 http://127.0.0.1:8768）
python bundle.py                 # 系列まとめ（コマンド版）
python tests/test_rag.py         # テスト
```
