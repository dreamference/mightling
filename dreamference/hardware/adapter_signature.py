"""The signature check of an adapter's manifest (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §5.1).

A manifest is signed with the release key in OpenSSH's SSHSIG format under its own namespace,
`mightling-adapter`, so a release signature (`mightling-release`, over `SHA256SUMS`) is never
accepted as an adapter's and an adapter's never as a release's. Python's standard library has no
Ed25519 and the package carries no cryptography dependency, so the check is `ssh-keygen -Y verify`,
exactly as install.sh checks a release, against the keys below.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final

NAMESPACE: Final[str] = "mightling-adapter"
PRINCIPAL: Final[str] = "mightling-adapter"

# The release keys, one OpenSSH public key per entry: the lines of `ling-rs/release-signing.pub`
# without its comments. A test keeps the two equal, as one keeps install.sh's RELEASE_KEYS equal to
# that file, so a key rotation (RELEASE_SIGNING §5) reaches adapters with the release that carries it.
RELEASE_KEYS: Final[tuple[str, ...]] = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKIG5+J3RTa4AaoT0o2qIhVr7bGvaa+T5b84rDv31ptj mightling-release-2026-10",
)

VERIFY_TIMEOUT_S: Final[float] = 30.0
MISSING_TOOL: Final[str] = (
    "ssh-keygen (OpenSSH 8.1 or newer) is needed to check an adapter's signature: "
    "install the openssh-client package"
)


class AdapterSignature:
    """Checks a manifest's SSHSIG signature against the release keys."""

    # The keys checked against; the tests replace it with session keys.
    keys: tuple[str, ...] = RELEASE_KEYS

    @classmethod
    def allowed_signers(cls) -> str:
        """The `allowed_signers` file `ssh-keygen -Y verify` reads.

        Returns:
            str: One line per key, limited to the adapter namespace.
        """
        lines = []
        for key in cls.keys:
            fields = key.split()
            if len(fields) >= 2:  # noqa: PLR2004 (a key is its type and its body)
                lines.append(f'{PRINCIPAL} namespaces="{NAMESPACE}" {fields[0]} {fields[1]}')
        return "\n".join(lines) + "\n"

    @classmethod
    def problem(cls, data: bytes, signature: bytes) -> str | None:
        """Says what is wrong with a signature, if anything.

        Args:
            data: The signed bytes (`manifest.json`).
            signature: The SSHSIG signature (`manifest.json.sig`).

        Returns:
            str | None: None when one of the release keys signed exactly these bytes under the
            adapter namespace; otherwise the reason.
        """
        tool = shutil.which("ssh-keygen")
        if tool is None:
            return MISSING_TOOL
        if not signature.strip():
            return "manifest.json.sig is empty"
        with tempfile.TemporaryDirectory(prefix="mightling-adapter-sig-") as work:
            signers = Path(work) / "allowed_signers"
            signers.write_text(cls.allowed_signers(), encoding="utf-8")
            sig_path = Path(work) / "manifest.json.sig"
            sig_path.write_bytes(signature)
            try:
                result = subprocess.run(
                    [
                        tool,
                        "-Y",
                        "verify",
                        "-f",
                        str(signers),
                        "-I",
                        PRINCIPAL,
                        "-n",
                        NAMESPACE,
                        "-s",
                        str(sig_path),
                    ],
                    input=data,
                    capture_output=True,
                    timeout=VERIFY_TIMEOUT_S,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                return f"ssh-keygen could not check the signature ({error})"
        if result.returncode != 0:
            detail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            return "the signature is not a Dreamference adapter signature" + (
                f" ({detail[-1]})" if detail else ""
            )
        return None
