"""Installing Mightling from a release, with no checkout (install.sh, `ling-admin host`, and the
builder on a machine that has no source).

`install.sh` runs for real against a stand-in for GitHub's release API on loopback, into the
test's own HOME. Nothing here runs sudo, docker, cargo or pip.
"""

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dreamference.chat.desktop_installer import DesktopInstaller
from dreamference.chat.desktop_runner import DesktopRunner
from dreamference.runner import codex_branded_builder
from dreamference.runner.codex_branded_builder import CodexBrandedBuilder
from dreamference.runner.codex_installer import CodexInstaller
from dreamference.vllm_server import HostSafetySetup, SandboxPrerequisite, VLLMServerManager
from dreamference.vllm_server import host_safety_setup

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"
TARGET = "aarch64-unknown-linux-gnu"


# -- a stand-in for GitHub's releases API ----------------------------------------------------------

RATE_LIMIT_BODY = b'{"message":"API rate limit exceeded for 203.0.113.7.","documentation_url":"https://docs.github.com/rest"}'


class FakeRelease:
    """Serves one release the way api.github.com prints it: pretty JSON, an asset's `url` before
    its `name`, and the uploader's own `url` between them."""

    def __init__(self, assets, tag="v9.9.9", need_token=None, indent=2, rate_limited=False):
        self.indent = indent
        self.rate_limited = rate_limited
        self.assets = dict(assets)
        self.tag = tag
        self.need_token = need_token
        self.requests = []
        release = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                release.requests.append((self.path, self.headers.get("Authorization")))
                if release.rate_limited:
                    return self._send(403, RATE_LIMIT_BODY)
                if release.need_token and self.headers.get("Authorization") != f"Bearer {release.need_token}":
                    return self._send(404, b'{"message": "Not Found"}')
                if self.path.endswith("/releases/latest") or self.path.endswith(f"/releases/tags/{release.tag}"):
                    return self._send(200, release.document(self.server.server_address[1]).encode())
                if "/releases/assets/" in self.path:
                    index = int(self.path.rsplit("/", 1)[1])
                    return self._send(200, list(release.assets.values())[index])
                self._send(404, b'{"message": "Not Found"}')

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        # A short poll, so that `shutdown()` at teardown returns at once (the default half second
        # was most of these tests' time).
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True).start()

    def document(self, port):
        base = f"http://127.0.0.1:{port}/repos/test/ling"
        return json.dumps({
            "url": f"{base}/releases/1",
            "tag_name": self.tag,
            "draft": False,
            "assets": [
                {"url": f"{base}/releases/assets/{index}", "id": index, "name": name,
                 "uploader": {"login": "bot", "url": "http://127.0.0.1/users/bot"}, "size": len(body)}
                for index, (name, body) in enumerate(self.assets.items())
            ],
        }, indent=self.indent, separators=None if self.indent else (",", ":"))

    def close(self):
        self.server.shutdown()


# -- release signing (specs/DREAMFERENCE_RELEASE_SIGNING.md) -----------------------------------------
#
# The tests sign with keys of their own, made once per session, and run a copy of install.sh whose
# trusted key is the test key: the release key's private half never leaves its owner's machine.

KEYS = {}
RELEASE_KEYS_LINE = re.compile(r"^RELEASE_KEYS='[^']*'$", re.MULTILINE)


@pytest.fixture(scope="session", autouse=True)
def release_keys(tmp_path_factory):
    folder = tmp_path_factory.mktemp("release-keys")
    for name in ("release", "next", "attacker"):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(folder / name)],
                       check=True)
        KEYS[name] = folder / name
    installer = folder / "install.sh"
    public = KEYS["release"].with_suffix(".pub").read_text().strip()
    installer.write_text(RELEASE_KEYS_LINE.sub(f"RELEASE_KEYS='{public}'", INSTALL_SH.read_text(), count=1))
    KEYS["installer"] = installer
    yield KEYS


def ssh_sign(data, key="release", namespace="mightling-release"):
    """What the release job does: `ssh-keygen -Y sign` of `data`, returned as the .sig bytes."""
    folder = Path(KEYS[key]).parent / f"sign-{os.getpid()}-{threading.get_ident()}"
    folder.mkdir(exist_ok=True)
    message = folder / "message"
    message.write_bytes(data)
    Path(f"{message}.sig").unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(KEYS[key]), "-n", namespace, str(message)],
                   check=True, capture_output=True)
    return Path(f"{message}.sig").read_bytes()


def sign(assets, key="release", listed=None):
    """Adds SHA256SUMS over every asset (`listed` overrides a file's checksum) and its signature."""
    listed = listed or {}
    lines = [f"{listed.get(name) or hashlib.sha256(body).hexdigest()}  {name}"
             for name, body in sorted(assets.items()) if name not in ("SHA256SUMS", "SHA256SUMS.sig")]
    assets["SHA256SUMS"] = ("\n".join(lines) + "\n").encode()
    assets["SHA256SUMS.sig"] = ssh_sign(assets["SHA256SUMS"], key)
    return assets


def binaries(names=("ling", "codex-code-mode-host", "ling-search", "ling-fetch"), corrupt=None, signed=True,
             target=TARGET):
    """Release assets for `names`: gzipped scripts that print their own name, the per-target sums
    file and, unless `signed` is false, SHA256SUMS signed by the test's release key."""
    assets, sums = {}, []
    for name in names:
        archive = gzip.compress(f"#!/bin/sh\necho {name} from the release\n".encode())
        sums.append(f"{hashlib.sha256(archive).hexdigest()}  {name}-{target}.gz")
        assets[f"{name}-{target}.gz"] = archive + (b"tampered" if name == corrupt else b"")
    assets[f"ling-{target}.sha256sums"] = ("\n".join(sums) + "\n").encode()
    return sign(assets) if signed else assets


@pytest.fixture
def release_server():
    servers = []

    def start(*args, **kwargs):
        servers.append(FakeRelease(*args, **kwargs))
        return servers[-1]
    yield start
    for server in servers:
        server.close()


# The machine's root tools, as stand-ins in the test's own HOME. Nothing real is ever run as root:
# the fake sudo runs only programs under HOME (these stand-ins and the fake virtualenv's
# ling-admin) and only records anything else. Each stand-in records its arguments in HOME.
FAKE_TOOLS = {
    # FAKE_SUDO: nopasswd (the default), password (asks once, on standard input: "secret").
    "sudo": r'''
log="$HOME/sudo.log"; nonint=0; validate=0
while [ $# -gt 0 ]; do
    case "$1" in -n) nonint=1; shift;; -v) validate=1; shift;; -u|-g) shift 2;; --) shift; break;; -*) shift;; *) break;; esac
done
if [ "${FAKE_SUDO:-nopasswd}" != nopasswd ] && [ ! -f "$HOME/.sudo-stamp" ]; then
    if [ "$nonint" = 1 ]; then echo "sudo: a password is required" >&2; exit 1; fi
    printf '[sudo] password: ' >&2
    IFS= read -r password || exit 1
    [ "$password" = secret ] || { echo "Sorry, try again." >&2; exit 1; }
    touch "$HOME/.sudo-stamp"
fi
[ $# -eq 0 ] && exit 0
echo "$*" >> "$log"
# A command run without -n could stop to ask for the password again.
[ "$nonint" = 1 ] || echo "$*" >> "$HOME/sudo-could-ask.log"
if [ "$1" = env ]; then
    shift
    while :; do case "$1" in *=*) export "$1"; shift;; *) break;; esac; done
fi
case "$(command -v "$1")" in "$HOME"/*) exec "$@";; esac
exit 0
''',
    # FAKE_GROUPS: this login's groups; $HOME/groups-db: the group database's (usermod adds to it).
    "id": r'''
case "$1" in
    -u) echo 1000;; -un) echo tester;;
    -nG) if [ -n "$2" ]; then cat "$HOME/groups-db" 2>/dev/null || echo tester; else echo "${FAKE_GROUPS:-tester}"; fi;;
    *) echo "uid=1000(tester)";;
esac
''',
    "getent": r'''[ "$1 $2" = "group docker" ] && [ -z "$FAKE_NO_DOCKER_GROUP" ] && echo "docker:x:999:" || exit 2''',
    "usermod": r'''echo "usermod $*" >> "$HOME/root.log"; echo "$(cat "$HOME/groups-db" 2>/dev/null || echo tester) docker" > "$HOME/groups-db"''',
    "loginctl": r'''echo "loginctl $*" >> "$HOME/root.log"; [ "$1" = enable-linger ] && mkdir -p "$MIGHTLING_LINGER_DIR" && touch "$MIGHTLING_LINGER_DIR/$2"''',
    "sg": r'''echo "sg $*" >> "$HOME/sg.log"; [ "$1" = docker ] && [ "$2" = -c ] && exec sh -c "$3"''',
    # A Mac's disk-image tools. `attach` "mounts" the dmg at its -mountpoint as a Mightling.app
    # whose Info.plist holds the dmg's bytes (FAKE_HDIUTIL_FAIL: the attach fails); each call is
    # recorded in $HOME/hdiutil.log, and xattr's in $HOME/xattr.log.
    "hdiutil": r'''
echo "hdiutil $*" >> "$HOME/hdiutil.log"
if [ "$1" = attach ]; then
    [ -n "$FAKE_HDIUTIL_FAIL" ] && { echo "hdiutil: attach failed - image not recognized" >&2; exit 1; }
    dmg="$2"; mount=""
    while [ $# -gt 0 ]; do [ "$1" = -mountpoint ] && mount="$2"; shift; done
    mkdir -p "$mount/Mightling.app/Contents"
    cp "$dmg" "$mount/Mightling.app/Contents/Info.plist"
fi
exit 0
''',
    "ditto": r'''cp -R "$1" "$2"''',
    "xattr": r'''echo "xattr $*" >> "$HOME/xattr.log"''',
    # $HOME/upgrades: the `Inst` lines of `apt-get -s full-upgrade`; $HOME/apt-fail: the upgrade fails.
    "apt-get": r'''
echo "apt-get $*" >> "$HOME/apt.log"
case " $* " in
    *" -s "*) cat "$HOME/upgrades" 2>/dev/null;;
    *" -y full-upgrade "*) [ -f "$HOME/apt-fail" ] && exit 100;;
    *" install "*python3-venv*) touch "$HOME/venv-installed";;
esac
exit 0
''',
    # $HOME/firmware.json: what `get-updates --json` prints; without it, nothing to update (exit 2).
    # $HOME/fwupd-fail: `update` fails.
    "fwupdmgr": r'''
echo "fwupdmgr $*" >> "$HOME/fwupd.log"
case "$1" in get-updates) [ -f "$HOME/firmware.json" ] && cat "$HOME/firmware.json" || exit 2;; esac
case "$1" in update) [ -f "$HOME/fwupd-fail" ] && exit 1;; esac
exit 0
''',
    # A logged-in gh when FAKE_GH_TOKEN is set (its token), logged out otherwise; each call is logged.
    "gh": r'''
echo "gh $*" >> "$HOME/gh.log"
[ "$1 $2" = "auth token" ] && [ -n "$FAKE_GH_TOKEN" ] && { echo "$FAKE_GH_TOKEN"; exit 0; }
exit 1
''',
    # `-m venv DIR` makes a virtualenv whose pip installs a ling-admin that records its arguments
    # ($HOME/admin.log), fails the commands FAKE_ADMIN_FAIL names and hangs up the script (SIGHUP) on
    # the one FAKE_ADMIN_HANGUP names; FAKE_NO_VENV is a python3
    # without ensurepip until python3-venv is installed.
    "python3": r'''
if [ "$1 $2" = "-m venv" ]; then
    if [ -n "$FAKE_NO_VENV" ] && [ ! -f "$HOME/venv-installed" ]; then
        mkdir -p "$3"; echo "The virtual environment was not created successfully because ensurepip is not available." >&2; exit 1
    fi
    mkdir -p "$3/bin"
    cat > "$3/bin/python" <<EOF
#!/bin/sh
echo "\$*" >> "\$HOME/pip.log"
cat > "$3/bin/ling-admin" <<'ADMIN'
#!/bin/sh
echo "\$*" >> "\$HOME/admin.log"
case ",\${FAKE_ADMIN_FAIL:-}," in *",\$1 \$2,"*) echo "ling-admin \$1 \$2 failed"; exit 1;; esac
[ "\$1 \$2" = "\${FAKE_ADMIN_HANGUP:-}" ] && kill -HUP \$PPID
[ "\$1 \$2" = "node id" ] && echo 0123456789abcdef
[ -t 0 ] && echo "ling-admin had a terminal on its standard input" >> "\$HOME/admin.log"
exit 0
ADMIN
chmod +x "$3/bin/ling-admin"
EOF
    chmod +x "$3/bin/python"
    exit 0
fi
exit 0
''',
}


def run_install(home, server, *args, token=None, uname_m="aarch64", gpu="Some Other GPU", pci=(),
                nvidia_smi=True, uname_s="Linux", env_extra=None, terminal_input=None):
    """Runs install.sh with a `uname`, an `nvidia-smi` and a PCI bus that report the machine the
    test wants, whatever runs the tests (on a GB10 the real bus has the GB10's GPU on it), the
    stand-in root tools above, and no controlling terminal (its own session), unless
    `terminal_input` is given: then standard input is a pseudo-terminal that is fed that text."""
    fake_bin = Path(home) / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    # Without a driver nvidia-smi fails; it is never left out of the fake bin, where the real one
    # in /usr/bin would answer for this machine's GPU.
    tools = [("uname", f'case "$1" in -s) echo {uname_s};; -m) echo {uname_m};; -n) echo spark-test;; *) echo {uname_s};; esac'),
             ("nvidia-smi", f'echo "{gpu}"' if nvidia_smi else 'echo "NVIDIA-SMI has failed" >&2; exit 9'),
             *FAKE_TOOLS.items()]
    for name, script in tools:
        (fake_bin / name).write_text(f"#!/bin/sh\n{script.strip()}\n")
        (fake_bin / name).chmod(0o755)
    pci_dir = Path(home) / "fakepci"
    shutil.rmtree(pci_dir, ignore_errors=True)
    pci_dir.mkdir()
    for number, (vendor, device) in enumerate(pci):
        slot = pci_dir / f"0000:0{number}:00.0"
        slot.mkdir()
        (slot / "vendor").write_text(vendor + "\n")
        (slot / "device").write_text(device + "\n")
    env = {"HOME": str(home), "PATH": f"{fake_bin}:/usr/bin:/bin", "MIGHTLING_PCI_DEVICES": str(pci_dir),
           "MIGHTLING_RELEASE_API": server.url, "MIGHTLING_RELEASE_REPO": "test/ling",
           "MIGHTLING_LINGER_DIR": str(Path(home) / "linger"), **(env_extra or {})}
    if token:
        env["GH_TOKEN"] = token
    if terminal_input is None:
        # A session of its own: no /dev/tty, as under `ssh host 'bash install.sh'`, whatever
        # terminal runs the tests.
        return subprocess.run(["bash", str(KEYS["installer"]), *args], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120, check=False, start_new_session=True)
    import pty
    controller, terminal = pty.openpty()
    try:
        os.write(controller, terminal_input.encode())
        return subprocess.run(["bash", str(KEYS["installer"]), *args], env=env, capture_output=True, text=True,
                              stdin=terminal, timeout=120, check=False, start_new_session=True)
    finally:
        os.close(terminal)
        os.close(controller)


def node_release(**kwargs):
    """A release a node installs from: the binaries and a wheel, signed."""
    assets = binaries(signed=False, **kwargs)
    assets["dreamference-9.9.9-py3-none-any.whl"] = b"a wheel"
    return sign(assets)


def logged(home, name):
    path = Path(home) / name
    return path.read_text() if path.exists() else ""


def test_the_client_is_installed_from_the_release_checked_and_linked(tmp_path, release_server):
    server = release_server(binaries())
    result = run_install(tmp_path, server, "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    bin_dir = tmp_path / ".local/share/dreamference/mightling/bin"
    assert sorted(path.name for path in bin_dir.iterdir()) == [
        "codex-code-mode-host", "ling", "ling-fetch", "ling-search"]
    for name in ("ling", "ling-search", "ling-fetch"):
        link = tmp_path / ".local/bin" / name
        assert link.is_symlink() and os.readlink(link) == str(bin_dir / name)
        assert subprocess.run([str(link)], capture_output=True, text=True).stdout == f"{name} from the release\n"
    # Codex finds its Code Mode host beside its own executable; it is not a command to type.
    assert not (tmp_path / ".local/bin/codex-code-mode-host").exists()
    assert "v9.9.9" in result.stdout and "role: client" in result.stdout
    assert "signed by Mightling's release key" in result.stdout
    # Nothing of the node: no virtualenv, no ling-admin.
    assert not (tmp_path / ".local/share/dreamference/venv").exists()


def test_the_link_folder_is_named_when_it_is_not_on_the_path(tmp_path, release_server):
    result = run_install(tmp_path, release_server(binaries()), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{tmp_path / '.local/bin'} is not on your PATH" in result.stdout
    home = tmp_path / "on-path"
    home.mkdir()
    path = f"{home / 'fakebin'}:{home / '.local/bin'}:/usr/bin:/bin"
    result = run_install(home, release_server(binaries()), "--role", "client", env_extra={"PATH": path})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "is not on your PATH" not in result.stdout


def test_a_release_with_the_code_index_installs_it_too(tmp_path, release_server):
    names = ("ling", "codex-code-mode-host", "ling-search", "ling-fetch", "ling-code")
    result = run_install(tmp_path, release_server(binaries(names)), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".local/bin/ling-code").is_symlink()


def test_an_old_release_without_the_optional_commands_still_installs(tmp_path, release_server):
    result = run_install(tmp_path, release_server(binaries(("ling", "codex-code-mode-host"))), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".local/bin/ling").is_symlink()
    assert not (tmp_path / ".local/bin/ling-search").exists()


def test_the_signal_bridge_is_installed_beside_ling_without_a_link(tmp_path, release_server):
    # `ling signal …` finds its bridge beside ling, as Codex finds its Code Mode host
    # (specs/DREAMFERENCE_SETUP.md §3.2).
    names = ("ling", "codex-code-mode-host", "ling-signal", "ling-delegate")
    result = run_install(tmp_path, release_server(binaries(names)), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    for name in names:
        assert (tmp_path / ".local/share/dreamference/mightling/bin" / name).is_file(), name
    assert not (tmp_path / ".local/bin/ling-signal").exists()
    assert not (tmp_path / ".local/bin/codex-code-mode-host").exists()
    assert (tmp_path / ".local/bin/ling-delegate").is_symlink()


@pytest.mark.parametrize("repo, env_token, sent", [
    ("", None, None),                         # the public repository: gh's login is not asked
    ("test/ling", None, "Bearer from-gh"),    # a fork: a logged-in gh's token is used
    ("test/ling", "from-env", "Bearer from-env"),   # GH_TOKEN wins over gh
], ids=["public", "fork", "env-token"])
def test_a_gh_login_is_used_only_for_a_fork(tmp_path, release_server, repo, env_token, sent):
    # A gh login carries far more access than reading a release (specs/DREAMFERENCE_SETUP.md §3.2).
    server = release_server(binaries())
    result = run_install(tmp_path, server, "--role", "client", token=env_token,
                         env_extra={"MIGHTLING_RELEASE_REPO": repo, "FAKE_GH_TOKEN": "from-gh"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert {auth for _, auth in server.requests} == {sent}
    assert (tmp_path / "gh.log").exists() == (sent == "Bearer from-gh")


def test_github_token_is_used_when_gh_token_is_not_set(tmp_path, release_server):
    # GitHub Actions sets GITHUB_TOKEN; with it, gh is not asked.
    server = release_server(binaries())
    result = run_install(tmp_path, server, "--role", "client", env_extra={"GITHUB_TOKEN": "from-actions"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert {auth for _, auth in server.requests} == {"Bearer from-actions"}
    assert not (tmp_path / "gh.log").exists()


def test_a_download_that_fails_its_checksum_installs_nothing(tmp_path, release_server):
    # The last archive is the tampered one: the ones before it passed, and must not be placed.
    result = run_install(tmp_path, release_server(binaries(corrupt="ling-fetch")), "--role", "client")
    assert result.returncode == 1
    assert "does not match its checksum" in result.stderr
    assert not (tmp_path / ".local/share/dreamference/mightling").exists()
    assert not (tmp_path / ".local/bin").exists()


def test_a_binary_its_sums_file_does_not_list_installs_nothing(tmp_path, release_server):
    assets = binaries(signed=False)
    sums = f"ling-{TARGET}.sha256sums"
    assets[sums] = b"".join(line + b"\n" for line in assets[sums].splitlines() if b"ling-search" not in line)
    result = run_install(tmp_path, release_server(sign(assets)), "--role", "client")
    assert result.returncode == 1
    assert f"{sums} has no checksum for ling-search-{TARGET}.gz" in result.stderr
    assert nothing_installed(tmp_path)


def test_a_release_without_a_required_binary_installs_nothing(tmp_path, release_server):
    server = release_server(binaries(names=("ling", "ling-search", "ling-fetch")))
    result = run_install(tmp_path, server, "--role", "client")
    assert result.returncode == 1
    assert f"release v9.9.9 is missing codex-code-mode-host-{TARGET}.gz." in result.stderr
    assert nothing_installed(tmp_path)


def test_sums_written_in_binary_mode_are_read(tmp_path, release_server):
    # `sha256sum --binary`, and shasum on a Mac, mark each file name with a `*`.
    assets = binaries(signed=False)
    sums = f"ling-{TARGET}.sha256sums"
    assets[sums] = assets[sums].replace(b"  ", b" *")
    sign(assets)
    assets["SHA256SUMS"] = assets["SHA256SUMS"].replace(b"  ", b" *")
    assets["SHA256SUMS.sig"] = ssh_sign(assets["SHA256SUMS"])
    result = run_install(tmp_path, release_server(assets), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".local/bin/ling").is_symlink()


def test_a_machine_the_release_has_no_binaries_for_is_told_which_exist(tmp_path, release_server):
    result = run_install(tmp_path, release_server(binaries()), "--role", "client", uname_m="x86_64")
    assert result.returncode == 1
    assert "no binaries for x86_64-unknown-linux-gnu" in result.stderr
    assert TARGET in result.stderr


def test_a_private_repository_without_a_token_says_what_to_set(tmp_path, release_server):
    server = release_server(binaries(), need_token="s3cret")
    result = run_install(tmp_path, server, "--role", "client")
    assert result.returncode == 1
    assert "GH_TOKEN" in result.stderr and "private" in result.stderr
    # With the token the same release installs, and the token goes to the API and the assets.
    result = run_install(tmp_path, server, "--role", "client", token="s3cret")
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(auth == "Bearer s3cret" for _, auth in server.requests[1:])


@pytest.mark.parametrize("indent", [2, None, 0], ids=["pretty", "compact", "newlines-only"])
def test_the_release_parses_whether_the_api_prints_it_pretty_or_compact(tmp_path, release_server, indent):
    """Finds the tag and the assets when a proxy re-prints the API's answer as one line."""
    server = release_server(binaries(), tag="v1.7.2", indent=indent)
    result = run_install(tmp_path, server, "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Mightling v1.7.2" in result.stdout
    assert "ling from the release" in subprocess.run(
        [str(tmp_path / ".local/bin/ling")], capture_output=True, text=True, check=False).stdout


def test_a_compact_answer_with_spaced_colons_still_has_its_tag(tmp_path, release_server):
    server = release_server(binaries(), tag="v1.7.2")
    base = server.document
    server.document = lambda port: re.sub(r'"\s*:\s*', '" :   ', json.dumps(json.loads(base(port))))
    result = run_install(tmp_path, server, "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Mightling v1.7.2" in result.stdout


def test_a_rate_limited_api_is_named_not_reported_as_a_missing_tag(tmp_path, release_server):
    server = release_server(binaries(), rate_limited=True)
    result = run_install(tmp_path, server, "--role", "client")
    assert result.returncode == 1
    assert "rate limit" in result.stderr and "GH_TOKEN" in result.stderr
    assert "no tag" not in result.stderr


def test_an_answer_without_a_tag_installs_nothing(tmp_path, release_server):
    server = release_server(binaries())
    server.document = lambda port: FakeRelease.document(server, port).replace('"tag_name"', '"tag"')
    result = run_install(tmp_path, server, "--role", "client")
    assert result.returncode == 1 and "has no tag" in result.stderr
    assert nothing_installed(tmp_path)


@pytest.mark.parametrize("version", [("--version", "1.4.0"), ("--version=1.4.0",)])
def test_a_named_version_is_fetched_by_its_tag(tmp_path, release_server, version):
    server = release_server(binaries(), tag="v1.4.0")
    result = run_install(tmp_path, server, "--role", "client", *version)
    assert result.returncode == 0, result.stdout + result.stderr
    assert server.requests[0][0].endswith("/releases/tags/v1.4.0")


def test_a_real_file_on_the_path_is_left_alone(tmp_path, release_server):
    (tmp_path / ".local/bin").mkdir(parents=True)
    (tmp_path / ".local/bin/ling").write_text("mine")
    result = run_install(tmp_path, release_server(binaries()), "--role", "client")
    assert result.returncode == 0
    assert (tmp_path / ".local/bin/ling").read_text() == "mine"
    assert "is not a link; leaving it" in result.stdout


def test_reinstalling_replaces_the_binaries_in_place(tmp_path, release_server):
    assert run_install(tmp_path, release_server(binaries()), "--role", "client").returncode == 0
    assert run_install(tmp_path, release_server(binaries()), "--role", "client").returncode == 0
    bin_dir = tmp_path / ".local/share/dreamference/mightling/bin"
    assert not [path.name for path in bin_dir.iterdir() if path.name.startswith(".")]


def puffin_install(home, files):
    """An installation from before the rename: its folder, with `files` (path: text) in it."""
    old = Path(home) / ".local/share/dreamference/puffin"
    for name, text in files.items():
        (old / name).parent.mkdir(parents=True, exist_ok=True)
        (old / name).write_text(text)
    return old


def test_a_puffin_installation_becomes_the_mightling_one_with_its_tools_renamed(tmp_path, release_server):
    old = puffin_install(tmp_path, {"bin/puffin-code": "the code index", "index/state": "kept"})
    result = run_install(tmp_path, release_server(binaries()), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    new = tmp_path / ".local/share/dreamference/mightling"
    assert not old.exists()
    assert (new / "index/state").read_text() == "kept"
    assert (new / "bin/ling-code").read_text() == "the code index"
    assert not (new / "bin/puffin-code").exists()
    assert "Puffin is now Mightling" in result.stdout


def test_a_tool_already_under_its_new_name_is_not_replaced_by_the_old_one(tmp_path, release_server):
    puffin_install(tmp_path, {"bin/puffin-code": "old", "bin/ling-code": "new"})
    result = run_install(tmp_path, release_server(binaries()), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".local/share/dreamference/mightling/bin/ling-code").read_text() == "new"


def test_a_puffin_folder_beside_a_mightling_one_is_left_where_it_is(tmp_path, release_server):
    old = puffin_install(tmp_path, {"bin/puffin": "old"})
    new = tmp_path / ".local/share/dreamference/mightling"
    new.mkdir(parents=True)
    result = run_install(tmp_path, release_server(binaries()), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (old / "bin/puffin").read_text() == "old"
    assert not (new / "puffin").exists()
    assert "Puffin is now Mightling" not in result.stdout


def test_the_old_command_links_go_but_a_real_file_of_that_name_stays(tmp_path, release_server):
    puffin_install(tmp_path, {"bin/puffin": "old"})
    links = tmp_path / ".local/bin"
    links.mkdir(parents=True)
    old_bin = tmp_path / ".local/share/dreamference/puffin/bin"
    for name in ("puffin", "puffin-search", "puffin-fetch", "puffin-code", "puffin-app"):
        (links / name).symlink_to(old_bin / name)
    (links / "puffin-admin").write_text("mine")
    result = run_install(tmp_path, release_server(binaries()), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in links.iterdir() if path.name.startswith("puffin")) == ["puffin-admin"]
    assert (links / "puffin-admin").read_text() == "mine"


def test_an_unknown_role_is_refused_before_anything_is_downloaded(tmp_path, release_server):
    server = release_server(binaries())
    result = run_install(tmp_path, server, "--role", "server")
    assert result.returncode == 1 and "--role is client or node" in result.stderr
    assert server.requests == []


def test_an_unknown_argument_is_refused_before_anything_is_downloaded(tmp_path, release_server):
    server = release_server(binaries())
    result = run_install(tmp_path, server, "--roll", "client")
    assert result.returncode == 1 and "unknown argument: --roll" in result.stderr
    assert server.requests == []


def test_role_both_is_the_node(tmp_path, release_server):
    # A node is a client of itself too, so `both` is taken as the node.
    server = release_server(binaries(target="aarch64-apple-darwin"))
    result = run_install(tmp_path, server, "--role", "both", uname_s="Darwin", uname_m="arm64")
    assert result.returncode == 1 and "macOS" in result.stderr
    assert server.requests == []


def test_the_machine_decides_the_role(tmp_path, release_server):
    # A GB10 gets the node (which then needs the release's wheel); anything else, the client.
    server = release_server(binaries())
    assert "role: client" in run_install(tmp_path, server).stdout
    on_gb10 = run_install(tmp_path, server, gpu="NVIDIA GB10")
    assert "role: node" in on_gb10.stdout and "not a GB10" not in on_gb10.stdout
    assert "role: client" in run_install(tmp_path, server, "--role", "client", gpu="NVIDIA GB10").stdout
    assert "role: client" in run_install(tmp_path, server, "--role=client", gpu="NVIDIA GB10").stdout
    # An x86 machine with some NVIDIA GPU is not one.
    assert "no binaries for x86_64" in run_install(tmp_path, server, uname_m="x86_64", gpu="NVIDIA GB10").stderr


def test_a_gb10_without_a_driver_is_still_found_by_its_pci_id(tmp_path, release_server):
    # Every GB10 machine (DGX Spark, Acer, ASUS, Dell, Gigabyte, HP, Lenovo, MSI) has the same GPU,
    # 10de:2e12; on a fresh Ubuntu install there is no nvidia-smi to ask yet.
    server = release_server(binaries())
    gb10 = [("0x10de", "0x22ce"), ("0x10de", "0x2e12")]
    assert "role: node" in run_install(tmp_path, server, nvidia_smi=False, pci=gb10).stdout
    # A driver that answers for another GPU is overruled by the bus only for the GB10's id.
    assert "role: node" in run_install(tmp_path, server, gpu="Some Other GPU", pci=gb10).stdout
    assert "role: client" in run_install(tmp_path, server, nvidia_smi=False,
                                         pci=[("0x10de", "0x2e13"), ("0x8086", "0x2e12")]).stdout
    assert "role: client" in run_install(tmp_path, server, nvidia_smi=False).stdout


@pytest.mark.parametrize("uname_s, uname_m, target", [
    ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
    # Some systems name it amd64.
    ("Linux", "amd64", "x86_64-unknown-linux-gnu"),
    # macOS's uname says arm64 where the asset says aarch64.
    ("Darwin", "arm64", "aarch64-apple-darwin"),
    ("Darwin", "x86_64", "x86_64-apple-darwin"),
])
def test_a_laptop_that_is_not_a_gb10_gets_the_client_built_for_it(tmp_path, release_server, uname_s, uname_m, target):
    # Intel/AMD Linux and both kinds of Mac are clients of a GB10 node: the same assets, named after
    # their own target, as `ling update` names them (update.rs `target_for`).
    names = ("ling", "codex-code-mode-host", "ling-search", "ling-fetch", "ling-code")
    server = release_server(binaries(names, target=target))
    result = run_install(tmp_path, server, uname_s=uname_s, uname_m=uname_m, gpu="NVIDIA GB10")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"for {target}, role: client" in result.stdout
    for name in ("ling", "ling-search", "ling-fetch", "ling-code"):
        link = tmp_path / ".local/bin" / name
        assert subprocess.run([str(link)], capture_output=True, text=True).stdout == f"{name} from the release\n"
    assert not (tmp_path / ".local/share/dreamference/venv").exists()


def test_a_mac_is_never_installed_as_a_node(tmp_path, release_server):
    # The node is a GB10's model server, its Docker containers and host settings: nothing a Mac can
    # run. Refused before anything is downloaded, rather than installed and left broken.
    server = release_server(binaries(target="aarch64-apple-darwin"))
    result = run_install(tmp_path, server, "--role", "node", uname_s="Darwin", uname_m="arm64")
    assert result.returncode == 1
    assert "macOS" in result.stderr and "client" in result.stderr
    assert server.requests == []


@pytest.mark.parametrize("uname_s, uname_m, said", [
    ("MINGW64_NT-10.0-26100", "x86_64", "on Windows use install.ps1"),
    ("Linux", "riscv64", "unsupported processor: riscv64"),
])
def test_a_system_or_processor_without_binaries_is_refused_before_anything_is_downloaded(
        tmp_path, release_server, uname_s, uname_m, said):
    server = release_server(binaries())
    result = run_install(tmp_path, server, "--role", "client", uname_s=uname_s, uname_m=uname_m)
    assert result.returncode == 1 and said in result.stderr
    assert server.requests == []


# -- the desktop app on a Mac ------------------------------------------------------------------

def mac_release(target="aarch64-apple-darwin", listed=None):
    """A Mac client's release: its binaries and both dmgs that mac-preview.yml builds, signed."""
    assets = binaries(signed=False, target=target)
    for arch in ("arm64", "x64"):
        assets[f"Mightling-9.9.9-{arch}-preview.dmg"] = f"the {arch} app".encode()
    return sign(assets, listed=listed)


def asset_requested(server, name):
    index = list(server.assets).index(name)
    return any(path.endswith(f"/releases/assets/{index}") for path, _ in server.requests)


@pytest.mark.parametrize("uname_m, target, arch", [
    ("arm64", "aarch64-apple-darwin", "arm64"),
    ("x86_64", "x86_64-apple-darwin", "x64"),
])
def test_a_mac_gets_the_desktop_app_from_its_own_dmg_with_no_quarantine(tmp_path, release_server, uname_m,
                                                                         target, arch):
    server = release_server(mac_release(target))
    result = run_install(tmp_path, server, uname_s="Darwin", uname_m=uname_m)
    assert result.returncode == 0, result.stdout + result.stderr
    app = tmp_path / "Applications/Mightling.app"
    # /Applications does not exist here, so the app goes to ~/Applications, which needs no admin.
    assert (app / "Contents/Info.plist").read_text() == f"the {arch} app"
    assert (tmp_path / ".local/bin/ling").is_symlink()
    other = "x64" if arch == "arm64" else "arm64"
    assert not asset_requested(server, f"Mightling-9.9.9-{other}-preview.dmg")
    attach = logged(tmp_path, "hdiutil.log").splitlines()[0]
    assert attach.startswith("hdiutil attach ") and f"-{arch}-preview.dmg" in attach
    for flag in ("-nobrowse", "-readonly", "-noautoopen", "-mountpoint"):
        assert flag in attach.split()
    assert "hdiutil detach" in logged(tmp_path, "hdiutil.log")
    # Downloaded with curl, nothing carries the mark; it is cleared anyway, never added.
    assert f"xattr -dr com.apple.quarantine {app}" in logged(tmp_path, "xattr.log")
    assert not (tmp_path / "Applications/.Mightling.app.new").exists()
    assert f"Installed the desktop app: {app}" in result.stdout
    # The record `ling update` reads to update the app too (update.rs, APP_RECORD).
    record = tmp_path / ".local/share/dreamference/mightling/desktop-app"
    assert record.read_text() == f"path={app}\nversion=v9.9.9\n"


def test_running_it_again_replaces_the_app_in_place(tmp_path, release_server):
    stale = tmp_path / "Applications/Mightling.app/Contents"
    stale.mkdir(parents=True)
    (stale / "left-over").write_text("from the last release")
    result = run_install(tmp_path, release_server(mac_release()), uname_s="Darwin", uname_m="arm64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (stale / "Info.plist").read_text() == "the arm64 app"
    assert not (stale / "left-over").exists()
    assert sorted(path.name for path in (tmp_path / "Applications").iterdir()) == ["Mightling.app"]


def test_a_dmg_that_fails_its_checksum_installs_nothing(tmp_path, release_server):
    server = release_server(mac_release(listed={"Mightling-9.9.9-arm64-preview.dmg": "0" * 64}))
    result = run_install(tmp_path, server, uname_s="Darwin", uname_m="arm64")
    assert result.returncode == 1
    assert "Mightling-9.9.9-arm64-preview.dmg does not match its checksum" in result.stderr
    assert nothing_installed(tmp_path) and not (tmp_path / "Applications").exists()
    assert logged(tmp_path, "hdiutil.log") == ""


def test_no_app_installs_the_terminal_agent_alone(tmp_path, release_server):
    server = release_server(mac_release())
    result = run_install(tmp_path, server, "--no-app", uname_s="Darwin", uname_m="arm64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".local/bin/ling").is_symlink()
    assert not (tmp_path / "Applications").exists()
    assert not asset_requested(server, "Mightling-9.9.9-arm64-preview.dmg")
    assert not (tmp_path / ".local/share/dreamference/mightling/desktop-app").exists()


def test_a_release_without_a_mac_app_installs_the_terminal_agent(tmp_path, release_server):
    # A Mac build is optional in release.yml: its absence is said, and is no failure.
    result = run_install(tmp_path, release_server(binaries(target="aarch64-apple-darwin")),
                         uname_s="Darwin", uname_m="arm64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "carries no desktop app for this Mac" in result.stdout
    assert (tmp_path / ".local/bin/ling").is_symlink()


def test_the_dmg_is_told_from_its_blockmap(tmp_path, release_server):
    # electron-builder writes a .dmg.blockmap beside each dmg, for updates; here it is listed first.
    assets = binaries(signed=False, target="aarch64-apple-darwin")
    assets["Mightling-9.9.9-arm64-preview.dmg.blockmap"] = b"the blockmap"
    assets["Mightling-9.9.9-arm64-preview.dmg"] = b"the arm64 app"
    result = run_install(tmp_path, release_server(sign(assets)), uname_s="Darwin", uname_m="arm64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "Applications/Mightling.app/Contents/Info.plist").read_text() == "the arm64 app"


def test_a_dmg_with_no_sha256sums_to_check_it_against_is_left_out(tmp_path, release_server):
    # A release from before signing has no release-wide SHA256SUMS.
    assets = binaries(signed=False, target="aarch64-apple-darwin")
    assets["Mightling-1.4.1-arm64.dmg"] = b"the arm64 app"
    server = release_server(assets, tag="v1.4.1")
    result = run_install(tmp_path, server, "--version", "1.4.1", uname_s="Darwin", uname_m="arm64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "has no SHA256SUMS to check Mightling-1.4.1-arm64.dmg against; the desktop app is left out." in result.stdout
    assert (tmp_path / ".local/bin/ling").is_symlink() and not (tmp_path / "Applications").exists()


def test_a_dmg_that_does_not_mount_leaves_the_terminal_agent_installed(tmp_path, release_server):
    result = run_install(tmp_path, release_server(mac_release()), uname_s="Darwin", uname_m="arm64",
                         env_extra={"FAKE_HDIUTIL_FAIL": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "the desktop app was not installed" in result.stdout
    assert (tmp_path / ".local/bin/ling").is_symlink()
    assert not (tmp_path / "Applications/Mightling.app").exists()


def test_linux_never_downloads_the_mac_app(tmp_path, release_server):
    assets = binaries(signed=False)
    assets["Mightling-9.9.9-arm64-preview.dmg"] = b"the arm64 app"
    server = release_server(sign(assets))
    result = run_install(tmp_path, server, "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not asset_requested(server, "Mightling-9.9.9-arm64-preview.dmg")
    assert logged(tmp_path, "hdiutil.log") == ""


# -- the node, unattended -----------------------------------------------------------------------

UPGRADES = ("Inst dgx-dashboard [1.0] (1.1 NVIDIA DGX:noble [arm64])\n"
            "Inst nvidia-modprobe [580.65] (580.82 cuda-ubuntu2404-sbsa [arm64])\n"
            "Inst libc6 [2.39-0ubuntu8.4] (2.39-0ubuntu8.5 Ubuntu:24.04/noble-updates [arm64])\n")
FIRMWARE_JSON = """{
  "Devices" : [
    {
      "Name" : "UEFI Device Firmware",
      "DeviceId" : "4b9e",
      "Version" : "0.8.4",
      "Releases" : [
        {
          "Name" : "UEFI Firmware",
          "Version" : "0.9.1"
        }
      ]
    },
    {
      "Name" : "USB-C PD Controller",
      "Version" : "1.20",
      "Releases" : [
        {
          "Version" : "1.22"
        }
      ]
    }
  ]
}
"""


def fresh_gb10(home):
    """What a GX10 out of the box has: updates waiting, no python3-venv, not in the docker group."""
    (home / "upgrades").write_text(UPGRADES)
    (home / "firmware.json").write_text(FIRMWARE_JSON)
    return {"FAKE_NO_VENV": "1", "FAKE_GROUPS": "tester adm sudo"}


def test_a_fresh_gb10_with_no_terminal_gets_every_step_and_asks_nothing(tmp_path, release_server):
    # 2026-10-08, installing 1.4.1 on a fresh GB10 over ssh: with no terminal, host setup and
    # advertising were silently left out, lingering was never turned on, and a missing
    # python3-venv stopped the script. Now a sudo that needs no password means no interaction.
    env = fresh_gb10(tmp_path)
    result = run_install(tmp_path, release_server(node_release()), gpu="NVIDIA GB10", env_extra=env)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    admin = logged(tmp_path, "admin.log").splitlines()
    assert admin == ["host setup --yes", "node id", "node enable --yes", "model download", "server start"]
    root = logged(tmp_path, "sudo.log")
    assert "apt-get -o DPkg::Lock::Timeout=600 install -y python3-venv" in root
    assert "usermod -aG docker tester" in root and "loginctl enable-linger tester" in root
    assert (tmp_path / "linger" / "tester").exists()
    # This login predates the group, so Docker is reached through sg (the model, not the rest).
    assert "server start" in logged(tmp_path, "sg.log") and "node enable" not in logged(tmp_path, "sg.log")
    # Updates are listed, never installed, with no terminal; phased ones never reach the list.
    assert "-y full-upgrade" not in logged(tmp_path, "apt.log") and "update -y" not in logged(tmp_path, "fwupd.log")
    assert "available, not installed" in out and "dgx-dashboard nvidia-modprobe" in out
    assert "sudo apt-get update && sudo apt-get full-upgrade" in out and "sudo fwupdmgr update" in out
    assert "USB-C PD Controller: 1.20 -> 1.22" in out and "UEFI Device Firmware: 0.8.4 -> 0.9.1" in out
    for line in ("python3-venv installed", "host settings applied", "docker group: tester added",
                 "lingering turned on", "advertised on the local network", "default model downloaded",
                 "model server started"):
        assert f"✅ {line}" in out, line
    assert "skipped" not in out.lower() and "❌" not in out and "[y/N]" not in out
    logs = list((tmp_path / ".local/state/dreamference").glob("install-*.log"))
    assert len(logs) == 1 and str(logs[0]) in out


def test_no_terminal_and_a_sudo_that_wants_a_password_stops_before_anything(tmp_path, release_server):
    server = release_server(node_release())
    result = run_install(tmp_path, server, gpu="NVIDIA GB10", env_extra={"FAKE_SUDO": "password"})
    assert result.returncode == 1
    assert "no terminal" in result.stderr and "bash install.sh" in result.stderr
    # Not half the work: not even the release was asked for.
    assert server.requests == [] and not (tmp_path / ".local/share/dreamference").exists()


def test_a_wrong_password_at_the_terminal_stops_before_anything(tmp_path, release_server):
    server = release_server(node_release())
    result = run_install(tmp_path, server, gpu="NVIDIA GB10", env_extra={"FAKE_SUDO": "password"},
                         terminal_input="wrong\n")
    assert result.returncode == 1
    assert "sudo did not accept the password; nothing was changed." in result.stderr
    assert server.requests == [] and logged(tmp_path, "sudo.log") == ""


def test_at_a_terminal_the_password_and_the_updates_are_asked_once_at_the_start(tmp_path, release_server):
    env = {**fresh_gb10(tmp_path), "FAKE_SUDO": "password"}
    result = run_install(tmp_path, release_server(node_release()), gpu="NVIDIA GB10", env_extra=env,
                         terminal_input="secret\ny\n")
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    assert result.stderr.count("[sudo] password") == 1 and out.count("[y/N]") == 1
    root = logged(tmp_path, "sudo.log")
    assert ("env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Dpkg::Options::=--force-confdef "
            "-o Dpkg::Options::=--force-confold -y full-upgrade") in root
    assert "fwupdmgr update -y --no-reboot-check" in root
    # After everything else, and never a reboot: the summary says when.
    assert root.index("loginctl") < root.index("full-upgrade")
    assert "reboot" not in logged(tmp_path, "admin.log") and "sudo reboot" in out
    assert "✅ system packages upgraded (3)" in out and "✅ firmware staged" in out
    # Every command after the password ran with sudo -n, which never asks.
    assert logged(tmp_path, "sudo-could-ask.log") == ""
    # No ling-admin ever had the terminal, so none of them could have asked anything.
    assert "terminal" not in logged(tmp_path, "admin.log")


@pytest.mark.parametrize("failing, reboot", [
    (("apt-fail", "fwupd-fail"), False), (("apt-fail",), True), (("fwupd-fail",), True)])
def test_the_reboot_is_asked_for_only_when_an_update_went_in(tmp_path, release_server, failing, reboot):
    # Both updates failing used to end with "Reboot now to finish the updates" all the same.
    env = {**fresh_gb10(tmp_path), "FAKE_SUDO": "password"}
    for name in failing:
        (tmp_path / name).write_text("")
    result = run_install(tmp_path, release_server(node_release()), gpu="NVIDIA GB10", env_extra=env,
                         terminal_input="secret\ny\n")
    out = result.stdout
    assert result.returncode == 1, out + result.stderr
    for name in failing:
        assert {"apt-fail": "❌ system packages", "fwupd-fail": "❌ firmware"}[name] in out
    assert ("sudo reboot" in out) == reboot, out


def test_the_update_question_defaults_to_no(tmp_path, release_server):
    env = {**fresh_gb10(tmp_path), "FAKE_SUDO": "password"}
    result = run_install(tmp_path, release_server(node_release()), gpu="NVIDIA GB10", env_extra=env,
                         terminal_input="secret\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "-y full-upgrade" not in logged(tmp_path, "sudo.log")
    assert "available, not installed" in result.stdout


def test_firmware_updates_alone_are_listed_too(tmp_path, release_server):
    # A machine whose packages are all current can still have firmware waiting.
    env = fresh_gb10(tmp_path)
    (tmp_path / "upgrades").unlink()
    result = run_install(tmp_path, release_server(node_release()), gpu="NVIDIA GB10", env_extra=env)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    assert "System updates available: 0 package(s), 2 firmware update(s)." in out
    assert "available, not installed: 2 firmware update(s); install: sudo fwupdmgr refresh" in out


def test_a_cuda_package_is_named_among_nvidias_updates(tmp_path, release_server):
    env = fresh_gb10(tmp_path)
    (tmp_path / "upgrades").write_text(
        "Inst cuda-compat-13-0 [13.0.1-1] (13.0.2-1 cuda-ubuntu2404-sbsa [arm64])\n"
        "Inst libc6 [2.39-0ubuntu8.4] (2.39-0ubuntu8.5 Ubuntu:24.04/noble-updates [arm64])\n")
    result = run_install(tmp_path, release_server(node_release()), gpu="NVIDIA GB10", env_extra=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NVIDIA (DGX, CUDA, driver): cuda-compat-13-0\n" in result.stdout


def test_a_device_with_several_newer_firmware_releases_is_one_update(tmp_path, release_server):
    # fwupd lists every newer release of a device; `fwupdmgr update` installs one of them.
    env = fresh_gb10(tmp_path)
    one = '          "Version" : "1.22"\n        }\n'
    two = '          "Version" : "1.22"\n        },\n        {\n          "Version" : "1.21"\n        }\n'
    assert one in FIRMWARE_JSON
    (tmp_path / "firmware.json").write_text(FIRMWARE_JSON.replace(one, two))
    result = run_install(tmp_path, release_server(node_release()), gpu="NVIDIA GB10", env_extra=env)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    assert "2 firmware update(s)" in out and out.count("USB-C PD Controller:") == 1


def test_a_failed_step_is_named_in_the_summary_and_the_exit_code(tmp_path, release_server):
    (tmp_path / "groups-db").write_text("tester docker\n")
    result = run_install(tmp_path, release_server(node_release()), gpu="NVIDIA GB10",
                         env_extra={"FAKE_ADMIN_FAIL": "server start,host setup", "FAKE_GROUPS": "tester docker"})
    out = result.stdout
    assert result.returncode == 1
    assert "❌ model server: `ling-admin server start` failed, see" in out
    assert "❌ host settings" in out and "✅ advertised on the local network" in out
    assert "docker group: tester is in it" in out and not logged(tmp_path, "sg.log")


def test_a_hangup_does_not_stop_the_node_install_halfway(tmp_path, release_server):
    # From the virtualenv on, an ssh session that drops leaves the install to run to its summary.
    env = {**fresh_gb10(tmp_path), "FAKE_ADMIN_HANGUP": "host setup"}
    result = run_install(tmp_path, release_server(node_release()), gpu="NVIDIA GB10", env_extra=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert logged(tmp_path, "admin.log").splitlines()[-1] == "server start"
    assert "✅ model server started" in result.stdout


def test_opt_outs_are_said_as_such_and_need_no_root(tmp_path, release_server):
    result = run_install(tmp_path, release_server(node_release()), "--no-advertise", gpu="NVIDIA GB10")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "node enable" not in logged(tmp_path, "admin.log")
    assert "⚪ advertising: --no-advertise" in result.stdout
    assert "🎉 Done. `ling` is the terminal agent." in result.stdout and "other computers" not in result.stdout
    # What `node provision` runs: no root at all, so no password and no update check, and no model.
    home = tmp_path / "provisioned"
    home.mkdir()
    result = run_install(home, release_server(node_release()), "--role", "node", "--no-advertise",
                         "--no-host-setup", "--no-model", env_extra={"FAKE_SUDO": "password"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert logged(home, "sudo.log") == "" and logged(home, "apt.log") == ""
    assert logged(home, "admin.log").splitlines() == ["node id"]
    assert "skipped" not in result.stdout.lower()


def test_without_host_setup_python3_venv_is_installed_only_by_a_sudo_that_asks_nothing(tmp_path, release_server):
    # What `node provision` runs, with no terminal: a sudo that wants a password is not asked,
    # and the missing python3-venv is named instead.
    env = {"FAKE_NO_VENV": "1", "FAKE_SUDO": "password"}
    result = run_install(tmp_path, release_server(node_release()), "--role", "node", "--no-advertise",
                         "--no-host-setup", "--no-model", env_extra=env)
    assert result.returncode == 1
    assert "install python3-venv (sudo apt install python3-venv)" in result.stderr
    assert logged(tmp_path, "apt.log") == ""


WITH_DOCS = ("ling", "codex-code-mode-host", "ling-search", "ling-fetch", "ling-code", "ling-docs")


def test_a_node_installs_the_file_index_and_what_it_loads(tmp_path, release_server):
    # ling-docs is a binary of the release; its PDFium, ONNX Runtime and model (about 320 MB)
    # come from `ling-admin docs setup`, before the main model.
    (tmp_path / "groups-db").write_text("tester docker\n")
    result = run_install(tmp_path, release_server(node_release(names=WITH_DOCS)), gpu="NVIDIA GB10",
                         env_extra={"FAKE_GROUPS": "tester docker"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".local/bin/ling-docs").is_symlink()
    admin = logged(tmp_path, "admin.log").splitlines()
    assert admin == ["host setup --yes", "node id", "node enable --yes", "docs setup", "model download", "server start"]
    assert "✅ local file index: PDFium, ONNX Runtime and its embedding model installed" in result.stdout
    # A failed download is a failed step, named, and the rest goes on.
    home = tmp_path / "failing"
    home.mkdir()
    (home / "groups-db").write_text("tester docker\n")
    result = run_install(home, release_server(node_release(names=WITH_DOCS)), gpu="NVIDIA GB10",
                         env_extra={"FAKE_GROUPS": "tester docker", "FAKE_ADMIN_FAIL": "docs setup"})
    assert result.returncode == 1
    assert "❌ local file index: `ling-admin docs setup` failed, see" in result.stdout
    assert "server start" in logged(home, "admin.log")
    # --no-model (node provision) leaves it to `ling-admin docs setup`, and says so.
    home = tmp_path / "provisioned"
    home.mkdir()
    result = run_install(home, release_server(node_release(names=WITH_DOCS)), "--role", "node", "--no-advertise",
                         "--no-host-setup", "--no-model")
    assert result.returncode == 0, result.stdout + result.stderr
    assert logged(home, "admin.log").splitlines() == ["node id"]
    assert "⚪ local file index: --no-model" in result.stdout


def test_a_mac_never_asks_for_the_file_index(tmp_path, release_server):
    server = release_server(binaries(WITH_DOCS, target="aarch64-apple-darwin"))
    result = run_install(tmp_path, server, "--role", "client", uname_s="Darwin", uname_m="arm64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any("ling-docs" in request for request in server.requests)
    assert not (tmp_path / ".local/bin/ling-docs").exists()


def test_a_client_needs_no_root(tmp_path, release_server):
    result = run_install(tmp_path, release_server(binaries()), "--role", "client",
                         env_extra={"FAKE_SUDO": "password"})
    assert result.returncode == 0 and logged(tmp_path, "sudo.log") == "" and logged(tmp_path, "apt.log") == ""


def test_a_node_reinstalls_a_wheel_of_the_same_version(tmp_path, release_server):
    # `node provision` reinstalls whenever the bundle differs, and a bundle of a checkout carries
    # setup.py's version, the one already installed: `pip install --upgrade` alone keeps the old
    # code then ("is already installed with the same version"), so the wheel itself is forced.
    result = run_install(tmp_path, release_server(node_release()), "--role", "node", "--no-advertise",
                         "--no-host-setup", "--no-model")
    assert result.returncode == 0, result.stdout + result.stderr
    pip = [line.split() for line in logged(tmp_path, "pip.log").splitlines() if line.startswith("-m pip install")]
    wheel = [args for args in pip if args[-1].endswith("/dreamference-9.9.9-py3-none-any.whl")]
    assert any("--upgrade" in args and "--no-deps" not in args for args in wheel), pip
    assert any("--force-reinstall" in args and "--no-deps" in args for args in wheel), pip


def test_a_bundle_with_a_wheelhouse_installs_the_node_without_a_package_index(tmp_path, release_server):
    # `node provision` brings the Python dependencies in the bundle: pip asks no index at all.
    bundle = tmp_path / "bundle"
    (bundle / "wheelhouse").mkdir(parents=True)
    for name, body in node_release().items():
        (bundle / name).write_bytes(body)
    server = release_server({})
    result = run_install(tmp_path, server, "--from", str(bundle), "--role", "node", "--no-advertise",
                         "--no-host-setup", "--no-model")
    assert result.returncode == 0, result.stdout + result.stderr
    pip = [line.split() for line in logged(tmp_path, "pip.log").splitlines() if line.startswith("-m pip install")]
    wheelhouse = str(bundle.resolve() / "wheelhouse")
    assert len(pip) == 2 and all("--no-index" in args and wheelhouse in args for args in pip), pip
    assert server.requests == []


def test_an_option_without_its_value_is_named(tmp_path, release_server):
    # `shift 2` past the end used to end the script under `set -e` with no word.
    for option in ("--role", "--version", "--from"):
        result = run_install(tmp_path, release_server(binaries()), option)
        assert result.returncode == 1
        assert f"{option} needs a value" in result.stderr, result.stderr


def test_help_under_a_pipe_says_where_the_help_is(tmp_path):
    # Under `curl … | bash -s -- --help` there is no file to read the header from: awk was given
    # `bash` and the script ended with awk's error and exit 2.
    with INSTALL_SH.open("rb") as script:
        result = subprocess.run(["bash", "-s", "--", "--help"], stdin=script, cwd=tmp_path, capture_output=True,
                                text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert "bash install.sh --help" in result.stdout and result.stderr == ""


def test_help_from_the_file_prints_its_header(tmp_path):
    result = subprocess.run(["bash", str(INSTALL_SH), "--help"], cwd=tmp_path, capture_output=True, text=True,
                            timeout=30, check=False)
    assert result.returncode == 0 and result.stdout.startswith("Installs Mightling from a release")


def test_the_docker_group_note_names_sg_only_when_the_model_used_it(tmp_path, release_server):
    # With --no-model nothing runs through `sg docker`, yet the note said this script had used it.
    result = run_install(tmp_path, release_server(node_release()), "--role", "node", "--no-model",
                         env_extra={"FAKE_GROUPS": "tester"})
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    assert "docker group: tester added" in out and "sg docker" not in out
    assert not logged(tmp_path, "sg.log")


def test_the_node_role_needs_the_wheel(tmp_path, release_server):
    # The binaries are placed first; a release with no wheel then stops the node half by name.
    result = run_install(tmp_path, release_server(binaries()), "--role", "node")
    assert result.returncode == 1
    assert "no Python wheel" in result.stderr
    assert "not a GB10" in result.stdout


# -- the builder where there is nothing to build from ------------------------------------------------

@pytest.fixture
def release_install(tmp_path, monkeypatch):
    """The package as a wheel leaves it: no patches, no crates, binaries under the install dir."""
    for name in ("CODEX_PATCH_DIR", "MIGHTLING_CRATE_DIR", "CODEX_SUBMODULE_DIR", "WEB_CRATE_DIR", "CODE_CRATE_DIR", "DOCS_CRATE_DIR"):
        monkeypatch.setattr(codex_branded_builder, name, str(tmp_path / "site-packages" / name.lower()))
    install_dir = tmp_path / "install"
    monkeypatch.setattr(codex_branded_builder, "INSTALL_DIR", str(install_dir))
    for name in ("PATH_LINK", "ADMIN_PATH_LINK", "SEARCH_PATH_LINK", "FETCH_PATH_LINK", "CODE_PATH_LINK", "DOCS_PATH_LINK", "DELEGATE_PATH_LINK"):
        monkeypatch.setattr(codex_branded_builder, name, str(tmp_path / "bin" / name.lower()))

    def place(*names):
        (install_dir / "bin").mkdir(parents=True, exist_ok=True)
        for name in names:
            path = install_dir / "bin" / name
            path.write_text("#!/bin/sh\n")
            path.chmod(0o755)
    return place


def test_a_release_install_is_current_and_never_starts_a_build(release_install, monkeypatch):
    # Measured with the v1.3.0 wheel: `ling-admin run` installed rustup and then died on the
    # missing ling-web-rs/ directory.
    def no_build(*args, **kwargs):
        raise AssertionError("a release install must not run cargo or install Rust")
    monkeypatch.setattr(subprocess, "call", no_build)
    monkeypatch.setattr(DesktopInstaller, "install_rust", no_build)

    assert not CodexBrandedBuilder.has_source()
    assert not CodexInstaller.is_installed()
    assert CodexBrandedBuilder.build() is False          # nothing installed: says how to install

    release_install("ling", "codex-code-mode-host", "ling-search", "ling-fetch")
    assert CodexBrandedBuilder.is_current() and CodexBrandedBuilder.web_tools_are_current()
    assert CodexInstaller.is_installed() and CodexInstaller.install_if_missing()
    assert CodexBrandedBuilder.build() is True           # nothing to build, links refreshed


def test_building_without_source_says_how_to_install(release_install, capsys):
    assert CodexBrandedBuilder.build(force=True) is False
    output = capsys.readouterr().out
    assert "not a checkout" in output and "install.sh" in output


def test_the_desktop_app_is_not_built_without_its_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("dreamference.chat.desktop_runner.DESKTOP_PROJECT_DIR", str(tmp_path / "desktop"))
    monkeypatch.setattr("dreamference.chat.desktop_runner.ELECTRON_DIR", tmp_path / "desktop" / "electron")
    monkeypatch.setattr(DesktopRunner, "_ensure_toolchain", lambda: pytest.fail("no toolchain is needed"))
    assert not DesktopRunner.has_source()
    assert DesktopRunner.build() == 1 and DesktopRunner.install() == 1
    assert "Mightling .deb" in capsys.readouterr().out
    # With the release's .deb installed, `desktop run` opens that app.
    monkeypatch.setattr("dreamference.chat.desktop_runner.shutil.which", lambda name: "/usr/bin/ling-app")
    calls = []
    monkeypatch.setattr("dreamference.chat.desktop_runner.subprocess.call", lambda command, **kw: calls.append(command) or 0)
    assert DesktopRunner.run() == 0 and calls == [["/usr/bin/ling-app"]]


# -- ling-admin host -------------------------------------------------------------------------------

@pytest.fixture
def host(monkeypatch):
    """A host whose readings the test sets; by default one that passes every check."""
    state = {"sar": True, "oom_problem": None, "earlyoom": ["earlyoom", "-m", "5,2", "-s", "100"],
             "swap_gb": 64.0, "areas": [{"name": "/swap.img", "type": "file", "size_gb": 64.0, "used_gb": 1.0}],
             "sysctl": {"vm.min_free_kbytes": 1_048_576, "vm.watermark_scale_factor": 200},
             "disk_free_gb": 300.0, "mem_available_gb": 60.0, "swap_file_exists": True, "sandbox": True,
             "missing": set(), "docker_access": True, "swap_file_is_swap": False, "swap_file_gb": 16.0}
    monkeypatch.setattr(HostSafetySetup, "is_swap_file", classmethod(lambda cls, path: state["swap_file_is_swap"]))
    monkeypatch.setattr(HostSafetySetup, "_file_gb", classmethod(lambda cls, path: state["swap_file_gb"]))
    monkeypatch.setattr(HostSafetySetup, "sandbox_works", classmethod(lambda cls: state["sandbox"]))
    monkeypatch.setattr(host_safety_setup.shutil, "which",
                        lambda name: None if name in state["missing"] or (name == "sar" and not state["sar"])
                        else "/usr/bin/" + name)
    monkeypatch.setattr(HostSafetySetup, "_may_use_docker", classmethod(lambda cls: state["docker_access"]))
    monkeypatch.setattr(HostSafetySetup, "_user", classmethod(lambda cls: "someone"))
    monkeypatch.setattr(VLLMServerManager, "_oom_handler_problem", classmethod(lambda cls: state["oom_problem"]))
    monkeypatch.setattr(VLLMServerManager, "_process_argv", staticmethod(lambda name: state["earlyoom"]))
    monkeypatch.setattr(VLLMServerManager, "_swap_total_gb", staticmethod(lambda: state["swap_gb"]))
    monkeypatch.setattr(VLLMServerManager, "_sysctl_int", staticmethod(lambda name: state["sysctl"].get(name)))
    monkeypatch.setattr(HostSafetySetup, "swap_areas", classmethod(lambda cls: state["areas"]))
    monkeypatch.setattr(HostSafetySetup, "_disk_free_gb", classmethod(lambda cls, path: state["disk_free_gb"]))
    monkeypatch.setattr(HostSafetySetup, "_mem_available_gb", classmethod(lambda cls: state["mem_available_gb"]))
    monkeypatch.setattr(host_safety_setup.os.path, "exists",
                        lambda path: state["swap_file_exists"] if path == "/swap.img" else os.path.lexists(path))
    # The machine's own /etc/apparmor.d must not decide a test: a CI runner's image has a profile
    # for bwrap where this machine has none, and the fix then loads that one instead of ours.
    monkeypatch.setattr(SandboxPrerequisite, "profiles_attaching", classmethod(lambda cls, binary: []))
    return state


def commands_of(step):
    return [" ".join(command) for command in step["commands"]]


def test_a_host_that_passes_needs_nothing_and_runs_no_sudo(host, monkeypatch, capsys):
    monkeypatch.setattr(host_safety_setup.subprocess, "run", lambda *a, **k: pytest.fail("sudo must not run"))
    assert HostSafetySetup.steps() == []
    assert HostSafetySetup.check() is True and HostSafetySetup.setup() is True
    assert "nothing to do" in capsys.readouterr().out


def test_a_fresh_machine_gets_every_fix_the_check_asks_for(host):
    host.update(sar=False, oom_problem="No userspace OOM handler is running.", earlyoom=None,
                swap_gb=8.0, areas=[{"name": "/swap.img", "type": "file", "size_gb": 8.0, "used_gb": 0.5}],
                sysctl={"vm.min_free_kbytes": 45_056, "vm.watermark_scale_factor": 10})
    steps = HostSafetySetup.steps()
    assert [step["name"] for step in steps] == [
        "install sysstat", "install and arm earlyoom", "raise swap to 64 GB",
        "set vm.min_free_kbytes=1048576", "set vm.watermark_scale_factor=200"]
    assert commands_of(steps[0])[0] == "apt-get install -y sysstat"
    # earlyoom is installed, given arguments that can fire on this hardware, then restarted.
    earlyoom = commands_of(steps[1])
    assert earlyoom[0] == "apt-get install -y earlyoom" and earlyoom[-1] == "systemctl restart earlyoom"
    assert "-m 5,2 -s 100 -r 60" in earlyoom[1] and "/etc/default/earlyoom" in earlyoom[1]
    swap = commands_of(steps[2])
    assert swap[:5] == ["swapoff /swap.img", "fallocate -l 64G /swap.img", "chmod 600 /swap.img",
                        "mkswap /swap.img", "swapon /swap.img"]
    assert "/etc/fstab" in swap[5]
    assert commands_of(steps[3])[0] == "sysctl -w vm.min_free_kbytes=1048576"
    assert "/etc/sysctl.d/99-dreamference.conf" in commands_of(steps[3])[1]


def test_a_misconfigured_earlyoom_is_rearmed_not_reinstalled(host):
    host.update(oom_problem="earlyoom is running but is misconfigured", earlyoom=["earlyoom", "-m", "10"])
    (step,) = HostSafetySetup.steps()
    assert step["name"] == "arm earlyoom"
    assert not any("apt-get" in command for command in commands_of(step))


def test_no_swap_at_all_creates_the_file_without_a_swapoff(host):
    host.update(swap_gb=0.0, areas=[], swap_file_exists=False)
    (step,) = HostSafetySetup.steps()
    assert commands_of(step)[0] == "fallocate -l 64G /swap.img"


@pytest.mark.parametrize("change, said", [
    ({"areas": [{"name": "/dev/zram0", "type": "partition", "size_gb": 8.0, "used_gb": 0.0},
                {"name": "/dev/nvme0n1p3", "type": "partition", "size_gb": 8.0, "used_gb": 0.0}]}, "not resized automatically"),
    ({"areas": [{"name": "/swap.img", "type": "file", "size_gb": 8.0, "used_gb": 0.0},
                {"name": "/dev/nvme0n1p3", "type": "partition", "size_gb": 8.0, "used_gb": 0.0}]}, "not resized automatically"),
    ({"areas": [], "swap_file_exists": True}, "exists but is not in use"),
    ({"disk_free_gb": 30.0}, "Free some space first"),
    ({"areas": [{"name": "/swap.img", "type": "file", "size_gb": 8.0, "used_gb": 7.5}], "mem_available_gb": 6.0},
     "would not fit back into memory"),
])
def test_swap_that_is_not_safe_to_resize_is_explained_not_touched(host, change, said):
    host.update(swap_gb=8.0, areas=[{"name": "/swap.img", "type": "file", "size_gb": 8.0, "used_gb": 0.5}])
    host.update(change)
    (step,) = HostSafetySetup.steps()
    assert "commands" not in step and said in step["manual"]


def test_zram_is_neither_counted_nor_in_the_way_of_the_swap_file(host):
    # zram keeps its pages in the RAM a model load is short of; a machine with it beside (or
    # instead of) the swap file still gets the file.
    zram = {"name": "/dev/zram0", "type": "partition", "size_gb": 8.0, "used_gb": 0.0}
    host.update(swap_gb=8.0, areas=[{"name": "/swap.img", "type": "file", "size_gb": 8.0, "used_gb": 0.5}, zram])
    (step,) = HostSafetySetup.steps()
    assert commands_of(step)[:2] == ["swapoff /swap.img", "fallocate -l 64G /swap.img"]
    host.update(swap_gb=0.0, areas=[zram], swap_file_exists=False)
    (step,) = HostSafetySetup.steps()
    assert commands_of(step)[0] == "fallocate -l 64G /swap.img"


def test_the_swap_check_counts_only_swap_on_disk(tmp_path, monkeypatch):
    real_open = open
    (tmp_path / "swaps").write_text(
        "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
        "/swap.img                               file\t\t67108860\t4334852\t\t-2\n"
        "/dev/zram0                              partition\t67108860\t0\t\t100\n")
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: real_open(
        tmp_path / "swaps" if path == "/proc/swaps" else path, *a, **k))
    assert round(VLLMServerManager._swap_total_gb()) == 64          # not 128: zram is not counted


def test_a_machine_installed_as_plain_ubuntu_is_told_what_dgx_os_would_have_had(host):
    # DGX OS ships Docker and the NVIDIA Container Toolkit; Ubuntu does not, and installing either
    # adds a vendor repository, so both are explained rather than done.
    host["missing"] = {"docker"}
    (step,) = HostSafetySetup.steps()
    assert "commands" not in step and "docs.docker.com" in step["manual"] and "container-toolkit" in step["manual"]
    host["missing"] = {"nvidia-ctk", "nvidia-container-runtime-hook", "nvidia-container-cli"}
    (step,) = HostSafetySetup.steps()
    assert step["name"] == "install the NVIDIA Container Toolkit" and "nvidia-ctk runtime configure" in step["manual"]
    host["missing"] = {"nvidia-container-runtime-hook", "nvidia-container-cli"}     # nvidia-ctk alone is enough
    assert HostSafetySetup.steps() == []


def test_a_user_outside_the_docker_group_is_added_to_it(host):
    host["docker_access"] = False
    (step,) = HostSafetySetup.steps()
    assert commands_of(step) == ["usermod -aG docker someone"] and "next login" in step["why"]


def test_docker_access_is_read_from_the_socket_then_the_group(monkeypatch, tmp_path):
    socket = tmp_path / "docker.sock"
    socket.write_text("")
    monkeypatch.setattr(host_safety_setup, "DOCKER_SOCKET", str(socket))
    monkeypatch.setattr(HostSafetySetup, "_user", classmethod(lambda cls: "someone"))
    monkeypatch.setattr(host_safety_setup.os, "access", lambda path, mode: False)
    group = type("Group", (), {"gr_mem": ["other"]})
    monkeypatch.setattr(host_safety_setup.grp, "getgrnam", lambda name: group)
    assert HostSafetySetup._may_use_docker() is False
    group.gr_mem = ["other", "someone"]
    assert HostSafetySetup._may_use_docker() is True
    monkeypatch.setattr(host_safety_setup.os, "access", lambda path, mode: True)
    group.gr_mem = []
    assert HostSafetySetup._may_use_docker() is True
    monkeypatch.setattr(host_safety_setup, "DOCKER_SOCKET", str(tmp_path / "absent.sock"))
    assert HostSafetySetup._may_use_docker() is True       # no daemon here: nothing to join


def test_a_machine_without_bubblewrap_gets_it_and_its_profile(host, monkeypatch):
    # Ubuntu Server has no bubblewrap (on DGX OS, GNOME brings it in).
    host["missing"] = {"bwrap"}
    host["sandbox"] = None
    monkeypatch.setattr(SandboxPrerequisite, "_userns_restricted", classmethod(lambda cls: True))
    (step,) = HostSafetySetup.steps()
    commands = commands_of(step)
    assert step["name"] == "install bubblewrap" and commands[0] == "apt-get install -y bubblewrap"
    assert commands[1].endswith("/etc/apparmor.d/puffin-bwrap") and commands[2].startswith("apparmor_parser -r")
    monkeypatch.setattr(SandboxPrerequisite, "_userns_restricted", classmethod(lambda cls: False))
    (step,) = HostSafetySetup.steps()
    assert commands_of(step) == ["apt-get install -y bubblewrap"]


def test_a_sandbox_that_only_works_under_the_ide_is_fixed_with_an_apparmor_profile(host, monkeypatch, capsys):
    # Measured 2026-10-02: bwrap worked from the IDE's terminal (a snap's AppArmor label) and from
    # nowhere else, so a release install typed into a plain terminal had no working sandbox.
    host["sandbox"] = False
    monkeypatch.setattr(SandboxPrerequisite, "_userns_restricted", classmethod(lambda cls: True))
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: True, raising=False)
    (step,) = HostSafetySetup.steps()
    install, load = step["commands"]
    assert install[:2] == ["install", "-m"] and install[-1] == "/etc/apparmor.d/puffin-bwrap"
    assert "profile puffin-bwrap" in Path(install[-2]).read_text() and load == ["apparmor_parser", "-r", "/etc/apparmor.d/puffin-bwrap"]
    ran = []

    def fake_run(command, **kwargs):
        ran.append(command)
        if command[-1] == "/etc/apparmor.d/puffin-bwrap" and command[1] == "apparmor_parser":
            host["sandbox"] = True
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(host_safety_setup.subprocess, "run", fake_run)
    SandboxPrerequisite.decision_path().parent.mkdir(parents=True, exist_ok=True)
    SandboxPrerequisite.decision_path().write_text('{"sandbox": "off"}')
    assert HostSafetySetup.setup() is True
    assert ran == [["sudo", *install], ["sudo", *load]]
    assert not SandboxPrerequisite.decision_path().exists()     # the "turn it off" answer is void
    assert "night enable" in capsys.readouterr().out
    host["sandbox"] = None          # cannot be tried (no bwrap, no user systemd): nothing reported
    assert HostSafetySetup.steps() == []


def test_a_sandbox_refused_for_another_reason_is_explained_not_changed(host, monkeypatch):
    host["sandbox"] = False
    monkeypatch.setattr(SandboxPrerequisite, "_userns_restricted", classmethod(lambda cls: False))
    (step,) = HostSafetySetup.steps()
    assert "commands" not in step and "user.max_user_namespaces" in step["manual"]


@pytest.mark.parametrize("stderr, code, expected", [
    ("", 0, True), ("bwrap: setting up uid map: Permission denied\n", 1, False),
    ("bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n", 0, False),
    ("Failed to connect to bus: No medium found\n", 1, None),
])
def test_the_sandbox_probe_reads_bubblewraps_own_complaint(monkeypatch, stderr, code, expected):
    monkeypatch.setattr(host_safety_setup.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(host_safety_setup.subprocess, "run",
                        lambda command, **kw: subprocess.CompletedProcess(command, code, "", stderr))
    assert HostSafetySetup.sandbox_works() is expected


def test_setup_runs_each_command_through_sudo_and_reads_the_host_again(host, monkeypatch, capsys):
    host["sysctl"]["vm.watermark_scale_factor"] = 10
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: True, raising=False)
    ran = []

    def fake_run(command, **kwargs):
        ran.append(command)
        if command[:3] == ["sudo", "sysctl", "-w"]:
            host["sysctl"]["vm.watermark_scale_factor"] = 200
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(host_safety_setup.subprocess, "run", fake_run)
    assert HostSafetySetup.setup() is True
    assert [command[:2] for command in ran] == [["sudo", "sysctl"], ["sudo", "sh"]]
    output = capsys.readouterr().out
    assert "sudo sysctl -w vm.watermark_scale_factor=200" in output and "now has what a model load" in output


def test_a_failed_command_stops_its_step_and_setup_reports_what_remains(host, monkeypatch, capsys):
    host["sysctl"]["vm.watermark_scale_factor"] = 10
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: True, raising=False)
    ran = []
    monkeypatch.setattr(host_safety_setup.subprocess, "run",
                        lambda command, **kw: ran.append(command) or subprocess.CompletedProcess(command, 1))
    assert HostSafetySetup.setup() is False
    assert len(ran) == 1                                   # the second command of the step did not run
    assert "still to fix: set vm.watermark_scale_factor=200" in capsys.readouterr().out


def test_without_a_terminal_setup_prints_the_commands_and_changes_nothing(host, monkeypatch, capsys):
    host["sar"] = False
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(host_safety_setup.subprocess, "run", lambda *a, **k: pytest.fail("sudo must not run"))
    assert HostSafetySetup.setup() is False
    output = capsys.readouterr().out
    assert "nothing was changed" in output and "sudo apt-get install -y sysstat" in output


def recording_sudo(host, monkeypatch, ran):
    """A subprocess.run that records each command and makes the sysctl one take effect."""
    def fake_run(command, **kwargs):
        ran.append((command, kwargs))
        if "sysctl" in command and "-w" in command:
            host["sysctl"]["vm.watermark_scale_factor"] = 200
        return subprocess.CompletedProcess(command, 0, "done\n")
    monkeypatch.setattr(host_safety_setup.subprocess, "run", fake_run)


def test_setup_with_yes_runs_through_sudo_n_with_no_terminal(host, monkeypatch, capsys):
    # 2026-10-08: on a GB10 installed over SSH with no terminal, `host setup` only printed its
    # commands, although sudo needed no password there. --yes is what install.sh runs.
    host["sysctl"]["vm.watermark_scale_factor"] = 10
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(HostSafetySetup, "passwordless_sudo", classmethod(lambda cls: True))
    ran = []
    recording_sudo(host, monkeypatch, ran)
    assert HostSafetySetup.setup(yes=True) is True
    assert [command[:3] for command, _ in ran] == [["sudo", "-n", "sysctl"], ["sudo", "-n", "sh"]]
    # Out of reach of a closed pipe (output collected here) and of any prompt (no stdin); still on
    # the terminal, whose sudo timestamp install.sh keeps fresh.
    assert all(kw["stdout"] == subprocess.PIPE and kw["stdin"] == subprocess.DEVNULL
               and not kw["start_new_session"] for _, kw in ran)
    assert "done" in capsys.readouterr().out


def test_setup_with_yes_never_asks_for_a_password_even_at_a_terminal(host, monkeypatch, capsys):
    host["sar"] = False
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(host_safety_setup.subprocess, "run", lambda *a, **k: pytest.fail("sudo must not run"))
    assert HostSafetySetup.setup(yes=True) is False
    output = capsys.readouterr().out
    assert "--yes never waits" in output and "sudo apt-get install -y sysstat" in output


def test_without_a_terminal_setup_still_runs_when_sudo_needs_no_password(host, monkeypatch):
    host["sysctl"]["vm.watermark_scale_factor"] = 10
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(HostSafetySetup, "passwordless_sudo", classmethod(lambda cls: True))
    ran = []
    recording_sudo(host, monkeypatch, ran)
    assert HostSafetySetup.setup() is True
    assert ran and all(command[:2] == ["sudo", "-n"] for command, _ in ran)


def test_a_reader_that_goes_away_does_not_stop_setup_between_two_commands(host, monkeypatch):
    # 2026-10-08: `host setup | head` died of the closed pipe after `swapoff` and `fallocate`,
    # before `mkswap`, and left the machine with no swap.
    host.update(swap_gb=16.0, areas=[{"name": "/swap.img", "type": "file", "size_gb": 16.0, "used_gb": 0.0}])
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: True, raising=False)

    class ClosedPipe:
        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")
    monkeypatch.setattr(host_safety_setup.sys, "stdout", ClosedPipe())
    ran = []
    recording_sudo(host, monkeypatch, ran)
    HostSafetySetup.setup()
    assert [command[1] for command, _ in ran][:5] == ["swapoff", "fallocate", "chmod", "mkswap", "swapon"]


def test_a_swap_file_left_off_by_an_interrupted_resize_is_finished(host):
    # What the second GB10 was left with: /swap.img grown to 64 GB, its old header, not in use.
    host.update(swap_gb=0.0, areas=[], swap_file_exists=True, swap_file_is_swap=True, swap_file_gb=64.0,
                disk_free_gb=30.0)
    (step,) = HostSafetySetup.steps()
    assert "not in use" in step["why"]
    assert commands_of(step)[:4] == ["fallocate -l 64G /swap.img", "chmod 600 /swap.img",
                                     "mkswap /swap.img", "swapon /swap.img"]
    assert not any(command.startswith("swapoff") for command in commands_of(step))


def test_a_swap_file_is_known_by_fstab(tmp_path, monkeypatch):
    fstab = tmp_path / "fstab"
    fstab.write_text("# comment /swap.img none swap\nUUID=1 / ext4 defaults 0 1\n/swap.img\tnone\tswap\tsw\t0\t0\n")
    monkeypatch.setattr(host_safety_setup, "FSTAB", str(fstab))
    assert HostSafetySetup.is_swap_file("/swap.img") is True
    fstab.write_text("UUID=1 / ext4 defaults 0 1\n")
    monkeypatch.setattr(host_safety_setup.shutil, "which", lambda name: None)    # and no blkid
    assert HostSafetySetup.is_swap_file("/swap.img") is False


def test_node_enable_with_yes_needs_no_terminal_when_sudo_needs_no_password(monkeypatch, capsys):
    from conftest import REAL_RUN_PRIVILEGED
    from dreamference.node import NodeAdvertiser
    from dreamference.node import node_advertiser
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", REAL_RUN_PRIVILEGED)
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: True, raising=False)
    ran = []
    monkeypatch.setattr(node_advertiser.subprocess, "run",
                        lambda command, **kw: ran.append(command) or subprocess.CompletedProcess(command, 0))
    # sudo would ask: --yes runs nothing, even at a terminal.
    assert NodeAdvertiser.run_privileged(["true"], "test", yes=True) is False and ran == []
    assert "--yes never waits" in capsys.readouterr().out
    monkeypatch.setattr(HostSafetySetup, "passwordless_sudo", classmethod(lambda cls: True))
    assert NodeAdvertiser.run_privileged(["install", "x", "y"], "test", yes=True) is True
    assert ran == [["sudo", "-n", "install", "x", "y"]]
    # Without --yes and with no terminal, a sudo that needs no password is used too.
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: False, raising=False)
    assert NodeAdvertiser.run_privileged(["true"], "test") is True and ran[-1] == ["sudo", "-n", "true"]


def test_the_cli_passes_yes_to_host_setup_and_node_enable(monkeypatch):
    from dreamference.cli import main
    from dreamference.node import NodeAdvertiser
    calls = []
    monkeypatch.setattr(HostSafetySetup, "setup", classmethod(lambda cls, yes=False: calls.append(("setup", yes)) or True))
    monkeypatch.setattr(NodeAdvertiser, "enable",
                        classmethod(lambda cls, no_web=False, yes=False: calls.append(("enable", yes)) or True))
    for argv in (["host", "setup", "--yes"], ["node", "enable", "--yes"], ["host", "setup"]):
        monkeypatch.setattr("sys.argv", ["ling-admin", *argv])
        with pytest.raises(SystemExit):
            main()
    assert calls == [("setup", True), ("enable", True), ("setup", False)]


def test_swap_areas_are_read_from_proc_swaps(tmp_path, monkeypatch):
    real_open = open
    text = ("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
            "/swap.img                               file\t\t67108860\t4334852\t\t-2\n"
            "/dev/zram0                              partition\t8388604\t0\t\t100\n")
    (tmp_path / "swaps").write_text(text)
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: real_open(
        tmp_path / "swaps" if path == "/proc/swaps" else path, *a, **k))
    areas = HostSafetySetup.swap_areas()
    assert [(area["name"], area["type"]) for area in areas] == [("/swap.img", "file"), ("/dev/zram0", "partition")]
    assert round(areas[0]["size_gb"]) == 64 and round(areas[0]["used_gb"], 1) == 4.1


def test_the_refusal_names_the_command_that_fixes_it(monkeypatch, capsys):
    monkeypatch.setattr("dreamference.vllm_server.vllm_server_manager.shutil.which", lambda name: None)
    monkeypatch.setattr(VLLMServerManager, "_oom_handler_problem", classmethod(lambda cls: None))
    monkeypatch.setattr(VLLMServerManager, "_swap_total_gb", staticmethod(lambda: 64.0))
    monkeypatch.setattr(VLLMServerManager, "_sysctl_int", staticmethod(lambda name: None))
    with pytest.raises(SystemExit):
        VLLMServerManager.check_host_safety()
    assert "ling-admin host setup" in capsys.readouterr().out


def test_the_cli_has_the_host_commands(monkeypatch, capsys):
    from dreamference.cli import main
    monkeypatch.setattr(HostSafetySetup, "check", classmethod(lambda cls: True))
    monkeypatch.setattr("sys.argv", ["ling-admin", "host", "check"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0


# -- the token stays with the API (security review 2026-10) ----------------------------------------

def test_the_token_is_not_sent_to_download_urls_on_another_host(tmp_path, release_server):
    # The release JSON names where each asset downloads from. A token sent along to such a URL
    # would reach whoever controls it: a tampered release, or an old repository name taken over.
    downloads = release_server(binaries())
    api = release_server(binaries())
    api.document = lambda port: FakeRelease.document(api, downloads.server.server_address[1]).replace(
        f'"url": "http://127.0.0.1:{downloads.server.server_address[1]}/repos/test/ling/releases/1"',
        f'"url": "{api.url}/repos/test/ling/releases/1"')
    result = run_install(tmp_path, api, "--role", "client", token="sekret")
    assert result.returncode == 0, result.stderr
    assert any(header == "Bearer sekret" for _, header in api.requests)
    assert downloads.requests and all(header is None for _, header in downloads.requests)


def test_a_logged_in_ghs_token_is_asked_for_only_for_another_repository(tmp_path, release_server):
    # The public repository is read anonymously: a gh login usually carries far more access than
    # reading a release. A fork named by MIGHTLING_RELEASE_REPO may be private and needs it.
    for repo, asked in (("", False), ("dreamference/mightling", False), ("test/ling", True)):
        home = tmp_path / (repo.replace("/", "-") or "unset")
        home.mkdir()
        server = release_server(binaries())
        result = run_install(home, server, "--role", "client",
                             env_extra={"MIGHTLING_RELEASE_REPO": repo, "FAKE_GH_TOKEN": "gh-sekret"})
        assert result.returncode == 0, result.stderr
        assert ("gh auth token" in logged(home, "gh.log")) is asked, repo
        assert {header for _, header in server.requests} == {"Bearer gh-sekret" if asked else None}, repo


def test_a_wheel_that_does_not_match_the_release_sums_is_not_installed(tmp_path, release_server):
    assets = binaries(signed=False)
    wheel = "dreamference-9.9.9-py3-none-any.whl"
    assets[wheel] = b"not the wheel that was built"
    sign(assets, listed={wheel: hashlib.sha256(b"the wheel that was built").hexdigest()})
    result = run_install(tmp_path, release_server(assets), "--role", "node")
    assert result.returncode == 1
    assert "does not match its checksum in SHA256SUMS" in result.stderr
    assert not (tmp_path / ".local/share/dreamference/venv").exists()


def test_a_wheel_from_a_release_with_no_sums_is_installed_with_that_said(tmp_path, release_server):
    # A 1.4.x release has no release-wide SHA256SUMS: the missing check is said, never skipped silently.
    assets = binaries(signed=False)
    assets["dreamference-9.9.9-py3-none-any.whl"] = b"a wheel"
    result = run_install(tmp_path, release_server(assets, tag="v1.4.1"), "--role", "node", "--version", "1.4.1",
                         "--no-advertise", "--no-host-setup", "--no-model")
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("Release v1.4.1 has no SHA256SUMS, so dreamference-9.9.9-py3-none-any.whl is installed without "
            "a checksum check.") in result.stdout


# -- signed releases (specs/DREAMFERENCE_RELEASE_SIGNING.md) ----------------------------------------

def nothing_installed(home):
    return not (home / ".local/share/dreamference/mightling").exists() and not (home / ".local/bin").exists()


def test_the_installer_trusts_the_keys_ling_update_compiles_in():
    keys = [line for line in (INSTALL_SH.parent / "ling-rs/release-signing.pub").read_text().splitlines()
            if line.strip() and not line.startswith("#")]
    embedded = RELEASE_KEYS_LINE.search(INSTALL_SH.read_text()).group(0)
    assert keys and embedded == f"RELEASE_KEYS='{chr(10).join(keys)}'"


def test_an_unsigned_release_from_the_cut_over_on_is_refused(tmp_path, release_server):
    # 2.0.0: a later major release with a smaller minor number; vnext: a tag that is not a
    # version is never older than signing.
    for tag in ("v1.5.0", "v9.9.9", "v2.0.0", "vnext"):
        server = release_server(binaries(signed=False), tag=tag)
        result = run_install(tmp_path, server, "--role", "client", "--version", tag)
        assert result.returncode == 1, tag
        assert f"release {tag} is not signed" in result.stderr
        assert nothing_installed(tmp_path)


def test_an_unsigned_latest_release_is_refused_whatever_its_tag(tmp_path, release_server):
    # The tag is only a name: whoever can mark a release latest without the signing key could tag
    # it v1.4.9 and have it installed on its checksums alone (found 2026-10-29).
    # this-<digest> names a node's own build, which only a --from bundle may be.
    for tag in ("v1.4.1", "v1.5.0", "nightly", "this-0123abc"):
        result = run_install(tmp_path, release_server(binaries(signed=False), tag=tag), "--role", "client")
        assert result.returncode == 1, tag
        assert f"the latest release ({tag}) is not signed" in result.stderr
        assert "Pass --version" in result.stderr
        assert nothing_installed(tmp_path)


def test_a_release_without_its_signature_file_is_refused(tmp_path, release_server):
    assets = binaries()
    del assets["SHA256SUMS.sig"]
    result = run_install(tmp_path, release_server(assets), "--role", "client")
    assert result.returncode == 1 and "is not signed" in result.stderr
    assert nothing_installed(tmp_path)


def test_a_release_older_than_signing_installs_with_a_warning(tmp_path, release_server):
    server = release_server(binaries(signed=False), tag="v1.4.1")
    result = run_install(tmp_path, server, "--role", "client", "--version", "1.4.1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "predates signed releases (1.5.0)" in result.stdout
    assert (tmp_path / ".local/bin/ling").is_symlink()


def test_a_tampered_checksum_file_fails_the_signature_check(tmp_path, release_server):
    assets = binaries()
    assets["SHA256SUMS"] += b"0000  something-added-after-signing\n"
    result = run_install(tmp_path, release_server(assets), "--role", "client")
    assert result.returncode == 1
    assert "failed its signature check" in result.stderr
    assert nothing_installed(tmp_path)


def test_a_release_signed_by_another_key_is_refused(tmp_path, release_server):
    assets = binaries(signed=False)
    sign(assets, key="attacker")
    result = run_install(tmp_path, release_server(assets), "--role", "client")
    assert result.returncode == 1
    assert "failed its signature check" in result.stderr
    assert nothing_installed(tmp_path)


def test_binaries_swapped_with_their_sums_file_do_not_pass_the_signed_sums(tmp_path, release_server):
    # Someone who can replace release assets but has no key can make the binaries and their
    # per-target sums agree; the signed SHA256SUMS still names the original sums file.
    assets = binaries()
    replaced = binaries(signed=False)
    replaced[f"ling-{TARGET}.gz"] = gzip.compress(b"#!/bin/sh\necho not ours\n")
    replaced[f"ling-{TARGET}.sha256sums"] = replaced[f"ling-{TARGET}.sha256sums"].replace(
        hashlib.sha256(binaries(signed=False)[f"ling-{TARGET}.gz"]).hexdigest().encode(),
        hashlib.sha256(replaced[f"ling-{TARGET}.gz"]).hexdigest().encode())
    assets.update({name: body for name, body in replaced.items() if name.startswith("ling-")})
    result = run_install(tmp_path, release_server(assets), "--role", "client")
    assert result.returncode == 1
    assert f"ling-{TARGET}.sha256sums does not match its checksum in SHA256SUMS" in result.stderr
    assert nothing_installed(tmp_path)


def test_a_new_key_endorsed_by_the_current_one_is_accepted(tmp_path, release_server):
    next_key = KEYS["next"].with_suffix(".pub").read_bytes()
    assets = binaries(signed=False)
    assets["release-key-transition.pub"] = next_key
    assets["release-key-transition.pub.sig"] = ssh_sign(next_key, "release", "mightling-release-key")
    sign(assets, key="next")
    result = run_install(tmp_path, release_server(assets), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "signed by Mightling's release key" in result.stdout


def test_the_current_key_still_signs_a_release_that_names_the_next_one(tmp_path, release_server):
    next_key = KEYS["next"].with_suffix(".pub").read_bytes()
    assets = binaries(signed=False)
    assets["release-key-transition.pub"] = next_key
    assets["release-key-transition.pub.sig"] = ssh_sign(next_key, "release", "mightling-release-key")
    sign(assets, key="release")
    result = run_install(tmp_path, release_server(assets), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "signed by Mightling's release key" in result.stdout


def test_a_new_key_that_endorses_itself_is_refused(tmp_path, release_server):
    attacker = KEYS["attacker"].with_suffix(".pub").read_bytes()
    for endorser, namespace in (("attacker", "mightling-release-key"), ("release", "mightling-release")):
        assets = binaries(signed=False)
        assets["release-key-transition.pub"] = attacker
        # Signed by itself, or by the real key but under the checksums' namespace (a signature
        # over checksums must never pass as one over a key).
        assets["release-key-transition.pub.sig"] = ssh_sign(attacker, endorser, namespace)
        sign(assets, key="attacker")
        result = run_install(tmp_path, release_server(assets), "--role", "client")
        assert result.returncode == 1, (endorser, namespace)
        assert "no trusted key has signed" in result.stderr
        assert nothing_installed(tmp_path)


def test_without_ssh_keygen_a_signed_release_stops_and_names_the_package(tmp_path, release_server):
    # No opt-out (§3): a machine without OpenSSH's client is told what to install, never let past
    # the check. The PATH is every tool of /usr/bin but ssh-keygen.
    tools = tmp_path / "tools-without-ssh-keygen"
    tools.mkdir()
    for tool in Path("/usr/bin").iterdir():
        if tool.name != "ssh-keygen":
            (tools / tool.name).symlink_to(tool)
    result = run_install(tmp_path, release_server(binaries()), "--role", "client",
                         env_extra={"PATH": f"{tmp_path / 'fakebin'}:{tools}"})
    assert result.returncode == 1
    assert "ssh-keygen (OpenSSH 8.1 or newer) is needed" in result.stderr
    assert "openssh-client" in result.stderr
    assert nothing_installed(tmp_path)


def test_a_signature_without_the_file_it_signs_is_refused(tmp_path, release_server):
    assets = binaries()
    del assets["SHA256SUMS"]
    result = run_install(tmp_path, release_server(assets), "--role", "client")
    assert result.returncode == 1
    assert "has SHA256SUMS.sig but no SHA256SUMS" in result.stderr
    assert nothing_installed(tmp_path)


def test_a_new_key_whose_endorsement_is_missing_is_refused(tmp_path, release_server):
    # The new key alone, without the current key's signature over it, earns no trust.
    assets = binaries(signed=False)
    assets["release-key-transition.pub"] = KEYS["next"].with_suffix(".pub").read_bytes()
    sign(assets, key="next")
    result = run_install(tmp_path, release_server(assets), "--role", "client")
    assert result.returncode == 1
    assert "its endorsement could not be downloaded" in result.stderr
    assert nothing_installed(tmp_path)
