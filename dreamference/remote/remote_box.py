"""
The rented box, deployed and removed over SSH (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §3).

The box is untrusted and does two jobs. NetBird's relay (with its embedded STUN) carries WireGuard
packets between peers that cannot reach each other directly; it terminates TLS with a certificate
from the node's CA and sees only ciphertext. haproxy, the distribution's package and the only one
added, listens on 443 in TCP mode and hands a connection whose TLS server name is the box's own name
to the reverse tunnel's loopback listener, unopened; any other name is refused. Nothing else is
installed: no Docker, no dashboard, no identity provider.

The relay binary comes from the relay image's per-architecture manifest, pinned by digest, and is
checked by SHA-256 on the node after extraction and again on the box before it is installed.

Everything runs through the user's own `ssh <dns-name>` (their key, their configuration), as root or
through passwordless sudo, in two steps: the staged files are copied, then one generated script
installs them and leaves a manifest that `remove` reads. SSH and Docker go through `_run`, the seam
the tests replace.
"""

import hashlib
import io
import shlex
import subprocess
import tarfile
from pathlib import Path
from typing import Dict, List, Optional

from dreamference.remote.remote_settings import (
    BOX_PUBLIC_PORT,
    BOX_RELAY_DIR,
    BOX_TUNNEL_PORT,
    BOX_TUNNEL_USER,
    RELAY_BINARY_IN_IMAGE,
    RELAY_BINARY_SHA256,
    RELAY_IMAGES,
    RELAY_PORT,
    STUN_PORT,
    RemoteSettings,
)

STAGE: str = "/tmp/mightling-remote-stage.tgz"
RELAY_USER: str = "mightling-relay"
RELAY_UNIT: str = "mightling-relay.service"
SSHD_DROP_IN: str = "/etc/ssh/sshd_config.d/mightling-tunnel.conf"
HAPROXY_CONFIG: str = "/etc/haproxy/haproxy.cfg"
# Runs the script on stdin as root: directly when the SSH user is root, through `sudo -n` otherwise.
AS_ROOT: str = 'if [ "$(id -u)" -eq 0 ]; then exec bash -s; else exec sudo -n bash -s; fi'
ARCHITECTURES: Dict[str, str] = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


class RemoteBox:
    """`remote setup`'s and `remote remove`'s work on the box."""

    @classmethod
    def _run(cls, argv: List[str], input_bytes: Optional[bytes] = None, timeout: int = 600) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(argv, capture_output=True, input=input_bytes, timeout=timeout, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(argv, 127, "", str(error))
        return subprocess.CompletedProcess(argv, result.returncode,
                                           result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"))

    @classmethod
    def ssh(cls, dns_name: str, command: str) -> List[str]:
        """
        Args:
            dns_name: The box's public DNS name, as the user's SSH configuration knows it.
            command: The remote command.

        Returns:
            List[str]: The argv: no password prompt, a bounded connection time.
        """
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", dns_name, command]

    # -- preflight --------------------------------------------------------------------------------

    @classmethod
    def probe(cls, dns_name: str) -> Dict[str, str]:
        """
        Asks the box what it is.

        Args:
            dns_name: The box's public DNS name.

        Returns:
            Dict[str, str]: `arch` (amd64 or arm64), `root` (`yes` when root or passwordless sudo
            works), `packages` (`apt`, `dnf` or empty) and `host_key` (the ed25519 host key line);
            `error` instead when SSH failed.
        """
        script = ("printf 'arch=%s\\n' \"$(uname -m)\"; "
                  "if [ \"$(id -u)\" -eq 0 ] || sudo -n true 2>/dev/null; then echo root=yes; else echo root=no; fi; "
                  "if command -v apt-get >/dev/null; then echo packages=apt; elif command -v dnf >/dev/null; then echo packages=dnf; else echo packages=; fi; "
                  "printf 'host_key=%s\\n' \"$(cat /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null)\"")
        result = cls._run(cls.ssh(dns_name, script), timeout=60)
        if result.returncode != 0:
            return {"error": (result.stderr or result.stdout).strip()[:300] or f"ssh exited {result.returncode}"}
        facts: Dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition("=")
            facts[key.strip()] = value.strip()
        facts["arch"] = ARCHITECTURES.get(facts.get("arch", ""), "")
        return facts

    # -- the relay binary -------------------------------------------------------------------------

    @classmethod
    def relay_binary(cls, arch: str) -> Optional[bytes]:
        """
        Takes the relay binary out of the pinned image for the box's architecture and checks it.

        Args:
            arch: `amd64` or `arm64`.

        Returns:
            Optional[bytes]: The binary, or None when it could not be had or its digest is wrong.
        """
        image = RELAY_IMAGES[arch]
        target = RemoteSettings.path("box", f"netbird-relay-{arch}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if cls._run(["docker", "pull", "-q", image]).returncode != 0:
            print(f"❌ Could not pull {image}.")
            return None
        created = cls._run(["docker", "create", image])
        container = created.stdout.strip()
        if created.returncode != 0 or not container:
            print(f"❌ Could not open {image}: {created.stderr.strip()[:300]}")
            return None
        try:
            copied = cls._run(["docker", "cp", f"{container}:{RELAY_BINARY_IN_IMAGE}", str(target)])
        finally:
            cls._run(["docker", "rm", container])
        if copied.returncode != 0:
            print(f"❌ Could not copy the relay out of {image}: {copied.stderr.strip()[:300]}")
            return None
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != RELAY_BINARY_SHA256[arch]:
            print(f"❌ The relay binary's SHA-256 is {digest}, not the pinned {RELAY_BINARY_SHA256[arch]}; nothing is installed.")
            return None
        return data

    # -- what goes to the box ---------------------------------------------------------------------

    @classmethod
    def haproxy_config(cls, dns_name: str) -> str:
        """
        Args:
            dns_name: The box's public DNS name.

        Returns:
            str: haproxy's configuration: TCP on 443, the box's own server name to the tunnel,
            anything else refused.
        """
        return "\n".join([
            "# Written by `ling-admin remote setup` on the Mightling node (REMOTE_ACCESS §3).",
            "# TCP only: TLS ends on the node; this box reads the server name and nothing else.",
            "global",
            "    log /dev/log local0",
            "    maxconn 2000",
            "defaults",
            "    mode tcp",
            "    log global",
            "    option tcplog",
            "    timeout connect 10s",
            "    timeout client 1h",
            "    timeout server 1h",
            "frontend mightling_tls",
            f"    bind :{BOX_PUBLIC_PORT}",
            "    tcp-request inspect-delay 5s",
            "    tcp-request content accept if { req.ssl_hello_type 1 }",
            f"    use_backend node if {{ req.ssl_sni -i {dns_name} }}",
            "    default_backend refuse",
            "backend node",
            f"    server tunnel 127.0.0.1:{BOX_TUNNEL_PORT}",
            "backend refuse",
            "    tcp-request content reject",
            "",
        ])

    @classmethod
    def relay_unit(cls, dns_name: str) -> str:
        """
        Args:
            dns_name: The box's public DNS name.

        Returns:
            str: The relay's system unit.
        """
        return "\n".join([
            "# Written by `ling-admin remote setup` on the Mightling node (REMOTE_ACCESS §3).",
            "[Unit]",
            "Description=Mightling remote access: NetBird relay and STUN",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            f"User={RELAY_USER}",
            f"EnvironmentFile={BOX_RELAY_DIR}/relay.env",
            f"ExecStart={BOX_RELAY_DIR}/netbird-relay --listen-address :{RELAY_PORT}"
            f" --exposed-address {RemoteSettings.relay_address(dns_name)}"
            f" --tls-cert-file {BOX_RELAY_DIR}/relay.crt --tls-key-file {BOX_RELAY_DIR}/relay.key"
            f" --enable-stun --stun-ports {STUN_PORT} --health-listen-address 127.0.0.1:9000",
            "Restart=always",
            "RestartSec=5",
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "PrivateTmp=yes",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ])

    @classmethod
    def sshd_drop_in(cls) -> str:
        """
        Returns:
            str: The SSH server's settings for the tunnel account: remote forwarding only.
        """
        return "\n".join([
            "# Written by `ling-admin remote setup` on the Mightling node (REMOTE_ACCESS §3).",
            f"Match User {BOX_TUNNEL_USER}",
            "    AllowTcpForwarding remote",
            "    GatewayPorts no",
            "    PermitTTY no",
            "    X11Forwarding no",
            "    AllowAgentForwarding no",
            "    PermitTunnel no",
            "",
        ])

    @classmethod
    def install_script(cls, arch: str, packages: str) -> str:
        """
        Args:
            arch: The box's architecture.
            packages: `apt` or `dnf`.

        Returns:
            str: The root script that installs the staged files. It fails at the first error.
        """
        install_haproxy = ("DEBIAN_FRONTEND=noninteractive apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q haproxy"
                           if packages == "apt" else "dnf install -y -q haproxy")
        port_rules = [f"{BOX_PUBLIC_PORT}/tcp", f"{RELAY_PORT}/tcp", f"{RELAY_PORT}/udp", f"{STUN_PORT}/udp"]
        return "\n".join([
            "set -eu",
            "stage=$(mktemp -d)",
            f"tar -xzf {STAGE} -C \"$stage\" && rm -f {STAGE}",
            f"echo '{RELAY_BINARY_SHA256[arch]}  '\"$stage\"/netbird-relay | sha256sum -c --quiet -",
            f"install -d -m 0755 {BOX_RELAY_DIR}",
            f"manifest={BOX_RELAY_DIR}/MANIFEST; : > \"$manifest\"",
            "if command -v haproxy >/dev/null; then echo 'haproxy=present' >> \"$manifest\"; else",
            f"  {install_haproxy}",
            "  echo 'haproxy=installed' >> \"$manifest\"",
            "fi",
            f"[ -f {HAPROXY_CONFIG}.mightling-orig ] || cp {HAPROXY_CONFIG} {HAPROXY_CONFIG}.mightling-orig 2>/dev/null || true",
            f"install -m 0644 \"$stage\"/haproxy.cfg {HAPROXY_CONFIG}",
            "haproxy -c -q -f /etc/haproxy/haproxy.cfg",
            f"id -u {RELAY_USER} >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin {RELAY_USER}",
            f"install -m 0755 \"$stage\"/netbird-relay {BOX_RELAY_DIR}/netbird-relay",
            f"install -m 0644 -o {RELAY_USER} \"$stage\"/relay.crt {BOX_RELAY_DIR}/relay.crt",
            f"install -m 0600 -o {RELAY_USER} \"$stage\"/relay.key {BOX_RELAY_DIR}/relay.key",
            f"install -m 0600 -o root \"$stage\"/relay.env {BOX_RELAY_DIR}/relay.env",
            f"install -m 0644 \"$stage\"/{RELAY_UNIT} /etc/systemd/system/{RELAY_UNIT}",
            f"id -u {BOX_TUNNEL_USER} >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/{BOX_TUNNEL_USER} --shell /usr/sbin/nologin {BOX_TUNNEL_USER}",
            f"install -d -m 0700 -o {BOX_TUNNEL_USER} /var/lib/{BOX_TUNNEL_USER}/.ssh",
            f"install -m 0600 -o {BOX_TUNNEL_USER} \"$stage\"/authorized_keys /var/lib/{BOX_TUNNEL_USER}/.ssh/authorized_keys",
            # The drop-in's Match block must change nothing for anyone else: the effective settings
            # for root are compared before and after, and a difference undoes it.
            "global_before=$(sshd -T -C user=root,host=localhost,addr=127.0.0.1 2>/dev/null | sort)",
            f"install -m 0644 \"$stage\"/sshd-tunnel.conf {SSHD_DROP_IN}",
            "sshd -t",
            "global_after=$(sshd -T -C user=root,host=localhost,addr=127.0.0.1 2>/dev/null | sort)",
            f"if [ \"$global_before\" != \"$global_after\" ]; then rm -f {SSHD_DROP_IN}; "
            "echo 'the sshd drop-in changed settings beyond the tunnel account; undone' >&2; exit 1; fi",
            "systemctl reload ssh 2>/dev/null || systemctl reload sshd",
            "systemctl daemon-reload",
            f"systemctl enable --now {RELAY_UNIT}",
            f"systemctl restart {RELAY_UNIT}",
            "systemctl enable haproxy",
            "systemctl restart haproxy",
            "if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then",
            *[f"  ufw allow {rule} >/dev/null" for rule in port_rules],
            "  echo 'firewall=ufw' >> \"$manifest\"",
            "elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then",
            *[f"  firewall-cmd --quiet --permanent --add-port={rule}" for rule in port_rules],
            "  firewall-cmd --quiet --reload",
            "  echo 'firewall=firewalld' >> \"$manifest\"",
            "fi",
            "rm -rf \"$stage\"",
            "echo mightling-box-ready",
            "",
        ])

    @classmethod
    def remove_script(cls) -> str:
        """
        Returns:
            str: The root script that undoes `install_script`, reading its manifest. It carries on
            past a missing piece, so a half-installed box is cleaned too.
        """
        port_rules = [f"{BOX_PUBLIC_PORT}/tcp", f"{RELAY_PORT}/tcp", f"{RELAY_PORT}/udp", f"{STUN_PORT}/udp"]
        return "\n".join([
            "set -u",
            f"manifest={BOX_RELAY_DIR}/MANIFEST",
            "has() { grep -qx \"$1\" \"$manifest\" 2>/dev/null; }",
            f"systemctl disable --now {RELAY_UNIT} 2>/dev/null",
            f"rm -f /etc/systemd/system/{RELAY_UNIT}",
            "systemctl daemon-reload",
            f"if [ -f {HAPROXY_CONFIG}.mightling-orig ]; then mv -f {HAPROXY_CONFIG}.mightling-orig {HAPROXY_CONFIG}; fi",
            "if has haproxy=installed; then",
            "  systemctl disable --now haproxy 2>/dev/null",
            "  if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get purge -y -q haproxy >/dev/null; else dnf remove -y -q haproxy >/dev/null; fi",
            "else systemctl restart haproxy 2>/dev/null; fi",
            "if has firewall=ufw; then",
            *[f"  ufw delete allow {rule} >/dev/null 2>&1" for rule in port_rules],
            "elif has firewall=firewalld; then",
            *[f"  firewall-cmd --quiet --permanent --remove-port={rule}" for rule in port_rules],
            "  firewall-cmd --quiet --reload",
            "fi",
            f"rm -f {SSHD_DROP_IN}",
            "sshd -t && { systemctl reload ssh 2>/dev/null || systemctl reload sshd; }",
            f"pkill -u {BOX_TUNNEL_USER} 2>/dev/null; userdel -r {BOX_TUNNEL_USER} 2>/dev/null",
            f"userdel {RELAY_USER} 2>/dev/null",
            f"rm -rf {BOX_RELAY_DIR}",
            "echo mightling-box-removed",
            "",
        ])

    @classmethod
    def stage(cls, files: Dict[str, bytes]) -> bytes:
        """
        Args:
            files: Name to content.

        Returns:
            bytes: A gzipped tar of the files, every one mode 0600 (the script sets the real modes).
        """
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o600
                archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    # -- the two operations -----------------------------------------------------------------------

    @classmethod
    def deploy(cls, dns_name: str, arch: str, packages: str, secret: str, relay_certificate: Path,
               relay_key: Path, tunnel_public_key: str) -> bool:
        """
        Installs the relay, haproxy and the tunnel account on the box.

        Args:
            dns_name: The box's public DNS name.
            arch: Its architecture.
            packages: Its package manager.
            secret: The relay's shared secret.
            relay_certificate: The relay's certificate, from the node's CA.
            relay_key: Its key.
            tunnel_public_key: The node's tunnel key.

        Returns:
            bool: True when the box reported itself ready.
        """
        from dreamference.remote.remote_tunnel import RemoteTunnel
        binary = cls.relay_binary(arch)
        if binary is None:
            return False
        archive = cls.stage({
            "netbird-relay": binary,
            "relay.crt": relay_certificate.read_bytes(),
            "relay.key": relay_key.read_bytes(),
            "relay.env": f"NB_AUTH_SECRET={secret}\n".encode(),
            "haproxy.cfg": cls.haproxy_config(dns_name).encode(),
            RELAY_UNIT: cls.relay_unit(dns_name).encode(),
            "authorized_keys": (RemoteTunnel.authorized_line(tunnel_public_key) + "\n").encode(),
            "sshd-tunnel.conf": cls.sshd_drop_in().encode(),
        })
        copied = cls._run(cls.ssh(dns_name, f"umask 077; cat > {shlex.quote(STAGE)}"), input_bytes=archive)
        if copied.returncode != 0:
            print(f"❌ Could not copy the files to {dns_name}: {copied.stderr.strip()[:300]}")
            return False
        installed = cls._run(cls.ssh(dns_name, AS_ROOT), input_bytes=cls.install_script(arch, packages).encode())
        if installed.returncode != 0 or "mightling-box-ready" not in installed.stdout:
            print(f"❌ Installing on {dns_name} failed:\n{(installed.stderr or installed.stdout).strip()[-800:]}")
            return False
        return True

    @classmethod
    def remove(cls, dns_name: str) -> bool:
        """
        Removes everything `deploy` installed.

        Args:
            dns_name: The box's public DNS name.

        Returns:
            bool: True when the box reported itself clean.
        """
        result = cls._run(cls.ssh(dns_name, AS_ROOT), input_bytes=cls.remove_script().encode())
        if result.returncode != 0 or "mightling-box-removed" not in result.stdout:
            print(f"⚠️  Removing from {dns_name} did not finish:\n{(result.stderr or result.stdout).strip()[-600:]}")
            return False
        return True

    @classmethod
    def unit_states(cls, dns_name: str) -> Optional[Dict[str, str]]:
        """
        Args:
            dns_name: The box's public DNS name.

        Returns:
            Optional[Dict[str, str]]: haproxy's and the relay's states, or None when SSH failed.
        """
        result = cls._run(cls.ssh(dns_name, f"systemctl is-active haproxy {RELAY_UNIT}"), timeout=60)
        lines = result.stdout.split()
        if len(lines) != 2:
            return None
        return {"haproxy": lines[0], "relay": lines[1]}
