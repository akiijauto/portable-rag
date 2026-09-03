"""SQLite に文書・チャンク・転置索引・埋め込みを保存する。
インデックスはファイル1個（index/rag.sqlite）なので Google ドライブでも持ち運べる。"""
import hashlib
import json
import os
import sqlite3
import struct
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  doc_id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT,
  mtime REAL, size INTEGER, sha1 TEXT, nchunks INTEGER, indexed_at TEXT);
CREATE TABLE IF NOT EXISTS chunks(
  chunk_id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER, text TEXT, length INTEGER);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS terms(term_id INTEGER PRIMARY KEY, term TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS postings(term_id INTEGER, chunk_id INTEGER, tf INTEGER);
CREATE INDEX IF NOT EXISTS ix_post_term ON postings(term_id);
CREATE INDEX IF NOT EXISTS ix_post_chunk ON postings(chunk_id);
CREATE TABLE IF NOT EXISTS vectors(chunk_id INTEGER PRIMARY KEY, dim INTEGER, vec BLOB);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

# 索引の形式。用語を文字列で持つ旧形式（1）から、用語 ID で持つ新形式（2）へ移行した。
# 旧形式の索引は Store 生成時に自動で作り直される（1トランザクションなので中断しても戻る）。
SCHEMA_VERSION = 2


def file_sha1(path, block=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class Store:
    def __init__(self, index_dir, cache_mb=256, log=None):
        """cache_mb: SQLite のページキャッシュ。**既定値を大きくしているのは意図的**。

        SQLite の既定は 2MB しかなく、転置索引がそれを超えた時点から、
        1行挿入するたびに B木のページをディスクへ読みに行くようになる。
        実測（postings 8.8M 行の索引へ10件追記）では 9.1秒 → 3.1秒 と約3倍違った。
        文書が増えるほど差が開くので、索引作成が途中から急に遅くなる主因はこれ。
        """
        os.makedirs(index_dir, exist_ok=True)
        self.path = os.path.join(index_dir, "rag.sqlite")
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        if cache_mb:
            self.con.execute("PRAGMA cache_size=-%d" % int(cache_mb * 1024))
        # 旧形式の索引に新形式の索引定義を当てると列が無くて失敗するため、移行が先。
        self._migrate(log)
        self.con.executescript(SCHEMA)
        self._term_cache = {}
        self.set_meta("schema_version", SCHEMA_VERSION)
        self.commit()

    # ---- 索引形式の移行 ----
    def _migrate(self, log=None):
        """旧形式（postings.term に文字列）を新形式（postings.term_id）へ作り直す。

        **全体を1トランザクションで行う**ので、途中で中断されても索引は旧形式のまま残り、
        壊れた状態にはならない。次回起動時にもう一度やり直せばよい。
        """
        cols = [r[1] for r in self.con.execute("PRAGMA table_info(postings)")]
        if not cols or "term_id" in cols:
            return False
        n = self.con.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
        if log:
            log(f"索引を新形式へ変換します（{n:,} 行）。中断しても元の索引は残ります…")
        t0 = time.time()
        prev = self.con.isolation_level
        self.con.isolation_level = None          # BEGIN/COMMIT を明示的に制御する
        try:
            self.con.execute("BEGIN IMMEDIATE")
            self.con.execute("CREATE TABLE IF NOT EXISTS terms("
                             "term_id INTEGER PRIMARY KEY, term TEXT UNIQUE)")
            self.con.execute("INSERT OR IGNORE INTO terms(term) SELECT DISTINCT term FROM postings")
            self.con.execute("CREATE TABLE postings_new(term_id INTEGER, chunk_id INTEGER, tf INTEGER)")
            self.con.execute("INSERT INTO postings_new(term_id, chunk_id, tf) "
                             "SELECT t.term_id, p.chunk_id, p.tf "
                             "FROM postings p JOIN terms t ON t.term = p.term")
            self.con.execute("DROP TABLE postings")
            self.con.execute("ALTER TABLE postings_new RENAME TO postings")
            self.con.execute("CREATE INDEX ix_post_term ON postings(term_id)")
            self.con.execute("CREATE INDEX ix_post_chunk ON postings(chunk_id)")
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            self.con.isolation_level = prev
            raise
        self.con.execute("VACUUM")               # 旧テーブルの領域を実際に解放する
        self.con.isolation_level = prev
        if log:
            log(f"変換が終わりました（{time.time() - t0:.1f}s）")
        return True

    # ---- 用語 ID ----
    def _term_id(self, term, create=True):
        """用語を整数 ID に変換する。索引のキーが短くなり、キャッシュに載りやすくなる。"""
        tid = self._term_cache.get(term)
        if tid is not None:
            return tid
        row = self.con.execute("SELECT term_id FROM terms WHERE term=?", (term,)).fetchone()
        if row is None:
            if not create:
                return None
            tid = self.con.execute("INSERT INTO terms(term) VALUES(?)", (term,)).lastrowid
        else:
            tid = row[0]
        self._term_cache[term] = tid
        return tid

    # ---- meta ----
    def get_meta(self, key, default=None):
        row = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    # ---- docs ----
    def known_docs(self):
        return {r[0]: r for r in self.con.execute("SELECT path, doc_id, mtime, size, sha1 FROM docs")}

    def delete_doc(self, doc_id):
        ids = [r[0] for r in self.con.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,))]
        self.con.executemany("DELETE FROM postings WHERE chunk_id=?", [(i,) for i in ids])
        self.con.executemany("DELETE FROM vectors WHERE chunk_id=?", [(i,) for i in ids])
        self.con.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        self.con.execute("DELETE FROM docs WHERE doc_id=?", (doc_id,))

    def add_doc(self, path, title, mtime, size, sha1, chunks_tokens, indexed_at):
        """chunks_tokens: [(text, tokens)] -> 新しい chunk_id のリスト"""
        cur = self.con.execute(
            "INSERT INTO docs(path,title,mtime,size,sha1,nchunks,indexed_at) VALUES(?,?,?,?,?,?,?)",
            (path, title, mtime, size, sha1, len(chunks_tokens), indexed_at))
        doc_id = cur.lastrowid
        ids = []
        for i, (text, tokens) in enumerate(chunks_tokens):
            c = self.con.execute("INSERT INTO chunks(doc_id,ord,text,length) VALUES(?,?,?,?)",
                                 (doc_id, i, text, len(tokens)))
            cid = c.lastrowid
            ids.append(cid)
            tf = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self.con.executemany("INSERT INTO postings VALUES(?,?,?)",
                                 [(self._term_id(t), cid, n) for t, n in tf.items()])
        return doc_id, ids

    def commit(self):
        self.con.commit()

    # ---- stats for BM25 ----
    def stats(self):
        n, avg = self.con.execute("SELECT COUNT(*), COALESCE(AVG(length),0) FROM chunks").fetchone()
        return n, avg

    def postings(self, term):
        tid = self._term_id(term, create=False)
        if tid is None:
            return []
        return self.con.execute("SELECT chunk_id, tf FROM postings WHERE term_id=?", (tid,)).fetchall()

    def chunk_lengths(self, ids):
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        return dict(self.con.execute(f"SELECT chunk_id,length FROM chunks WHERE chunk_id IN ({q})", ids))

    def chunks_info(self, ids):
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        rows = self.con.execute(
            f"SELECT c.chunk_id, c.doc_id, c.ord, c.text, d.path, d.title FROM chunks c "
            f"JOIN docs d ON c.doc_id=d.doc_id WHERE c.chunk_id IN ({q})", ids)
        return {r[0]: {"chunk_id": r[0], "doc_id": r[1], "ord": r[2], "text": r[3],
                       "path": r[4], "title": r[5]} for r in rows}

    # ---- vectors ----
    def put_vectors(self, items):
        """items: [(chunk_id, [float])]"""
        self.con.executemany("INSERT OR REPLACE INTO vectors VALUES(?,?,?)",
                             [(cid, len(v), struct.pack(f"{len(v)}f", *v)) for cid, v in items])

    def chunks_without_vectors(self):
        return self.con.execute(
            "SELECT c.chunk_id, c.text FROM chunks c LEFT JOIN vectors v ON c.chunk_id=v.chunk_id "
            "WHERE v.chunk_id IS NULL").fetchall()

    def all_vectors(self):
        for cid, dim, blob in self.con.execute("SELECT chunk_id, dim, vec FROM vectors"):
            yield cid, struct.unpack(f"{dim}f", blob)

    def vector_count(self):
        return self.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def doc_count(self):
        return self.con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
