# Task: Python Log Analyzer CLI

Build a standalone Python command-line tool that reads an application log file,
counts log levels, finds the most frequent error messages, handles missing files
and malformed lines, and exports a JSON report.

Create every file in the current workspace root. Python 3.10+. Use only the
standard library (`argparse`, `json`, `re`, `pathlib`, `datetime`). Tests use
`pytest`.

## Files to create

- `log_analyzer.py` — library: parsing, counting, report building, JSON export.
- `cli.py` — command-line entry point.
- `tests/test_log_analyzer.py` — requirement-mapped pytest tests.
- `tests/fixtures/sample.log` — valid log fixture (see below).
- `tests/fixtures/malformed.log` — malformed log fixture (see below).
- `README.md` — short usage, schema, and test instructions.

## Library API (`log_analyzer.py`)

- `LEVELS = ("INFO", "WARNING", "ERROR")`
- `analyze_lines(lines, source="") -> dict` — analyze an iterable of lines.
- `analyze_file(path) -> dict` — read a file and call `analyze_lines`; raise
  `FileNotFoundError("log file not found: <path>")` when the file is missing.
- `write_report(report, output_path) -> None` — write the report as JSON.

## Parsing rules

- Read UTF-8 with `errors="replace"`.
- `total_lines` is the number of lines from `splitlines()` (blank lines count).
- A non-empty line is valid when it contains one of `INFO`, `WARNING`, `ERROR`
  as a standalone word, case-insensitive (use a word-boundary regex).
- Normalize the matched level to uppercase before counting.
- The error message is the text after the level token, stripped.
  If it is empty, use `"(no message)"`.
- A non-empty line with no recognized level is malformed: ignore it for counts
  and increment `malformed`. Blank/whitespace-only lines are ignored and are
  NOT counted as malformed.
- `counts` always contains all three keys (`INFO`, `WARNING`, `ERROR`), using 0
  when absent.
- `top_errors` groups ERROR messages by exact text, sorts by count descending
  then message ascending, and includes at most 10 entries.
- `file` is the source path as a string (`str(path)`).
- `generated_at` is a UTC ISO-8601 timestamp.

## JSON schema (exact keys)

```json
{
  "file": "app.log",
  "total_lines": 7,
  "counts": {"INFO": 3, "WARNING": 1, "ERROR": 3},
  "top_errors": [
    {"message": "Database timeout", "count": 2},
    {"message": "Cache miss", "count": 1}
  ],
  "malformed": 0,
  "generated_at": "2026-11-02T07:00:00+00:00"
}
```

## CLI contract (`cli.py`)

```bash
python cli.py --input app.log --output report.json
```

- `--input` and `--output` are required.
- On success: write the JSON report with `indent=2`, `ensure_ascii=False`, print
  `wrote <output>` to stdout, and exit 0.
- Missing input file: print `error: log file not found: <path>` to stderr, do
  not write the output file, exit 2.
- Any other read/write error: print `error: <message>` to stderr, exit 2.
- `if __name__ == "__main__": raise SystemExit(main())`

## Fixtures

`tests/fixtures/sample.log`:

```text
2026-11-02 07:00:00 INFO Application started
2026-11-02 07:00:01 WARNING Disk usage high
2026-11-02 07:00:02 ERROR Database timeout
2026-11-02 07:00:03 INFO Request received
2026-11-02 07:00:04 ERROR Database timeout
2026-11-02 07:00:05 INFO Request received
2026-11-02 07:00:06 ERROR Cache miss
```

`tests/fixtures/malformed.log`:

```text
INFO good line
this line has no level
WARNING another valid line
CRITICAL unknown level

ERROR final error
```

## Required tests (exact names)

- `test_reads_log_file` — `analyze_file` returns `file` and `total_lines`.
- `test_counts_by_level` — sample counts are INFO 3, WARNING 1, ERROR 3.
- `test_top_error_messages` — `Database timeout` (2) before `Cache miss` (1).
- `test_missing_file_returns_error` — `analyze_file` raises `FileNotFoundError`.
- `test_malformed_lines_are_skipped` — malformed fixture: counts stay valid,
  `malformed == 2`, blank line ignored.
- `test_export_json_report` — `write_report` creates valid JSON with exactly the
  keys `file`, `total_lines`, `counts`, `top_errors`, `malformed`,
  `generated_at`.
- `test_cli_end_to_end` — run `python cli.py --input tests/fixtures/sample.log
  --output <tmp>/report.json`, exit 0, JSON matches expected counts.
- `test_empty_file` — counts are all 0, `top_errors == []`, `malformed == 0`.
- `test_unknown_level_is_malformed` — `CRITICAL` line increments `malformed`.
- `test_case_insensitive_levels` — lowercase `info` counts as `INFO`.

## Acceptance

- `python -m pytest -q` passes with all tests green.
- `python cli.py --input tests/fixtures/sample.log --output report.json` exits 0
  and produces a valid JSON report.
- A missing input exits 2 and writes no report.
- Do not create or modify files outside the workspace root.
- Run the tests at the end and report the exact command and output.
