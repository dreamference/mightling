"""The root helper on a role node: host setup's automated fixes, and the only thing sudo allows.

`ling-admin node provision --role` used to give the node's user passwordless `systemctl`,
`apt-get`, `apt` and `docker` with any arguments, and `ling-admin host setup|node prepare` from
paths that user can write: root to anyone with the user's shell, which a paired client that
breaks out of its forced command would have (finding A4 of the node security read, 2026-10-29).

Now the drop-in names one file, `HELPER_PATH`, a shell script installed root:root 0755 in a
root-owned directory. It takes a fixed set of verbs, the root commands `ling-admin host setup`
runs (`HostSafetySetup` hands each step's verb to it when sudo allows nothing else), checks every
argument before it asks for root, and runs only system programs on a fixed `PATH`. The values it
writes are rendered in from the same constants the checks read, so it cannot disagree with them;
a role run that finds the installed copy different puts the rendered one back.
"""

import re
import shlex
from typing import Final

HELPER_PATH: Final[str] = "/usr/local/sbin/mightling-node-root"
# Each verb is one step of `HostSafetySetup`; `sysctl` takes one of the two names it raises.
VERBS: Final[tuple[str, ...]] = (
    "probe",
    "sysstat",
    "earlyoom",
    "swap",
    "sysctl",
    "docker-group",
    "bubblewrap",
    "bwrap-profile",
)
# How the usage line shows a verb that takes an argument.
VERB_USAGE: Final[dict[str, str]] = {"sysctl": "sysctl NAME"}
# The bubblewrap the profile attaches to: where Ubuntu installs it. A machine whose `bwrap` is
# elsewhere is refused, and host setup's own commands, run from a terminal, still fix it.
BWRAP: Final[str] = "/usr/bin/bwrap"
PROFILE_END: Final[str] = "MIGHTLING_PROFILE_END"

TEMPLATE: Final[str] = r"""#!/bin/sh
# mightling-node-root: the root commands `ling-admin host setup` needs on a Mightling node, and
# nothing else. Installed root:root 0755 by `ling-admin node provision --role`, whose sudoers
# drop-in lets the node's user run this file and no other command without a password.
# Rendered by dreamference/vllm_server/host_root_helper.py; the next role run puts it back.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 022
LC_ALL=C
DEBIAN_FRONTEND=noninteractive
export LC_ALL DEBIAN_FRONTEND

usage() {
    echo "usage: mightling-node-root @VERBS@" >&2
    exit 2
}
die() {
    echo "mightling-node-root: $*" >&2
    exit 1
}

[ $# -ge 1 ] || usage
verb=$1
shift
case "$verb" in
    probe | sysstat | earlyoom | swap | docker-group | bubblewrap | bwrap-profile)
        [ $# -eq 0 ] || usage
        ;;
    sysctl)
        [ $# -eq 1 ] || usage
        case "$1" in
@SYSCTL_CASES@
            *) usage ;;
        esac
        ;;
    *) usage ;;
esac
[ "$(id -u)" = 0 ] || die "must run as root (through sudo)"

case "$verb" in
    probe)
        echo ok
        ;;
    sysstat)
        apt-get install -y sysstat
        sed -i 's/^ENABLED=.*/ENABLED="true"/' /etc/default/sysstat
        systemctl enable --now sysstat
        ;;
    earlyoom)
        command -v earlyoom >/dev/null || apt-get install -y earlyoom
        printf '%s\n' @EARLYOOM_LINE@ > @EARLYOOM_DEFAULTS@
        systemctl enable earlyoom
        systemctl restart earlyoom
        ;;
    swap)
        f=@SWAP_FILE@
        [ ! -L "$f" ] || die "$f is a symbolic link"
        have_kb=$(df -Pk / | awk 'NR == 2 { print $4 }')
        [ -e "$f" ] && have_kb=$((have_kb + $(du -k --apparent-size "$f" | cut -f1)))
        [ "$have_kb" -ge @SWAP_NEED_KB@ ] || die "not enough free disk for a @SWAP_SIZE@ swap file"
        if awk -v f="$f" '$1 == f { found = 1 } END { exit !found }' /proc/swaps; then
            used_kb=$(awk -v f="$f" '$1 == f { print $4 }' /proc/swaps)
            free_kb=$(awk '$1 == "MemAvailable:" { print $2 }' /proc/meminfo)
            [ "$used_kb" -le $((free_kb - 4194304)) ] || die "what is swapped out would not fit back into memory"
            swapoff "$f"
        elif [ -e "$f" ]; then
            grep -Eq "^$f[[:space:]]+[^[:space:]]+[[:space:]]+swap([[:space:]]|\$)" @FSTAB@ \
                || [ "$(blkid -p -o value -s TYPE "$f" || true)" = swap ] \
                || die "$f exists but is not a swap file"
        fi
        fallocate -l @SWAP_SIZE@ "$f"
        chmod 600 "$f"
        mkswap "$f"
        swapon "$f"
        grep -q "^$f[[:space:]]" @FSTAB@ || echo "$f none swap sw 0 0" >> @FSTAB@
        ;;
    sysctl)
        name=$1
        case "$name" in
@SYSCTL_VALUES@
        esac
        [ "$(sysctl -n "$name")" -ge "$wanted" ] && exit 0
        line="$name=$wanted"
        sysctl -w "$line"
        grep -qx "$line" @SYSCTL_FILE@ 2>/dev/null || echo "$line" >> @SYSCTL_FILE@
        ;;
    docker-group)
        user=${SUDO_USER:-}
        case "$user" in
            "" | root | [!a-z_]* | *[!a-z0-9_-]*) die "run it through sudo as the node's user" ;;
        esac
        id "$user" >/dev/null
        usermod -aG docker "$user"
        ;;
    bubblewrap)
        apt-get install -y bubblewrap
        ;;
    bwrap-profile)
        [ "$(readlink -f "$(command -v bwrap)")" = @BWRAP@ ] || die "bwrap is not @BWRAP@"
        for other in @APPARMOR_DIR@/*; do
            [ "$other" = @PROFILE_PATH@ ] && continue
            [ -f "$other" ] || continue
            attaches='^[[:space:]]*profile[[:space:]]+[^[:space:]]+[[:space:]]+"?@BWRAP@"?([[:space:]]|$)'
            if grep -Eq "$attaches" "$other"; then
                die "$other already attaches to @BWRAP@; run ling-admin host setup in a terminal"
            fi
        done
        staged=$(mktemp)
        cat > "$staged" <<'@PROFILE_END@'
@PROFILE@@PROFILE_END@
        install -m 644 -o root -g root "$staged" @PROFILE_PATH@
        rm -f "$staged"
        apparmor_parser -r @PROFILE_PATH@
        ;;
esac
"""


class HostRootHelper:
    """Renders the root helper a role node's sudoers allows."""

    @classmethod
    def render(cls) -> str:
        """Return the helper script, with host setup's values written in.

        Returns:
            str: The file installed at `HELPER_PATH`.
        """
        from dreamference.vllm_server.host_safety_setup import (
            EARLYOOM_DEFAULTS,
            FSTAB,
            SWAP_DISK_MARGIN_GB,
            SWAP_FILE,
            SYSCTL_FILE,
        )
        from dreamference.vllm_server.sandbox_prerequisite import (
            APPARMOR_DIR,
            PROFILE_PATH,
            SandboxPrerequisite,
        )
        from dreamference.vllm_server.vllm_server_manager import (
            EARLYOOM_ARGS,
            MIN_FREE_KBYTES,
            MIN_SWAP_GB,
            WATERMARK_SCALE_FACTOR,
        )

        sysctls = {
            "vm.min_free_kbytes": MIN_FREE_KBYTES,
            "vm.watermark_scale_factor": WATERMARK_SCALE_FACTOR,
        }
        values = {
            "VERBS": "|".join(VERB_USAGE.get(verb, verb) for verb in VERBS),
            "SYSCTL_CASES": "\n".join(f"            {name}) ;;" for name in sysctls),
            "SYSCTL_VALUES": "\n".join(
                f"            {name}) wanted={int(value)} ;;" for name, value in sysctls.items()
            ),
            "EARLYOOM_LINE": shlex.quote(f'EARLYOOM_ARGS="{EARLYOOM_ARGS}"'),
            "EARLYOOM_DEFAULTS": shlex.quote(EARLYOOM_DEFAULTS),
            "SWAP_FILE": shlex.quote(SWAP_FILE),
            "SWAP_SIZE": f"{MIN_SWAP_GB:.0f}G",
            "SWAP_NEED_KB": str(int((MIN_SWAP_GB + SWAP_DISK_MARGIN_GB) * 1024 * 1024)),
            "FSTAB": shlex.quote(FSTAB),
            "SYSCTL_FILE": shlex.quote(SYSCTL_FILE),
            "BWRAP": BWRAP,
            "APPARMOR_DIR": shlex.quote(APPARMOR_DIR),
            "PROFILE_PATH": shlex.quote(PROFILE_PATH),
            "PROFILE": SandboxPrerequisite.profile_text(BWRAP),
            "PROFILE_END": PROFILE_END,
        }
        text = TEMPLATE
        for key, value in values.items():
            text = text.replace(f"@{key}@", value)
        if PROFILE_END in values["PROFILE"] or re.search(r"@[A-Z_]+@", text):
            raise ValueError("the helper's template was not filled")
        return text
