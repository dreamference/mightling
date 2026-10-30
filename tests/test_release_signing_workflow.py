"""The release job's signing (specs/DREAMFERENCE_RELEASE_SIGNING.md §1, §4, §5).

The workflow's shape is asserted, and its "Write and sign SHA256SUMS" step runs as GitHub runs it
(`bash -eo pipefail`) in a folder laid out like the job's, with session keys from ssh-keygen,
never the release key. Nothing is uploaded: the publishing step is never run.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
ATTEST = "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"
SIGN_STEP = "Write and sign SHA256SUMS"
TARGET = "aarch64-unknown-linux-gnu"


def workflow(name="release.yml"):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def release_steps():
    return workflow()["jobs"]["release"]["steps"]


def step(name):
    return next(s for s in release_steps() if s.get("name") == name)


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    folder = tmp_path_factory.mktemp("keys")
    for name in ("release", "next", "attacker"):
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(folder / name)],
            check=True,
        )
    return folder


def endorse(keys, public, signer, namespace="mightling-release-key"):
    """`ssh-keygen -Y sign` of a key file, as §5 step 2 does."""
    subprocess.run(
        ["ssh-keygen", "-q", "-Y", "sign", "-f", str(keys / signer), "-n", namespace, str(public)],
        check=True,
        capture_output=True,
    )


def run_sign_step(tmp_path, keys, secret, transition=None):
    """Runs the step in `tmp_path/job` as GitHub runs it.

    The trusted key file is checked out under source/, the builds' files are under artifacts/ (in
    subfolders, as merge-multiple leaves them).
    """
    job = tmp_path / "job"
    (job / "source" / "ling-rs").mkdir(parents=True)
    (job / "source" / "ling-rs" / "release-signing.pub").write_text(
        "# the trusted keys\n" + (keys / "release.pub").read_text()
    )
    if transition:
        signer, new_key = transition
        public = job / "source" / "ling-rs" / "release-key-transition.pub"
        public.write_text((keys / f"{new_key}.pub").read_text())
        endorse(keys, public, signer)
    (job / "artifacts" / "ling").mkdir(parents=True)
    (job / "artifacts" / "ling" / f"ling-{TARGET}.gz").write_bytes(b"ling")
    (job / "artifacts" / "deb").mkdir()
    (job / "artifacts" / "deb" / "mightling_9.9.9_arm64.deb").write_bytes(b"deb")
    (job / "artifacts" / "install.sh").write_text("#!/bin/sh\n")
    temp = tmp_path / "tmp"
    temp.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "TMPDIR": str(temp),
        "RELEASE_SIGNING_KEY": (keys / secret).read_text() if secret else "",
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step(SIGN_STEP)["run"]],
        cwd=job,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return job, temp, result


def verifies(job, keys, key):
    signers = job / "signers"
    signers.write_text(
        "mightling-release " + " ".join((keys / f"{key}.pub").read_text().split()[:2]) + "\n"
    )
    with (job / "SHA256SUMS").open("rb") as message:
        return (
            subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "verify",
                    "-f",
                    str(signers),
                    "-I",
                    "mightling-release",
                    "-n",
                    "mightling-release",
                    "-s",
                    str(job / "SHA256SUMS.sig"),
                ],
                stdin=message,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )


def test_the_release_job_alone_holds_the_key_and_the_attestation_rights():
    jobs = workflow()["jobs"]
    assert jobs["release"]["environment"] == "release"
    assert [name for name, job in jobs.items() if "environment" in job] == ["release"]
    assert jobs["release"]["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert workflow()["permissions"] == {"contents": "read"}
    for name, job in jobs.items():
        if name != "release":
            assert not {"id-token", "attestations"} & set(job.get("permissions") or {}), name
    build = (WORKFLOWS / "release-build.yml").read_text()
    assert "id-token" not in build and "attestations:" not in build
    holders = [s.get("name") for s in release_steps() if "RELEASE_SIGNING_KEY" in json.dumps(s)]
    assert holders == [SIGN_STEP]
    assert step(SIGN_STEP)["env"] == {"RELEASE_SIGNING_KEY": "${{ secrets.RELEASE_SIGNING_KEY }}"}


def test_sums_are_checked_then_signed_then_attested_then_published():
    names = [s.get("name") for s in release_steps()]
    order = [
        "Check the builds' checksums",
        SIGN_STEP,
        "Attest build provenance",
        "Publish GitHub release",
    ]
    assert [name for name in names if name in order] == order
    attest = step("Attest build provenance")
    assert attest["uses"] == ATTEST
    assert attest["with"] == {"subject-checksums": "attested.sha256"}
    assert "files+=(SHA256SUMS SHA256SUMS.sig)" in step("Publish GitHub release")["run"]


def test_a_dispatch_publishes_unless_it_asks_for_a_draft():
    inputs = workflow().get("on", workflow().get(True))["workflow_dispatch"]["inputs"]
    assert inputs["draft"]["default"] is False


def test_the_step_signs_every_file_and_attests_them_with_the_signature(tmp_path, keys):
    job, temp, result = run_sign_step(tmp_path, keys, "release")
    assert result.returncode == 0, result.stdout + result.stderr
    assert verifies(job, keys, "release")
    assert not verifies(job, keys, "attacker")
    listed = {line.split()[1] for line in (job / "SHA256SUMS").read_text().splitlines()}
    assert listed == {f"ling-{TARGET}.gz", "mightling_9.9.9_arm64.deb", "install.sh"}
    attested = {line.split()[1] for line in (job / "attested.sha256").read_text().splitlines()}
    assert attested == listed | {"SHA256SUMS", "SHA256SUMS.sig"}
    # The key was on disk only for the signing call.
    for file in temp.rglob("*"):
        assert not file.is_file() or b"PRIVATE KEY" not in file.read_bytes(), file


def test_a_missing_secret_stops_the_release_before_anything_is_signed(tmp_path, keys):
    job, _, result = run_sign_step(tmp_path, keys, None)
    assert result.returncode != 0
    assert "The RELEASE_SIGNING_KEY secret is not set" in result.stdout
    assert not (job / "SHA256SUMS.sig").exists()
    assert not (job / "attested.sha256").exists()


def test_a_secret_that_is_not_a_release_key_stops_the_release(tmp_path, keys):
    job, _, result = run_sign_step(tmp_path, keys, "attacker")
    assert result.returncode != 0
    assert "RELEASE_SIGNING_KEY is not a release key" in result.stdout
    assert not (job / "attested.sha256").exists()


def test_after_a_rotation_the_new_key_signs_and_the_transition_ships(tmp_path, keys):
    job, _, result = run_sign_step(tmp_path, keys, "next", transition=("release", "next"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert verifies(job, keys, "next")
    listed = {line.split()[1] for line in (job / "SHA256SUMS").read_text().splitlines()}
    assert {"release-key-transition.pub", "release-key-transition.pub.sig"} <= listed


def test_a_transition_the_trusted_key_did_not_sign_stops_the_release(tmp_path, keys):
    job, _, result = run_sign_step(tmp_path, keys, "attacker", transition=("attacker", "attacker"))
    assert result.returncode != 0
    # The endorsement is what fails, before the checksums' signature is even checked.
    assert "Could not verify signature" in result.stdout + result.stderr
    assert "not a release key" not in result.stdout
    assert not (job / "attested.sha256").exists()
