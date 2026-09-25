import csv
from pathlib import Path
import sys

import py7zr
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perf_analysis_tools import cobalt_stats_tally as cst


def test_trailing_fmt_metadata_is_stripped_and_preserved(tmp_path):
    spec_path = tmp_path / "rollup.txt"
    spec_path.write_text("total_reads: *.std.lsc.rd [fmt=#,##0]\n", encoding="utf-8")

    specs = cst.read_stat_specs(spec_path)

    assert len(specs) == 1
    spec = specs[0]
    assert isinstance(spec, cst.PatternColumn)
    assert spec.header == "total_reads"
    assert spec.pattern.source == "*.std.lsc.rd"
    assert spec.fmt == "#,##0"


def test_formula_fmt_metadata_is_preserved(tmp_path):
    spec_path = tmp_path / "rollup.txt"
    spec_path.write_text(
        "hit_rate = safe_div(total_hit, total_total) [fmt=0.00%]\n",
        encoding="utf-8",
    )

    specs = cst.read_stat_specs(spec_path)

    assert len(specs) == 1
    spec = specs[0]
    assert isinstance(spec, cst.FormulaColumn)
    assert spec.header == "hit_rate"
    assert spec.expression == "safe_div(total_hit, total_total)"
    assert spec.fmt == "0.00%"


def test_apply_fmt_handles_general_integer_and_percent_formats():
    assert cst.apply_format(1234, "general") == "1234"
    assert cst.apply_format(1234, "#,##0") == "1,234"
    assert cst.apply_format(0.125, "0.00%") == "12.50%"


def test_discover_stats_files_preserves_grouped_rollup_order(tmp_path, monkeypatch):
    file_patterns = tmp_path / "stats_files.txt"
    file_patterns.write_text(
        "\n".join(
            [
                r"^ttl_hpx_Test2[\\/]\d+\.esimd[^\\/]*[\\/]cobalt_sim_stats\.(?:7z|txt)$",
                r"^ttl_hpx_Test11[\\/]\d+\.esimd[^\\/]*[\\/]cobalt_sim_stats\.(?:7z|txt)$",
                r"^ttl_hpx_Test13[\\/]\d+\.esimd[^\\/]*[\\/]cobalt_sim_stats\.(?:7z|txt)$",
                r"^ttl_hpx_Test19[\\/]\d+\.esimd[^\\/]*[\\/]cobalt_sim_stats\.(?:7z|txt)$",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    for relative_path in [
        "ttl_hpx_Test2/1.esimd/cobalt_sim_stats.7z",
        "ttl_hpx_Test11/0.esimd/cobalt_sim_stats.7z",
        "ttl_hpx_Test13/2.esimd/cobalt_sim_stats.7z",
        "ttl_hpx_Test13/10.esimd_dist1_twolevel0/cobalt_sim_stats.7z",
        "ttl_hpx_Test13/11.esimd_dist1_twolevel1/cobalt_sim_stats.7z",
        "ttl_hpx_Test13/12.esimd_dist2_twolevel0/cobalt_sim_stats.7z",
        "ttl_hpx_Test13/13.esimd_dist2_twolevel1/cobalt_sim_stats.7z",
        "ttl_hpx_Test13/14.esimd_dist2_twolevel0/cobalt_sim_stats.txt",
        "ttl_hpx_Test13/15.esimd_dist2_twolevel1/cobalt_sim_stats.7z",
        "ttl_hpx_Test19/3.esimd/cobalt_sim_stats.txt",
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    discovered = cst.discover_stats_files(cst.read_file_patterns(file_patterns))

    assert [path.as_posix() for path in discovered] == [
        (tmp_path / "ttl_hpx_Test2/1.esimd/cobalt_sim_stats.7z").resolve().as_posix(),
        (tmp_path / "ttl_hpx_Test11/0.esimd/cobalt_sim_stats.7z").resolve().as_posix(),
        (tmp_path / "ttl_hpx_Test13/2.esimd/cobalt_sim_stats.7z").resolve().as_posix(),
        (tmp_path / "ttl_hpx_Test13/10.esimd_dist1_twolevel0/cobalt_sim_stats.7z").resolve().as_posix(),
        (tmp_path / "ttl_hpx_Test13/11.esimd_dist1_twolevel1/cobalt_sim_stats.7z").resolve().as_posix(),
        (tmp_path / "ttl_hpx_Test13/12.esimd_dist2_twolevel0/cobalt_sim_stats.7z").resolve().as_posix(),
        (tmp_path / "ttl_hpx_Test13/13.esimd_dist2_twolevel1/cobalt_sim_stats.7z").resolve().as_posix(),
        (tmp_path / "ttl_hpx_Test13/14.esimd_dist2_twolevel0/cobalt_sim_stats.txt").resolve().as_posix(),
        (tmp_path / "ttl_hpx_Test13/15.esimd_dist2_twolevel1/cobalt_sim_stats.7z").resolve().as_posix(),
        (tmp_path / "ttl_hpx_Test19/3.esimd/cobalt_sim_stats.txt").resolve().as_posix(),
    ]


def test_parse_compact_and_single_value_lines():
    assert cst.parse_compact_or_single_line(["foo", "bar", "=", "3"]) == [
        cst.StatEntry(name="foo.bar", value=3),
    ]

    assert cst.parse_compact_or_single_line(["foo", "9"]) == [
        cst.StatEntry(name="foo", value=9),
    ]


def test_iter_stats_lines_prefers_cobalt_stats_txt_in_archive(tmp_path):
    archive_path = tmp_path / "stats.7z"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "ignored.txt").write_text("wrong\n", encoding="utf-8")
    (source_dir / "cobalt_sim_stats.txt").write_text("right\n", encoding="utf-8")
    with py7zr.SevenZipFile(archive_path, "w") as archive:
        archive.write(source_dir / "ignored.txt", arcname="ignored.txt")
        archive.write(source_dir / "cobalt_sim_stats.txt", arcname="cobalt_sim_stats.txt")

    assert cst.iter_stats_lines(archive_path) == ["right"]


def test_validate_formula_tags_rejects_later_references(tmp_path):
    spec_path = tmp_path / "rollup.txt"
    spec_path.write_text(
        "hit: *.hit\ntotal = safe_div(hit, miss)\nmiss: *.miss\n",
        encoding="utf-8",
    )

    specs = cst.read_stat_specs(spec_path)

    with pytest.raises(ValueError, match="unknown or later tag 'miss'"):
        cst.validate_formula_tags(specs)


def test_main_smoke_writes_expected_csv(tmp_path, monkeypatch):
    file_regexes = tmp_path / "stats_files.txt"
    file_regexes.write_text("^sample[\\/]1\\.esimd[\\/]cobalt_sim_stats\\.txt$\n", encoding="utf-8")

    stats_spec = tmp_path / "rollup_stats.txt"
    stats_spec.write_text(
        "hits: *.hit [fmt=#,##0]\n"
        "misses: *.miss [fmt=#,##0]\n"
        "hit_rate = safe_div(hits, hits + misses) [fmt=0.00%]\n",
        encoding="utf-8",
    )

    stats_dir = tmp_path / "sample" / "1.esimd"
    stats_dir.mkdir(parents=True)
    (stats_dir / "cobalt_sim_stats.txt").write_text(
        "foo.hit 3\n"
        "foo.miss 1\n",
        encoding="utf-8",
    )

    output_csv = tmp_path / "out" / "result.csv"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cobalt-stats-tally",
            str(file_regexes),
            str(stats_spec),
            str(output_csv),
        ],
    )

    assert cst.main() == 0

    with output_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    assert rows == [
        ["stats_file", "hits", "misses", "hit_rate"],
        [(stats_dir / "cobalt_sim_stats.txt").resolve().as_posix(), "3", "1", "75.00%"],
    ]
