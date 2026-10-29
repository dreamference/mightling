"""The reverse tunnel's key, unit and state (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2).

`ssh-keygen` and `systemctl` never run: `subprocess.run` or the class's `_run` seam is replaced,
and the node's folder is the test's own HOME.
"""

import subprocess

from dreamference.remote import RemoteTunnel


def _done(argv, out="", code=0, err=""):
    return subprocess.CompletedProcess(argv, code, out, err)


def _commands(monkeypatch, code=0, out="", fails=()):
    calls = []

    def fake(cls, argv):
        calls.append(argv)
        bad = any(word in argv for word in fails)
        return _done(argv, out=out, code=1 if bad else code)

    monkeypatch.setattr(RemoteTunnel, "_run", classmethod(fake))
    return calls


def test_a_missing_ssh_or_a_timeout_is_a_failed_command_not_a_raise(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError("ssh-keygen")

    monkeypatch.setattr(subprocess, "run", missing)
    result = RemoteTunnel._run(["ssh-keygen"])
    assert result.returncode == 127 and "ssh-keygen" in result.stderr

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", slow)
    assert RemoteTunnel._run(["ssh-keygen"]).returncode == 127


def test_the_tunnels_key_is_made_once_with_no_passphrase_and_its_public_half_returned(monkeypatch):
    calls = []

    def keygen(cls, argv):
        calls.append(argv)
        RemoteTunnel.key().write_text("private")
        (RemoteTunnel.key().parent / (RemoteTunnel.key().name + ".pub")).write_text(
            "ssh-ed25519 AAAA mightling-remote-tunnel\n"
        )
        return _done(argv)

    monkeypatch.setattr(RemoteTunnel, "_run", classmethod(keygen))
    assert RemoteTunnel.public_key() == "ssh-ed25519 AAAA mightling-remote-tunnel"
    argv = calls[0]
    assert argv[argv.index("-N") + 1] == "" and argv[argv.index("-t") + 1] == "ed25519"
    assert RemoteTunnel.public_key() == "ssh-ed25519 AAAA mightling-remote-tunnel"
    assert len(calls) == 1, "an existing key is kept, never remade"


def test_a_failed_ssh_keygen_gives_no_public_key(monkeypatch):
    _commands(monkeypatch, code=1)
    assert RemoteTunnel.public_key() is None
    assert not RemoteTunnel.key().exists()


def test_a_key_without_its_public_half_gives_none_not_a_raise(monkeypatch):
    RemoteTunnel.key().parent.mkdir(parents=True, exist_ok=True)
    RemoteTunnel.key().write_text("private")
    calls = _commands(monkeypatch)
    assert RemoteTunnel.public_key() is None
    assert calls == []


def test_the_boxs_authorized_line_allows_one_loopback_listener_and_no_shell():
    line = RemoteTunnel.authorized_line("ssh-ed25519 AAAA tunnel")
    assert line.startswith("restrict,port-forwarding,permitlisten=")
    assert 'command="/usr/sbin/nologin"' in line and line.endswith("ssh-ed25519 AAAA tunnel")
    assert "permitopen" not in line and "agent-forwarding" not in line


def test_a_failed_enable_or_restart_means_the_tunnel_is_not_up(monkeypatch):
    _commands(monkeypatch, fails=("enable",))
    assert RemoteTunnel.install("box.example.org") is False
    _commands(monkeypatch, fails=("restart",))
    assert RemoteTunnel.install("box.example.org") is False
    assert RemoteTunnel.unit_path().is_file(), "the unit is written either way"


def test_removing_the_tunnel_stops_it_and_deletes_its_unit_but_keeps_the_key(monkeypatch):
    _commands(monkeypatch)
    RemoteTunnel.install("box.example.org")
    RemoteTunnel.key().parent.mkdir(parents=True, exist_ok=True)
    RemoteTunnel.key().write_text("private")
    calls = _commands(monkeypatch)
    RemoteTunnel.remove()
    assert not RemoteTunnel.unit_path().exists()
    assert ["systemctl", "--user", "disable", "--now", "mightling-remote-tunnel.service"] in calls
    assert calls[-1] == ["systemctl", "--user", "daemon-reload"]
    assert RemoteTunnel.key().is_file()
    RemoteTunnel.remove()  # nothing left to delete: no raise


def test_the_tunnels_state_is_absent_without_a_unit_and_never_asks_systemd(monkeypatch):
    calls = _commands(monkeypatch, out="active\n")
    assert RemoteTunnel.state() == "absent"
    assert calls == []


def test_the_tunnels_state_is_what_systemd_says_or_unknown(monkeypatch):
    RemoteTunnel.unit_path().parent.mkdir(parents=True, exist_ok=True)
    RemoteTunnel.unit_path().write_text("[Unit]\n")
    _commands(monkeypatch, out="failed\n", code=3)
    assert RemoteTunnel.state() == "failed"
    _commands(monkeypatch, out="", code=127)
    assert RemoteTunnel.state() == "unknown"
