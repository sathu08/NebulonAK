"""Pipeline data shapes (no pydantic — stdlib only, DAG with branches)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


NODE_TYPES = ("prompt", "tool", "craft", "condition", "agent", "verify")
SLUG_RE = r"^[a-z0-9-]{1,64}$"


@dataclass
class PipelineNode:
    id: str
    type: str
    label: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "label": self.label, "config": dict(self.config or {})}


@dataclass
class PipelineEdge:
    from_id: str
    to_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"from_id": self.from_id, "to_id": self.to_id}


@dataclass
class PipelineDefinition:
    name: str
    slug: str
    description: str = ""
    nodes: list[PipelineNode] = field(default_factory=list)
    edges: list[PipelineEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> PipelineDefinition:
        nodes = []
        for n in raw.get("nodes") or []:
            if not isinstance(n, dict):
                continue
            nodes.append(
                PipelineNode(
                    id=str(n.get("id", "")),
                    type=str(n.get("type", "")),
                    label=str(n.get("label", "") or ""),
                    config=dict(n.get("config") or {}) if isinstance(n.get("config"), dict) else {},
                )
            )
        edges = []
        for e in raw.get("edges") or []:
            if not isinstance(e, dict):
                continue
            edges.append(PipelineEdge(from_id=str(e.get("from_id", "")), to_id=str(e.get("to_id", ""))))
        slug = str(raw.get("slug") or raw.get("name") or "").strip().lower().replace("_", "-").replace(" ", "-")
        return PipelineDefinition(
            name=str(raw.get("name", "") or slug or "pipeline"),
            slug=slug or "pipeline",
            description=str(raw.get("description", "") or ""),
            nodes=nodes,
            edges=edges,
        )

    def as_plain(self) -> dict[str, Any]:
        return asdict(self)
