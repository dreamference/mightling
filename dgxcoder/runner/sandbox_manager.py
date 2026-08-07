import shutil
from typing import List

class SandboxManager:
    """Generates subagent container sandbox launcher prefixes for unprivileged execution."""

    @classmethod
    def get_prefix(cls, mode: str, cwd: str) -> List[str]:
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
