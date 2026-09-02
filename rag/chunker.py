"""本文をチャンクに分割。文末（。！？\n）で切れる位置を優先する。"""
import re

_SENT_END = re.compile(r"(?<=[。！？!?\n])")


def split_chunks(text, size=600, overlap=120):
    if not text:
        return []
    sents = [s for s in _SENT_END.split(text) if s]
    chunks, buf = [], ""
    for s in sents:
        while len(s) > size:          # 極端に長い一文は強制分割
            head, s = s[:size], s[size:]
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(head)
        if len(buf) + len(s) > size and buf:
            chunks.append(buf)
            buf = buf[-overlap:] if overlap else ""
        buf += s
    if buf.strip():
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]
