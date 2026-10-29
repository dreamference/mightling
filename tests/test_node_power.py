"""Smart-plug power cycling: Shelly's local API, the plug tables, the neighbour's forced command.

No test opens a socket or runs ssh: the plug's HTTP goes through `NodeShellyPlug._get` and the
pairing through `NodePairing.run`/`find`, both replaced.
"""

import json
import subprocess

import pytest

from dreamference.node import (
    NodePairing,
    NodePowerCycle,
    NodePowerSettings,
    NodeServe,
    NodeShellyPlug,
    node_power_cycle,
)


class FakePlug:
    """A Shelly plug of either generation, answering the URLs the module builds."""

    def __init__(self, generation=2, on=True, stuck=False):
        self.generation, self.on, self.stuck, self.urls = generation, on, stuck, []

    def __call__(self, url):
        self.urls.append(url)
        if "Switch.Set" in url or "turn=" in url:
            wanted = "on=true" in url or "turn=on" in url
            if not self.stuck:
                self.on = wanted
            return {"was_on": True} if self.generation == 2 else {"ison": self.on}
        if self.generation == 2:
            return {"output": self.on, "apower": 41.5 if self.on else 0.0}
        return {"ison": self.on, "power": 3}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(node_power_cycle.time, "sleep", lambda seconds: None)


def test_a_gen2_plug_is_switched_and_read_back_by_rpc(monkeypatch):
    plug = FakePlug(2)
    monkeypatch.setattr(NodeShellyPlug, "_get", classmethod(lambda cls, url: plug(url)))
    assert NodeShellyPlug.switch("shellyplug-aa.local", False) == {"on": False, "power_w": 0.0}
    assert plug.urls[0] == "http://shellyplug-aa.local/rpc/Switch.Set?id=0&on=false"
    assert plug.urls[1] == "http://shellyplug-aa.local/rpc/Switch.GetStatus?id=0"
    assert NodeShellyPlug.state("shellyplug-aa.local")["on"] is False


def test_a_gen1_plug_uses_the_relay_endpoint(monkeypatch):
    plug = FakePlug(1)
    monkeypatch.setattr(NodeShellyPlug, "_get", classmethod(lambda cls, url: plug(url)))
    NodeShellyPlug.switch("plug.local", False, channel=0, generation=1)
    assert plug.urls[0] == "http://plug.local/relay/0?turn=off"
    assert NodeShellyPlug.state("plug.local", 0, 1) == {"on": False, "power_w": 3.0}


def test_a_plug_that_accepts_the_command_but_does_not_change_is_an_error(monkeypatch):
    plug = FakePlug(2, stuck=True)
    monkeypatch.setattr(NodeShellyPlug, "_get", classmethod(lambda cls, url: plug(url)))
    with pytest.raises(OSError, match="reports itself on"):
        NodeShellyPlug.switch("p.local", False)


@pytest.mark.parametrize("host", ["", "http://x", "x/../y", "x:80", "a b", "x@y", "-x", "x?y=1"])
def test_a_host_that_could_reshape_the_url_is_refused(host):
    with pytest.raises(ValueError):
        NodeShellyPlug.state(host)


def test_settings_keep_plugs_and_nodes_and_refuse_bad_input():
    assert NodePowerSettings.load() == {"plugs": {}, "nodes": {}}
    assert NodePowerSettings.set_plug("house-1-plug", "shellyplug-aa.local") is None
    assert NodePowerSettings.set_plug("x", "http://evil/") is not None
    assert NodePowerSettings.set_plug("x", "p.local", kind="tapo") is not None
    assert NodePowerSettings.set_node("house-1", "missing") is not None  # not in this table
    assert NodePowerSettings.set_node("house-1", "house-1-plug", via="house-2") is None
    data = NodePowerSettings.load()
    assert data["plugs"]["house-1-plug"]["host"] == "shellyplug-aa.local"
    assert data["nodes"]["house-1"] == {"plug": "house-1-plug", "via": "house-2"}


def test_the_neighbour_serves_only_plugs_it_already_knows(monkeypatch, capsys):
    plug = FakePlug(2)
    monkeypatch.setattr(NodeShellyPlug, "_get", classmethod(lambda cls, url: plug(url)))
    NodePowerSettings.set_plug("p1", "p1.local")
    assert NodeServe.serve("power-plug p1 off") == 0
    assert json.loads(capsys.readouterr().out)["on"] is False
    assert NodeServe.serve("power-plug nope off") == 2  # unknown name: nothing is contacted
    assert NodeServe.serve("power-plug p1 reboot") == 2
    assert NodeServe.serve("power-plug p1.local off") == 2  # a host is not a plug name
    assert NodeServe.serve("power-plug p1") == 2
    assert all("nope" not in url for url in plug.urls)


def _paired(monkeypatch, answers):
    """Nodes `house-1` (the target) and `house-2` (the neighbour); `answers` decides `info`."""
    records = {
        "house-1": {"node": "n1", "name": "house-1", "user": "u", "address": "a"},
        "house-2": {"node": "n2", "name": "house-2", "user": "u", "address": "b"},
    }
    sent = []

    def run(cls, record, request, **kwargs):
        sent.append((record["name"], request))
        if request == "info":
            return subprocess.CompletedProcess([], 0 if answers(record["name"]) else 255, "{}", "")
        return subprocess.CompletedProcess([], *plug_reply(request))

    plug = FakePlug(2)

    def plug_reply(request):
        _, _name, action = request.split()
        if action == "status":
            state = {"on": plug.on, "power_w": 40.0 if plug.on else 0.0}
        else:
            plug.on = action == "on"
            state = {"on": plug.on, "power_w": None}
        return 0, json.dumps(state) + "\n", ""

    monkeypatch.setattr(
        NodePairing, "find", classmethod(lambda cls, name, browse=True: records.get(name))
    )
    monkeypatch.setattr(NodePairing, "run", classmethod(run))
    return sent, plug


def test_a_hung_node_is_cycled_through_the_neighbour_and_waited_for(monkeypatch, capsys):
    alive = {"house-1": False, "house-2": True}
    sent, plug = _paired(monkeypatch, lambda name: alive[name])
    NodePowerSettings.set_plug("unused", "u.local")
    NodePowerSettings.set_node("house-1", "p1", via="house-2")
    calls = []
    monkeypatch.setattr(
        node_power_cycle.time,
        "sleep",
        lambda s: (calls.append(s), alive.update({"house-1": plug.on and len(calls) > 1})),
    )
    assert NodePowerCycle.cycle("house-1", off_seconds=1, wait_minutes=1) == 0
    actions = [request.split()[2] for who, request in sent if request.startswith("power-plug")]
    assert actions == ["status", "off", "on"]
    assert all(who == "house-2" for who, request in sent if request.startswith("power-plug"))
    assert 5 in calls  # the off time is never under 5 s
    out = capsys.readouterr().out
    assert "answers again" in out


def test_a_node_that_still_answers_is_not_cut_without_force(monkeypatch, capsys):
    sent, _plug = _paired(monkeypatch, lambda name: True)
    NodePowerSettings.set_node("house-1", "p1", via="house-2")
    assert NodePowerCycle.cycle("house-1") == 1
    assert not any(request.startswith("power-plug") for _, request in sent)
    assert "not hung" in capsys.readouterr().out


def test_a_node_with_no_plug_set_says_how_to_set_one(capsys):
    assert NodePowerCycle.cycle("house-1") == 3
    assert "power-config node house-1" in capsys.readouterr().out


def test_a_direct_plug_is_used_from_here_and_the_plug_is_never_left_off(monkeypatch, capsys):
    plug = FakePlug(2)
    monkeypatch.setattr(NodeShellyPlug, "_get", classmethod(lambda cls, url: plug(url)))
    NodePowerSettings.set_plug("p1", "p1.local")
    NodePowerSettings.set_node("house-1", "p1")
    _paired(monkeypatch, lambda name: False)
    state = {"tries": 0}
    real = NodeShellyPlug.switch.__func__

    def flaky(cls, host, on, channel=0, generation=2):
        if on:
            state["tries"] += 1
            if state["tries"] < 3:
                raise OSError("timed out")
        return real(cls, host, on, channel, generation)

    monkeypatch.setattr(NodeShellyPlug, "switch", classmethod(flaky))
    monkeypatch.setattr(
        node_power_cycle.NodePowerCycle,
        "_answers",
        classmethod(lambda cls, node: plug.on and state["tries"] >= 3),
    )
    assert NodePowerCycle.cycle("house-1", off_seconds=5, wait_minutes=1) == 0
    assert state["tries"] == 3 and plug.on


def test_a_plug_that_ignores_off_is_reported_and_the_node_is_not_waited_for(monkeypatch, capsys):
    plug = FakePlug(2, stuck=True)
    monkeypatch.setattr(NodeShellyPlug, "_get", classmethod(lambda cls, url: plug(url)))
    NodePowerSettings.set_plug("p1", "p1.local")
    NodePowerSettings.set_node("house-1", "p1")
    _paired(monkeypatch, lambda name: False)
    asked = []
    monkeypatch.setattr(
        node_power_cycle.NodePowerCycle,
        "_answers",
        classmethod(lambda cls, node: asked.append(node) or False),
    )
    assert NodePowerCycle.cycle("house-1", off_seconds=5, wait_minutes=1) == 1
    out = capsys.readouterr().out
    assert "still reports itself on" in out and "Power is back" not in out
    assert asked == ["house-1"]


def test_a_reply_cut_off_mid_body_is_an_oserror_like_any_other_failed_reply(monkeypatch):
    import http.client
    import io

    from dreamference.node import node_shelly_plug

    class Cut(io.BytesIO):
        def read(self, *args):
            raise http.client.IncompleteRead(b'{"output"')

    monkeypatch.setattr(node_shelly_plug.urllib.request, "urlopen", lambda url, timeout: Cut())
    with pytest.raises(OSError):
        NodeShellyPlug._get("http://p.local/rpc/Switch.GetStatus?id=0")
