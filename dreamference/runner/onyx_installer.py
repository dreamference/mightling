"""
Onyx CLI Provisioning & Detection for Dreamference.

This module provides the OnyxInstaller class which verifies the Onyx CLI (`onyx-cli`) is
present and installs it via pip if missing. `onyx-cli` is the supported way to deploy and
manage a self-hosted Onyx stack; Dreamference never writes Onyx's compose files itself.
"""

import os
import shutil
import subprocess
import sys
from typing import Optional


class OnyxInstaller:
    """
    Installer and verifier class for the Onyx CLI (`onyx-cli`).
    """

    @classmethod
    def get_onyx_executable(cls) -> Optional[str]:
        """
        Locates the `onyx-cli` binary in the project venv, PATH, or standard install locations.

        The venv is checked first, and deliberately so: `pip install onyx-cli` inside this
        project's venv puts it next to `dream`, which is where it lands when Dreamference
        installs it, and a system-wide copy of a different version should not silently win.

        Returns:
            Optional[str]: Absolute path to the onyx-cli binary, or None if not found.
        """
        venv_candidate = os.path.join(os.path.dirname(sys.executable), "onyx-cli")
        for path in [
            venv_candidate,
            shutil.which("onyx-cli"),
            os.path.expanduser("~/.local/bin/onyx-cli"),
        ]:
            if path and os.path.exists(path) and os.access(path, os.X_OK):
                return path
        return None

    @classmethod
    def is_installed(cls) -> bool:
        """
        Checks whether the Onyx CLI binary exists.

        Returns:
            bool: True if the onyx-cli executable is present.
        """
        return cls.get_onyx_executable() is not None

    @classmethod
    def install_if_missing(cls) -> bool:
        """
        Installs `onyx-cli` into the active interpreter's environment if it is absent.

        pip rather than `uv tool install` (which the Onyx docs recommend) because uv is not a
        dependency of this project and may not be present, while the interpreter running this
        code always has a pip to call.

        Returns:
            bool: True if the CLI is available once this returns.
        """
        if cls.is_installed():
            return True

        print("📦 Installing Onyx CLI (`pip install onyx-cli`)...")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "onyx-cli"], check=False
            )
            if result.returncode == 0 and cls.is_installed():
                print("✅ Onyx CLI installed successfully!")
                return True
        except Exception as exc:
            print(f"⚠️ Failed to install Onyx CLI: {exc}")

        print("💡 Install it manually with: pip install onyx-cli  (or: uv tool install onyx-cli)")
        return False
