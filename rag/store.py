"""SQLite に文書・チャンク・転置索引・埋め込みを保存する。
インデックスはファイル1個（index/rag.sqlite）なので Google ドライブでも持ち運べる。"""
import hashlib
import json
import os
import sqlite3
import struct

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  doc_id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT,
  mtime REAL, size INTEGER, sha1 TEXT, nchunks INTEGER, indexed_at TEXT);
CREATE TABLE IF NOT EXISTS chunks(
  chunk_id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER, text TEXT, length INTEGER);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS postings(term TEXT, chunk_id INTEGER, tf INTEGER);
CREATE INDEX IF NOT EXISTS ix_post_term ON postings(term);
CREATE INDEX IF NOT EXISTS ix_post_chunk ON postings(chunk_id);
CREATE TABLE IF NOT EXISTS vectors(chunk_id INTEGER PRIMARY KEY, dim INTEGER, vec BLOB);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


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
    def __init__(self, index_dir):
        os.makedirs(index_dir, exist_ok=True)
        self.path = os.path.join(index_dir, "rag.sqlite")
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.executescript(SCHEMA)

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
            self.con.executemany("INSERT INTO postings VALUES(?,?,?)", [(t, cid, n) for t, n in tf.items()])
        return doc_id, ids

    def commit(self):
        self.con.commit()

    # ---- stats for BM25 ----
    def stats(self):
        n, avg = self.con.execute("SELECT COUNT(*), COALESCE(AVG(length),0) FROM chunks").fetchone()
        return n, avg

    def postings(self, term):
        return self.con.execute("SELECT chunk_id, tf FROM postings WHERE term=?", (term,)).fetchall()

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
