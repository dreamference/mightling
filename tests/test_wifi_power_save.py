"""
Wi-Fi power saving (dreamference/vllm_server/wifi_power_save.py): it loses the multicast mDNS a
client finds its node by, so `host check` reports it, `host setup` turns it off and `node enable`
warns. Every nmcli and iw reading here is a stub (conftest stubs them for every other test too).
"""

import subprocess

import pytest

from dreamference.node import NodeAdvertiser, NodePrepare
from dreamference.vllm_server import HostSafetySetup, WifiPowerSave
from dreamference.vllm_server import host_safety_setup, wifi_power_save
from dreamference.vllm_server.wifi_power_save import STEP_NAME

WIFI_ONLY = "wlP9s9:wifi:connected:TP-Link_DA74\np2p-dev-wlP9s9:wifi-p2p:disconnected:\nenP7s7:ethernet:unavailable:\n"
# The real reader, taken before conftest stubs it for each test.
REAL_READ = WifiPowerSave.__dict__["_read"]
ETHERNET_ONLY = "enP7s7:ethernet:connected:Wired connection 1\nwlP9s9:wifi:disconnected:\nlo:loopback:connected (externally):lo\n"


@pytest.fixture
def network(monkeypatch):
    """A machine whose nmcli and iw answers the test sets; by default Wi-Fi with power saving on."""
    state = {"nmcli": True, "iw": True, "devices": WIFI_ONLY, "connection": "TP-Link_DA74",
             "setting": "default", "radio": "on"}
    asked = []

    def read(cls, argv):
        asked.append(argv)
        program = argv[0]
        if program == "nmcli" and not state["nmcli"] or program == "iw" and not state["iw"]:
            return None
        if argv[:2] == ["nmcli", "-t"]:
            return state["devices"]
        if "GENERAL.CONNECTION" in argv:
            # Terse output escapes `:` and `\` in a value unless asked not to (`-e no`).
            name = state["connection"]
            if argv[1:3] != ["-e", "no"]:
                name = name.replace("\\", "\\\\").replace(":", "\\:")
            return name + "\n"
        if "802-11-wireless.powersave" in argv:
            return state["setting"] + "\n"
        if program == "iw":
            return f"Power save: {state['radio']}\n"
        raise AssertionError(f"unexpected reading {argv!r}")

    monkeypatch.setattr(WifiPowerSave, "_read", classmethod(read))
    state["asked"] = asked
    return state


@pytest.mark.parametrize("setting", ["default", "enable", "ignore", "0", "3", "0 (default)"])
def test_wifi_with_power_saving_not_disabled_is_reported_with_both_fixes(network, setting):
    network["setting"] = setting
    (problem,) = WifiPowerSave.problems()
    assert (problem["device"], problem["connection"], problem["radio"]) == ("wlP9s9", "TP-Link_DA74", True)
    assert problem["commands"] == [
        ["nmcli", "connection", "modify", "TP-Link_DA74", "802-11-wireless.powersave", "2"],
        ["iw", "dev", "wlP9s9", "set", "power_save", "off"],
    ]
    # Every reading is a reading: nothing that changes the network is asked.
    assert all("modify" not in argv and "set" not in argv for argv in network["asked"])


@pytest.mark.parametrize("setting", ["disable", "2"])
def test_power_saving_disabled_and_off_passes(network, setting):
    network["setting"], network["radio"] = setting, "off"
    assert WifiPowerSave.problems() == [] and WifiPowerSave.step() is None and WifiPowerSave.warning() is None


def test_disabled_for_the_connection_but_still_on_in_the_radio_needs_only_iw(network):
    # nmcli modified, no reconnect yet: the setting is right, the radio still sleeps.
    network["setting"] = "disable"
    (problem,) = WifiPowerSave.problems()
    assert problem["commands"] == [["iw", "dev", "wlP9s9", "set", "power_save", "off"]]


def test_off_in_the_radio_but_default_for_the_connection_needs_the_persistent_fix(network):
    # `iw … set power_save off` by hand lasts until the next reconnect.
    network["radio"] = "off"
    (problem,) = WifiPowerSave.problems()
    assert problem["commands"] == [["nmcli", "connection", "modify", "TP-Link_DA74", "802-11-wireless.powersave", "2"]]


def test_without_iw_the_setting_alone_decides(network):
    network["iw"] = False
    step = WifiPowerSave.step()
    assert step["commands"] == [["nmcli", "connection", "modify", "TP-Link_DA74", "802-11-wireless.powersave", "2"]]
    assert "next reconnect" in step["why"]
    network["setting"] = "disable"
    assert WifiPowerSave.step() is None


def test_ethernet_only_passes(network):
    network["devices"] = ETHERNET_ONLY
    assert WifiPowerSave.problems() == [] and WifiPowerSave.step() is None
    assert not any(argv[0] == "iw" for argv in network["asked"])


def test_without_networkmanager_it_is_skipped_quietly(network, capsys):
    network["nmcli"] = False
    assert WifiPowerSave.problems() == [] and WifiPowerSave.warning() is None
    assert capsys.readouterr().out == ""


def test_the_reader_runs_nothing_when_the_program_is_missing(monkeypatch):
    monkeypatch.setattr(WifiPowerSave, "_read", REAL_READ)
    monkeypatch.setattr(wifi_power_save.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(wifi_power_save.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    assert WifiPowerSave._read(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"]) is None


def test_the_reader_asks_in_the_c_locale_and_drops_a_failure(monkeypatch):
    monkeypatch.setattr(WifiPowerSave, "_read", REAL_READ)
    seen = {}
    monkeypatch.setattr(wifi_power_save.shutil, "which", lambda name, path=None: "/usr/sbin/" + name)

    def run(argv, **kwargs):
        seen.update(argv=argv, env=kwargs["env"])
        return subprocess.CompletedProcess(argv, seen.get("code", 0), "Power save: on\n", "")

    monkeypatch.setattr(wifi_power_save.subprocess, "run", run)
    assert WifiPowerSave.radio_power_save("wlP9s9") is True
    assert seen["argv"] == ["/usr/sbin/iw", "dev", "wlP9s9", "get", "power_save"] and seen["env"]["LC_ALL"] == "C"
    seen["code"] = 1
    assert WifiPowerSave.radio_power_save("wlP9s9") is None


# -- host check, host setup, node enable, node prepare ----------------------------------------------

@pytest.fixture
def only_wifi_to_fix(monkeypatch):
    """Every other host check passes, so the Wi-Fi step is the only one."""
    for name in ("_sysstat_step", "_oom_step", "_swap_step", "_container_step", "_sandbox_step"):
        monkeypatch.setattr(HostSafetySetup, name, classmethod(lambda cls: None))
    monkeypatch.setattr(HostSafetySetup, "_sysctl_steps", classmethod(lambda cls: []))


def test_host_check_prints_the_exact_commands(network, only_wifi_to_fix, capsys):
    assert HostSafetySetup.check() is False
    out = capsys.readouterr().out
    assert STEP_NAME in out and "mDNS" in out
    assert "sudo nmcli connection modify TP-Link_DA74 802-11-wireless.powersave 2" in out
    assert "sudo iw dev wlP9s9 set power_save off" in out


def test_host_check_passes_on_ethernet(network, only_wifi_to_fix, capsys):
    network["devices"] = ETHERNET_ONLY
    assert HostSafetySetup.check() is True
    assert "nothing to do" in capsys.readouterr().out


def test_host_setup_turns_it_off_for_good_and_now(network, only_wifi_to_fix, monkeypatch, capsys):
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(host_safety_setup.shutil, "which", lambda name: "/usr/bin/" + name)
    ran = []

    def fake_run(command, **kwargs):
        ran.append(command)
        if command[1] == "nmcli":
            network["setting"] = "disable"
        elif command[1] == "iw":
            network["radio"] = "off"
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(host_safety_setup.subprocess, "run", fake_run)
    assert HostSafetySetup.setup() is True
    assert ran == [["sudo", "nmcli", "connection", "modify", "TP-Link_DA74", "802-11-wireless.powersave", "2"],
                   ["sudo", "iw", "dev", "wlP9s9", "set", "power_save", "off"]]
    assert "now has what" in capsys.readouterr().out


def test_node_enable_warns_but_goes_on(network, monkeypatch, capsys):
    monkeypatch.setattr(NodeAdvertiser, "is_gb10", classmethod(lambda cls: True))
    monkeypatch.setattr(NodeAdvertiser, "install_service_file", classmethod(lambda cls, text, yes=False: True))
    monkeypatch.setattr(NodeAdvertiser, "apply_binds", classmethod(lambda cls: None))
    monkeypatch.setattr(NodeAdvertiser, "model_answers", classmethod(lambda cls, port: False))
    monkeypatch.setattr(NodeAdvertiser, "search_port", classmethod(lambda cls: None))
    monkeypatch.setattr(NodeAdvertiser, "ling_web_available", classmethod(lambda cls: False))
    assert NodeAdvertiser.enable(no_web=True) is True
    out = capsys.readouterr().out
    assert "Wi-Fi power saving is on for wlP9s9 (TP-Link_DA74)" in out and "ling-admin host setup" in out
    assert "sudo iw dev wlP9s9 set power_save off" in out and "This node is advertised" in out
    network["setting"], network["radio"] = "disable", "off"
    assert NodeAdvertiser.enable(no_web=True) is True
    assert "Wi-Fi" not in capsys.readouterr().out


def test_node_prepare_turns_it_off_too(network, only_wifi_to_fix, monkeypatch, capsys):
    # Decided 2026-10-12: the one NetworkManager change node prepare makes.
    from dreamference.node import node_prepare
    assert [step["name"] for step in NodePrepare._host_steps()] == [STEP_NAME]
    ran = []

    def fake_run(command, **kwargs):
        ran.append(command)
        if command[0] == "nmcli":
            network["setting"] = "disable"
        elif command[0] == "iw":
            network["radio"] = "off"
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(node_prepare.subprocess, "run", fake_run)
    for command in NodePrepare._host_steps()[0]["commands"]:
        assert NodePrepare._execute(command) is True
    assert ran == [["nmcli", "connection", "modify", "TP-Link_DA74", "802-11-wireless.powersave", "2"],
                   ["iw", "dev", "wlP9s9", "set", "power_save", "off"]]
    assert NodePrepare._host_steps() == []
    assert "Refused" not in capsys.readouterr().out


@pytest.mark.parametrize("command", [
    ["nmcli", "connection", "modify", "TP-Link_DA74", "802-11-wireless.powersave", "2"],
    ["nmcli", "connection", "modify", "Wired connection 1", "802-11-wireless.powersave", "2"],
    ["iw", "dev", "wlP9s9", "set", "power_save", "off"],
])
def test_node_prepare_allows_exactly_the_two_wifi_commands(command):
    assert WifiPowerSave.is_fix_command(command) is True
    assert NodePrepare.allowed(command) is True


@pytest.mark.parametrize("command", [
    ["nmcli", "radio", "wifi", "off"],
    ["nmcli", "networking", "off"],
    ["nmcli", "connection", "down", "TP-Link_DA74"],
    ["nmcli", "connection", "up", "TP-Link_DA74"],
    ["nmcli", "device", "reapply", "wlP9s9"],
    ["nmcli", "connection", "add", "type", "wifi", "ifname", "wlP9s9"],
    ["nmcli", "connection", "modify", "TP-Link_DA74", "ipv4.method", "manual"],
    ["nmcli", "connection", "modify", "TP-Link_DA74", "802-11-wireless.powersave", "3"],
    ["nmcli", "connection", "modify", "TP-Link_DA74", "802-11-wireless.powersave", "2", "ipv4.dns", "1.1.1.1"],
    ["nmcli", "connection", "modify", "--temporary", "802-11-wireless.powersave", "2"],
    ["nmcli", "connection", "modify", "", "802-11-wireless.powersave", "2"],
    ["/usr/bin/nmcli", "connection", "modify", "TP-Link_DA74", "802-11-wireless.powersave", "2"],
    ["iw", "dev", "wlP9s9", "set", "power_save", "on"],
    ["iw", "dev", "wlP9s9", "disconnect"],
    ["iw", "reg", "set", "US"],
    ["iw", "dev", "-x", "set", "power_save", "off"],
    ["systemctl", "restart", "NetworkManager"],
    ["sh", "-c", "nmcli connection modify x 802-11-wireless.powersave 2"],
])
def test_node_prepare_refuses_every_other_network_command(command, monkeypatch, capsys):
    from dreamference.node import node_prepare
    called = []
    monkeypatch.setattr(node_prepare.subprocess, "run", lambda *a, **k: called.append(a))
    assert NodePrepare.allowed(command) is False
    assert NodePrepare._execute(command) is False
    assert called == [] and "never runs" in capsys.readouterr().out


def test_a_connection_named_with_a_colon_is_fixed_by_its_own_name(network):
    network["connection"] = "Home:5G"
    [problem] = WifiPowerSave.problems()
    assert problem["connection"] == "Home:5G"
    assert ["nmcli", "connection", "modify", "Home:5G", "802-11-wireless.powersave", "2"] in problem["commands"]
