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
        self.assertEqual(tsv.split("\n")[1].count("\t"), 4)  # 5列
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
