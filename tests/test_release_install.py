"""Installing Mightling from a release, with no checkout (install.sh, `ling-admin host`, and the
builder on a machine that has no source).

`install.sh` runs for real against a stand-in for GitHub's release API on loopback, into the
test's own HOME. Nothing here runs sudo, docker, cargo or pip.
"""

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dreamference.chat.desktop_runner import DesktopRunner
from dreamference.runner import codex_branded_builder
from dreamference.runner.codex_branded_builder import CodexBrandedBuilder
from dreamference.runner.codex_installer import CodexInstaller
from dreamference.vllm_server import HostSafetySetup, SandboxPrerequisite, VLLMServerManager
from dreamference.vllm_server import host_safety_setup

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"
TARGET = "aarch64-unknown-linux-gnu"


# -- a stand-in for GitHub's releases API ----------------------------------------------------------

class FakeRelease:
    """Serves one release the way api.github.com prints it: pretty JSON, an asset's `url` before
    its `name`, and the uploader's own `url` between them."""

    def __init__(self, assets, tag="v9.9.9", need_token=None):
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
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

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
        }, indent=2)

    def close(self):
        self.server.shutdown()


def binaries(names=("ling", "codex-code-mode-host", "ling-search", "ling-fetch"), corrupt=None):
    """Release assets for `names`: gzipped scripts that print their own name, and the sums file."""
    assets, sums = {}, []
    for name in names:
        archive = gzip.compress(f"#!/bin/sh\necho {name} from the release\n".encode())
        sums.append(f"{hashlib.sha256(archive).hexdigest()}  {name}-{TARGET}.gz")
        assets[f"{name}-{TARGET}.gz"] = archive + (b"tampered" if name == corrupt else b"")
    assets[f"ling-{TARGET}.sha256sums"] = ("\n".join(sums) + "\n").encode()
    return assets


@pytest.fixture
def release_server():
    servers = []

    def start(*args, **kwargs):
        servers.append(FakeRelease(*args, **kwargs))
        return servers[-1]
    yield start
    for server in servers:
        server.close()


def run_install(home, server, *args, token=None, uname_m="aarch64", gpu="Some Other GPU", pci=(),
                nvidia_smi=True):
    """Runs install.sh with a `uname`, an `nvidia-smi` and a PCI bus that report the machine the
    test wants, whatever runs the tests (on a GB10 the real bus has the GB10's GPU on it)."""
    fake_bin = Path(home) / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    # Without a driver nvidia-smi fails; it is never left out of the fake bin, where the real one
    # in /usr/bin would answer for this machine's GPU.
    tools = [("uname", f'case "$1" in -s) echo Linux;; -m) echo {uname_m};; *) echo Linux;; esac'),
             ("nvidia-smi", f'echo "{gpu}"' if nvidia_smi else 'echo "NVIDIA-SMI has failed" >&2; exit 9')]
    for name, script in tools:
        (fake_bin / name).write_text(f"#!/bin/sh\n{script}\n")
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
           "MIGHTLING_RELEASE_API": server.url, "MIGHTLING_RELEASE_REPO": "test/ling"}
    if token:
        env["GH_TOKEN"] = token
    return subprocess.run(["bash", str(INSTALL_SH), *args], env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=120, check=False)


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
    # Nothing of the node: no virtualenv, no ling-admin.
    assert not (tmp_path / ".local/share/dreamference/venv").exists()


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


def test_a_download_that_fails_its_checksum_installs_nothing(tmp_path, release_server):
    # The last archive is the tampered one: the ones before it passed, and must not be placed.
    result = run_install(tmp_path, release_server(binaries(corrupt="ling-fetch")), "--role", "client")
    assert result.returncode == 1
    assert "does not match its checksum" in result.stderr
    assert not (tmp_path / ".local/share/dreamference/mightling").exists()
    assert not (tmp_path / ".local/bin").exists()


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


def test_a_named_version_is_fetched_by_its_tag(tmp_path, release_server):
    server = release_server(binaries(), tag="v1.4.0")
    result = run_install(tmp_path, server, "--role", "client", "--version", "1.4.0")
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


def test_an_unknown_role_is_refused_before_anything_is_downloaded(tmp_path, release_server):
    server = release_server(binaries())
    result = run_install(tmp_path, server, "--role", "server")
    assert result.returncode == 1 and "--role is client or node" in result.stderr
    assert server.requests == []


def test_the_machine_decides_the_role(tmp_path, release_server):
    # A GB10 gets the node (which then needs the release's wheel); anything else, the client.
    server = release_server(binaries())
    assert "role: client" in run_install(tmp_path, server).stdout
    on_gb10 = run_install(tmp_path, server, gpu="NVIDIA GB10")
    assert "role: node" in on_gb10.stdout and "not a GB10" not in on_gb10.stdout
    assert "role: client" in run_install(tmp_path, server, "--role", "client", gpu="NVIDIA GB10").stdout
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
    for name in ("CODEX_PATCH_DIR", "MIGHTLING_CRATE_DIR", "CODEX_SUBMODULE_DIR", "WEB_CRATE_DIR", "CODE_CRATE_DIR"):
        monkeypatch.setattr(codex_branded_builder, name, str(tmp_path / "site-packages" / name.lower()))
    install_dir = tmp_path / "install"
    monkeypatch.setattr(codex_branded_builder, "INSTALL_DIR", str(install_dir))
    for name in ("PATH_LINK", "ADMIN_PATH_LINK", "SEARCH_PATH_LINK", "FETCH_PATH_LINK", "CODE_PATH_LINK"):
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
    monkeypatch.setattr(codex_branded_builder.DesktopInstaller, "install_rust", no_build)

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
    monkeypatch.setattr(DesktopRunner, "onyx_is_up", classmethod(lambda cls, url=None: True))
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
             "missing": set(), "docker_access": True}
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
