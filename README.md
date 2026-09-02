# portable-rag

API 不可・Google ドライブのみ・インストール不要という制約の業務PC向けに、HTML 約5,000件をローカルで検索し、AI（ブラウザの Gemini、スプレッドシートの AI 関数）に渡しやすい形で出力する Python 製 RAG 検索ツール。

- 標準ライブラリのみで動作（BM25 / 文字 bigram / SQLite 転置索引 / 差分更新）
- `sentence-transformers` 導入時のみベクトル検索を自動併用（RRF 統合）
- 出力：TSV（スプレッドシート）/ Gemini 貼付用プロンプト / HTML / JSON
- `python rag_app.py` で localhost の Web UI

詳細は [README.html](README.html)（導入手順）、[要件定義.html](要件定義.html)、[振り返り.html](振り返り.html) を参照。

```
python build_index.py            # 索引の作成・差分更新
python search.py "返品 送料" --format prompt
python rag_app.py                # http://127.0.0.1:8765
python tests/test_rag.py         # テスト
```
