"""
Rootless Subagent Container Sandbox Launcher.

This module provides the SandboxManager class for wrapping subagent shell commands in rootless
container sandboxes (Apptainer, Podman, Docker) for secure execution.
"""

import shutil
from typing import List

class SandboxManager:
    """
    Manager class generating container command prefixes for isolated subagent execution.
    """

    @classmethod
    def get_prefix(cls, mode: str, cwd: str) -> List[str]:
        """
        Generates command prefix array for rootless container execution.

        Modes supported:
        - `apptainer`: Rootless HPC container isolation (`apptainer exec --writable-tmpfs`)
        - `podman`: Rootless container isolation (`podman run --rm -it ...`)
        - `docker`: Standard container isolation (`docker run --rm -it ...`)
        - `none`: Native un-sandboxed host execution (`[]`)

        Args:
            mode (str): Sandbox mode selection string.
            cwd (str): Current working directory path to mount into container workspace.

        Returns:
            List[str]: Container launcher command prefix list.
        """
        mode = mode.lower()
        if mode == "apptainer":
            if shutil.which("apptainer"):
                return ["apptainer", "exec", "--writable-tmpfs", "--bind", f"{cwd}:/workspace", "docker://ubuntu:22.04"]
            else:
                print("⚠️ Apptainer container runtime requested but not found in PATH. Running un-sandboxed.")
                return []

        elif mode == "podman":
            if shutil.which("podman"):
                return ["podman", "run", "--rm", "-it", "-v", f"{cwd}:/workspace:Z", "-w", "/workspace", "ubuntu:22.04"]
            else:
                print("⚠️ Podman container runtime requested but not found in PATH. Running un-sandboxed.")
                return []

        elif mode == "docker":
            if shutil.which("docker"):
                return ["docker", "run", "--rm", "-it", "-v", f"{cwd}:/workspace", "-w", "/workspace", "ubuntu:22.04"]
            else:
                print("⚠️ Docker container runtime requested but not found in PATH. Running un-sandboxed.")
                return []

        return []
