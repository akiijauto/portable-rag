"""検索本体：BM25（転置索引）＋ベクトル（任意）を RRF で統合する。"""
import math
import re
from collections import defaultdict

from . import embed
from .store import Store
from .tokenizer import tokenize


class Searcher:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(cfg["index_dir"], cache_mb=cfg.get("sqlite_cache_mb", 256))
        self._vec_cache = None

    # ---- BM25 ----
    def bm25(self, query, limit=50):
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        n, avgdl = self.store.stats()
        if n == 0:
            return []
        k1, b = self.cfg["bm25_k1"], self.cfg["bm25_b"]
        scores = defaultdict(float)
        seen_terms = set()
        for t in q_tokens:
            if t in seen_terms:
                continue
            seen_terms.add(t)
            post = self.store.postings(t)
            if not post:
                continue
            df = len(post)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            lens = self.store.chunk_lengths([c for c, _ in post])
            for cid, tf in post:
                dl = lens.get(cid, avgdl)
                scores[cid] += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))
        # フレーズ一致（完全一致）ボーナス
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:limit * 3]
        if ranked:
            info = self.store.chunks_info([c for c, _ in ranked])
            phrase = query.strip().lower()
            if len(phrase) >= 2:
                ranked = [(c, s * (1.3 if phrase in info[c]["text"].lower() else 1.0)) for c, s in ranked]
                ranked.sort(key=lambda x: -x[1])
        return ranked[:limit]

    # ---- vector ----
    def vectors_enabled(self):
        mode = str(self.cfg["use_embeddings"]).lower()
        if mode == "false":
            return False
        if self.store.vector_count() == 0:
            return False
        return embed.available()

    def vector(self, query, limit=50):
        if not self.vectors_enabled():
            return []
        if self._vec_cache is None:
            self._vec_cache = list(self.store.all_vectors())
        qv = embed.encode(self.cfg["embedding_model"], [query], kind="query")[0]
        try:
            import numpy as np
            ids = [c for c, _ in self._vec_cache]
            mat = np.array([v for _, v in self._vec_cache], dtype="float32")
            sims = mat @ np.array(qv, dtype="float32")
            order = np.argsort(-sims)[:limit]
            return [(ids[i], float(sims[i])) for i in order]
        except ImportError:
            sims = [(cid, embed.cosine(qv, v)) for cid, v in self._vec_cache]
            sims.sort(key=lambda x: -x[1])
            return sims[:limit]

    # ---- hybrid ----
    def search(self, query, top_k=None, mode="hybrid"):
        top_k = top_k or self.cfg["top_k"]
        k = self.cfg["rrf_k"]
        fused = defaultdict(float)
        sources = defaultdict(dict)
        bm = self.bm25(query) if mode in ("hybrid", "bm25") else []
        vec = self.vector(query) if mode in ("hybrid", "vector") else []
        for rank, (cid, s) in enumerate(bm):
            fused[cid] += self.cfg["weight_bm25"] / (k + rank + 1)
            sources[cid]["bm25"] = s
        for rank, (cid, s) in enumerate(vec):
            fused[cid] += self.cfg["weight_vector"] / (k + rank + 1)
            sources[cid]["vector"] = s
        ranked = sorted(fused.items(), key=lambda x: -x[1])
        # 同一文書からは最大2チャンクまで（多様性確保）
        per_doc, picked = defaultdict(int), []
        info = self.store.chunks_info([c for c, _ in ranked[:top_k * 4]])
        for cid, s in ranked:
            if cid not in info:
                continue
            d = info[cid]["doc_id"]
            if per_doc[d] >= 2:
                continue
            per_doc[d] += 1
            hit = dict(info[cid])
            hit["score"] = round(s, 5)
            hit["signals"] = sources[cid]
            hit["snippet"] = self._snippet(hit["text"], query)
            picked.append(hit)
            if len(picked) >= top_k:
                break
        return {"query": query, "mode": mode, "vector_used": bool(vec), "hits": picked}

    def _snippet(self, text, query):
        limit = self.cfg["max_chars_per_hit"]
        if len(text) <= limit:
            return text
        terms = [t for t in re.split(r"\s+", query.strip()) if t]
        low = text.lower()
        pos = min((low.find(t.lower()) for t in terms if low.find(t.lower()) >= 0), default=-1)
        if pos < 0:
            return text[:limit] + "…"
        start = max(0, pos - limit // 3)
        return ("…" if start else "") + text[start:start + limit] + "…"
