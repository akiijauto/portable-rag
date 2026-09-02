"""HTML から本文テキストとタイトルを抜く（標準ライブラリのみ）。"""
import html
import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "template", "svg"}
_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
          "table", "section", "article", "header", "footer", "blockquote", "pre", "dd", "dt"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = []
        self._skip = 0
        self._in_title = False
        self._cell = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
        else:
            self.parts.append(data)


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "cp932", "euc_jp", "iso2022_jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def normalize(text):
    text = html.unescape(text)
    text = text.replace("　", " ").replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def extract(path):
    """(title, body) を返す。HTML 以外はそのままテキストとして扱う。"""
    text = _read_text(path)
    if not path.lower().endswith((".html", ".htm")):
        first = text.strip().split("\n", 1)[0][:80] if text.strip() else ""
        return first, normalize(text)
    p = _Extractor()
    p.feed(text)
    p.close()
    title = normalize("".join(p.title))
    body = normalize("".join(p.parts))
    if not title:
        m = re.search(r"^(.{3,80})$", body, re.M)
        title = m.group(1).strip() if m else ""
    return title, body
