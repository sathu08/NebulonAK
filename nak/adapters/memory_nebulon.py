"""Memory adapter: MemoryPort over an injected brain (no worker needed).

Strangler pattern: the worker's MemoryMixin is untouched and keeps working;
only new code (Phase D agent loop) talks to this adapter. Same underlying
calls the mixin makes (search / save_chat / remember); transport errors
propagate unwrapped.
"""

from __future__ import annotations

from typing import Any


class NebulonMemory:
    """Recall + store against NebulonMD through any BrainPort-shaped brain."""

    def __init__(self, brain: Any = None, user: str = "") -> None:
        self._brain = brain
        self._user = str(user or "")

    def _require_brain(self) -> Any:
        if self._brain is None:
            raise RuntimeError("no brain injected — pass brain=... explicitly")
        return self._brain

    def recall(self, query: str, top_k: int = 5, user: str | None = None,
               **kwargs: Any) -> dict[str, Any]:
        """Semantic recall: normalized ``{query, hits, count}``."""
        text = str(query or "").strip()
        if not text:
            raise ValueError("query must be non-empty")
        brain = self._require_brain()
        try:
            hits = brain.search(text, top_k=int(top_k))
        except TypeError:
            hits = brain.search(text)
        hits = list(hits or []) if isinstance(hits, (list, tuple)) else [hits]
        return {"query": text, "hits": hits, "count": len(hits)}

    def store(self, text: str | None = None, turn: dict[str, Any] | None = None,
              facts: list[dict[str, Any]] | None = None,
              user: str | None = None,
              **kwargs: Any) -> dict[str, Any]:
        """Persist a turn and/or facts. Returns what the backend reported.

        * ``turn``    -> ``brain.save_chat(turn, user=...)`` as given;
        * ``text``    -> ``brain.remember(text, user=...)``;
        * ``facts``   -> one ``remember`` per ``{text, ...}`` entry.
        At least one of the three is required.
        """
        if text is None and turn is None and not facts:
            raise ValueError("store needs text=, turn= or facts=")
        brain = self._require_brain()
        who = str(user or "") or self._user
        out: dict[str, Any] = {}
        if turn is not None:
            if not isinstance(turn, dict):
                raise ValueError("turn must be a mapping")
            out["saved"] = brain.save_chat(dict(turn), user=who or None)
        if text is not None:
            out["remembered"] = brain.remember(str(text), user=who or None)
        remembered_facts = []
        for fact in facts or []:
            if not isinstance(fact, dict) or not str(fact.get("text", "")).strip():
                raise ValueError("each fact needs a non-empty 'text'")
            entry = dict(fact)
            body = entry.pop("text")
            remembered_facts.append(brain.remember(str(body), user=who or None, **entry))
        if remembered_facts:
            out["facts"] = remembered_facts
        return out
