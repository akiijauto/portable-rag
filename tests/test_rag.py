"""機能テスト:  python -m pytest tests  または  python tests/test_rag.py"""
import json
import os
import shutil
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
