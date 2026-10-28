"""`node provision --model-source hf`: the node downloads public weights, this machine checks them.

No network, ssh or rsync: the session is a stand-in holding the "remote" files as a dictionary.
"""

import hashlib
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

from dreamference.node import node_provisioner
from dreamference.node.fleet_hub_fetch import HF_ATTEMPTS, FleetHubFetch
from dreamference.node.fleet_model_plan import FleetModelPlan
from dreamference.node.node_provisioner import NodeProvisioner

REVISION = "a" * 40
FOLDER = "models--org--m"
BIG = b"weights" * 100
BIG_SHA = hashlib.sha256(BIG).hexdigest()


@pytest.fixture
def hub(tmp_path):
    root = tmp_path / "hub" / FOLDER
    (root / "blobs").mkdir(parents=True)
    (root / "snapshots" / REVISION).mkdir(parents=True)
    (root / "refs").mkdir()
    (root / "refs" / "main").write_text(REVISION)
    (root / "blobs" / BIG_SHA).write_bytes(BIG)
    (root / "snapshots" / REVISION / "model.safetensors").symlink_to(f"../../blobs/{BIG_SHA}")
    (root / "snapshots" / REVISION / "config.json").write_text('{"a": 1}')
    return tmp_path / "hub"


def completed(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class RemoteSession:
    """Holds what the node has: `files` maps a snapshot path to its sha256."""

    host = "house-1"

    def __init__(self, files=None, downloads=(0,), gives=None):
        self.files = dict(files or {})
        self.downloads = list(downloads)
        self.gives = gives or {}  # what a successful download puts there
        self.commands = []
        self.rsyncs = []
        self.sha_files = None

    def run(self, script, input_text=None, timeout=None, **kwargs):
        self.commands.append(script)
        if "snapshot_download" in script:
            code = self.downloads.pop(0) if len(self.downloads) > 1 else self.downloads[0]
            if code == 0:
                self.files.update(self.gives)
            return completed(code, "", "boom")
        if "sha256sum" in script:
            wanted = input_text.split()
            return completed(
                0, "".join(f"{self.files[n]}  {n}\n" for n in wanted if n in self.files)
            )
        return completed(0)

    def rsync(self, source, destination, attempts=1, wait_s=20.0, files=None):
        self.rsyncs.append((destination, files))
        for name in files:
            if name.startswith("snapshots/"):
                relative = name.split("/", 2)[2]
                real = (Path(source) / name).resolve()
                self.files[relative] = (
                    real.name
                    if len(real.name) == 64
                    else hashlib.sha256(real.read_bytes()).hexdigest()
                )
        return completed(0)


def fetcher(hub, session):
    sleeps = []
    return FleetHubFetch(session, "/remote/hub", hub, sleep=sleeps.append), sleeps


CONFIG_SHA = hashlib.sha256(b'{"a": 1}').hexdigest()


def test_the_manifest_names_large_files_by_their_blob_and_hashes_the_small_ones(hub):
    fetch, _ = fetcher(hub, RemoteSession())
    revision, files = fetch.manifest(FOLDER)
    assert revision == REVISION
    assert files == {"model.safetensors": BIG_SHA, "config.json": CONFIG_SHA}
    assert fetch.manifest("models--org--none") is None


def test_files_the_hub_gave_unchanged_cost_nothing_from_here(hub):
    session = RemoteSession(gives={"model.safetensors": BIG_SHA, "config.json": CONFIG_SHA})
    outcome = fetcher(hub, session)[0].ensure(FOLDER)
    assert outcome["ok"] and outcome["sent"] == 0 and session.rsyncs == []
    download = next(c for c in session.commands if "snapshot_download" in c)
    assert "org/m" in download and REVISION in download and "/remote/hub" in download


def test_a_file_that_differs_from_the_hub_is_sent_with_its_blob_and_checked_again(hub):
    session = RemoteSession(gives={"model.safetensors": BIG_SHA, "config.json": "0" * 64})
    outcome = fetcher(hub, session)[0].ensure(FOLDER)
    assert outcome == {"ok": True, "hub_failed": False, "sent": 1, "why": ""}
    destination, files = session.rsyncs[0]
    assert destination == f"/remote/hub/{FOLDER}"
    assert files == [f"snapshots/{REVISION}/config.json"]


def test_a_repository_the_hub_does_not_have_is_sent_whole(hub):
    session = RemoteSession(downloads=(3,))
    outcome = fetcher(hub, session)[0].ensure(FOLDER)
    assert outcome["ok"] and outcome["sent"] == 2
    assert f"blobs/{BIG_SHA}" in session.rsyncs[0][1]
    assert f"snapshots/{REVISION}/model.safetensors" in session.rsyncs[0][1]


def test_a_dropping_line_is_retried_and_then_given_up(hub):
    session = RemoteSession(
        downloads=(1, 255, 0), gives={"model.safetensors": BIG_SHA, "config.json": CONFIG_SHA}
    )
    fetch, sleeps = fetcher(hub, session)
    assert fetch.ensure(FOLDER)["ok"] and len(sleeps) == 2
    dead = RemoteSession(downloads=(1,))
    fetch, sleeps = fetcher(hub, dead)
    outcome = fetch.ensure(FOLDER)
    assert not outcome["ok"] and outcome["hub_failed"] and dead.rsyncs == []
    assert len(sleeps) == HF_ATTEMPTS - 1


def test_a_file_that_still_differs_after_the_copy_fails_the_folder(hub):
    session = RemoteSession(gives={"model.safetensors": BIG_SHA, "config.json": "0" * 64})
    session.rsync = lambda *a, **k: completed(0)  # sends nothing that sticks
    outcome = fetcher(hub, session)[0].ensure(FOLDER)
    assert not outcome["ok"] and "config.json" in outcome["why"]


def test_the_lan_copy_authorises_an_ephemeral_key_pins_the_host_key_and_always_cleans_up(hub):
    recipient, donor = RemoteSession(), RemoteSession()
    donor.host = "house-2"

    def recipient_run(script, input_text=None, timeout=None, **kwargs):
        recipient.commands.append(script)
        if "ssh-keygen" in script:
            return completed(0, "/tmp/work\nssh-ed25519 AAAAkey mightling-lan-copy-x\n")
        return completed(0)

    def donor_run(script, input_text=None, timeout=None, **kwargs):
        donor.commands.append(script)
        return (
            completed(0, "ssh-ed25519 AAAAhost root@x\n")
            if "ssh_host_ed25519" in script
            else completed(0)
        )

    recipient.run, donor.run = recipient_run, donor_run
    fetch, _ = fetcher(hub, recipient)
    assert fetch.lan_copy(FOLDER, donor, "spark-1a2b.local", "owner", "/donor/hub")
    authorise = next(c for c in donor.commands if "authorized_keys" in c and "printf" in c)
    # restricted and expiring on its own, so a removal that never arrives leaves no lasting key
    assert "restrict,expiry-time=" in authorise and "ssh-ed25519 AAAAkey" in authorise
    assert "date -d '+7 hours' +%Y%m%d%H%M" in authorise
    copy = next(c for c in recipient.commands if "rsync" in c)
    assert "spark-1a2b.local ssh-ed25519 AAAAhost" in copy and "StrictHostKeyChecking=yes" in copy
    assert "owner@spark-1a2b.local:/donor/hub/models--org--m/" in copy
    assert any("sed -i" in c and "mightling-lan-copy-" in c for c in donor.commands)
    assert any(c.startswith("rm -rf") for c in recipient.commands)
    # a failed copy still removes the key
    recipient_run_ok = recipient_run
    recipient.run = lambda script, **k: (
        completed(1) if "rsync" in script else recipient_run_ok(script, **k)
    )
    donor.commands.clear()
    assert not fetch.lan_copy(FOLDER, donor, "spark-1a2b.local", "owner", "/donor/hub")
    assert any("sed -i" in c for c in donor.commands)


def test_a_lan_copy_key_whose_removal_fails_is_named_in_a_warning(hub, caplog):
    recipient, donor = RemoteSession(), RemoteSession()

    def recipient_run(script, input_text=None, timeout=None, **kwargs):
        if "ssh-keygen" in script:
            return completed(0, "/tmp/work\nssh-ed25519 AAAAkey mightling-lan-copy-x\n")
        return completed(0)

    def donor_run(script, input_text=None, timeout=None, **kwargs):
        if "ssh_host_ed25519" in script:
            return completed(0, "ssh-ed25519 AAAAhost root@x\n")
        return completed(255) if "sed -i" in script else completed(0)

    recipient.run, donor.run = recipient_run, donor_run
    fetch, _ = fetcher(hub, recipient)
    with caplog.at_level("WARNING"):
        assert fetch.lan_copy(FOLDER, donor, "spark-1a2b.local", "owner", "/donor/hub")
    assert "mightling-lan-copy-" in caplog.text and "spark-1a2b.local" in caplog.text


# -- the provisioner -------------------------------------------------------------------------------


class FakeFetch:
    outcomes: ClassVar[dict] = {}
    copies: ClassVar[list] = []

    def __init__(self, session, remote_hub, hub):
        self.session = session

    def ensure(self, folder, attempts=30):
        return dict(self.outcomes[self.session.host])

    def lan_copy(self, folder, donor, donor_name, donor_user, donor_hub):
        FakeFetch.copies.append((self.session.host, donor.host, donor_name, donor_user, donor_hub))
        return True

    def verify(self, folder):
        return True


def provisioner(monkeypatch, tmp_path, outcomes):
    FakeFetch.outcomes, FakeFetch.copies = outcomes, []
    monkeypatch.setattr(node_provisioner, "FleetHubFetch", FakeFetch)
    monkeypatch.setattr(
        FleetModelPlan,
        "local_folders",
        classmethod(lambda cls, folders: [tmp_path / f for f in folders]),
    )
    monkeypatch.setattr(FleetModelPlan, "hub", classmethod(lambda cls: tmp_path))
    run = NodeProvisioner.__new__(NodeProvisioner)
    run.options = {"model_source": "hf"}
    run.user, run.users, run.sessions, run.connect = "owner", {}, {}, {}
    run.results = {}
    for name in ("house-1", "house-2"):
        session = type("S", (), {"host": name})()
        run.sessions[name] = session
        run.results[name] = {
            "host": name,
            "done": [],
            "probe": {"hub": f"/h/{name}", "hostname": f"spark-{name}"},
        }
    return run


def test_hf_source_never_rsyncs_the_weights_and_marks_the_machine_as_a_donor(monkeypatch, tmp_path):
    run = provisioner(
        monkeypatch, tmp_path, {"house-1": {"ok": True, "hub_failed": False, "sent": 2, "why": ""}}
    )
    assert run._models_from_hub("house-1", run.sessions["house-1"], [FOLDER])
    assert run.results["house-1"]["models_verified"]
    assert any("not on the Hub" in line for line in run.results["house-1"]["done"])


def test_a_failed_download_is_copied_from_the_neighbour_over_the_lan(monkeypatch, tmp_path):
    failed = {"ok": False, "hub_failed": True, "sent": 0, "why": "the download failed"}
    run = provisioner(monkeypatch, tmp_path, {"house-2": failed})
    run.results["house-1"]["models_verified"] = True
    assert run._models_from_hub("house-2", run.sessions["house-2"], [FOLDER])
    assert FakeFetch.copies == [
        ("house-2", "house-1", "spark-house-1.local", "owner", "/h/house-1")
    ]


def test_without_a_donor_a_failed_download_fails_the_step_naming_the_command(
    monkeypatch, tmp_path, capsys
):
    failed = {"ok": False, "hub_failed": True, "sent": 0, "why": "the download failed"}
    run = provisioner(monkeypatch, tmp_path, {"house-1": failed})
    run._fail = lambda host, step, why, left: (capsys.readouterr(), left)[1] and False
    assert not run._models_from_hub("house-1", run.sessions["house-1"], [FOLDER])
    assert FakeFetch.copies == []


def test_rsync_with_a_file_list_sends_only_those_and_keeps_links(tmp_path):
    from dreamference.node.fleet_session import FleetSession

    session = FleetSession("house-1.netbird.selfhosted", "owner", tmp_path)
    seen = []

    def spawn(command, capture, **kwargs):
        seen.append(
            (
                command,
                Path(
                    command[[c.startswith("--files-from=") for c in command].index(True)].split(
                        "=", 1
                    )[1]
                ).read_text(),
            )
        )
        return completed(0)

    session._spawn = spawn
    session._log = lambda *a, **k: None
    assert session.rsync(tmp_path, "/h/x", files=["a/b", "c"]).returncode == 0
    assert seen[0][1] == "a/b\nc\n"
    assert not list(tmp_path.glob("files-*"))
