"""フォルダを走査して差分だけ再インデックスする（冪等）。"""
import datetime
import os
import sys
import time

from . import embed
from . import syncdir
from .chunker import split_chunks
from .html_extract import extract
from .store import Store, file_sha1
from .tokenizer import tokenize


def scan(cfg):
    exts = tuple(cfg["extensions"])
    for d in cfg["source_dirs"]:
        if not os.path.isdir(d):
            print(f"[警告] フォルダが見つかりません: {d}", file=sys.stderr)
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                if fn.lower().endswith(exts) and not fn.startswith(("~$", ".")):
                    yield os.path.join(root, fn)


def build(cfg, full=False, log=print):
    # 索引の保存先が同期フォルダだと10倍近く遅くなる。処理は止めず警告だけ出す。
    syncdir.warn_if_synced(cfg["index_dir"], log)
    store = Store(cfg["index_dir"], cache_mb=cfg.get("sqlite_cache_mb", 256), log=log)
    known = store.known_docs()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    seen, added, updated, skipped, removed = set(), 0, 0, 0, 0
    t0 = time.time()
    # 進捗ログは件数だけでなく「そのバッチのチャンク数」を出す。
    # **所要時間は件数ではなくチャンク数でほぼ決まる**（実測: 相関 +0.99）。
    # 件数だけ見ていると「10件なのに236秒」が不規則に見えるが、
    # チャンク数を並べると大きい文書が入っていただけだと分かる。
    every = max(1, int(cfg.get("progress_every", 100)))
    b_chunks = b_bytes = 0
    t_batch = t0
    for path in scan(cfg):
        seen.add(path)
        st = os.stat(path)
        old = known.get(path)
        if old and not full and old[2] == st.st_mtime and old[3] == st.st_size:
            skipped += 1
            continue
        sha = file_sha1(path)
        if old and not full and old[4] == sha:
            skipped += 1
            continue
        title, body = extract(path)
        chunks = split_chunks(body, cfg["chunk_size"], cfg["chunk_overlap"])
        if title:
            chunks = [f"{title}\n{c}" for c in chunks] or [title]
        if old:
            store.delete_doc(old[1])
            updated += 1
        else:
            added += 1
        store.add_doc(path, title, st.st_mtime, st.st_size, sha,
                      [(c, tokenize(c)) for c in chunks], now)
        b_chunks += len(chunks)
        b_bytes += st.st_size
        if (added + updated) % every == 0:
            store.commit()
            el = time.time() - t_batch
            rate = f"{1000 * el / b_chunks:.1f}秒/千チャンク" if b_chunks else "-"
            log(f"  {added + updated} 件処理  直近{every}件: "
                f"{b_chunks:,} チャンク {b_bytes / 1048576:.1f}MB {el:.1f}s ({rate})  "
                f"累計 {time.time() - t0:.0f}s")
            b_chunks = b_bytes = 0
            t_batch = time.time()
    for path, row in known.items():
        if path not in seen:
            store.delete_doc(row[1])
            removed += 1
    store.set_meta("last_build", now)
    store.commit()
    log(f"追加 {added} / 更新 {updated} / 変更なし {skipped} / 削除 {removed}  "
        f"文書 {store.doc_count()} 件, チャンク {store.stats()[0]} 件  ({time.time() - t0:.1f}s)")

    mode = str(cfg["use_embeddings"]).lower()
    if mode != "false" and embed.available():
        todo = store.chunks_without_vectors()
        if todo:
            log(f"ベクトル化 {len(todo)} チャンク（モデル {cfg['embedding_model']}）…")
            for i in range(0, len(todo), 256):
                batch = todo[i:i + 256]
                vecs = embed.encode(cfg["embedding_model"], [t for _, t in batch])
                store.put_vectors(list(zip([c for c, _ in batch], vecs)))
                store.commit()
                log(f"  {min(i + 256, len(todo))}/{len(todo)}")
        store.set_meta("embedding_model", cfg["embedding_model"])
    elif mode == "true":
        log("[警告] use_embeddings=true ですが sentence-transformers が未導入のためキーワード検索のみです")
    else:
        log("ベクトル検索: 無効（sentence-transformers 未導入）。キーワード検索のみで動作します")
    return store
