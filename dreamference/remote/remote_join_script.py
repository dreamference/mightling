"""The one-line join for a machine that has no `ling` yet (spec §4.5).

A new machine at another site cannot take the LAN code (`ling node remote join`): it is not on the
node's LAN and has no launcher. `ling-admin remote code --join` instead writes **one line** that the
owner receives over an end-to-end encrypted channel (Signal's Note to Self) and pastes into a
terminal there. The line carries a small bash script, base64-encoded, with the bundle's values in
it: the management URL, a single-use setup key and, only while the control plane has no public
certificate, the node's CA. The script, run as root, installs NetBird's client from the pinned
release archive (SHA-256 checked), makes sure `sshd` is there, joins the overlay with the key read
from a 0600 file, and adds an `ssh` alias for the node.

The key is never in a URL (the script downloads only NetBird's release archive) and never in a log:
the script does not trace itself, `netbird up` gets `--setup-key-file`, and the script reaches
`sudo bash -s` on its standard input, never as an argument (sudo logs a command's arguments, and any
local user reads a running one's in `/proc/<pid>/cmdline`). It does appear on the line itself, as
part of the script, so the line is secret until its key is spent or expires.
"""

import base64
import re
import shlex
from typing import Any, Final

from dreamference.remote.remote_settings import CLIENT_ARCHIVES, CLIENT_URL, NETBIRD_VERSION

ALIAS: Final[str] = "gx10-remote"

SCRIPT: Final[str] = r"""set -eu
umask 077
if [ "$(id -u)" != 0 ]; then echo "Run this line with sudo."; exit 1; fi
MGMT=@MGMT@
KEY=@KEY@
VERSION=@VERSION@
PEER=@PEER@
NODE_NAME=@NODE_NAME@
SSH_ALIAS=@ALIAS@
SSH_USER=@SSH_USER@
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64; SHA=@SHA_AMD64@ ;;
  aarch64|arm64) ARCH=arm64; SHA=@SHA_ARM64@ ;;
  *) echo "Unsupported machine: $(uname -m)"; exit 1 ;;
esac
[ -n "$PEER" ] || PEER="$(hostname -s | tr 'A-Z_' 'a-z-')"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
printf '%s\n' "$KEY" > "$work/key"
@CA@
need() { command -v "$1" >/dev/null 2>&1; }
if ! need curl || ! need sshd; then
  export DEBIAN_FRONTEND=noninteractive
  echo "Installing curl and the SSH server..."
  apt-get update -qq >/dev/null && apt-get install -y -qq curl openssh-server >/dev/null
fi
if ! need netbird || [ "$(netbird version 2>/dev/null | head -n1)" != "$VERSION" ]; then
  url="@URL@"
  url="${url//@ARCH@/$ARCH}"
  curl -fsSL --retry 5 -o "$work/netbird.tgz" "$url"
  if ! echo "$SHA  $work/netbird.tgz" | sha256sum -c - >/dev/null; then
    echo "NetBird's archive failed its checksum; nothing installed."; exit 1
  fi
  tar -xzf "$work/netbird.tgz" -C "$work" netbird
  install -m 0755 "$work/netbird" /usr/local/bin/netbird
  ca_changed=1
fi
systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1 || true
netbird service install >/dev/null 2>&1 || true
if [ "${ca_changed:-0}" = 1 ]; then netbird service stop >/dev/null 2>&1 || true; fi
netbird service start >/dev/null 2>&1 || true
for _ in $(seq 30); do netbird status >/dev/null 2>&1 && break; sleep 0.5; done
netbird down >/dev/null 2>&1 || true
netbird up --management-url "$MGMT" --setup-key-file "$work/key" --hostname "$PEER"
for _ in $(seq 40); do
  netbird status 2>/dev/null | grep -q 'Management: Connected' && break
  sleep 1
done
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
  home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  cfg="$home/.ssh/config"
  mkdir -p "$home/.ssh"
  touch "$cfg"
  if ! grep -q "^Host $SSH_ALIAS\$" "$cfg"; then
    {
      printf '\nHost %s\n  HostName %s\n' "$SSH_ALIAS" "$NODE_NAME"
      [ -n "$SSH_USER" ] && printf '  User %s\n' "$SSH_USER"
    } >> "$cfg"
  fi
  chown -R "$SUDO_USER": "$home/.ssh"
  chmod 700 "$home/.ssh"; chmod 600 "$cfg"
fi
echo
netbird status 2>/dev/null | grep -E 'Management|Signal|NetBird IP|FQDN' | grep -v '^$' || true
echo "Joined the overlay as $PEER. Tell the coordinator."
"""


class RemoteJoinScript:
    """Builds the join script and the one line that carries it."""

    @classmethod
    def script(cls, bundle: dict[str, Any], peer: str = "", ssh_user: str = "") -> str:
        """The join script for one bundle.

        Args:
            bundle: A bundle from `RemoteAccess.bundle` (its `ca` only when the control plane has
                no public certificate).
            peer: The new machine's name in the overlay, one DNS label; empty to use its host name.
            ssh_user: The account to name in the node's `ssh` alias; empty names none.

        Returns:
            str: The bash script, secret as long as its key is live.
        """
        ca = bundle.get("ca") or ""
        ca_block = (
            "\n".join(
                [
                    "# The node's own CA (the control plane has no public certificate).",
                    f"printf '%s\\n' {shlex.quote(ca)} > \"$work/ca.crt\"",
                    "store=/usr/local/share/ca-certificates/mightling-node-ca.crt",
                    'if ! cmp -s "$work/ca.crt" "$store"; then',
                    '  install -m 0644 "$work/ca.crt" "$store"',
                    "  update-ca-certificates >/dev/null; ca_changed=1",
                    "fi",
                ]
            )
            if ca
            else ":"
        )
        values = {
            "@MGMT@": shlex.quote(bundle["management_url"]),
            "@KEY@": shlex.quote(bundle["setup_key"]),
            "@VERSION@": shlex.quote(bundle.get("netbird_version") or NETBIRD_VERSION),
            "@PEER@": shlex.quote(peer),
            "@NODE_NAME@": shlex.quote(bundle["overlay_name"]),
            "@ALIAS@": shlex.quote(ALIAS),
            "@SSH_USER@": shlex.quote(ssh_user),
            "@SHA_AMD64@": shlex.quote(CLIENT_ARCHIVES["amd64"]),
            "@SHA_ARM64@": shlex.quote(CLIENT_ARCHIVES["arm64"]),
            "@URL@": CLIENT_URL.format(version=NETBIRD_VERSION, arch="@ARCH@"),
        }
        values["@CA@"] = ca_block
        # One pass, so that a value which holds another placeholder's text (a setup key, a peer
        # name) is not itself substituted into, which would end its quotes early.
        pattern = re.compile("|".join(re.escape(placeholder) for placeholder in values))
        return pattern.sub(lambda found: values[found.group(0)], SCRIPT)

    @classmethod
    def one_line(cls, script: str) -> str:
        """The single command that runs a script without a file, a URL or `ling`.

        Args:
            script: The join script.

        Returns:
            str: A line starting with a space (so a shell that skips such lines keeps it out of its
            history) that decodes the script and pipes it into `sudo bash -s`: `printf` is the
            shell's builtin and the script travels on a pipe, so the key is in no process's
            arguments and not in sudo's log. The script is one function called on its last line,
            so bash has read all of it before any command runs and none can read the rest as input.
        """
        wrapped = f"join_main() {{\n{script.rstrip()}\n}}\njoin_main\n"
        encoded = base64.b64encode(wrapped.encode()).decode()
        return f" printf %s '{encoded}' | base64 -d | sudo bash -s"
