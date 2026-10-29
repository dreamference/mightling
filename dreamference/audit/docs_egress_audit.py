"""`ling-admin audit egress --docs`.

The local file index's scenario (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §10.2,
specs/DREAMFERENCE_MIGHTLING_EGRESS.md).

This module provides the DocsEgressAudit class. It indexes a fixture folder with `ling-docs` and
runs a search, both under `strace -f`, in a throwaway home, and passes only if the trace shows no
network destination at all: not a remote one, not a loopback one, and no DNS query. Unlike the
session audit it needs no model server, since `ling-docs` never talks to one. The extractor and the
embedder run in `systemd-run --scope` (which execs the command, so strace follows it) inside bwrap
with an empty network namespace; the trace covers them too.
"""

import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import asdict

from dreamference.audit.egress_trace import EgressTrace
from dreamference.audit.egress_verdict import FAIL, PASS, TRACE_FAILED, EgressVerdict
from dreamference.audit.strace_parser import StraceParser

TRACED_SYSCALLS = "connect,sendto,sendmsg,sendmmsg,execve"
RUN_TIMEOUT_S = 900
FIXTURE_QUESTION = "Which bird nests in burrows on sea cliffs?"
FIXTURE_ANSWER = "puffins nest in burrows"
# The session name in the result file's name and body, beside `exec`, `tui`, `app` and `web`.
DOCS_SESSION = "docs"


class DocsEgressAudit:
    """Traces `ling-docs index` and `ling-docs search` over a fixture and judges the trace."""

    @classmethod
    def fixture_pdf(cls, lines: list[str]) -> bytes:
        """A one-page PDF whose text layer is `lines` (Helvetica, no external resources).

        Args:
            lines (List[str]): The page's lines.

        Returns:
            bytes: The file.
        """
        content = (
            "BT /F1 12 Tf 72 720 Td 14 TL\n"
            + "".join(
                "(" + line.replace("(", "\\(").replace(")", "\\)") + ") Tj T*\n" for line in lines
            )
            + "ET"
        )
        objects = [
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, body in enumerate(objects, 1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n{body}\nendobj\n".encode()
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)

    @classmethod
    def make_fixture(cls, home: str) -> str:
        """Writes the fixture folder.

        `~/Documents` (a default collection) with a Markdown note, a text file, a PDF, a PDF page
        without a text layer (for OCR), a saved web page, a Word file and an e-mail with a CSV
        attachment (every parser the index has), plus a file of a type the index never opens.

        Args:
            home (str): The throwaway home.

        Returns:
            str: The folder.
        """
        folder = os.path.join(home, "Documents")
        pathlib.Path(folder).mkdir(parents=True)
        with pathlib.Path(os.path.join(folder, "seabirds.md")).open("w") as handle:
            handle.write(
                "# Seabirds\n\n## Atlantic puffin\n\nAtlantic puffins nest in burrows on grassy sea cliffs.\n"
            )
        with pathlib.Path(os.path.join(folder, "notes.txt")).open("w") as handle:
            handle.write("Shopping: bread, milk, coffee beans.\n")
        with pathlib.Path(os.path.join(folder, "report.pdf")).open("wb") as handle:
            handle.write(
                cls.fixture_pdf(
                    ["Colony survey", "The colony counted 412 breeding pairs this year."]
                )
            )
        # A page with no text layer, as a scan reads: OCR's scope runs too where OCR is installed.
        (pathlib.Path(folder) / "scan.pdf").write_bytes(cls.fixture_pdf([]))
        with (pathlib.Path(folder) / "terns.html").open("w") as handle:
            handle.write(
                "<html><body><h1>Terns</h1><p>Arctic terns migrate from pole to pole.</p></body></html>\n"
            )
        word = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        with zipfile.ZipFile(
            pathlib.Path(folder) / "gulls.docx", "w", zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                "word/document.xml",
                f"<w:document {word}><w:body><w:p><w:r><w:t>Gulls follow fishing boats.</w:t></w:r></w:p></w:body></w:document>",
            )
        with (pathlib.Path(folder) / "survey.eml").open("w") as handle:
            handle.write(
                "From: warden@example.org\nSubject: Survey dates\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=b\n\n"
                "--b\nContent-Type: text/plain\n\nThe survey runs in June.\n--b\nContent-Type: text/csv\n"
                "Content-Disposition: attachment; filename=counts.csv\n\nSpecies,Pairs\nRazorbill,57\n--b--\n"
            )
        with pathlib.Path(os.path.join(folder, "installer.deb")).open("wb") as handle:
            handle.write(b"never opened")
        return folder

    @classmethod
    def judge(cls, trace: EgressTrace, indexed: bool, found: bool) -> EgressVerdict:
        """Decides the verdict: no destination of any kind, no DNS, and a run that did its work.

        Args:
            trace (EgressTrace): What the two commands did.
            indexed (bool): Whether `ling-docs index` reported the collection `ok`.
            found (bool): Whether the search found the fixture's answer.

        Returns:
            EgressVerdict: The verdict and its reasons.
        """
        problems = [
            f"connected or sent to {target} ({count}x)"
            for target, count in sorted(trace.destinations.items())
        ]
        problems += [
            f"sent a DNS query to {server} ({count}x)"
            for server, count in sorted(trace.dns_servers.items())
        ]
        problems += [
            f"asked a resolver for {name} ({count}x)"
            for name, count in sorted(trace.dns_names.items())
        ]
        if problems:
            return EgressVerdict(FAIL, problems)
        if not trace.processes:
            return EgressVerdict(TRACE_FAILED, ["strace recorded no process: it could not attach"])
        if not indexed:
            return EgressVerdict(
                TRACE_FAILED, ["ling-docs did not index the fixture, so the trace shows nothing"]
            )
        if not found:
            return EgressVerdict(TRACE_FAILED, ["the search did not find the fixture's answer"])
        return EgressVerdict(PASS)

    @classmethod
    def trace(cls, ling_docs: str, work_dir: str) -> tuple[EgressTrace, bool, bool, dict[str, str]]:
        """Runs `ling-docs index` and `ling-docs search` under strace in a throwaway home.

        Args:
            ling_docs (str): The executable.
            work_dir (str): A scratch directory, owned by the caller.

        Returns:
            Tuple[EgressTrace, bool, bool, Dict[str, str]]: The parsed trace of both commands,
            whether indexing succeeded, whether the search found the answer, and their output.
        """
        home = os.path.join(work_dir, "home")
        pathlib.Path(home).mkdir(parents=True)
        cls.make_fixture(home)
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ("CODEX_HOME", "DREAMFERENCE_CONFIG_PATH")
        }
        env.update(
            {
                "HOME": home,
                "CODEX_HOME": os.path.join(home, ".mightling"),
                "DREAMFERENCE_CONFIG_PATH": os.path.join(work_dir, "config.toml"),
            }
        )
        with pathlib.Path(env["DREAMFERENCE_CONFIG_PATH"]).open("w") as handle:
            handle.write("mightling_docs = true\n")
        text = ""
        outputs: dict[str, str] = {}
        for step, args in (
            ("index", ["index"]),
            ("search", ["search", FIXTURE_QUESTION, "--k", "3"]),
        ):
            trace_path = os.path.join(work_dir, f"trace-{step}.txt")
            command = [
                "strace",
                "-f",
                "-qq",
                "-e",
                f"trace={TRACED_SYSCALLS}",
                "-s",
                "256",
                "-o",
                trace_path,
                ling_docs,
            ] + args
            try:
                result = subprocess.run(
                    command,
                    cwd=home,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=RUN_TIMEOUT_S,
                )
                outputs[step] = result.stdout + result.stderr
            except (OSError, subprocess.SubprocessError) as error:
                outputs[step] = str(error)
            if pathlib.Path(trace_path).is_file():
                with pathlib.Path(trace_path).open(errors="replace") as handle:
                    text += handle.read()
        indexed = "documents: ok" in outputs.get("index", "")
        found = FIXTURE_ANSWER in outputs.get("search", "").lower()
        return StraceParser.parse(text), indexed, found, outputs

    @classmethod
    def write_result(cls, trace: EgressTrace, verdict: EgressVerdict, ling_docs: str) -> str:
        """Writes the full result as JSON under `$CODEX_HOME/audit/`.

        As `<timestamp>-docs.json` beside the session audit's `<timestamp>-<session>.json`.

        Args:
            trace (EgressTrace): What the two commands did.
            verdict (EgressVerdict): The verdict.
            ling_docs (str): The executable that was traced.

        Returns:
            str: The file written.
        """
        from dreamference.runner.codex_installer import CodexInstaller

        directory = os.path.join(CodexInstaller.home_dir(), "audit")
        pathlib.Path(directory).mkdir(exist_ok=True, parents=True)
        now = datetime.datetime.now().astimezone()
        path = os.path.join(directory, f"{now:%Y%m%d-%H%M%S}-{DOCS_SESSION}.json")
        digest = hashlib.sha256()
        try:
            with pathlib.Path(ling_docs).open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            sha256: str | None = digest.hexdigest()
        except OSError:
            sha256 = None
        result = {
            "at": now.isoformat(timespec="seconds"),
            "session": DOCS_SESSION,
            "verdict": verdict.status,
            "problems": verdict.problems,
            **asdict(trace),
            "ling_docs": ling_docs,
            "ling_docs_sha256": sha256,
        }
        with pathlib.Path(path).open("w") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        return path

    @classmethod
    def run(cls, ling_docs: str | None = None, write_json: bool = False) -> int:
        """Runs the scenario and prints its report.

        Args:
            ling_docs (Optional[str]): The executable; the installed one by default.
            write_json (bool): Also write the full result to `$CODEX_HOME/audit/<timestamp>-docs.json`.

        Returns:
            int: 0 on a pass, 1 on any destination, 2 when the trace itself failed.
        """
        from dreamference.runner.codex_branded_builder import INSTALL_DIR

        if shutil.which("strace") is None:
            print(
                "⚠️  Docs egress audit: trace failed\n   - strace is not installed: sudo apt-get install strace"
            )
            return 2
        ling_docs = ling_docs or os.path.join(INSTALL_DIR, "bin", "ling-docs")
        if not os.access(ling_docs, os.X_OK):
            print(
                "⚠️  Docs egress audit: trace failed\n   - ling-docs is not built: run `ling-admin codex build` first."
            )
            return 2
        print(
            "🚀 Tracing `ling-docs index` and `ling-docs search` over a fixture folder (throwaway home)..."
        )
        work_dir = tempfile.mkdtemp(prefix="ling-docs-audit-")
        try:
            trace, indexed, found, outputs = cls.trace(ling_docs, work_dir)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        verdict = cls.judge(trace, indexed, found)
        mark = {PASS: "✅", FAIL: "❌"}.get(verdict.status, "⚠️ ")
        print(f"{mark} Docs egress audit: {verdict.status}")
        for problem in verdict.problems:
            print(f"   - {problem}")
        if verdict.status == TRACE_FAILED:
            for step, text in outputs.items():
                print(f"   {step}: {text.strip()[-400:]}")
        print(f"Network destinations: {', '.join(sorted(trace.destinations)) or 'none'}")
        print(f"DNS queries: {', '.join(sorted(trace.dns_names)) or 'none'}")
        print(f"Unix sockets: {', '.join(sorted(trace.unix_sockets)) or 'none'}")
        programs = ", ".join(
            f"{name} ({count})"
            for name, count in sorted(trace.processes.items(), key=lambda item: (-item[1], item[0]))
        )
        print(f"Processes started: {programs or 'none'}")
        if write_json:
            print(f"💡 Full result: {cls.write_result(trace, verdict, ling_docs)}")
        return verdict.exit_code
