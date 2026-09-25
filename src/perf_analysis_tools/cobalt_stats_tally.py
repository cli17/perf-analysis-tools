#!/usr/bin/env python3
"""Tally Cobalt sim stats into a CSV matrix.

Usage:
    cobalt_stats_tally.py FILE_REGEXES STATS_REGEXES OUTPUT_CSV
FILE_REGEXES is a text file with one file-matching pattern per line. Each line
may be either a Python regular expression or a shell-style glob.

STATS_REGEXES is a text file with one stat spec per line. Supported forms:

    TAG: PATTERN
    NAME = FORMULA

PATTERN may be either a Python regular expression or a shell-style glob such as
*.std.lsc.rd. Formula expressions may reference earlier TAG values and use +,
-, *, /, //, parentheses, unary +/-, and safe_div(a, b).

Each stats-matching pattern must have an explicit tag, and that tag becomes the
CSV header for the corresponding column. OUTPUT_CSV contains one row per stats
file and one column per stat spec.
"""

from __future__ import annotations

import argparse
import ast
import csv
import fnmatch
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import py7zr


@dataclass(frozen=True)
class StatEntry:
    name: str
    value: int | float


@dataclass(frozen=True)
class CompiledPattern:
    source: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class PatternColumn:
    header: str
    tag: str | None
    pattern: CompiledPattern
    fmt: str = "general"


@dataclass(frozen=True)
class FormulaColumn:
    header: str
    expression: str
    fmt: str = "general"


ColumnSpec = PatternColumn | FormulaColumn
ALLOWED_FUNCTIONS = {"safe_div"}
HEARTBEAT_LINE_INTERVAL = 10000


def parse_stat_value(token: str) -> int | float | None:
    lower = token.lower()
    if lower == "true":
        return 1
    if lower == "false":
        return 0

    try:
        return int(token, 10)
    except ValueError:
        try:
            return float(token)
        except ValueError:
            return None


def parse_named_value(name: str, token: str) -> StatEntry | None:
    value = parse_stat_value(token)
    if value is None:
        return None
    return StatEntry(name=name, value=value)


def parse_compact_pairs(tokens: list[str]) -> list[StatEntry]:
    first_equals = tokens.index("=")
    prefix_tokens = tokens[: max(first_equals - 1, 0)]
    prefix = " ".join(prefix_tokens)
    entries: list[StatEntry] = []

    index = max(first_equals - 1, 0)
    while index + 2 < len(tokens):
        name = tokens[index]
        if tokens[index + 1] != "=":
            index += 1
            continue

        entry_name = f"{prefix}.{name}" if prefix else name
        entry = parse_named_value(entry_name, tokens[index + 2])
        if entry is not None:
            entries.append(entry)
        index += 3

    return entries


def parse_single_value(tokens: list[str]) -> list[StatEntry]:
    for index in range(len(tokens) - 1, -1, -1):
        entry = parse_named_value(" ".join(tokens[:index]), tokens[index])
        if entry is not None:
            return [entry]
    return []


def parse_compact_or_single_line(tokens: list[str]) -> list[StatEntry]:
    if "=" in tokens:
        return parse_compact_pairs(tokens)
    return parse_single_value(tokens)


def iter_stats_lines(path: Path) -> list[str]:
    if path.suffix.lower() != ".7z":
        return path.read_text(encoding="utf-8").splitlines()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        with py7zr.SevenZipFile(path, "r") as archive:
            names = archive.getnames()
            text_name = select_archive_member(names, path)
            archive.extract(path=temp_root, targets=[text_name])

        extracted_path = temp_root / text_name
        return extracted_path.read_text(encoding="utf-8").splitlines()


def select_archive_member(names: list[str], archive_path: Path) -> str:
    text_names = [name for name in names if name.endswith(".txt")]
    if len(text_names) == 1:
        return text_names[0]
    if "cobalt_sim_stats.txt" in names:
        return "cobalt_sim_stats.txt"
    if len(names) == 1:
        return names[0]
    raise ValueError(f"Could not determine stats member inside archive {archive_path}")


def parse_stats_file(path: Path) -> list[StatEntry]:
    entries: list[StatEntry] = []
    for raw_line in iter_stats_lines(path):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        tokens = line.split()
        entries.extend(parse_compact_or_single_line(tokens))
    return entries


def iter_parsed_entries(path: Path, file_index: int, total_files: int) -> tuple[list[StatEntry], int]:
    entries: list[StatEntry] = []
    line_count = 0
    heartbeat = ProgressHeartbeat(path, file_index, total_files)
    for raw_line in iter_stats_lines(path):
        line_count += 1
        heartbeat.maybe_report(line_count)
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        tokens = line.split()
        entries.extend(parse_compact_or_single_line(tokens))

    heartbeat.finish(line_count)
    return entries, line_count


def compile_user_pattern(pattern: str, source_path: Path, line_number: int) -> CompiledPattern:
    try:
        return CompiledPattern(source=pattern, regex=re.compile(pattern))
    except re.error:
        try:
            return CompiledPattern(source=pattern, regex=re.compile(fnmatch.translate(pattern)))
        except re.error as exc:
            raise ValueError(
                f"Invalid pattern on line {line_number} of {source_path}: {pattern!r}"
            ) from exc


def read_non_comment_lines(path: Path) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            text = raw_line.strip().lstrip("\ufeff")
            if not text or text.startswith("#"):
                continue
            lines.append((line_number, text))
    return lines


def read_file_patterns(path: Path) -> list[CompiledPattern]:
    return [compile_user_pattern(text, path, line_number) for line_number, text in read_non_comment_lines(path)]


def is_formula_line(text: str) -> bool:
    if ":" in text:
        colon_index = text.index(":")
        equals_index = text.find("=")
        if equals_index != -1 and colon_index < equals_index:
            return False
    return "=" in text


def strip_trailing_format(text: str) -> tuple[str, str]:
    fmt = "general"
    marker = "[fmt="
    if marker in text:
        start = text.rfind(marker)
        if start >= 0 and text.endswith("]"):
            fmt = text[start + len(marker) : -1].strip()
            text = text[:start].rstrip()
    return text, fmt


def read_stat_specs(path: Path) -> list[ColumnSpec]:
    specs: list[ColumnSpec] = []
    for line_number, text in read_non_comment_lines(path):
        text, fmt = strip_trailing_format(text)

        if is_formula_line(text):
            header, expression = text.split("=", 1)
            header = header.strip()
            expression = expression.strip()
            if not header or not expression:
                raise ValueError(f"Invalid formula on line {line_number} of {path}: {text!r}")
            specs.append(FormulaColumn(header=header, expression=expression, fmt=fmt))
            continue

        if ":" in text:
            tag, pattern = text.split(":", 1)
            tag = tag.strip()
            pattern = pattern.strip()
            if not tag or not pattern:
                raise ValueError(f"Invalid tagged stat on line {line_number} of {path}: {text!r}")
            specs.append(
                PatternColumn(
                    header=tag,
                    tag=tag,
                    pattern=compile_user_pattern(pattern, path, line_number),
                    fmt=fmt,
                )
            )
            continue

        raise ValueError(
            f"Invalid stat spec on line {line_number} of {path}: expected 'tag: pattern' or 'name = formula'"
        )
    return specs


def normalize_path(path: Path) -> list[str]:
    resolved = path.resolve()
    candidates = [str(path), path.as_posix(), str(resolved), resolved.as_posix()]
    try:
        relative = resolved.relative_to(Path.cwd().resolve())
        candidates.append(relative.as_posix())
        candidates.append(str(relative))
    except ValueError:
        pass

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def path_matches_any(path: Path, patterns: list[CompiledPattern]) -> bool:
    candidates = normalize_path(path)
    for pattern in patterns:
        if any(pattern.regex.search(candidate) for candidate in candidates):
            return True
    return False


def natural_sort_key(path: Path) -> tuple[tuple[int | str, ...], ...]:
    key_parts: list[tuple[int | str, ...]] = []
    for segment in path.as_posix().split("/"):
        chunks = re.split(r"(\d+)", segment)
        normalized = tuple(int(chunk) if chunk.isdigit() else chunk for chunk in chunks if chunk)
        key_parts.append(normalized)
    return tuple(key_parts)


def discover_stats_files(patterns: list[CompiledPattern]) -> list[Path]:
    root = Path.cwd()
    candidates = [candidate.resolve() for candidate in root.rglob("*") if candidate.is_file()]
    selected: list[Path] = []
    seen_keys: set[Path] = set()

    for pattern in patterns:
        preferred: dict[Path, Path] = {}
        for candidate in candidates:
            if not path_matches_any(candidate, [pattern]):
                continue

            key = stats_file_preference_key(candidate)
            previous = preferred.get(key)
            if previous is None or stats_file_priority(candidate) < stats_file_priority(previous):
                preferred[key] = candidate

        for candidate in sorted(preferred.values(), key=natural_sort_key):
            key = stats_file_preference_key(candidate)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            selected.append(candidate)

    return selected


def stats_file_preference_key(path: Path) -> Path:
    suffix = path.suffix.lower()
    if suffix in {".7z", ".txt"}:
        return path.parent / path.stem
    return path


def stats_file_priority(path: Path) -> int:
    suffix = path.suffix.lower()
    if suffix == ".7z":
        return 0
    if suffix == ".txt":
        return 1
    return 2


class ProgressHeartbeat:
    def __init__(self, path: Path, file_index: int, total_files: int):
        self.path = path
        self.file_index = file_index
        self.total_files = total_files
        self.start_time = time.monotonic()
        self.last_reported_line = 0
        self.dots_emitted = 0
        print(
            f"[{file_index}/{total_files}] parsing {path.as_posix()} ",
            end="",
            flush=True,
        )

    def maybe_report(self, line_count: int) -> None:
        if line_count - self.last_reported_line < HEARTBEAT_LINE_INTERVAL:
            return
        print(".", end="", flush=True)
        self.dots_emitted += 1
        self.last_reported_line = line_count

    def finish(self, line_count: int) -> None:
        elapsed = time.monotonic() - self.start_time
        print(flush=True)
        print(
            f"[done] parsing {self.path.as_posix()} lines={line_count} elapsed={elapsed:.1f}s",
            flush=True,
        )


def split_specs(specs: list[ColumnSpec]) -> tuple[list[PatternColumn], list[FormulaColumn]]:
    pattern_specs: list[PatternColumn] = []
    formula_specs: list[FormulaColumn] = []
    for spec in specs:
        if isinstance(spec, PatternColumn):
            pattern_specs.append(spec)
        else:
            formula_specs.append(spec)
    return pattern_specs, formula_specs


def matching_pattern_indexes(
    stat_name: str,
    pattern_specs: list[PatternColumn],
    cache: dict[str, list[int]],
) -> list[int]:
    cached = cache.get(stat_name)
    if cached is not None:
        return cached

    matches = [
        index for index, spec in enumerate(pattern_specs)
        if spec.pattern.regex.search(stat_name)
    ]
    cache[stat_name] = matches
    return matches


def tally_file(
    path: Path,
    pattern_specs: list[PatternColumn],
    match_cache: dict[str, list[int]],
    file_index: int,
    total_files: int,
) -> dict[str, int | float]:
    totals = {spec.header: 0 for spec in pattern_specs}
    entries, _line_count = iter_parsed_entries(path, file_index, total_files)
    for entry in entries:
        for index in matching_pattern_indexes(entry.name, pattern_specs, match_cache):
            totals[pattern_specs[index].header] += entry.value
    return totals


def referenced_names(expression: str) -> set[str]:
    node = ast.parse(expression, mode="eval")
    names: set[str] = set()
    for subnode in ast.walk(node):
        if not isinstance(subnode, ast.Name):
            continue
        if isinstance(subnode.ctx, ast.Load) and subnode.id in ALLOWED_FUNCTIONS:
            continue
        names.add(subnode.id)
    return names


def validate_formula_tags(specs: list[ColumnSpec]) -> None:
    available_tags: set[str] = set()
    for spec in specs:
        if isinstance(spec, PatternColumn):
            if spec.tag is not None:
                available_tags.add(spec.tag)
            continue

        for tag in referenced_names(spec.expression):
            if tag not in available_tags:
                raise ValueError(
                    f"Formula '{spec.header}' references unknown or later tag '{tag}'"
                )
        available_tags.add(spec.header)


def eval_formula(expression: str, values: dict[str, int | float]) -> int | float:
    node = ast.parse(expression, mode="eval")
    return eval_formula_node(node.body, values)


def safe_div(numerator: int | float, denominator: int | float) -> int | float:
    if denominator == 0:
        return 0
    return numerator / denominator


def eval_formula_node(node: ast.AST, values: dict[str, int | float]) -> int | float:
    if isinstance(node, ast.BinOp):
        left = eval_formula_node(node.left, values)
        right = eval_formula_node(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        raise ValueError(f"Unsupported operator in formula: {ast.dump(node.op)}")

    if isinstance(node, ast.UnaryOp):
        operand = eval_formula_node(node.operand, values)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"Unsupported unary operator in formula: {ast.dump(node.op)}")

    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ValueError(f"Unknown value referenced in formula: {node.id}")
        return values[node.id]

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError(f"Unsupported function call in formula: {ast.dump(node)}")
        if node.func.id not in ALLOWED_FUNCTIONS:
            raise ValueError(f"Unsupported function in formula: {ast.dump(node)}")
        if node.func.id == "safe_div":
            if len(node.args) != 2 or node.keywords:
                raise ValueError("safe_div requires exactly two positional arguments")
            numerator = eval_formula_node(node.args[0], values)
            denominator = eval_formula_node(node.args[1], values)
            return safe_div(numerator, denominator)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value

    raise ValueError(f"Unsupported formula expression: {ast.dump(node)}")


def apply_format(value: int | float, fmt: str = "general") -> str:
    if fmt == "general" or fmt == "":
        return format_value(value)

    if fmt in {"#,##0", "#,#0"}:
        number = float(value) if not isinstance(value, int) else value
        if isinstance(number, float) and number.is_integer():
            number = int(number)
        return f"{number:,}"

    if fmt.endswith("%"):
        fmt_body = fmt[:-1].strip()
        if fmt_body.startswith("0.") or fmt_body.startswith("#."):
            places = max(len(fmt_body.split(".", 1)[1]) if "." in fmt_body else 0, 0)
            return f"{value * 100:.{places}f}%"

    return format_value(value)


def format_value(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def build_row_from_totals(
    totals_by_header: dict[str, int | float],
    pattern_specs: list[PatternColumn],
    formula_specs: list[FormulaColumn],
) -> list[str]:
    values_by_tag: dict[str, int | float] = dict(totals_by_header)
    row: list[str] = []

    for spec in pattern_specs:
        row.append(apply_format(totals_by_header[spec.header], spec.fmt))

    for spec in formula_specs:
        value = eval_formula(spec.expression, values_by_tag)
        row.append(apply_format(value, spec.fmt))
        values_by_tag[spec.header] = value

    return row


def write_csv(output_csv: Path, stats_files: list[Path], specs: list[ColumnSpec]) -> None:
    headers = [spec.header for spec in specs]
    pattern_specs, formula_specs = split_specs(specs)
    match_cache: dict[str, list[int]] = {}
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["stats_file", *headers])
        total_files = len(stats_files)
        for file_index, stats_file in enumerate(stats_files, start=1):
            totals = tally_file(stats_file, pattern_specs, match_cache, file_index, total_files)
            row = build_row_from_totals(totals, pattern_specs, formula_specs)
            writer.writerow([stats_file.as_posix(), *row])


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file_regexes", type=Path, help="Text file with one stats-file pattern per line")
    parser.add_argument("stats_regexes", type=Path, help="Text file with stat patterns, tags, or formulas")
    parser.add_argument("output_csv", type=Path, help="Output CSV file")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    file_patterns = read_file_patterns(args.file_regexes)
    if not file_patterns:
        raise ValueError(f"No stats-file regexes found in {args.file_regexes}")

    stat_specs = read_stat_specs(args.stats_regexes)
    if not stat_specs:
        raise ValueError(f"No stat regexes found in {args.stats_regexes}")
    validate_formula_tags(stat_specs)

    stats_files = discover_stats_files(file_patterns)
    if not stats_files:
        raise ValueError("No stats files matched the provided file regexes")

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_csv, stats_files, stat_specs)

    print(f"Wrote {len(stats_files)} rows to {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
