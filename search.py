#!/usr/bin/env python3
"""使い方:  python search.py "検索語" [--format tsv|prompt|html|json] [--top 8] [--mode hybrid|bm25|vector] [--out ファイル]
例:  python search.py "返品 送料 負担" --format prompt   → Gemini に貼る文章を表示
     python search.py "返品 送料 負担" --format tsv --out result.tsv → スプレッドシートに読み込む"""
import argparse
import sys

from rag.config import load_config
from rag.formats import FORMATS
from rag.search import Searcher

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--format", default="prompt", choices=sorted(FORMATS))
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument("--mode", default="hybrid", choices=["hybrid", "bm25", "vector"])
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    cfg = load_config(a.config)
    res = Searcher(cfg).search(a.query, top_k=a.top, mode=a.mode)
    text = FORMATS[a.format](res, cfg)
    if a.out:
        with open(a.out, "w", encoding="utf-8-sig" if a.format == "tsv" else "utf-8") as f:
            f.write(text)
        print(f"書き出し: {a.out} ({len(res['hits'])} 件)")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
