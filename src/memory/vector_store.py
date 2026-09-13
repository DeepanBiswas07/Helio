"""
vector_store.py — retrieval over the user's own documents.

Until now "file knowledge" stored one summary per document. That answers
"have I read this?" but not "what did the contract say about payment terms" —
the question people actually want a file assistant for. This module keeps the
documents themselves, in pieces, and finds the pieces that answer a question.

Two deliberate choices:

*Local embeddings, on CPU.* all-MiniLM-L6-v2 is 22M parameters and ~90MB.
It runs offline, which the rest of Helio does, and it is pinned to CPU because
the 4GB card is already carrying Kokoro and Whisper — a third resident model is
what starts evicting the voice.

*numpy instead of a vector database.* A personal corpus is hundreds to a few
thousand chunks. Brute-force cosine over a float32 matrix that size takes about
a millisecond, so Chroma or FAISS would add a dependency, a daemon and a schema
to solve a problem that doesn't exist at this scale. The store is one .npz and
one .json, both inspectable.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "memory"
VECTORS_PATH = DATA_DIR / "doc_vectors.npz"
CHUNKS_PATH = DATA_DIR / "doc_chunks.json"

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384

# ~500 characters is roughly one clause or one topic. The first version used
# 1200 and a short contract came out as a single chunk, which put us straight
# back to document-level retrieval: one blended vector that matched "payment
# terms" weakly and "how much per day" not at all. Chunks have to be small
# enough that one idea dominates the vector.
CHUNK_CHARS = 500
CHUNK_OVERLAP = 80
MIN_CHUNK_CHARS = 60

# A paragraph starting like "2. COMPENSATION" or a bare ALL-CAPS line is a new
# section. Packing across one of those merges unrelated ideas into one vector,
# so these are hard boundaries regardless of remaining budget.
_SECTION_START = re.compile(
    r"^\s*(?:\d+[.)]\s+\S|[A-Z][A-Z0-9 \-/&']{5,}\s*$|#{1,6}\s)")

_model = None
_model_lock = threading.Lock()


def _get_model():
    """Load MiniLM once, on CPU, and keep it. First call costs a few seconds."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            # device="cpu" is not a fallback — see the module docstring.
            _model = SentenceTransformer(MODEL_NAME, device="cpu")
    return _model


def model_ready():
    """True when the embedder is already loaded, so callers can warn about a wait."""
    return _model is not None


# ── chunking ─────────────────────────────────────────────────────────────────

def chunk_text(text, chunk_chars=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
    """
    Split on paragraph boundaries, packing up to chunk_chars.

    Splitting on blank lines rather than a fixed stride keeps a clause or a
    heading with the text under it, which is what makes a retrieved chunk
    readable on its own instead of starting mid-sentence.
    """
    text = re.sub(r"\r\n?", "\n", text or "").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks, current = [], ""
    for paragraph in paragraphs:
        starts_section = bool(_SECTION_START.match(paragraph))

        # A bare heading belongs with the text under it, not alone in a chunk.
        heading_only = starts_section and len(paragraph) < MIN_CHUNK_CHARS

        # A new numbered clause or heading always begins a chunk, even when
        # there is room left — otherwise "3. TERM" gets folded into the
        # payment clause and neither idea owns the vector.
        if starts_section and current and not heading_only:
            chunks.append(current)
            current = ""

        # A single paragraph longer than the budget gets sentence-split.
        if len(paragraph) > chunk_chars:
            if current:
                chunks.append(current)
                current = ""
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
                if len(current) + len(sentence) + 1 > chunk_chars and current:
                    chunks.append(current)
                    current = current[-overlap:] if overlap else ""
                current = (current + " " + sentence).strip()
            continue

        if len(current) + len(paragraph) + 2 > chunk_chars and current:
            chunks.append(current)
            current = current[-overlap:] if overlap else ""
        current = (current + "\n\n" + paragraph).strip()

    if current:
        chunks.append(current)

    kept = [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]
    return kept or chunks[:1]


# ── persistence ──────────────────────────────────────────────────────────────

def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load():
    """Returns (vectors [N, D] float32, chunk records list). Never raises."""
    _ensure_dir()
    records = []
    if CHUNKS_PATH.exists():
        try:
            with CHUNKS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                records = data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            records = []

    vectors = np.zeros((0, EMBED_DIM), dtype=np.float32)
    if VECTORS_PATH.exists():
        try:
            with np.load(str(VECTORS_PATH)) as bundle:
                vectors = bundle["vectors"].astype(np.float32)
        except Exception:
            vectors = np.zeros((0, EMBED_DIM), dtype=np.float32)

    # A mismatch means one file was written and the other wasn't. Rebuilding is
    # cheap; serving misaligned vectors would return confidently wrong chunks.
    if len(records) != vectors.shape[0]:
        return np.zeros((0, EMBED_DIM), dtype=np.float32), []
    return vectors, records


def _save(vectors, records):
    _ensure_dir()
    try:
        np.savez_compressed(str(VECTORS_PATH), vectors=vectors.astype(np.float32))
        with CHUNKS_PATH.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False)
    except OSError:
        pass


def store_mtime():
    try:
        return CHUNKS_PATH.stat().st_mtime
    except OSError:
        return 0.0


# ── indexing ─────────────────────────────────────────────────────────────────

def _embed(texts, batch_size=32):
    model = _get_model()
    vectors = model.encode(
        texts, batch_size=batch_size, convert_to_numpy=True,
        normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vectors, dtype=np.float32)


def index_document(path, text=None, title=None, progress=None):
    """
    Chunk, embed and store one document. Re-indexing replaces its old chunks.

    Returns (chunk_count, message).
    """
    path = str(path)
    name = title or os.path.basename(path)

    if text is None:
        try:
            from tools.file_ops.file_reader import read_path
            text = read_path(path)
        except Exception as e:
            return 0, f"Couldn't read {name}: {e}"

    if not text or not str(text).strip():
        return 0, f"{name} has no readable text."

    pieces = chunk_text(str(text))
    if not pieces:
        return 0, f"{name} produced no usable chunks."

    if progress:
        progress(f"embedding {len(pieces)} chunks from {name}")

    fresh = _embed(pieces)

    vectors, records = _load()
    # Drop any previous version of this document before adding the new one.
    keep = [i for i, r in enumerate(records) if r.get("path") != path]
    if keep:
        vectors = vectors[keep]
        records = [records[i] for i in keep]
    else:
        vectors = np.zeros((0, EMBED_DIM), dtype=np.float32)
        records = []

    now = time.time()
    for index, piece in enumerate(pieces):
        records.append({
            "path": path,
            "name": name,
            "chunk": index,
            "total": len(pieces),
            "text": piece,
            "indexed_ts": now,
        })

    vectors = np.vstack([vectors, fresh]) if vectors.size else fresh
    _save(vectors, records)
    return len(pieces), f"Indexed {name} — {len(pieces)} chunks."


def forget_document(path):
    """Remove every chunk of one document. Returns how many went."""
    path = str(path)
    vectors, records = _load()
    keep = [i for i, r in enumerate(records) if r.get("path") != path]
    removed = len(records) - len(keep)
    if not removed:
        return 0
    _save(vectors[keep] if keep else np.zeros((0, EMBED_DIM), dtype=np.float32),
          [records[i] for i in keep])
    return removed


def indexed_documents():
    """One entry per document: path, name, chunk count, when it was indexed."""
    _vectors, records = _load()
    docs = {}
    for record in records:
        path = record.get("path")
        entry = docs.setdefault(path, {
            "path": path, "name": record.get("name", ""),
            "chunks": 0, "indexed_ts": record.get("indexed_ts", 0),
        })
        entry["chunks"] += 1
    return sorted(docs.values(), key=lambda d: d["indexed_ts"], reverse=True)


def chunk_count():
    _vectors, records = _load()
    return len(records)


# ── search ───────────────────────────────────────────────────────────────────

# Words too common to signal anything about which chunk is relevant.
_STOPWORDS = frozenset("""
a an and are as at be been but by can did do does for from had has have how i
if in is it its me my of on or our say said should so than that the their them
then there these they this to was were what when where which who whom why will
with would you your
""".split())

LEXICAL_WEIGHT = 0.18


def _content_words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def search(query, top_k=5, min_score=0.14, path=None):
    """
    The most relevant chunks for a question, best first.

    Hybrid: cosine similarity plus a small lexical overlap term. Pure cosine
    ranked "what did the contract say about payment terms" against the TERM AND
    TERMINATION clause above the COMPENSATION AND PAYMENT TERMS one — the
    embedding heard "term" and the exact phrase lost. Adding a keyword signal
    fixes that class of miss without needing a second index.

    Vectors are L2-normalised at encode time, so a dot product is the cosine —
    no division, no per-query normalisation pass.
    """
    if not query or not str(query).strip():
        return []

    vectors, records = _load()
    if not len(records):
        return []

    mask = None
    if path:
        wanted = str(path).lower()
        mask = [i for i, r in enumerate(records)
                if wanted in str(r.get("path", "")).lower()]
        if not mask:
            return []
        vectors = vectors[mask]

    query_vector = _embed([str(query)])[0]
    scores = vectors @ query_vector

    query_words = _content_words(query)
    if query_words:
        source = ([records[i] for i in mask] if mask else records)
        overlap = np.array([
            len(query_words & _content_words(r.get("text", ""))) / len(query_words)
            for r in source
        ], dtype=np.float32)
        scores = scores + LEXICAL_WEIGHT * overlap

    order = np.argsort(-scores)[:max(1, top_k)]
    hits = []
    for position in order:
        score = float(scores[position])
        if score < min_score:
            continue
        record = records[mask[position]] if mask else records[position]
        hits.append({
            "score": score,
            "path": record.get("path", ""),
            "name": record.get("name", ""),
            "chunk": record.get("chunk", 0),
            "total": record.get("total", 1),
            "text": record.get("text", ""),
        })
    return hits
