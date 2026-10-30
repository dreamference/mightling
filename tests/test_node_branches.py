"""Decisions in the node package that no other test reached: refusals, guards and fallbacks.

Nothing here reaches another machine or changes this one: sockets stay in the test's own run
folder, and every ssh, plug or subprocess call is a stand-in.
"""

import os
import socket
import struct
from pathlib import Path

from dreamference.node.fleet_askpass import FleetAskpass
from dreamference.node.fleet_session import FleetSession

SECRET = "pa55-for-the-test"


# -- askpass: the peer check


def test_askpass_gives_another_user_nothing_and_keeps_answering(monkeypatch, capsys):
    run_dir = Path(FleetSession.new_run_dir())
    askpass = FleetAskpass(run_dir, {"spark-1": SECRET})
    same_user = [False]
    monkeypatch.setattr(
        FleetAskpass, "_same_user", classmethod(lambda cls, connection: same_user[0])
    )
    askpass.start("/usr/bin/false")
    try:
        for key, value in askpass.environment("spark-1").items():
            monkeypatch.setenv(key, value)
        assert FleetAskpass.ask() == 1
        assert capsys.readouterr().out == ""
        # The refused peer did not stop the server: the next connection is answered.
        same_user[0] = True
        assert FleetAskpass.ask() == 0
        assert capsys.readouterr().out == SECRET + "\n"
    finally:
        askpass.stop()
        run_dir.rmdir()


class PeerConnection:
    """A connection whose SO_PEERCRED names the given uid, or fails."""

    def __init__(self, uid=None):
        self.uid = uid

    def getsockopt(self, level, option, size):
        assert (level, option, size) == (
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        if self.uid is None:
            raise OSError("no credentials")
        return struct.pack("3i", 4242, self.uid, 0)


def test_askpass_peer_check_wants_this_uid_and_fails_closed():
    assert FleetAskpass._same_user(PeerConnection(os.getuid()))
    assert not FleetAskpass._same_user(PeerConnection(os.getuid() + 1))
    assert not FleetAskpass._same_user(PeerConnection(None))


def test_askpass_stop_before_start_removes_what_is_there(tmp_path):
    askpass = FleetAskpass(tmp_path, {})
    askpass.script_path.write_text("#!/bin/sh\n")
    askpass.stop()
    askpass.stop()
    assert not askpass.script_path.exists() and not askpass.socket_path.exists()
