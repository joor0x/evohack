import hashlib
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import difflib


class EvoMemory:
    """
    Lightweight wrapper around ChromaDB to store high‑fitness payloads and retrieve
    context‑similar candidates for future runs. Embeddings are optional:
    - If OPENAI_API_KEY is present, uses OpenAI embeddings via Chroma helpers.
    - Otherwise, falls back to keyword filtering and local similarity ranking.
    """

    def __init__(self, persist_dir: str = ".evohack_chroma", collection: str = "evohack_payloads") -> None:
        self.persist_dir = persist_dir
        self.collection_name = collection
        self.client = None
        self.collection = None
        self._embedder = None
        self._init()

    def _init(self) -> None:
        try:
            import chromadb
            from chromadb.config import Settings
            self.client = chromadb.PersistentClient(path=self.persist_dir, settings=Settings(allow_reset=False))
        except Exception:
            # Fallback to in-memory if persistent not available
            import chromadb
            self.client = chromadb.Client()
        # Optional embedding function (OpenAI)
        try:
            from chromadb.utils import embedding_functions
            # Prefer OpenAI if configured
            if os.environ.get("OPENAI_API_KEY"):
                self._embedder = embedding_functions.OpenAIEmbeddingFunction(
                    model_name=os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")
                )
            else:
                # Try Ollama embeddings (local), default model nomic-embed-text
                _ollama_url = (
                    os.environ.get("OLLAMA_API_URL")
                    or os.environ.get("OLLAMA_HOST")
                    or "http://127.0.0.1:11434"
                )
                _ollama_model = os.environ.get("OLLAMA_EMBED_MODEL") or os.environ.get("EVOHACK_OLLAMA_EMBED_MODEL") or "nomic-embed-text"
                self._embedder = _OllamaEmbeddingFunction(api_url=_ollama_url, model=_ollama_model)
        except Exception:
            self._embedder = None
        # Create or get collection
        try:
            self.collection = self.client.get_or_create_collection(name=self.collection_name, embedding_function=self._embedder, metadata={"hnsw:space": "cosine"})
        except Exception:
            # If embedding function incompatible, try without
            self.collection = self.client.get_or_create_collection(name=self.collection_name)

    # ----------------- Public API -----------------
    def add(self, payload: str, fitness: float, context: Dict[str, Any]) -> None:
        if not payload:
            return
        meta = self._build_metadata(fitness, context)
        doc = self._build_doc(payload, context)
        _id = self._make_id(payload, meta)
        try:
            self.collection.upsert(ids=[_id], metadatas=[meta], documents=[doc])
        except Exception:
            # Best effort: ignore failures
            pass

    def top_for_context(self, context: Dict[str, Any], limit: int = 10) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Return up to `limit` payloads suitable for given context.
        Each item is (payload, metadata). Ensures diversity via similarity filtering.
        """
        if self.collection is None:
            return []
        # Build a short context text
        qtext = self._context_text(context)
        docs: List[str] = []
        metas: List[Dict[str, Any]] = []
        # If we have embeddings, query by text
        try:
            if self._embedder is not None and qtext:
                q = self.collection.query(query_texts=[qtext], n_results=max(50, limit * 5))
                docs = (q.get("documents") or [[]])[0]
                metas = (q.get("metadatas") or [[]])[0]
            else:
                # Fallback: filter by host and method/category
                host = self._host(context)
                method = str((context or {}).get("method", "")).upper() or None
                where: Dict[str, Any] = {}
                if host:
                    where["host"] = host
                if method:
                    where["method"] = method
                r = self.collection.get(where=where or None)
                docs = r.get("documents") or []
                metas = r.get("metadatas") or []
        except Exception:
            return []
        # Rank by composite score: prior fitness + simple similarity on context text
        pairs: List[Tuple[str, Dict[str, Any], float]] = []
        for d, m in zip(docs, metas):
            if not isinstance(d, str) or not isinstance(m, dict):
                continue
            pl = self._extract_payload(d)
            base = float(m.get("fitness", 0.0))
            sim = self._context_similarity(qtext, d)
            score = 0.7 * base + 0.3 * (sim * 500.0)  # normalize to ~0..500 like fitness
            pairs.append((pl, m, score))
        pairs.sort(key=lambda x: x[2], reverse=True)
        # Diversity filter to avoid near-duplicates
        out: List[Tuple[str, Dict[str, Any]]] = []
        seen: List[str] = []
        for pl, m, _ in pairs:
            if not pl:
                continue
            if any(self._is_similar(pl, s) for s in seen):
                continue
            out.append((pl, m))
            seen.append(pl)
            if len(out) >= limit:
                break
        return out

    # ----------------- Helpers -----------------
    def _make_id(self, payload: str, meta: Dict[str, Any]) -> str:
        h = hashlib.sha1()
        h.update(payload.encode("utf-8", errors="ignore"))
        h.update(str(meta.get("host", "")).encode())
        h.update(str(meta.get("path", "")).encode())
        return h.hexdigest()

    def _build_metadata(self, fitness: float, context: Dict[str, Any]) -> Dict[str, Any]:
        host, path = self._host_path(context.get("url") or context.get("target", ""))
        cats = context.get("categories") or []
        method = str(context.get("method") or self._infer_method_from_target(context.get("target")) or "").upper()
        return {
            "fitness": float(fitness),
            "ts": int(time.time()),
            "host": host or None,
            "path": path or None,
            "method": method or None,
            "categories": cats,
            "instruction": context.get("instruction") or None,
        }

    def _build_doc(self, payload: str, context: Dict[str, Any]) -> str:
        parts = [payload]
        tgt = context.get("target") or ""
        cats = ",".join(context.get("categories") or [])
        instr = context.get("instruction") or ""
        if tgt or cats or instr:
            parts.append(f"\nCTX target={tgt} cats={cats} instr={instr}")
        return "".join(parts)

    def _extract_payload(self, doc: str) -> str:
        # first line is payload
        return (doc.splitlines() or [doc])[0].strip()

    def _host(self, context: Dict[str, Any]) -> Optional[str]:
        host, _ = self._host_path(context.get("url") or context.get("target", ""))
        return host

    def _host_path(self, url_or_target: str) -> Tuple[Optional[str], Optional[str]]:
        u = None
        if not url_or_target:
            return None, None
        try:
            # extract url from target like "POST http://host/path ..."
            text = str(url_or_target)
            if "://" not in text and " " in text:
                text = text.split(" ", 1)[1]
            u = urlparse(text)
            return (u.hostname or None), (u.path or None)
        except Exception:
            return None, None

    def _infer_method_from_target(self, target: Optional[str]) -> Optional[str]:
        if not target:
            return None
        t = str(target).strip().split(" ", 1)[0]
        if t.upper() in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
            return t.upper()
        return None

    def _context_text(self, context: Dict[str, Any]) -> str:
        tgt = str(context.get("target") or "")
        cats = ", ".join(context.get("categories") or [])
        instr = str(context.get("instruction") or "")
        return f"{tgt}\n{cats}\n{instr}".strip()

    def _context_similarity(self, q: str, doc: str) -> float:
        try:
            return difflib.SequenceMatcher(None, q, doc).ratio()
        except Exception:
            return 0.0

    def _is_similar(self, a: str, b: str, threshold: float = 0.90) -> bool:
        try:
            r = difflib.SequenceMatcher(None, a, b).ratio()
            return r >= threshold
        except Exception:
            return False


class _OllamaEmbeddingFunction:
    """Minimal embedding function for Chroma using Ollama /api/embeddings.

    Expects an embeddings-capable model (e.g., nomic-embed-text) to be present locally.
    """

    def __init__(self, api_url: str = "http://127.0.0.1:11434", model: str = "nomic-embed-text", timeout_s: int = 30) -> None:
        self.api_url = api_url.rstrip("/")
        self.model = model
        self.timeout_s = int(timeout_s)

    def __call__(self, inputs: List[str]) -> List[List[float]]:
        import requests

        out: List[List[float]] = []
        for text in inputs:
            try:
                r = requests.post(
                    f"{self.api_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                    timeout=self.timeout_s,
                )
                r.raise_for_status()
                j = r.json() or {}
                emb = j.get("embedding") or []
                if not isinstance(emb, list):
                    emb = []
                out.append([float(x) for x in emb])
            except Exception:
                out.append([])
        return out
