"""A node fetches a model's public weights from Hugging Face itself (`node provision --model-source hf`).

For a machine at another site the 22 GiB copy from the main node would cross the internet on the
main node's upload; the house's own download line is faster and the files are public. So the node
downloads each repository at the commit this machine's cache holds (the registry's pin), with the
Hub's own resume of unfinished files and retries around it, and the main node then checks every
file of the snapshot by SHA-256 against its own copy. What the Hub does not carry (a repository that
is not there, or a file that differs from the Hub's) is the only thing sent from here, by rsync. If
the download itself keeps failing, a node of the same run that has the model on the same LAN gives it
over that LAN (`lan_copy`).

The main node's own weight files are trusted by name: a large file in the hub cache is stored under
the SHA-256 of its content; the small files are hashed here. Nothing is stored about the machines but
their names (the LAN copy uses the donor's `<hostname>.local`, resolved there, never an address).
"""

import hashlib
import logging
import re
import secrets
import shlex
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from dreamference.node.fleet_session import AUTHORIZED_KEYS_ENDS_IN_NEWLINE, FleetSession
from dreamference.node.node_model_sync import LFS_BLOB

LOG: Final[logging.Logger] = logging.getLogger(__name__)

# Fields of a public key line that pin a host: its type and its key.
KEY_FIELDS: Final[int] = 2
# The donor's host key as `ssh-keygen` writes it; anything else would reach the recipient's shell.
HOST_KEY: Final[re.Pattern] = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}")

# How often a download is started again, with a pause between: a house line drops now and then.
HF_ATTEMPTS: Final[int] = 8
HF_WAIT_S: Final[float] = 30.0

# One download may take this long (22 GiB at 50 Mbit/s is about an hour).
HF_TIMEOUT_S: Final[int] = 6 * 60 * 60

#: How long the LAN copy's ephemeral key stays valid on the donor if its removal never arrives: the
#: copy's own limit and an hour.
KEY_EXPIRY_HOURS: Final[int] = HF_TIMEOUT_S // 3600 + 1

# The remote script's exit for "the Hub does not have this repository or commit".
NOT_ON_HUB: Final[int] = 3

REMOTE_PYTHON: Final[str] = "$HOME/.local/share/dreamference/venv/bin/python"

# Reads: repository, commit, hub cache folder. Exit 0 downloaded (unfinished files resume), 3 not on
# the Hub, 1 anything else (the network, mostly).
DOWNLOAD_SCRIPT: Final[str] = """
import sys
from huggingface_hub import snapshot_download
from huggingface_hub.utils import RepositoryNotFoundError, RevisionNotFoundError
repo, revision, cache = sys.argv[1:4]
try:
    snapshot_download(repo_id=repo, revision=revision, cache_dir=cache, max_workers=4)
except (RepositoryNotFoundError, RevisionNotFoundError) as error:
    print(f"not on the Hub: {error}", file=sys.stderr)
    sys.exit(3)
except Exception as error:
    print(f"{type(error).__name__}: {error}", file=sys.stderr)
    sys.exit(1)
"""

# rsync's exits that mean the connection went (FleetSession.RSYNC_DROPPED).
LAN_RETRY_CODES: Final[str] = "10|12|30|35|255"


class FleetHubFetch:
    """The model-fetch step of one machine."""

    def __init__(
        self,
        session: FleetSession,
        remote_hub: str,
        hub: Path,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Set up the fetch for one machine.

        Args:
            session: The provisioning session to it (`FleetSession`).
            remote_hub: Its hub cache folder, as the probe read it.
            hub: This machine's hub cache.
            sleep: The pause between attempts (a test replaces it).
        """
        self.session = session
        self.remote_hub = remote_hub
        self.hub = hub
        self.sleep = sleep

    # -- what this machine holds ------------------------------------------------------------------

    @classmethod
    def repo_of(cls, folder: str) -> str:
        """Names the repository a hub cache folder holds.

        Args:
            folder: A hub cache folder name (`models--owner--name`).

        Returns:
            str: The repository id.
        """
        return folder.removeprefix("models--").replace("--", "/", 1)

    def manifest(self, folder: str) -> tuple[str, dict[str, str]] | None:
        """Names every file of this machine's snapshot with its SHA-256.

        The snapshot is the registry's pin when this cache has it, else the one `refs/main` names,
        else the only one. A large file's checksum is its blob's name; the others are hashed.

        Args:
            folder: A hub cache folder name.

        Returns:
            tuple[str, dict[str, str]] | None: The commit and `{path in the snapshot: sha256}`; None
            when this machine has no snapshot of it.
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

        root = self.hub / folder
        snapshots = root / "snapshots"
        if not snapshots.is_dir():
            return None
        names = sorted(path.name for path in snapshots.iterdir() if path.is_dir())
        pin = ModelMatrixRegistry.pinned_revision(self.repo_of(folder))
        main = root / "refs" / "main"
        main_commit = main.read_text().strip() if main.is_file() else ""
        revision = next(
            (name for name in (pin, main_commit) if name and name in names),
            names[0] if len(names) == 1 else "",
        )
        if not revision:
            return None
        files: dict[str, str] = {}
        base = snapshots / revision
        for path in sorted(base.rglob("*")):
            if path.is_dir():
                continue
            relative = path.relative_to(base).as_posix()
            real = path.resolve()
            if not real.is_file():
                continue
            files[relative] = real.name if LFS_BLOB.fullmatch(real.name) else self._sha256(real)
        return revision, files

    @classmethod
    def _sha256(cls, path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    # -- the download -----------------------------------------------------------------------------

    def download(self, repo: str, revision: str) -> int:
        """Has the machine download one repository at one commit, again while the line drops.

        Args:
            repo: The Hugging Face repository.
            revision: The commit.

        Returns:
            int: 0 when downloaded, `NOT_ON_HUB` when the Hub has no such repository or commit, 1
            when the attempts ran out.
        """
        line = (
            f"HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DOWNLOAD_TIMEOUT=60 {REMOTE_PYTHON} -c "
            f"{shlex.quote(DOWNLOAD_SCRIPT)} {shlex.quote(repo)} {shlex.quote(revision)} "
            f"{shlex.quote(self.remote_hub)}"
        )
        code = 1
        for attempt in range(1, HF_ATTEMPTS + 1):
            answer = self.session.run(line, timeout=HF_TIMEOUT_S)
            code = answer.returncode
            if code == 0:
                return 0
            if code == NOT_ON_HUB:
                return NOT_ON_HUB
            LOG.warning(
                f"⏳ {self.session.host}: the download of {repo} stopped (exit {code}: "
                f"{(answer.stderr or '').strip()[-120:]}); attempt {attempt} of {HF_ATTEMPTS}."
            )
            if attempt < HF_ATTEMPTS:
                self.sleep(HF_WAIT_S)
        return 1

    # -- the check --------------------------------------------------------------------------------

    def mismatches(self, folder: str, revision: str, files: dict[str, str]) -> list[str]:
        """Compares the machine's snapshot with this machine's by SHA-256.

        Args:
            folder: A hub cache folder name.
            revision: The commit.
            files: This machine's manifest of it.

        Returns:
            list[str]: The paths the machine lacks or holds with other content.
        """
        snapshot = f"{self.remote_hub}/{folder}/snapshots/{revision}"
        script = (
            f"cd {shlex.quote(snapshot)} 2>/dev/null && xargs -d '\\n' -r sha256sum -- 2>/dev/null"
        )
        answer = self.session.run(script, input_text="\n".join(files) + "\n", timeout=60 * 60)
        theirs: dict[str, str] = {}
        for line in (answer.stdout or "").splitlines():
            digest, _, name = line.partition("  ")
            if digest and name:
                theirs[name] = digest
        return [name for name, digest in files.items() if theirs.get(name) != digest]

    def verify(self, folder: str) -> bool:
        """Checks a whole folder against this machine's by SHA-256.

        Args:
            folder: A hub cache folder name.

        Returns:
            bool: True when every file of this machine's snapshot is there and equal.
        """
        found = self.manifest(folder)
        return found is not None and not self.mismatches(folder, found[0], found[1])

    def send(self, folder: str, revision: str, names: list[str], attempts: int) -> bool:
        """Sends the files the Hub did not give, from this machine, by rsync.

        Each goes as its blob and its link in the snapshot, so the cache keeps its shape.

        Args:
            folder: A hub cache folder name.
            revision: The commit.
            names: Paths in the snapshot.
            attempts: How often an interrupted copy is started again.

        Returns:
            bool: True when rsync succeeded.
        """
        root = self.hub / folder
        listed: list[str] = []
        for name in names:
            link = root / "snapshots" / revision / name
            listed.append(f"snapshots/{revision}/{name}")
            real = link.resolve()
            if real.parent == (root / "blobs").resolve():
                listed.append(f"blobs/{real.name}")
        answer = self.session.rsync(
            root, f"{self.remote_hub}/{folder}", attempts=attempts, files=sorted(set(listed))
        )
        return answer.returncode == 0

    # -- one folder -------------------------------------------------------------------------------

    def ensure(self, folder: str, attempts: int = 30) -> dict[str, Any]:
        """Brings one repository to the machine: Hub first, then only what differs, then the check.

        Args:
            folder: A hub cache folder name.
            attempts: How often an interrupted rsync is started again.

        Returns:
            dict[str, Any]: `ok`; `hub_failed` (the download kept failing, so the LAN copy may
            help); `sent` (files that came from this machine); `why` when not ok.
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

        found = self.manifest(folder)
        if found is None:
            return {
                "ok": False,
                "hub_failed": False,
                "sent": 0,
                "why": "not in this machine's cache",
            }
        revision, files = found
        repo = self.repo_of(folder)
        if ModelMatrixRegistry.get_release_source(repo) is None:
            code = self.download(repo, revision)
            if code == 1:
                return {"ok": False, "hub_failed": True, "sent": 0, "why": "the download failed"}
        bad = self.mismatches(folder, revision, files)
        sent = 0
        if bad:
            if not self.send(folder, revision, bad, attempts):
                return {"ok": False, "hub_failed": False, "sent": 0, "why": "the copy failed"}
            sent = len(bad)
            bad = self.mismatches(folder, revision, files)
        if bad:
            return {
                "ok": False,
                "hub_failed": False,
                "sent": sent,
                "why": f"{len(bad)} file(s) differ from this machine's: {', '.join(bad[:3])}",
            }
        return {"ok": True, "hub_failed": False, "sent": sent, "why": ""}

    # -- the fallback: from a neighbour on the same LAN -------------------------------------------

    def lan_copy(
        self, folder: str, donor: FleetSession, donor_name: str, donor_user: str, donor_hub: str
    ) -> bool:
        """Copies one folder from a machine of this run over the machines' own LAN.

        An ephemeral key made on this machine is authorised on the donor (restricted, removed at the
        end, whatever happens) and the donor's host key is pinned from its own session, so nothing
        persistent is added and nothing is trusted on first use. The copy runs between the two
        machines; nothing passes through here.

        Args:
            folder: A hub cache folder name.
            donor: The donor's provisioning session.
            donor_name: The donor's name on the LAN (`<hostname>.local`).
            donor_user: The account there.
            donor_hub: The donor's hub cache folder.

        Returns:
            bool: True when rsync succeeded between them.
        """
        token = f"mightling-lan-copy-{secrets.token_hex(6)}"
        made = self.session.run(
            'd="$(mktemp -d)" && ssh-keygen -q -t ed25519 -N "" -C '
            f'{token} -f "$d/k" && printf "%s\\n" "$d" && cat "$d/k.pub"'
        )
        lines = (made.stdout or "").splitlines()
        if made.returncode != 0 or len(lines) < KEY_FIELDS:
            return False
        workdir, public = lines[0].strip(), lines[1].strip()
        try:
            hostkey = donor.run("cat /etc/ssh/ssh_host_ed25519_key.pub")
            fields = (hostkey.stdout or "").split()
            if hostkey.returncode != 0 or len(fields) < KEY_FIELDS:
                return False
            if not HOST_KEY.fullmatch(" ".join(fields[:KEY_FIELDS])):
                LOG.warning(
                    "%s gave a host key that is not an ed25519 key; no LAN copy.", donor_name
                )
                return False
            # `expiry-time` (the donor's own clock) ends the key even when the removal below never
            # reaches the donor: the link is failing, which is why this copy runs at all.
            authorised = donor.run(
                f"umask 077; mkdir -p ~/.ssh && {AUTHORIZED_KEYS_ENDS_IN_NEWLINE} && "
                f"expires=\"$(date -d '+{KEY_EXPIRY_HOURS} hours' +%Y%m%d%H%M)\" && "
                'printf \'restrict,expiry-time="%s" %s\\n\' "$expires" '
                f"{shlex.quote(public)} >> ~/.ssh/authorized_keys"
            )
            if authorised.returncode != 0:
                return False
            transport = (
                f"ssh -i {workdir}/k -o IdentitiesOnly=yes -o BatchMode=yes "
                f"-o UserKnownHostsFile={workdir}/kh -o StrictHostKeyChecking=yes "
                "-o ServerAliveInterval=15 -o ServerAliveCountMax=4"
            )
            copy = (
                f"printf '%s %s %s\\n' {shlex.quote(donor_name)} {shlex.quote(fields[0])} "
                f"{shlex.quote(fields[1])} "
                f"> {workdir}/kh && for i in $(seq 1 20); do "
                f"rsync -aH --partial --append-verify --timeout=120 --mkpath "
                f"-e {shlex.quote(transport)} "
                f"{shlex.quote(f'{donor_user}@{donor_name}:{donor_hub}/{folder}/')} "
                f"{shlex.quote(f'{self.remote_hub}/{folder}/')} && exit 0; s=$?; "
                f'case "$s" in {LAN_RETRY_CODES}) sleep 10;; *) exit "$s";; esac; done; exit 1'
            )
            return self.session.run(copy, timeout=HF_TIMEOUT_S).returncode == 0
        finally:
            removed = donor.run(f"sed -i '/{token}/d' ~/.ssh/authorized_keys")
            if removed.returncode != 0:
                LOG.warning(
                    "The LAN copy's key %s is still authorised on %s until it expires (%d h); "
                    "remove its line from ~/.ssh/authorized_keys there.",
                    token,
                    donor_name,
                    KEY_EXPIRY_HOURS,
                )
            self.session.run(f"rm -rf {shlex.quote(workdir)}")
