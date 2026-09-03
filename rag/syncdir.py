"""索引の保存先がクラウド同期フォルダかどうかを調べる（標準ライブラリのみ）。

**なぜ要るか（2026-09-03 業務PCでの実測）**
索引フォルダが Google ドライブの同期対象配下にあったせいで、実データ100件の
索引作成に 400秒 かかっていた。同期対象外のフォルダへ移しただけで 42秒 になった。
約9.5倍。索引作成は1回で数百万行を書くため、1行ごとに同期クライアントが
ファイル変更を検知しにいくと桁が変わる。

config.py には以前から「ドライブ同期外に置くと速い」と書いてあったが、
**効果の大きさが数値で書かれていなかったため読み飛ばされた**。
そこで、構築の前に実行時に警告を出すことにした。
"""
import os
import sys

# パスに含まれていたら同期フォルダとみなす名前。大小と全半角の揺れを吸収して照合する。
_MARKERS = {
    "googledrive": "Google ドライブ",
    "google drive": "Google ドライブ",
    # 日本語版 Windows のフォルダ名。**ここを ASCII だけで書いていて取りこぼした**（実測で発覚）
    "google ドライブ": "Google ドライブ",
    "googleドライブ": "Google ドライブ",
    "グーグルドライブ": "Google ドライブ",
    "マイドライブ": "Google ドライブ",
    "my drive": "Google ドライブ",
    "共有ドライブ": "Google ドライブ",
    "shared drives": "Google ドライブ",
    "onedrive": "OneDrive",
    "dropbox": "Dropbox",
    "icloud drive": "iCloud Drive",
    "iclouddrive": "iCloud Drive",
    "box sync": "Box",
    "nextcloud": "Nextcloud",
}

# ボリュームラベルがこれらなら、その仮想ドライブごと同期対象（例: G:\マイドライブ）
_VOLUME_LABELS = {
    "google drive": "Google ドライブ",
    "googledrive": "Google ドライブ",
    "dropbox": "Dropbox",
}


def _volume_label(path):
    """Windows で、そのパスが属するドライブのボリュームラベルを返す。取れなければ None。"""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes

        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return None
        buf = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive + "\\"), buf, 261,
            None, None, None, None, 0)
        return buf.value if ok else None
    except Exception:
        # 権限やドライブ種別で失敗しうる。検出できないだけなので黙って諦める。
        return None


def detect(path):
    """同期フォルダらしければサービス名を返す。判定できなければ None。

    誤検出しても実害は警告文が出るだけなので、取りこぼしより拾いすぎを許容する。
    """
    ap = os.path.abspath(path)
    low = ap.replace("\u3000", " ").lower()
    for marker, name in _MARKERS.items():
        if marker in low:
            return name
    label = _volume_label(ap)
    if label and label.strip().lower() in _VOLUME_LABELS:
        return _VOLUME_LABELS[label.strip().lower()]
    return None


def warn_if_synced(index_dir, log=print):
    """索引の保存先が同期フォルダなら警告を出す。戻り値は検出したサービス名または None。

    処理は止めない。止めると、同期フォルダしか使えない環境で動かなくなるため。
    """
    name = detect(index_dir)
    if not name:
        return None
    log("")
    log("=" * 62)
    log(f"[警告] 索引の保存先が {name} の同期フォルダの中にあります。")
    log(f"        {index_dir}")
    log("        索引作成が10倍近く遅くなります（実測: 100件で400秒 → 42秒）。")
    log("        config.json の index_dir を同期対象外のフォルダへ移してください。")
    log("        例: \"index_dir\": \"C:/portable-rag-index\"")
    log("        索引は作り直せるので、移動せず作り直しても構いません。")
    log("=" * 62)
    log("")
    return name
