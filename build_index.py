#!/usr/bin/env python3
"""使い方:  python build_index.py [--full] [--config config.json]
config.json の source_dirs 配下の HTML を読み、index/rag.sqlite を作る（差分更新）。"""
import argparse

from rag.config import load_config
from rag.indexer import build

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--full", action="store_true", help="全件を作り直す")
    a = ap.parse_args()
    build(load_config(a.config), full=a.full)
