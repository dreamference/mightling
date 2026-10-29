"""NetBird's client on the node: download, pin, install script, leave script.

The network, the privilege prompt and the root script never run: `urllib.request.urlopen` and the
class's `_run_root` seam are replaced, and the node's folder is the test's own HOME.
"""

import hashlib
import io
import stat
import tarfile
import urllib.error
import urllib.request

import pytest

from dreamference.remote import RemoteNodePeer, RemoteSettings, remote_node_peer

NODE_ID = "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001"


def _archive(*members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            archive.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return buffer.getvalue()


def _pinned(monkeypatch, data, arch="arm64"):
    monkeypatch.setattr(RemoteNodePeer, "arch", classmethod(lambda cls: arch))
    monkeypatch.setattr(RemoteNodePeer, "_download", classmethod(lambda cls, url: data))
    monkeypatch.setitem(remote_node_peer.CLIENT_ARCHIVES, arch, hashlib.sha256(data).hexdigest())


def test_a_download_that_fails_is_none_not_a_raise(monkeypatch):
    def refuse(url, timeout):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert RemoteNodePeer._download("https://203.0.113.5/netbird.tar.gz") is None


def test_a_download_returns_the_bytes(monkeypatch):
    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: Reply(b"data"))
    assert RemoteNodePeer._download("https://203.0.113.5/x") == b"data"


@pytest.mark.parametrize(
    "machine, arch",
    [("x86_64", "amd64"), ("AMD64", "amd64"), ("aarch64", "arm64"), ("arm64", "arm64")],
)
def test_the_machines_architecture_is_named_as_netbird_names_it(monkeypatch, machine, arch):
    monkeypatch.setattr(remote_node_peer.platform, "machine", lambda: machine)
    assert RemoteNodePeer.arch() == arch


def test_an_architecture_netbird_is_not_pinned_for_downloads_nothing(monkeypatch):
    monkeypatch.setattr(remote_node_peer.platform, "machine", lambda: "riscv64")
    assert RemoteNodePeer.arch() is None
    monkeypatch.setattr(
        RemoteNodePeer, "_download", classmethod(lambda cls, url: pytest.fail("downloaded"))
    )
    assert RemoteNodePeer.client_binary() is None


def test_a_failed_download_installs_nothing(monkeypatch):
    monkeypatch.setattr(RemoteNodePeer, "arch", classmethod(lambda cls: "amd64"))
    monkeypatch.setattr(RemoteNodePeer, "_download", classmethod(lambda cls, url: None))
    assert RemoteNodePeer.client_binary() is None
    assert not RemoteSettings.path("box", "netbird").exists()


def test_an_archive_that_holds_no_netbird_file_installs_nothing(monkeypatch):
    # A directory called netbird is not the binary.
    _pinned(
        monkeypatch,
        _archive(("README.md", b"r", tarfile.REGTYPE), ("netbird", b"", tarfile.DIRTYPE)),
    )
    assert RemoteNodePeer.client_binary() is None
    assert not RemoteSettings.path("box", "netbird").exists()


def test_the_binary_is_found_in_a_subfolder_and_kept_executable(monkeypatch):
    _pinned(monkeypatch, _archive(("netbird_0.1/netbird", b"binary", tarfile.REGTYPE)))
    binary = RemoteNodePeer.client_binary()
    assert binary is not None and binary.read_bytes() == b"binary"
    assert stat.S_IMODE(binary.stat().st_mode) == 0o755


def test_a_name_that_only_ends_in_netbird_is_not_the_binary(monkeypatch):
    _pinned(monkeypatch, _archive(("not-netbird", b"x", tarfile.REGTYPE)))
    assert RemoteNodePeer.client_binary() is None


def test_the_installed_client_is_where_the_path_finds_it(monkeypatch):
    monkeypatch.setattr(remote_node_peer.shutil, "which", lambda name: "/usr/local/bin/netbird")
    assert RemoteNodePeer.installed() == "/usr/local/bin/netbird"
    monkeypatch.setattr(remote_node_peer.shutil, "which", lambda name: None)
    assert RemoteNodePeer.installed() is None


def test_the_root_script_goes_through_the_privilege_prompt_with_the_purpose(monkeypatch, tmp_path):
    seen = {}

    def run_privileged(argv, purpose, yes=False):
        seen.update(argv=argv, purpose=purpose, yes=yes)
        return True

    monkeypatch.setattr(
        "dreamference.node.node_advertiser.NodeAdvertiser.run_privileged", run_privileged
    )
    script = tmp_path / "s.sh"
    assert RemoteNodePeer._run_root(script, "do a thing", True) is True
    assert seen == {"argv": ["bash", str(script)], "purpose": "do a thing", "yes": True}


def test_the_join_script_quotes_every_value_it_is_given(tmp_path):
    script = RemoteNodePeer.script(
        None,
        tmp_path / "ca; rm -rf x.crt",
        "https://box.example.org; reboot",
        tmp_path / "key $(id)",
        "peer`id`",
    )
    assert "'https://box.example.org; reboot'" in script
    assert "'peer`id`'" in script and "'" + str(tmp_path / "key $(id)") + "'" in script
    assert "install -m 0755" not in script, "no binary to install: the installed one is kept"


def test_the_join_script_installs_the_checked_binary_when_given_one(tmp_path):
    script = RemoteNodePeer.script(
        tmp_path / "netbird", tmp_path / "ca.crt", "https://box.example.org", tmp_path / "k", "p"
    )
    assert f"install -m 0755 {tmp_path / 'netbird'} /usr/local/bin/netbird" in script


# -- enrolling


def test_without_a_client_or_a_checked_binary_nothing_is_written_or_run(monkeypatch, tmp_path):
    monkeypatch.setattr(RemoteNodePeer, "installed", classmethod(lambda cls: None))
    monkeypatch.setattr(RemoteNodePeer, "client_binary", classmethod(lambda cls: None))
    monkeypatch.setattr(RemoteNodePeer, "_run_root", classmethod(lambda *a: pytest.fail("ran")))
    assert (
        RemoteNodePeer.enrol(tmp_path / "ca.crt", "https://b", "SETUPKEY", NODE_ID, True) is False
    )
    assert not RemoteSettings.path("node-setup-key").exists()


def test_the_setup_key_is_in_a_private_file_never_the_script_and_both_go_after_the_run(
    monkeypatch, tmp_path
):
    seen = {}

    def run_root(cls, script, purpose, yes):
        seen["script_text"] = script.read_text()
        seen["script_mode"] = stat.S_IMODE(script.stat().st_mode)
        seen["key_mode"] = stat.S_IMODE(RemoteSettings.path("node-setup-key").stat().st_mode)
        seen["key"] = RemoteSettings.path("node-setup-key").read_text()
        return True

    monkeypatch.setattr(RemoteNodePeer, "installed", classmethod(lambda cls: "/usr/bin/netbird"))
    monkeypatch.setattr(RemoteNodePeer, "_run_root", classmethod(run_root))
    assert RemoteNodePeer.enrol(tmp_path / "ca.crt", "https://b", "SETUPKEY", NODE_ID, True) is True
    assert "SETUPKEY" not in seen["script_text"] and "--setup-key-file" in seen["script_text"]
    assert (
        seen["key"] == "SETUPKEY\n" and seen["key_mode"] == 0o600 and seen["script_mode"] == 0o600
    )
    assert not RemoteSettings.path("node-setup-key").exists()
    assert not RemoteSettings.path("node-peer.sh").exists()


def test_the_setup_key_file_goes_even_when_the_root_script_raises(monkeypatch, tmp_path):
    def run_root(cls, script, purpose, yes):
        raise RuntimeError("sudo failed")

    monkeypatch.setattr(RemoteNodePeer, "installed", classmethod(lambda cls: "/usr/bin/netbird"))
    monkeypatch.setattr(RemoteNodePeer, "_run_root", classmethod(run_root))
    with pytest.raises(RuntimeError):
        RemoteNodePeer.enrol(tmp_path / "ca.crt", "https://b", "SETUPKEY", NODE_ID, False)
    assert not RemoteSettings.path("node-setup-key").exists()
    assert not RemoteSettings.path("node-peer.sh").exists()


def test_the_leave_script_takes_the_overlay_down_and_the_ca_out_of_the_trust_store():
    lines = RemoteNodePeer.leave_script().splitlines()
    assert lines[0] == "set -u", "a failing step does not stop the later ones"
    assert "netbird down >/dev/null 2>&1" in lines
    assert f"rm -f {remote_node_peer.TRUST_STORE_FILE}" in lines
    assert lines.index("netbird down >/dev/null 2>&1") < lines.index(
        "update-ca-certificates --fresh >/dev/null"
    )


def test_leaving_runs_the_script_as_root_and_deletes_it(monkeypatch):
    seen = {}

    def run_root(cls, script, purpose, yes):
        seen["text"] = script.read_text()
        seen["path"] = script
        return True

    monkeypatch.setattr(RemoteNodePeer, "_run_root", classmethod(run_root))
    assert RemoteNodePeer.leave(True) is True
    assert seen["text"] == RemoteNodePeer.leave_script()
    assert not seen["path"].exists()
