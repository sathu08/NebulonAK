"""Render staged improvement notes."""

from __future__ import annotations

from typing import Any


def candidate_md(original: str, improvement: dict[str, Any]) -> str:
    note = [
        "",
        "---",
        "",
        "## Staged improvement",
        "",
        f"- Source: {improvement.get('source', '')}",
        f"- Severity: {improvement.get('severity', '')}",
        f"- Problem: {improvement.get('problem', '')}",
        "",
        "### Evidence",
        "",
        str(improvement.get("evidence", "") or "(none)"),
        "",
        "### Required change",
        "",
        str(improvement.get("suggestion", "") or "(none)"),
        "",
    ]
    return original.rstrip() + "\n" + "\n".join(note)
