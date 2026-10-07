"""Every `mling` TUI slash command, driven through a real terminal against the running model server,
and the command-line subcommands Mightling changes.

The slash-command tests are live. They skip unless the model server answers at the configured vLLM
URL and `mling` has been built (`mling-admin codex build`), so the ordinary suite stays offline.
The subcommand tests need only the built binary, since the launcher answers them before it looks
for a server. With the server up, run the file on its own; the tests that make the model work take
minutes:

    .venv/bin/python -m pytest tests/test_mightling_slash_commands.py -v

Each command runs in a fresh session: `mling` on a pseudo-terminal, rendered by pyte, in a
throwaway git repository with a throwaway CODEX_HOME, so nothing touches the user's own sessions
or config. The list of commands is read from the pinned Codex source, and every one of them must
appear in CASES below. A command added by a submodule bump therefore fails here until someone
decides how to drive it.

Nothing here confirms an action with outside effects: pop-ups are closed with Esc, and neither
`mling update` (which would replace the installed binaries) nor `mling app` (which opens a
desktop window) is run.
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pytest

import requests

try:
    import pexpect
    import pyte
except ImportError:  # the subcommand tests do not need a terminal
    pexpect = pyte = None

from dreamference.config import DreamferenceConfig  # noqa: E402
from dreamference.runner.codex_branded_builder import (  # noqa: E402
    CODEX_SUBMODULE_DIR,
    CodexBrandedBuilder,
)

ROWS, COLS = 60, 200
SLASH_SOURCE = Path(CODEX_SUBMODULE_DIR) / "codex-rs" / "tui" / "src" / "slash_command.rs"
# MIGHTLING_BIN points the tests at another build, e.g. one not yet installed.
MIGHTLING = os.environ.get("MIGHTLING_BIN") or CodexBrandedBuilder.executable_path()
VLLM_HOST = (os.environ.get("DREAMFERENCE_VLLM_HOST") or DreamferenceConfig().vllm_host).rstrip("/")

# Text that means something broke, whatever the command was.
FAILURE_MARKERS = (
    "panicked",
    "Unrecognized command",
    "stream disconnected",
    "Connection failed",
    "unexpected status",
    "Error loading configuration",
    # A command that refuses to run ("'/side' is unavailable until the current conversation has
    # started") did nothing, whatever else is on screen.
    "is unavailable until",
)
# Shown only while a turn is running.
BUSY_MARKER = "esc to interrupt"
# The prompt used wherever a command needs a conversation to act on.
PING = "Reply with exactly one word: pong"


def _served_model() -> Optional[str]:
    try:
        response = requests.get(f"{VLLM_HOST}/v1/models", timeout=3)
        return response.json()["data"][0]["id"] if response.ok else None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


SERVED_MODEL = _served_model()

needs_source = pytest.mark.skipif(not SLASH_SOURCE.is_file(), reason="codex submodule not checked out")
needs_mightling = pytest.mark.skipif(not os.access(MIGHTLING, os.X_OK), reason=f"{MIGHTLING} is not built; run `mling-admin codex build`")
needs_server = pytest.mark.skipif(SERVED_MODEL is None, reason=f"no model server answering at {VLLM_HOST}/v1/models")
needs_terminal = pytest.mark.skipif(pexpect is None, reason="pexpect and pyte are not installed")


def live(test: Callable) -> Callable:
    """Marks a test that drives `mling` on a terminal against the running model server."""
    for mark in (needs_source, needs_mightling, needs_server, needs_terminal):
        test = mark(test)
    return test


def slash_commands() -> Dict[str, str]:
    """
    Reads the slash commands from the pinned source, as the TUI names them.

    Returns:
        Dict[str, str]: Command name -> enum variant, in the order the popup lists them.
    """
    if not SLASH_SOURCE.is_file():
        return {}
    body = SLASH_SOURCE.read_text().split("pub enum SlashCommand {", 1)[1].split("\n}", 1)[0]
    commands: Dict[str, str] = {}
    pending: Optional[str] = None
    for line in body.splitlines():
        line = line.strip()
        attr = re.match(r'#\[strum\((?:to_string = "([^"]+)")?(?:, )?(?:serialize = "([^"]+)")?\)\]', line)
        if attr:
            pending = attr.group(1) or attr.group(2)
            continue
        variant = re.match(r"([A-Z]\w*),$", line)
        if variant:
            name = pending or re.sub(r"(?<!^)(?=[A-Z])", "-", variant.group(1)).lower()
            commands[name] = variant.group(1)
            pending = None
    return commands


@dataclass(frozen=True)
class Case:
    """
    How to drive one command and what it must produce.

    Attributes:
        mode: "popup" (opens a view, dismissed with Esc), "inline" (prints into the transcript),
            "turn" (makes the model work), "exit" (ends the session), "absent" (hidden by a Mightling
            patch or not offered on this platform/build, so the TUI must say it is unrecognised)
            or "skip".
        args: Inline arguments typed after the command.
        expect: Text the screen must show afterwards; SERVED_MODEL and WORKSPACE are substituted.
        history: Hold one exchange with the model first, for commands that act on a conversation.
        keys: Keystrokes sent after the command, e.g. to choose an entry in the popup it opens.
        check: A further assertion on (session, workspace) once the command has finished.
        reason: Why a command is skipped or absent.
    """

    mode: str
    args: str = ""
    expect: Tuple[str, ...] = ()
    history: bool = False
    keys: str = ""
    check: Optional[Callable[["Session", Path], None]] = None
    reason: str = ""


def _agents_md_written(session: "Session", workspace: Path) -> None:
    # The local model sometimes ends /init's turn on "I'll create a concise AGENTS.md..." without
    # the tool call (seen twice in the TUI on 2026-09-28/29; 6 of 6 `mling exec` runs of the same
    # prompt wrote it, with or without an extra "act, don't announce" instruction). A user would say
    # "go ahead", so the test does too, once; it fails only if the file still does not appear.
    if not (workspace / "AGENTS.md").is_file():
        session.command("Go ahead and write AGENTS.md now.")
        session.wait_idle(timeout=300)
    assert (workspace / "AGENTS.md").is_file(), f"/init finished without writing AGENTS.md:\n{session.text()}"


def _cd_moved(session: "Session", workspace: Path) -> None:
    session.command("/pwd")
    assert str(workspace / "sub") in session.text()


CASES: Dict[str, Case] = {
    "model": Case("popup", expect=("SERVED_MODEL",)),
    "ide": Case("inline"),
    "permissions": Case("popup"),
    "keymap": Case("popup"),
    "vim": Case("inline"),
    "setup-default-sandbox": Case("absent", reason="sets up the elevated sandbox, a Windows feature"),
    "experimental": Case("popup"),
    # SlashCommand::AutoReview: retries an approval review, which defaults to OpenAI's
    # codex-auto-review model.
    "approve": Case("absent", reason="hidden by patch 0012: auto-review defaults to an OpenAI model"),
    "memories": Case("popup"),
    "skills": Case("popup"),
    "import": Case("popup"),
    "hooks": Case("popup"),
    # Opens a picker of what to review; the first entry is the uncommitted changes in a.txt.
    "review": Case("turn", keys="\r"),
    "rename": Case("inline", args="slash-command test", history=True),
    "new": Case("inline", history=True),
    "archive": Case("popup", history=True),
    "delete": Case("popup", history=True),
    "resume": Case("popup", history=True),
    "fork": Case("inline", history=True),
    "worktree": Case("popup"),
    "app": Case("absent", reason="opens the desktop app, compiled only for macOS and Windows"),
    "init": Case("turn", check=_agents_md_written),
    "compact": Case("turn", history=True),
    "recap": Case("turn", history=True),
    "plan": Case("inline", expect=("Plan",)),
    "voice": Case("absent", reason="hidden by patch 0010: voice uses OpenAI's realtime API"),
    "goal": Case("inline", args="Keep every answer to one word"),
    "agents": Case("popup"),
    "side": Case("turn", args="What is two plus two? Answer with digits only.", expect=("4",), history=True),
    # An alias of /side, which is unavailable until the conversation has started.
    "btw": Case("turn", args="What is three plus three? Answer with digits only.", expect=("6",), history=True),
    "copy": Case("inline", history=True),
    "export": Case("inline", history=True),
    "raw": Case("inline"),
    "tui": Case("popup"),
    "diff": Case("inline", expect=("a.txt",)),
    "mention": Case("popup"),
    "status": Case("inline", expect=("Mightling", "SERVED_MODEL", "openai-custom")),
    "daemon": Case("popup"),
    "warnings": Case("popup"),
    "cd": Case("inline", args="sub", check=_cd_moved),
    "pwd": Case("inline", expect=("WORKSPACE",)),
    # Patch 0011: token statistics for the session instead of ChatGPT plan limits.
    "usage": Case("inline", history=True, expect=("Token usage this session", "Total")),
    "debug-config": Case("inline"),
    "title": Case("popup"),
    "statusline": Case("popup"),
    "theme": Case("popup"),
    "pets": Case("absent", reason="hidden by patch 0010: pet art is downloaded from OpenAI's CDN"),
    "mcp": Case("inline", expect=("MCP",)),
    "apps": Case("absent", reason="apps are OpenAI-hosted connectors that need a ChatGPT login"),
    "plugins": Case("popup"),
    "logout": Case("absent", reason="hidden by patch 0005: there is no account to sign out of"),
    "quit": Case("exit"),
    "exit": Case("exit"),
    "feedback": Case("absent", reason="removed by patch 0009: it uploads session logs to OpenAI"),
    "rollout": Case("absent", reason="debug builds only"),
    "ps": Case("inline"),
    "stop": Case("inline"),
    "clear": Case("inline", history=True),
    "test-approval": Case("absent", reason="debug builds only"),
    "subagents": Case("popup"),
    "debug-m-drop": Case("skip", reason='described upstream as "DO NOT USE": drops the memory store'),
    "debug-m-update": Case("skip", reason='described upstream as "DO NOT USE"'),
}

# Commands the patch series adds. The pinned source does not list them, so until 2026-10-02 no
# live test typed them: each was checked once by hand in tmux when it was built. All three answer
# from the launcher without a model turn; `/night` reads the throwaway CODEX_HOME's empty queue.
MIGHTLING_CASES: Dict[str, Case] = {
    "cavemode": Case("inline", expect=("Cave mode:",)),
    "night": Case("inline", expect=("No Night Shift tasks for this repository",)),
    "airgapped": Case("inline", expect=("Airgapped:", "← in force")),
}
CASES.update(MIGHTLING_CASES)


def patched_slash_commands() -> Dict[str, str]:
    """
    Reads the slash commands the patch series adds to the `SlashCommand` enum.

    Returns:
        Dict[str, str]: Command name -> the patch that adds its variant.
    """
    from dreamference.runner.codex_branded_builder import CodexBrandedBuilder

    added: Dict[str, str] = {}
    for patch in CodexBrandedBuilder.patches():
        in_enum_file = False
        for line in Path(patch).read_text().splitlines():
            if line.startswith("diff --git"):
                in_enum_file = line.endswith("tui/src/slash_command.rs")
            variant = re.match(r"\+    ([A-Z]\w*),$", line)
            if in_enum_file and variant:
                added[re.sub(r"(?<!^)(?=[A-Z])", "-", variant.group(1)).lower()] = os.path.basename(patch)
    return added


class Session:
    """
    One `mling` process on a pseudo-terminal, with its screen rendered by pyte.
    """

    def __init__(self, workspace: Path, codex_home: Path, args: List[str]):
        self.screen = pyte.Screen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        env = dict(os.environ, CODEX_HOME=str(codex_home), TERM="xterm-256color", DREAMFERENCE_VLLM_HOST=VLLM_HOST)
        self.child = pexpect.spawn(MIGHTLING, args, cwd=str(workspace), env=env, dimensions=(ROWS, COLS))

    def pump(self, seconds: float) -> bool:
        """Renders output for `seconds`; False once the process has exited."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                self.stream.feed(self.child.read_nonblocking(65536, timeout=0.2))
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                return False
        return True

    def text(self) -> str:
        return "\n".join(line.rstrip() for line in self.screen.display)

    def wait_for(self, predicate: Callable[[str], bool], timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            alive = self.pump(0.5)
            if predicate(self.text()):
                return True
            if not alive:
                return False
        return False

    def wait_ready(self) -> None:
        # The launcher may first wait for the server, and a cold compile of the prompt takes time.
        ready = self.wait_for(lambda text: ">_ Mightling" in text and "›" in text, timeout=120)
        assert ready, f"mling never reached its composer:\n{self.text()}"
        assert "Sign in with ChatGPT" not in self.text()
        # Which commands are offered depends on feature flags that load just after the composer
        # appears; typing sooner races them.
        self.pump(3.0)

    def command(self, line: str) -> None:
        """Types a line, lets the command popup settle, and submits it."""
        self.child.send(line)
        self.pump(1.0)
        self.child.send("\r")
        self.pump(2.0)

    def wait_idle(self, timeout: float, settle: float = 3.0,
                  started: Optional[Callable[[str], bool]] = None) -> None:
        """
        Waits until a model turn has started and finished.

        The busy marker also disappears for a moment between one tool call and the next, so a
        single check that it is gone ended /init's turn early; the test then found no AGENTS.md
        and its Ctrl-C cleanup interrupted the model. The turn counts as finished only once the
        marker has stayed away for `settle` seconds.

        `started` also accepts a turn whose result is already on screen: a fast model can finish
        a short turn between two screen reads, the marker is never seen, and waiting 30 s for it
        added about six minutes to the suite on Qwen3.8.
        """
        # The marker is drawn by the client when the turn begins, not when the model answers, so
        # not seeing it within a few seconds means the turn already ended, not that it is late.
        self.wait_for(lambda text: BUSY_MARKER in text or (started is not None and started(text)), timeout=8)
        end = time.monotonic() + timeout
        quiet_since: Optional[float] = None
        while time.monotonic() < end:
            alive = self.pump(0.5)
            if BUSY_MARKER in self.text():
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= settle:
                return
            if not alive:
                return
        raise AssertionError(f"the turn did not finish within {timeout}s:\n{self.text()}")

    def converse(self) -> None:
        """Holds one exchange with the model, so conversation-level commands have something to act on."""
        self.command(PING)
        # The prompt itself, echoed on screen, contains "pong" once; the answer is a second one.
        answered = lambda text: text.lower().count("pong") >= 2  # noqa: E731
        self.wait_idle(timeout=300, started=answered)
        assert answered(self.text()), f"the model did not answer:\n{self.text()}"

    def close(self) -> None:
        if self.child.isalive():
            self.child.sendcontrol("c")
            self.pump(0.5)
            self.child.sendcontrol("c")
            self.pump(1.0)
        self.child.terminate(force=True)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A git repository with one uncommitted change, so /diff and /review have something to show."""
    repo = tmp_path / "ws"
    (repo / "sub").mkdir(parents=True)
    (repo / "a.txt").write_text("a\n")
    (repo / "sub" / "b.txt").write_text("b\n")
    git = ["git", "-c", "user.name=mightling-test", "-c", "user.email=mling@test"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "a.txt").write_text("a\nchanged\n")
    return repo


@pytest.fixture
def codex_home(tmp_path: Path, workspace: Path) -> Path:
    """
    A CODEX_HOME that already trusts the workspace and names the local provider.

    Both are pre-seeded so every test reaches the composer. `model_provider` is also what keeps the
    ChatGPT sign-in screen away; test_a_fresh_home_opens_on_the_composer checks that the launcher
    handles that on its own.
    """
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        'model_provider = "openai-custom"\n\n'
        f'[projects."{workspace}"]\ntrust_level = "trusted"\n'
    )
    return home


def _run(name: str, case: Case, workspace: Path, codex_home: Path) -> None:
    # `-a never`: an approval prompt in the middle of a model turn would stall the test.
    session = Session(workspace, codex_home, ["-a", "never", "-s", "workspace-write"])
    try:
        session.wait_ready()
        if case.history:
            session.converse()
        if case.mode == "absent":
            # With a trailing space the completion popup closes and the name is sent as typed;
            # otherwise Enter picks the closest command offered (`/app` would run `/approve`).
            session.command(f"/{name} ")
        else:
            session.command(f"/{name} {case.args}".rstrip())
        if case.keys:
            session.child.send(case.keys)
            session.pump(2.0)

        if case.mode == "exit":
            ended = session.wait_for(lambda text: False, timeout=10) or not session.child.isalive()
            assert ended, f"/{name} did not end the session:\n{session.text()}"
            return
        if case.mode == "absent":
            assert "Unrecognized command" in session.text(), f"/{name} is offered, but should not be here ({case.reason})"
            return
        if case.mode == "turn":
            session.wait_idle(timeout=600)

        screen = session.text()
        assert session.child.isalive(), f"mling exited after /{name}:\n{screen}"
        for marker in FAILURE_MARKERS:
            assert marker not in screen, f"/{name} produced {marker!r}:\n{screen}"
        for wanted in case.expect:
            wanted = wanted.replace("SERVED_MODEL", SERVED_MODEL or "").replace("WORKSPACE", str(workspace))
            assert wanted in screen, f"/{name} did not show {wanted!r}:\n{screen}"
        if case.mode == "popup":
            session.child.send("\x1b")
            assert session.pump(1.0), f"mling exited when /{name} was dismissed"
        if case.check:
            case.check(session, workspace)
    finally:
        session.close()


@needs_source
def test_every_slash_command_has_a_case():
    commands = slash_commands()
    assert commands, "could not read any slash commands from the Codex source"
    missing = sorted(set(commands) - set(CASES))
    stale = sorted(set(CASES) - set(commands) - set(MIGHTLING_CASES))
    assert not missing, f"new slash commands with no test case: {missing}"
    assert not stale, f"test cases for commands that no longer exist: {stale}"


def test_every_command_a_patch_adds_has_a_case():
    # The other half of the check above: a command added by a patch is invisible to the pinned
    # source, which is how /cavemode, /night and /airgapped went without a live test.
    added = patched_slash_commands()
    assert added, "no patch adds a slash command any more; MIGHTLING_CASES should be empty then"
    missing = {name: patch for name, patch in added.items() if name not in MIGHTLING_CASES}
    stale = sorted(set(MIGHTLING_CASES) - set(added))
    assert not missing, f"slash commands added by a patch with no live test case: {missing}"
    assert not stale, f"live test cases for commands no patch adds: {stale}"


@live
@pytest.mark.parametrize("name", list(CASES))
def test_slash_command(name: str, workspace: Path, codex_home: Path):
    case = CASES[name]
    if case.mode == "skip":
        pytest.skip(case.reason)
    _run(name, case, workspace, codex_home)


@live
def test_a_fresh_home_opens_on_the_composer(tmp_path: Path, workspace: Path):
    # No pre-seeded provider: whatever the launcher writes must be enough to skip the ChatGPT
    # sign-in screen, which is what a new user of `mling` would otherwise land on.
    home = tmp_path / "fresh-home"
    home.mkdir()
    (home / "config.toml").write_text(f'[projects."{workspace}"]\ntrust_level = "trusted"\n')
    session = Session(workspace, home, [])
    try:
        reached = session.wait_for(
            lambda text: ("›" in text and ">_ Mightling" in text) or "Sign in with ChatGPT" in text, 120
        )
        assert reached, f"mling showed neither its composer nor a sign-in screen:\n{session.text()}"
        # The first screen to match is not the answer: onboarding can replace the composer a moment
        # later, and a check at that instant passed while a new user still landed on the sign-in.
        session.pump(5.0)
        screen = session.text()
        assert "Sign in with ChatGPT" not in screen, f"a fresh CODEX_HOME lands on the ChatGPT sign-in screen:\n{screen}"
        assert ">_ Mightling" in screen and "›" in screen, f"mling is not on its composer:\n{screen}"
    finally:
        session.close()


# Command-line subcommands. The launcher answers these before it looks for a model server, so they
# need only the built binary.

HIDDEN_SUBCOMMANDS = ("cloud", "login", "logout", "remote-control")
REFUSED_SUBCOMMANDS = ("cloud", "cloud-tasks", "login", "logout")


def _mightling(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, CODEX_HOME=str(home), DREAMFERENCE_VLLM_HOST="http://127.0.0.1:9")
    return subprocess.run(
        [MIGHTLING, *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
    )


@needs_mightling
def test_version_names_mightling(tmp_path: Path):
    result = _mightling(tmp_path, "--version")
    assert result.returncode == 0 and result.stdout.startswith("mling "), result.stdout + result.stderr


@needs_mightling
def test_help_lists_only_what_mightling_offers(tmp_path: Path):
    result = _mightling(tmp_path, "--help")
    assert result.returncode == 0, result.stderr
    listed = {line.split()[0] for line in result.stdout.splitlines() if line.startswith("  ") and line.split()}
    assert {"exec", "resume", "update"} <= listed
    assert not listed & set(HIDDEN_SUBCOMMANDS), f"hidden subcommands are listed: {sorted(listed & set(HIDDEN_SUBCOMMANDS))}"


@needs_mightling
@pytest.mark.parametrize("name", REFUSED_SUBCOMMANDS)
def test_openai_hosted_subcommands_are_refused(tmp_path: Path, name: str):
    # Refused by the launcher before Codex parses anything, so no sign-in or cloud request starts.
    result = _mightling(tmp_path, name)
    assert result.returncode != 0
    assert f"`mling {name}` is not available" in result.stderr, result.stdout + result.stderr


@needs_mightling
@pytest.mark.parametrize("args", [("-c", "x=1", "login"), ("--oss", "cloud"), ("-cx=1", "logout")])
def test_refusal_is_not_bypassed_by_options_before_the_subcommand(tmp_path: Path, args: Tuple[str, ...]):
    # The launcher once checked only the first argument, so any option in front of `login` let
    # the OpenAI sign-in start.
    result = _mightling(tmp_path, *args)
    assert result.returncode != 0
    assert f"`mling {args[-1]}` is not available" in result.stderr, result.stdout + result.stderr


@needs_mightling
def test_offline_subcommands_do_not_wait_for_the_model_after_options(tmp_path: Path):
    # With the server unreachable, a command that needs no model must answer at once rather than
    # wait (up to ten minutes) for one; _mightling's 30 s timeout turns a wait into a failure.
    result = _mightling(tmp_path, "-m", "any-model", "completion", "bash")
    assert result.returncode == 0, result.stderr
    assert "mling" in result.stdout and "Waiting for local vLLM" not in result.stderr


@needs_mightling
@pytest.mark.parametrize("subcommand", ["exec", "plugin", "mcp"])
def test_subcommand_usage_names_mightling(tmp_path: Path, subcommand: str):
    result = _mightling(tmp_path, subcommand, "--help")
    usage = next((line for line in result.stdout.splitlines() if line.startswith("Usage:")), "")
    assert usage.startswith(f"Usage: mling {subcommand}"), result.stdout + result.stderr
