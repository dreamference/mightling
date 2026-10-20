"""The node's own certificate authority (REMOTE_ACCESS §0.3, §2, §14.3).

Made once by `remote setup`, with `openssl` (no Python dependency), under `remote/ca/` with its key
readable only by the user. It signs the control plane's certificate (the box's name, and
`localhost` for `ling-admin`'s own calls on loopback) and the relay's (the relay's name). Clients
put it in their system trust store, because NetBird's client trusts nothing else (§14.2, item 2).

A CA in a client's system store could vouch for any site, so this one is **name-constrained**: it
may sign only names under its scope (the box's domain, chosen when the CA is made), the overlay's
domain and `localhost`, and no address but loopback. A stolen key cannot impersonate a bank.

The CA lives ten years; a server certificate 397 days, since macOS refuses one valid for more than
825 even under a root it trusts. `remote setup` reissues a certificate with less than 30 days left.
"""

import datetime
import hashlib
import secrets
import ssl
from pathlib import Path

from dreamference.remote.remote_executor import RemoteExecutor
from dreamference.remote.remote_state import PRIVATE_FILE_MODE, RemoteState

CA_DAYS = 3650
LEAF_DAYS = 397
RENEW_DAYS = 30
OVERLAY_DOMAIN = "netbird.selfhosted"
LOCALHOST = "localhost"
# A name of three labels or more is scoped to its parent (`nb.example.org` → `example.org`), so a
# replacement box under the same domain needs no new CA; a shorter one to itself.
SCOPE_PARENT_MIN_LABELS = 3
SERIAL_BYTES = 16
OPENSSL_DATE = "%b %d %H:%M:%S %Y %Z"
CURVE = ("-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256")


class RemoteCA:
    """Makes the CA, issues and inspects its certificates."""

    @classmethod
    def key_path(cls) -> Path:
        """The CA's private key.

        Returns:
            Path: `remote/ca/ca.key`.
        """
        return RemoteState.ca_dir() / "ca.key"

    @classmethod
    def cert_path(cls) -> Path:
        """The CA's certificate, the one file clients trust.

        Returns:
            Path: `remote/ca/ca.crt`.
        """
        return RemoteState.ca_dir() / "ca.crt"

    @classmethod
    def exists(cls) -> bool:
        """Whether the CA was made.

        Returns:
            bool: True when both its key and its certificate are there.
        """
        return cls.key_path().is_file() and cls.cert_path().is_file()

    @classmethod
    def default_scope(cls, dns_name: str) -> str:
        """The names a new CA may sign under, for a box name.

        Args:
            dns_name: The box's DNS name.

        Returns:
            str: The parent domain of a name of three labels or more, else the name itself.
        """
        labels = dns_name.lower().strip(".").split(".")
        return ".".join(labels[1:]) if len(labels) >= SCOPE_PARENT_MIN_LABELS else ".".join(labels)

    @classmethod
    def in_scope(cls, name: str, scope: str) -> bool:
        """Whether the CA's constraints let it sign a name.

        Args:
            name: A DNS name.
            scope: The CA's scope.

        Returns:
            bool: True for the scope itself, a name under it, the overlay's names and localhost.
        """
        name = name.lower().strip(".")
        return any(
            name == base or name.endswith("." + base) for base in (scope.lower(), OVERLAY_DOMAIN)
        ) or (name == LOCALHOST)

    @classmethod
    def name_constraints(cls, scope: str) -> str:
        """The CA's nameConstraints extension, as openssl takes it.

        Args:
            scope: The domain it may sign under.

        Returns:
            str: The extension's value.
        """
        permitted = [
            f"DNS:{scope}",
            f"DNS:{OVERLAY_DOMAIN}",
            "DNS:localhost",
            "IP:127.0.0.1/255.255.255.255",
        ]
        return "critical," + ",".join(f"permitted;{entry}" for entry in permitted)

    @classmethod
    def create(cls, scope: str, label: str) -> bool:
        """Makes the CA, unless it exists.

        Args:
            scope: The domain it may sign under.
            label: Words for its common name (the node's name).

        Returns:
            bool: Whether the CA exists afterwards.
        """
        if cls.exists():
            return True
        RemoteState.ensure_dir(RemoteState.ca_dir())
        argv = [
            "openssl", "req", "-x509", *CURVE, "-nodes", "-sha256",
            "-days", str(CA_DAYS),
            "-keyout", str(cls.key_path()), "-out", str(cls.cert_path()),
            "-subj", f"/CN=Mightling node CA ({label})",
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            "-addext", f"nameConstraints={cls.name_constraints(scope)}",
        ]  # fmt: skip
        if not RemoteExecutor.ok(argv):
            return False
        cls.key_path().chmod(PRIVATE_FILE_MODE)
        return cls.exists()

    @classmethod
    def issue(cls, name: str, sans: list[str], out_dir: Path) -> bool:
        """Issues a server certificate signed by the CA, replacing one of the same name.

        Args:
            name: The file stem (`management`, `relay`) under `out_dir`.
            sans: The DNS names it is valid for; the first is its common name.
            out_dir: Where `<name>.crt` and `<name>.key` go.

        Returns:
            bool: Whether both files were written.
        """
        RemoteState.ensure_dir(out_dir)
        key, csr, crt, ext = (
            out_dir / f"{name}{suffix}" for suffix in (".key", ".csr", ".crt", ".ext")
        )
        ext_text = (
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature\n"
            "extendedKeyUsage=serverAuth\n"
            f"subjectAltName={','.join(f'DNS:{san}' for san in sans)}\n"
        )
        RemoteState.write_private(ext, ext_text)
        request = ["openssl", "req", "-new", *CURVE, "-nodes", "-keyout", str(key), "-out", str(csr),
                   "-subj", f"/CN={sans[0]}"]  # fmt: skip
        sign = [
            "openssl", "x509", "-req", "-in", str(csr), "-sha256",
            "-CA", str(cls.cert_path()), "-CAkey", str(cls.key_path()),
            "-set_serial", "0x" + secrets.token_hex(SERIAL_BYTES),
            "-days", str(LEAF_DAYS), "-extfile", str(ext), "-out", str(crt),
        ]  # fmt: skip
        written = RemoteExecutor.ok(request) and RemoteExecutor.ok(sign)
        for leftover in (csr, ext):
            leftover.unlink(missing_ok=True)
        if not written or not key.is_file() or not crt.is_file():
            return False
        key.chmod(PRIVATE_FILE_MODE)
        return True

    @classmethod
    def not_after(cls, cert: Path) -> datetime.datetime | None:
        """When a certificate expires.

        Args:
            cert: The certificate file.

        Returns:
            datetime.datetime | None: Its `notAfter`, in UTC, or None when it cannot be read.
        """
        result = RemoteExecutor.run(["openssl", "x509", "-noout", "-enddate", "-in", str(cert)])
        _, found, value = result.stdout.strip().partition("=")
        if result.returncode != 0 or not found:
            return None
        try:
            parsed = datetime.datetime.strptime(value, OPENSSL_DATE)
        except ValueError:
            return None
        return parsed.replace(tzinfo=datetime.UTC)

    @classmethod
    def days_left(cls, cert: Path, now: datetime.datetime | None = None) -> int | None:
        """Whole days until a certificate expires.

        Args:
            cert: The certificate file.
            now: The time; defaults to the clock.

        Returns:
            int | None: The days (negative once expired), or None when it cannot be read.
        """
        expiry = cls.not_after(cert)
        if expiry is None:
            return None
        return (expiry - (now or datetime.datetime.now(datetime.UTC))).days

    @classmethod
    def needs_renewal(cls, cert: Path, now: datetime.datetime | None = None) -> bool:
        """Whether a certificate is missing, unreadable or expires within `RENEW_DAYS`.

        Args:
            cert: The certificate file.
            now: The time; defaults to the clock.

        Returns:
            bool: True when it should be issued again.
        """
        left = cls.days_left(cert, now)
        return left is None or left < RENEW_DAYS

    @classmethod
    def pem(cls) -> str:
        """The CA's certificate, as clients install it.

        Returns:
            str: The PEM text, or "" before the CA exists.
        """
        try:
            return cls.cert_path().read_text(encoding="ascii")
        except OSError:
            return ""

    @classmethod
    def fingerprint(cls) -> str:
        """The CA certificate's SHA-256 fingerprint, for a person to compare.

        Returns:
            str: Hex pairs separated by colons, or "" before the CA exists.
        """
        text = cls.pem()
        if not text:
            return ""
        digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(text)).hexdigest().upper()
        return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))
