import os
import shutil
import subprocess
import sys
from typing import Optional

class GooseInstaller:
    """Manages Goose CLI detection and automated provisioning."""

    @classmethod
    def get_goose_executable(cls) -> Optional[str]:
        which_goose = shutil.which("goose")
        if which_goose:
            return which_goose

        candidate_paths = [
            os.path.expanduser("~/.local/bin/goose"),
            os.path.expanduser("~/.goose/bin/goose"),
            os.path.join(sys.prefix, "bin", "goose"),
            os.path.join(sys.prefix, "bin", "goose-ai"),
        ]
        for path in candidate_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                return path
        return None

    @classmethod
    def is_installed(cls) -> bool:
        return cls.get_goose_executable() is not None

    @classmethod
    def install_if_missing(cls) -> bool:
        if cls.is_installed():
            return True

        print("📦 Goose CLI (`goose`) is not installed. Initiating automatic installation...")
        try:
            subprocess.run(
                "curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes || true",
                shell=True,
                check=False
            )
            if cls.is_installed():
                print("✅ Official Goose CLI (1.45+) installed successfully!")
                return True
        except Exception as e:
            print(f"⚠️ Installation failed: {e}")

        return cls.is_installed()
