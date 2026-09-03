"""設定の読み込み。config.json が無ければ既定値で動く。"""
import json
import os

DEFAULTS = {
    # 検索対象フォルダ（Google ドライブの同期フォルダなど）。複数可。
    "source_dirs": ["./sample_docs"],
    # インデックスの保存先。**必ずクラウド同期の対象外に置くこと。**
    # 業務PCでの実測（2026-09-03）: Google ドライブ同期配下だと100件400秒、
    # 同期外へ移すだけで42秒（約9.5倍）。build 時に自動で警告を出す（rag/syncdir.py）
    "index_dir": "./index",
    "extensions": [".html", ".htm", ".txt", ".md"],
    # チャンク分割（文字数）。重なりを持たせて文脈切れを防ぐ
    "chunk_size": 600,
    "chunk_overlap": 120,
    # BM25 パラメータ
    "bm25_k1": 1.2,
    "bm25_b": 0.75,
    # SQLite のページキャッシュ（MB）。SQLite の既定は 2MB しかなく、転置索引の書き込みが
    # 全体を通して遅くなる。**「途中から急に遅くなる」のではなく、最初から最後まで効く定数倍。**
    # 実測（300件103MB、サイズにばらつきあり）: 2MB で128秒、256MB で61秒（2.1倍）。
    # メモリの少ないPCでは 64 程度まで下げてよい。
    "sqlite_cache_mb": 256,
    # 進捗ログを何件ごとに出すか。所要時間は件数ではなくチャンク数で決まるため、
    # ログにはチャンク数も出す（件数だけ見ると不規則に見える）
    "progress_every": 100,
    # ベクトル検索（sentence-transformers が導入済みのときのみ有効）
    "embedding_model": "intfloat/multilingual-e5-small",
    "use_embeddings": "auto",   # auto / true / false
    # ハイブリッド統合（RRF）の重み
    "rrf_k": 60,
    "weight_bm25": 1.0,
    "weight_vector": 1.0,
    # 出力
    "top_k": 8,
    "max_chars_per_hit": 700,
    "web_host": "127.0.0.1",
    "web_port": 8765,
}


def load_config(path="config.json"):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        unknown = set(user) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"config.json に未知のキーがあります: {sorted(unknown)}")
        cfg.update(user)
    base = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()
    cfg["source_dirs"] = [os.path.abspath(os.path.join(base, d)) for d in cfg["source_dirs"]]
    cfg["index_dir"] = os.path.abspath(os.path.join(base, cfg["index_dir"]))
    return cfg
