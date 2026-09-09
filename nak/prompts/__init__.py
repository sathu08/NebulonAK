"""nak.prompts -- prompt templates for agents (decision.md etc)."""
from pathlib import Path

_PROMPTS_ROOT = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Load a prompt template by name without extension (e.g. 'decision')."""
    for ext in (".md", ".j2", ".txt", ".md.j2"):
        p = _PROMPTS_ROOT / f"{name}{ext}"
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"prompt {name!r} not found in {_PROMPTS_ROOT}")


def render_prompt(name: str, **kwargs) -> str:
    """Load and format prompt with kwargs (safe: only replaces {key} for given kwargs)."""
    tmpl = load_prompt(name)
    # Try jinja2 if available and template looks jinja-ish
    if "{{" in tmpl or "{%" in tmpl:
        try:
            import jinja2  # type: ignore

            return jinja2.Template(tmpl).render(**kwargs)
        except Exception:
            pass
    # Safe replace: only substitute known {key} placeholders, preserve JSON braces like {"action":...}
    result = tmpl
    for k, v in kwargs.items():
        result = result.replace("{" + k + "}", str(v))
    return result
