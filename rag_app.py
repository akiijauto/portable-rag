#!/usr/bin/env python3
"""ローカル Web UI（標準ライブラリのみ、外部通信なし）。
使い方:  python rag_app.py   → ブラウザで http://127.0.0.1:8765 を開く"""
import argparse
import json
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from rag.config import load_config
from rag.formats import FORMATS
from rag.indexer import build
from rag.search import Searcher

PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>ローカルRAG検索</title>
<style>
body{font-family:system-ui,"Segoe UI","Meiryo",sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222}
form{display:flex;gap:8px;flex-wrap:wrap}input[type=text]{flex:1;min-width:280px;font-size:16px;padding:8px}
button{padding:8px 14px;font-size:14px;cursor:pointer}.meta,.path{color:#666;font-size:13px}
.hit{border:1px solid #ddd;border-radius:6px;padding:8px 12px;margin:10px 0}.hit h3{margin:4px 0;font-size:16px}
pre{white-space:pre-wrap;background:#f7f7f7;padding:8px;border-radius:4px;font-size:13px;margin:6px 0}
textarea{width:100%;height:220px;font-size:13px}.tabs button{margin-right:6px}#status{margin-left:8px;color:#0a7}
</style></head><body>
<h1>ローカルRAG検索 <small class="meta" id="stat"></small></h1>
<form id="f"><input type="text" id="q" placeholder="検索語（例: 返品 送料 負担）" autofocus>
<select id="mode"><option value="hybrid">ハイブリッド</option><option value="bm25">キーワード</option><option value="vector">意味検索</option></select>
<input type="number" id="top" value="8" min="1" max="50" style="width:64px">
<button type="submit">検索</button><button type="button" id="reindex">差分インデックス更新</button><span id="status"></span></form>
<div class="tabs"><button data-f="html">閲覧</button><button data-f="prompt">Gemini貼付用</button><button data-f="tsv">スプレッドシート用(TSV)</button><button id="copy">コピー</button></div>
<div id="out"></div>
<script>
let cur=null,fmt="html";const $=id=>document.getElementById(id);
async function run(){const q=document.getElementById('q').value.trim();if(!q)return;
 const u='/api/search?'+new URLSearchParams({q,mode:$('mode').value,top:$('top').value});
 const r=await fetch(u);cur=await r.json();render();}
function render(){if(!cur)return;const o=document.getElementById('out');
 if(fmt==='html'){o.innerHTML=cur.html;}else{o.innerHTML='<textarea id="ta"></textarea>';document.getElementById('ta').value=cur[fmt];}}
document.getElementById('f').onsubmit=e=>{e.preventDefault();run();};
document.querySelectorAll('.tabs button[data-f]').forEach(b=>b.onclick=()=>{fmt=b.dataset.f;render();});
document.getElementById('copy').onclick=()=>{if(!cur)return;const t=fmt==='html'?cur.prompt:cur[fmt];navigator.clipboard.writeText(t);$('status').textContent='コピーしました';setTimeout(()=>$('status').textContent='',1500);};
document.getElementById('reindex').onclick=async()=>{$('status').textContent='更新中…';const r=await fetch('/api/reindex',{method:'POST'});$('status').textContent=(await r.json()).log;loadStat();};
async function loadStat(){const r=await fetch('/api/stats');const s=await r.json();document.getElementById('stat').textContent=`文書 ${s.docs} 件 / チャンク ${s.chunks} 件 / ベクトル ${s.vectors?'有効':'無効'} / 最終更新 ${s.last_build||'-'}`;}
loadStat();
</script></body></html>"""


def make_handler(cfg):
    searcher = Searcher(cfg)

    class H(BaseHTTPRequestHandler):
        def _send(self, body, ctype="application/json; charset=utf-8", code=200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                return self._send(PAGE, "text/html; charset=utf-8")
            if u.path == "/api/stats":
                st = searcher.store
                return self._send(json.dumps({
                    "docs": st.doc_count(), "chunks": st.stats()[0],
                    "vectors": searcher.vectors_enabled(), "last_build": st.get_meta("last_build")}))
            if u.path == "/api/search":
                q = qs.get("q", [""])[0]
                mode = qs.get("mode", ["hybrid"])[0]
                top = int(qs.get("top", [cfg["top_k"]])[0])
                res = searcher.search(q, top_k=top, mode=mode)
                out = {k: f(res, cfg) for k, f in FORMATS.items()}
                out["hits"] = len(res["hits"])
                return self._send(json.dumps(out, ensure_ascii=False))
            self._send("not found", "text/plain", 404)

        def do_POST(self):
            if self.path == "/api/reindex":
                logs = []
                build(cfg, log=logs.append)
                searcher._vec_cache = None
                return self._send(json.dumps({"log": " / ".join(logs) or "ok"}, ensure_ascii=False))
            self._send("not found", "text/plain", 404)

        def log_message(self, fmt, *args):  # 静かに
            pass

    return H


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    srv = HTTPServer((cfg["web_host"], cfg["web_port"]), make_handler(cfg))
    url = f"http://{cfg['web_host']}:{cfg['web_port']}/"
    print(f"起動: {url}  (Ctrl+C で終了)")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
