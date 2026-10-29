# ruff: noqa: B023, RUF001
"""Seeded random inputs for the hand-written readers and writers under `dreamference/remote/`.

No `random` module and no extra dependency: a small xorshift generator and a fixed seed list, so
a failure repeats on every machine. `MIGHTLING_FUZZ_CASES` sets the cases per seed (default 600);
a long hunt is `MIGHTLING_FUZZ_CASES=200000`. The properties:

- every value the join script quotes reaches a real bash as exactly the text it was given, even one
  that holds another placeholder, a quote or a command substitution, and the one-line form decodes
  to the script it wraps;
- a name the box or the relay may be given is an ASCII host name, and only such a name reaches the
  pinned `known_hosts` line, which is then three fields;
- a key lifetime, an SSH destination, a CA scope, the box's facts and an `openssl` date are read
  without raising and agree with a plain reference;
- the tunnel unit's `ExecStart=` is read back by systemd's word rules as the arguments it was
  built from, whatever the key's and `known_hosts` file's path holds.
"""

import base64
import contextlib
import io
import os
import re
import subprocess

import pytest

from dreamference.remote import (
    RemoteAccess,
    RemoteBox,
    RemoteJoinScript,
    RemotePublicCertificate,
    RemoteRelayCertificate,
    RemoteSettings,
    RemoteTunnel,
)
from dreamference.remote.remote_settings import SETUP_KEY_MAX_MINUTES

SEEDS = (1, 2, 3, 0x9E3779B97F4A7C15, 0xDEADBEEFCAFEF00D)
CASES = int(os.environ.get("MIGHTLING_FUZZ_CASES", "600"))
MASK = (1 << 64) - 1

PLACEHOLDERS = ("@MGMT@", "@KEY@", "@VERSION@", "@PEER@", "@NODE_NAME@", "@SSH_USER@", "@CA@")
# Pieces that break hand-written readers: quotes, backslashes, `=`, `#`, `:`, line ends, a byte
# order mark, tabs, non-ASCII, shell metacharacters, placeholders and plain words.
PIECES = (
    '"', "'", "\\", "=", "#", ":", ";", "\n", "\r\n", "\r", "﻿", "\t", " ", "  ", "é", "ß",
    "日本", "\U0001f600", "a", "b", "key", "-", "_", ".", "/", "[", "]", "{", "}", ",", "%", "0",
    "1", "&", "<", ">", "$", "`", "$(", ")", "*", "?", "~", "|", "!", "; touch PWN;", "$(touch PWN)",
    "' ; touch PWN ; '", *PLACEHOLDERS, "@ARCH@", "@",
)  # fmt: skip
# The same without the characters `splitlines` and a file's lines treat as a line end.
FLAT = tuple(piece for piece in PIECES if "\n" not in piece and "\r" not in piece)


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

    def text(self, longest: int = 8, pieces=PIECES) -> str:
        return "".join(self.pick(pieces) for _ in range(self.below(longest + 1)))


# -- the join script -----------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_every_join_script_value_reaches_bash_as_the_text_it_was_given(seed, tmp_path):
    rng = XorShift64(seed)
    cases = []
    for _ in range(min(2000, max(10, CASES // 4))):
        bundle = {
            "management_url": rng.text(),
            "setup_key": rng.text(),
            "overlay_name": rng.text(),
            "netbird_version": rng.text() if rng.chance(1, 2) else "",
            "ca": rng.text() if rng.chance(1, 2) else "",
        }
        cases.append((bundle, rng.text(), rng.text()))
    blocks = []
    for index, (bundle, peer, ssh_user) in enumerate(cases):
        script = RemoteJoinScript.script(bundle, peer=peer, ssh_user=ssh_user)
        start = script.index("MGMT=")
        assignments = script[start : script.index('case "$(uname -m)"')]
        ca = ""
        if bundle["ca"]:
            at = script.index("printf '%s\\n' ", script.index("The node's own CA"))
            ca = script[at : script.index("\nstore=", at)]
        blocks.append(
            f'(\n{assignments}\nwork={tmp_path}/w{index}; mkdir -p "$work"\n{ca or ":"}\n'
            'printf "%s\\0" "$MGMT" "$KEY" "$VERSION" "$PEER" "$NODE_NAME" "$SSH_USER"\n)\n'
        )
    program = tmp_path / "join.sh"
    program.write_text("".join(blocks))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    done = subprocess.run(
        ["bash", str(program)], cwd=run_dir, capture_output=True, timeout=120, check=False
    )
    assert not (run_dir / "PWN").exists()
    seen = done.stdout.decode().split("\0")[:-1]
    expected = []
    for bundle, peer, ssh_user in cases:
        expected += [bundle["management_url"], bundle["setup_key"]]
        expected += [
            bundle["netbird_version"] or None,
            peer,
            bundle["overlay_name"],
            ssh_user,
        ]
    for position, (want, got) in enumerate(zip(expected, seen, strict=False)):
        if want is None:  # the pinned version stands in for an empty one
            assert got, position
        else:
            assert got == want, (position // 6, position % 6, done.stderr[:200])
    assert len(seen) == len(expected), done.stderr[:300]
    for index, (bundle, _, _) in enumerate(cases):
        if bundle["ca"]:
            written = (tmp_path / f"w{index}" / "ca.crt").read_bytes()
            assert written == (bundle["ca"] + "\n").encode(), index


@pytest.mark.parametrize("seed", SEEDS)
def test_the_one_line_form_decodes_to_the_script_it_wraps(seed):
    rng = XorShift64(seed)
    for _ in range(CASES):
        script = rng.text(20)
        line = RemoteJoinScript.one_line(script)
        found = re.fullmatch(r" printf %s '([A-Za-z0-9+/=]*)' \| base64 -d \| sudo bash -s", line)
        assert found, line
        decoded = base64.b64decode(found.group(1)).decode()
        assert decoded == f"join_main() {{\n{script.rstrip()}\n}}\njoin_main\n"


# -- names, destinations, lifetimes ---------------------------------------------------------------

LABELS = (
    "relay",
    "box",
    "a-b",
    "x1",
    "-x",
    "x-",
    "é",
    "١٢",
    "a_b",
    "",
    "*",
    "a b",
    "A",
    "9",
    "a" * 64,
)


def dns_name(rng: XorShift64) -> str:
    return ".".join(rng.pick(LABELS) for _ in range(1 + rng.below(4)))


def ascii_host_name(name: str) -> bool:
    labels = name.split(".")
    return len(name) <= 253 and all(
        re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels
    )  # fmt: skip


@pytest.mark.parametrize("seed", SEEDS)
def test_a_relay_name_that_is_accepted_is_an_ascii_host_name(seed):
    rng = XorShift64(seed)
    accepted = 0
    for _ in range(CASES):
        name = dns_name(rng)
        if RemoteRelayCertificate.valid_name(name, "box.example.org"):
            accepted += 1
            assert ascii_host_name(name), name
            assert len(name.split(".")) >= 3
            assert name != "box.example.org"
    assert accepted > 0


@pytest.mark.parametrize("seed", SEEDS)
def test_only_an_ascii_host_name_gets_past_setup_to_the_known_hosts_line(seed, monkeypatch):
    rng = XorShift64(seed)
    monkeypatch.setattr(RemoteAccess, "_node_id", classmethod(lambda cls: None))
    passed = 0
    for _ in range(max(10, CASES // 3)):
        name = dns_name(rng)
        if rng.chance(1, 4):
            name = rng.text(4, FLAT) + name
        with contextlib.redirect_stdout(io.StringIO()):
            _, status = RemoteAccess._setup_inputs(name, None, None)
        if status != 1:  # 1 is "not a node", reached only once the name was accepted
            continue
        passed += 1
        cleaned = name.strip().lower().rstrip(".")
        assert ascii_host_name(cleaned) and "." in cleaned, name
        RemoteTunnel.write_known_host(cleaned, "ssh-ed25519 AAAAC3 comment")
        written = RemoteSettings.path("known_hosts").read_text()
        assert written == f"{cleaned} ssh-ed25519 AAAAC3\n"
    assert passed > 0


@pytest.mark.parametrize("seed", SEEDS)
def test_a_ca_scope_holds_the_name_it_was_made_for_and_nothing_that_merely_ends_alike(seed):
    rng = XorShift64(seed)
    for _ in range(CASES):
        name = rng.text(8, FLAT)
        scope = RemoteSettings.default_ca_scope(name)
        if ascii_host_name(name.strip(".").lower()) and name.strip("."):
            assert RemoteSettings.in_scope(name, scope), (name, scope)
        other = rng.text(6, FLAT)
        if RemoteSettings.in_scope(other, scope):
            assert other.lower().strip(".") == scope.lower().strip(".") or (
                other.lower().strip(".").endswith("." + scope.lower().strip("."))
            )
        base = "".join(piece for piece in rng.text(4, ("a", "b", "-", "x")) or "a")
        assert RemoteSettings.in_scope("evil" + base + ".org", base + ".org") is False


def reference_ttl(text: str) -> int | None:
    word = text.strip().lower()
    unit = {"m": 1, "h": 60, "d": 1440}
    digits, suffix = (word[:-1], word[-1]) if word[-1:] in unit else (word, "m")
    if not (digits.isascii() and digits.isdigit() and len(digits) <= 4):
        return None
    minutes = int(digits) * unit[suffix]
    return minutes if 1 <= minutes <= SETUP_KEY_MAX_MINUTES else None


@pytest.mark.parametrize("seed", SEEDS)
def test_a_key_lifetime_is_read_as_a_plain_reference_reads_it(seed):
    rng = XorShift64(seed)
    pieces = ("0", "1", "2", "5", "9", "m", "h", "d", "M", "H", " ", "\t", "\n", "-", "+", ".", "١")
    for _ in range(CASES):
        text = rng.text(6, pieces)
        got = RemoteAccess.parse_ttl(text)
        assert got is None or 1 <= got <= SETUP_KEY_MAX_MINUTES
        if text.isascii():
            assert got == reference_ttl(text), repr(text)


@pytest.mark.parametrize("seed", SEEDS)
def test_an_ssh_destination_that_is_accepted_is_one_word_and_no_option(seed):
    rng = XorShift64(seed)
    pieces = ("a", "b", "@", "-", " ", "\t", ".", ":", "[", "]", "1", "::1", "10.0.0.1", "o")
    for _ in range(CASES):
        text = rng.text(6, pieces)
        if RemoteAccess.ssh_destination_problem(text) is None:
            assert text and not text.startswith("-")
            assert not any(char.isspace() for char in text)


# -- what the box and openssl print ---------------------------------------------------------------


class Done:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


@pytest.mark.parametrize("seed", SEEDS)
def test_the_boxs_facts_are_the_last_value_given_for_each_key(seed, monkeypatch):
    rng = XorShift64(seed)
    keys = ("arch", "root", "packages", "host_key", rng.text(3, FLAT), "a b")
    for _ in range(CASES):
        lines = [
            f"{rng.pick(keys)}={rng.text(4, FLAT)}" if rng.chance(3, 4) else rng.text(5, FLAT)
            for _ in range(rng.below(8))
        ]
        monkeypatch.setattr(
            RemoteBox, "_run", classmethod(lambda cls, *a, **k: Done("\n".join(lines)))
        )
        facts = RemoteBox.probe("box.example.org")
        assert facts["arch"] in ("amd64", "arm64", "")
        for key in ("root", "packages", "host_key"):
            given = [
                ln.partition("=")[2].strip() for ln in lines if ln.partition("=")[0].strip() == key
            ]
            if given:
                assert facts[key] == given[-1]


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@pytest.mark.parametrize("seed", SEEDS)
def test_a_certificates_end_date_and_issuer_are_read_from_openssls_lines(
    seed, monkeypatch, tmp_path
):
    rng = XorShift64(seed)
    for _ in range(CASES):
        year = 2000 + rng.below(90)
        date = f"{rng.pick(MONTHS)} {rng.below(31) + 1:2d} {rng.below(24):02d}:00:00 {year} GMT"
        organisation, common = rng.text(4, FLAT).strip(), rng.text(4, FLAT).strip()
        lines = ["issuer=", f"    organizationName={organisation}", f"    commonName={common}"]
        noise = [rng.text(5, FLAT) for _ in range(rng.below(3))]
        output = "\n".join(noise + ([f"notAfter={date}"] if rng.chance(9, 10) else []) + lines)
        monkeypatch.setattr(
            RemotePublicCertificate, "_run", classmethod(lambda cls, *a, **k: Done(output))
        )
        described = RemotePublicCertificate.describe(tmp_path / "c.pem")
        assert described is None or isinstance(described["days"], int)
        if described is not None:
            parts = [part for part in (organisation, common) if part]
            wanted = " ".join(parts) or "unknown"
            # A noise line that happens to read `organizationName=...` is later, so it can win.
            if not any(re.match(r"\s*(organizationName|commonName|notAfter)=", n) for n in noise):
                assert described["issuer"] == wanted


@pytest.mark.parametrize("seed", SEEDS)
def test_openssl_output_of_any_shape_is_never_a_crash(seed, monkeypatch, tmp_path):
    rng = XorShift64(seed)
    heads = ("notAfter=", "organizationName=", "commonName=", "notAfter=Jan  1 00:00:00 2030 GMT")
    for _ in range(CASES):
        output = "\n".join(rng.pick(heads) + rng.text(4) for _ in range(rng.below(6)))
        monkeypatch.setattr(
            RemotePublicCertificate, "_run", classmethod(lambda cls, *a, **k: Done(output))
        )
        RemotePublicCertificate.describe(tmp_path / "c.pem")


# -- the tunnel unit ------------------------------------------------------------------------------


def systemd_words(line: str) -> list[str]:
    """Splits an `ExecStart=` value as systemd does: quotes group, backslash escapes, `$$` and `%%`."""
    words, current, started, quote = [], [], False, None
    index = 0
    while index < len(line):
        char = line[index]
        if quote is None and char in " \t":
            if started:
                words.append("".join(current))
                current, started = [], False
        elif char in "\"'" and quote in (None, char):
            quote, started = (None if quote else char), True
        elif char == "\\" and quote is not None:
            index += 1
            current.append({"n": "\n", "t": "\t"}.get(line[index], line[index]))
            started = True
        elif char in "$%":
            assert line[index + 1 : index + 2] == char, f"{char!r} would be expanded: {line!r}"
            index += 1
            current.append(char)
            started = True
        else:
            current.append(char)
            started = True
        index += 1
    assert quote is None, f"unterminated quote: {line!r}"
    if started:
        words.append("".join(current))
    return words


@pytest.mark.parametrize("seed", SEEDS)
def test_the_tunnel_unit_runs_ssh_with_the_paths_it_was_built_from(seed, monkeypatch, tmp_path):
    rng = XorShift64(seed)
    for _ in range(max(10, CASES // 3)):
        key = tmp_path / rng.text(5, FLAT).replace("\x00", "")
        known = tmp_path / rng.text(5, FLAT).replace("\x00", "")
        monkeypatch.setattr(RemoteTunnel, "key", classmethod(lambda cls: key))
        monkeypatch.setattr(RemoteTunnel, "known_hosts", classmethod(lambda cls: known))
        unit = RemoteTunnel.render_unit("remote.example.org")
        line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
        words = systemd_words(line.removeprefix("ExecStart="))
        assert words[words.index("-i") + 1] == str(key), line
        assert f"UserKnownHostsFile={known}" in words, line
        assert words[-1] == "tunnel@remote.example.org" or words[-1].endswith("@remote.example.org")
