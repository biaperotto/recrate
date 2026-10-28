"""Outputs of kind ``files``: a workflow's own test compares output files (sha1), and so does ReCRATE."""

from __future__ import annotations

import hashlib

from recrate.compare import compare_results, files_table
from recrate.model import COMPARED_KINDS, OUTPUT_KINDS, TOLERANCE_SOURCES, build_ladder


def test_files_are_compared_by_sha1_and_the_ladder_reads_it(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.txt").write_text("alpha\n")
    (out / "b.txt").write_text("BETA!\n")  # same size as the published one, other content
    produced = files_table(out, tmp_path / "produced.csv")
    sha_a, sha_b = hashlib.sha1(b"alpha\n").hexdigest(), hashlib.sha1(b"beta!\n").hexdigest()
    expected = tmp_path / "expected.csv"
    expected.write_text(f"file,sha1,size\na.txt,{sha_a},6\nb.txt,{sha_b},6\nc.txt,00,3\n")
    c = compare_results(produced, expected, {"sha1": 0.0}, sources={"sha1": "test-stated"})
    by = {m.measure: m for m in c.measures}
    assert by["sha1 · a.txt"].within and by["sha1 · a.txt"].note == "identical (sha1)"
    assert not by["sha1 · b.txt"].within and by["sha1 · b.txt"].note == "same size, different content"
    assert c.missing_images == ["c.txt"] and not c.reproduced and c.count == "1/2"
    assert "b.txt: same size, different content" in c.headline()

    class Spec:  # what build_ladder reads
        workflow_path, example_data, expected_results = expected, out, expected
        output_kind, draft, needs_data = "files", False, True

    ladder = build_ladder(Spec(), comparison=c)
    assert ladder["numbers"]["status"] == "partial" and "test-stated" in ladder["numbers"]["reason"]
    assert "1 published item(s) not produced" in ladder["numbers"]["reason"]
    assert "files" in COMPARED_KINDS and "files" in OUTPUT_KINDS and "test-stated" in TOLERANCE_SOURCES


def test_all_files_identical_reproduces(tmp_path):
    out = tmp_path / "out"
    (out / "sub").mkdir(parents=True)
    (out / "sub" / "r.csv").write_text("x\n1\n")
    produced = files_table(out, tmp_path / "produced.csv")
    expected = tmp_path / "expected.csv"
    sha_r = hashlib.sha1(b"x\n1\n").hexdigest()
    expected.write_text(f"file,sha1\nr.csv,{sha_r}\n")  # matched by base name too
    c = compare_results(produced, expected, {"sha1": 0.0}, sources={"sha1": "test-stated"})
    assert c.reproduced and c.headline().startswith("Reproduced")


def test_files_pinned_by_md5_or_by_name_only(tmp_path):
    """nf-test snapshots pin a file's md5 (``name:md5,<hash>``) and, for files whose content varies, its name only:
    the first compares by md5, the second is reproduced when the file is produced at all."""
    out = tmp_path / "out"
    (out / "fastqc").mkdir(parents=True)
    (out / "cowpy.txt").write_text("moo\n")
    (out / "fastqc" / "report.html").write_text("<html>varies</html>")
    produced = files_table(out, tmp_path / "produced.csv")
    assert produced.read_text().splitlines()[0] == "file,sha1,md5,size"
    expected = tmp_path / "expected.csv"
    expected.write_text(f"file,md5\ncowpy.txt,{hashlib.md5(b'moo' + chr(10).encode()).hexdigest()}\n"
                        "fastqc/report.html,\nfastqc/missing.html,\n")
    c = compare_results(produced, expected, {}, sources={"md5": "test-stated"})
    by = {m.measure: m for m in c.measures}
    assert by["md5 · cowpy.txt"].within and by["md5 · cowpy.txt"].note == "identical (md5)"
    assert by["present · fastqc/report.html"].within and "pins its name" in by["present · fastqc/report.html"].note
    assert c.missing_images == ["fastqc/missing.html"] and c.count == "2/2"
    assert all(m.source == "test-stated" for m in c.measures)
    # a compressed file that differs is said with why it can (gzip headers), not as a plain difference
    (out / "reads.fastq.gz").write_bytes(b"\x1f\x8b" + b"x" * 20)
    produced = files_table(out, tmp_path / "produced2.csv")
    expected.write_text("file,md5\nreads.fastq.gz,00000000000000000000000000000000\n")
    c = compare_results(produced, expected, {}, sources={"md5": "test-stated"})
    assert not c.measures[0].within and "timestamp" in c.measures[0].note
