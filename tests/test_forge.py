"""The node's forge (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md): nothing here runs docker or reaches a node."""

import io
import json
import shutil
import subprocess
import sys
import tarfile
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


# -- backups ---------------------------------------------------------------------------------------


def test_retention_keeps_seven_days_and_four_weeks():
    names = [f"forge-202610{day:02d}-063000" for day in range(1, 28)] + ["forge-20261027-120000"]
    kept = ForgeBackup.kept(names)
    assert kept[:7] == ["forge-20261027-120000"] + [
        f"forge-202610{d:02d}-063000" for d in range(26, 20, -1)
    ]
    assert len(kept) == 11 and len(set(kept)) == 11


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
        "refs/github/heads/behind": ahead,
    }  # GitHub ahead → forge
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
    assert plan["diverged"] == ["refs/heads/fork"]
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


def test_airgapped_on_stops_the_mirror(monkeypatch):
    ForgeSettings.update_state(role="record")
    ForgeGithubMirror.save_settings(True, "dgxcoder")
    config = Path("~/.mightling/config.toml").expanduser()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('mightling_airgapped = "on"\n')
    monkeypatch.setattr(
        ForgeGithubMirror, "mirror_one", classmethod(lambda *a: pytest.fail("sent while airgapped"))
    )
    assert ForgeGithubMirror.run() == 2
    assert "airgapped" in ForgeGithubMirror.status()["stopped"]


def test_a_boolean_or_a_byte_order_mark_still_reads_as_airgapped():
    # The Rust resolver reads both files as `on`; the mirror read them as `off`.
    config = Path("~/.mightling/config.toml").expanduser()
    config.parent.mkdir(parents=True, exist_ok=True)
    for text in ("mightling_airgapped = true\n", '\ufeffmightling_airgapped = "on"\n'):
        config.write_text(text, encoding="utf-8")
        assert ForgeGithubMirror.airgapped(), text
    config.write_text("mightling_airgapped = false\n")
    assert not ForgeGithubMirror.airgapped()


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
