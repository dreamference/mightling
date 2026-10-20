"""A sandboxed command never reaches the host's control sockets (1.7.1).

On 2026-10-20 a command in `ling exec`'s workspace-write sandbox ran `docker restart` on the host:
the user is in the `docker` group, and a read-only bind of `/` does not stop a `connect()` to
`/run/docker.sock`, which is root on the host. Codex's sandbox helper now denies every host control
socket that exists (`ling_airgapped::host_sockets`, patch 0019), and bubblewrap masks each one. Full
Access has no sandbox, as before; the air-gap hook beside it is unchanged.
"""

import re
from pathlib import Path
from typing import Final

import pytest

from dreamference.runner.codex_branded_builder import CODEX_SUBMODULE_DIR, CodexBrandedBuilder

REPO: Final[Path] = Path(__file__).resolve().parent.parent
LEAF_RS: Final[Path] = REPO / "ling-rs" / "airgapped" / "src" / "lib.rs"
SUBMODULE_PRESENT: Final[bool] = (Path(CODEX_SUBMODULE_DIR) / "codex-rs").is_dir()


def listed(name: str) -> list[str]:
    """The strings of one `pub const NAME: [&str; N]` array in the leaf crate."""
    text = LEAF_RS.read_text(encoding="utf-8")
    match = re.search(rf"pub const {name}: \[&str; \d+\] =\s*\[(.*?)\];", text, re.S)
    assert match, name
    return re.findall(r'"([^"]+)"', match.group(1))


def test_the_host_control_sockets_are_listed():
    host = listed("HOST_SOCKETS")
    for path in (
        "run/docker.sock",
        "run/containerd/containerd.sock",
        "run/podman",
        "run/dbus/system_bus_socket",
    ):
        assert path in host
    runtime = listed("HOST_SOCKETS_IN_RUNTIME_DIR")
    for name in ("bus", "systemd", "docker.sock", "podman", "mightling"):
        assert name in runtime
    # The air-gap seals stay readable: commands resolve their level from them.
    assert "ling-airgapped" not in runtime


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_the_sandbox_helper_denies_them_for_every_managed_profile(tmp_path):
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "src"))
    helper = (
        tmp_path / "src" / "codex-rs" / "linux-sandbox" / "src" / "linux_run_main.rs"
    ).read_text()
    body = helper[helper.index("fn resolve_permission_profile(") :]
    body = body[: body.index("Ok(EffectivePermissions")]
    # After the runtime permissions are derived, before they are used, whatever the profile.
    assert "ling_airgapped::host_sockets()" in body
    assert "FileSystemAccessMode::Deny" in body
    assert body.index("to_runtime_permissions()") < body.index("ling_airgapped::host_sockets()")
    # The air-gap hook is still there, unchanged.
    assert (
        "ling_airgapped::sealed_for_command()" in body
        and "NetworkSandboxPolicy::Restricted" in body
    )
