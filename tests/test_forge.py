"""The node's forge (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md): nothing here runs docker or reaches a node."""

import configparser
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from dreamference.audit.egress_audit import EgressAudit
from dreamference.forge import (
    ForgeAccounts,
    ForgeAnonymity,
    ForgeAppIni,
    ForgeBackup,
    ForgeBind,
    ForgeContainer,
    ForgeGithubMirror,
    ForgeMirrors,
    ForgePhone,
    ForgeRepositories,
    ForgeService,
    ForgeSettings,
    ForgeStorage,
)
from dreamference.node.node_service_file import NodeServiceFile

sys.path.insert(0, str(Path(__file__).parent))
import test_anonymity

LAN = "192.168"
GIT = shutil.which("git")


def interface(name, address):
    return {"ifname": name, "addr_info": [{"family": "inet", "local": address}]}


# -- binding ---------------------------------------------------------------------------------------


def test_the_forge_binds_loopback_and_the_lan_never_bridges_or_overlays():
    interfaces = [
        interface("lo", "127.0.0.1"),
        interface("wlP9s9", f"{LAN}.50.10"),
        interface("docker0", "172.17.0.1"),
        interface("br-6ff11b390f65", "172.18.0.1"),
        interface("veth12ab", "169.254.1.1"),
        interface("wt0", "100.64.0.1"),
        interface("tailscale0", "100.101.102.103"),
        interface("enP7s7", f"{LAN}.1.20"),
    ]
    assert ForgeBind.lan_addresses(interfaces, avahi_text="") == [f"{LAN}.50.10", f"{LAN}.1.20"]
    allow = "[server]\nallow-interfaces=enP7s7\n"
    assert ForgeBind.lan_addresses(interfaces, avahi_text=allow) == [f"{LAN}.1.20"]
    deny = "[server]\ndeny-interfaces=enP7s7, wlP9s9\n"
    assert ForgeBind.lan_addresses(interfaces, avahi_text=deny) == []


def test_the_forge_follows_the_node_off_the_lan_and_back(monkeypatch, capsys):
    from dreamference.node.node_settings import NodeSettings

    advertised = [False]
    monkeypatch.setattr(NodeSettings, "advertised", classmethod(lambda cls: advertised[0]))
    monkeypatch.setattr(
        ForgeBind, "lan_addresses", classmethod(lambda cls, *a, **k: [f"{LAN}.50.10"])
    )
    monkeypatch.setattr(ForgeAppIni, "write", classmethod(lambda cls, *a: None))
    monkeypatch.setattr(ForgeAnonymity, "write_patterns", classmethod(lambda cls, *a: None))
    monkeypatch.setattr(ForgeContainer, "running", classmethod(lambda cls: True))
    restarts = []
    monkeypatch.setattr(
        ForgeService, "restart", classmethod(lambda cls: restarts.append(1) or True)
    )
    ForgeSettings.update_state(role="record")

    def published():
        argv = ForgeService.run_arguments()
        return [argv[i + 1] for i, word in enumerate(argv[:-1]) if word == "-p"]

    assert published() and all(p.startswith("127.0.0.1:") for p in published()), (
        "a node that is not advertised keeps its forge on loopback"
    )
    assert not ForgeService.rebind() and not restarts
    advertised[0] = True
    assert ForgeService.rebind() and restarts == [1]
    assert any(p.startswith(f"{LAN}.50.10:") for p in published())
    assert not ForgeService.rebind() and restarts == [1]
    advertised[0] = False
    assert ForgeService.rebind() and restarts == [1, 1], (
        "`node disable` takes the forge off the LAN"
    )
    assert "loopback only" in capsys.readouterr().out


def test_the_forge_folders_are_private_whatever_the_umask(monkeypatch):
    monkeypatch.setattr(ForgeAppIni, "write", classmethod(lambda cls, *a: None))
    monkeypatch.setattr(ForgeAnonymity, "write_patterns", classmethod(lambda cls, *a: None))
    monkeypatch.setattr(ForgeService, "systemctl", classmethod(lambda cls, *a: None))
    ForgeSettings.update_state(role="record")
    folders = (ForgeSettings.share_dir(), ForgeSettings.data_dir())
    previous = os.umask(0o022)
    try:
        for start in (ForgeService.install_units, ForgeService.run_arguments):
            ForgeSettings.data_dir().mkdir(parents=True, exist_ok=True)
            for folder in folders:
                folder.chmod(0o755)
            start()
            modes = {folder.name: folder.stat().st_mode & 0o777 for folder in folders}
            assert modes == {"forge": 0o700, "data": 0o700}, (
                f"{start.__name__} leaves the forge open to other local users"
            )
    finally:
        os.umask(previous)


def test_every_publish_names_an_address():
    arguments = ForgeBind.publish_arguments([f"{LAN}.50.10"], 3200, 3000)
    assert arguments == ["-p", "127.0.0.1:3200:3000", "-p", f"{LAN}.50.10:3200:3000"]
    argv = ForgeContainer.run_arguments(Path("/data"), [f"{LAN}.50.10"])
    published = [argv[i + 1] for i, word in enumerate(argv) if word == "-p"]
    assert published and all(value.count(":") == 2 for value in published)
    assert "--memory" in argv and argv[argv.index("--memory") + 1] == "1024m"
    assert argv[argv.index("--memory-swap") + 1] == "1024m"
    assert argv[argv.index("--network") + 1] == "mightling-forge"
    assert argv[-1].startswith("codeberg.org/forgejo/forgejo:") and "@sha256:" in argv[-1]
    assert "--add-host" not in argv
    mirror = ForgeContainer.run_arguments(Path("/data"), [], record_address=f"{LAN}.50.11")
    assert mirror[mirror.index("--add-host") + 1] == f"forge-record:{LAN}.50.11"


# -- configuration ---------------------------------------------------------------------------------


def test_app_ini_is_lan_only_private_and_never_push_mirrors(tmp_path):
    path = ForgeAppIni.write(tmp_path, "gx10-1a2b", "record")
    text = path.read_text()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    for line in (
        "DISABLE_REGISTRATION = true",
        "REQUIRE_SIGNIN_VIEW = true",
        "DEFAULT_PRIVATE = private",
        "DB_TYPE = sqlite3",
        "DISABLE_NEW_PUSH = true",
        "ROOT_URL = http://gx10-1a2b.local:3100/forge/",
        "SSH_PORT = 2222",
        "INSTALL_LOCK = true",
    ):
        assert line in text
    assert "[cron.update_checker]\nENABLED = false" in text
    # `ling web` signs in with a form and keeps the cookies; the header sign-in stays off (§2.2).
    assert "REVERSE_PROXY" not in text
    secrets = ForgeAppIni.read_secrets(tmp_path)
    assert len(secrets) == 4
    ForgeAppIni.write(tmp_path, "node-b", "mirror")
    assert ForgeAppIni.read_secrets(tmp_path) == secrets, "secrets survive every rewrite"
    mirror = ForgeAppIni.path(tmp_path).read_text()
    assert "ALLOW_LOCALNETWORKS = true" in mirror and "ALLOWED_DOMAINS = forge-record" in mirror
    assert "[actions]\nENABLED = false" in mirror


def app_ini(path):
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string("[DEFAULT]\n" + path.read_text())
    return parser


SHUT_FOR_BOTH_ROLES = {
    ("server", "OFFLINE_MODE"): "true",
    ("security", "DISABLE_GIT_HOOKS"): "true",
    ("security", "MIN_PASSWORD_LENGTH"): "12",
    ("service", "DEFAULT_ORG_VISIBILITY"): "limited",
    ("service", "DEFAULT_USER_VISIBILITY"): "limited",
    ("openid", "ENABLE_OPENID_SIGNIN"): "false",
    ("openid", "ENABLE_OPENID_SIGNUP"): "false",
    ("picture", "DISABLE_GRAVATAR"): "true",
    ("picture", "ENABLE_FEDERATED_AVATAR"): "false",
    ("federation", "ENABLED"): "false",
    ("packages", "ENABLED"): "false",
    ("mirror", "DISABLE_NEW_PUSH"): "true",
}


@pytest.mark.parametrize(
    "role, mirroring, local_networks, domains",
    [("record", "false", "false", ""), ("mirror", "true", "true", "forge-record")],
)
def test_app_ini_reaches_nothing_outside_and_only_a_mirror_pulls_from_the_forge_of_record(
    tmp_path, role, mirroring, local_networks, domains
):
    parser = app_ini(ForgeAppIni.write(tmp_path, "gx10-1a2b", role))
    expected = SHUT_FOR_BOTH_ROLES | {
        ("mirror", "ENABLED"): mirroring,
        ("migrations", "ALLOW_LOCALNETWORKS"): local_networks,
        ("migrations", "ALLOWED_DOMAINS"): domains,
    }
    assert {key: parser.get(*key) for key in expected} == expected


def test_a_secret_left_blank_in_app_ini_is_made_again(tmp_path):
    path = ForgeAppIni.write(tmp_path, "gx10-1a2b", "record")
    secrets = ForgeAppIni.read_secrets(tmp_path)
    lines = [
        "SECRET_KEY =" if line.startswith("SECRET_KEY =") else line
        for line in path.read_text().splitlines()
    ]
    path.write_text("\n".join(lines) + "\n")
    assert ForgeAppIni.read_secrets(tmp_path) == {
        key: value for key, value in secrets.items() if key != "security.SECRET_KEY"
    }
    ForgeAppIni.write(tmp_path, "gx10-1a2b", "record")
    assert app_ini(path).get("security", "SECRET_KEY")


def test_the_switch_defaults_on_and_is_kept_in_the_nodes_config(monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_FORGE", raising=False)
    assert ForgeSettings.enabled()
    ForgeSettings.config_path().parent.mkdir(parents=True, exist_ok=True)
    ForgeSettings.config_path().write_text('[night]\nwindow = "22-06"\n')
    ForgeSettings.set_enabled(False)
    assert not ForgeSettings.enabled()
    assert '[night]\nwindow = "22-06"' in ForgeSettings.config_path().read_text()
    ForgeSettings.set_enabled(True)
    assert ForgeSettings.enabled() and ForgeSettings.config_path().read_text().count("[forge]") == 1
    monkeypatch.setenv("DREAMFERENCE_FORGE", "0")
    assert not ForgeSettings.enabled()


def test_the_switch_finds_a_forge_table_written_with_spaces_or_a_comment(monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_FORGE", raising=False)
    ForgeSettings.config_path().parent.mkdir(parents=True, exist_ok=True)
    for header in ("[forge]  # the switch", "[ forge ]"):
        ForgeSettings.config_path().write_text(f"{header}\nenabled = true\n")
        ForgeSettings.set_enabled(False)
        assert not ForgeSettings.enabled(), header
        assert ForgeSettings.config_path().read_text().count("forge") == 1, header


def test_a_stray_line_in_app_ini_does_not_cost_the_secrets(tmp_path):
    # New secrets would leave every sealed field (tokens, 2FA, mirror credentials) unreadable.
    path = ForgeAppIni.write(tmp_path, "gx10-1a2b", "record")
    secrets = ForgeAppIni.read_secrets(tmp_path)
    path.write_text(path.read_text() + "a line a hand edit left without an equals sign\n")
    ForgeAppIni.write(tmp_path, "gx10-1a2b", "record")
    assert ForgeAppIni.read_secrets(tmp_path) == secrets


@pytest.mark.parametrize("word", ["0", "false", "no", "off", " OFF ", "No"])
def test_every_off_word_in_any_case_turns_the_forge_off(monkeypatch, word):
    monkeypatch.setenv("DREAMFERENCE_FORGE", word)
    assert not ForgeSettings.enabled()


@pytest.mark.parametrize(
    "config, enabled",
    [
        ("[forge]\nenabled = 0\n", True),
        ("[forge]\nimage = 1\n", True),
        ("[ forge ]\nenabled = false\na line TOML cannot parse\n", False),
        ("[forge]\nenabled = False\n", False),
        ("[web]\nenabled = false\n[forge]\na line TOML cannot parse\n", True),
    ],
)
def test_only_a_written_false_turns_the_forge_off(monkeypatch, config, enabled):
    monkeypatch.delenv("DREAMFERENCE_FORGE", raising=False)
    ForgeSettings.config_path().parent.mkdir(parents=True, exist_ok=True)
    ForgeSettings.config_path().write_text(config)
    assert ForgeSettings.enabled() is enabled


@pytest.mark.parametrize(
    "config",
    [
        "[forge]\nenabled = true\n",
        '[forge]\nimage = "x"\n\n[night]\nwindow = "22-06"\n',
        '[night]\nwindow = "22-06"\n\n[forge]\nimage = "x"\n',
    ],
)
def test_the_switch_is_written_once_into_the_forge_table_the_config_has(monkeypatch, config):
    import tomllib

    monkeypatch.delenv("DREAMFERENCE_FORGE", raising=False)
    path = ForgeSettings.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config)
    path.chmod(0o600)
    ForgeSettings.set_enabled(False)
    text = path.read_text()
    assert tomllib.loads(text)["forge"]["enabled"] is False, text
    assert text.count("[forge]") == 1 and text.count("enabled") == 1, text
    assert oct(path.stat().st_mode & 0o777) == "0o600", "the config keeps its mode"


def test_an_empty_secret_reads_as_none_and_an_odd_state_as_none_set_up():
    ForgeSettings.write_secret("admin-token", "")
    assert ForgeSettings.read_secret("admin-token") is None
    ForgeSettings.write_secret("state.json", '["record"]')
    assert ForgeSettings.state() == {} and ForgeSettings.role() is None
    ForgeSettings.update_state(role="writer")
    assert ForgeSettings.role() is None


def test_a_forge_systemd_would_not_stop_is_not_reported_off(monkeypatch, capsys):
    # The container would still be published on the LAN.
    monkeypatch.setattr(
        ForgeService,
        "systemctl",
        classmethod(lambda cls, *a: subprocess.CompletedProcess(a, 1, "", "Job timed out")),
    )
    monkeypatch.setattr(ForgeService, "advertise", classmethod(lambda cls, role: None))
    assert ForgeService.off() == 1
    out = capsys.readouterr().out
    assert "✅" not in out and "Job timed out" in out


def test_secrets_are_private_and_the_role_is_kept():
    path = ForgeSettings.write_secret("owner-password", "x" * 24)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert ForgeSettings.role() is None
    ForgeSettings.update_state(role="mirror", record_node="abc")
    assert ForgeSettings.role() == "mirror" and ForgeSettings.state()["record_node"] == "abc"
    ForgeSettings.update_state(record_node=None)
    assert "record_node" not in ForgeSettings.state()


def test_the_first_node_is_the_record_and_a_found_record_makes_a_mirror(monkeypatch):
    monkeypatch.setattr(ForgeService, "find_record", classmethod(lambda cls: None))
    assert ForgeService.decide_role() == "record"
    ForgeSettings.state_path().unlink()
    monkeypatch.setattr(
        ForgeService,
        "find_record",
        classmethod(lambda cls: {"node": "n-1", "name": "gx10-1a2b", "role": "record"}),
    )
    assert ForgeService.decide_role() == "mirror"
    monkeypatch.setattr(ForgeService, "find_record", classmethod(lambda cls: None))
    assert ForgeService.decide_role() == "mirror", "a browse that misses the record never promotes"


def test_the_memory_allowance_is_the_cap_while_the_forge_runs(monkeypatch):
    from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

    monkeypatch.setattr(ForgeContainer, "running", classmethod(lambda cls: True))
    assert ForgeService.memory_allowance_gb() == 0.0, "never set up: docker is not asked"
    assert VLLMServerManager.side_service_allowance() == []
    ForgeSettings.update_state(role="record")
    assert ForgeService.memory_allowance_gb() == pytest.approx(1.0737, abs=1e-3)
    assert VLLMServerManager.side_service_allowance()[0][0].startswith("the forge")
    monkeypatch.setattr(ForgeContainer, "running", classmethod(lambda cls: False))
    assert ForgeService.memory_allowance_gb() == 0.0


def test_units_run_the_container_in_the_foreground_and_time_the_jobs(monkeypatch):
    monkeypatch.setattr(
        ForgeService, "admin_executable", classmethod(lambda cls: "/opt/ling-admin")
    )
    units = ForgeService.units()
    assert "ExecStart=/opt/ling-admin forge run" in units["mightling-forge.service"]
    assert "OnCalendar=*-*-* 06:30:00" in units["mightling-forge-backup.timer"]
    assert (
        "ExecStart=/opt/ling-admin forge github-mirror --run"
        in units["mightling-forge-mirror.service"]
    )
    assert "PathModified=" in units["mightling-forge-mirror.path"]
    assert "OnUnitActiveSec=10min" in units["mightling-forge-tick.timer"]


# -- the advertisement ---------------------------------------------------------------------------


def test_the_forge_is_a_second_service_in_the_nodes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(NodeServiceFile, "service_path", tmp_path / "mightling-node.service")
    text = NodeServiceFile.render(
        port=8000, node_id="n-1", version="1.7", state="ready", forge=("record", 3200)
    )
    assert text.count("<service>") == 2 and "<type>_mightling-forge._tcp</type>" in text
    records = NodeServiceFile.parse(text)
    assert records["port"] == "8000" and "role" not in records and records["node"] == "n-1"
    assert NodeServiceFile.parse_forge(text) == ("record", 3200)
    NodeServiceFile.service_path.write_text(text)
    assert NodeServiceFile.update(state="stopped")
    assert NodeServiceFile.parse_forge(NodeServiceFile.service_path.read_text()) == ("record", 3200)
    assert NodeServiceFile.update(forge=None)
    assert NodeServiceFile.parse_forge(NodeServiceFile.service_path.read_text()) is None
    assert NodeServiceFile.read()["state"] == "stopped"
    with pytest.raises(ValueError):
        NodeServiceFile.render(port=8000, node_id="n", version="1", forge=("writer", 3200))


# -- accounts --------------------------------------------------------------------------------------


def test_passwords_are_generated_by_forgejo_never_on_a_command_line(monkeypatch, capsys):
    calls = []

    def forgejo(cls, arguments, timeout=300):
        calls.append(arguments)
        if arguments[:3] == ["admin", "user", "create"]:
            return subprocess.CompletedProcess(
                arguments, 0, "generated random password is 'Abc123Abc123Abc123Abc123'\n", ""
            )
        return subprocess.CompletedProcess(
            arguments, 0, "0123456789abcdef0123456789abcdef01234567\n", ""
        )

    monkeypatch.setattr(ForgeContainer, "forgejo", classmethod(forgejo))
    ready, password = ForgeAccounts.ensure()
    assert ready and password == "Abc123Abc123Abc123Abc123"
    assert [c[3:5] for c in calls if c[2] == "create"] == [
        ["--username", "forge-admin"],
        ["--username", "owner"],
    ]
    assert "--admin" in calls[0] and "--admin" not in calls[2]
    assert all("Abc123" not in " ".join(c) for c in calls)
    assert ForgeAccounts.admin_token() == "0123456789abcdef0123456789abcdef01234567"
    assert ForgeAccounts.ensure() == (True, None), "made once"
    ForgeAccounts.show_password_once(password)
    out = capsys.readouterr().out
    assert password not in out and "forge password --show" in out, "not a terminal: never printed"


TOKEN = "0123456789abcdef0123456789abcdef01234567"
PASSWORD = "Abc123Abc123Abc123Abc123"


def forgejo_replies(monkeypatch, password=PASSWORD, token=TOKEN):
    """Answer `forgejo admin user` like Forgejo: a None password or token fails that command."""
    calls = []

    def forgejo(cls, arguments, timeout=300):
        calls.append(arguments)
        if arguments[2] == "create":
            out = f"generated random password is '{password}'\n" if password else ""
            return subprocess.CompletedProcess(arguments, 0 if password else 1, out, "")
        return subprocess.CompletedProcess(arguments, 0 if token else 1, f"{token or ''}\n", "")

    monkeypatch.setattr(ForgeContainer, "forgejo", classmethod(forgejo))
    return calls


def scopes(call):
    return call[call.index("--scopes") + 1]


@pytest.mark.parametrize(
    "code, stdout, expected",
    [
        (0, f"generated random password is '{PASSWORD}'\n", PASSWORD),
        (1, f"generated random password is '{PASSWORD}'\nbut the account was not saved\n", None),
        (0, "the account was created\n", None),
    ],
)
def test_an_account_counts_as_made_only_when_forgejo_succeeds_and_names_its_password(
    monkeypatch, code, stdout, expected
):
    monkeypatch.setattr(
        ForgeContainer,
        "forgejo",
        classmethod(lambda cls, a, timeout=300: subprocess.CompletedProcess(a, code, stdout, "")),
    )
    assert ForgeAccounts.create_user("owner", admin=False) == expected


@pytest.mark.parametrize(
    "code, stdout, expected",
    [
        (0, f"{TOKEN}\n", TOKEN),
        (0, f"a warning first\n{TOKEN}\n", TOKEN),
        (1, f"{TOKEN}\n", None),
        (0, "Error: a token of that name exists\n", None),
        (0, "0123abcd\n", None),
    ],
)
def test_a_token_is_the_last_line_of_forty_hex_digits_of_a_command_that_succeeded(
    monkeypatch, code, stdout, expected
):
    monkeypatch.setattr(
        ForgeContainer,
        "forgejo",
        classmethod(lambda cls, a, timeout=300: subprocess.CompletedProcess(a, code, stdout, "")),
    )
    assert ForgeAccounts.generate_token("forge-admin", "ling-admin", "all") == expected


def test_the_admin_keeps_its_password_and_a_token_for_everything_and_no_one_must_change_one(
    monkeypatch,
):
    calls = forgejo_replies(monkeypatch)
    assert ForgeAccounts.ensure() == (True, PASSWORD)
    assert ForgeSettings.read_secret("admin-password") == PASSWORD
    assert [scopes(c) for c in calls if c[2] == "generate-access-token"] == ["all"]
    # The owner signs in with the password `forge password --show` prints.
    assert all("--must-change-password=false" in c for c in calls if c[2] == "create")


def test_an_admin_whose_password_is_kept_is_not_made_again(monkeypatch):
    ForgeSettings.write_secret("admin-password", "kept")
    calls = forgejo_replies(monkeypatch)
    assert ForgeAccounts.ensure() == (True, PASSWORD)
    assert [c[4] for c in calls if c[2] == "create"] == ["owner"]
    assert ForgeSettings.read_secret("admin-password") == "kept"


@pytest.mark.parametrize("password, token", [(None, TOKEN), (PASSWORD, None)])
def test_an_admin_forgejo_would_not_make_stops_the_setup_before_the_owner(
    monkeypatch, capsys, password, token
):
    calls = forgejo_replies(monkeypatch, password, token)
    assert ForgeAccounts.ensure() == (False, None)
    assert ForgeAccounts.admin_token() is None
    assert ForgeSettings.read_secret("admin-password") == password
    assert not [c for c in calls if "owner" in c]
    assert "❌" in capsys.readouterr().out


@pytest.mark.parametrize(
    "status, body, expected",
    [(200, {"is_admin": True}, True), (200, {"is_admin": False}, False), (500, {}, None)],
)
def test_the_owner_is_reported_an_admin_only_when_the_forge_says_so(
    monkeypatch, status, body, expected
):
    monkeypatch.setattr(ForgeContainer, "api", classmethod(lambda cls, *a, **k: (status, body)))
    assert ForgeAccounts.owner_is_admin() is expected


def test_the_mirror_account_is_plain_reads_only_and_its_token_is_made_once(monkeypatch):
    calls = forgejo_replies(monkeypatch)
    account = {"status": 404}
    asked = []
    monkeypatch.setattr(
        ForgeContainer,
        "api",
        classmethod(
            lambda cls, method, path, *a, **k: asked.append(path) or (account["status"], {})
        ),
    )
    assert ForgeAccounts.mirror_token() == TOKEN
    created = [c for c in calls if c[2] == "create"]
    assert [c[4] for c in created] == ["mirror"] and "--admin" not in created[0]
    assert [scopes(c) for c in calls if c[2] == "generate-access-token"] == ["read:repository"]
    assert ForgeSettings.read_secret("mirror-token") == TOKEN
    calls.clear()
    asked.clear()
    assert ForgeAccounts.mirror_token() == TOKEN and not calls and not asked, "kept"
    # An account that exists gets a token, not a second account.
    ForgeSettings.secret_path("mirror-token").unlink()
    account["status"] = 200
    assert ForgeAccounts.mirror_token() == TOKEN
    assert [c[2] for c in calls] == ["generate-access-token"]
    # A token Forgejo would not make leaves nothing behind.
    ForgeSettings.secret_path("mirror-token").unlink()
    forgejo_replies(monkeypatch, token=None)
    assert ForgeAccounts.mirror_token() is None
    assert not ForgeSettings.secret_path("mirror-token").exists()


# -- the anonymity check ---------------------------------------------------------------------------


def test_the_forge_check_keeps_the_anonymity_tests_rules():
    from dreamference.forge import forge_anonymity

    assert {str(n) for n in test_anonymity.EXAMPLE_LAN_SUBNETS} == set(
        forge_anonymity.EXAMPLE_LAN_SUBNETS
    )
    assert set(test_anonymity.EXAMPLE_OVERLAY_ADDRESSES) == set(
        forge_anonymity.EXAMPLE_OVERLAY_ADDRESSES
    )
    assert set(test_anonymity.PLACEHOLDER_USERS) == set(forge_anonymity.PLACEHOLDER_USERS)


@pytest.mark.parametrize(
    "line",
    [
        f"connect to {LAN}.7.105:8000",
        f"peer 100.{90}.1.2",
        "run " + "/ho" + "me/somebody/bin",
        "file:///" + "home/somebody/x",
        "C:" + "\\Users\\somebody\\x",
        f"{LAN}.50.105 100.101.102.103 /home/user/mightling 10.0.0.2 172.17.0.1",
        f"version 1.{LAN}.0.5 and 2001.{LAN}.0",
        "https://example.com/home/kitchen and results/run-1/home/sessions",
        "/home/" + "USER/x",
        f"{LAN}.07.1",
    ],
)
def test_the_generic_patterns_find_what_the_anonymity_test_finds(line):
    patterns = ForgeAnonymity.generic_patterns()
    assert bool(ForgeAnonymity.findings(line, patterns)) == bool(
        test_anonymity.generic_findings(line)
    ), line


def test_denylist_entries_are_whole_words_and_literal_when_broken():
    patterns = ForgeAnonymity.denylist_patterns("# c\n\nnode-q\n\\w*zed\\w*\n[unclosed\n")
    assert len(patterns) == 3
    assert ForgeAnonymity.findings("ssh NODE-Q.local", patterns) and not ForgeAnonymity.findings(
        "node-q2", patterns
    )
    assert ForgeAnonymity.findings("realized", patterns) and ForgeAnonymity.findings(
        "a [unclosed b", patterns
    )


def test_added_lines_name_their_file_and_skip_exempt_ones():
    patch = (
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+hello\n"
        "diff --git a/tests/test_anonymity.py b/tests/test_anonymity.py\n+++ b/tests/test_anonymity.py\n+secret\n"
        "diff --git a/gone b/gone\n+++ /dev/null\n-bye\n"
    )
    assert ForgeAnonymity.added_lines(patch, ["tests/test_anonymity.py"]) == "x\nx: hello\n"


def test_the_mirror_scans_a_path_the_hook_cannot_exempt():
    # The hook passes exempt paths to awk only when they are plain; one with a space is scanned
    # there, so the GitHub mirror's own scan must not skip it either.
    patch = "diff --git a/my notes b/my notes\n+++ b/my notes\n@@ -0,0 +1 @@\n+secret\n"
    assert ForgeAnonymity.added_lines(patch, ["my notes"]) == "my notes\nmy notes: secret\n"
    assert "my notes" not in ForgeAnonymity.hook_script("full", ["my notes"])


@pytest.fixture
def hooked_repository(tmp_path):
    """A bare repository with the forge's hook as its pre-receive, the patterns beside it."""
    (tmp_path / "probe").write_text("x\n")
    if (
        GIT is None
        or subprocess.run(
            ["git", "grep", "--no-index", "-q", "-P", "(?<!y)x", "--", "probe"],
            cwd=tmp_path,
            capture_output=True,
        ).returncode
        != 0
    ):
        pytest.skip("git without PCRE")
    patterns = tmp_path / "mightling"
    patterns.mkdir()
    (patterns / "anonymity-full.pcre").write_text(
        "\n".join(ForgeAnonymity.patterns("full", "node-q\n")) + "\n"
    )
    bare = tmp_path / "forge.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(
        ForgeAnonymity.hook_script(
            "full", ["tests/test_anonymity.py"], True, directory=str(patterns)
        )
    )
    hook.chmod(0o755)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for key, value in (("user.name", "dgxcoder"), ("user.email", "dgxcoder@example.com")):
        subprocess.run(["git", "-C", str(work), "config", key, value], check=True)
    return bare, work, patterns


def commit(work, name, text, message="m"):
    path = work / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", message], check=True)


def push(work, bare, ref="main"):
    return subprocess.run(
        ["git", "-C", str(work), "push", str(bare), ref], capture_output=True, text=True
    )


def test_the_hook_refuses_identifiers_and_foreign_histories(hooked_repository):
    bare, work, patterns = hooked_repository
    commit(work, "a.txt", "fine\n")
    assert push(work, bare).returncode == 0
    commit(work, "b.txt", f"reach {LAN}.7.105\n")
    refused = push(work, bare)
    assert (
        refused.returncode != 0
        and "anonymity check" in refused.stderr
        and "b.txt" in refused.stderr
    )
    subprocess.run(["git", "-C", str(work), "reset", "-q", "--hard", "HEAD~1"], check=True)
    commit(work, "c.txt", "see you\n", message="ask NODE-Q about it")
    assert "anonymity check" in push(work, bare).stderr, "commit messages are checked"
    subprocess.run(["git", "-C", str(work), "reset", "-q", "--hard", "HEAD~1"], check=True)
    commit(work, "tests/test_anonymity.py", f"FIXTURE = '{LAN}.7.105'\n")
    commit(work, "d.txt", f"examples: {LAN}.50.3 and /home/user\n")
    assert push(work, bare).returncode == 0, "exempt file and placeholders pass"
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "--orphan", "old"], check=True)
    commit(work, "e.txt", "old history\n")
    refused = push(work, bare, "old")
    assert refused.returncode != 0 and "root commit" in refused.stderr
    (patterns / "frozen").write_text("1")
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)
    commit(work, "f.txt", "later\n")
    assert "frozen" in push(work, bare).stderr


@pytest.mark.parametrize(
    "header", ["++ /dev/null", "++ b/tests/test_anonymity.py", "x\rdiff --git a/q b/q"]
)
def test_a_line_shaped_like_a_file_header_does_not_end_the_scan(hooked_repository, header):
    # The line shows as `+++ …` in the patch; read as a header, it hid the lines after it.
    bare, work, _ = hooked_repository
    commit(work, "a.txt", "fine\n")
    assert push(work, bare).returncode == 0
    commit(work, "b.txt", f"{header}\nreach {LAN}.7.105\n")
    assert f"b.txt: reach {LAN}.7.105" in ForgeAnonymity.added_text(
        work, ["HEAD"], ["HEAD~1"], ["tests/test_anonymity.py"]
    )
    refused = push(work, bare)
    assert refused.returncode != 0 and "anonymity check" in refused.stderr


@pytest.mark.parametrize("parents", [2, 3])
def test_lines_a_merge_adds_itself_are_checked(hooked_repository, parents):
    # `git log -p` shows no patch for a merge: a line its resolution added went unread.
    bare, work, _ = hooked_repository
    commit(work, "a.txt", "one\n")
    assert push(work, bare).returncode == 0
    known = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    sides = [f"side{n}" for n in range(1, parents)]
    for side in sides:
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", side, known], check=True)
        commit(work, f"{side}.txt", "side\n")
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(work), "merge", "-q", "--no-ff", "--no-commit", *sides], check=True
    )
    (work / "a.txt").write_text(f"one\n+ reach {LAN}.7.105\n")
    subprocess.run(["git", "-C", str(work), "commit", "-qam", "merge"], check=True)
    added = ForgeAnonymity.added_text(work, ["HEAD"], [known], ["tests/test_anonymity.py"])
    assert f"a.txt: {'+' * (parents - 1)}+ reach {LAN}.7.105" in added
    refused = push(work, bare)
    assert refused.returncode != 0 and "anonymity check" in refused.stderr


# -- storage ---------------------------------------------------------------------------------------


def test_the_budget_prunes_artefacts_before_logs_and_never_repositories(monkeypatch):
    data = ForgeSettings.data_dir()
    for relative, size in (
        ("data/actions_artifacts/a.zip", 3),
        ("data/actions_log/l.log", 2),
        ("git/repositories/o/r.git/objects/pack/p.pack", 5),
    ):
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    monkeypatch.setattr("dreamference.forge.forge_storage.GB", 1)
    assert ForgeStorage.prune(free_gb=200, night=False) == []
    lines = ForgeStorage.prune(free_gb=157, night=False)
    assert "pruning" in lines[0] and not (data / "data/actions_artifacts/a.zip").exists()
    assert (data / "data/actions_log/l.log").exists(), "artefacts were enough"
    assert (data / "git/repositories/o/r.git/objects/pack/p.pack").exists()
    lines = ForgeStorage.prune(free_gb=180, night=True)
    assert lines and not (data / "data/actions_log/l.log").exists(), "a night's floor is 175 GB"
    assert "Still below the floor" in lines[-1]


def test_the_usage_counts_logs_and_dumps_never_backups_or_links_and_never_below_zero(tmp_path):
    data, share = ForgeSettings.data_dir(), ForgeSettings.share_dir()
    for relative, size in (
        (data / "data/actions_artifacts/a.zip", 1),
        (data / "data/actions_log/l.log", 2),
        (data / "log/forgejo.log", 4),
        (data / "dumps/d.zip", 8),
        (share / "dumps/s.zip", 16),
        (share / "backups/forge-20261027-063000/dump.tar.gz", 1000),
        (data / "data/forgejo.db", 32),
        (tmp_path / "elsewhere/big.pack", 5000),
    ):
        relative.parent.mkdir(parents=True, exist_ok=True)
        relative.write_bytes(b"x" * size)
    (data / "data/linked.pack").symlink_to(tmp_path / "elsewhere/big.pack")
    assert ForgeStorage.size(data / "data") == 1 + 2 + 32, "a link is not counted"
    usage = ForgeStorage.usage()
    assert usage == {
        "repositories": 0,
        "logs_artefacts": 7,
        "dumps": 24,
        "other": 32,
        "total": 63,
    }
    # Repositories kept on another disk through a link are counted, the rest never below 0.
    (data / "git").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    assert ForgeStorage.usage()["repositories"] == 5000
    assert ForgeStorage.usage()["other"] == 0


@pytest.mark.parametrize(
    "stdout, night", [("ling-swe-night.service loaded active\n", True), ("\n", False)]
)
def test_a_night_runs_when_a_swe_bench_unit_is_active(monkeypatch, stdout, night):
    monkeypatch.setattr(
        "dreamference.forge.forge_storage.subprocess.run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, stdout, ""),
    )
    assert ForgeStorage.night_running() is night


def test_the_prune_takes_each_kind_oldest_first_and_files_only():
    artefacts = ForgeSettings.data_dir() / "data/actions_artifacts"
    # Named and nested out of their age order: c is the oldest, d the newest.
    oldest_first = [
        artefacts / name for name in ("run-1/c.zip", "a.zip", "run-1/e.zip", "b.zip", "d.zip")
    ]
    oldest_first += [
        ForgeSettings.data_dir() / "log/forgejo.log",
        ForgeSettings.share_dir() / "dumps/d.zip",
    ]
    for path in oldest_first:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    for age, path in enumerate(oldest_first[:5]):
        os.utime(path, (1_000_000 + age, 1_000_000 + age))
    assert ForgeStorage.candidates() == oldest_first


def test_the_prune_stops_at_the_floor_plus_the_margin_and_goes_past_a_file_it_cannot_remove(
    monkeypatch,
):
    folder = ForgeSettings.data_dir() / "log"
    folder.mkdir(parents=True)
    kept = folder / "kept.log"
    kept.write_bytes(b"x" * 5)
    monkeypatch.setattr("dreamference.forge.forge_storage.GB", 1)
    assert ForgeStorage.prune(free_gb=160, night=False) == [], "150 GB plus 10 is enough"
    monkeypatch.setattr(
        ForgeStorage, "candidates", classmethod(lambda cls: [folder / "gone.log", kept])
    )
    lines = ForgeStorage.prune(free_gb=159, night=False)
    assert not kept.exists() and "Still below" not in lines[-1]


# -- backups ---------------------------------------------------------------------------------------


def test_retention_keeps_seven_days_and_four_weeks():
    names = [f"forge-202610{day:02d}-063000" for day in range(1, 28)] + ["forge-20261027-120000"]
    kept = ForgeBackup.kept(names)
    assert kept[:7] == ["forge-20261027-120000"] + [
        f"forge-202610{d:02d}-063000" for d in range(26, 20, -1)
    ]
    assert len(kept) == 11 and len(set(kept)) == 11


def test_retention_keeps_the_newest_of_each_of_the_four_weeks_before_the_seven_days():
    start = datetime.date(2026, 8, 31)
    names = [f"forge-{start + datetime.timedelta(days):%Y%m%d}-063000" for days in range(60)]
    names.append("forge-20261029-180000")
    assert ForgeBackup.kept(names) == [
        "forge-20261029-180000",
        *(f"forge-202610{day}-063000" for day in range(28, 21, -1)),
        # The newest left of each week (Monday to Sunday): Thursday the 22nd, then Sundays.
        "forge-20261018-063000",
        "forge-20261011-063000",
        "forge-20261004-063000",
    ]


def test_the_retention_removes_old_sets_keeps_bundles_in_the_newest_and_leaves_other_folders(
    tmp_path,
):
    names = [f"forge-202610{day:02d}-063000" for day in range(1, 29)]
    for name in names:
        (tmp_path / name / "bundles").mkdir(parents=True)
    (tmp_path / "notes").mkdir()
    ForgeBackup.keep(tmp_path)
    left = sorted(item.name for item in tmp_path.iterdir())
    assert left == sorted([*ForgeBackup.kept(names), "notes"]) and len(left) < len(names)
    assert [name for name in left if (tmp_path / name / "bundles").exists()] == [names[-1]]


def test_the_latest_set_is_the_newest_received_or_own():
    (ForgeBackup.received_dir() / "forge-20261028-063000").mkdir(parents=True)
    (ForgeBackup.local_dir() / "forge-20261029-063000").mkdir(parents=True)
    assert ForgeBackup.latest().name == "forge-20261029-063000"


def make_set(folder: Path, repositories: Path) -> dict:
    """A backup set as `take` writes it, around a real bare repository."""
    folder.mkdir(parents=True)
    dump = io.BytesIO()
    with tarfile.open(fileobj=dump, mode="w:gz") as archive:
        archive.add(repositories, arcname="repos")
        sql = (
            b"CREATE TABLE `user` (`id` INTEGER PRIMARY KEY, `name` TEXT);\n"
            b"INSERT INTO `user` VALUES (1, unistr('own\\0065r'));\n"
        )
        info = tarfile.TarInfo("forgejo-db.sql")
        info.size = len(sql)
        archive.addfile(info, io.BytesIO(sql))
        ini = b"[security]\nSECRET_KEY = kept\n"
        info = tarfile.TarInfo("custom/conf/app.ini")
        info.size = len(ini)
        archive.addfile(info, io.BytesIO(ini))
    (folder / "dump.tar.gz").write_bytes(dump.getvalue())
    manifest = {"set": folder.name, "refs": ForgeBackup.repository_refs(repositories)}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    (folder / "secrets.json").write_text(json.dumps({"owner-password": "p" * 24}))
    return manifest


@pytest.fixture
def repositories(tmp_path):
    if GIT is None:
        pytest.skip("no git")
    root = tmp_path / "repos"
    work = tmp_path / "w"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.name=d",
            "-c",
            "user.email=d@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "one",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(work), str(root / "owner" / "demo.git")], check=True
    )
    return root


def test_a_set_restores_with_every_ref_and_its_database(tmp_path, repositories):
    folder = tmp_path / "sets" / "forge-20261027-063000"
    manifest = make_set(folder, repositories)
    target = tmp_path / "restored"
    assert ForgeBackup.unpack(folder, target) == manifest
    assert ForgeBackup.compare(manifest, target / "git" / "repositories") == []
    import sqlite3

    assert sqlite3.connect(target / "data" / "forgejo.db").execute(
        "select name from user"
    ).fetchone() == ("owner",)
    assert "SECRET_KEY = kept" in (target / "custom" / "conf" / "app.ini").read_text()
    assert ForgeBackup.check(folder) == 0
    manifest["refs"]["owner/demo"]["refs/heads/main"] = "0" * 40
    (folder / "manifest.json").write_text(json.dumps(manifest))
    assert ForgeBackup.check(folder) == 1


def test_receiving_a_set_refuses_odd_names_and_keeps_the_newest_bundles(tmp_path, repositories):
    assert ForgeBackup.receive("../../etc", io.BytesIO(b"")) == 2
    for name in ("forge-20261026-063000", "forge-20261027-063000"):
        source = tmp_path / "out" / name
        make_set(source, repositories)
        (source / "bundles").mkdir()
        (source / "bundles" / "owner__demo.bundle").write_bytes(b"b")
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            archive.add(source, arcname=name)
        stream.seek(0)
        assert ForgeBackup.receive(name, stream) == 0
    root = ForgeBackup.received_dir()
    assert sorted(p.name for p in root.iterdir()) == [
        "forge-20261026-063000",
        "forge-20261027-063000",
    ]
    assert not (root / "forge-20261026-063000" / "bundles").exists()
    assert (root / "forge-20261027-063000" / "bundles" / "owner__demo.bundle").exists()
    assert oct(root.stat().st_mode & 0o777) == "0o700"
    assert ForgeBackup.latest().name == "forge-20261027-063000"


# -- the GitHub mirror -------------------------------------------------------------------------------


def git(*arguments, cwd=None):
    return subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_the_mirror_plan_sends_what_is_ahead_brings_back_what_is_behind_and_never_forces(tmp_path):
    if GIT is None:
        pytest.skip("no git")
    work = tmp_path / "work.git"
    git("init", "-q", "--bare", str(work))
    source = tmp_path / "src"
    git("init", "-q", "-b", "main", str(source))

    def make(message):
        git(
            "-C",
            str(source),
            "-c",
            "user.name=d",
            "-c",
            "user.email=d@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
        )
        return git("-C", str(source), "rev-parse", "HEAD")

    base, ahead = make("base"), make("ahead")
    git("-C", str(source), "checkout", "-q", "-b", "side", base)
    side = make("side")
    git("-C", str(work), "fetch", "-q", str(source), "main", "side")
    refs = {
        "refs/forge/heads/main": ahead,
        "refs/github/heads/main": base,  # forge ahead → GitHub
        "refs/forge/heads/only-forge": base,  # new on the forge → GitHub
        "refs/github/heads/release": base,  # only on GitHub → forge
        "refs/forge/heads/fork": side,
        "refs/github/heads/fork": ahead,  # diverged → reported
        "refs/github/tags/v1": base,  # GitHub's tag → forge
        "refs/forge/heads/behind": base,
        "refs/github/heads/behind": ahead,  # GitHub ahead → forge
        "refs/forge/heads/same": base,
        "refs/github/heads/same": base,  # the same on both → nothing
        "refs/forge/tags/v2": ahead,
        "refs/github/tags/v2": base,  # a tag moved on the forge → reported, never moved
        "refs/forge/tags/v3": base,
        "refs/github/tags/v3": ahead,  # a tag moved on GitHub → reported, never moved
    }
    for ref, sha in refs.items():
        git("-C", str(work), "update-ref", ref, sha)
    plan = ForgeGithubMirror.plan(work)
    assert sorted(plan["to_github"]) == [
        "refs/forge/heads/main:refs/heads/main",
        "refs/forge/heads/only-forge:refs/heads/only-forge",
    ]
    assert sorted(plan["to_forge"]) == [
        "refs/github/heads/behind:refs/heads/behind",
        "refs/github/heads/release:refs/heads/release",
        "refs/github/tags/v1:refs/tags/v1",
    ]
    assert plan["diverged"] == ["refs/heads/fork", "refs/tags/v2", "refs/tags/v3"]
    assert not any(spec.startswith("+") for spec in plan["to_github"] + plan["to_forge"])


def test_the_github_credential_comes_from_gh_at_run_time():
    arguments = " ".join(ForgeGithubMirror.github_credential_arguments())
    assert "gh auth token --user" in arguments and "x-access-token" in arguments
    assert "ghp_" not in arguments and "gho_" not in arguments


def test_one_refused_branch_holds_back_no_other_and_the_cause_names_file_and_line(tmp_path):
    if GIT is None:
        pytest.skip("no git")
    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(remote))
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\nwhile read old new ref; do\n"
        '  if [ "$ref" = refs/heads/bad ]; then\n'
        '    echo "remote: Refused by the forge\'s anonymity check: replace it" >&2\n'
        '    echo "remote: added:7:bench/w6.steps: HUB=/home/user/.cache" >&2\n'
        "    exit 1\n  fi\ndone\n"
    )
    hook.chmod(0o755)
    work = tmp_path / "work.git"
    git("init", "-q", "--bare", str(work))
    tree = subprocess.run(
        ["git", "-C", str(work), "mktree"], input="", text=True, capture_output=True, check=True
    ).stdout.strip()
    identity = {
        "GIT_AUTHOR_NAME": "d",
        "GIT_AUTHOR_EMAIL": "d@example.com",
        "GIT_COMMITTER_NAME": "d",
        "GIT_COMMITTER_EMAIL": "d@example.com",
        "PATH": "/usr/bin:/bin",
    }
    commit = subprocess.run(
        ["git", "-C", str(work), "commit-tree", tree, "-m", "m"],
        capture_output=True,
        text=True,
        check=True,
        env=identity,
    ).stdout.strip()
    refspecs = []
    for name in ("a", "bad", "z"):
        git("-C", str(work), "update-ref", f"refs/forge/heads/{name}", commit)
        refspecs.append(f"refs/forge/heads/{name}:refs/heads/{name}")
    outcome = ForgeGithubMirror.push_each(work, ["push", "--quiet", str(remote)], refspecs, None)
    assert outcome["pushed"] == 2 and outcome["backoff"] == ""
    assert list(outcome["failures"]) == ["refs/heads/bad"]
    cause = outcome["failures"]["refs/heads/bad"]
    assert "anonymity" in cause and "added:7:bench/w6.steps" in cause
    assert "/home/user" not in cause
    assert git("-C", str(remote), "for-each-ref", "--format=%(refname)").split() == [
        "refs/heads/a",
        "refs/heads/z",
    ]


def test_a_github_rate_limit_backs_off_to_the_next_tick(monkeypatch, tmp_path):
    calls = []

    def throttled(cls, work, arguments, environment=None, timeout=0):
        calls.append(arguments[-1])
        return subprocess.CompletedProcess(
            arguments, 1, "", "remote: You have exceeded a secondary rate limit.\nfatal: x"
        )

    monkeypatch.setattr(ForgeRepositories, "git", classmethod(throttled))
    outcome = ForgeGithubMirror.push_each(tmp_path, ["push"], ["a:b", "c:d"], None)
    assert calls == ["a:b"]  # nothing more is tried this tick
    assert "rate limit" in outcome["backoff"] and not outcome["failures"]


@pytest.mark.parametrize(
    "stderr",
    [
        "remote: Permission to o/r.git denied to dgxcoder.\nfatal: unable to access "
        "'https://github.com/o/r/': The requested URL returned error: 403",
        "remote: Write access to repository not granted.\nfatal: unable to access "
        "'https://github.com/o/r/': The requested URL returned error: 403",
    ],
)
def test_a_403_that_refuses_the_token_is_a_failure_not_a_rate_limit(monkeypatch, tmp_path, stderr):
    def refused(cls, work, arguments, environment=None, timeout=0):
        return subprocess.CompletedProcess(arguments, 128, "", stderr)

    monkeypatch.setattr(ForgeRepositories, "git", classmethod(refused))
    outcome = ForgeGithubMirror.push_each(tmp_path, ["push"], ["a:b", "c:d"], None)
    assert not outcome["backoff"] and set(outcome["failures"]) == {"b", "d"}


def _one_repository_run(monkeypatch, result):
    ForgeSettings.update_state(role="record")
    ForgeGithubMirror.save_settings(True, "dgxcoder")
    monkeypatch.setattr(ForgeAccounts, "admin_token", classmethod(lambda cls: "t"))
    monkeypatch.setattr(
        ForgeRepositories,
        "all",
        classmethod(lambda cls, token=None: [{"full_name": "o/r", "name": "r", "private": False}]),
    )
    monkeypatch.setattr(
        ForgeRepositories,
        "config",
        classmethod(lambda cls: {"repositories": {"o/r": {"github": "https://github.com/o/r"}}}),
    )
    monkeypatch.setattr(ForgeGithubMirror, "mirror_one", classmethod(lambda cls, *a: result))
    return ForgeGithubMirror.run()


def test_a_failed_branch_is_printed_with_its_cause_and_the_run_exits_1(monkeypatch, capsys):
    code = _one_repository_run(
        monkeypatch,
        {"ok": False, "failures": {"refs/heads/x": "push to GitHub failed: nope"}, "held": {}},
    )
    assert code == 1
    assert "o/r refs/heads/x: push to GitHub failed: nope" in capsys.readouterr().out


def test_held_and_throttled_branches_are_printed_but_do_not_fail_the_run(monkeypatch, capsys):
    code = _one_repository_run(
        monkeypatch,
        {
            "ok": True,
            "failures": {},
            "held": {"refs/heads/d": "diverged both ways: never forced"},
            "backoff": "rate limit",
        },
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "refs/heads/d: held: diverged" in out and "retrying next tick" in out
    from dreamference.forge.forge_command import ForgeCommand

    summary = ForgeCommand.github_summary(ForgeGithubMirror.status())
    assert "1 branches held" in summary and "throttled" in summary


def test_a_failed_fetch_is_printed(monkeypatch, capsys):
    code = _one_repository_run(monkeypatch, {"ok": False, "error": "fetch from GitHub failed: x"})
    assert code == 1
    assert "o/r: fetch from GitHub failed: x" in capsys.readouterr().out


def _mirror_one_with(monkeypatch, plan, fetched=None, sent=None, brought=None):
    monkeypatch.setattr(ForgeGithubMirror, "fetch", classmethod(lambda cls, *a: fetched))
    monkeypatch.setattr(ForgeGithubMirror, "plan", classmethod(lambda cls, work: plan))
    monkeypatch.setattr(
        ForgeGithubMirror,
        "send_to_github",
        classmethod(lambda cls, *a: sent or pytest.fail("sent to GitHub")),
    )
    monkeypatch.setattr(
        ForgeGithubMirror,
        "bring_back",
        classmethod(lambda cls, *a: brought or pytest.fail("brought back")),
    )
    return ForgeGithubMirror.mirror_one("o/r", "https://github.com/o/r", "t")


def test_a_failed_fetch_ends_the_repository_before_any_plan(monkeypatch):
    error = "fetch from GitHub failed: x"
    assert _mirror_one_with(monkeypatch, None, fetched=error) == {"ok": False, "error": error}


def test_an_anonymity_stop_before_github_is_the_repositorys_whole_answer(monkeypatch):
    stop = {"ok": False, "stop": True, "error": "the anonymity re-check found 1 identifiers"}
    plan = {"to_github": ["refs/forge/heads/a:refs/heads/a"], "to_forge": ["x:y"], "diverged": []}
    assert _mirror_one_with(monkeypatch, plan, sent=stop) == stop


def test_what_the_forges_anonymity_check_refuses_is_held_and_any_other_refusal_fails(monkeypatch):
    plan = {
        "to_github": [],
        "to_forge": ["refs/github/heads/a:refs/heads/a", "refs/github/heads/b:refs/heads/b"],
        "diverged": ["refs/heads/d"],
    }
    brought = {
        "pushed": 0,
        "failures": {
            "refs/heads/a": "Refused by the forge's anonymity check: replace it",
            "refs/heads/b": "fatal: the forge did not answer",
        },
        "backoff": "",
    }
    result = _mirror_one_with(monkeypatch, plan, brought=brought)
    assert result["ok"] is False
    assert result["failures"] == {"refs/heads/b": "fatal: the forge did not answer"}
    assert set(result["held"]) == {"refs/heads/a", "refs/heads/d"}


def test_a_rate_limit_in_capitals_still_backs_off(monkeypatch, tmp_path):
    def throttled(cls, work, arguments, environment=None, timeout=0):
        return subprocess.CompletedProcess(arguments, 1, "", "remote: API RATE LIMIT EXCEEDED")

    monkeypatch.setattr(ForgeRepositories, "git", classmethod(throttled))
    outcome = ForgeGithubMirror.push_each(tmp_path, ["push"], ["a:b", "c:d"], None)
    assert outcome["backoff"] and not outcome["failures"]


@pytest.mark.parametrize(
    "codes, error",
    [
        ([128], "fetch from the forge failed: boom"),
        ([0, 128], "fetch from GitHub failed: boom"),
        ([0, 0], None),
    ],
)
def test_the_fetches_prune_and_the_first_failure_ends_them(monkeypatch, tmp_path, codes, error):
    calls = []

    def fetch(cls, work, arguments, environment=None, timeout=0):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, codes[len(calls) - 1], "", "boom\n")

    monkeypatch.setattr(ForgeRepositories, "git", classmethod(fetch))
    assert ForgeGithubMirror.fetch(tmp_path, "o/r", "https://github.com/o/r", {}, {}) == error
    assert len(calls) == len(codes)
    assert all("--prune" in arguments and "--no-tags" in arguments for arguments in calls)


@pytest.mark.parametrize("role, enabled", [("mirror", True), ("record", False), (None, True)])
def test_only_an_enabled_forge_of_record_runs_the_github_mirror(monkeypatch, role, enabled):
    if role:
        ForgeSettings.update_state(role=role)
    ForgeGithubMirror.save_settings(enabled, "dgxcoder")
    monkeypatch.setattr(
        ForgeGithubMirror, "run_locked", classmethod(lambda cls: pytest.fail("mirrored"))
    )
    assert ForgeGithubMirror.run() == 0


def _run_over(monkeypatch, repositories, result=None):
    ForgeSettings.update_state(role="record")
    ForgeGithubMirror.save_settings(True, "dgxcoder")
    monkeypatch.setattr(ForgeAccounts, "admin_token", classmethod(lambda cls: "t"))
    monkeypatch.setattr(
        ForgeRepositories,
        "all",
        classmethod(lambda cls, token=None: [{"full_name": "o/r", "name": "r", "private": False}]),
    )
    monkeypatch.setattr(
        ForgeRepositories, "config", classmethod(lambda cls: {"repositories": repositories})
    )
    issues, mirrored = [], []
    monkeypatch.setattr(
        ForgeGithubMirror, "open_issue", classmethod(lambda cls, *a: issues.append(a))
    )
    monkeypatch.setattr(
        ForgeGithubMirror,
        "mirror_one",
        classmethod(lambda cls, name, *a: mirrored.append(name) or result),
    )
    return ForgeGithubMirror.run(), mirrored, issues


def test_a_repository_the_forge_lacks_or_a_destination_off_github_is_skipped(monkeypatch):
    code, mirrored, _ = _run_over(
        monkeypatch,
        {
            "o/gone": {"github": "https://github.com/o/gone"},
            "o/r": {"github": "https://gitlab.com/o/r"},
        },
    )
    assert code == 0 and mirrored == []
    repositories = ForgeGithubMirror.status()["repositories"]
    assert "not on the forge: skipped" in repositories["o/gone"]["error"]
    assert repositories["o/r"]["error"] == "not a GitHub repository URL: skipped"


def test_a_stopped_repository_stops_the_run_and_opens_an_issue(monkeypatch):
    stop = {"ok": False, "stop": True, "error": "the anonymity re-check found 1 identifiers"}
    code, mirrored, issues = _run_over(
        monkeypatch, {"o/r": {"github": "https://github.com/o/r"}}, stop
    )
    assert code == 2 and mirrored == ["o/r"]
    assert issues == [("o/r", stop["error"])]
    assert ForgeGithubMirror.status()["stopped"] == f"o/r: {stop['error']}"


def test_airgapped_on_stops_the_mirror(monkeypatch):
    ForgeSettings.update_state(role="record")
    ForgeGithubMirror.save_settings(True, "dgxcoder")
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "on")
    monkeypatch.setattr(
        ForgeGithubMirror, "mirror_one", classmethod(lambda *a: pytest.fail("sent while airgapped"))
    )
    assert ForgeGithubMirror.run() == 2
    assert "airgapped" in ForgeGithubMirror.status()["stopped"]


def test_the_mirror_stops_when_ling_cannot_say_the_level(monkeypatch, tmp_path):
    # The one reader fails closed (AIRGAPPED §6.5): no answer is `on`, and nothing is sent.
    from dreamference.config import AirgappedResolver

    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", raising=False)
    monkeypatch.setattr(
        AirgappedResolver, "binary", classmethod(lambda cls: str(tmp_path / "no-ling"))
    )
    assert ForgeGithubMirror.airgapped()


@pytest.mark.parametrize("where", ["user default", "environment"])
def test_the_airgapped_default_set_by_the_command_stops_the_mirror(monkeypatch, where):
    """`/airgapped default on` writes the user-level file the resolver reads, not Codex's."""
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", raising=False)
    monkeypatch.delenv("DREAMFERENCE_CONFIG_PATH", raising=False)
    if where == "environment":
        monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "on")
    else:
        user = Path("~/.config/dreamference/config.toml").expanduser()
        user.parent.mkdir(parents=True, exist_ok=True)
        user.write_text('mightling_airgapped = "on"\n')
    assert ForgeGithubMirror.airgapped()


# -- the phone account (§2.7) ------------------------------------------------------------------------


def test_the_phone_account_gets_a_token_for_ling_web_write_access_and_a_protected_main(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    ForgeSettings.write_secret("admin-token", "a" * 40)
    ForgeSettings.config_path().parent.mkdir(parents=True, exist_ok=True)
    ForgeSettings.config_path().write_text(
        '[chat_repos]\nallow_main = ["dreamference/ling-engine"]\n'
    )
    created = []
    monkeypatch.setattr(
        ForgeAccounts,
        "create_user",
        classmethod(lambda cls, name, admin: created.append((name, admin)) or "pw"),
    )
    monkeypatch.setattr(
        ForgeAccounts, "generate_token", classmethod(lambda cls, user, name, scopes: "p" * 40)
    )
    calls = []
    rules = {
        "dreamference/mightling": [],
        "dreamference/ling-engine": [{"rule_name": "main", "push_whitelist_usernames": ["owner"]}],
    }

    def api(cls, method, path, token, body=None, timeout=30):
        calls.append((method, path, body))
        if path == "/users/mightling-phone":
            return 404, None
        if path.startswith("/repos/search"):
            return 200, {
                "data": [
                    {
                        "full_name": "dreamference/mightling",
                        "default_branch": "main",
                        "private": False,
                    },
                    {
                        "full_name": "dreamference/ling-engine",
                        "default_branch": "main",
                        "private": False,
                    },
                    {
                        "full_name": "dreamference/archive-pre-rewrite",
                        "archived": True,
                        "private": True,
                    },
                ]
            }
        if method == "GET" and path.endswith("/branch_protections"):
            return 200, rules[path[len("/repos/") : -len("/branch_protections")]]
        return (204, None) if method == "PUT" else (201, {})

    monkeypatch.setattr(ForgeContainer, "api", classmethod(api))
    assert ForgePhone.ensure() == []
    assert created == [("mightling-phone", False)]
    token = ForgePhone.token_path()
    assert token == tmp_path / "codex-home" / "web" / "forge-phone-token"
    assert token.read_text() == "p" * 40 + "\n"
    assert (
        oct(token.stat().st_mode & 0o777) == "0o600"
        and oct(token.parent.stat().st_mode & 0o777) == "0o700"
    )
    access = {path: body["permission"] for method, path, body in calls if method == "PUT"}
    assert access == {
        "/repos/dreamference/mightling/collaborators/mightling-phone": "write",
        "/repos/dreamference/ling-engine/collaborators/mightling-phone": "write",
        "/repos/dreamference/archive-pre-rewrite/collaborators/mightling-phone": "read",
    }
    created_rule = next(body for method, path, body in calls if method == "POST")
    assert created_rule["rule_name"] == "main" and created_rule["push_whitelist_usernames"] == [
        "owner",
        "forge-admin",
    ]
    changed = next((path, body) for method, path, body in calls if method == "PATCH")
    assert changed[0] == "/repos/dreamference/ling-engine/branch_protections/main"
    assert changed[1]["push_whitelist_usernames"] == ["owner", "forge-admin", "mightling-phone"]
    assert not any("archive-pre-rewrite/branch_protections" in path for _m, path, _b in calls)
    # A second run with everything in place changes nothing and keeps the token.
    calls.clear()
    rules["dreamference/mightling"] = [
        created_rule | {"enable_push": True, "enable_push_whitelist": True}
    ]
    rules["dreamference/ling-engine"] = [changed[1] | {"rule_name": "main"}]
    assert ForgePhone.ensure() == []
    assert not [call for call in calls if call[0] in ("POST", "PATCH")]
    assert token.read_text() == "p" * 40 + "\n"


def test_the_ten_minute_tick_keeps_the_phone_account_in_line_and_reports_a_failure(
    monkeypatch, capsys
):
    """CHAT_REPOS §3.3: the forge of record's timer re-checks the phone account; a failure is said."""
    monkeypatch.setattr(ForgeService, "rebind", classmethod(lambda cls: False))
    monkeypatch.setattr(ForgeService, "housekeeping", classmethod(lambda cls: None))
    monkeypatch.setattr(ForgeSettings, "role", classmethod(lambda cls: "record"))
    monkeypatch.setattr(ForgeContainer, "running", classmethod(lambda cls: True))
    monkeypatch.setattr(ForgeRepositories, "remove_push_mirrors", classmethod(lambda cls: []))
    monkeypatch.setattr(ForgeRepositories, "install_hooks", classmethod(lambda cls, **k: None))
    monkeypatch.setattr(ForgeGithubMirror, "run", classmethod(lambda cls: 0))
    ForgeSettings.update_state(mirrors_synced_at=int(time.time()))
    runs = []

    def ensure(cls):
        runs.append(1)
        return ["⚠️  dreamference/mightling: `main` not protected (HTTP 500)."]

    monkeypatch.setattr(ForgePhone, "ensure", classmethod(ensure))
    assert ForgeService.tick() == 0
    assert runs == [1]
    assert "`main` not protected" in capsys.readouterr().out

    def unreachable(cls):
        raise RuntimeError("the forge did not list its repositories")

    monkeypatch.setattr(ForgePhone, "ensure", classmethod(unreachable))
    assert ForgeService.tick() == 0
    assert "phone account was not checked: the forge did not list" in capsys.readouterr().out

    # A mirror node has no phone account: its tick never touches it.
    monkeypatch.setattr(ForgeSettings, "role", classmethod(lambda cls: "mirror"))
    monkeypatch.setattr(ForgeService, "record_address", classmethod(lambda cls: None))
    monkeypatch.setattr(
        ForgePhone, "ensure", classmethod(lambda cls: pytest.fail("phone on mirror"))
    )
    assert ForgeService.tick() == 0


PHONE_REPO = "dreamference/mightling"
PROTECTIONS = f"/repos/{PHONE_REPO}/branch_protections"
COLLABORATOR = f"/repos/{PHONE_REPO}/collaborators/mightling-phone"


def phone_forge(monkeypatch, answers):
    """Answer the forge API from `answers` ((method, path) → (status, body)); record the calls."""
    calls = []

    def api(cls, method, path, token, body=None, timeout=30):
        calls.append((method, path, body))
        return answers.get((method, path), (204, None) if method == "PUT" else (201, {}))

    monkeypatch.setattr(ForgeContainer, "api", classmethod(api))
    return calls


@pytest.mark.parametrize(
    "config",
    [
        '[chat_repos]\nallow_main = "dreamference/mightling"\n',
        'chat_repos = "dreamference/mightling"\n',
    ],
)
def test_an_allow_main_that_is_not_a_list_in_a_table_allows_nothing(config):
    ForgeSettings.config_path().parent.mkdir(parents=True, exist_ok=True)
    ForgeSettings.config_path().write_text(config)
    assert ForgePhone.allow_main() == set()


@pytest.mark.parametrize(
    "status, password, token", [(500, "pw", "p" * 40), (404, None, "p" * 40), (404, "pw", None)]
)
def test_the_phone_account_is_not_ready_when_the_forge_does_not_make_it(
    monkeypatch, tmp_path, status, password, token
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    made = []
    monkeypatch.setattr(
        ForgeAccounts,
        "create_user",
        classmethod(lambda cls, name, admin: made.append("account") or password),
    )
    monkeypatch.setattr(
        ForgeAccounts,
        "generate_token",
        classmethod(lambda cls, user, name, scopes: made.append("token") or token),
    )
    phone_forge(monkeypatch, {("GET", "/users/mightling-phone"): (status, None)})
    assert ForgePhone.ensure_account("a" * 40) is False
    assert not ForgePhone.token_path().exists()
    if status != 404:
        assert made == [], "a forge that did not answer is not given a second account"


def test_the_phone_account_is_not_checked_without_the_admin_token(monkeypatch):
    calls = phone_forge(monkeypatch, {})
    assert ForgePhone.ensure() == [
        "⚠️  The phone account was not checked: no `forge-admin` token yet."
    ]
    assert calls == []


def test_a_repository_the_phone_cannot_be_given_or_protect_is_reported(monkeypatch):
    phone_forge(
        monkeypatch,
        {
            ("PUT", COLLABORATOR): (403, None),
            ("GET", PROTECTIONS): (200, []),
            ("POST", PROTECTIONS): (422, {}),
        },
    )
    repository = {"full_name": PHONE_REPO, "default_branch": "main"}
    problems = ForgePhone.ensure_repository(repository, "a" * 40, set())
    assert len(problems) == 2
    assert "not given write access (HTTP 403)" in problems[0]
    assert "`main` not protected (HTTP 422)" in problems[1]
    calls = phone_forge(monkeypatch, {("GET", PROTECTIONS): (500, None)})
    assert ForgePhone.ensure_repository(repository, "a" * 40, set()) == [
        f"⚠️  {PHONE_REPO}: branch protection not readable (HTTP 500)."
    ]
    assert not [call for call in calls if call[0] in ("POST", "PATCH")]


def test_a_mirror_or_a_nameless_repository_gets_no_protection(monkeypatch):
    calls = phone_forge(monkeypatch, {})
    mirror = {"full_name": PHONE_REPO, "default_branch": "main", "mirror": True}
    assert ForgePhone.ensure_repository(mirror, "a" * 40, set()) == []
    assert [call[0] for call in calls] == ["PUT"]
    calls.clear()
    assert ForgePhone.ensure_repository({"full_name": ""}, "a" * 40, set()) == []
    assert calls == []


def test_the_default_branch_is_the_one_protected(monkeypatch):
    calls = phone_forge(monkeypatch, {("GET", PROTECTIONS): (200, [])})
    repository = {"full_name": PHONE_REPO, "default_branch": "trunk"}
    assert ForgePhone.ensure_repository(repository, "a" * 40, set()) == []
    assert [body["rule_name"] for method, _path, body in calls if method == "POST"] == ["trunk"]


PUSHERS = ["owner", "forge-admin"]


@pytest.mark.parametrize(
    "rule, change",
    [
        ({"rule_name": "dev", "enable_push": True, "enable_push_whitelist": True}, "POST"),
        (
            {
                "enable_push": True,
                "enable_push_whitelist": True,
                "push_whitelist_usernames": ["owner"],
            },
            "PATCH",
        ),
        ({"enable_push": True, "enable_push_whitelist": False}, "PATCH"),
        ({"enable_push": True, "enable_push_whitelist": True}, None),
    ],
)
def test_a_main_rule_that_lets_others_push_is_corrected_and_one_in_place_is_kept(
    monkeypatch, rule, change
):
    rule = {"rule_name": "main", "push_whitelist_usernames": PUSHERS[::-1]} | rule
    calls = phone_forge(monkeypatch, {("GET", PROTECTIONS): (200, [rule])})
    repository = {"full_name": PHONE_REPO, "default_branch": "main"}
    assert ForgePhone.ensure_repository(repository, "a" * 40, set()) == []
    changes = [(method, path) for method, path, _body in calls if method in ("POST", "PATCH")]
    assert changes == (
        [] if change is None else [(change, PROTECTIONS + ("/main" if change == "PATCH" else ""))]
    )


# -- mirror nodes ------------------------------------------------------------------------------------


def test_a_mirror_node_pulls_each_shared_repository_with_the_read_token(monkeypatch):
    ForgeSettings.update_state(role="mirror", record_node="rec-1", record_name="gx10-1a2b")
    ForgeSettings.write_secret("admin-token", "a" * 40)
    monkeypatch.setattr(ForgeBackup, "node_id", classmethod(lambda cls: "me-1"))
    calls = []

    def api(cls, method, path, token, body=None, timeout=30):
        calls.append((method, path, body))
        if method == "GET" and path == "/repos/dreamference/mightling":
            return 200, {}
        if method == "GET":
            return 404, None
        return 201, {}

    monkeypatch.setattr(ForgeContainer, "api", classmethod(api))
    lines = ForgeMirrors.apply(
        {
            "record_node": "rec-1",
            "record_name": "gx10-1a2b",
            "token": "t" * 40,
            "repositories": ["dreamference/mightling", "dreamference/ling-engine"],
        }
    )
    assert lines == ["dreamference/mightling: mirrored", "dreamference/ling-engine: mirror created"]
    migrate = next(body for _method, path, body in calls if path == "/repos/migrate")
    assert (
        migrate["mirror"]
        and migrate["clone_addr"] == "http://forge-record:3200/dreamference/ling-engine.git"
    )
    assert migrate["auth_username"] == "mirror" and migrate["mirror_interval"] == "10m"
    ForgeSettings.update_state(role="record")
    assert ForgeMirrors.apply({"record_node": "x", "repositories": []})[0].startswith("refused")
    assert ForgeMirrors.serve_apply("not json") == 2


def test_a_sync_whose_own_mirrors_failed_says_so(monkeypatch, capsys):
    from dreamference.node.node_pairing import NodePairing

    ForgeSettings.update_state(role="mirror")
    plan = {"record_node": "rec-1", "repositories": ["dreamference/mightling"]}
    monkeypatch.setattr(ForgeMirrors, "fetch_plan", classmethod(lambda cls: plan))
    monkeypatch.setattr(NodePairing, "paired", classmethod(lambda cls: []))
    monkeypatch.setattr(
        ForgeMirrors,
        "apply",
        classmethod(lambda cls, p: ["dreamference/mightling: not mirrored (HTTP 500: boom)"]),
    )
    assert ForgeMirrors.sync(quiet=True) == 1
    assert "not mirrored (HTTP 500: boom)" in capsys.readouterr().out


def test_the_plan_comes_only_from_a_running_record_with_a_token_and_names_shared_repositories(
    monkeypatch,
):
    token = ["t" * 40]
    monkeypatch.setattr(ForgeContainer, "running", classmethod(lambda cls: True))
    monkeypatch.setattr(ForgeAccounts, "mirror_token", classmethod(lambda cls: token[0]))
    monkeypatch.setattr(ForgeBackup, "node_id", classmethod(lambda cls: "rec-1"))
    monkeypatch.setattr(ForgeBackup, "host_name", classmethod(lambda cls: "gx10-1a2b"))
    monkeypatch.setattr(
        ForgeRepositories,
        "all",
        classmethod(
            lambda cls, token=None: [
                {"full_name": "dreamference/mightling", "name": "mightling", "private": False},
                {"full_name": "dreamference/secret", "name": "secret", "private": True},
                {"full_name": "dreamference/memos", "name": "memos", "private": False},
                {"full_name": "dreamference/unknown", "name": "unknown"},
            ]
        ),
    )
    ForgeSettings.update_state(role="mirror")
    assert ForgeMirrors.plan() is None
    ForgeSettings.update_state(role="record")
    assert ForgeMirrors.plan() == {
        "record_node": "rec-1",
        "record_name": "gx10-1a2b",
        "token": "t" * 40,
        "repositories": ["dreamference/mightling"],
    }
    token[0] = None
    assert ForgeMirrors.plan() is None


def test_the_record_given_its_own_plan_changes_nothing(monkeypatch):
    monkeypatch.setattr(ForgeBackup, "node_id", classmethod(lambda cls: "rec-1"))
    monkeypatch.setattr(ForgeService, "restart", classmethod(lambda cls: pytest.fail("restarted")))
    monkeypatch.setattr(
        ForgeContainer, "api", classmethod(lambda cls, *a, **k: pytest.fail("asked the forge"))
    )
    plan = {"record_node": "rec-1", "token": "t" * 40, "repositories": ["o/r"]}
    assert ForgeMirrors.apply(plan) == ["this node is the forge of record"]
    assert ForgeSettings.state() == {} and ForgeSettings.read_secret("record-mirror-token") is None


def test_a_new_record_restarts_the_forge_once_and_each_mirror_reads_with_its_token(monkeypatch):
    ForgeSettings.update_state(role="mirror", record_node="rec-old")
    monkeypatch.setattr(ForgeBackup, "node_id", classmethod(lambda cls: "me-1"))
    monkeypatch.setattr(ForgeAccounts, "admin_token", classmethod(lambda cls: "a" * 40))
    restarts, organisations, migrations = [], [], []
    monkeypatch.setattr(ForgeService, "restart", classmethod(lambda cls: restarts.append(1)))
    monkeypatch.setattr(
        ForgeContainer, "wait_until_answering", classmethod(lambda cls, seconds=120.0: True)
    )
    monkeypatch.setattr(
        ForgeRepositories,
        "ensure_organisation",
        classmethod(lambda cls, owner: organisations.append(owner)),
    )

    def api(cls, method, path, token, body=None, timeout=30):
        if method == "GET":
            return 404, None
        migrations.append(body)
        return (500, {"message": "boom"}) if body["repo_name"] == "bad" else (201, {})

    monkeypatch.setattr(ForgeContainer, "api", classmethod(api))
    plan = {
        "record_node": "rec-2",
        "record_name": "gx10-1a2b",
        "token": "t" * 40,
        "repositories": ["owner/a", "team/b", "team/bad"],
    }
    assert ForgeMirrors.apply(plan) == [
        "owner/a: mirror created",
        "team/b: mirror created",
        "team/bad: not mirrored (HTTP 500: boom)",
    ]
    assert restarts == [1] and organisations == ["team", "team"]
    assert ForgeSettings.state()["record_node"] == "rec-2"
    assert ForgeSettings.read_secret("record-mirror-token") == "t" * 40
    assert all(body["private"] is False and body["auth_token"] == "t" * 40 for body in migrations)
    ForgeMirrors.apply(plan)
    assert restarts == [1], "the same record again restarts nothing"


def test_a_plan_took_only_when_every_repository_is_mirrored():
    assert ForgeMirrors.took(["o/a: mirrored", "o/b: mirror created"])
    assert not ForgeMirrors.took(["o/a: mirrored", "o/b: not mirrored (HTTP 500: boom)"])


@pytest.mark.parametrize(
    "text, forge, lines, code",
    [
        ('{"record_node": "rec-1"}', "on", None, 2),
        ("[]", "on", None, 2),
        ('{"repositories": []}', "off", None, 1),
        ('{"repositories": ["o/a"]}', "on", ["o/a: not mirrored (HTTP 500: boom)"], 1),
        ('{"repositories": ["o/a"]}', "on", ["o/a: mirrored"], 0),
    ],
)
def test_a_mirror_node_applies_only_a_readable_plan_with_its_forge_on(
    monkeypatch, text, forge, lines, code
):
    monkeypatch.setenv("DREAMFERENCE_FORGE", forge)
    monkeypatch.setattr(
        ForgeMirrors, "apply", classmethod(lambda cls, plan: lines or pytest.fail("applied"))
    )
    assert ForgeMirrors.serve_apply(text) == code


@pytest.mark.parametrize(
    "code, stdout, plan",
    [
        (1, '{"record_node": "rec-1"}', None),
        (0, '["rec-1"]', None),
        (0, '{"record_node": "rec-1"}', {"record_node": "rec-1"}),
    ],
)
def test_a_mirror_takes_the_plan_only_from_a_record_that_answered_with_one(
    monkeypatch, code, stdout, plan
):
    from dreamference.node.node_pairing import NodePairing

    ForgeSettings.update_state(role="mirror", record_node="rec-1")
    monkeypatch.setattr(
        NodePairing, "find", classmethod(lambda cls, name, browse=True: {"node": name})
    )
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, record, request, **k: subprocess.CompletedProcess([], code, stdout, "")
        ),
    )
    assert ForgeMirrors.fetch_plan() == plan


def test_the_record_hands_its_plan_to_every_other_node_and_a_silent_one_fails(monkeypatch, capsys):
    from dreamference.node.node_pairing import NodePairing

    ForgeSettings.update_state(role="record")
    plan = {"record_node": "rec-1", "repositories": ["o/r"]}
    monkeypatch.setattr(ForgeMirrors, "fetch_plan", classmethod(lambda cls: plan))
    monkeypatch.setattr(
        ForgeMirrors, "apply", classmethod(lambda cls, p: pytest.fail("applied on the record"))
    )
    monkeypatch.setattr(
        NodePairing,
        "paired",
        classmethod(lambda cls: [{"node": "rec-1"}, {"node": "m-1", "name": "a"}, {"node": "m-2"}]),
    )
    asked = []

    def run(cls, record, request, input_text=None, **k):
        asked.append((record["node"], request, json.loads(input_text)))
        code = 0 if record["node"] == "m-1" else 1
        return subprocess.CompletedProcess([], code, "o/r: mirrored\n" if code == 0 else "", "")

    monkeypatch.setattr(NodePairing, "run", classmethod(run))
    assert ForgeMirrors.sync() == 1
    assert asked == [("m-1", "forge-mirror-apply", plan), ("m-2", "forge-mirror-apply", plan)]
    out = capsys.readouterr().out
    assert "a: o/r: mirrored" in out and "m-2: no answer" in out


def test_the_forced_command_takes_only_the_forges_operations(monkeypatch):
    from dreamference.forge.forge_serve import ForgeServe

    seen = []
    monkeypatch.setattr(
        ForgeMirrors, "serve_plan", classmethod(lambda cls: seen.append("plan") or 0)
    )
    assert ForgeServe.serve("forge-mirror-plan", []) == 0 and seen == ["plan"]
    assert ForgeServe.serve("forge-mirror-plan", ["x"]) == 2
    assert ForgeServe.serve("forge-shell", []) == 2
    assert ForgeServe.serve("forge-backup-receive", ["a", "b"]) == 2


# -- the egress audit ------------------------------------------------------------------------------


def test_the_audit_names_the_forge_and_where_its_mirror_sends(monkeypatch):
    monkeypatch.setattr(
        EgressAudit,
        "_unit_enabled",
        classmethod(lambda cls, unit, user: unit == "mightling-forge.service"),
    )
    ForgeSettings.update_state(role="record")
    assert "no outbound mirror" in EgressAudit.forge_lines()[0]
    ForgeRepositories.set_settings(
        "dreamference/mightling", github="https://github.com/dreamference/mightling.git"
    )
    ForgeGithubMirror.save_settings(True, "dgxcoder")
    line = EgressAudit.forge_lines()[0]
    assert "https://github.com/dreamference/mightling.git" in line and "LAN only" in line
    monkeypatch.setattr(EgressAudit, "_unit_enabled", classmethod(lambda cls, unit, user: False))
    assert EgressAudit.forge_lines() == []


# -- the command line ------------------------------------------------------------------------------


def test_node_enable_sets_the_forge_up_unless_told_not_to(monkeypatch):
    from dreamference.cli import main
    from dreamference.node import NodeAdvertiser

    monkeypatch.setattr(
        NodeAdvertiser, "enable", classmethod(lambda cls, no_web=False, yes=False: True)
    )
    setups = []
    monkeypatch.setattr(
        ForgeService, "setup", classmethod(lambda cls, show_password=True: setups.append(1) or True)
    )
    for argv in (["node", "enable"], ["node", "enable", "--no-forge"]):
        monkeypatch.setattr(sys, "argv", ["ling-admin", *argv])
        with pytest.raises(SystemExit) as exit_:
            main()
        assert exit_.value.code == 0
    assert setups == [1]


def test_the_password_is_never_printed_off_the_nodes_terminal(monkeypatch, capsys):
    from dreamference.forge.forge_command import ForgeCommand

    ForgeSettings.write_secret("owner-password", "q" * 24)
    import argparse

    assert ForgeCommand.dispatch(argparse.Namespace(forge_command="password", show=True)) == 1
    assert "q" * 24 not in capsys.readouterr().out


def test_the_role_moves_only_through_its_own_operations(monkeypatch):
    from dreamference.forge.forge_move import ForgeMove
    from dreamference.forge.forge_serve import ForgeServe

    assert ForgeServe.serve("forge-become-mirror", ["n-1"]) == 2
    assert ForgeServe.serve("forge-become-mirror", ["n-1", "a;b"]) == 2
    ForgeSettings.update_state(role="record")
    assert ForgeServe.serve("forge-become-mirror", ["n-1", "gx10-1a2b"]) == 2, (
        "only a frozen record"
    )
    assert ForgeServe.serve("forge-hand-over", ["x"]) == 2
    ForgeMove.freeze(True)
    assert ForgeServe.serve("forge-unfreeze", []) == 0 and not ForgeMove.frozen_flag().exists()
    ForgeSettings.update_state(role="mirror")
    assert ForgeMove.serve_hand_over() == 1, "a mirror hands nothing over"


def test_a_moved_forge_keeps_the_denylist(tmp_path, repositories):
    folder = tmp_path / "forge-20261027-063000"
    make_set(folder, repositories)
    (folder / "secrets.json").write_text(json.dumps({"anonymity-denylist": "node-q\n"}))
    ForgeBackup.restore_secrets(folder)
    assert ForgeAnonymity.read_denylist() == "node-q\n"


# -- what the spec promises and nothing else checked ------------------------------------------------


def test_the_record_runs_actions_and_mails_nothing_and_a_mirror_pulls_every_ten_minutes(tmp_path):
    record = ForgeAppIni.write(tmp_path / "record", "gx10-1a2b", "record").read_text()
    assert "[actions]\nENABLED = true" in record
    assert "ARTIFACT_RETENTION_DAYS = 7" in record and "LOG_RETENTION_DAYS = 7" in record
    mirror = ForgeAppIni.write(tmp_path / "mirror", "node-b", "mirror").read_text()
    assert "ARTIFACT_RETENTION_DAYS = 14" in mirror and "LOG_RETENTION_DAYS = 14" in mirror
    assert "DEFAULT_INTERVAL = 10m" in mirror and "MIN_INTERVAL = 10m" in mirror
    for text in (record, mirror):
        assert "[mailer]\nENABLED = false" in text


def test_a_monthly_timer_checks_that_the_newest_set_restores(monkeypatch):
    monkeypatch.setattr(
        ForgeService, "admin_executable", classmethod(lambda cls: "/opt/ling-admin")
    )
    units = ForgeService.units()
    assert "OnCalendar=monthly" in units["mightling-forge-check.timer"]
    assert (
        "ExecStart=/opt/ling-admin forge restore --check" in units["mightling-forge-check.service"]
    )


def test_the_container_yields_the_cpu_to_the_model_server():
    argv = ForgeContainer.run_arguments(Path("/data"), [])
    assert int(argv[argv.index("--cpu-shares") + 1]) < 1024, "below docker's default share"


def test_off_keeps_the_data_and_removes_the_advertisement(monkeypatch, capsys):
    monkeypatch.delenv("DREAMFERENCE_FORGE", raising=False)
    calls = []
    monkeypatch.setattr(
        ForgeService,
        "systemctl",
        classmethod(lambda cls, *a: calls.append(a) or subprocess.CompletedProcess(a, 0, "", "")),
    )
    advertised = []
    monkeypatch.setattr(
        ForgeService, "advertise", classmethod(lambda cls, role: advertised.append(role))
    )
    repository = ForgeSettings.data_dir() / "git" / "repositories" / "owner" / "x.git"
    repository.mkdir(parents=True)
    assert ForgeService.off() == 0
    assert not ForgeSettings.enabled() and advertised == [None]
    assert calls[0][:3] == ("disable", "--now", "mightling-forge.service")
    assert "mightling-forge-backup.timer" in calls[0]
    assert repository.is_dir() and "✅" in capsys.readouterr().out
    monkeypatch.setattr(ForgeService, "setup", classmethod(lambda cls, **k: True))
    assert ForgeService.on() == 0 and ForgeSettings.enabled()


def test_key_add_gives_the_owner_the_public_key(tmp_path, monkeypatch):
    from dreamference.forge.forge_command import ForgeCommand

    posted = []
    monkeypatch.setattr(ForgeAccounts, "admin_token", classmethod(lambda cls: "t"))
    monkeypatch.setattr(
        ForgeContainer,
        "api",
        classmethod(
            lambda cls, method, path, token, body=None, timeout=30: (
                posted.append((method, path, body)) or (201, {})
            )
        ),
    )
    key = tmp_path / "laptop.pub"
    key.write_text("ssh-ed25519 AAAAC3Nza laptop\n")
    import argparse

    args = argparse.Namespace(forge_command="key", key_command="add", file=str(key))
    assert ForgeCommand.dispatch(args) == 0
    assert posted == [
        (
            "POST",
            "/admin/users/owner/keys",
            {"key": "ssh-ed25519 AAAAC3Nza laptop", "title": "laptop.pub", "read_only": False},
        )
    ]
    monkeypatch.setattr(
        ForgeContainer, "api", classmethod(lambda cls, *a, **k: (422, {"message": "key exists"}))
    )
    assert ForgeCommand.dispatch(args) == 1


def test_the_storage_alarm_and_budget_are_reported():
    gb = 1000**3
    usage = {"repositories": gb // 2, "logs_artefacts": 0, "dumps": 0, "other": 0, "total": gb}
    assert ForgeStorage.warnings(usage) == []
    usage.update(repositories=2 * gb, total=11 * gb)
    warnings = ForgeStorage.warnings(usage)
    assert len(warnings) == 2
    assert "above the 1 GB alarm" in warnings[0] and "above its 10 GB budget" in warnings[1]


def test_status_names_the_role_the_last_backup_the_mirror_and_the_storage(monkeypatch):
    from dreamference.forge.forge_command import ForgeCommand

    monkeypatch.delenv("DREAMFERENCE_FORGE", raising=False)
    monkeypatch.setattr(ForgeService, "host_name", classmethod(lambda cls: "gx10-1a2b"))
    monkeypatch.setattr(ForgeContainer, "running", classmethod(lambda cls: False))
    monkeypatch.setattr(ForgeStorage, "free_gb", classmethod(lambda cls: 400.0))
    monkeypatch.setattr(
        ForgeStorage,
        "usage",
        classmethod(
            lambda cls: {
                "repositories": 2 * 1000**3,
                "logs_artefacts": 0,
                "dumps": 0,
                "other": 0,
                "total": 2 * 1000**3,
            }
        ),
    )
    ForgeSettings.update_state(role="record")
    ForgeSettings.write_secret("owner-password", "p" * 24)
    lines = ForgeCommand.status_lines()
    assert lines[0].startswith(
        "Forge: on, record, not running at http://gx10-1a2b.local:3100/forge/"
    )
    assert "Last backup: never" in lines and "GitHub mirror: off" in lines
    assert any(line.startswith("Storage: 2.00 GB") for line in lines)
    assert any("above the 1 GB alarm" in line for line in lines)
    assert "p" * 24 not in "\n".join(lines) and "password --show" in lines[-1]
    ForgeSettings.update_state(role="mirror", record_name="gx10-77c0")
    lines = ForgeCommand.status_lines()
    assert "Forge of record: gx10-77c0" in lines and not any(
        "Last backup" in line for line in lines
    )
    # The record's advertisement is gone once it is off, and the mirror says so (§2.5).
    assert any("forge of record is off" in line for line in lines)
    monkeypatch.setattr(
        ForgeService,
        "find_record",
        classmethod(lambda cls: {"node": "n-1", "name": "gx10-77c0", "role": "record"}),
    )
    assert not any("is off" in line for line in ForgeCommand.status_lines())


def test_the_node_list_shows_each_nodes_forge_role(monkeypatch):
    from dreamference.night_shift.night_shift_host import NightShiftHost
    from dreamference.node.node_browser import NodeBrowser
    from dreamference.node.node_identity import NodeIdentity
    from dreamference.node.node_pairing import NodePairing
    from dreamference.node.node_remote import NodeRemote

    def node(name, node_id, last):
        return {
            "name": name,
            "node": node_id,
            "address": f"{LAN}.50.{last}",
            "port": "8000",
            "version": "1.3.0",
            "state": "stopped",
            "main": "1",
        }

    nodes = [node("gx10-1a2b", "n-1", 10), node("node-b", "n-2", 11), node("node-c", "n-3", 12)]
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: nodes))
    forges = [{"node": "n-1", "role": "record"}, {"node": "n-2", "role": "mirror"}]
    monkeypatch.setattr(
        NodeBrowser,
        "browse_service",
        classmethod(
            lambda cls, service_type, timeout=6: (
                forges if service_type == "_mightling-forge._tcp" else []
            )
        ),
    )
    monkeypatch.setattr(NodeIdentity, "read", classmethod(lambda cls: None))
    monkeypatch.setattr(NodePairing, "paired", classmethod(lambda cls: []))
    monkeypatch.setattr(
        NightShiftHost, "served_model", classmethod(lambda cls, host, timeout=3.0: None)
    )
    lines = NodeRemote.list_lines()
    assert lines[0].endswith("; forge record)")
    assert lines[1].endswith("; forge mirror)")
    assert "forge" not in lines[2], "a node without a forge says nothing about one"


def test_the_forge_is_published_again_when_the_advertised_addresses_change(monkeypatch, capsys):
    from dreamference.node.node_settings import NodeSettings

    monkeypatch.setattr(NodeSettings, "advertised", classmethod(lambda cls: True))
    monkeypatch.setattr(ForgeService, "housekeeping", classmethod(lambda cls: None))
    monkeypatch.setattr(ForgeService, "host_name", classmethod(lambda cls: "gx10-1a2b"))
    monkeypatch.setattr(ForgeContainer, "running", classmethod(lambda cls: True))
    monkeypatch.setattr(ForgeRepositories, "remove_push_mirrors", classmethod(lambda cls: []))
    monkeypatch.setattr(ForgeService, "record_address", classmethod(lambda cls: None))
    restarts = []
    monkeypatch.setattr(
        ForgeService, "restart", classmethod(lambda cls: restarts.append(1) or True)
    )
    addresses = [f"{LAN}.50.10"]
    monkeypatch.setattr(
        ForgeBind, "lan_addresses", classmethod(lambda cls, *a, **k: list(addresses))
    )
    ForgeSettings.update_state(role="mirror", record_node="n-1")
    ForgeService.tick()
    assert restarts == [], "started before the addresses were kept: nothing to compare"
    argv = ForgeService.run_arguments()
    assert f"{LAN}.50.10:3200:3000" in argv
    ForgeService.tick()
    assert restarts == []
    addresses[0] = f"{LAN}.1.20"
    ForgeService.tick()
    assert restarts == [1] and "addresses changed" in capsys.readouterr().out


@pytest.mark.parametrize("flip", [False, True])
def test_the_records_address_does_not_depend_on_the_order_avahi_lists_it_in(monkeypatch, flip):
    from dreamference.node.node_browser import NodeBrowser

    ForgeSettings.update_state(role="mirror", record_node="rec-1")
    seen = [
        {"node": "rec-1", "address": "fe80::1%eth0"},
        {"node": "rec-1", "address": f"{LAN}.50.12"},
        {"node": "rec-1", "address": f"{LAN}.50.11"},
    ]
    entries = seen[::-1] if flip else seen
    monkeypatch.setattr(
        NodeBrowser, "browse_service", classmethod(lambda cls, kind, timeout=6: entries)
    )
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    assert ForgeService.record_address() == f"{LAN}.50.11", "else the tick restarts the mirror"


def test_a_forge_that_breaks_off_a_reply_is_no_answer_not_a_crash(monkeypatch):
    import http.client
    import urllib.request

    class Opener:
        def open(self, request, timeout):
            raise http.client.IncompleteRead(b"{")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    assert ForgeContainer.api("GET", "/version", None) == (0, None)


def test_a_repository_git_cannot_read_does_not_match_the_manifest(tmp_path, monkeypatch):
    if GIT is None:
        pytest.skip("no git")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    (tmp_path / "repos" / "o" / "new.git").mkdir(parents=True)
    # A repository with no commit yet has no refs; one git cannot read has none either.
    manifest = {"refs": {"o/new": {}}}
    assert ForgeBackup.compare(manifest, tmp_path / "repos"), "an unreadable repository passed"


def test_a_switch_written_halfway_leaves_the_nodes_config_as_it_was(monkeypatch):
    path = ForgeSettings.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[night]\nwindow = "22-06"\n')

    def cut_short(self, target):
        raise OSError("power cut")

    monkeypatch.setattr(Path, "replace", cut_short)
    with pytest.raises(OSError):
        ForgeSettings.set_enabled(False)
    assert path.read_text() == '[night]\nwindow = "22-06"\n', "the node's config was truncated"


def test_a_file_removed_while_the_prune_lists_them_is_skipped(monkeypatch):
    folder = ForgeSettings.data_dir() / "log"
    folder.mkdir(parents=True, exist_ok=True)
    kept, gone = folder / "kept.log", folder / "gone.log"
    kept.write_text("x")
    gone.write_text("x")
    real = Path.is_file

    def listed_then_removed(self):
        found = real(self)
        if self == gone and found:
            self.unlink()  # Forgejo rotates its log between the listing and the sort
        return found

    monkeypatch.setattr(Path, "is_file", listed_then_removed)
    assert kept in ForgeStorage.candidates()


def test_a_forge_switched_off_stays_off_when_the_rest_of_the_config_does_not_parse(monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_FORGE", raising=False)
    path = ForgeSettings.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[forge]\nenabled = false  # off\n\n[night\nwindow = 22-06\n")
    assert ForgeSettings.enabled() is False
    path.write_text("[forge]\nenabled = true\n[night\n")
    assert ForgeSettings.enabled() is True


def test_a_config_that_cannot_be_read_is_not_replaced_by_the_switch(monkeypatch):
    path = ForgeSettings.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[night]\nwindow = "22-06"\n')
    real = Path.read_text

    def unreadable(self, *args, **kwargs):
        if self == path:
            raise PermissionError("denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(OSError):
        ForgeSettings.set_enabled(False)
    monkeypatch.undo()
    assert path.read_text() == '[night]\nwindow = "22-06"\n', "the node's config was replaced"


def test_the_app_ini_is_on_disk_before_it_replaces_the_old_one(tmp_path, monkeypatch):
    import os

    events = []
    real_fsync, real_replace = os.fsync, Path.replace
    monkeypatch.setattr(os, "fsync", lambda fd: events.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(
        Path, "replace", lambda self, target: events.append("replace") or real_replace(self, target)
    )
    ForgeAppIni.write(tmp_path, "gx10-1a2b", "record")
    assert events[:2] == ["fsync", "replace"], (
        "an empty app.ini after a power cut means new secrets"
    )


@pytest.mark.parametrize(
    "account, answer, kept",
    [(404, 200, False), (200, 401, False), (200, 200, True), (200, 0, True)],
)
def test_a_phone_token_the_forge_does_not_know_is_replaced(
    account, answer, kept, tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    ForgePhone.write_token("o" * 40)

    def api(cls, method, path, token, body=None, timeout=30):
        return (account, None) if path == "/users/mightling-phone" else (answer, None)

    monkeypatch.setattr(ForgeContainer, "api", classmethod(api))
    monkeypatch.setattr(ForgeAccounts, "create_user", classmethod(lambda cls, user, admin: "pw"))
    monkeypatch.setattr(
        ForgeAccounts, "generate_token", classmethod(lambda cls, user, name, scopes: "n" * 40)
    )
    assert ForgePhone.ensure_account("t")
    # A new account (a forge built afresh) or a refused token: the old file opens nothing.
    assert ForgePhone.token_path().read_text() == ("o" if kept else "n") * 40 + "\n"


def test_a_secret_is_on_disk_before_it_replaces_the_old_one(monkeypatch):
    import os

    events = []
    real_fsync, real_replace = os.fsync, Path.replace
    monkeypatch.setattr(os, "fsync", lambda fd: events.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(
        Path, "replace", lambda self, target: events.append("replace") or real_replace(self, target)
    )
    ForgeSettings.update_state(role="record")
    assert events[:2] == ["fsync", "replace"], "an empty state.json after a power cut has no role"
