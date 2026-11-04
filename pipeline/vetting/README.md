# Vetting

Vetting evaluates NebulonAK workers with Nunnan as the impartial assessor. It
supports three workflows behind one command:

- `run` — question/answer vetting with craft and tool probes
- `card` — persona, behavior, tool and craft report cards
- `improve` — stage and verify craft improvements from a run

## Entry points

```bash
nak-vetting --help
python -m pipeline.vetting --help
```

Both point at the same CLI. With no subcommand, `nak-vetting` defaults to
`run`, so existing commands such as `nak-vetting --workers viyan` still work.

## Commands

### Run vetting

```bash
nak-vetting run --workers viyan,ezhilan --cases 6 --threshold 0.6
nak-vetting run --workers viyan --json
nak-vetting run --workers viyan --quiet
nak-vetting run --workers ezhilan --cases-from ezhilan --repeat 2
```

Outputs land in `pipeline/vetting/runs/<stamp>/` with `summary.md`,
`summary.json`, and one report per worker.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--workers` | Worker names, space or comma separated |
| `--cases` | Cases per worker (default `6`) |
| `--threshold` | Passing score in `[0, 1]` (default `0.6`) |
| `--run-dir` | Explicit output directory |
| `--endpoint` | NebulonMD brain URL |
| `--timeout` | Brain request timeout in seconds |
| `--skip-crafts` / `--skip-tools` | Skip probes |
| `--include-assessor` | Include Nunnan in an auto-run |
| `--target-decision` | Keep the target's triage/policy layer enabled |
| `--cases-from` | Grade on a reviewed bank (worker name, `worker.vN`, or file path) instead of generated questions |
| `--repeat` | Run N passes and grade the aggregate (median + majority; default `1`) |
| `--json` / `--quiet` | Machine-readable or minimal output |

### Graded runs (banks + repeats)

Generative runs invent fresh questions every time, so scores move between
runs. Graded runs fix the questions: reviewed banks live in
`pipeline/vetting/cases/<worker>.vN.json`, and `--repeat N` runs the same
bank N times (subdirs `repeat-01..N`) grading the aggregate
(`aggregate.json`/`aggregate.md`): per-worker median score plus a
strict-majority pass count. Grade promotions on banks + repeats; use
generative runs for discovery (finding new weaknesses).

```bash
nak-vetting run --workers ezhilan --cases-from ezhilan --repeat 3
```

Rules: with `--cases-from`, every vetted worker must resolve a bank (a path
bank must declare that worker); `--cases` is nominal (each worker runs its
bank's questions). Banks are validated on load and re-checked for craft
grounding at run time.

### Report card

```bash
nak-vetting card --workers viyan --persona-cases 10 --behavior-cases 10
nak-vetting card --workers viyan --threshold 60 --weights persona=25,behavior=25,tools=25,craft=25
```

Cards are written under `<run-dir>/report_card/` with `summary.md`,
`summary.json`, and one card per worker.

Card-only flags:

| Flag | Meaning |
| --- | --- |
| `--persona-cases` | Persona scenarios per worker (default `10`, max `10`) |
| `--behavior-cases` | Behavior cases per worker (default `10`) |
| `--threshold` | Passing overall score out of `100` (default `60`) |
| `--weights` | Section weights, e.g. `persona=25,behavior=25,tools=25,craft=25` |
| `--skip-persona` / `--skip-behavior` | Skip a section |

### Improvement loop

```bash
nak-vetting improve plan <run-dir>
nak-vetting improve apply <run-dir> --workers viyan
nak-vetting improve apply <run-dir> --no-core --no-verify
nak-vetting improve discard <run-id>
```

`plan` shows which crafts would be staged without writing anything. `apply`
copies the craft tree into `<workspace>/improve/<run>/candidate/`, tests it with
the `core` team, then re-vets the affected workers. `discard` removes a staged
candidate while keeping `history.json`.

The old `--improvement` flag is replaced by the `improve` subcommand.

## Common options

All commands accept `--workers`, `--include-assessor`, `--run-dir`,
`--endpoint`, `--timeout`, `--workers-file`, `--permissions-file`,
`--answer-chars`, `--skip-crafts`, `--skip-tools`, `--json`, and `--quiet`.

## Layout

```text
pipeline/vetting/
  cli/            # unified CLI: run, card, improve
  utils/          # shared vetting internals (models, probes, runner, prompting)
  report/         # markdown/JSON rendering and writing
  improve/        # improvement planning, staging, verification and history
  report_card/    # report-card library (no CLI)
  prompts/        # judge/plan prompt templates
  runs/           # generated runs (gitignored)
```

`python -m pipeline.vetting.report_card` remains as a deprecated alias for
`python -m pipeline.vetting card`.

## Testing

```bash
.venv/bin/ruff check pipeline/vetting
.venv/bin/python -m pytest unittest/unit/pipeline -q
.venv/bin/python -m pytest unittest -q
```
