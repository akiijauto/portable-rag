#!/usr/bin/env python3
"""ローカル Web UI（標準ライブラリのみ、外部通信なし）。
使い方:  python rag_app.py   → ブラウザで http://127.0.0.1:8765 を開く

索引更新は**別スレッドで走らせ、画面から進捗を見られる**ようにしてある。
リクエストの中で最後まで実行すると、その間サーバーが他の要求に応答できず、
「動いているのか止まっているのか分からない」状態になるため。
"""
import argparse
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

from rag import webui
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
header{display:flex;align-items:baseline;gap:12px}header h1{margin:0}
__PROGRESS_CSS__
</style></head><body>
<header><h1>ローカルRAG検索</h1><small class="meta" id="stat"></small>
<button id="exit" style="margin-left:auto">終了</button></header>
<form id="f"><input type="text" id="q" placeholder="検索語（例: 返品 送料 負担）" autofocus>
<select id="mode"><option value="hybrid">ハイブリッド</option><option value="bm25">キーワード</option><option value="vector">意味検索</option></select>
<input type="number" id="top" value="8" min="1" max="50" style="width:64px">
<button type="submit" id="btnSearch">検索</button>
<button type="button" id="reindex">差分インデックス更新</button><span id="status"></span></form>
__PROGRESS_HTML__
<div class="tabs"><button data-f="html">閲覧</button><button data-f="prompt">Gemini貼付用</button><button data-f="tsv">スプレッドシート用(TSV)</button><button id="copy">コピー</button></div>
<div id="out"></div>
<script>
__PROGRESS_JS__
let cur=null,fmt="html";const $=id=>document.getElementById(id);

// 検索中も「何秒経ったか」を出す。無反応との区別がつくようにするため
let tick=null;
function busy(on,label){
  const b=$('btnSearch');b.disabled=on;$('reindex').disabled=on;
  if(tick){clearInterval(tick);tick=null;}
  if(on){const t0=Date.now();
    $('status').textContent=label+' 0.0秒';
    tick=setInterval(()=>{$('status').textContent=label+' '+((Date.now()-t0)/1000).toFixed(1)+'秒';},100);
  }else{$('status').textContent='';}
}
async function run(){const q=$('q').value.trim();if(!q)return;
 const u='/api/search?'+new URLSearchParams({q,mode:$('mode').value,top:$('top').value});
 busy(true,'検索中…');
 try{const r=await fetch(u);cur=await r.json();render();
   busy(false);$('status').textContent=cur.hits+' 件';
 }catch(e){busy(false);$('status').textContent='検索に失敗しました: '+e;}
}
function render(){if(!cur)return;const o=$('out');
 if(fmt==='html'){o.innerHTML=cur.html;}else{o.innerHTML='<textarea id="ta"></textarea>';$('ta').value=cur[fmt];}}
$('f').onsubmit=e=>{e.preventDefault();run();};
document.querySelectorAll('.tabs button[data-f]').forEach(b=>b.onclick=()=>{fmt=b.dataset.f;render();});
$('copy').onclick=()=>{if(!cur)return;const t=fmt==='html'?cur.prompt:cur[fmt];navigator.clipboard.writeText(t);
 $('status').textContent='コピーしました';setTimeout(()=>$('status').textContent='',1500);};

// 索引更新は投げっぱなしにして、進捗は /api/progress を見に行く
$('reindex').onclick=async()=>{
  const r=await fetch('/api/reindex',{method:'POST'});
  const j=await r.json();
  if(j.error){alert(j.error);return;}
  $('reindex').disabled=true;$('btnSearch').disabled=false;   // 更新中も検索はできる
  startPolling();
};
function onJobEnd(s){$('reindex').disabled=false;loadStat();}
$('exit').onclick=doExit;

async function loadStat(){const r=await fetch('/api/stats');const s=await r.json();
 $('stat').textContent=`文書 ${s.docs} 件 / チャンク ${s.chunks} 件 / ベクトル ${s.vectors?'有効':'無効'} / 最終更新 ${s.last_build||'-'}`;}
loadStat();
// 起動時、既に更新が走っていれば拾う（ブラウザを開き直した場合）
fetch('/api/progress').then(r=>r.json()).then(s=>{if(s.state==='running'){$('reindex').disabled=true;startPolling();}});
</script></body></html>"""

PAGE = (PAGE.replace("__PROGRESS_CSS__", webui.PROGRESS_CSS)
            .replace("__PROGRESS_HTML__", webui.PROGRESS_HTML)
            .replace("__PROGRESS_JS__", webui.PROGRESS_JS))


def make_handler(cfg):
    searcher = Searcher(cfg)
    job = webui.Job()

    class H(BaseHTTPRequestHandler):
        server_ref = None                      # webui.serve が差し込む

        def _send(self, body, ctype="application/json; charset=utf-8", code=200):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                return self._send(PAGE, "text/html; charset=utf-8")
            if u.path == "/api/progress":
                return self._send(json.dumps(job.snapshot(), ensure_ascii=False))
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
                def work(log):
                    build(cfg, log=log)
                    searcher._vec_cache = None
                    log("画面の統計を更新しました")
                started = job.start("インデックス更新", work)
                if not started:
                    return self._send(json.dumps({"error": "すでに更新が動いています"},
                                                 ensure_ascii=False))
                return self._send(json.dumps({"ok": True}))
            if self.path == "/api/exit":
                self._send(json.dumps({"ok": True}))
                webui.request_shutdown(self.server_ref)
                return
            self._send("not found", "text/plain", 404)

        def log_message(self, fmt, *args):     # 静かに
            pass

    return H


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    sys.exit(webui.serve(make_handler(cfg), cfg["web_host"], cfg["web_port"],
                         "ローカルRAG検索", open_browser=not a.no_browser))
