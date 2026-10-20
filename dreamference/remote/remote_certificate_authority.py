"""The node's own certificate authority (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2, §4.1).

Made once by `ling-admin remote setup`, with `openssl` (no Python dependency is added for it). The
control plane's certificate and the relay's are issued from it, and every enrolled client installs
its certificate in the system trust store, which is the only way NetBird's client trusts a private
CA. A root in a system store could vouch for any site, so this one is **name-constrained**: it may
only sign the box's DNS name (and names under it) and the overlay's domain. A certificate it signed
for anything else is refused by the client's TLS stack, so a stolen CA key cannot be used to
impersonate a bank to an enrolled laptop. The price: a box with a different DNS name needs a new CA
and a new enrolment of every client; a replacement box under the same name needs neither.

Keys are EC P-256, the CA's mode 0600 in a 0700 folder. Every `openssl` call goes through `_run`,
the seam the tests replace.
"""

import datetime
import logging
import subprocess
from pathlib import Path
from typing import Final

from dreamference.remote.remote_settings import OVERLAY_DOMAIN, RemoteSettings

# Failures go to standard error through this logger; the console-facing `RemoteAccess` prints
# the rest (specs/DREAMFERENCE_PYTHON_QUALITY.md §3.9).
LOG: Final[logging.Logger] = logging.getLogger(__name__)

CA_DAYS: Final[int] = 3650
# The ceiling Apple's and the browsers' stores accept for a server certificate.
LEAF_DAYS: Final[int] = 825


class RemoteCertificateAuthority:
    """`~/.config/dreamference/remote/ca/`: the CA, and the server certificates it issues."""

    @classmethod
    def _run(cls, argv: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                argv, capture_output=True, text=True, input=input_text, timeout=60, check=False
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(argv, 127, "", str(error))

    @classmethod
    def ca_certificate(cls) -> Path:
        """The CA's certificate.

        Returns:
            Path: The CA's certificate, the one file clients install.
        """
        return RemoteSettings.path("ca", "ca.crt")

    @classmethod
    def ca_key(cls) -> Path:
        """The CA's private key.

        Returns:
            Path: The CA's private key; it never leaves the node.
        """
        return RemoteSettings.path("ca", "ca.key")

    @classmethod
    def constraints(cls, scope: str) -> str:
        """The CA's name constraints.

        Args:
            scope: The domain it may sign under (`remote setup --ca-scope`; by default the parent
                of the box's name, so a later box under the same domain needs no new CA).

        Returns:
            str: The `nameConstraints` extension's value: the scope and the overlay's domain.
        """
        return f"critical,permitted;DNS:{scope},permitted;DNS:{OVERLAY_DOMAIN}"

    @classmethod
    def ensure(cls, scope: str) -> bool:
        """Makes the CA if there is none.

        An existing CA is kept, whatever name it was made for: `remote setup` checks that first
        (`permits`).

        Args:
            scope: The domain a new CA is constrained to.

        Returns:
            bool: True when a CA exists afterwards.
        """
        if cls.ca_certificate().is_file() and cls.ca_key().is_file():
            return True
        folder = cls.ca_key().parent
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        result = cls._run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-nodes",
                "-days",
                str(CA_DAYS),
                "-subj",
                "/O=Mightling/CN=Mightling node CA",
                "-keyout",
                str(cls.ca_key()),
                "-out",
                str(cls.ca_certificate()),
                "-addext",
                "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
                "-addext",
                f"nameConstraints={cls.constraints(scope)}",
            ]
        )
        if result.returncode != 0:
            LOG.error(f"❌ openssl could not make the node's CA: {result.stderr.strip()[:300]}")
            return False
        cls.ca_key().chmod(0o600)
        return True

    @classmethod
    def permits(cls, dns_name: str) -> bool:
        """Whether the existing CA may sign a name.

        Args:
            dns_name: The box's public DNS name.

        Returns:
            bool: Whether the existing CA may sign that name (its constraints name it or a domain
            above it). False when there is no CA.
        """
        result = cls._run(
            [
                "openssl",
                "x509",
                "-in",
                str(cls.ca_certificate()),
                "-noout",
                "-ext",
                "nameConstraints",
            ]
        )
        if result.returncode != 0:
            return False
        permitted = [
            line.strip()[4:]
            for line in result.stdout.splitlines()
            if line.strip().startswith("DNS:")
        ]
        name = dns_name.lower().rstrip(".")
        return any(
            name == entry.lower() or name.endswith("." + entry.lower().lstrip("."))
            for entry in permitted
        )

    @classmethod
    def issue(cls, label: str, names: list[str]) -> tuple[Path, Path] | None:
        """Issues a server certificate.

        Args:
            label: The file name under `certs/` (`control-plane`, `relay`).
            names: The DNS names it is for, the first as its common name.

        Returns:
            Optional[Tuple[Path, Path]]: The certificate and its key, or None when openssl failed.
        """
        folder = RemoteSettings.path("certs")
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        key, request, certificate = (
            folder / f"{label}.key",
            folder / f"{label}.csr",
            folder / f"{label}.crt",
        )
        made = cls._run(
            [
                "openssl",
                "req",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-nodes",
                "-subj",
                f"/O=Mightling/CN={names[0]}",
                "-keyout",
                str(key),
                "-out",
                str(request),
            ]
        )
        if made.returncode != 0:
            LOG.error(f"❌ openssl could not make the {label} key: {made.stderr.strip()[:300]}")
            return None
        key.chmod(0o600)
        extensions = "\n".join(
            [
                "subjectAltName=" + ",".join(f"DNS:{name}" for name in names),
                "extendedKeyUsage=serverAuth",
                "keyUsage=critical,digitalSignature",
                "basicConstraints=critical,CA:FALSE",
            ]
        )
        extension_file = folder / f"{label}.ext"
        extension_file.write_text(extensions + "\n")
        signed = cls._run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(request),
                "-CA",
                str(cls.ca_certificate()),
                "-CAkey",
                str(cls.ca_key()),
                "-CAcreateserial",
                "-days",
                str(LEAF_DAYS),
                "-out",
                str(certificate),
                "-extfile",
                str(extension_file),
            ]
        )
        if signed.returncode != 0:
            LOG.error(
                f"❌ openssl could not sign the {label} certificate: {signed.stderr.strip()[:300]}"
            )
            return None
        return certificate, key

    @classmethod
    def expiry(cls, certificate: Path) -> datetime.datetime | None:
        """When a certificate expires.

        Args:
            certificate: A certificate file.

        Returns:
            Optional[datetime.datetime]: When it expires (UTC), or None when it cannot be read.
        """
        result = cls._run(["openssl", "x509", "-in", str(certificate), "-noout", "-enddate"])
        _, found, text = result.stdout.strip().partition("=")
        if result.returncode != 0 or not found:
            return None
        try:
            return datetime.datetime.strptime(text, "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=datetime.UTC
            )
        except ValueError:
            return None
