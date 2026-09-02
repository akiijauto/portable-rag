#!/usr/bin/env python3
"""系列まとめ（ブラウザ画面版）
黒い窓（コマンドプロンプト）で番号を打つ代わりに、ブラウザの画面で選んで実行する。
標準ライブラリのみ・外部通信なし。まとめる処理そのものは bundle.py と同じものを呼ぶ。
使い方:  python bundle_app.py   → http://127.0.0.1:8768 が開く
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import bundle
from rag.config import load_config

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "bundles")

# 画面の「区切り文字」の選択肢 → 正規表現に変換する
SEPARATORS = {"_": "_（アンダーバー）", "-": "-（ハイフン）", " ": "（空白）", ".": ".（ドット）"}


def make_by(p):
    """画面の入力（kind と付随項目）を bundle.py の --by 文字列にする。"""
    kind = p.get("kind")
    if kind == "folder":
        return "folder"
    if kind == "name":
        rx = (p.get("regex") or "").strip()
        if rx:
            if "(?P<series>" not in rx:
                raise ValueError("正規表現には (?P<series>...) を含めてください")
            return "name:" + rx
        sep = p.get("sep") or "_"
        if sep not in SEPARATORS:
            raise ValueError("区切り文字の指定が不正です")
        return "name:^(?P<series>[^" + re.escape(sep) + "]+)" + re.escape(sep)
    if kind == "field":
        label = (p.get("label") or "").strip()
        if not label:
            raise ValueError("項目名を入れてください（例: 顧客名）")
        return "field:" + label
    if kind == "search":
        q = (p.get("query") or "").strip()
        if not q:
            raise ValueError("検索語を入れてください")
        return "search:" + q
    raise ValueError("まとめ方を選んでください")


def make_mode(p):
    full, summary = bool(p.get("full")), bool(p.get("summary"))
    if full and summary:
        return "both"
    if full:
        return "merge"
    if summary:
        return "summary"
    raise ValueError("作るものを1つ以上選んでください")


def source_info(cfg):
    dirs = []
    total = 0
    for d in cfg["source_dirs"]:
        n = sum(1 for base, _ in bundle.scan_files({"source_dirs": [d], "extensions": cfg["extensions"]}))
        dirs.append({"path": d, "exists": os.path.isdir(d), "files": n})
        total += n
    return {"dirs": dirs, "total": total}


def preview(cfg, by, top=50):
    """書き込みなしで「どんな系列が何件できるか」を返す。folder / name はパスだけで数えるので速い。"""
    kind, _, arg = by.partition(":")
    if kind in ("folder", "name"):
        rx = re.compile(arg) if kind == "name" else None
        counts = {}
        for base, path in bundle.scan_files(cfg):
            if kind == "folder":
                rel = os.path.relpath(path, base)
                key = rel.split(os.sep)[0] if os.sep in rel else "(直下)"
            else:
                m = rx.search(os.path.basename(path))
                key = m.group("series") if m else "(未分類)"
            counts[key] = counts.get(key, 0) + 1
        rows = [{"series": k, "docs": v, "chars": None} for k, v in counts.items()]
    else:
        groups = bundle.group_files(cfg, by, top)
        rows = [{"series": k, "docs": len(v), "chars": sum(len(b) for _, _, b in v)} for k, v in groups.items()]
    rows.sort(key=lambda r: -r["docs"])
    return rows


def run(cfg, p):
    by = make_by(p)
    mode = make_mode(p)
    logs = []
    report = bundle.build(cfg, by, mode, OUT_DIR, bool(p.get("compact")),
                          int(p.get("top") or 50), int(p.get("target") or 3000), log=logs.append)
    return {"out": OUT_DIR, "index": os.path.join(OUT_DIR, "index.html"),
            "report": report, "log": logs, "by": by, "mode": mode}


def open_folder(path):
    """出力フォルダ配下だけを Explorer で開く（それ以外は開かない）。"""
    path = os.path.abspath(path)
    if not path.startswith(os.path.abspath(OUT_DIR)) or not os.path.exists(path):
        raise ValueError("出力フォルダの中だけ開けます")
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


PAGE = r"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>系列まとめ</title>
<style>
body{font-family:system-ui,"Segoe UI","Meiryo",sans-serif;margin:0;background:#f3f5f8;color:#222;line-height:1.6}
header{background:#1f3a5f;color:#fff;padding:14px 20px}header h1{font-size:20px;margin:0}header p{margin:4px 0 0;font-size:13px;opacity:.9}
main{max-width:880px;margin:0 auto;padding:16px}
.card{background:#fff;border:1px solid #d7dce3;border-radius:12px;padding:16px 18px;margin:14px 0}
.card h2{font-size:16px;margin:0 0 6px;display:flex;align-items:center;gap:8px}
.card h2 .n{display:inline-flex;width:26px;height:26px;border-radius:50%;background:#2b6cb0;color:#fff;font-size:14px;align-items:center;justify-content:center}
.lead{color:#555;font-size:13px;margin:0 0 10px}
.choice{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}
.choice label{display:block;border:2px solid #d7dce3;border-radius:10px;padding:10px 12px;cursor:pointer;background:#fafbfc}
.choice label:hover{border-color:#8fb3e0}.choice label.on{border-color:#2b6cb0;background:#eef4fb}
.choice input{margin-right:6px}.choice b{font-size:15px}.choice .d{display:block;font-size:12px;color:#555;margin-top:2px}
.opt{display:none;margin-top:12px;padding:12px;background:#f7f9fc;border-radius:8px}.opt.on{display:block}
.opt label{display:block;margin:6px 0;font-size:13px}.opt input[type=text],.opt input[type=number],.opt select{font-size:14px;padding:6px;width:100%;max-width:420px;box-sizing:border-box}
.opt .ex{font-size:12px;color:#666}
button{font-size:14px;padding:8px 16px;border-radius:8px;border:1px solid #2b6cb0;background:#fff;color:#2b6cb0;cursor:pointer}
button.primary{background:#2b6cb0;color:#fff;font-size:17px;padding:12px 28px}button.primary:hover{background:#1f4f85}
button:disabled{opacity:.5;cursor:default}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}th,td{border:1px solid #ddd;padding:6px 8px;text-align:left}th{background:#f2f2f2}
td.num{text-align:right;white-space:nowrap}
.warn{color:#b00}.ok{color:#0a7}.muted{color:#777;font-size:12px}
#preview .sum{font-size:14px;margin:8px 0 0}
.chk label{display:block;margin:6px 0;font-size:14px}.chk .d{display:block;font-size:12px;color:#555;margin-left:24px}
details{margin-top:8px;font-size:13px}summary{cursor:pointer;color:#2b6cb0}
#result{display:none}#result.on{display:block}
.next li{margin:6px 0}
.src{font-size:13px;background:#f7f9fc;padding:8px 12px;border-radius:8px;margin-bottom:10px}
.src .warn{display:block}
.spin{display:inline-block;width:14px;height:14px;border:2px solid #ccc;border-top-color:#2b6cb0;border-radius:50%;animation:s .8s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes s{to{transform:rotate(360deg)}}
</style></head><body>
<header><h1>系列まとめ</h1><p>たくさんの文書を「系列」（テーマや相手ごとのグループ）に分けて、NotebookLM や Gemini にそのまま渡せる大きなファイルにまとめます。</p></header>
<main>
<div class="card"><div class="src" id="src">対象フォルダを確認中…</div>
<h2><span class="n">1</span>どうやって分けるか選ぶ</h2>
<p class="lead">文書を何のまとまりで分けるかを選びます。選んだら「分け方を確認」を押すと、実際に何個のグループができるかが分かります（まだファイルは作られません）。</p>
<div class="choice">
<label><input type="radio" name="kind" value="folder"><b>フォルダごと</b><span class="d">対象フォルダのすぐ下にあるフォルダ名で分けます。すでにフォルダで整理してある場合はこれ。</span></label>
<label><input type="radio" name="kind" value="name"><b>ファイル名の先頭</b><span class="d">ファイル名の最初の区切り文字までを名前にします。例: <code>faq_返品.html</code> → 「faq」</span></label>
<label><input type="radio" name="kind" value="field"><b>文書の中の項目</b><span class="d">文書に「顧客名: ○○」のような行があるとき、その値で分けます。</span></label>
<label><input type="radio" name="kind" value="search"><b>検索して集める</b><span class="d">検索語に関係する文書だけを集めて 1 つのグループにします。</span></label>
</div>
<div class="opt" id="opt-name"><label>区切り文字 <select id="sep"><option value="_">_（アンダーバー）</option><option value="-">-（ハイフン）</option><option value=" ">（空白）</option><option value=".">.（ドット）</option></select></label>
<details><summary>詳しい人向け：正規表現で指定する</summary><label><input type="text" id="regex" placeholder="例: ^(?P&lt;series&gt;[^_]+)_"><span class="ex">(?P&lt;series&gt;...) の部分がグループ名になります。ここに入れると区切り文字の設定は無視されます。</span></label></details></div>
<div class="opt" id="opt-field"><label>項目名 <input type="text" id="label" placeholder="例: 顧客名、件名、案件番号"><span class="ex">文書の中で「項目名: 値」「項目名<b>[Tab]</b>値」と書かれている行を探します。</span></label></div>
<div class="opt" id="opt-search"><label>検索語 <input type="text" id="query" placeholder="例: 返品 送料"></label><label>集める件数（上位から） <input type="number" id="topn" value="50" min="1" max="500" style="max-width:120px"></label><span class="ex">先に「RAG 索引更新」を実行しておく必要があります。</span></div>
<p style="margin:12px 0 0"><button id="btnPreview">分け方を確認</button> <span id="pvStatus" class="muted"></span></p>
<div id="preview"></div>
</div>

<div class="card"><h2><span class="n">2</span>何を作るか選ぶ</h2>
<div class="chk">
<label><input type="checkbox" id="full" checked> 全文をまとめたファイル<span class="d">NotebookLM 用（1ファイル30万字まで・最大300個）と Gemini 用（1ファイル12万字まで・最大10個）の2種類を作ります。</span></label>
<label><input type="checkbox" id="summary" checked> Gemini に要約させるための依頼文<span class="d">貼り付けるだけで「厳選した要約」を返してくれる文章です。要約は後で検索対象にもできます。</span></label>
<label><input type="checkbox" id="compact"> 定型文を取り除いて小さくする<span class="d">署名やヘッダーなど、グループ内の半分以上の文書に同じ行があれば取り除きます。「上限を超えた」と出たときに試してください。</span></label>
</div>
<details><summary>細かい設定</summary><label style="font-size:13px">要約の目標文字数 <input type="number" id="target" value="3000" min="500" max="20000" style="width:100px"></label></details>
</div>

<div class="card"><h2><span class="n">3</span>まとめを作る</h2>
<p class="lead">「1」と「2」を選んだら押してください。文書の数によっては数十秒かかります。</p>
<button class="primary" id="btnRun">まとめを作る</button> <span id="runStatus" class="muted"></span>
<div id="result"></div>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
async function api(p,body){const r=await fetch(p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});return r.json();}
function esc(s){return String(s??'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
function kind(){const r=document.querySelector('input[name=kind]:checked');return r?r.value:null;}
function params(){return {kind:kind(),sep:$('sep').value,regex:$('regex').value,label:$('label').value,query:$('query').value,top:+$('topn').value,
 full:$('full').checked,summary:$('summary').checked,compact:$('compact').checked,target:+$('target').value};}
document.querySelectorAll('input[name=kind]').forEach(r=>r.onchange=()=>{document.querySelectorAll('.choice label').forEach(l=>l.classList.toggle('on',l.querySelector('input').checked));
 ['name','field','search'].forEach(k=>$('opt-'+k).classList.toggle('on',kind()===k));$('preview').innerHTML='';});
async function loadSrc(){const s=await api('/api/source');const el=$('src');
 if(!s.dirs.length){el.innerHTML='<span class="warn">検索対象フォルダが設定されていません。コントロールUI の「検索対象フォルダ」で設定してください。</span>';return;}
 el.innerHTML='対象: <b>'+s.total.toLocaleString()+' 件</b> の文書 　'+s.dirs.map(d=>d.exists?esc(d.path)+'（'+d.files.toLocaleString()+' 件）':'<span class="warn">'+esc(d.path)+'（フォルダが見つかりません）</span>').join('、');}
$('btnPreview').onclick=async()=>{if(!kind()){alert('まず分け方を選んでください');return;}
 $('pvStatus').innerHTML='<span class="spin"></span>数えています…';$('btnPreview').disabled=true;
 const r=await api('/api/preview',params());$('btnPreview').disabled=false;$('pvStatus').textContent='';
 if(r.error){$('preview').innerHTML='<p class="warn">'+esc(r.error)+'</p>';return;}
 if(!r.rows.length){$('preview').innerHTML='<p class="warn">対象の文書が見つかりませんでした。</p>';return;}
 const un=r.rows.filter(x=>x.series==='(未分類)').reduce((a,x)=>a+x.docs,0);
 let h='<p class="sum"><b>'+r.rows.length+' 個</b>のグループに分かれます。'+(un?'<span class="warn">（うち「(未分類)」'+un+' 件：分け方に当てはまらなかった文書です）</span>':'')+'</p>';
 h+='<table><tr><th>グループ名</th><th>文書数</th>'+(r.rows[0].chars!=null?'<th>文字数</th>':'')+'</tr>';
 r.rows.slice(0,50).forEach(x=>{h+='<tr><td>'+esc(x.series)+'</td><td class="num">'+x.docs+'</td>'+(x.chars!=null?'<td class="num">'+x.chars.toLocaleString()+'</td>':'')+'</tr>';});
 h+='</table>'+(r.rows.length>50?'<p class="muted">（上位50件を表示）</p>':'');$('preview').innerHTML=h;};
$('btnRun').onclick=async()=>{if(!kind()){alert('「1」で分け方を選んでください');return;}
 if(!$('full').checked&&!$('summary').checked){alert('「2」で作るものを1つ以上選んでください');return;}
 $('btnRun').disabled=true;$('runStatus').innerHTML='<span class="spin"></span>作成中… 文書が多いと数十秒かかります';$('result').classList.remove('on');
 const r=await api('/api/run',params());$('btnRun').disabled=false;
 if(r.error){$('runStatus').innerHTML='<span class="warn">エラー: '+esc(r.error)+'</span>';return;}
 $('runStatus').innerHTML='<span class="ok">✔ できました</span>';render(r);};
function render(r){const anyTrunc=r.report.some(x=>Object.values(x.files).some(f=>f.truncated));
 let h='<p style="margin-top:12px">出力先: <code>'+esc(r.out)+'</code> <button data-p="'+esc(r.out)+'" class="open">フォルダを開く</button></p>';
 h+='<table><tr><th>グループ名</th><th>文書数</th><th>文字数</th>'+(r.mode!=='summary'?'<th>NotebookLM 用</th><th>Gemini 用</th>':'')+(r.mode!=='merge'?'<th>要約依頼文</th>':'')+'<th></th></tr>';
 r.report.forEach(x=>{const f=k=>{const v=x.files[k];if(!v)return'<td>-</td>';return'<td class="num">'+v.parts+' ファイル'+(v.truncated?' <span class="warn">⚠一部省略</span>':'')+'</td>';};
  h+='<tr><td>'+esc(x.series)+'</td><td class="num">'+x.docs+'</td><td class="num">'+x.chars.toLocaleString()+'</td>'+(r.mode!=='summary'?f('notebooklm')+f('gemini'):'')+(r.mode!=='merge'?f('summary_prompt'):'')+
   '<td><button class="open" data-p="'+esc(r.out+'\\'+x.series.replace(/[\\/:*?"<>|]/g,'_'))+'">開く</button></td></tr>';});
 h+='</table>';
 if(anyTrunc)h+='<p class="warn">⚠ 上限（NotebookLM 300 ファイル / Gemini 10 ファイル）を超えたグループがあり、後半を省きました。分け方を細かくするか、「定型文を取り除いて小さくする」をオンにしてもう一度作ってください。</p>';
 h+='<h3 style="font-size:15px;margin:16px 0 4px">次にやること</h3><ol class="next">';
 if(r.mode!=='merge')h+='<li><b>要約を作る：</b>グループのフォルダを開き、<code>summary_prompt</code> の中のテキストファイルを 1 つずつ開いて全文コピー → ブラウザの Gemini に貼る → 返ってきた要約を <code>（グループ名）_要約.md</code> に貼って保存。</li>';
 if(r.mode!=='summary')h+='<li><b>NotebookLM に読み込む：</b><code>notebooklm</code> フォルダの中のファイルを、NotebookLM の「ソースを追加」でまとめて追加。</li><li><b>Gemini に全文を渡す：</b><code>gemini</code> フォルダの中のファイルをチャットに添付（一度に 10 個まで）。</li>';
 h+='<li>まとめの一覧は <code>bundles/index.html</code> をダブルクリックでも見られます。</li></ol>';
 const el=$('result');el.innerHTML=h;el.classList.add('on');
 el.querySelectorAll('button.open').forEach(b=>b.onclick=async()=>{const x=await api('/api/open',{path:b.dataset.p});if(x.error)alert(x.error);});}
loadSrc();
</script></body></html>"""


def make_handler(cfg):
    class H(BaseHTTPRequestHandler):
        def _send(self, body, ctype="application/json; charset=utf-8", code=200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj, ensure_ascii=False), code=code)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/":
                return self._send(PAGE, "text/html; charset=utf-8")
            if u.path == "/api/source":
                return self._json(source_info(cfg))
            self._send("not found", "text/plain", 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            try:
                if self.path == "/api/preview":
                    by = make_by(body)
                    return self._json({"by": by, "rows": preview(cfg, by, int(body.get("top") or 50))})
                if self.path == "/api/run":
                    return self._json(run(cfg, body))
                if self.path == "/api/open":
                    open_folder(body.get("path", ""))
                    return self._json({"ok": True})
            except (ValueError, SystemExit, re.error) as e:  # 入力の間違いはそのまま画面に出す
                return self._json({"error": str(e)})
            except Exception as e:  # 想定外も黒い窓ではなく画面に出す
                return self._json({"error": f"{type(e).__name__}: {e}"})
            self._send("not found", "text/plain", 404)

        def log_message(self, *a):  # 静かに
            pass

    return H


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--port", type=int, default=8768)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"系列まとめ 起動: {url}  (この窓は閉じずに、ブラウザで操作してください。終了は Ctrl+C)")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        HTTPServer(("127.0.0.1", a.port), make_handler(cfg)).serve_forever()
    except KeyboardInterrupt:
        pass
