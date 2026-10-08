"""
`ling-admin audit egress --docs`: the local file index's scenario
(specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §10.2, specs/DREAMFERENCE_MIGHTLING_EGRESS.md).

This module provides the DocsEgressAudit class. It indexes a fixture folder with `ling-docs` and
runs a search, both under `strace -f`, in a throwaway home, and passes only if the trace shows no
network destination at all: not a remote one, not a loopback one, and no DNS query. Unlike the
session audit it needs no model server, since `ling-docs` never talks to one. The extractor and the
embedder run in `systemd-run --scope` (which execs the command, so strace follows it) inside bwrap
with an empty network namespace; the trace covers them too.
"""

import os
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

from dreamference.audit.egress_trace import EgressTrace
from dreamference.audit.egress_verdict import FAIL, PASS, TRACE_FAILED, EgressVerdict
from dreamference.audit.strace_parser import StraceParser

TRACED_SYSCALLS = "connect,sendto,sendmsg,sendmmsg,execve"
RUN_TIMEOUT_S = 900
FIXTURE_QUESTION = "Which bird nests in burrows on sea cliffs?"
FIXTURE_ANSWER = "puffins nest in burrows"


class DocsEgressAudit:
    """Traces `ling-docs index` and `ling-docs search` over a fixture and judges the trace."""

    @classmethod
    def fixture_pdf(cls, lines: List[str]) -> bytes:
        """
        A one-page PDF whose text layer is `lines` (Helvetica, no external resources).

        Args:
            lines (List[str]): The page's lines.

        Returns:
            bytes: The file.
        """
        content = "BT /F1 12 Tf 72 720 Td 14 TL\n" + "".join(
            "(" + line.replace("(", "\\(").replace(")", "\\)") + ") Tj T*\n" for line in lines) + "ET"
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
        """
        Writes the fixture folder: `~/Documents` (a default collection) with a Markdown note, a
        text file and a PDF, plus a file of a type the index never opens.

        Args:
            home (str): The throwaway home.

        Returns:
            str: The folder.
        """
        folder = os.path.join(home, "Documents")
        os.makedirs(folder)
        with open(os.path.join(folder, "seabirds.md"), "w") as handle:
            handle.write("# Seabirds\n\n## Atlantic puffin\n\nAtlantic puffins nest in burrows on grassy sea cliffs.\n")
        with open(os.path.join(folder, "notes.txt"), "w") as handle:
            handle.write("Shopping: bread, milk, coffee beans.\n")
        with open(os.path.join(folder, "report.pdf"), "wb") as handle:
            handle.write(cls.fixture_pdf(["Colony survey", "The colony counted 412 breeding pairs this year."]))
        with open(os.path.join(folder, "installer.deb"), "wb") as handle:
            handle.write(b"never opened")
        return folder

    @classmethod
    def judge(cls, trace: EgressTrace, indexed: bool, found: bool) -> EgressVerdict:
        """
        Decides the verdict: no destination of any kind, no DNS, and a run that did its work.

        Args:
            trace (EgressTrace): What the two commands did.
            indexed (bool): Whether `ling-docs index` reported the collection `ok`.
            found (bool): Whether the search found the fixture's answer.

        Returns:
            EgressVerdict: The verdict and its reasons.
        """
        problems = [f"connected or sent to {target} ({count}x)" for target, count in sorted(trace.destinations.items())]
        problems += [f"sent a DNS query to {server} ({count}x)" for server, count in sorted(trace.dns_servers.items())]
        problems += [f"asked a resolver for {name} ({count}x)" for name, count in sorted(trace.dns_names.items())]
        if problems:
            return EgressVerdict(FAIL, problems)
        if not trace.processes:
            return EgressVerdict(TRACE_FAILED, ["strace recorded no process: it could not attach"])
        if not indexed:
            return EgressVerdict(TRACE_FAILED, ["ling-docs did not index the fixture, so the trace shows nothing"])
        if not found:
            return EgressVerdict(TRACE_FAILED, ["the search did not find the fixture's answer"])
        return EgressVerdict(PASS)

    @classmethod
    def trace(cls, ling_docs: str, work_dir: str) -> Tuple[EgressTrace, bool, bool, Dict[str, str]]:
        """
        Runs `ling-docs index` and `ling-docs search` under strace in a throwaway home.

        Args:
            ling_docs (str): The executable.
            work_dir (str): A scratch directory, owned by the caller.

        Returns:
            Tuple[EgressTrace, bool, bool, Dict[str, str]]: The parsed trace of both commands,
            whether indexing succeeded, whether the search found the answer, and their output.
        """
        home = os.path.join(work_dir, "home")
        os.makedirs(home)
        cls.make_fixture(home)
        env = {key: value for key, value in os.environ.items() if key not in ("CODEX_HOME", "DREAMFERENCE_CONFIG_PATH")}
        env.update({"HOME": home, "CODEX_HOME": os.path.join(home, ".mightling"),
                    "DREAMFERENCE_CONFIG_PATH": os.path.join(work_dir, "config.toml")})
        with open(env["DREAMFERENCE_CONFIG_PATH"], "w") as handle:
            handle.write("mightling_docs = true\n")
        text = ""
        outputs: Dict[str, str] = {}
        for step, args in (("index", ["index"]), ("search", ["search", FIXTURE_QUESTION, "--k", "3"])):
            trace_path = os.path.join(work_dir, f"trace-{step}.txt")
            command = ["strace", "-f", "-qq", "-e", f"trace={TRACED_SYSCALLS}", "-s", "256", "-o", trace_path, ling_docs] + args
            try:
                result = subprocess.run(command, cwd=home, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
                outputs[step] = result.stdout + result.stderr
            except (OSError, subprocess.SubprocessError) as error:
                outputs[step] = str(error)
            if os.path.isfile(trace_path):
                with open(trace_path, errors="replace") as handle:
                    text += handle.read()
        indexed = "documents: ok" in outputs.get("index", "")
        found = FIXTURE_ANSWER in outputs.get("search", "").lower()
        return StraceParser.parse(text), indexed, found, outputs

    @classmethod
    def run(cls, ling_docs: Optional[str] = None) -> int:
        """
        Runs the scenario and prints its report.

        Args:
            ling_docs (Optional[str]): The executable; the installed one by default.

        Returns:
            int: 0 on a pass, 1 on any destination, 2 when the trace itself failed.
        """
        from dreamference.runner.codex_branded_builder import INSTALL_DIR
        if shutil.which("strace") is None:
            print("⚠️  Docs egress audit: trace failed\n   - strace is not installed: sudo apt-get install strace")
            return 2
        ling_docs = ling_docs or os.path.join(INSTALL_DIR, "bin", "ling-docs")
        if not os.access(ling_docs, os.X_OK):
            print("⚠️  Docs egress audit: trace failed\n   - ling-docs is not built: run `ling-admin codex build` first.")
            return 2
        print("🚀 Tracing `ling-docs index` and `ling-docs search` over a fixture folder (throwaway home)...")
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
        programs = ", ".join(f"{name} ({count})" for name, count in sorted(trace.processes.items(), key=lambda item: (-item[1], item[0])))
        print(f"Processes started: {programs or 'none'}")
        return verdict.exit_code
