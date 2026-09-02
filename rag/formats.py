"""検索結果の出力形式：TSV（スプレッドシート）/ Gemini 貼付テキスト / HTML / JSON"""
import html
import json
import os


def _rel(path, cfg):
    for d in cfg["source_dirs"]:
        if path.startswith(d):
            return os.path.relpath(path, d)
    return path


def to_json(result, cfg):
    out = dict(result)
    for h in out["hits"]:
        h["file"] = _rel(h["path"], cfg)
    return json.dumps(out, ensure_ascii=False, indent=2)


def to_tsv(result, cfg):
    """1行=1ヒット。セルに改行・タブが入らないよう潰す。
    スプレッドシートの AI 関数（例 =AI("要約して", A2:D9)）の参照範囲にそのまま使える。"""
    rows = ["順位\tファイル\tタイトル\tスコア\t本文"]
    for i, h in enumerate(result["hits"], 1):
        body = h["snippet"].replace("\t", " ").replace("\n", " ")
        rows.append(f"{i}\t{_rel(h['path'], cfg)}\t{h['title']}\t{h['score']}\t{body}")
    return "\n".join(rows)


def to_prompt(result, cfg, question=None):
    """ブラウザの Gemini / Claude にそのまま貼れる形式。"""
    q = question or result["query"]
    lines = ["以下は社内資料から検索した参考情報です。この情報のみを根拠に、"
             "質問に日本語で答えてください。根拠が無い場合は「資料に見当たらない」と答えてください。",
             "", f"# 質問", q, "", "# 参考情報"]
    for i, h in enumerate(result["hits"], 1):
        lines += [f"## [{i}] {h['title'] or '(無題)'}  （{_rel(h['path'], cfg)}）", h["snippet"], ""]
    return "\n".join(lines)


def to_html(result, cfg):
    e = html.escape
    parts = [f"<h2>検索: {e(result['query'])}</h2>",
             f"<p class='meta'>件数 {len(result['hits'])} / モード {e(result['mode'])}"
             f"{' / ベクトル併用' if result['vector_used'] else ' / キーワードのみ'}</p>"]
    for i, h in enumerate(result["hits"], 1):
        parts.append(
            f"<article class='hit'><h3>[{i}] {e(h['title'] or '(無題)')}</h3>"
            f"<p class='path'><a href='file:///{e(h['path'].replace(os.sep, '/'))}' target='_blank'>"
            f"{e(_rel(h['path'], cfg))}</a> <span class='score'>score {h['score']}</span></p>"
            f"<pre>{e(h['snippet'])}</pre></article>")
    return "\n".join(parts)


FORMATS = {"json": to_json, "tsv": to_tsv, "prompt": to_prompt, "html": to_html}
