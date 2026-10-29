# ruff: noqa: B023, RUF005
"""Seeded random inputs for the hand-written readers and writers under `dreamference/node/`.

No `random` module and no extra dependency: a small xorshift generator and a fixed seed list, so
a failure repeats on every machine. `MIGHTLING_FUZZ_CASES` sets the cases per seed (default 600);
a long hunt is `MIGHTLING_FUZZ_CASES=200000`. The properties:

- the Avahi service file reads back exactly what `render` wrote, and `update` keeps the rest;
- a node cannot override, through its TXT records, the address a browse line gives it;
- the probe script runs a folder or image name as data, whatever characters it holds;
- the forced-command line in `authorized_keys` is one `command="..."` option as sshd reads it, and
  `authorize` and `unauthorize` touch only that key's line;
- `ssh-keygen` output, the `nodes` setting and the run arguments are read without raising.
"""

import os
import subprocess
from types import SimpleNamespace

import pytest

from dreamference.node import FleetProbe, NodeBrowser, NodeLanes, NodePairing
from dreamference.node.fleet_session import FleetSession
from dreamference.node.node_job_sender import NodeJobSender
from dreamference.node.node_pairing import KEY_COMMENT, KEY_RESTRICTIONS
from dreamference.node.node_serve import NodeServe
from dreamference.node.node_service_file import NodeServiceFile

SEEDS = (1, 2, 3, 0x9E3779B97F4A7C15, 0xDEADBEEFCAFEF00D)
CASES = int(os.environ.get("MIGHTLING_FUZZ_CASES", "600"))
MASK = (1 << 64) - 1

# Pieces that break hand-written readers: quotes, backslashes, `=`, `#`, `:`, line ends, a byte
# order mark, tabs, non-ASCII, shell metacharacters and plain words.
PIECES = (
    '"', "'", "\\", "=", "#", ":", ";", "\n", "\r\n", "\r", "﻿", "\t", " ", "  ", "é", "ß",
    "日本", "\U0001f600", "a", "b", "node", "-", "_", ".", "/", "[", "]", "{", "}", ",", "%", "0",
    "1", "&", "<", ">", "&amp;", "</txt-record>", "$", "`", "$(", ")", "*", "?", "~", "|", "!",
)  # fmt: skip
# The same without line ends, for values that sit on one line of output.
FLAT = tuple(piece for piece in PIECES if "\n" not in piece and "\r" not in piece)
HOSTILE = ("; touch PWN;", "$(touch PWN)", "`touch PWN`", "' ; touch PWN ; '", "&& touch PWN")


class XorShift64:
    """A tiny repeatable generator; the same seed gives the same cases everywhere."""

    def __init__(self, seed: int) -> None:
        self.state = seed & MASK or 1

    def next(self) -> int:
        x = self.state
        x ^= (x << 13) & MASK
        x ^= x >> 7
        x ^= (x << 17) & MASK
        self.state = x
        return x

    def below(self, bound: int) -> int:
        return self.next() % bound

    def chance(self, numerator: int, denominator: int) -> bool:
        return self.below(denominator) < numerator

    def pick(self, options):
        return options[self.below(len(options))]

    def text(self, longest: int = 10, pieces=PIECES) -> str:
        return "".join(self.pick(pieces) for _ in range(self.below(longest + 1)))


# -- the Avahi service file ----------------------------------------------------------------------


def printable(text: str) -> str:
    """Drop the characters XML cannot hold; the writer is for ids and versions, never binary."""
    return "".join(char for char in text if char in "\t\n\r" or char >= " ")


@pytest.mark.parametrize("seed", SEEDS)
def test_the_service_file_reads_back_what_it_was_rendered_with(seed):
    rng = XorShift64(seed)
    for _ in range(CASES):
        node = printable(rng.text())
        version = printable(rng.text())
        remote = printable(rng.text()) if rng.chance(1, 2) else None
        port = rng.below(65536)
        web = rng.below(65536) if rng.chance(1, 2) else None
        search = rng.below(65536) if rng.chance(1, 2) else None
        main = rng.chance(1, 2)
        state = rng.pick(("stopped", "loading", "ready"))
        text = NodeServiceFile.render(
            port, node, version, state=state, web_port=web, search_port=search, main=main,
            remote=remote,
            forge=(rng.pick(("record", "mirror")), rng.below(65536)) if rng.chance(1, 2) else None,
        )  # fmt: skip
        read = NodeServiceFile.parse(text)
        assert read is not None, (node, version, remote)
        assert read["port"] == str(port)
        assert read["node"] == node
        assert read["version"] == version
        assert read["state"] == state
        assert read.get("web") == (None if web is None else str(web))
        assert read.get("search") == (None if search is None else str(search))
        assert ("main" in read) == main
        assert read.get("remote") == (remote if remote else None)


@pytest.mark.parametrize("seed", SEEDS)
def test_updating_the_service_file_changes_only_what_was_asked(seed, tmp_path, monkeypatch):
    rng = XorShift64(seed)
    path = tmp_path / "mightling-node.service"
    monkeypatch.setattr(NodeServiceFile, "service_path", path)
    for _ in range(max(10, CASES // 20)):
        node = printable(rng.text()).replace("\r", "")
        version = printable(rng.text()).replace("\r", "")
        path.write_text(NodeServiceFile.render(8000, node, version, web_port=8080))
        state = rng.pick(("stopped", "loading", "ready"))
        assert NodeServiceFile.update(state=state) is True
        read = NodeServiceFile.read()
        assert read["state"] == state
        assert read["node"] == node
        assert read["version"] == version
        assert read["web"] == "8080"
        assert read["port"] == "8000"


@pytest.mark.parametrize("seed", SEEDS)
def test_arbitrary_text_is_never_a_crash_for_the_service_file_readers(seed):
    rng = XorShift64(seed)
    tags = ("<type>_mightling-node._tcp</type>", "<port>", "</port>", "<txt-record>", "<service>")
    for _ in range(CASES):
        text = "".join(
            rng.pick(tags) if rng.chance(1, 3) else rng.pick(PIECES) for _ in range(rng.below(16))
        )
        NodeServiceFile.parse(text)
        NodeServiceFile.parse_forge(text)
        NodeServiceFile.service_block(text, "_mightling-node._tcp")


# -- the browse output ---------------------------------------------------------------------------

STRUCTURAL = ("interface", "name", "host", "address", "port")


@pytest.mark.parametrize("seed", SEEDS)
def test_a_nodes_own_records_cannot_replace_what_the_browse_line_says(seed):
    rng = XorShift64(seed)
    plain = tuple(piece for piece in FLAT if piece not in ('"', ";", "\\"))
    for _ in range(CASES):
        address = f"10.{rng.below(256)}.{rng.below(256)}.{rng.below(256)}"
        name = f"node{rng.below(1000)}"
        host = f"{name}.local"
        port = str(rng.below(65536))
        keys = STRUCTURAL + ("node", "state", "version", "web", rng.text(3, plain))
        records = []
        for _ in range(rng.below(8)):
            records.append(f"{rng.pick(keys)}={rng.text(6, plain)}")
        txt = " ".join(f'"{record}"' for record in records)
        line = f"=;eth0;IPv4;{name};_mightling-node._tcp;local;{host};{address};{port};{txt}"
        found = NodeBrowser.parse(line)
        assert len(found) == 1, line
        entry = found[0]
        assert entry["address"] == address, line
        assert entry["host"] == host, line
        assert entry["port"] == port, line
        assert entry["name"] == name, line
        assert entry["interface"] == "eth0", line
        for key in ("state", "version", "web"):
            given = [r.partition("=")[2] for r in records if r.partition("=")[0] == key]
            if given:
                assert entry[key] == given[-1], line


@pytest.mark.parametrize("seed", SEEDS)
def test_arbitrary_browse_output_is_never_a_crash(seed):
    rng = XorShift64(seed)
    heads = ("=", "+", "=;eth0;IPv4;n;_mightling-node._tcp;local;h;1.2.3.4;80;")
    for _ in range(CASES):
        lines = []
        for _ in range(rng.below(5)):
            lines.append(rng.pick(heads) + ";".join(rng.text(4) for _ in range(rng.below(8))))
        for entry in NodeBrowser.parse("\n".join(lines)):
            assert {"interface", "name", "host", "address", "port"} <= set(entry)


# -- the probe script ----------------------------------------------------------------------------

NAME_PIECES = tuple(piece for piece in FLAT if piece not in ("\\", "'")) + HOSTILE


def probe_names(rng: XorShift64, count: int) -> list[str]:
    names = []
    for _ in range(count):
        name = rng.text(6, NAME_PIECES)
        names.append(rng.pick(HOSTILE) + name if rng.chance(1, 4) else name)
    return [name for name in names if name]


@pytest.mark.parametrize("seed", SEEDS)
def test_the_probe_script_runs_a_folder_or_image_name_as_data(seed, tmp_path):
    rng = XorShift64(seed)
    folders = probe_names(rng, CASES // 2)
    images = probe_names(rng, CASES // 2)
    body = FleetProbe.script(folders, images)
    generated = body.split("\n", 1)[1]
    definition = next(line for line in body.splitlines() if line.startswith("v()"))
    hub = tmp_path / "hub"
    hub.mkdir()
    script = (
        f"{definition}\nhub={hub}\ndocker() {{ return 1; }}\nsg() {{ return 1; }}\n"
        + "\n".join(line for line in generated.splitlines() if line.startswith(("[ -d", "( docker")))
    )  # fmt: skip
    (tmp_path / "probe.sh").write_text(script)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = subprocess.run(
        ["sh", str(tmp_path / "probe.sh")],
        cwd=run_dir,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert not (run_dir / "PWN").exists()
    expected = [f"model:{name}=0" for name in folders] + [f"image:{name}=0" for name in images]
    assert result.stdout.splitlines() == expected, result.stderr[:300]


@pytest.mark.parametrize("seed", SEEDS)
def test_the_probe_output_is_read_without_a_crash(seed):
    rng = XorShift64(seed)
    for _ in range(CASES):
        output = "\n".join(rng.text(8) for _ in range(rng.below(6)))
        state = FleetProbe.parse(output)
        for key, value in state.items():
            assert key and " " not in key
            assert value == value.strip()
        FleetProbe.is_gb10(state)
        FleetProbe.missing_models(state, ["models--a--b"])


# -- the forced command in authorized_keys -------------------------------------------------------


def sshd_options(line: str) -> tuple[str, str] | None:
    r"""Reads `command="..."` then the rest of the options as sshd does: `\"` is a quote."""
    if not line.startswith('command="'):
        return None
    command = []
    index = len('command="')
    while index < len(line):
        char = line[index]
        if char == "\\" and line[index + 1 : index + 2] == '"':
            command.append('"')
            index += 2
            continue
        if char == '"':
            return "".join(command), line[index + 1 :]
        command.append(char)
        index += 1
    return None


def public_key(rng: XorShift64) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    body = "".join(rng.pick(alphabet) for _ in range(8 + rng.below(40)))
    kind = rng.pick(("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "sk-ssh-ed25519@openssh.com"))
    return f"{kind} {body} {rng.text(3)}".replace("\n", " ")


@pytest.mark.parametrize("seed", SEEDS)
def test_the_forced_command_line_is_one_command_option_as_sshd_reads_it(seed):
    rng = XorShift64(seed)
    for _ in range(CASES):
        key = public_key(rng)
        command = "/opt/x/bin/ling-admin node serve-job --key " + rng.text(5)
        try:
            line = NodePairing.authorized_line(key, command)
        except ValueError:
            continue
        parsed = sshd_options(line)
        assert parsed is not None, line
        assert parsed[0] == command, line
        assert parsed[1].startswith(f",{KEY_RESTRICTIONS} "), line
        assert parsed[1].endswith(f" {KEY_COMMENT}"), line
        assert "\n" not in line


@pytest.mark.parametrize("seed", SEEDS)
def test_authorize_and_unauthorize_touch_only_that_keys_line(seed):
    rng = XorShift64(seed)
    authorized = NodeServe.authorized_keys()
    for _ in range(max(10, CASES // 30)):
        authorized.parent.mkdir(parents=True, exist_ok=True)
        foreign = [rng.text(12, FLAT) for _ in range(rng.below(4))]
        foreign = [line for line in foreign if not line.startswith("#") and "--key" not in line]
        authorized.write_text("".join(f"{line}\n" for line in foreign))
        first, second = public_key(rng), public_key(rng)
        if first.split()[1] == second.split()[1]:
            continue
        assert NodeServe.authorize(first) is True
        assert NodeServe.authorize(first) is True
        assert NodeServe.authorize(second) is True
        lines = authorized.read_text().splitlines()
        assert lines[: len(foreign)] == foreign
        assert len(lines) == len(foreign) + 2
        tag = NodeServe.key_tag(first)
        assert NodeServe.unauthorize(tag) is True
        left = authorized.read_text().splitlines()
        assert len(left) == len(foreign) + 1
        assert f"--key {tag}" not in authorized.read_text()
        assert f"--key {NodeServe.key_tag(second)}" in left[-1]
        assert NodeServe.unauthorize(tag) is False


# -- other readers -------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_host_key_lines_reads_any_ssh_keygen_output(seed, tmp_path, monkeypatch):
    rng = XorShift64(seed)
    session = FleetSession.__new__(FleetSession)
    session.host, session.ssh_port, session.known_hosts = "h", 22, tmp_path / "known_hosts"
    for _ in range(CASES):
        out = "\n".join(
            rng.pick(("# Host h found: line 1", "h ssh-ed25519 AAAA", "h", rng.text(8)))
            for _ in range(rng.below(6))
        )
        monkeypatch.setattr(
            FleetSession, "_spawn", lambda *args, **kwargs: SimpleNamespace(stdout=out, stderr="")
        )
        for key in session.host_key_lines():
            assert len(key.split(" ")) == 2


@pytest.mark.parametrize("seed", SEEDS)
def test_the_nodes_setting_and_the_url_host_are_read_without_a_crash(seed):
    rng = XorShift64(seed)
    for _ in range(CASES):
        setting = rng.pick((None, True, False, 0, 3, rng.text(8), [rng.text(3), rng.text(3)]))
        names = NodeLanes.wanted(setting)
        assert names is None or all(name == name.strip() and name for name in names)
        address = rng.text(8)
        host = NodeLanes.url_host(address)
        assert host == address or host == f"[{address}]"


@pytest.mark.parametrize("seed", SEEDS)
def test_the_run_arguments_split_into_options_and_the_jobs_command(seed):
    rng = XorShift64(seed)
    options = ("--memory", "--time", "--test", "--setup", "--out", "--bind")
    plain = tuple(piece for piece in FLAT if not piece.startswith("-"))
    for _ in range(CASES):
        own = []
        for _ in range(rng.below(4)):
            own += [rng.pick(options), "v" + rng.text(3, plain)]
        command = [f"c{rng.text(3, plain)}" for _ in range(rng.below(4))]
        if rng.chance(1, 2):
            command = ["--" + rng.text(3, plain)] + command
            words = own + ["--"] + command
        else:
            words = own + command
        found, rest = NodeJobSender.split_run_arguments(words)
        assert rest == command, words
        assert isinstance(found.bind, list)
