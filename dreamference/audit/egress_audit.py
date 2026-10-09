"""
`ling-admin audit egress`: where does a `ling` session connect?
(specs/DREAMFERENCE_MIGHTLING_EGRESS.md §3)

This module provides the EgressAudit class. It runs one real `ling` session under `strace`
(`ling exec`; with `--tui` the full-screen interface on a pseudo-terminal; with `--web` the web
server, `ling web serve`, answering one Ask thread asked through it), in a throwaway
repository with a throwaway `CODEX_HOME`, and prints every network destination, every name asked
of a resolver and every process the session started, with a verdict. It makes "your code stays on your machine" something a user can check and re-check
after each Codex bump, instead of a promise.

The audit reads; the one thing it writes outside its scratch directory is the `--json` result
under `$CODEX_HOME/audit/`.
"""

import datetime
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from typing import Any, Dict, Final, List, Optional, Tuple
from urllib.parse import urlparse

from dreamference.audit.egress_trace import EgressTrace
from dreamference.audit.egress_verdict import FAIL, PASS, TRACE_FAILED, EgressVerdict
from dreamference.audit.strace_parser import StraceParser
from dreamference.audit.tui_session import TuiSession

DEFAULT_PROMPT: Final[str] = "Reply with exactly: pong"

# The syscalls traced. `sendmmsg` is how glibc sends a lookup's queries (the spec's first list
# had only sendto and sendmsg, which leaves a DNS query without its name). `write` and `writev`
# are there for one reason: a payload on a connected UDP socket, which is what turns a route
# lookup (StraceParser) back into a destination.
TRACED_SYSCALLS: Final[str] = "connect,sendto,sendmsg,sendmmsg,execve,write,writev"

SESSION_TIMEOUT_S: Final[int] = 300

# Loopback services a session may reach besides the model server (spec §4.3).
GMAIL_PORT: Final[int] = 8767
SEARXNG_PORT: Final[int] = 8888

# Where the launcher points `chatgpt_base_url`, so that a ChatGPT-backend call no patch closed
# fails on this machine. A connect there is exactly such a call.
CHATGPT_BLACKHOLE_PORT: Final[int] = 9

# The kinds of session, and what the report calls them.
EXEC: Final[str] = "exec"
TUI: Final[str] = "tui"
APP: Final[str] = "app"
WEB: Final[str] = "web"
SESSION_NAMES: Final[Dict[str, str]] = {
    EXEC: "`ling exec` session",
    TUI: "full-screen `ling` session",
    APP: "desktop app session (`ling-app`, windows hidden)",
    WEB: "`ling web` server answering an Ask thread",
}

# The desktop app's session (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §6): `ling-app` is
# started as `ling app` starts it, with `MIGHTLING_APP_AUDIT=<seconds>`: its one window opens
# hidden on Ask, the app-server it owns is started (and traced with it), and it quits by itself
# after that many seconds. Its allowlist is the `exec` session's: since 2026-10-09 the app opens
# no window on `ling web` (specs/DREAMFERENCE_MIGHTLING_ASK.md §18.6), so a connection to that
# server's port 3100, like one to the retired Onyx web UI's 3000, is a finding.
APP_SESSION_S: Final[int] = 40

# Mightling over Signal (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §10): the one component that talks to
# an outside service by itself, and only once the user set it up. It runs as its own system unit,
# outside any traced session, so the report names it instead of passing silently.
SIGNAL_UNIT: Final[str] = "mightling-signal.service"
# The messenger bridge (specs/DREAMFERENCE_MIGHTLING_CHAT.md): `ling chat start`'s user unit, and the
# loopback proxy socket `ling-admin matrix start` enables in front of the homeserver. Named the
# same way when on; off, nothing of either runs.
CHAT_UNIT: Final[str] = "mightling-chat.service"
MATRIX_PROXY_UNIT: Final[str] = "mightling-matrix-proxy.socket"

# How long `ling web serve` may take to listen.
WEB_START_TIMEOUT_S: Final[int] = 30


class EgressAudit:
    """Traces one session and judges where it connected."""

    # How far the last `tui` session got (`start`, `composer`, `reply`, `quit`), for the report
    # of a trace that failed: "no reply" alone does not say whether the interface ever opened.
    tui_stage: str = ""

    @classmethod
    def allowed_ports(cls, vllm_host: str, session: str = EXEC) -> Dict[int, str]:
        """
        The loopback ports a session may connect to.

        Args:
            vllm_host (str): The model server's base URL.
            session (str): `exec`, `tui`, `app` or `web`; every kind has the same allowlist (the
                app's reached `ling web` until its Ask window was folded into the app window).

        Returns:
            Dict[int, str]: Port to the service behind it.
        """
        parsed = urlparse(vllm_host if "://" in vllm_host else f"http://{vllm_host}")
        model_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return {model_port: "model server", GMAIL_PORT: "Gmail search service", SEARXNG_PORT: "SearXNG"}

    @classmethod
    def judge(cls, trace: EgressTrace, allowed: Dict[int, str], replied: bool) -> EgressVerdict:
        """
        Decides the verdict (§3.2): a pass needs every IP destination on loopback and on the
        allowlist, no DNS query, and no git command that reaches a network.

        Args:
            trace (EgressTrace): What the session did.
            allowed (Dict[int, str]): The allowed loopback ports.
            replied (bool): Whether the session produced a reply. Without one nothing was shown,
                and that is not a pass.

        Returns:
            EgressVerdict: The verdict and its reasons.
        """
        problems: List[str] = []
        for target, count in sorted(trace.destinations.items()):
            address, _, port = target.rpartition(":")
            address = address.strip("[]")
            if not StraceParser.is_loopback(address):
                problems.append(f"connected to {target} ({count}x): not on this machine")
            elif int(port) == CHATGPT_BLACKHOLE_PORT:
                problems.append(f"connected to {target} ({count}x): a call to the upstream vendor's backend that no patch closes "
                                "(it failed here only because the launcher redirects that URL)")
            elif int(port) not in allowed:
                problems.append(f"connected to {target} ({count}x): a local port that is not on the allowlist")
        for name, count in sorted(trace.dns_names.items()):
            problems.append(f"asked a resolver for {name} ({count}x)")
        if trace.dns_servers and not trace.dns_names:
            servers = ", ".join(sorted(trace.dns_servers))
            problems.append(f"sent a DNS query to {servers} whose name could not be read")
        for command in trace.networked_git:
            problems.append(f"ran a git command that reaches a network: {command}")
        if problems:
            return EgressVerdict(FAIL, problems)
        if not replied:
            return EgressVerdict(TRACE_FAILED, ["the session produced no reply, so the trace shows nothing"])
        if not trace.processes:
            return EgressVerdict(TRACE_FAILED, ["strace recorded no process: it could not attach"])
        return EgressVerdict(PASS)

    @classmethod
    def trace_session(cls, mightling_bin: str, vllm_host: str, prompt: str, work_dir: str,
                      session: str = EXEC, app_bin: Optional[str] = None) -> Tuple[EgressTrace, bool, str]:
        """
        Runs one `ling` session under strace in a throwaway repository with a throwaway
        `CODEX_HOME`, so no login, history or config of the user's influences the result, and
        none is touched.

        An `exec` session writes its reply to a file. A `tui` session is the interface itself on
        a pseudo-terminal (TuiSession): the throwaway home already trusts the throwaway
        repository, so it opens on the composer, and its reply is read from the session file it
        writes.

        The code index and the local file index are switched off for the session
        (`code_index_enabled = false`, `mightling_docs = false` in the throwaway config): their
        indexers run detached in their own network-less sandbox and outlive the session, so they
        are not part of what this trace can show (`audit egress --docs` traces the file index).

        An `app` session is the desktop app itself (`app_bin`), started with no argument as
        `ling app` starts it, in its audit mode: its one window hidden on Ask, its own
        `ling app-server` started (the `ling` under test, named in `MIGHTLING_BIN`), then quitting
        by itself. It "replied" when it ran to that end and exited 0. Its HOME is a scratch
        folder, so Chromium's profile and the app's own data folder are throwaway too.

        Args:
            mightling_bin (str): The `ling` executable.
            vllm_host (str): The model server's base URL.
            prompt (str): The prompt to send.
            work_dir (str): A scratch directory, owned by the caller.
            session (str): `exec`, `tui` or `app`.
            app_bin (Optional[str]): The desktop app's executable, for an `app` session.

        Returns:
            Tuple[EgressTrace, bool, str]: The parsed trace, whether the session replied, and the
            path of the raw trace.
        """
        repo = os.path.join(work_dir, "repo")
        home = os.path.join(work_dir, "home")
        os.makedirs(repo)
        os.makedirs(home)
        with open(os.path.join(repo, "README.md"), "w") as handle:
            handle.write("A throwaway repository for `ling-admin audit egress`.\n")
        git = ["git", "-c", "user.name=audit", "-c", "user.email=audit@localhost", "-c", "commit.gpgsign=false"]
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "audit"]):
            subprocess.run(git + args, cwd=repo, capture_output=True, check=False)
        config = os.path.join(work_dir, "config.toml")
        with open(config, "w") as handle:
            handle.write(f"vllm_host = {json.dumps(vllm_host)}\ncode_index_enabled = false\nmightling_docs = false\n")
        trace_path = os.path.join(work_dir, "trace.txt")
        reply_path = os.path.join(work_dir, "reply.txt")
        env = dict(os.environ)
        env.update({"CODEX_HOME": home, "DREAMFERENCE_CONFIG_PATH": config, "DREAMFERENCE_VLLM_HOST": vllm_host})
        # `-yy` labels each descriptor with its socket kind and inode, which is what tells a UDP
        # route lookup from a connection (StraceParser).
        strace = ["strace", "-f", "-qq", "-yy", "-e", f"trace={TRACED_SYSCALLS}", "-s", "256", "-o", trace_path, mightling_bin]
        if session == WEB:
            replied = cls._web_session(strace, mightling_bin, repo, env, work_dir, home, prompt)
            return cls._read_trace(trace_path), replied, trace_path
        if session == TUI:
            TuiSession.trust(home, repo)
            try:
                outcome = TuiSession.run(strace, repo, env, home, prompt, SESSION_TIMEOUT_S)
            except OSError:
                outcome = {"replied": False, "stage": "start"}
            cls.tui_stage = str(outcome.get("stage", ""))
            return cls._read_trace(trace_path), bool(outcome["replied"]), trace_path
        if session == APP:
            scratch_home = os.path.join(work_dir, "home-dir")
            os.makedirs(scratch_home)
            env.update({"HOME": scratch_home, "MIGHTLING_APP_AUDIT": str(APP_SESSION_S), "MIGHTLING_BIN": mightling_bin})
            command = strace[:-1] + [str(app_bin)]
        else:
            command = strace + ["exec", "--skip-git-repo-check", "-o", reply_path, prompt]
        exited = None
        try:
            # Its own process group, so a session that never answers is stopped with everything
            # it started: strace alone, killed, would leave `ling` waiting for the server.
            process = subprocess.Popen(command, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                exited = process.wait(timeout=SESSION_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, sig)
                        process.wait(timeout=10)
                        break
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        continue
        except OSError:
            pass
        if session == APP:
            return cls._read_trace(trace_path), exited == 0, trace_path
        replied = os.path.isfile(reply_path) and bool(open(reply_path, errors="replace").read().strip())
        return cls._read_trace(trace_path), replied, trace_path

    @classmethod
    def app_executable(cls) -> Optional[str]:
        """
        The desktop app to trace: the checkout's packaged build, else the installed `ling-app`.

        Returns:
            Optional[str]: Its path, or None when neither exists.
        """
        from dreamference.chat.desktop_runner import DesktopRunner
        return DesktopRunner.binary_path() or shutil.which("ling-app")

    @classmethod
    def _web_session(cls, strace: List[str], mightling_bin: str, repo: str, env: Dict[str, str], work_dir: str,
                     home: str, prompt: str) -> bool:
        """
        Traces `ling web serve` on a free loopback port while `ling web ask`, untraced, asks it
        one question through the bridge (specs/DREAMFERENCE_MIGHTLING_ASK.md §13). Everything the
        server starts is traced with it: the app-server it launches on its own socket, and
        `ling prompt show ask --composed`. `HOME` and `XDG_RUNTIME_DIR` are scratch too, so the
        server finds no advertised node, no user unit and no app-server of the user's to join.

        Args:
            strace (List[str]): The strace command line, ending with the `ling` executable.
            mightling_bin (str): The `ling` executable, for the untraced client.
            repo (str): The throwaway repository, used as the working directory.
            env (Dict[str, str]): The session's environment; updated in place.
            work_dir (str): The scratch directory.
            home (str): The throwaway `CODEX_HOME`.
            prompt (str): The question.

        Returns:
            bool: Whether the Ask thread answered.
        """
        run_dir = os.path.join(work_dir, "run")
        user_home = os.path.join(work_dir, "user")
        os.makedirs(run_dir, mode=0o700)
        os.makedirs(user_home)
        env.update({"HOME": user_home, "XDG_RUNTIME_DIR": run_dir})
        port = cls.free_port()
        server_file = os.path.join(home, "web", "server.json")
        try:
            server = subprocess.Popen(strace + ["web", "serve", "--port", str(port)], cwd=repo, env=env,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return False
        replied = False
        try:
            deadline = time.monotonic() + WEB_START_TIMEOUT_S
            while not os.path.isfile(server_file) and server.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if os.path.isfile(server_file):
                try:
                    ask = subprocess.run([mightling_bin, "web", "ask", "--port", str(port), prompt], cwd=repo, env=env,
                                         stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                         timeout=SESSION_TIMEOUT_S, check=False)
                    replied = ask.returncode == 0 and bool(ask.stdout.strip())
                except (OSError, subprocess.TimeoutExpired):
                    replied = False
        finally:
            # SIGTERM first: the server stops the app-server it started and removes its marker.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(server.pid, sig)
                    server.wait(timeout=20)
                    break
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    continue
        return replied

    @classmethod
    def free_port(cls) -> int:
        """
        Returns:
            int: A loopback port nothing listens on now.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    @classmethod
    def _read_trace(cls, trace_path: str) -> EgressTrace:
        text = ""
        if os.path.isfile(trace_path):
            with open(trace_path, errors="replace") as handle:
                text = handle.read()
        return StraceParser.parse(text)

    @classmethod
    def _unit_enabled(cls, unit: str, user: bool) -> bool:
        """Whether a systemd unit is enabled (`--user` for the user's own units). Reads only."""
        command = ["systemctl"] + (["--user"] if user else []) + ["is-enabled", "--quiet", unit]
        return subprocess.run(command, capture_output=True).returncode == 0

    @classmethod
    def _chat_file(cls, name: str) -> Dict[str, Any]:
        """One of the messenger bridge's JSON files in `$CODEX_HOME/chat`; empty when absent or unreadable."""
        from dreamference.runner.codex_installer import CodexInstaller

        try:
            with open(os.path.join(CodexInstaller.home_dir(), "chat", name), encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @classmethod
    def declared_exceptions(cls) -> List[str]:
        """
        Names what the user turned on that reaches outside this machine by design, which no traced
        session shows: the messenger bridges, each off until its own command turns it on
        (SIGNAL §10, CHAT §10). Off, none of them is named and none adds a destination to the trace.

        - Signal: the system unit `mightling-signal.service` (`ling signal setup`); signal-cli
          connects to Signal's servers.
        - The chat bridge: the user unit `mightling-chat.service` (`ling chat start`) with a
          Telegram bot set up (`ling chat telegram setup`) connects to Telegram's Bot API.
        - The Matrix homeserver (`ling-admin matrix start`): its loopback proxy socket is enabled
          and `tailscale serve` offers it to the user's tailnet; with push on, push notifications
          can leave the machine.

        Returns:
            List[str]: One line per enabled exception; empty when there is none.
        """
        if shutil.which("systemctl") is None:
            return []
        lines: List[str] = []
        if cls._unit_enabled(SIGNAL_UNIT, user=False):
            lines.append(f"Signal bridge enabled ({SIGNAL_UNIT}): signal-cli connects to Signal's servers, outside this trace. "
                         "`ling signal remove` turns it off.")
        if cls._unit_enabled(CHAT_UNIT, user=True) and cls._chat_file("telegram.json").get("token"):
            lines.append(f"Telegram bridge enabled ({CHAT_UNIT}): `ling chat serve` connects to Telegram's Bot API, outside this trace. "
                         "`ling chat telegram off` or `ling chat stop` turns it off.")
        if cls._unit_enabled(MATRIX_PROXY_UNIT, user=True):
            push = bool(cls._chat_file("matrix-admin.json").get("push"))
            lines.append("Matrix homeserver enabled (`ling-admin matrix`): offered to your tailnet by `tailscale serve`"
                         + ("; push is on, so push notifications (event ids, no text) can leave this machine" if push else ", with no route out")
                         + ". `ling-admin matrix stop` turns it off.")
        return lines

    @classmethod
    def render(cls, trace: EgressTrace, verdict: EgressVerdict, allowed: Dict[int, str]) -> List[str]:
        """
        Renders the report: the verdict and anything unexpected first, then everything seen.

        Args:
            trace (EgressTrace): What the session did.
            verdict (EgressVerdict): The verdict.
            allowed (Dict[int, str]): The allowed loopback ports.

        Returns:
            List[str]: The lines to print.
        """
        mark = {PASS: "✅", FAIL: "❌"}.get(verdict.status, "⚠️ ")
        lines = [f"{mark} Egress audit: {verdict.status}"]
        lines += [f"   - {problem}" for problem in verdict.problems]
        lines.append("Network destinations:")
        if not trace.destinations:
            lines.append("   none")
        for target, count in sorted(trace.destinations.items()):
            port = int(target.rpartition(":")[2])
            address = target.rpartition(":")[0].strip("[]")
            label = allowed.get(port, "not on the allowlist") if StraceParser.is_loopback(address) else "not on this machine"
            lines.append(f"   {target:<24} {count:>4}x  {label}")
        lookups = ", ".join(f"{target} ({count}x)" for target, count in sorted(trace.route_lookups.items())) or "none"
        lines.append(f"Route lookups (UDP connect, nothing sent): {lookups}")
        names = ", ".join(f"{name} ({count}x)" for name, count in sorted(trace.dns_names.items())) or "none"
        lines.append(f"DNS queries: {names}")
        sockets = ", ".join(sorted(trace.unix_sockets)) or "none"
        lines.append(f"Unix sockets: {sockets}")
        programs = ", ".join(f"{name} ({count})" for name, count in sorted(trace.processes.items(), key=lambda item: (-item[1], item[0])))
        lines.append(f"Processes started: {programs or 'none'}")
        lines.append(f"Networked git commands: {'; '.join(trace.networked_git) or 'none'}")
        return lines

    @classmethod
    def build_identity(cls, mightling_bin: str) -> Dict[str, Any]:
        """
        Says which build was audited, so two audits can be compared across builds.

        Args:
            mightling_bin (str): The `ling` executable.

        Returns:
            Dict[str, Any]: `ling --version`, the traced binary's path and hash, the Codex tag,
            the build key when the traced binary is the installed one, whether that build matches
            this checkout, and each patch's hash only when it does.
        """
        from dreamference.runner.codex_branded_builder import (
            BUILD_STAMP_NAME, CODEX_RELEASE_TAG, INSTALL_DIR, CodexBrandedBuilder,
        )
        try:
            version = subprocess.run([mightling_bin, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            version = ""
        binary = os.path.realpath(mightling_bin)
        binary_sha256 = None
        if os.path.isfile(binary):
            digest = hashlib.sha256()
            with open(binary, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
            binary_sha256 = digest.hexdigest()
        # The stamp describes the installed binary only: a scratch build traced with --mightling-bin
        # (a build without one patch, say) has none, and must not be credited with the installed one's.
        build_key = ""
        stamp = os.path.join(INSTALL_DIR, BUILD_STAMP_NAME)
        if binary == os.path.realpath(CodexBrandedBuilder.executable_path()) and os.path.isfile(stamp):
            with open(stamp) as handle:
                build_key = handle.read().strip()
        matches = bool(build_key) and build_key == CodexBrandedBuilder.build_key()
        patches: Optional[Dict[str, str]] = None
        if matches:
            patches = {}
            for path in CodexBrandedBuilder.patches():
                with open(path, "rb") as handle:
                    patches[os.path.basename(path)] = hashlib.sha256(handle.read()).hexdigest()
        return {
            "mightling_version": version,
            "mightling_bin": binary,
            "mightling_sha256": binary_sha256,
            "codex_tag": CODEX_RELEASE_TAG,
            "build_key": build_key,
            "build_matches_checkout": matches,
            # The checkout's patch hashes, recorded only when they are known to be the traced
            # binary's; otherwise None, and `mightling_sha256` is what identifies the build.
            "patches": patches,
        }

    @classmethod
    def run(cls, prompt: Optional[str] = None, write_json: bool = False,
            mightling_bin: Optional[str] = None, vllm_host: Optional[str] = None, tui: bool = False,
            app: bool = False, app_bin: Optional[str] = None, web: bool = False) -> int:
        """
        Runs the audit and prints its report.

        Args:
            prompt (Optional[str]): The prompt for the traced session; a one-word reply by default.
            write_json (bool): Also write the full result to `$CODEX_HOME/audit/<timestamp>.json`.
            tui (bool): Trace the full-screen interface on a pseudo-terminal instead of
                `ling exec`. Codex starts things there that `exec` never does.
            app (bool): Trace the desktop app in its hidden audit session instead (needs a
                display: `DISPLAY`, or `xvfb-run`; Electron's headless platform crashes here).
            web (bool): Trace `ling web serve` answering one Ask thread instead.
            mightling_bin (Optional[str]): The `ling` executable; the installed build by default.
            vllm_host (Optional[str]): The model server; the configured one by default.
            app_bin (Optional[str]): The desktop app; the packaged or installed one by default.

        Returns:
            int: 0 on a pass, 1 on an unexpected destination, 2 when the trace itself failed.
        """
        from dreamference.runner.codex_installer import CodexInstaller
        if shutil.which("strace") is None:
            print("⚠️  Egress audit: trace failed")
            print("   - strace is not installed: sudo apt-get install strace")
            return 2
        mightling_bin = mightling_bin or CodexInstaller.get_codex_executable()
        if not mightling_bin:
            print("⚠️  Egress audit: trace failed")
            print("   - ling is not built: run `ling-admin codex build` first.")
            return 2
        session = APP if app else WEB if web else TUI if tui else EXEC
        if app:
            app_bin = app_bin or cls.app_executable()
            if not app_bin:
                print("⚠️  Egress audit: trace failed")
                print("   - the desktop app is not built: run `ling-admin desktop build`, or install its .deb.")
                return 2
            if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                print("⚠️  Egress audit: trace failed")
                print("   - the desktop app needs a display: set DISPLAY, or run this under xvfb-run.")
                return 2
        missing = TuiSession.missing_modules() if tui else []
        if missing:
            print("⚠️  Egress audit: trace failed")
            print(f"   - --tui drives the interface with {' and '.join(missing)}, which this environment lacks: "
                  f"{sys.executable} -m pip install {' '.join(missing)}")
            return 2
        if vllm_host is None:
            from dreamference.config import DreamferenceConfig
            vllm_host = DreamferenceConfig().vllm_host
        allowed = cls.allowed_ports(vllm_host, session)
        print(f"🚀 Tracing one {SESSION_NAMES[session]} against {vllm_host} (throwaway repository and CODEX_HOME)...")
        work_dir = tempfile.mkdtemp(prefix="mightling-audit-")
        try:
            trace, replied, _ = cls.trace_session(mightling_bin, vllm_host, prompt or DEFAULT_PROMPT, work_dir, session, app_bin)
            verdict = cls.judge(trace, allowed, replied)
            for line in cls.render(trace, verdict, allowed):
                print(line)
            for exception in cls.declared_exceptions():
                print(f"ℹ️  Declared exception: {exception}")
            if verdict.status == TRACE_FAILED:
                if tui and cls.tui_stage == "composer":
                    print("💡 The interface opened and took the prompt, but no reply was recorded.")
                if app:
                    print("💡 The app did not run its audit session to the end: start it by hand on this display to see why.")
                print(f"💡 The session needs the model server at {vllm_host}: `ling-admin server start`.")
            if write_json:
                print(f"💡 Full result: {cls.write_result(trace, verdict, allowed, cls.build_identity(mightling_bin), session)}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        return verdict.exit_code

    @classmethod
    def after_build(cls, vllm_host: Optional[str] = None, mightling_bin: Optional[str] = None) -> Optional[int]:
        """
        The audit `ling-admin codex build` runs once it has installed a new `ling` (§2): a
        Codex bump is when a new channel would appear, and nobody remembers to re-run a trace by
        hand. Both kinds of session are traced, `exec` and then the interface, and each result
        is written under `$CODEX_HOME/audit/`.

        It never fails the build: the binary is installed either way, and the verdict is what
        the user reads. Without a model server it does not wait for one; it says how to run the
        audit later.

        Args:
            vllm_host (Optional[str]): The model server; the configured one by default.
            mightling_bin (Optional[str]): The `ling` executable; the installed build by default.

        Returns:
            Optional[int]: The worst exit code of the sessions traced (0 pass, 1 unexpected
            destination, 2 trace failed), or None when the audit was skipped.
        """
        later = "run `ling-admin audit egress` and `ling-admin audit egress --tui` to check this build"
        try:
            if vllm_host is None:
                from dreamference.config import DreamferenceConfig
                vllm_host = DreamferenceConfig().vllm_host
            if shutil.which("strace") is None:
                print(f"💡 Egress audit skipped: strace is not installed (sudo apt-get install strace); then {later}.")
                return None
            from dreamference.night_shift import NightShiftHost
            if NightShiftHost.served_model(vllm_host, timeout=3.0) is None:
                print(f"💡 Egress audit skipped: the model server at {vllm_host} is not answering. "
                      f"After `ling-admin server start`, {later}.")
                return None
            print("🔎 Auditing what the new build does on the network...")
            codes = [cls.run(write_json=True, mightling_bin=mightling_bin, vllm_host=vllm_host)]
            if TuiSession.missing_modules():
                print("💡 The full-screen interface was not traced (pexpect and pyte are not installed): "
                      "`ling-admin audit egress --tui` says how to add them.")
            else:
                codes.append(cls.run(write_json=True, mightling_bin=mightling_bin, vllm_host=vllm_host, tui=True))
            worst = 1 if 1 in codes else max(codes)
            if worst == 1:
                print("❌ This build reaches something it should not: see the destinations above. "
                      "The build is installed; do not use it for private work until that is explained.")
            elif worst == 2:
                print(f"⚠️  The audit could not show what this build does; {later}.")
            return worst
        except Exception as error:  # An audit that breaks must not turn a good build into a failed one.
            print(f"⚠️  Egress audit did not run ({error}); {later}.")
            return None

    @classmethod
    def write_result(cls, trace: EgressTrace, verdict: EgressVerdict, allowed: Dict[int, str],
                     identity: Dict[str, Any], session: str = EXEC) -> str:
        """
        Writes the full result as JSON under `$CODEX_HOME/audit/`.

        Args:
            trace (EgressTrace): What the session did.
            verdict (EgressVerdict): The verdict.
            allowed (Dict[int, str]): The allowed loopback ports.
            identity (Dict[str, Any]): Which build was audited.
            session (str): `exec` or `tui`, the kind of session that was traced.

        Returns:
            str: The file written.
        """
        from dreamference.runner.codex_installer import CodexInstaller
        directory = os.path.join(CodexInstaller.home_dir(), "audit")
        os.makedirs(directory, exist_ok=True)
        now = datetime.datetime.now().astimezone()
        path = os.path.join(directory, f"{now:%Y%m%d-%H%M%S}-{session}.json")
        result = {
            "at": now.isoformat(timespec="seconds"),
            "session": session,
            "verdict": verdict.status,
            "problems": verdict.problems,
            "allowed_loopback_ports": {str(port): service for port, service in allowed.items()},
            **asdict(trace),
            **identity,
        }
        with open(path, "w") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        return path
