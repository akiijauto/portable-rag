"""日本語向けトークナイザ。形態素解析器なしで動くよう、
英数字は単語、日本語は文字 bigram（＋unigram の漢字1文字は捨てる）で分割する。"""
import re
import unicodedata

_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-\.]*")
_JA = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]+")


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    return text.lower()


def tokenize(text):
    text = normalize(text)
    tokens = []
    for m in _WORD.finditer(text):
        w = m.group(0).strip(".-")
        if len(w) >= 2 or w.isdigit():
            tokens.append(w)
    for m in _JA.finditer(text):
        s = m.group(0)
        if len(s) == 1:
            tokens.append(s)
            continue
        tokens.extend(s[i:i + 2] for i in range(len(s) - 1))
    return tokens
