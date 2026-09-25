# perf-analysis-tools

Standalone performance analysis utilities for parsing simulator output and producing rollup reports.

## Scope

This repository is independent from any single producer or consumer repository.
It does not assume a checkout layout and does not import from external project codebases.

## Install

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Commands

### cobalt-stats-tally

```bash
cobalt-stats-tally FILE_REGEXES STATS_SPEC OUTPUT_CSV
```

Arguments:

- `FILE_REGEXES`: text file with one file-matching regex or glob per line
- `STATS_SPEC`: text file with tagged stat specs and formulas
- `OUTPUT_CSV`: generated rollup CSV

## Stat Spec Format

Supported forms:

```text
TAG: PATTERN
NAME = FORMULA
```

Trailing display metadata is supported:

```text
total_reads: *.std.lsc.rd [fmt=#,##0]
hit_rate = safe_div(total_hit, total_total) [fmt=0.00%]
```

## Development

Run tests:

```bash
pytest
```
