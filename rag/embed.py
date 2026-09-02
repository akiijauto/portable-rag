"""ベクトル検索（任意）。sentence-transformers が無ければ無効になる。
モデルは初回だけダウンロードされ、以後は HF キャッシュから読む。オフライン PC へは
キャッシュフォルダごとコピーすればよい。"""
import math

_model = None
_model_name = None


def available():
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


def load(model_name):
    global _model, _model_name
    if _model is None or _model_name != model_name:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(model_name)
        _model_name = model_name
    return _model


def _prefix(model_name, kind):
    # e5 系は "query: " / "passage: " の接頭辞が必要
    if "e5" in model_name.lower():
        return "query: " if kind == "query" else "passage: "
    return ""


def encode(model_name, texts, kind="passage", batch_size=32):
    m = load(model_name)
    p = _prefix(model_name, kind)
    vecs = m.encode([p + t for t in texts], batch_size=batch_size,
                    normalize_embeddings=True, show_progress_bar=False)
    return [list(map(float, v)) for v in vecs]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
