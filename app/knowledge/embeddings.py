"""Embeddings via the project's Gemini client (gemini-embedding-001 at 1536 dims = the pgvector column size)."""
import logging
import math
import os
import time

logger = logging.getLogger("g_cortex.embeddings")
MODEL = "gemini-embedding-001"
DIM = 1536
_client = None
_blocked_until = 0.0          # circuit breaker: after the quota is exhausted, further calls fail fast (full-text fallback) instead of stalling for minutes


def _get_client():
    global _client
    if _client is None:
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        from google import genai
        _client = genai.Client(api_key=key)
    return _client


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts: list[str], task: str = "RETRIEVAL_DOCUMENT", batch: int = 50, retries: int = 6) -> list[list[float] | None]:
    """Return one normalised vector per text, or None where the service could not embed it (callers fall back to full-text)."""
    global _blocked_until
    cl = _get_client()
    out: list[list[float] | None] = [None] * len(texts)
    if cl is None or time.monotonic() < _blocked_until:
        return out
    from google.genai import types
    for start in range(0, len(texts), batch):
        part = [t[:8000] for t in texts[start:start + batch]]
        for attempt in range(retries):
            try:
                r = cl.models.embed_content(model=MODEL, contents=part, config=types.EmbedContentConfig(output_dimensionality=DIM, task_type=task))
                for i, e in enumerate(r.embeddings):
                    out[start + i] = _norm(list(e.values))
                break
            except Exception as exc:
                msg = str(exc)
                if any(k in msg for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")) and attempt < retries - 1:
                    time.sleep(min(60, 12 * (attempt + 1)) if retries > 2 else 3)
                    continue
                logger.warning("embedding batch failed: %s", msg[:200])
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    _blocked_until = time.monotonic() + 600
                    return out
                break
    return out


def to_pg(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
