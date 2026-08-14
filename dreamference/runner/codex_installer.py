"""
Codex CLI Provisioning & Detection for Dreamference.

This module provides the CodexInstaller class which verifies Codex installation (`codex`)
and manages auto-installing `codex` via pip or pipx if missing.
"""

import shutil
import subprocess
import sys
from typing import Optional

class CodexInstaller:
    """
    Installer and verifier class for Codex CLI (`codex`).
    """

    @classmethod
    def get_codex_executable(cls) -> Optional[str]:
        """
        Locates executable `codex` binary in system PATH or standard install locations.

        Returns:
            Optional[str]: Absolute path to executable codex binary or None if not found.
        """
        import os
        for path in [
            shutil.which("codex"),
            os.path.expanduser("~/.local/bin/codex"),
            os.path.expanduser("~/.codex/bin/codex")
        ]:
            if path and os.path.exists(path) and os.access(path, os.X_OK):
                return path
        return None

    @classmethod
    def is_installed(cls) -> bool:
        """
        Checks if Codex CLI binary exists.

        Returns:
            bool: True if codex executable is present.
        """
        return cls.get_codex_executable() is not None

    @classmethod
    def install_if_missing(cls) -> bool:
        """
        Auto-installs `codex` CLI via the official standalone install script.

        Returns:
            bool: True if installation succeeds.
        """
        if cls.is_installed():
            print("✅ Codex CLI (`codex`) is already installed.")
            return True

        print("📦 Installing Codex CLI standalone (`curl -fsSL https://chatgpt.com/codex/install.sh | sh`)...")
        try:
            res = subprocess.run(["sh", "-c", "curl -fsSL https://chatgpt.com/codex/install.sh | sh"], check=False)
            if res.returncode == 0 and cls.is_installed():
                print("✅ Codex CLI installed successfully!")
                return True
        except Exception as e:
            print(f"⚠️ Failed to install Codex via install script: {e}")

        return False
