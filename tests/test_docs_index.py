"""
The Python side of the local file index (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md): the pinned
run-time files `ling-admin docs setup` installs, and the docs egress scenario's verdict. Nothing
here downloads anything or runs strace: fetches are stubbed, traces are text.
"""

import hashlib
import io
import os
import zipfile
import tarfile

import pytest

from dreamference.audit.docs_egress_audit import DocsEgressAudit
from dreamference.audit.strace_parser import StraceParser
from dreamference.runner import docs_index_setup
from dreamference.runner.docs_index_setup import DocsIndexSetup


def _tarball(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    """Pins pointing at in-memory archives and model files, and the install dirs under tmp."""
    pdfium = _tarball({"lib/libpdfium.so": b"pdfium"})
    ort = _tarball({"ort/lib/libonnxruntime.so.1.30.0": b"onnxruntime"})
    files = {
        "https://x/pdfium.tgz": pdfium,
        "https://x/ort.tgz": ort,
    }
    sha = lambda data: hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(docs_index_setup, "LIBRARIES", {"aarch64": [
        ("https://x/pdfium.tgz", sha(pdfium), "lib/libpdfium.so", "libpdfium.so"),
        ("https://x/ort.tgz", sha(ort), "ort/lib/libonnxruntime.so.1.30.0", "libonnxruntime.so"),
    ]})
    model = {"onnx/model_int8.onnx": b"weights", "tokenizer.json": b"{}"}
    monkeypatch.setattr(docs_index_setup, "MODEL_FILES", [
        ("onnx/model_int8.onnx", sha(model["onnx/model_int8.onnx"]), "model.onnx"),
        ("tokenizer.json", sha(model["tokenizer.json"]), "tokenizer.json"),
    ])
    for path, data in model.items():
        files[f"https://huggingface.co/{docs_index_setup.MODEL_REPO}/resolve/{docs_index_setup.MODEL_REVISION}/{path}"] = data
    monkeypatch.setattr(docs_index_setup, "DOCS_LIB_DIR", str(tmp_path / "lib" / "ling-docs"))
    monkeypatch.setattr(docs_index_setup, "DOCS_MODEL_DIR", str(tmp_path / "models" / "m"))
    fetched = []

    def fake_fetch(cls, url, expected):
        fetched.append(url)
        data = files[url]
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("mismatch")
        return data

    monkeypatch.setattr(DocsIndexSetup, "fetch", classmethod(fake_fetch))
    return tmp_path, files, fetched


def test_setup_installs_each_pinned_file_once(pinned):
    tmp_path, _, fetched = pinned
    assert DocsIndexSetup.missing("aarch64") == ["libpdfium.so", "libonnxruntime.so", "model.onnx", "tokenizer.json"]
    assert DocsIndexSetup.install("aarch64") is True
    assert (tmp_path / "lib" / "ling-docs" / "libpdfium.so").read_bytes() == b"pdfium"
    assert (tmp_path / "lib" / "ling-docs" / "libonnxruntime.so").read_bytes() == b"onnxruntime"
    assert (tmp_path / "models" / "m" / "model.onnx").read_bytes() == b"weights"
    assert len(fetched) == 4
    assert DocsIndexSetup.missing("aarch64") == []
    assert DocsIndexSetup.install("aarch64") is True
    assert len(fetched) == 4, "nothing is fetched again"


def test_a_file_that_does_not_match_its_pin_is_refused(pinned):
    tmp_path, files, _ = pinned
    files["https://x/ort.tgz"] = _tarball({"ort/lib/libonnxruntime.so.1.30.0": b"tampered"})
    assert DocsIndexSetup.install("aarch64") is False
    assert not (tmp_path / "lib" / "ling-docs" / "libonnxruntime.so").exists()


def test_an_unknown_machine_is_refused_without_a_download(pinned):
    _, _, fetched = pinned
    assert DocsIndexSetup.install("riscv64") is False
    assert fetched == []


def test_the_real_pins_cover_both_linux_machines_and_the_measured_model():
    assert set(docs_index_setup.LIBRARIES) == {"aarch64", "x86_64"}
    for entries in docs_index_setup.LIBRARIES.values():
        assert [name for *_, name in entries] == ["libpdfium.so", "libonnxruntime.so"]
        for url, sha, _, _ in entries:
            assert url.startswith("https://github.com/") and len(sha) == 64
    assert docs_index_setup.PDFIUM_RELEASE == "chromium/8076"
    assert [name for *_, name in docs_index_setup.MODEL_FILES] == ["model.onnx", "tokenizer.json"]
    assert docs_index_setup.MODEL_FILES[0][0] == "onnx/model_int8.onnx"


# -- the docs egress scenario -------------------------------------------------------------------

CLEAN = """\
100 execve("/opt/ling-docs", ["ling-docs", "index"], 0x0 /* 3 vars */) = 0
100 connect(3, {sa_family=AF_UNIX, sun_path="/run/user/1000/bus"}, 110) = 0
101 execve("/usr/bin/bwrap", ["bwrap", "--unshare-net"], 0x0 /* 3 vars */) = 0
"""


def test_a_trace_with_no_destination_passes_once_the_work_was_done():
    trace = StraceParser.parse(CLEAN)
    assert DocsEgressAudit.judge(trace, indexed=True, found=True).status == "pass"
    assert DocsEgressAudit.judge(trace, indexed=False, found=True).status == "trace failed"
    assert DocsEgressAudit.judge(trace, indexed=True, found=False).status == "trace failed"


def test_any_destination_fails_even_on_loopback():
    for line in (
        '102 connect(5, {sa_family=AF_INET, sin_port=htons(8000), sin_addr=inet_addr("127.0.0.1")}, 16) = 0\n',
        '102 connect(5, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("140.82.112.3")}, 16) = -1 EINPROGRESS\n',
        '102 connect(5, {sa_family=AF_INET, sin_port=htons(53), sin_addr=inet_addr("127.0.0.53")}, 16) = 0\n',
    ):
        verdict = DocsEgressAudit.judge(StraceParser.parse(CLEAN + line), indexed=True, found=True)
        assert verdict.status == "fail", line
        assert verdict.exit_code == 1


def test_the_fixture_is_a_readable_pdf_and_a_folder_the_index_takes(tmp_path):
    folder = DocsEgressAudit.make_fixture(str(tmp_path))
    assert folder == os.path.join(str(tmp_path), "Documents")
    # Every parser the index has is exercised: a page, a Word file (a ZIP), mail with an attachment.
    assert sorted(os.listdir(folder)) == ["gulls.docx", "installer.deb", "notes.txt", "report.pdf", "seabirds.md", "survey.eml", "terns.html"]
    assert zipfile.is_zipfile(os.path.join(folder, "gulls.docx"))
    with open(os.path.join(folder, "report.pdf"), "rb") as handle:
        data = handle.read()
    assert data.startswith(b"%PDF-1.4") and b"startxref" in data
    # The xref offsets point at the objects.
    xref = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert data[xref:xref + 4] == b"xref"
    first = int(data[xref:].split(b"\n")[3].split()[0])
    assert data[first:first + 7] == b"1 0 obj"


def test_without_strace_or_ling_docs_the_trace_fails(monkeypatch, tmp_path):
    import shutil as shutil_module
    monkeypatch.setattr(shutil_module, "which", lambda name: None)
    assert DocsEgressAudit.run(ling_docs=str(tmp_path / "ling-docs")) == 2
    monkeypatch.setattr(shutil_module, "which", lambda name: "/usr/bin/strace")
    assert DocsEgressAudit.run(ling_docs=str(tmp_path / "missing")) == 2


def test_json_writes_the_result_as_timestamp_docs_json(monkeypatch, tmp_path, capsys):
    # `audit egress --docs --json` used to ignore `--json`; the file sits beside the session
    # audit's `<timestamp>-<session>.json`.
    import json
    import shutil as shutil_module
    ling_docs = tmp_path / "ling-docs"
    ling_docs.write_bytes(b"#!/bin/sh\n")
    ling_docs.chmod(0o755)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(shutil_module, "which", lambda name: "/usr/bin/strace")
    monkeypatch.setattr(DocsEgressAudit, "trace", classmethod(
        lambda cls, binary, work_dir: (StraceParser.parse(CLEAN), True, True, {})))
    assert DocsEgressAudit.run(ling_docs=str(ling_docs)) == 0
    assert not (tmp_path / "codex-home" / "audit").exists()
    assert DocsEgressAudit.run(ling_docs=str(ling_docs), write_json=True) == 0
    written = list((tmp_path / "codex-home" / "audit").iterdir())
    assert len(written) == 1 and written[0].name.endswith("-docs.json")
    assert str(written[0]) in capsys.readouterr().out
    result = json.loads(written[0].read_text())
    assert result["session"] == "docs" and result["verdict"] == "pass" and result["problems"] == []
    assert result["destinations"] == {} and "bwrap" in result["processes"]
    assert result["ling_docs_sha256"] and result["ling_docs"] == str(ling_docs)


def test_every_setting_ling_docs_reads_reaches_its_mcp_server():
    # `ling-docs mcp` runs with Codex's short allow-list plus the launcher's FORWARDED_ENV; four
    # `DREAMFERENCE_MIGHTLING_DOCS_*` settings (the embedding model among them) were not in it.
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    read = set(re.findall(r'"(DREAMFERENCE_MIGHTLING_DOCS\w*)"', (repo / "ling-docs-rs" / "src" / "config.rs").read_text()))
    launcher = (repo / "ling-rs" / "src" / "docs_index.rs").read_text()
    forwarded = set(re.findall(r'"(\w+)"', launcher.split("const FORWARDED_ENV", 1)[1].split("];", 1)[0]))
    assert "DREAMFERENCE_MIGHTLING_DOCS_EMBEDDING_MODEL" in read
    assert read <= forwarded, sorted(read - forwarded)
