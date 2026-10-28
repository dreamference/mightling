"""The control plane's public certificate (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §15).

Android's NetBird app trusts only the system store, which a user cannot add the node's CA to, so the
box's public name (`remote.dreamference.ai`) gets a Let's Encrypt certificate for the control plane
alone (the owner's decision of 2026-10-28). It is issued and renewed **on the node**, by lego (pinned
by release and SHA-256) run as the user, with a DNS-01 challenge in the owner's Cloudflare zone: the
key never leaves the node, and the box keeps passing TLS through by server name, unopened.

A CAA record on the box's name lets only this node's ACME account issue for it, and only by DNS-01:
the box, which answers for the name on 443, could otherwise prove control of it to any public CA.

The certificate files live under `~/.config/dreamference/remote/acme/` (lego writes them 0600 in
0700 folders) and are copied into the control plane's `server/` folder, which the container mounts.
NetBird's combined server reads its certificate once at start, so a new one means a container
restart: the peers' WireGuard sessions are peer to peer and carry on, and their management and
signal streams reconnect by themselves.

lego, Cloudflare's API and the TLS probes go through `_run`, `_download`, `_cloudflare` and
`_served_der`; the tests replace those.
"""

import datetime
import hashlib
import io
import json
import logging
import os
import platform
import socket
import ssl
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Final

from dreamference.remote.remote_control_plane import RemoteControlPlane
from dreamference.remote.remote_settings import (
    ACME_PRODUCTION,
    ACME_STAGING,
    CERTIFICATE_ALERT_UNIT,
    CERTIFICATE_SERVICE_UNIT,
    CERTIFICATE_TIMER_UNIT,
    CLOUDFLARE_API,
    CLOUDFLARE_TOKEN_FILE,
    CONTROL_PLANE_PORT,
    LEGO_ARCHIVES,
    LEGO_URL,
    LEGO_VERSION,
    PUBLIC_RENEW_DAYS,
    RemoteSettings,
)

LOG: Final[logging.Logger] = logging.getLogger(__name__)

ARCHITECTURES: Final[dict[str, str]] = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}
# lego's account name under its folder; no e-mail is registered (Let's Encrypt sends none any more).
ACCOUNT_ID: Final[str] = "mightling-node"
KEY_TYPE: Final[str] = "EC256"
LEGO_TIMEOUT_S: Final[int] = 600
RELOAD_WAIT_S: Final[float] = 90.0
RELOAD_POLL_S: Final[float] = 2.0
CAA_ISSUER: Final[str] = "letsencrypt.org"
HTTP_OK: Final[int] = 200
PEM_END: Final[str] = "-----END CERTIFICATE-----"
SECRET_WORD: Final[str] = "token"
LEGO_NAME: Final[str] = "lego"
NOT_AFTER: Final[str] = "notAfter"
ISSUER_FIELDS: Final[tuple[str, str]] = ("organizationName", "commonName")


class RemotePublicCertificate:
    """lego, the CAA record, the copy into the control plane, and the renewal timer."""

    # -- seams ------------------------------------------------------------------------------------

    @classmethod
    def _run(
        cls, argv: list[str], env: dict[str, str] | None = None, timeout: int = LEGO_TIMEOUT_S
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, check=False, env=env
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(argv, 127, "", str(error))

    @classmethod
    def _download(cls, url: str) -> bytes | None:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except OSError as error:
            LOG.error(f"❌ Could not download {url}: {error}")
            return None

    @classmethod
    def _cloudflare(
        cls, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        """One call to Cloudflare's API with the owner's token, which is never printed."""
        token = cls.token()
        if token is None:
            return 0, {"errors": [{"message": "no Cloudflare token"}]}
        request = urllib.request.Request(
            CLOUDFLARE_API + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"{}")
            except ValueError:
                return error.code, {}
        except (OSError, ValueError) as error:
            return 0, {"errors": [{"message": type(error).__name__}]}

    @classmethod
    def _served_der(cls, host: str, port: int, server_name: str) -> bytes | None:
        """The leaf certificate a TLS server presents, unverified (compared by fingerprint)."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            with (
                socket.create_connection((host, port), timeout=10) as raw,
                context.wrap_socket(raw, server_hostname=server_name) as tls,
            ):
                return tls.getpeercert(binary_form=True)
        except (OSError, ssl.SSLError):
            return None

    # -- files ------------------------------------------------------------------------------------

    @classmethod
    def folder(cls, staging: bool = False) -> Path:
        """The folder lego keeps its account and the certificates in.

        Args:
            staging: Let's Encrypt's staging CA (for a test; its certificates are never served).

        Returns:
            Path: `remote/acme` or `remote/acme-staging`.
        """
        return RemoteSettings.path("acme-staging" if staging else "acme")

    @classmethod
    def files(cls, dns_name: str, staging: bool = False) -> tuple[Path, Path]:
        """Where lego keeps the certificate (with its chain) and its key.

        Args:
            dns_name: The box's public DNS name.
            staging: The staging CA's copy.

        Returns:
            Tuple[Path, Path]: The certificate and the key.
        """
        base = cls.folder(staging) / "certificates"
        return base / f"{dns_name}.crt", base / f"{dns_name}.key"

    @classmethod
    def served_files(cls) -> tuple[Path, Path]:
        """The certificate and key the control plane's container reads.

        Returns:
            Tuple[Path, Path]: `server/tls.crt` and `server/tls.key`.
        """
        return RemoteSettings.path("server", "tls.crt"), RemoteSettings.path("server", "tls.key")

    @classmethod
    def token(cls) -> str | None:
        """The owner's Cloudflare token.

        Returns:
            Optional[str]: The token, or None when the file is missing or empty.
        """
        try:
            text = Path(CLOUDFLARE_TOKEN_FILE).expanduser().read_text().strip()
        except OSError:
            return None
        return text or None

    @classmethod
    def fingerprint(cls, pem_or_der: str | bytes | Path | None) -> str | None:
        """The SHA-256 of a certificate's DER: the leaf's, for a PEM chain.

        Args:
            pem_or_der: A PEM file, PEM text or DER bytes.

        Returns:
            Optional[str]: Lowercase hex, or None when there is no certificate.
        """
        if pem_or_der is None:
            return None
        if isinstance(pem_or_der, Path):
            try:
                pem_or_der = pem_or_der.read_text()
            except OSError:
                return None
        if isinstance(pem_or_der, str):
            if PEM_END not in pem_or_der:
                return None
            try:
                der = ssl.PEM_cert_to_DER_cert(pem_or_der.split(PEM_END)[0] + PEM_END + "\n")
            except ValueError:
                return None
        else:
            der = pem_or_der
        return hashlib.sha256(der).hexdigest()

    @classmethod
    def describe(cls, certificate: Path) -> dict[str, Any] | None:
        """A certificate's issuer and days left.

        Args:
            certificate: A PEM file (the leaf first).

        Returns:
            Optional[Dict[str, Any]]: `issuer` (organisation and common name) and `days`, or None.
        """
        result = cls._run(
            [
                "openssl",
                "x509",
                "-in",
                str(certificate),
                "-noout",
                "-enddate",
                "-issuer",
                "-nameopt",
                "multiline,utf8",
            ],
            timeout=30,
        )
        if result.returncode != 0:
            return None
        fields: dict[str, str] = {}
        end = None
        for line in result.stdout.splitlines():
            key, _, value = line.strip().partition("=")
            if key == NOT_AFTER:
                end = value.strip()
            elif key.strip() in ISSUER_FIELDS:
                fields[key.strip()] = value.strip()
        if end is None:
            return None
        try:
            expires = (
                datetime.datetime.strptime(end.replace(" GMT", ""), "%b %d %H:%M:%S %Y")
                .replace(tzinfo=datetime.UTC)
                .timestamp()
            )
        except ValueError:
            return None
        issuer = " ".join(
            part for part in (fields.get("organizationName"), fields.get("commonName")) if part
        )
        return {"issuer": issuer or "unknown", "days": int((expires - time.time()) // 86400)}

    # -- lego -------------------------------------------------------------------------------------

    @classmethod
    def lego(cls) -> Path | None:
        """The lego binary, downloaded once and checked against the pinned SHA-256.

        Returns:
            Optional[Path]: The binary, or None.
        """
        target = RemoteSettings.path("acme", "bin", f"lego-{LEGO_VERSION}")
        if target.is_file() and os.access(target, os.X_OK):
            return target
        arch = ARCHITECTURES.get(platform.machine().lower())
        if arch is None:
            LOG.error(f"❌ lego is not pinned for {platform.machine()}.")
            return None
        url = LEGO_URL.format(version=LEGO_VERSION, arch=arch)
        data = cls._download(url)
        if data is None:
            return None
        digest = hashlib.sha256(data).hexdigest()
        if digest != LEGO_ARCHIVES[arch]:
            LOG.error(f"❌ {url} has SHA-256 {digest}, not the pinned {LEGO_ARCHIVES[arch]}.")
            return None
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = next(
                (m for m in archive.getmembers() if m.name == LEGO_NAME and m.isfile()), None
            )
            handle = archive.extractfile(member) if member else None
            binary = handle.read() if handle else b""
        if not binary:
            LOG.error(f"❌ {url} holds no `lego` binary.")
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        for folder in (RemoteSettings.folder(), cls.folder(), target.parent):
            folder.chmod(0o700)
        target.write_bytes(binary)
        target.chmod(0o700)
        return target

    @classmethod
    def lego_env(cls) -> dict[str, str]:
        """The environment lego runs in: the token by file, never in the environment itself.

        Returns:
            Dict[str, str]: The environment for lego.
        """
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("CF_", "CLOUDFLARE_", "LEGO_"))
        }
        env["CF_DNS_API_TOKEN_FILE"] = str(Path(CLOUDFLARE_TOKEN_FILE).expanduser())
        env["LEGO_LOG_FORMAT"] = "text"
        return env

    @classmethod
    def lego_argv(
        cls, binary: Path, command: list[str], staging: bool, account_id: str = ACCOUNT_ID
    ) -> list[str]:
        """One lego command line against Let's Encrypt.

        Args:
            binary: lego.
            command: The subcommand and its own options.
            staging: The staging CA.
            account_id: lego's account name.

        Returns:
            List[str]: The argv.
        """
        return [
            str(binary),
            *command,
            "--accept-tos",
            "--server",
            ACME_STAGING if staging else ACME_PRODUCTION,
            "--path",
            str(cls.folder(staging)),
            "--account-id",
            account_id,
            "--key-type",
            KEY_TYPE,
        ]

    @classmethod
    def account_url(cls, staging: bool = False, account_id: str = ACCOUNT_ID) -> str | None:
        """The ACME account's URL, which the CAA record names.

        Args:
            staging: The staging CA's account.
            account_id: lego's account name: the control plane's, or the relay's own.

        Returns:
            Optional[str]: The URL, or None before the account exists.
        """
        host = urllib.parse.urlsplit(ACME_STAGING if staging else ACME_PRODUCTION).hostname or ""
        path = cls.folder(staging) / "accounts" / host / account_id / "account.json"
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        registration = record.get("registration") if isinstance(record, dict) else None
        url = registration.get("accountURL") if isinstance(registration, dict) else None
        return url if isinstance(url, str) and url.startswith("https://") else None

    @classmethod
    def register(cls, binary: Path, staging: bool, account_id: str = ACCOUNT_ID) -> str | None:
        """Registers the ACME account once.

        Args:
            binary: lego.
            staging: The staging CA.
            account_id: lego's account name.

        Returns:
            Optional[str]: The account's URL.
        """
        url = cls.account_url(staging, account_id)
        if url:
            return url
        result = cls._run(
            cls.lego_argv(binary, ["accounts", "register"], staging, account_id),
            env=cls.lego_env(),
        )
        if result.returncode != 0:
            LOG.error(f"❌ lego could not register the ACME account: {cls._tail(result)}")
            return None
        return cls.account_url(staging, account_id)

    @classmethod
    def obtain(cls, dns_name: str, staging: bool = False, account_id: str = ACCOUNT_ID) -> bool:
        """Gets the certificate, or renews it when it is due (lego's `run` is both).

        Args:
            dns_name: The name the certificate is for: the box's, or the relay's own.
            staging: Let's Encrypt's staging CA.
            account_id: lego's account, the only one the name's CAA record then allows.

        Returns:
            bool: True when a current certificate is in lego's folder afterwards.
        """
        if cls.token() is None:
            LOG.error(f"❌ No Cloudflare token in {CLOUDFLARE_TOKEN_FILE} (DNS edit on the zone).")
            return False
        binary = cls.lego()
        if binary is None:
            return False
        if cls.register(binary, staging, account_id) is None:
            return False
        accounts = [
            url
            for url in (cls.account_url(False, account_id), cls.account_url(True, account_id))
            if url
        ]
        if not cls.ensure_caa(dns_name, accounts):
            return False
        command = ["run", "--dns", "cloudflare", "--domains", dns_name]
        command += ["--renew-days", str(PUBLIC_RENEW_DAYS), "--no-random-sleep"]
        result = cls._run(cls.lego_argv(binary, command, staging, account_id), env=cls.lego_env())
        if result.returncode != 0 or not cls.files(dns_name, staging)[0].is_file():
            LOG.error(f"❌ lego could not get the certificate: {cls._tail(result)}")
            return False
        return True

    @classmethod
    def _tail(cls, result: subprocess.CompletedProcess) -> str:
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        return " | ".join(line for line in lines[-3:] if SECRET_WORD not in line.lower())[:600]

    # -- CAA --------------------------------------------------------------------------------------

    @classmethod
    def caa_records(cls, account_urls: list[str]) -> list[dict[str, Any]]:
        """The CAA records the box's name carries.

        Args:
            account_urls: The ACME accounts allowed to issue.

        Returns:
            List[Dict[str, Any]]: Cloudflare's `data` for each record: Let's Encrypt only, DNS-01
            only, these accounts only, and no wildcard.
        """
        records = [
            {
                "flags": 0,
                "tag": "issue",
                "value": f"{CAA_ISSUER}; validationmethods=dns-01; accounturi={url}",
            }
            for url in sorted(set(account_urls))
        ]
        return [*records, {"flags": 0, "tag": "issuewild", "value": ";"}]

    @classmethod
    def zone_id(cls, dns_name: str) -> str | None:
        """The Cloudflare zone that holds a name.

        Args:
            dns_name: A DNS name.

        Returns:
            Optional[str]: The zone's id, or None.
        """
        labels = dns_name.strip(".").lower().split(".")
        for start in range(len(labels) - 1):
            name = ".".join(labels[start:])
            status, body = cls._cloudflare("GET", f"/zones?name={urllib.parse.quote(name)}")
            zones = body.get("result") if status == HTTP_OK else None
            if isinstance(zones, list) and zones:
                return str(zones[0].get("id"))
        return None

    @classmethod
    def ensure_caa(cls, dns_name: str, account_urls: list[str]) -> bool:
        """Makes the box's name carry exactly the CAA records of `caa_records`.

        Args:
            dns_name: The box's public DNS name.
            account_urls: The ACME accounts allowed to issue.

        Returns:
            bool: True when the records are in place.
        """
        zone = cls.zone_id(dns_name)
        if zone is None:
            LOG.error(f"❌ Cloudflare holds no zone for {dns_name} that the token may read.")
            return False
        wanted = cls.caa_records(account_urls)
        query = urllib.parse.urlencode({"type": "CAA", "name": dns_name})
        status, body = cls._cloudflare("GET", f"/zones/{zone}/dns_records?{query}")
        existing = body.get("result") if status == HTTP_OK else None
        if not isinstance(existing, list):
            LOG.error(f"❌ Cloudflare did not list the CAA records of {dns_name} ({status}).")
            return False

        def key(data: dict[str, Any]) -> tuple[int, str, str]:
            return int(data.get("flags", 0)), str(data.get("tag")), str(data.get("value"))

        if sorted(key(r.get("data") or {}) for r in existing) == sorted(map(key, wanted)):
            return True
        for record in existing:
            cls._cloudflare("DELETE", f"/zones/{zone}/dns_records/{record.get('id')}")
        for data in wanted:
            status, body = cls._cloudflare(
                "POST",
                f"/zones/{zone}/dns_records",
                {"type": "CAA", "name": dns_name, "data": data, "ttl": 300},
            )
            if status != HTTP_OK:
                errors = [e.get("message") for e in body.get("errors") or []]
                LOG.error(f"❌ Cloudflare refused a CAA record for {dns_name} ({status}): {errors}")
                return False
        return True

    # -- the control plane ------------------------------------------------------------------------

    @classmethod
    def install(cls, dns_name: str) -> bool:
        """Copies lego's certificate into the control plane's folder and restarts it.

        Args:
            dns_name: The box's public DNS name.

        Returns:
            bool: True when the control plane presents it on loopback afterwards.
        """
        certificate, key = cls.files(dns_name)
        return cls.serve(dns_name, certificate.read_text(), key.read_text())

    @classmethod
    def serve(cls, dns_name: str, certificate: str, key: str) -> bool:
        """Writes a certificate and key for the container, restarts it, and checks what it serves.

        Args:
            dns_name: The box's public DNS name.
            certificate: The certificate with its chain (PEM).
            key: Its key (PEM).

        Returns:
            bool: True when the control plane presents that certificate on loopback.
        """
        RemoteSettings.write_private("server/tls.crt", certificate)
        RemoteSettings.write_private("server/tls.key", key)
        if not RemoteControlPlane.running():
            return True
        if not RemoteControlPlane.restart():
            return False
        expected = cls.fingerprint(certificate)
        deadline = time.monotonic() + RELOAD_WAIT_S
        while time.monotonic() < deadline:
            served = cls._served_der("127.0.0.1", CONTROL_PLANE_PORT, dns_name)
            if served is not None and cls.fingerprint(served) == expected:
                return True
            time.sleep(RELOAD_POLL_S)
        LOG.error("❌ The control plane did not come back with the new certificate.")
        return False

    @classmethod
    def due(cls, dns_name: str) -> bool:
        """Whether the control plane serves something other than lego's current certificate.

        Args:
            dns_name: The box's public DNS name.

        Returns:
            bool: True when lego's certificate and the served one differ.
        """
        return cls.fingerprint(cls.files(dns_name)[0]) != cls.fingerprint(cls.served_files()[0])

    # -- the renewal timer ------------------------------------------------------------------------

    @classmethod
    def timer_units(cls) -> dict[str, str]:
        """The renewal's user units: daily, run in the batch slice, with an alert on failure.

        Returns:
            Dict[str, str]: Unit file name → text.
        """
        header = "# Written by `ling-admin remote certificate issue` (REMOTE_ACCESS §15).\n"
        service = (
            header
            + "[Unit]\nDescription=Mightling remote access: renew the public certificates (control plane and relay)\n"
            + "Wants=network-online.target\nAfter=network-online.target\n"
            + f"OnFailure={CERTIFICATE_ALERT_UNIT}\n\n"
            + "[Service]\nType=oneshot\nSlice=mightling-batch.slice\nNice=10\n"
            + "ExecStart=%h/.local/bin/ling-admin remote certificate renew\n"
        )
        # A failed renewal tells the owner the day it fails (the same Telegram path as the acceptance
        # run), long before a certificate expires.
        alert = (
            header
            + "[Unit]\nDescription=Mightling remote access: tell the owner a certificate renewal failed\n\n"
            + "[Service]\nType=oneshot\nSlice=mightling-batch.slice\nNice=10\n"
            + "ExecStart=%h/.local/bin/owner-notify 'Remote access: the public certificate renewal "
            + "failed. See: journalctl --user -u mightling-remote-certificate.service; "
            + "ling-admin remote status shows the days left.'\n"
        )
        timer = (
            header
            + "[Unit]\nDescription=Mightling remote access: renew the public certificates daily\n\n"
            + "[Timer]\nOnCalendar=*-*-* 04:17:00 UTC\nRandomizedDelaySec=45min\nPersistent=true\n\n"
            + "[Install]\nWantedBy=timers.target\n"
        )
        return {
            CERTIFICATE_SERVICE_UNIT: service,
            CERTIFICATE_ALERT_UNIT: alert,
            CERTIFICATE_TIMER_UNIT: timer,
        }

    @classmethod
    def install_timer(cls) -> bool:
        """Writes the renewal units and enables the timer.

        Returns:
            bool: True when systemd took them.
        """
        folder = RemoteControlPlane.unit_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in cls.timer_units().items():
            (folder / name).write_text(text)
        steps = [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", CERTIFICATE_TIMER_UNIT],
        ]
        return all(cls._run(step, timeout=60).returncode == 0 for step in steps)

    @classmethod
    def remove_timer(cls) -> None:
        """Disables the renewal timer and deletes its units."""
        cls._run(["systemctl", "--user", "disable", "--now", CERTIFICATE_TIMER_UNIT], timeout=60)
        for name in cls.timer_units():
            (RemoteControlPlane.unit_dir() / name).unlink(missing_ok=True)
        cls._run(["systemctl", "--user", "daemon-reload"], timeout=60)

    @classmethod
    def timer_state(cls) -> str:
        """The renewal timer's state.

        Returns:
            str: `systemctl --user is-active`'s word (`active`, `inactive`…).
        """
        result = cls._run(["systemctl", "--user", "is-active", CERTIFICATE_TIMER_UNIT], timeout=30)
        return result.stdout.strip() or "unknown"
