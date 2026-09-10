"""
Terminal chat for pipeline.chatagent -- validation loop for DecisionAgent.

Usage:
    python -m pipeline.chatagent
    python -m pipeline.chatagent --user alice --base-url http://localhost:9696/api/NebulonMind
    python -m pipeline.chatagent --no-decision --no-session
    python -m pipeline.chatagent --once "hello, what can you do?"

Slash commands (in chat):
    /help               show this help
    /agents             list registered agents (registry.json leader)
    /create-agent <desc> guided creation: asks name + capabilities (1/2/3) + custom prompt
    /health             brain health + LLM status
    /remember <text>    store a memory directly
    /search <query>     semantic search memories
    /session            show current session id
    /new-session        create a fresh Mind session
    /decision on|off    toggle decision JSON display (default on)
    /exit, /quit, /bye  leave chat
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys

# Allow `python pipeline/chatagent/__main__.py` as well as `-m pipeline.chatagent`
try:
    from pipeline.chatagent.pipeline import ChatAgentPipeline
except ImportError:  # fallback when run as a script file
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from pipeline.chatagent.pipeline import ChatAgentPipeline

from nak.agents.DecisionAgent.models import DecisionResult
from nak.brain.client import Brain, BrainError
from nak.plugins.tool.policy import (
    get_policy,
    create_once_approved,
    mark_create_approved,
)
from nak.utils.agent_registry import validate_agent_name

BANNER = """\
NebulonAK :: chatagent pipeline (DecisionAgent + Brain)
Type a message, or /help for commands. Ctrl+C or /exit to quit.
"""

HELP = """\
Commands:
  /help               show this help
  /agents             list registered agents
  /create-agent <desc> guided creation: asks name + capabilities (1/2/3) + custom prompt
  /health             brain health + LLM status
  /remember <text>    store memory via Brain.remember
  /search <query>     search memories via Brain.search
  /session            show current session id
  /new-session        create fresh Mind session
  /decision on|off    show/hide DecisionResult JSON per turn
  /exit, /quit, /bye  leave
"""

# -- guided /create-agent helpers (thin wiring over AgentCreator + registry) --
# NOTE: the model proposes names/capabilities first (agent_naming.md,
# capabilities.md). The keyword maps below are OFFLINE FALLBACK only, used
# when Mind is unreachable or slow — never the primary path.

_NAME_KEYWORDS = (
    ("plan", "PlanningAgent"), ("excel", "ExcelAgent"), ("pdf", "PdfReaderAgent"),
    ("email", "EmailAgent"), ("research", "ResearchAgent"), ("code", "CodingAgent"),
    ("api", "CodingAgent"), ("test", "TestingAgent"),
)
_CAP_KEYWORDS = (
    ("plan", ["planning", "roadmap", "design"]),
    ("excel", ["excel", "duplicate-detection"]),
    ("pdf", ["pdf", "extract"]),
    ("research", ["research", "compare"]),
    ("code", ["coding", "review"]), ("api", ["api", "backend"]),
)
_NAME_STOP = {"a", "an", "the", "that", "this", "agent", "create", "make", "new",
              "my", "i", "want", "need", "needs", "type", "of", "for", "to",
              "please", "me", "us", "our", "with", "and"}


def _kw_hit(low: str, kw: str) -> bool:
    """Word-prefix match: 'planning' hits 'plan', but 'explain' does NOT.

    Production guard — plain `kw in low` substring matching misroutes
    (e.g. ex**plan** → PlanningAgent). Requires a word boundary before
    the keyword; trailing word chars (plurals, -ning) are allowed.
    """
    return re.search(rf"\b{re.escape(kw)}\w*\b", low) is not None


def suggest_agent_name(desc: str) -> str:
    """Suggest a reusable agent name for a free-text description."""
    low = (desc or "").lower()
    for kw, name in _NAME_KEYWORDS:
        if _kw_hit(low, kw):
            return name
    words = [w for w in re.findall(r"[A-Za-z]+", desc) if w.lower() not in _NAME_STOP][:2]
    base = "".join(w[:1].upper() + w[1:] for w in words) or "Custom"
    if not base.endswith("Agent"):
        base += "Agent"
    try:
        return validate_agent_name(base)
    except ValueError:
        return "CustomAgent"


def capability_options(desc: str) -> list:
    """3 numbered capability choices with suitable names. Last is always custom."""
    low = (desc or "").lower()
    for kw, caps in _CAP_KEYWORDS:
        if _kw_hit(low, kw):
            return [(f"{', '.join(caps)} (Recommended)", caps),
                    (caps[0], [caps[0]]),
                    ("custom (type your own)", [])]
    return [("general (Recommended)", ["general"]),
            ("chat, remember, recall", ["chat", "remember", "recall"]),
            ("custom (type your own)", [])]


async def _ask(prompt: str):
    """Prompt on terminal. Returns stripped str, or None on EOF/Ctrl+C."""
    try:
        return (await asyncio.to_thread(input, prompt)).strip()
    except (EOFError, KeyboardInterrupt):
        return None


async def _ask_yes(prompt: str) -> bool:
    """Yes-decision: accepts y / yes / 1."""
    return ((await _ask(prompt)) or "").lower() in ("y", "yes", "1")


async def _ask_choice(prompt: str, n: int) -> int:
    """Numbered-decision: 1..n (default 1 on empty/invalid)."""
    try:
        i = int(((await _ask(prompt)) or "1").strip())
        return i if 1 <= i <= n else 1
    except (ValueError, TypeError):
        return 1


async def llm_suggest_agent_name(pipe: ChatAgentPipeline, desc: str):
    """Model-proposed reusable agent name (prompt folder, no hardcoded map).

    Validated only — generic naming is enforced by the agent_naming.md
    prompt itself. Returns None on any failure/timeout — caller then falls
    back to the offline keyword suggest_agent_name().
    Bounded at 25s so a slow Mind never blocks guided creation.
    """
    try:
        from nak.prompts import render_prompt

        prompt = render_prompt("agent_naming", description=desc[:300])
    except FileNotFoundError:
        return None  # prompt folder is source of truth; caller uses keyword fallback
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(pipe.brain.chat, prompt), timeout=25)
    except Exception:
        return None
    answer = (raw.get("answer") if isinstance(raw, dict) else str(raw)) or ""
    m = re.search(r"\{.*\}", answer, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        proposed = (data.get("agent_name") if isinstance(data, dict) else None) or ""
        return validate_agent_name(proposed)
    except (ValueError, AttributeError):
        return None


async def llm_capability_options(pipe: ChatAgentPipeline, desc: str):
    """LLM-proposed capability sets (its own 3 options).

    Returns list [(label, caps)] or None on any failure/timeout —
    caller then falls back to pre-structured capability_options().
    Bounded at 25s so a slow Mind never blocks guided creation.
    """
    try:
        from nak.prompts import render_prompt

        prompt = render_prompt("capabilities", description=desc[:300])
    except FileNotFoundError:
        return None  # prompt folder is source of truth; caller uses pre-structured list
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(pipe.brain.chat, prompt), timeout=25)
    except Exception:
        return None
    answer = (raw.get("answer") if isinstance(raw, dict) else str(raw)) or ""
    m = re.search(r"\{.*\}", answer, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        items = data.get("options") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        opts = []
        for it in items[:3]:
            if not isinstance(it, dict):
                continue
            caps = [str(c).strip().lower() for c in (it.get("capabilities") or []) if str(c).strip()]
            label = str(it.get("label") or ", ".join(caps)).strip()
            if caps and label:
                opts.append((label, caps))
        if not opts:
            return None
        opts.append(("custom (type your own)", []))
        return opts
    except (ValueError, AttributeError):
        return None


async def _handle_create_agent(pipe: ChatAgentPipeline, arg: str) -> bool:
    """Guided flow: description -> ask name -> ask capabilities (1/2/3) -> confirm -> create."""
    desc = (arg or "").strip()
    if not desc:
        desc = await _ask("What type of agent do you need? Describe it: ") or ""
        if not desc:
            print("cancelled.")
            return True
    suggested = await llm_suggest_agent_name(pipe, desc) or suggest_agent_name(desc)
    raw = await _ask(f"Agent name? [{suggested}]: ")
    if raw is None:
        print("\ncancelled.")
        return True
    try:
        name = validate_agent_name(raw or suggested)
    except ValueError as exc:
        print(f"invalid name: {exc}")
        return True
    # Exact-name guard: agent_name must match the folder/registry name exactly
    # for USE_AGENT routing to resolve (validate_agent_name above enforces it).
    if pipe.registry.exists(name):
        print(f"{name} already exists — try /agents or another name.")
        return True
    opts = await llm_capability_options(pipe, desc) or capability_options(desc)
    print(f"Capabilities for {name!r} (pick 1, 2 or 3):")
    for i, (label, _) in enumerate(opts, 1):
        print(f"  {i}. {label}")
    caps = opts[(await _ask_choice("Choose [1]: ", len(opts))) - 1][1]
    if not caps:
        custom = await _ask("Type capabilities (comma separated): ") or ""
        caps = [c.strip() for c in custom.split(",") if c.strip()] or ["general"]
    raw_custom = await _ask("Custom instructions for this agent? [Enter to skip]: ")
    if raw_custom is None:
        print("\ncancelled.")
        return True
    instructions = raw_custom.strip()
    print(f"Create {name!r} — {desc[:150]} — caps: {', '.join(caps)}"
          + (f" — instructions: {instructions[:120]}" if instructions else ""))
    if not await _ask_yes("Create? [y/N or 1/2]: "):
        print("skip creation.")
        return True
    spec = DecisionResult(action="CREATE_AGENT", agent_name=name, reason=desc[:300],
                          confidence=1.0, parameters={"capabilities": caps})

    async def _yes(_s) -> bool:
        return True  # terminal already confirmed; gate still enforced via callback

    try:
        path = await pipe.creator.create(spec, _yes, description=desc[:300],
                                         capabilities=caps,
                                         template_vars={"custom_instructions": instructions})
    except (FileExistsError, ValueError) as exc:
        print(f"create failed: {exc}")
        return True
    print(f"created: {path}\nagents now: {pipe.list_agents_str()}")
    print("Now chat normally — it will route via USE_AGENT.")
    return True


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipeline.chatagent", description="DecisionAgent terminal chat (validation)"
    )
    p.add_argument("--cfg", default=None, help="Path to nebulonak.cfg")
    p.add_argument("--base-url", default=None, help="Override NebulonMind base URL")
    p.add_argument("--user", default=None, help="Override username")
    p.add_argument("--session", default=None, help="Reuse an existing Mind session id")
    p.add_argument(
        "--no-session", action="store_true", help="Disable auto session creation"
    )
    p.add_argument(
        "--no-decision", action="store_true", help="Hide DecisionResult JSON per turn"
    )
    p.add_argument("--once", default=None, help="Single turn then exit (non-interactive)")
    p.add_argument("--verbose", action="store_true", help="Verbose logging")
    return p


def _print_decision(decision: dict) -> None:
    print(
        f"[decision] action={decision.get('action')} "
        f"agent={decision.get('agent_name')} "
        f"conf={decision.get('confidence', 0.0):.2f}"
    )
    reason = (decision.get("reason") or "").strip()
    if reason:
        print(f"           reason: {reason[:300]}")
    params = decision.get("parameters") or {}
    if params and params.keys() != {"raw_answer"}:
        try:
            print(f"           params: {json.dumps(params, ensure_ascii=False)[:300]}")
        except Exception:
            pass


def _print_steps(res: dict) -> None:
    """Show which tools the agent actually ran: [tools used: a, b(failed), c(denied)]."""
    steps = res.get("steps") or []
    if not steps:
        return
    parts = []
    for s in steps:
        suffix = ""
        if not s.get("ok"):
            suffix = "(denied)" if s.get("denied") else "(failed)"
        parts.append(s.get("tool", "?") + suffix)
    print(f"[tools used: {', '.join(parts)}]")


async def _handle_slash(pipe: ChatAgentPipeline, line: str) -> bool:
    """Handle slash commands. Returns True if handled (no pipeline turn needed)."""
    parts = line.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit", "/bye"):
        print("bye.")
        raise SystemExit(0)
    if cmd == "/help":
        print(HELP)
        return True
    if cmd == "/agents":
        print(f"agents: {pipe.list_agents_str()}")
        return True
    if cmd == "/create-agent":
        return await _handle_create_agent(pipe, arg)
    if cmd == "/session":
        print(f"session_id: {pipe.session_id}")
        return True
    if cmd == "/new-session":
        sid = await asyncio.to_thread(pipe.reset_session)
        print(f"new session_id: {sid}")
        return True
    if cmd == "/health":
        try:
            live = await asyncio.to_thread(pipe.brain.health_live)
            print(f"health/live: {json.dumps(live, ensure_ascii=False)[:500]}")
        except BrainError as exc:
            print(f"health/live FAILED: {exc} (status={exc.status})")
        try:
            llm = await asyncio.to_thread(pipe.brain.llm_status)
            print(f"llm/status: {json.dumps(llm, ensure_ascii=False)[:500]}")
        except BrainError as exc:
            print(f"llm/status FAILED: {exc} (status={exc.status})")
        return True
    if cmd == "/remember":
        if not arg:
            print("usage: /remember <text>")
            return True
        try:
            res = await asyncio.to_thread(pipe.brain.remember, arg)
            print(f"remembered: {json.dumps(res, ensure_ascii=False)[:500]}")
        except BrainError as exc:
            print(f"remember FAILED: {exc} (status={exc.status})")
        return True
    if cmd == "/search":
        if not arg:
            print("usage: /search <query>")
            return True
        try:
            hits = await asyncio.to_thread(pipe.brain.search, arg)
            print(f"hits ({len(hits)}):")
            for h in hits[:5]:
                txt = json.dumps(h, ensure_ascii=False)[:300]
                print(f"  - {txt}")
        except BrainError as exc:
            print(f"search FAILED: {exc} (status={exc.status})")
        return True
    if cmd == "/decision":
        # handled by caller via return value sentinel; keep simple here
        print("usage: /decision on|off (handled per-turn)")
        return True
    print(f"unknown command {cmd!r} — try /help")
    return True


async def _one_turn(
    pipe: ChatAgentPipeline, text: str, show_decision: bool
) -> None:
    try:
        res = await pipe.handle(text)
    except BrainError as exc:
        print(f"[brain error] {exc} (status={exc.status})")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"[pipeline error] {exc}")
        return

    decision = res.get("decision", {})
    run_on = res.get("run_on") or res.get("routed_via", "DecisionAgent")
    if show_decision:
        _print_decision(decision)
    # Production indicator: ALWAYS show which agent ran (or DecisionAgent Q/proposal)
    print(f"[Running on: {run_on}]")
    _print_steps(res)
    print(f"assistant> {res.get('answer', '')}")

    # Confirm gate: DecisionAgent asks the user before ANY agent is created.
    # Covers both CREATE_AGENT and USE_AGENT-missing (stale registry) paths.
    if res.get("needs_confirm"):
        name = decision.get("agent_name") or "NewAgent"
        reason = (decision.get("reason") or "").strip()[:200]
        print(f"-- No runnable agent for this task (decision={decision.get('action')}).")
        if reason:
            print(f"   reason: {reason}")
        try:
            cmode = get_policy()["create_agent"]
        except Exception:
            cmode = "ask"
        if cmode == "no":
            print("Agent creation is disabled by policy ([policy] create_agent=no).")
            print(f"Existing agents: {pipe.list_agents_str()}")
            print("Rephrase your request, or set create_agent=ask to allow creation.")
            return
        if cmode == "allow_always":
            confirmed = True
            print("(auto-create per policy: create_agent=allow_always)")
        elif cmode == "allow_once" and create_once_approved():
            confirmed = True
            print("(creation already approved once this session)")
        else:
            confirmed = await _ask_yes(f"Can I create agent {name!r}? [y/N or 1/2]: ")
            if confirmed:
                mark_create_approved()
        if not confirmed:
            # NO branch: nothing created, nothing executed elsewhere.
            # Guide instead of going silent.
            print("No agent created.")
            print(f"Existing agents: {pipe.list_agents_str()}")
            print("Rephrase your request, or run /create-agent <description> anytime.")
            return
        try:
            path = await pipe.create_confirmed(decision)
            print(f"created: {path}")
            print(f"agents now: {pipe.list_agents_str()}")
        except FileExistsError:
            print(f"{name} already exists — running on it.")
        except (ValueError, BrainError) as exc:
            print(f"create failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"create failed: {exc}")
            return
        # YES branch: run the original request on the new agent right away.
        # Plan first (shown to you), approve to begin, then execute.
        print(f"[Running on: {name}] (first run: plan only)")
        try:
            plan_res = await pipe.run_agent_text(
                name,
                "Create a short step-by-step plan for this request. "
                "List the tools you will use "
                "(recall, remember, read_file, write_file). "
                f"Do NOT execute yet, only output the plan:\n{text}",
            )
            print(f"assistant (plan)> {plan_res['answer']}")
            _print_steps(plan_res)
            if not await _ask_yes("Approve to begin? [y/N or 1/2]: "):
                print("Stopped before execution. The plan is above — say the word to proceed.")
                return
            exec_res = await pipe.run_agent_text(
                name,
                "Approved. Execute this plan now:\n"
                f"{plan_res['answer']}\nOriginal request: {text}",
            )
            print(f"[Running on: {name}]")
            _print_steps(exec_res)
            print(f"assistant> {exec_res['answer']}")
        except BrainError as exc:
            print(f"[brain error] {exc} (status={exc.status})")
        except Exception as exc:  # noqa: BLE001
            print(f"[run error] {exc}")


async def amain(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    brain = Brain(
        base_url=args.base_url,
        user=args.user,
        cfg_path=args.cfg,
    )
    show_decision = not args.no_decision

    # Startup probe (best-effort, never fatal — validation loop must start)
    print(BANNER.rstrip())
    print(f"brain: {brain.base_url} user={brain.user}")
    try:
        live = await asyncio.to_thread(brain.health_live)
        print(f"mind live: {json.dumps(live, ensure_ascii=False)[:200]}")
    except BrainError as exc:
        print(f"mind live FAILED: {exc} (status={exc.status})")
        print("Hint: start NebulonDB (:6969) and NebulonMind (:9696) first.")
    try:
        me = await asyncio.to_thread(brain.ensure_user)
        print(f"user ok: {json.dumps(me, ensure_ascii=False)[:200]}")
    except BrainError as exc:
        print(f"ensure_user FAILED: {exc} (status={exc.status})")

    pipe = ChatAgentPipeline(
        brain=brain,
        session_id=args.session,
        auto_session=not args.no_session and args.session is None,
    )
    print(f"agents: {pipe.list_agents_str()}")
    print(f"session: {pipe.session_id}  (decision display: {'on' if show_decision else 'off'})")
    print("----")

    if args.once:
        await _one_turn(pipe, args.once, show_decision)
        return 0

    while True:
        try:
            line = await asyncio.to_thread(input, "you> ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return 0
        line = line.strip()
        if not line:
            continue
        if line.startswith("/decision"):
            _, _, val = line.partition(" ")
            val = val.strip().lower()
            if val in ("on", "off"):
                show_decision = val == "on"
                print(f"decision display: {val}")
            else:
                print(f"decision display is {'on' if show_decision else 'off'} — use /decision on|off")
            continue
        if line.startswith("/"):
            try:
                await _handle_slash(pipe, line)
            except SystemExit:
                return 0
            continue
        await _one_turn(pipe, line, show_decision)


def main(argv=None) -> int:
    try:
        return asyncio.run(amain(argv))
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
