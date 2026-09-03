"""ブラウザ画面の共通部品：進捗表示つきバックグラウンド実行と、きれいな終了。

**なぜ要るか（2026-09-03 ユーザー要望）**
「ボタンを押したあと、更新中なのか単に止まっているのか分からない」
「終了ボタンを押したらコマンドラインの窓ごと片付けてほしい」

元の作りは3つの画面とも単一スレッドの HTTPServer だった。索引更新のような
重い処理をリクエストの中で最後まで実行するため、**その間サーバーは他の要求に
一切応答できない**。つまり進捗を問い合わせる余地が原理的に無かった。
画面は「更新中…」と出したまま固まり、生きているのか死んだのか区別できない。

ここでは2つを提供する。
  1. ThreadingHTTPServer ＋ Job … 重い処理を別スレッドで走らせ、進捗を返せるようにする
  2. request_shutdown        … 応答を返してからサーバーを止め、終了コード0で抜ける
"""
import json
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer

MAX_LINES = 300          # 画面に出す進捗行の上限。索引更新は行数が多くなりうる


class Job:
    """重い処理を1本だけ背後で走らせ、進捗を読み取れるようにする。

    同時に2本走らせない。索引の更新が二重に走ると SQLite の書き込みが競合するため。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._reset("idle", None)

    def _reset(self, state, name):
        self.state = state          # idle / running / done / error
        self.name = name
        self.lines = []
        self.error = None
        self.started = None
        self.finished = None
        self.last_line_at = None

    # ---- 実行側 ----
    def start(self, name, fn):
        """fn(log) を別スレッドで実行する。既に実行中なら False を返す。"""
        with self._lock:
            if self.state == "running":
                return False
            self._reset("running", name)
            self.started = time.time()
            self.last_line_at = self.started

        def runner():
            try:
                fn(self.log)
                with self._lock:
                    self.state = "done"
            except BaseException as e:                    # 画面に出すため握る
                with self._lock:
                    self.state = "error"
                    self.error = f"{type(e).__name__}: {e}"
                    self.lines.append(f"[エラー] {self.error}")
            finally:
                with self._lock:
                    self.finished = time.time()

        threading.Thread(target=runner, daemon=True, name="job").start()
        return True

    def log(self, line):
        with self._lock:
            self.lines.append(str(line))
            if len(self.lines) > MAX_LINES:
                # 先頭を捨てる。省略した事実は残す
                drop = len(self.lines) - MAX_LINES
                self.lines = [f"（古い {drop} 行を省略）"] + self.lines[drop + 1:]
            self.last_line_at = time.time()

    # ---- 表示側 ----
    def snapshot(self):
        """画面へ返す状態。**running でなくても呼べる**ことが大事で、
        これが返ってくること自体が「プログラムは生きている」証拠になる。"""
        with self._lock:
            now = time.time()
            end = self.finished or now
            return {
                "state": self.state,
                "name": self.name,
                "lines": list(self.lines),
                "error": self.error,
                "elapsed": round(end - self.started, 1) if self.started else 0,
                "quiet": round(now - self.last_line_at, 1) if (
                    self.state == "running" and self.last_line_at) else 0,
                "server_time": round(now, 3),
            }


def request_shutdown(server, delay=0.3):
    """HTTP 応答を返し終えてからサーバーを止める。

    `shutdown()` は serve_forever が回っているスレッドとは別のスレッドから
    呼ぶ必要がある。ここでは新しいスレッドから、わずかに遅らせて呼ぶ。
    遅らせるのは、止める前にブラウザへ応答を返し切るため。
    """
    def stopper():
        time.sleep(delay)
        server.shutdown()
    threading.Thread(target=stopper, daemon=True, name="shutdown").start()


def serve(handler_cls, host, port, label, open_browser=True):
    """ThreadingHTTPServer で起動する。戻り値は終了コード。

    **終了コード 0 は「利用者が終了ボタンか Ctrl+C で正常に終えた」を意味する。**
    .bat 側はこれを見て、窓を閉じるか（0）エラーを読ませるため止めるか（0以外）を決める。
    """
    srv = ThreadingHTTPServer((host, port), handler_cls)
    srv.daemon_threads = True
    handler_cls.server_ref = srv                  # 終了要求のために持たせる
    url = f"http://{host}:{port}/"
    print(f"{label} 起動: {url}")
    print("  終了するには、画面の「終了」ボタンを押すか、この窓で Ctrl+C を押してください。")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します。")
    finally:
        srv.server_close()
    return 0


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


# 画面に貼る共通の進捗パネル。各アプリの HTML から読み込んで使う。
PROGRESS_CSS = """
#panel{border:1px solid #ddd;border-radius:6px;padding:10px 12px;margin:10px 0;display:none;background:#fbfbfb}
#panel.on{display:block}
#panel .head{display:flex;align-items:center;gap:10px;font-size:14px}
#panel .dot{width:10px;height:10px;border-radius:50%;background:#0a7;flex:0 0 auto}
#panel.run .dot{animation:blink 1s infinite}
#panel.err .dot{background:#c33;animation:none}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
#panel pre{max-height:240px;overflow:auto;margin:8px 0 0;background:#fff;border:1px solid #eee}
#alive{color:#666;font-size:12px;margin-left:auto}
#exit{background:#fff;border:1px solid #c33;color:#c33}
"""

PROGRESS_HTML = """
<div id="panel"><div class="head"><span class="dot"></span>
<b id="pTitle">処理中</b><span id="pInfo"></span><span id="alive"></span></div>
<pre id="pLog"></pre></div>
"""

# 進捗ポーリングと終了ボタンの共通スクリプト。
# **応答が返ること自体が「生きている」証拠**なので、最後に応答した時刻を必ず出す。
PROGRESS_JS = """
let pollTimer=null;
function showPanel(on){document.getElementById('panel').classList.toggle('on',on);}
function paint(s){
  const p=document.getElementById('panel');
  p.classList.toggle('run',s.state==='running');
  p.classList.toggle('err',s.state==='error');
  document.getElementById('pTitle').textContent =
    s.state==='running' ? (s.name||'処理')+' を実行中'
    : s.state==='error' ? (s.name||'処理')+' でエラー'
    : s.state==='done'  ? (s.name||'処理')+' が完了' : '待機中';
  let info='経過 '+s.elapsed+'秒';
  if(s.state==='running'&&s.quiet>=5) info+=' / 次の区切りまで処理中（'+s.quiet+'秒）';
  document.getElementById('pInfo').textContent=info;
  document.getElementById('alive').textContent='応答あり '+new Date().toLocaleTimeString();
  const log=document.getElementById('pLog');
  const stick=log.scrollTop+log.clientHeight>=log.scrollHeight-20;
  log.textContent=s.lines.join('\\n');
  if(stick)log.scrollTop=log.scrollHeight;
}
async function pollOnce(){
  try{
    const r=await fetch('/api/progress',{cache:'no-store'});
    const s=await r.json(); paint(s);
    if(s.state!=='running'){clearInterval(pollTimer);pollTimer=null;if(typeof onJobEnd==='function')onJobEnd(s);}
  }catch(e){
    // 応答が来ない＝プログラムが落ちたか終了した。固まって見えないよう明示する
    const p=document.getElementById('panel');p.classList.add('err');p.classList.remove('run');
    document.getElementById('pTitle').textContent='応答がありません';
    document.getElementById('pInfo').textContent='プログラムが終了したか停止した可能性があります';
    clearInterval(pollTimer);pollTimer=null;
  }
}
function startPolling(){showPanel(true);pollOnce();if(!pollTimer)pollTimer=setInterval(pollOnce,700);}
async function doExit(){
  if(!confirm('プログラムを終了します。コマンドラインの窓も閉じます。よろしいですか？'))return;
  try{await fetch('/api/exit',{method:'POST'});}catch(e){}
  document.body.innerHTML='<div style="max-width:640px;margin:80px auto;'
    +'font-family:system-ui,Meiryo,sans-serif;text-align:center;color:#444">'
    +'<h2>終了しました</h2><p>コマンドラインの窓は自動で閉じます。</p>'
    +'<p style="color:#888">このタブは閉じて構いません。</p></div>';
}
"""
