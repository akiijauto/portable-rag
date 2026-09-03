#!/usr/bin/env python3
"""コントロールUI（ランチャー）
任意フォルダの .py / .bat をボタンとして登録し、ワンクリックで実行する。
標準ライブラリのみ。外部通信なし。ブラウザで http://127.0.0.1:8766 を開く。
使い方:  python launcher.py
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler

from rag import webui

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "launcher.json")
RAG_CONFIG_PATH = os.path.join(HERE, "config.json")     # portable-rag の設定（検索対象フォルダ）
RAG_DEFAULT_EXTS = (".html", ".htm", ".txt", ".md")     # rag/config.py の既定と同じ
COUNT_CAP = 50000                                       # 件数カウントの上限（巨大フォルダで固まらないため）
IS_WIN = sys.platform.startswith("win")

DEFAULT_CONFIG = {
    "title": "業務ツール コントロールパネル",
    "size": "medium",       # small / medium / large
    "columns": 2,           # 1 / 2 / 3
    "buttons": [
        {"name": "RAG 索引更新", "desc": "検索対象フォルダの HTML を読み込み、追加・変更分だけ索引に反映します。文書を追加したら実行してください。",
         "path": os.path.join(HERE, "build_index.py"), "args": ""},
        {"name": "RAG 検索画面", "desc": "ブラウザで検索画面を開きます。結果は Gemini 貼付用 / スプレッドシート用にコピーできます。",
         "path": os.path.join(HERE, "rag_app.py"), "args": ""},
        {"name": "系列まとめ", "desc": "文書をテーマや相手ごとにまとめて、NotebookLM / Gemini に渡せる大きなファイルと要約依頼文を作ります。ブラウザの画面で選んで実行します。",
         "path": os.path.join(HERE, "bundle_app.py"), "args": ""},
    ],
}
BUNDLE_APP_DESC = DEFAULT_CONFIG["buttons"][-1]["desc"]

processes = {}   # pid -> (Popen, name)


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        for b in cfg.get("buttons", []):
            # 旧版の「系列まとめ」（黒い窓の bundle.py）は、同じ場所に画面版があればそちらへ差し替える
            if os.path.basename(b.get("path", "")) == "bundle.py":
                new = os.path.join(os.path.dirname(b["path"]), "bundle_app.py")
                if os.path.exists(new):
                    b["path"], b["desc"] = new, BUNDLE_APP_DESC
        return cfg
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg):
    cfg["size"] = cfg.get("size") if cfg.get("size") in ("small", "medium", "large") else "medium"
    cfg["columns"] = int(cfg.get("columns", 2)) if int(cfg.get("columns", 2)) in (1, 2, 3) else 2
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def run_target(path, args=""):
    """.py は同じ Python で、.bat は cmd で、それぞれ新しいコンソール窓を開いて実行する。"""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".py", ".bat", ".cmd"):
        raise ValueError("実行できるのは .py / .bat / .cmd だけです")
    extra = args.split() if args else []
    cwd = os.path.dirname(path)
    if ext == ".py":
        cmd = [sys.executable, path] + extra
    else:
        cmd = ["cmd", "/c", path] + extra if IS_WIN else ["sh", path] + extra
    kw = {"cwd": cwd}
    if IS_WIN:
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        kw["stdout"] = subprocess.DEVNULL
        kw["stderr"] = subprocess.DEVNULL
    p = subprocess.Popen(cmd, **kw)
    processes[p.pid] = (p, os.path.basename(path))
    return p.pid


def list_dir(folder):
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        return []
    out = []
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith((".py", ".bat", ".cmd")) and not fn.startswith("."):
            out.append({"name": fn, "path": os.path.join(folder, fn)})
    return out


def status():
    res = []
    for pid, (p, name) in list(processes.items()):
        rc = p.poll()
        res.append({"pid": pid, "name": name, "running": rc is None, "code": rc})
    return res


# ---- 検索対象フォルダ（config.json の source_dirs）を画面から設定する ----

def _read_rag_raw():
    """config.json をそのまま読む（無ければ None）。
    rag.config.load_config は絶対パス化と既定値の合成をするので、書き戻しには使わない。"""
    if not os.path.exists(RAG_CONFIG_PATH):
        return None
    with open(RAG_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _count_targets(folder, exts):
    """対象拡張子のファイル数（上限あり）。「そのフォルダで合っているか」を人が確かめる目安。"""
    n = 0
    for _, _, files in os.walk(folder):
        for fn in files:
            if fn.lower().endswith(exts) and not fn.startswith(("~$", ".")):
                n += 1
                if n >= COUNT_CAP:
                    return n, True
    return n, False


def rag_source_dirs():
    raw = _read_rag_raw()
    if raw is None:
        return {"available": False, "dirs": []}
    exts = tuple(raw.get("extensions") or RAG_DEFAULT_EXTS)
    out = []
    for d in raw.get("source_dirs") or []:
        full = os.path.abspath(os.path.join(HERE, d))   # 相対パスは config.json の場所が基準（rag/config.py と同じ）
        ok = os.path.isdir(full)
        cnt, capped = _count_targets(full, exts) if ok else (0, False)
        out.append({"raw": d, "path": full, "exists": ok, "files": cnt, "capped": capped})
    return {"available": True, "dirs": out, "extensions": list(exts)}


def save_rag_source_dirs(dirs):
    raw = _read_rag_raw()
    if raw is None:
        raise FileNotFoundError("config.json が見つかりません（launcher.py と同じフォルダに必要です）")
    cleaned = []
    for d in dirs:
        d = str(d).strip().strip('"')
        if d and d not in cleaned:
            cleaned.append(d)
    if not cleaned:
        raise ValueError("検索対象フォルダは1つ以上必要です")
    raw["source_dirs"] = cleaned
    with open(RAG_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
        f.write("\n")


def list_subdirs(folder):
    """フォルダ選択用：直下のサブフォルダ一覧。空ならホームから始める。"""
    folder = os.path.abspath(folder) if folder else os.path.expanduser("~")
    if not os.path.isdir(folder):
        return {"folder": folder, "parent": None, "dirs": []}
    dirs = []
    try:
        for fn in sorted(os.listdir(folder)):
            p = os.path.join(folder, fn)
            if os.path.isdir(p) and not fn.startswith((".", "$")):
                dirs.append({"name": fn, "path": p})
    except PermissionError:
        pass
    parent = os.path.dirname(folder)
    return {"folder": folder, "parent": parent if parent != folder else None, "dirs": dirs}


PAGE = r"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>コントロールUI</title>
<style>
:root{--btn-h:64px;--btn-fs:16px;--desc-fs:13px}
body{font-family:system-ui,"Segoe UI","Meiryo",sans-serif;margin:0;background:#f3f5f8;color:#222}
header{background:#1f3a5f;color:#fff;padding:12px 20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
header h1{font-size:18px;margin:0;flex:1}header label{font-size:13px}header select{font-size:13px;padding:2px 4px}
main{display:grid;grid-template-columns:1fr 320px;gap:16px;padding:16px;max-width:1200px;margin:0 auto}
#grid{display:grid;gap:12px;grid-template-columns:repeat(var(--cols,2),1fr)}
.card{background:#fff;border:1px solid #d7dce3;border-radius:10px;padding:10px;display:flex;flex-direction:column;gap:6px}
.card button.run{height:var(--btn-h);font-size:var(--btn-fs);font-weight:600;border:0;border-radius:8px;background:#2b6cb0;color:#fff;cursor:pointer}
.card button.run:hover{background:#1f4f85}.card .d{font-size:var(--desc-fs);color:#444;white-space:pre-wrap;min-height:1.5em}
.card .p{font-size:11px;color:#888;word-break:break-all}
aside{background:#fff;border:1px solid #d7dce3;border-radius:10px;padding:12px;font-size:13px;align-self:start}
aside h2{font-size:14px;margin:0 0 8px}#log{max-height:200px;overflow:auto;background:#f7f7f7;padding:6px;border-radius:6px;white-space:pre-wrap}
#editor{display:none}#editor.on{display:block}
#editor input,#editor textarea,#editor select{width:100%;box-sizing:border-box;margin:2px 0 8px;font-size:13px;padding:4px}
.row{display:flex;gap:6px}.row button{flex:1;padding:6px;cursor:pointer}
ul#files{list-style:none;padding:0;margin:0;max-height:150px;overflow:auto;border:1px solid #ddd;border-radius:6px}
ul#files li{padding:4px 6px;cursor:pointer;border-bottom:1px solid #eee}ul#files li:hover{background:#eef4fb}
.small{font-size:11px;color:#777}.card .tools{display:none;gap:4px}.edit .card .tools{display:flex}.tools button{font-size:11px;padding:2px 6px;cursor:pointer}
ul.list{list-style:none;padding:0;margin:0 0 6px;max-height:180px;overflow:auto;border:1px solid #ddd;border-radius:6px}
ul.list li{padding:4px 6px;border-bottom:1px solid #eee;word-break:break-all}ul.list li button{font-size:11px;padding:1px 6px;cursor:pointer}
ul.pick li{cursor:pointer}ul.pick li:hover{background:#eef4fb}.warn{color:#b00}#rag input{width:100%;box-sizing:border-box;font-size:13px;padding:4px}
@media(max-width:800px){main{grid-template-columns:1fr}}
</style></head><body>
<header><h1 id="title"></h1>
<label>ボタンの大きさ <select id="size"><option value="small">小</option><option value="medium">中</option><option value="large">大</option></select></label>
<label>並び <select id="cols"><option value="1">1列</option><option value="2">2列</option><option value="3">3列</option></select></label>
<button id="toggleEdit">ボタンを編集</button><button id="exit" style="margin-left:auto;border:1px solid #c33;color:#c33;background:#fff">終了</button></header>
<main><section><div id="grid"></div></section>
<aside>
<div id="rag"><h2>検索対象フォルダ</h2>
<ul id="srclist" class="list"></ul>
<div class="row"><input id="srcnew" placeholder="フォルダのパスを貼り付け"><button id="srcpick" style="flex:0 0 auto">参照</button><button id="srcadd" style="flex:0 0 auto">追加</button></div>
<ul id="srcpicker" class="list pick" style="display:none"></ul>
<p class="small">検索したい HTML があるフォルダ（Google ドライブの同期フォルダなど）。変えたら「RAG 索引更新」を押してください。</p></div>
<h2 style="margin-top:12px">説明</h2><div id="desc" class="d">ボタンにマウスを乗せると説明が出ます。クリックで実行します。</div>
<h2 style="margin-top:12px">実行状況</h2><div id="log">まだ何も実行していません。</div>
<div id="editor"><h2 style="margin-top:12px">ボタンの追加・編集</h2>
<input type="hidden" id="idx" value="-1">
<label>ボタン名（短く）<input id="f_name" placeholder="例: 日報作成"></label>
<label>説明<textarea id="f_desc" rows="3" placeholder="何をするツールか、いつ使うか"></textarea></label>
<label>実行ファイル（.py / .bat のフルパス）<input id="f_path" placeholder="C:\\Users\\name\\tools\\report.py"></label>
<label>引数（任意）<input id="f_args"></label>
<div class="row"><button id="save">保存</button><button id="cancel">取消</button></div>
<h2 style="margin-top:12px">フォルダから選ぶ</h2>
<div class="row"><input id="folder" placeholder="フォルダのパスを貼り付け"><button id="browse" style="flex:0 0 auto">一覧</button></div>
<ul id="files"></ul><p class="small">一覧のファイルをクリックすると実行ファイル欄に入ります。</p>
</div></aside></main>
<script>
async function doExit(){
  if(!confirm('コントロールUIを終了します。コマンドラインの窓も閉じます。よろしいですか？
（ボタンから起動した別窓のプログラムは終了しません）'))return;
  try{await fetch('/api/exit',{method:'POST'});}catch(e){}
  document.body.innerHTML='<div style="max-width:640px;margin:80px auto;'
    +'font-family:system-ui,Meiryo,sans-serif;text-align:center;color:#444">'
    +'<h2>終了しました</h2><p>コマンドラインの窓は自動で閉じます。</p>'
    +'<p style="color:#888">このタブは閉じて構いません。</p></div>';
}

let cfg=null,edit=false;const $=id=>document.getElementById(id);
const SZ={small:['44px','14px','12px'],medium:['64px','16px','13px'],large:['96px','20px','15px']};
async function api(p,body){const r=await fetch(p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});return r.json();}
function applyStyle(){const s=SZ[cfg.size]||SZ.medium;const r=document.documentElement.style;r.setProperty('--btn-h',s[0]);r.setProperty('--btn-fs',s[1]);r.setProperty('--desc-fs',s[2]);r.setProperty('--cols',cfg.columns);}
function render(){$('title').textContent=cfg.title;document.title=cfg.title;$('size').value=cfg.size;$('cols').value=cfg.columns;applyStyle();
 const g=$('grid');g.innerHTML='';document.body.classList.toggle('edit',edit);
 cfg.buttons.forEach((b,i)=>{const c=document.createElement('div');c.className='card';
  c.innerHTML=`<button class="run">${esc(b.name)}</button><div class="d">${esc(b.desc)}</div><div class="p">${esc(b.path)}</div>
  <div class="tools"><button data-a="edit">編集</button><button data-a="up">↑</button><button data-a="down">↓</button><button data-a="del">削除</button></div>`;
  c.querySelector('.run').onclick=()=>run(i);c.onmouseenter=()=>{$('desc').textContent=b.desc||'(説明なし)';};
  c.querySelectorAll('.tools button').forEach(t=>t.onclick=()=>tool(t.dataset.a,i));g.appendChild(c);});
 if(!cfg.buttons.length)g.innerHTML='<p>ボタンがありません。「ボタンを編集」から追加してください。</p>';}
function esc(s){return String(s??'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
async function run(i){const b=cfg.buttons[i];$('log').textContent=`実行中: ${b.name} …`;const r=await api('/api/run',{index:i});
 $('log').textContent=r.error?`エラー: ${r.error}`:`起動しました: ${b.name} (PID ${r.pid})\n別の窓が開きます。終わるとその窓に結果が出ます。`;}
async function saveCfg(){await api('/api/config',cfg);render();}
async function tool(a,i){if(a==='edit'){const b=cfg.buttons[i];$('idx').value=i;$('f_name').value=b.name;$('f_desc').value=b.desc;$('f_path').value=b.path;$('f_args').value=b.args||'';return;}
 if(a==='del'){if(!confirm(`「${cfg.buttons[i].name}」ボタンを削除しますか？（ファイル自体は消えません）`))return;cfg.buttons.splice(i,1);}
 if(a==='up'&&i>0)[cfg.buttons[i-1],cfg.buttons[i]]=[cfg.buttons[i],cfg.buttons[i-1]];
 if(a==='down'&&i<cfg.buttons.length-1)[cfg.buttons[i+1],cfg.buttons[i]]=[cfg.buttons[i],cfg.buttons[i+1]];
 await saveCfg();}
$('size').onchange=e=>{cfg.size=e.target.value;saveCfg();};$('cols').onchange=e=>{cfg.columns=+e.target.value;saveCfg();};
$('exit').onclick=doExit;
$('toggleEdit').onclick=()=>{edit=!edit;$('editor').classList.toggle('on',edit);$('toggleEdit').textContent=edit?'編集を終了':'ボタンを編集';render();};
$('save').onclick=async()=>{const b={name:$('f_name').value.trim(),desc:$('f_desc').value.trim(),path:$('f_path').value.trim(),args:$('f_args').value.trim()};
 if(!b.name||!b.path){alert('ボタン名と実行ファイルは必須です');return;}const i=+$('idx').value;if(i>=0)cfg.buttons[i]=b;else cfg.buttons.push(b);clearForm();await saveCfg();};
$('cancel').onclick=clearForm;function clearForm(){$('idx').value=-1;['f_name','f_desc','f_path','f_args'].forEach(k=>$(k).value='');}
$('browse').onclick=async()=>{const r=await api('/api/list?folder='+encodeURIComponent($('folder').value));const ul=$('files');ul.innerHTML='';
 if(!r.length)ul.innerHTML='<li>.py / .bat が見つかりません</li>';r.forEach(f=>{const li=document.createElement('li');li.textContent=f.name;li.onclick=()=>{$('f_path').value=f.path;if(!$('f_name').value)$('f_name').value=f.name.replace(/\.(py|bat|cmd)$/i,'');};ul.appendChild(li);});};
setInterval(async()=>{const s=await api('/api/status');const run=s.filter(x=>x.running);const done=s.filter(x=>!x.running);
 if(s.length)$('log').textContent=(run.length?`実行中: ${run.map(x=>x.name).join(', ')}\n`:'')+done.slice(-5).map(x=>`終了: ${x.name} (終了コード ${x.code})`).join('\n');},2000);
api('/api/config').then(c=>{cfg=c;render();});
// ---- 検索対象フォルダ ----
let rag=null;
async function loadRag(){rag=await api('/api/ragconfig');const ul=$('srclist');ul.innerHTML='';
 if(!rag.available){$('rag').style.display='none';return;}
 rag.dirs.forEach((d,i)=>{const li=document.createElement('li');
  const st=d.exists?`対象ファイル ${d.files.toLocaleString()}${d.capped?' 件以上':' 件'}`:'<span class="warn">フォルダが見つかりません</span>';
  li.innerHTML=`<div>${esc(d.path)}</div><div class="small">${st} <button class="srcdel">外す</button></div>`;
  li.querySelector('.srcdel').onclick=async()=>{if(!confirm('この検索対象フォルダを外しますか？（フォルダ自体は消えません）'))return;await saveRag(rag.dirs.map(x=>x.raw).filter((_,j)=>j!==i));};
  ul.appendChild(li);});
 if(!rag.dirs.length)ul.innerHTML='<li class="warn">検索対象フォルダが未設定です。下の欄から追加してください。</li>';}
async function saveRag(dirs){const r=await api('/api/ragconfig',{source_dirs:dirs});if(r.error){alert(r.error);return;}$('srcnew').value='';$('srcpicker').style.display='none';await loadRag();}
$('srcadd').onclick=()=>{const v=$('srcnew').value.trim();if(!v){alert('フォルダのパスを入れるか「参照」で選んでください');return;}saveRag([...rag.dirs.map(x=>x.raw),v]);};
async function pick(folder){const r=await api('/api/subdirs?folder='+encodeURIComponent(folder||''));const ul=$('srcpicker');ul.style.display='';ul.innerHTML='';
 const head=document.createElement('li');head.innerHTML=`<b>${esc(r.folder)}</b> <button class="srcuse">このフォルダにする</button>`;head.style.cursor='default';
 head.querySelector('.srcuse').onclick=e=>{e.stopPropagation();$('srcnew').value=r.folder;ul.style.display='none';};ul.appendChild(head);
 if(r.parent){const up=document.createElement('li');up.textContent='↑ 上のフォルダへ';up.onclick=()=>pick(r.parent);ul.appendChild(up);}
 r.dirs.forEach(d=>{const li=document.createElement('li');li.textContent='📁 '+d.name;li.onclick=()=>pick(d.path);ul.appendChild(li);});
 if(!r.dirs.length){const li=document.createElement('li');li.className='small';li.textContent='（サブフォルダなし）';li.style.cursor='default';ul.appendChild(li);}}
$('srcpick').onclick=()=>pick($('srcnew').value.trim());
loadRag();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_ref = None      # webui.serve が差し込む

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif u.path == "/api/config":
            self._json(load_config())
        elif u.path == "/api/list":
            self._json(list_dir(qs.get("folder", [""])[0]))
        elif u.path == "/api/ragconfig":
            self._json(rag_source_dirs())
        elif u.path == "/api/subdirs":
            self._json(list_subdirs(qs.get("folder", [""])[0]))
        elif u.path == "/api/status":
            self._json(status())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/exit":
            self._json({"ok": True})
            webui.request_shutdown(self.server_ref)
            return
        if self.path == "/api/config":
            save_config(body)
            self._json({"ok": True})
        elif self.path == "/api/ragconfig":
            try:
                save_rag_source_dirs(body.get("source_dirs", []))
                self._json({"ok": True})
            except Exception as e:  # ユーザーに見せる
                self._json({"error": str(e)})
        elif self.path == "/api/run":
            cfg = load_config()
            try:
                b = cfg["buttons"][int(body["index"])]
                self._json({"pid": run_target(b["path"], b.get("args", ""))})
            except Exception as e:  # ユーザーに見せる
                self._json({"error": str(e)})
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(CONFIG_PATH):
        save_config(load_config())
    sys.exit(webui.serve(Handler, "127.0.0.1", a.port,
                         "コントロールUI", open_browser=not a.no_browser))
