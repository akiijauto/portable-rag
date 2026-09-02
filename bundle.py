#!/usr/bin/env python3
"""系列まとめツール（中間ファイル生成）
HTML を「系列」ごとに集め、NotebookLM / Gemini に渡せる大きなテキストと、要約依頼用のプロンプトを作る。

系列の決め方（--by で選ぶ）:
  folder                 サブフォルダ名を系列にする
  name:<正規表現>        ファイル名にマッチさせ、(?P<series>...) の部分を系列名にする
                         例: name:^(?P<series>[^_]+)_
  field:<項目名>         本文中の「項目名: 値」「項目名 <tab> 値」の値を系列名にする  例: field:顧客名
  search:<検索語>        RAG で検索した上位 --top 件を 1 つの系列にする（先に build_index.py が必要）

モード（--mode）:
  merge     単純結合：系列の本文をそのまま連結して大きなファイルにする（NotebookLM 用と Gemini 用に分割）
  summary   まとめ：Gemini に貼って「厳選した要約」を作らせるプロンプトファイルを作る
  both      両方（既定）

--compact を付けると、系列内の半数以上の文書に共通する定型行（署名・ヘッダー等）を取り除いて小さくする。
引数なしで実行すると対話メニューになる。
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
from collections import Counter, OrderedDict

from rag.config import load_config
from rag.html_extract import extract

# 出力の目安。NotebookLM は 1 ソース 50 万語・最大 300 ソース、Gemini チャットは最大 10 ファイル。
LIMITS = {
    "notebooklm": {"chars_per_file": 300_000, "max_files": 300},
    "gemini": {"chars_per_file": 120_000, "max_files": 10},
}

SUMMARY_PROMPT = """あなたは社内資料の整理担当です。以下は「{series}」という系列の資料（{n}件中の {part}）です。
後日、AI に過去の経緯として渡すための「厳選した要約」を作ってください。

# 出力の条件
- 日本語。見出し付きの箇条書き。全体で {target} 文字以内
- 含める: 決定事項、経緯、金額・日付・番号などの具体値、繰り返し出てくる質問と回答、例外的な対応
- 除く: 挨拶、定型文、重複、資料に無い推測
- 各項目の末尾に根拠のファイル名を [ファイル名] の形で付ける
- 最後に「未解決・要確認」の節を置く

# 資料
{body}
"""

MERGE_PROMPT = """以下は「{series}」の第 {part} 部の資料です（複数部に分かれています。すべて受け取ってから質問に答えてください）。
"""


# ---------- 系列分け ----------
def scan_files(cfg):
    exts = tuple(cfg["extensions"])
    for d in cfg["source_dirs"]:
        for root, _, files in os.walk(d):
            for fn in sorted(files):
                if fn.lower().endswith(exts) and not fn.startswith(("~$", ".")):
                    yield d, os.path.join(root, fn)


def field_value(text, label):
    pat = re.compile(r"^\s*" + re.escape(label) + r"\s*[:：\t 　]+\s*(.+?)\s*$", re.M)
    m = pat.search(text)
    if not m:
        return None
    v = re.sub(r"[\\/:*?\"<>|]", "_", m.group(1))[:60]
    return v or None


def group_files(cfg, by, top=50):
    """{系列名: [(path, title, body)]} を返す。"""
    groups = OrderedDict()
    kind, _, arg = by.partition(":")
    if kind == "search":
        from rag.search import Searcher
        res = Searcher(cfg).search(arg, top_k=top)
        seen = OrderedDict()
        for h in res["hits"]:
            seen.setdefault(h["path"], None)
        docs = []
        for p in seen:
            t, b = extract(p)
            docs.append((p, t, b))
        key = re.sub(r"\s+", "_", arg)[:40]
        groups["検索_" + key] = docs
        return groups
    if kind == "name":
        rx = re.compile(arg)
        if "series" not in rx.groupindex:
            raise SystemExit("name: の正規表現には (?P<series>...) を含めてください")
    for base, path in scan_files(cfg):
        title, body = extract(path)
        if kind == "folder":
            rel = os.path.relpath(path, base)
            key = rel.split(os.sep)[0] if os.sep in rel else "(直下)"
        elif kind == "name":
            m = rx.search(os.path.basename(path))
            key = m.group("series") if m else "(未分類)"
        elif kind == "field":
            key = field_value(body, arg) or "(未分類)"
        else:
            raise SystemExit(f"不明な系列指定: {by}")
        groups.setdefault(key, []).append((path, title, body))
    return groups


# ---------- 圧縮 ----------
def compact(docs):
    """系列内の半数以上の文書に出る行（定型文）を除く。"""
    if len(docs) < 2:
        return docs
    counter = Counter()
    for _, _, body in docs:
        counter.update(set(l.strip() for l in body.split("\n") if len(l.strip()) >= 4))
    common = {l for l, c in counter.items() if c >= max(2, len(docs) * 0.5)}
    out = []
    for p, t, body in docs:
        lines = [l for l in body.split("\n") if l.strip() not in common]
        out.append((p, t, "\n".join(lines)))
    return out


# ---------- 出力 ----------
def doc_block(path, title, body, base_dirs):
    rel = path
    for d in base_dirs:
        if path.startswith(d):
            rel = os.path.relpath(path, d)
            break
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    return f"\n===== {rel} | {title or '(無題)'} | {mtime} =====\n{body}\n"


def split_blocks(blocks, chars_per_file, max_files):
    parts, cur, size = [], [], 0
    for b in blocks:
        while len(b) > chars_per_file:          # 1文書が上限超えなら強制分割
            head, b = b[:chars_per_file], b[chars_per_file:]
            if cur:
                parts.append("".join(cur)); cur, size = [], 0
            parts.append(head)
        if size + len(b) > chars_per_file and cur:
            parts.append("".join(cur)); cur, size = [], 0
        cur.append(b); size += len(b)
    if cur:
        parts.append("".join(cur))
    truncated = len(parts) > max_files
    return parts[:max_files], truncated


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build(cfg, by, mode="both", out_dir="bundles", do_compact=False, top=50, target=3000, log=print):
    groups = group_files(cfg, by, top)
    out_dir = os.path.abspath(out_dir)
    report = []
    for series, docs in groups.items():
        if do_compact:
            docs = compact(docs)
        blocks = [doc_block(p, t, b, cfg["source_dirs"]) for p, t, b in docs]
        total = sum(len(b) for b in blocks)
        sdir = os.path.join(out_dir, re.sub(r"[\\/:*?\"<>|]", "_", series))
        info = {"series": series, "docs": len(docs), "chars": total, "files": {}}
        if mode in ("merge", "both"):
            for target_name, lim in LIMITS.items():
                parts, trunc = split_blocks(blocks, lim["chars_per_file"], lim["max_files"])
                for i, p in enumerate(parts, 1):
                    head = MERGE_PROMPT.format(series=series, part=f"{i}/{len(parts)}") if target_name == "gemini" else ""
                    write(os.path.join(sdir, target_name, f"{series}_part{i:02d}.txt"), head + p)
                info["files"][target_name] = {"parts": len(parts), "truncated": trunc}
        if mode in ("summary", "both"):
            lim = LIMITS["gemini"]
            parts, trunc = split_blocks(blocks, lim["chars_per_file"] - 2000, lim["max_files"])
            for i, p in enumerate(parts, 1):
                write(os.path.join(sdir, "summary_prompt", f"{series}_要約依頼{i:02d}.txt"),
                      SUMMARY_PROMPT.format(series=series, n=len(docs), part=f"第{i}部/{len(parts)}部",
                                            target=target, body=p))
            note = os.path.join(sdir, f"{series}_要約.md")
            if not os.path.exists(note):
                write(note, f"# {series} 要約（Gemini の回答をここに貼る）\n\n作成日: \n元資料: {len(docs)} 件\n\n")
            info["files"]["summary_prompt"] = {"parts": len(parts), "truncated": trunc}
        report.append(info)
        log(f"{series}: {len(docs)}件 {total:,}文字 → " + ", ".join(
            f"{k} {v['parts']}分割{'（上限で打切り）' if v['truncated'] else ''}" for k, v in info["files"].items()))
    write_index(out_dir, by, mode, report)
    return report


def write_index(out_dir, by, mode, report):
    rows = "".join(
        f"<tr><td>{html.escape(r['series'])}</td><td>{r['docs']}</td><td>{r['chars']:,}</td>"
        + "".join(f"<td>{r['files'].get(k, {}).get('parts', '-')}{' ⚠' if r['files'].get(k, {}).get('truncated') else ''}</td>"
                  for k in ("notebooklm", "gemini", "summary_prompt")) + "</tr>" for r in report)
    page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>系列まとめ 一覧</title>
<style>body{{font-family:system-ui,Meiryo,sans-serif;max-width:900px;margin:24px auto;padding:0 16px}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:5px 8px}}th{{background:#f2f2f2}}</style></head><body>
<h1>系列まとめ 一覧</h1><p>系列: <code>{html.escape(by)}</code> / モード: {mode} / 作成: {datetime.datetime.now():%Y-%m-%d %H:%M}</p>
<table><tr><th>系列</th><th>文書数</th><th>文字数</th><th>NotebookLM 分割</th><th>Gemini 分割</th><th>要約依頼 分割</th></tr>{rows}</table>
<p>⚠ は上限（NotebookLM 300 / Gemini 10 ファイル）で打ち切ったもの。系列をさらに分けるか --compact を使ってください。</p>
<h2>使い方</h2><ol><li><b>要約を作る</b>: 各系列の <code>summary_prompt/</code> のファイルを 1 つずつ Gemini に貼り、返ってきた要約を <code>&lt;系列&gt;_要約.md</code> に貼る</li>
<li><b>NotebookLM</b>: <code>notebooklm/</code> のファイルをソースとして追加（最大 300）</li>
<li><b>Gemini に全文を渡す</b>: <code>gemini/</code> のファイルを添付（最大 10）</li></ol></body></html>"""
    write(os.path.join(out_dir, "index.html"), page)
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def interactive():
    print("=== 系列まとめツール ===")
    print("系列の決め方: 1) フォルダごと  2) ファイル名の規則  3) 文書内の項目  4) 検索結果")
    c = input("番号> ").strip()
    if c == "1":
        by = "folder"
    elif c == "2":
        print("ファイル名の先頭から最初の '_' までを系列名にします（変えたい場合は --by name:正規表現 で実行）")
        by = "name:^(?P<series>[^_]+)_"
    elif c == "3":
        by = "field:" + input("項目名（例: 顧客名）> ").strip()
    else:
        by = "search:" + input("検索語> ").strip()
    m = input("モード 1) 単純結合  2) 要約依頼  3) 両方（Enter=両方）> ").strip()
    mode = {"1": "merge", "2": "summary"}.get(m, "both")
    cp = input("定型行を除いて小さくしますか？ y/N> ").strip().lower() == "y"
    return by, mode, cp


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--by", help="folder | name:<正規表現> | field:<項目名> | search:<検索語>")
    ap.add_argument("--mode", default="both", choices=["merge", "summary", "both"])
    ap.add_argument("--out", default="bundles")
    ap.add_argument("--compact", action="store_true")
    ap.add_argument("--top", type=int, default=50, help="search: のとき集める件数")
    ap.add_argument("--target", type=int, default=3000, help="要約の目標文字数")
    ap.add_argument("--config", default="config.json")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if not a.by:
        a.by, a.mode, a.compact = interactive()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    build(cfg, a.by, a.mode, a.out, a.compact, a.top, a.target)
    print(f"出力先: {os.path.abspath(a.out)}  （index.html を開くと一覧）")
