"""
Test: nak brain connectivity probe.

Run:
    python -m nak.test.test_brain_connection
    python -m nak.brain --check
    python nak/test/test_brain_connection.py

This hits the live NebulonMind at base_url from nebulonak.cfg
and prints a nice report. No pytest needed.
"""

import json
import sys
from pathlib import Path

# allow running as script without install
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nak.brain import Brain


def main() -> int:
    brain = Brain()
    print(f"NebulonAK Brain Connectivity Test")
    print(f"  base_url : {brain.base_url}")
    print(f"  user     : {brain.user}")
    print(f"  cfg      : {brain._cfg_path}")
    print()

    result = brain.test_connection(probe_write=True, verbose=True)

    print("\n" + "=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    print("=" * 60)

    if result.get("ok"):
        print("\n[OK] Brain is CONNECTED -- you can build agents/tools on it.")
        # demo chat if LLM is configured
        try:
            demo = brain.chat("Hello! Who are you? Reply in one short sentence.")
            print("\n[demo] agent/chat response preview:")
            print(json.dumps(demo, indent=2, ensure_ascii=False, default=str)[:3000])
        except Exception as exc:
            print(f"\n[demo] chat skipped (LLM maybe not configured): {exc}")
        return 0
    else:
        print("\n[FAIL] Not fully connected -- see steps above.", file=sys.stderr)
        print("Hint: ensure NebulonDB (6969) and NebulonMind (9696) are running:", file=sys.stderr)
        print("  curl http://localhost:9696/api/NebulonMind/health", file=sys.stderr)
        print("  python -m nak.brain --check", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
