"""The relay's own public certificate (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §15.5).

NetBird's relay client verifies the relay against the system store, so an Android phone has no
relayed path while the relay presents the node CA's certificate. A public certificate for the relay
has to live on the box, which the control plane's must not, so the relay gets a name of its own
(`relay.<the box's parent domain>`) and a certificate of its own:

- **A separate name.** The box's key then vouches for `relay.…` only. The control plane's certificate
  and key never leave the node, and the CAA record on the box's name (§15.2) is untouched.
- **A separate ACME account.** The relay's name carries its own CAA record, which allows that account
  only (and only by DNS-01, no wildcard), so no other public CA can issue for it either; the control
  plane's account is not in it, and the relay's account is not in the control plane's.
- **Issued on the node**, by the same pinned lego and the same Cloudflare token (passed by file, never
  copied to the box). Only the relay's certificate and key are pushed to the box, over the user's SSH,
  and only the relay unit is restarted.

The name has A and AAAA records copied from the box's own, unproxied (the relay speaks UDP too).
Management then names `rels://<the relay's name>:33080`; the peers learn it from management's next
sync and need no re-enrolment.

Cloudflare's API, lego and the box go through the seams of `RemotePublicCertificate` and `RemoteBox`.
"""

import logging
import re
import urllib.parse
from pathlib import Path
from typing import Any, Final

from dreamference.remote.remote_box import VERIFIED, RemoteBox
from dreamference.remote.remote_certificate_authority import RemoteCertificateAuthority
from dreamference.remote.remote_control_plane import RemoteControlPlane
from dreamference.remote.remote_public_certificate import RemotePublicCertificate
from dreamference.remote.remote_settings import (
    RELAY_ACCOUNT_ID,
    RELAY_PORT,
    RemoteSettings,
)

LOG: Final[logging.Logger] = logging.getLogger(__name__)

HTTP_OK: Final[int] = 200
ADDRESS_TYPES: Final[tuple[str, str]] = ("A", "AAAA")
DNS_TTL: Final[int] = 300
# The keys of remote.json this feature owns: the relay's name, and the fingerprint of the leaf last
# pushed to the box (so a renewal pushes only a new certificate).
RECORD_KEYS: Final[tuple[str, ...]] = ("relay_name", "relay_pushed")

# A DNS label as hostnames use it: ASCII letters, digits and hyphens, never a hyphen at either end
# (a leading one would read as an option where the name becomes an argument), 63 characters at most.
DNS_LABEL: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
MAX_NAME: Final[int] = 253


class RemoteRelayCertificate:
    """The relay's name, its DNS records, its certificate, and putting it on the box."""

    @classmethod
    def valid_name(cls, relay_name: str, dns_name: str) -> bool:
        """Whether a name can be the relay's.

        Args:
            relay_name: The proposed name.
            dns_name: The box's public DNS name.

        Returns:
            bool: True for a DNS name with a parent domain that is not the control plane's.
        """
        labels = relay_name.split(".")
        return (
            relay_name != dns_name
            and len(relay_name) <= MAX_NAME
            and len(labels) >= 3  # noqa: PLR2004 - a label and a registrable domain
            and all(DNS_LABEL.fullmatch(label) for label in labels)
        )

    @classmethod
    def box_files(cls, dns_name: str, node_ca_files: tuple[Path, Path]) -> tuple[Path, Path]:
        """The certificate and key `setup` sends to the box.

        Args:
            dns_name: The box's public DNS name.
            node_ca_files: The node CA's relay certificate and key.

        Returns:
            Tuple[Path, Path]: The relay's public certificate and key once it has them, else the
            node CA's, so a `setup` rerun never puts the node CA's certificate back.
        """
        name = RemoteSettings.relay_name(dns_name)
        if name == dns_name:
            return node_ca_files
        public = RemotePublicCertificate.files(name)
        return public if all(path.is_file() for path in public) else node_ca_files

    # -- DNS --------------------------------------------------------------------------------------

    @classmethod
    def ensure_addresses(cls, relay_name: str, dns_name: str) -> bool:
        """Gives the relay's name the box's own A and AAAA records, unproxied.

        Args:
            relay_name: The relay's name.
            dns_name: The box's public DNS name, whose addresses are copied.

        Returns:
            bool: True when the records are in place.
        """
        zone = RemotePublicCertificate.zone_id(relay_name)
        if zone is None:
            LOG.error(f"❌ Cloudflare holds no zone for {relay_name} that the token may read.")
            return False
        for kind in ADDRESS_TYPES:
            source = cls._records(zone, dns_name, kind)
            current = cls._records(zone, relay_name, kind)
            if source is None or current is None:
                return False
            wanted = sorted(str(record.get("content")) for record in source)
            if sorted(str(record.get("content")) for record in current) == wanted and not any(
                record.get("proxied") for record in current
            ):
                continue
            for record in current:
                RemotePublicCertificate._cloudflare(
                    "DELETE", f"/zones/{zone}/dns_records/{record.get('id')}"
                )
            for content in wanted:
                status, body = RemotePublicCertificate._cloudflare(
                    "POST",
                    f"/zones/{zone}/dns_records",
                    {
                        "type": kind,
                        "name": relay_name,
                        "content": content,
                        "ttl": DNS_TTL,
                        "proxied": False,
                    },
                )
                if status != HTTP_OK:
                    errors = [e.get("message") for e in body.get("errors") or []]
                    LOG.error(f"❌ Cloudflare refused a {kind} record for {relay_name}: {errors}")
                    return False
        return True

    @classmethod
    def _records(cls, zone: str, name: str, kind: str) -> list[dict[str, Any]] | None:
        query = urllib.parse.urlencode({"type": kind, "name": name})
        status, body = RemotePublicCertificate._cloudflare(
            "GET", f"/zones/{zone}/dns_records?{query}"
        )
        result = body.get("result") if status == HTTP_OK else None
        if not isinstance(result, list):
            LOG.error(f"❌ Cloudflare did not list the {kind} records of {name} ({status}).")
            return None
        return result

    # -- the certificate --------------------------------------------------------------------------

    @classmethod
    def obtain(cls, relay_name: str, staging: bool = False) -> bool:
        """Gets the relay's certificate, or renews it when due: its own account, its own CAA record.

        Args:
            relay_name: The relay's name.
            staging: Let's Encrypt's staging CA, for a test.

        Returns:
            bool: True when a current certificate is in lego's folder afterwards.
        """
        return RemotePublicCertificate.obtain(
            relay_name, staging=staging, account_id=RELAY_ACCOUNT_ID
        )

    @classmethod
    def pushed(cls, record: dict[str, Any], relay_name: str) -> bool:
        """Whether the box already has lego's current relay certificate.

        Args:
            record: `remote.json`.
            relay_name: The relay's name.

        Returns:
            bool: True when the recorded fingerprint is the current leaf's.
        """
        current = RemotePublicCertificate.fingerprint(RemotePublicCertificate.files(relay_name)[0])
        return current is not None and record.get("relay_pushed") == current

    @classmethod
    def push(cls, record: dict[str, Any], relay_name: str) -> bool:
        """Puts lego's relay certificate and key on the box, restarts the relay and checks it.

        Writes `remote.json` with the relay's name first (the relay's unit is rendered from it) and
        puts the old record back when the box does not present the new leaf afterwards.

        Args:
            record: `remote.json` as it is now.
            relay_name: The relay's name.

        Returns:
            bool: True when the relay presents the new certificate on its public port.
        """
        dns_name = record["dns_name"]
        certificate, key = RemotePublicCertificate.files(relay_name)
        expected = RemotePublicCertificate.fingerprint(certificate)
        updated = {**record, "relay_name": relay_name}
        RemoteSettings.write(updated)
        if (
            not RemoteBox.update_relay(
                dns_name, certificate.read_text(), key.read_text(), record.get("ssh")
            )
            or RemoteBox._handshake(relay_name, RELAY_PORT, relay_name, None, expected=expected)
            != VERIFIED
        ):
            LOG.error(
                f"❌ The relay on the box does not present the new certificate for {relay_name}."
            )
            RemoteSettings.write(record)
            return False
        RemoteSettings.write({**updated, "relay_pushed": expected})
        return True

    @classmethod
    def switch(cls, record: dict[str, Any], relay_name: str) -> bool:
        """Moves the relay to its own name: the box first, then management's relay address.

        Args:
            record: `remote.json`.
            relay_name: The relay's name; its certificate is in lego's folder.

        Returns:
            bool: True when the relay presents the certificate and the control plane, restarted, names
            the relay's new address. On a failure after the box changed, the node CA's certificate and
            the old address are put back.
        """
        if not cls.push(record, relay_name):
            cls.off(record)
            return False
        if cls._point_management(record["dns_name"]):
            return True
        cls.off(RemoteSettings.read() or record)
        return False

    @classmethod
    def _point_management(cls, dns_name: str) -> bool:
        if not RemoteControlPlane.rewrite_config(dns_name):
            LOG.error(
                "❌ The relay's secret or the store key is missing; management was not changed."
            )
            return False
        crt, key = RemotePublicCertificate.served_files()
        if not crt.is_file() or not key.is_file():
            return False
        return RemotePublicCertificate.serve(dns_name, crt.read_text(), key.read_text())

    @classmethod
    def off(cls, record: dict[str, Any]) -> bool:
        """Puts the node CA's relay certificate and the box's name back.

        Args:
            record: `remote.json`.

        Returns:
            bool: True when the relay and management are back on the box's name.
        """
        dns_name = record["dns_name"]
        node_ca = RemoteCertificateAuthority.issue("relay", [dns_name])
        if node_ca is None:
            return False
        restored = {k: v for k, v in record.items() if k not in RECORD_KEYS}
        RemoteSettings.write(restored)
        if not RemoteBox.update_relay(
            dns_name, node_ca[0].read_text(), node_ca[1].read_text(), record.get("ssh")
        ):
            RemoteSettings.write(record)
            return False
        return cls._point_management(dns_name)

    @classmethod
    def renew(cls, record: dict[str, Any]) -> bool:
        """The daily step: renews the relay's certificate when due and pushes a new one to the box.

        Args:
            record: `remote.json`, with `relay_name` set.

        Returns:
            bool: True when the box holds lego's current certificate afterwards. Only the relay
            restarts, and only for a new certificate.
        """
        relay_name = RemoteSettings.relay_name(record["dns_name"])
        if not cls.obtain(relay_name):
            return False
        if cls.pushed(record, relay_name):
            return True
        return cls.push(record, relay_name)
