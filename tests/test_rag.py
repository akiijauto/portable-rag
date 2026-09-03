"""機能テスト:  python -m pytest tests  または  python tests/test_rag.py"""
import json
import os
import shutil
import sys
import sqlite3
import tempfile
import threading
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from rag.chunker import split_chunks          # noqa: E402
from rag.config import load_config            # noqa: E402
from rag.formats import FORMATS               # noqa: E402
from rag.html_extract import extract          # noqa: E402
from rag.indexer import build                 # noqa: E402
from rag.search import Searcher               # noqa: E402
from rag.tokenizer import tokenize            # noqa: E402
from rag.store import Store                  # noqa: E402
from rag import syncdir                      # noqa: E402
from rag import webui                        # noqa: E402


class TestUnits(unittest.TestCase):
    def test_tokenize_ja_bigram_and_words(self):
        t = tokenize("送料 Shipping FEE 2025")
        self.assertIn("送料", t)
        self.assertIn("shipping", t)
        self.assertIn("2025", t)

    def test_chunks_overlap(self):
        text = "あ" * 100 + "。" + "い" * 100 + "。" + "う" * 100 + "。"
        ch = split_chunks(text, size=150, overlap=20)
        self.assertGreaterEqual(len(ch), 2)
        self.assertTrue(all(len(c) <= 170 for c in ch))

    def test_extract_skips_script_and_style(self):
        title, body = extract(os.path.join(ROOT, "sample_docs", "faq_返品.html"))
        self.assertEqual(title, "返品・交換ポリシー")
        self.assertNotIn("alert", body)
        self.assertIn("送料をお客様負担", body)

    def test_extract_cp932(self):
        title, body = extract(os.path.join(ROOT, "sample_docs", "cp932.html"))
        self.assertEqual(title, "CP932テスト")


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = os.path.join(cls.tmp, "docs")
        shutil.copytree(os.path.join(ROOT, "sample_docs"), cls.src)
        cfgp = os.path.join(cls.tmp, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"source_dirs": ["./docs"], "index_dir": "./index", "use_embeddings": "false"}, f)
        cls.cfg = load_config(cfgp)
        build(cls.cfg, log=lambda *_: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_search_hits_expected_doc(self):
        res = Searcher(self.cfg).search("返品 送料 負担", top_k=3)
        self.assertTrue(res["hits"])
        self.assertIn("faq_返品.html", res["hits"][0]["path"])
        self.assertFalse(res["vector_used"])

    def test_english_query(self):
        res = Searcher(self.cfg).search("invoice shipping refund", top_k=3)
        self.assertIn("memo_english.html", res["hits"][0]["path"])

    def test_formats(self):
        res = Searcher(self.cfg).search("請求書 発行", top_k=2)
        tsv = FORMATS["tsv"](res, self.cfg)
        self.assertEqual(len(tsv.split("\n")), len(res["hits"]) + 1)
        self.assertEqual(tsv.split("\n")[1].count("\t"), 5)  # 6列: 順位/ファイル/タイトル/一致度/意味的な近さ/本文
        self.assertIn("一致度", tsv.split("\n")[0])
        self.assertNotEqual(tsv.split("\n")[1].split("\t")[3], "-")  # BM25 の生スコアが入っている
        self.assertIn("# 参考情報", FORMATS["prompt"](res, self.cfg))
        self.assertIn("<article", FORMATS["html"](res, self.cfg))
        json.loads(FORMATS["json"](res, self.cfg))

    def test_incremental_update_and_delete(self):
        s = Searcher(self.cfg)
        n0 = s.store.doc_count()
        newf = os.path.join(self.src, "new_doc.html")
        with open(newf, "w", encoding="utf-8") as f:
            f.write("<html><title>新規</title><body>ユニークワード ゼブラ紅茶</body></html>")
        build(self.cfg, log=lambda *_: None)
        s = Searcher(self.cfg)
        self.assertEqual(s.store.doc_count(), n0 + 1)
        self.assertIn("new_doc.html", s.search("ゼブラ紅茶", top_k=1)["hits"][0]["path"])
        os.remove(newf)
        build(self.cfg, log=lambda *_: None)
        s = Searcher(self.cfg)
        self.assertEqual(s.store.doc_count(), n0)
        self.assertFalse(s.search("ゼブラ紅茶", top_k=1)["hits"])

    def test_long_doc_multi_chunks_limited_per_doc(self):
        res = Searcher(self.cfg).search("有給休暇 繰越", top_k=8)
        paths = [h["path"] for h in res["hits"]]
        self.assertLessEqual(paths.count(next(p for p in paths if "規程" in p)), 2)

    def test_unknown_config_key_fails(self):
        p = os.path.join(self.tmp, "bad.json")
        with open(p, "w") as f:
            json.dump({"typo_key": 1}, f)
        with self.assertRaises(ValueError):
            load_config(p)




class TestBundle(unittest.TestCase):
    def test_split_respects_limits(self):
        from bundle import split_blocks
        blocks = ["a" * 1000] * 30
        parts, trunc = split_blocks(blocks, 5000, 10)
        self.assertEqual(len(parts), 6)
        self.assertFalse(trunc)
        self.assertTrue(all(len(p) <= 5000 for p in parts))
        parts, trunc = split_blocks(blocks, 1000, 3)
        self.assertEqual(len(parts), 3)
        self.assertTrue(trunc)

    def test_compact_removes_common_lines(self):
        from bundle import compact
        docs = [("a", "t", "共通の署名行\n個別1"), ("b", "t", "共通の署名行\n個別2"), ("c", "t", "共通の署名行\n個別3")]
        out = compact(docs)
        self.assertTrue(all("共通の署名行" not in b for _, _, b in out))
        self.assertIn("個別2", out[1][2])

    def test_field_value(self):
        from bundle import field_value
        self.assertEqual(field_value("件名\t送料について\n本文 x", "件名"), "送料について")
        self.assertEqual(field_value("顧客名: 株式会社A/B", "顧客名"), "株式会社A_B")
        self.assertIsNone(field_value("なし", "顧客名"))

    def test_build_by_name(self):
        from bundle import build
        tmp = tempfile.mkdtemp()
        cfgp = os.path.join(tmp, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"source_dirs": [os.path.join(ROOT, "sample_docs")], "index_dir": "./index",
                       "use_embeddings": "false"}, f)
        rep = build(load_config(cfgp), "name:^(?P<series>[^_]+)_", "both", os.path.join(tmp, "out"), log=lambda *_: None)
        names = {r["series"] for r in rep}
        self.assertIn("faq", names)
        self.assertTrue(os.path.exists(os.path.join(tmp, "out", "faq", "gemini", "faq_part01.txt")))
        self.assertTrue(os.path.exists(os.path.join(tmp, "out", "faq", "summary_prompt", "faq_要約依頼01.txt")))
        self.assertTrue(os.path.exists(os.path.join(tmp, "out", "index.html")))
        shutil.rmtree(tmp, ignore_errors=True)

    def test_bundle_app_inputs_and_preview(self):
        import bundle_app
        self.assertEqual(bundle_app.make_by({"kind": "folder"}), "folder")
        self.assertEqual(bundle_app.make_by({"kind": "name", "sep": "_"}), "name:^(?P<series>[^_]+)_")
        self.assertEqual(bundle_app.make_by({"kind": "name", "sep": "-", "regex": "^(?P<series>x)"}), "name:^(?P<series>x)")
        self.assertEqual(bundle_app.make_by({"kind": "field", "label": " 顧客名 "}), "field:顧客名")
        for bad in ({}, {"kind": "field", "label": ""}, {"kind": "search", "query": " "},
                    {"kind": "name", "regex": "^([a-z]+)"}, {"kind": "name", "sep": "|"}):
            with self.assertRaises(ValueError):
                bundle_app.make_by(bad)
        self.assertEqual(bundle_app.make_mode({"full": True, "summary": True}), "both")
        self.assertEqual(bundle_app.make_mode({"summary": True}), "summary")
        with self.assertRaises(ValueError):
            bundle_app.make_mode({})
        tmp = tempfile.mkdtemp()
        cfgp = os.path.join(tmp, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"source_dirs": [os.path.join(ROOT, "sample_docs")], "index_dir": "./index",
                       "use_embeddings": "false"}, f)
        rows = bundle_app.preview(load_config(cfgp), "name:^(?P<series>[^_]+)_")
        self.assertIn("faq", {r["series"] for r in rows})
        self.assertEqual(sum(r["docs"] for r in rows), 6)          # サンプル6件がすべてどこかに入る
        self.assertTrue(all(r["chars"] is None for r in rows))     # name はパスだけで数える（本文は読まない）
        shutil.rmtree(tmp, ignore_errors=True)



class TestSyncDirWarning(unittest.TestCase):
    """索引の保存先がクラウド同期フォルダかどうかの判定。
    2026-09-03、同期配下に置いたせいで索引作成が10倍遅くなった実測を受けて追加。"""

    def test_detects_google_drive_variants(self):
        # 日本語版 Windows のフォルダ名を取りこぼしていた実バグの回帰テスト
        for path in [r"G:\マイドライブ\index", r"C:\Users\x\Google ドライブ\index",
                     r"C:\Users\x\Googleドライブ\index", r"C:\Users\x\GoogleDrive\index"]:
            self.assertEqual(syncdir.detect(path), "Google ドライブ", path)

    def test_detects_other_services(self):
        self.assertEqual(syncdir.detect(r"C:\Users\x\OneDrive\index"), "OneDrive")
        self.assertEqual(syncdir.detect(r"C:\Users\x\Dropbox\index"), "Dropbox")

    def test_local_path_is_not_flagged(self):
        self.assertIsNone(syncdir.detect(r"C:\portable-rag-index"))
        self.assertIsNone(syncdir.detect(os.path.join(ROOT, "index")))

    def test_warn_emits_message_only_when_synced(self):
        msgs = []
        self.assertEqual(syncdir.warn_if_synced(r"G:\マイドライブ\index", msgs.append), "Google ドライブ")
        self.assertTrue(any("警告" in m for m in msgs))
        msgs.clear()
        self.assertIsNone(syncdir.warn_if_synced(r"C:\local\index", msgs.append))
        self.assertEqual(msgs, [])                                  # 同期外では何も出さない


class TestSchemaMigration(unittest.TestCase):
    """旧形式（postings.term に文字列）から新形式（postings.term_id）への移行。"""

    OLD_SCHEMA = """
    CREATE TABLE docs(doc_id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT,
      mtime REAL, size INTEGER, sha1 TEXT, nchunks INTEGER, indexed_at TEXT);
    CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER,
      text TEXT, length INTEGER);
    CREATE TABLE postings(term TEXT, chunk_id INTEGER, tf INTEGER);
    CREATE INDEX ix_post_term ON postings(term);
    CREATE INDEX ix_post_chunk ON postings(chunk_id);
    CREATE TABLE vectors(chunk_id INTEGER PRIMARY KEY, dim INTEGER, vec BLOB);
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    """
    ROWS = [("返品", 1, 2), ("送料", 1, 1), ("返品", 2, 5), ("あい", 3, 1)]

    def _make_old(self):
        d = tempfile.mkdtemp()
        con = sqlite3.connect(os.path.join(d, "rag.sqlite"))
        con.executescript(self.OLD_SCHEMA)
        con.execute("INSERT INTO chunks VALUES(1,1,0,'返品と送料',5)")
        con.execute("INSERT INTO chunks VALUES(2,1,1,'返品のみ',4)")
        con.execute("INSERT INTO chunks VALUES(3,2,0,'あい',2)")
        con.executemany("INSERT INTO postings VALUES(?,?,?)", self.ROWS)
        con.commit()
        con.close()
        return d

    def test_migration_preserves_every_posting(self):
        d = self._make_old()
        try:
            st = Store(d)
            got = set(st.con.execute(
                "SELECT t.term, p.chunk_id, p.tf FROM postings p "
                "JOIN terms t ON t.term_id = p.term_id"))
            self.assertEqual(got, set(self.ROWS))
            self.assertEqual(st.get_meta("schema_version"), 2)
            self.assertEqual(sorted(st.postings("返品")), [(1, 2), (2, 5)])
            self.assertEqual(st.postings("存在しない語"), [])        # 未知語は空。例外にしない
            st.con.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_migration_is_atomic_when_interrupted(self):
        """途中で落ちても旧形式のまま残り、やり直せば移行できる。
        索引を後からまとめて作る方式を採らなかったのは、この中断時の故障を避けるため。"""
        import rag.store as store_mod

        class Flaky(sqlite3.Connection):
            def execute(self, sql, *a):
                if sql.strip().upper().startswith("INSERT INTO POSTINGS_NEW"):
                    raise KeyboardInterrupt("中断をシミュレート")
                return super().execute(sql, *a)

        d = self._make_old()
        real = store_mod.sqlite3
        store_mod.sqlite3 = types.SimpleNamespace(
            connect=lambda p, **kw: sqlite3.connect(p, factory=Flaky, **kw))
        try:
            with self.assertRaises(KeyboardInterrupt):
                store_mod.Store(d)
        finally:
            store_mod.sqlite3 = real

        try:
            con = sqlite3.connect(os.path.join(d, "rag.sqlite"))
            cols = [r[1] for r in con.execute("PRAGMA table_info(postings)")]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(cols, ["term", "chunk_id", "tf"])       # 旧形式のまま無傷
            self.assertNotIn("postings_new", tables)                 # 中間テーブルが残らない
            self.assertEqual(con.execute("SELECT COUNT(*) FROM postings").fetchone()[0], len(self.ROWS))
            con.close()

            st = Store(d)                                            # やり直せば移行できる
            self.assertEqual([r[1] for r in st.con.execute("PRAGMA table_info(postings)")],
                             ["term_id", "chunk_id", "tf"])
            st.con.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cache_size_pragma_is_applied(self):
        """既定の2MBのままだと索引が育った時点で急に遅くなるため、明示的に広げている。"""
        d = tempfile.mkdtemp()
        try:
            st = Store(d, cache_mb=64)
            self.assertEqual(st.con.execute("PRAGMA cache_size").fetchone()[0], -65536)
            st.con.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)



class TestConfigEncoding(unittest.TestCase):
    """config.json はユーザーがメモ帳で編集する。文字コードと書式の事故を親切に扱う。"""

    def _write(self, data, encoding):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "config.json")
        with open(p, "wb") as f:
            f.write(data.encode(encoding))
        return d, p

    def test_reads_cp932_saved_config(self):
        # メモ帳の ANSI 保存。index_dir に日本語パスを書くと utf-8 決め打ちでは落ちていた
        body = '{"index_dir": "C:/索引フォルダ", "source_dirs": ["./sample_docs"]}'
        d, p = self._write(body, "cp932")
        try:
            cfg = load_config(p)
            self.assertTrue(cfg["index_dir"].endswith("索引フォルダ"))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reads_utf8_bom_config(self):
        body = '{"top_k": 3}'
        d, p = self._write(body, "utf-8-sig")
        try:
            self.assertEqual(load_config(p)["top_k"], 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_broken_json_explains_how_to_fix(self):
        d, p = self._write('{"top_k": 3,}', "utf-8")       # 末尾の余分なカンマ
        try:
            with self.assertRaises(ValueError) as cm:
                load_config(p)
            self.assertIn("JSON", str(cm.exception))
            self.assertIn("カンマ", str(cm.exception))     # 直し方まで書く
        finally:
            shutil.rmtree(d, ignore_errors=True)



class TestJobProgress(unittest.TestCase):
    """重い処理を背後で走らせ、進捗を読み取れること。
    2026-09-03、「ボタンを押したあと動いているのか止まっているのか分からない」という
    要望を受けて追加。元は単一スレッドで、処理中はサーバーが一切応答できなかった。"""

    def test_reports_progress_while_running(self):
        job = webui.Job()
        gate = threading.Event()

        def work(log):
            log("1件目")
            gate.wait(5)                                   # 実行中の状態を観測するため止める
            log("2件目")

        self.assertTrue(job.start("テスト処理", work))
        for _ in range(50):                                # 最初の行が出るまで待つ
            if job.snapshot()["lines"]:
                break
            time.sleep(0.02)
        s = job.snapshot()
        self.assertEqual(s["state"], "running")            # 実行中でも snapshot は返る
        self.assertEqual(s["name"], "テスト処理")
        self.assertEqual(s["lines"], ["1件目"])
        self.assertFalse(job.start("別の処理", lambda log: None))   # 二重起動はしない

        gate.set()
        for _ in range(100):
            if job.snapshot()["state"] != "running":
                break
            time.sleep(0.02)
        s = job.snapshot()
        self.assertEqual(s["state"], "done")
        self.assertEqual(s["lines"], ["1件目", "2件目"])
        self.assertTrue(job.start("次の処理", lambda log: None))    # 完了後は再度開始できる

    def test_error_is_captured_not_raised(self):
        """処理が落ちても画面に理由が出る。サーバーごと死なせない。"""
        job = webui.Job()
        job.start("壊れる処理", lambda log: (_ for _ in ()).throw(ValueError("わざと失敗")))
        for _ in range(100):
            if job.snapshot()["state"] != "running":
                break
            time.sleep(0.02)
        s = job.snapshot()
        self.assertEqual(s["state"], "error")
        self.assertIn("わざと失敗", s["error"])
        self.assertTrue(any("エラー" in ln for ln in s["lines"]))

    def test_idle_snapshot_is_safe(self):
        s = webui.Job().snapshot()
        self.assertEqual(s["state"], "idle")
        self.assertEqual(s["elapsed"], 0)
        self.assertEqual(s["lines"], [])

    def test_old_lines_are_dropped_with_a_note(self):
        job = webui.Job()
        for i in range(webui.MAX_LINES + 50):
            job.log(f"line{i}")
        s = job.snapshot()
        self.assertLessEqual(len(s["lines"]), webui.MAX_LINES)
        self.assertIn("省略", s["lines"][0])                # 捨てた事実は残す


class TestBatFiles(unittest.TestCase):
    """終了ボタンで窓が閉じるには、正常終了時に pause してはいけない。"""

    def test_web_apps_pause_only_on_error(self):
        for name in ("run_search.bat", "launcher.bat", "bundle.bat"):
            path = os.path.join(ROOT, name)
            with open(path, "rb") as f:
                text = f.read().decode("cp932")
            self.assertIn("if errorlevel 1", text, name)
            # pause が errorlevel の判定より前に単独で現れていないこと
            self.assertLess(text.index("if errorlevel 1"), text.index("pause"), name)

    def test_build_index_always_pauses(self):
        # こちらは結果を読ませたいので常に止める
        with open(os.path.join(ROOT, "build_index.bat"), "rb") as f:
            text = f.read().decode("cp932")
        self.assertIn("pause", text)
        self.assertNotIn("if errorlevel", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
