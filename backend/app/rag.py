"""Advanced RAG: multi-format ingestion -> chunking -> hybrid retrieval (BM25 + dense vectors)
fused with Reciprocal Rank Fusion -> cross-encoder reranking -> confidence gate ("I don't know")."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import re
import threading
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .config import settings

log = logging.getLogger("rag")

TOKEN_RE = re.compile(r"[a-z0-9£]+(?:[.'][a-z0-9]+)*")  # hyphens split: "next-day" -> next, day
STOP = set("""a an the and or but if of to in on at for with by from is are was were be been it its this that
these those i you we they he she my your our their me us do does did can could would should will shall may might
have has had not no yes how what when where which who whom why there here about into than then so as up out
long much many some something anything get need want take please tell know like just also still""".split())


def _stem(t: str) -> str:
    """Tiny suffix stripper (returns/returned/returning -> return) - good enough for BM25 recall."""
    if len(t) <= 4 or t[0].isdigit() or t[0] == "£":
        return t
    for suf in ("ing", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            return t[: -len(suf)]
    return t


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in TOKEN_RE.findall(text.lower()) if t not in STOP]


@dataclass
class Chunk:
    id: str
    doc: str
    title: str
    section: str
    source_type: str  # pdf | html | markdown | csv
    text: str

    def citation(self) -> dict:
        return {"id": self.id, "doc": self.doc, "title": self.title, "section": self.section,
                "source_type": self.source_type}


# ------------------------------------------------------------------ ingestion
def _chunk_text(text: str, max_chars: int = 900, overlap: int = 150) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= max_chars:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):  # break on a sentence boundary when possible
            cut = text.rfind(". ", start + max_chars // 2, end)
            end = cut + 1 if cut > 0 else end
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def _load_pdf(path: Path) -> tuple[str, list[tuple[str, str]]]:
    from pypdf import PdfReader
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = lines[0] if lines else path.stem
    sections: list[tuple[str, str]] = []
    heading, buf = "Overview", []
    for line in lines[1:]:
        # headings in our policies are short lines like "3. How to start a return"
        if (re.match(r"^\d+\.\s+\S", line) and len(line) < 60) or (len(line) < 40 and not line.endswith(".")):
            if buf:
                sections.append((heading, " ".join(buf)))
            heading, buf = line, []
        else:
            buf.append(line)
    if buf:
        sections.append((heading, " ".join(buf)))
    return title, sections


def _load_html(path: Path) -> tuple[str, list[tuple[str, str]]]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    for tag in soup(["nav", "footer", "script", "style"]):
        tag.decompose()
    title = (soup.find("h1").get_text(strip=True) if soup.find("h1") else path.stem)
    sections = []
    for h in soup.find_all(["h2", "h3"]):
        parts = []
        for sib in h.find_next_siblings():
            if sib.name in {"h2", "h3"}:
                break
            parts.append(sib.get_text(" ", strip=True))
        sections.append((h.get_text(strip=True), " ".join(parts)))
    if not sections:
        sections = [("Overview", soup.get_text(" ", strip=True))]
    return title, sections


def _load_md(path: Path) -> tuple[str, list[tuple[str, str]]]:
    text = path.read_text(encoding="utf-8")
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), path.stem)
    sections, heading, buf = [], "Overview", []
    for line in text.splitlines():
        if line.startswith("## "):
            if buf:
                sections.append((heading, " ".join(buf)))
            heading, buf = line[3:].strip(), []
        elif line.strip() and not line.startswith("# "):
            buf.append(line.strip())
    if buf:
        sections.append((heading, " ".join(buf)))
    return title, sections


def load_documents(docs_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.iterdir()):
        suffix = path.suffix.lower()
        try:
            if suffix == ".csv":
                with open(path, encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        stock = int(row.get("stock", 0) or 0)
                        text = (f"Product: {row['name']}. SKU {row['sku']}. Category: {row['category']}. "
                                f"Price £{row['price_gbp']}. {'In stock (' + str(stock) + ' available)' if stock else 'Out of stock'}. "
                                f"Guarantee: {row.get('warranty', '')}. {row.get('description', '')}")
                        chunks.append(Chunk(f"{path.name}#{row['sku']}", path.name, "Product catalogue",
                                            row["name"], "csv", text))
                continue
            if suffix == ".pdf":
                title, sections, stype = *_load_pdf(path), "pdf"
            elif suffix in {".html", ".htm"}:
                title, sections, stype = *_load_html(path), "html"
            elif suffix in {".md", ".txt"}:
                title, sections, stype = *_load_md(path), "markdown"
            else:
                continue
            for heading, body in sections:
                for i, piece in enumerate(_chunk_text(body)):
                    cid = f"{path.name}#{re.sub(r'[^a-z0-9]+', '-', heading.lower()).strip('-')}" + (f"-{i}" if i else "")
                    # Contextual chunk header: prepend doc title + section so both retrievers see context
                    chunks.append(Chunk(cid, path.name, title, heading, stype, f"{title} - {heading}: {piece}"))
        except Exception:
            log.exception("failed to ingest %s", path)
    return chunks


# ------------------------------------------------------------------ BM25 (keyword)
class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.doc_len = [len(d) for d in docs]
        self.avgdl = sum(self.doc_len) / max(1, len(docs))
        self.tf = [Counter(d) for d in docs]
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(len(self.docs))
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in query:
                if t in tf:
                    f = tf[t]
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl))
            out[i] = s
        return out


# ------------------------------------------------------------------ dense embeddings
class Embedder:
    """fastembed (ONNX, CPU, no torch) when available; otherwise an LSA (TF-IDF + SVD) dense space so
    the system still runs anywhere. The active backend is exposed on /api/health."""

    def __init__(self):
        self.backend = "none"
        self._model = None
        self._vocab: dict[str, int] = {}
        self._idf: np.ndarray | None = None
        self._proj: np.ndarray | None = None
        try:
            if settings.rag_backend == "lite":
                raise RuntimeError("RAG_BACKEND=lite")
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=settings.embedding_model)
            self.backend = f"fastembed:{settings.embedding_model}"
        except Exception as e:  # pragma: no cover - depends on environment
            log.warning("fastembed unavailable (%s) - using LSA embeddings", e)

    def fit(self, texts: list[str]) -> None:
        if self._model is not None:
            return
        docs = [tokenize(t) for t in texts]
        vocab = sorted({t for d in docs for t in d} | {f"{a}_{b}" for d in docs for a, b in zip(d, d[1:])})
        self._vocab = {t: i for i, t in enumerate(vocab)}
        m = self._tfidf_raw(docs)
        df = (m > 0).sum(axis=0)
        self._idf = np.log((1 + len(docs)) / (1 + df)) + 1
        m = m * self._idf
        k = min(128, min(m.shape) - 1)
        _, _, vt = np.linalg.svd(m, full_matrices=False)
        self._proj = vt[:k].T
        self.backend = f"lsa-{k}d"

    def _tfidf_raw(self, docs: list[list[str]]) -> np.ndarray:
        m = np.zeros((len(docs), len(self._vocab)), dtype=np.float32)
        for i, d in enumerate(docs):
            for t in d + [f"{a}_{b}" for a, b in zip(d, d[1:])]:
                j = self._vocab.get(t)
                if j is not None:
                    m[i, j] += 1
        return np.log1p(m)

    def embed(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        if self._model is not None:
            if is_query and hasattr(self._model, "query_embed"):
                vecs = np.array(list(self._model.query_embed(texts)), dtype=np.float32)
            else:
                vecs = np.array(list(self._model.embed(texts)), dtype=np.float32)
        else:
            if self._proj is None:
                raise RuntimeError("LSA embedder not fitted")
            vecs = (self._tfidf_raw([tokenize(t) for t in texts]) * self._idf) @ self._proj
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-9)


class Reranker:
    def __init__(self):
        self.backend = "heuristic"
        self._model = None
        try:
            if settings.rag_backend == "lite":
                raise RuntimeError("RAG_BACKEND=lite")
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            self._model = TextCrossEncoder(model_name=settings.reranker_model)
            self.backend = f"cross-encoder:{settings.reranker_model}"
        except Exception as e:  # pragma: no cover
            log.warning("cross-encoder unavailable (%s) - using heuristic reranker", e)

    def score(self, query: str, chunks: list[Chunk], fused: list[float], dense: list[float],
              idf: dict[str, float] | None = None) -> list[float]:
        if self._model is not None:
            logits = list(self._model.rerank(query, [c.text for c in chunks]))
            return [1 / (1 + math.exp(-float(x))) for x in logits]
        # Heuristic reranker: IDF-weighted query-term coverage (+ bigram bonus) blended with dense similarity.
        # Unknown query terms get the max IDF, so off-topic questions score low -> "I don't know".
        q = tokenize(query)
        idf = idf or {}
        max_idf = max(idf.values()) if idf else 1.0
        w = {t: idf.get(t, max_idf) for t in set(q)}
        total = sum(w.values()) or 1.0
        qbi = {f"{a} {b}" for a, b in zip(q, q[1:])}
        out = []
        for c, f, d in zip(chunks, fused, dense):
            toks = tokenize(c.text)
            ts = set(toks)
            cov = sum(v for t, v in w.items() if t in ts) / total
            bi = len(qbi & {f"{a} {b}" for a, b in zip(toks, toks[1:])}) / max(1, len(qbi)) if qbi else 0
            out.append(cov * (0.6 + 0.25 * max(0.0, d)) + 0.15 * bi)
        return out


@dataclass
class Hit:
    chunk: Chunk
    score: float
    bm25_rank: int | None
    vector_rank: int | None

    def to_dict(self) -> dict:
        return {**self.chunk.citation(), "score": round(self.score, 4), "bm25_rank": self.bm25_rank,
                "vector_rank": self.vector_rank, "text": self.chunk.text}


class KnowledgeBase:
    def __init__(self, docs_dir: Path | None = None, index_dir: Path | None = None):
        self.docs_dir = docs_dir or settings.docs_dir
        self.index_dir = index_dir or settings.index_dir
        self.chunks: list[Chunk] = []
        self.bm25: BM25 | None = None
        self.vectors: np.ndarray | None = None
        self.embedder: Embedder | None = None
        self.reranker: Reranker | None = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.bm25 is not None

    def _fingerprint(self) -> str:
        h = hashlib.sha256(settings.embedding_model.encode())
        for p in sorted(self.docs_dir.iterdir()):
            st = p.stat()
            h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
        return h.hexdigest()[:16]

    def build(self, force: bool = False) -> dict:
        with self._lock:
            if not self.docs_dir.exists() or not any(self.docs_dir.iterdir()):
                from scripts.generate_docs import main as gen  # first boot convenience
                gen()
            self.chunks = load_documents(self.docs_dir)
            self.bm25 = BM25([tokenize(c.text) for c in self.chunks])
            self.embedder = self.embedder or Embedder()
            self.reranker = self.reranker or Reranker()
            texts = [c.text for c in self.chunks]
            self.embedder.fit(texts)
            fp = self._fingerprint() + self.embedder.backend
            cache = self.index_dir / f"vectors-{hashlib.md5(fp.encode()).hexdigest()[:10]}.npy"
            if cache.exists() and not force and self.embedder.backend.startswith("fastembed"):
                self.vectors = np.load(cache)
            else:
                self.vectors = self.embedder.embed(texts)
                self.index_dir.mkdir(parents=True, exist_ok=True)
                np.save(cache, self.vectors)
                (self.index_dir / "chunks.json").write_text(json.dumps([asdict(c) for c in self.chunks], indent=1))
            docs = sorted({c.doc for c in self.chunks})
            log.info("indexed %d chunks from %d docs (%s, %s)", len(self.chunks), len(docs),
                     self.embedder.backend, self.reranker.backend)
            return self.stats()

    def stats(self) -> dict:
        docs = Counter(c.source_type for c in {c.doc: c for c in self.chunks}.values())
        return {"chunks": len(self.chunks), "documents": len({c.doc for c in self.chunks}), "by_type": dict(docs),
                "embedder": self.embedder.backend if self.embedder else None,
                "reranker": self.reranker.backend if self.reranker else None}

    def search(self, query: str, k: int = 4, candidates: int = 20) -> list[Hit]:
        if not self.ready:
            self.build()
        assert self.bm25 is not None and self.vectors is not None and self.embedder and self.reranker
        bm = self.bm25.scores(tokenize(query))
        qv = self.embedder.embed([query], is_query=True)[0]
        dense = self.vectors @ qv
        bm_order = [i for i in np.argsort(-bm)[:candidates] if bm[i] > 0]
        vec_order = list(np.argsort(-dense)[:candidates])
        # Reciprocal Rank Fusion (k=60) - robust to the two retrievers' different score scales
        fused: dict[int, float] = {}
        for rank, i in enumerate(bm_order):
            fused[i] = fused.get(i, 0) + 1 / (60 + rank)
        for rank, i in enumerate(vec_order):
            fused[i] = fused.get(i, 0) + 1 / (60 + rank)
        pool = sorted(fused, key=lambda i: -fused[i])[:12]
        if not pool:
            return []
        cand = [self.chunks[i] for i in pool]
        rr = self.reranker.score(query, cand, [fused[i] for i in pool], [float(dense[i]) for i in pool], self.bm25.idf)
        ranked = sorted(zip(pool, rr), key=lambda x: -x[1])[:k]
        bm_rank = {i: r + 1 for r, i in enumerate(bm_order)}
        vec_rank = {i: r + 1 for r, i in enumerate(vec_order)}
        return [Hit(self.chunks[i], s, bm_rank.get(i), vec_rank.get(i)) for i, s in ranked]

    @property
    def threshold(self) -> float:
        # cross-encoder probabilities and heuristic coverage scores live on different scales
        if self.reranker and self.reranker.backend.startswith("cross-encoder"):
            return settings.rag_min_score
        return settings.rag_heuristic_min_score

    def confident(self, hits: list[Hit]) -> bool:
        return bool(hits) and hits[0].score >= self.threshold

    def borderline(self, hits: list[Hit]) -> bool:
        """Under the answer threshold but not unrelated - worth letting a model read the passages and decline."""
        return bool(hits) and self.threshold * 0.5 <= hits[0].score < self.threshold


kb = KnowledgeBase()
