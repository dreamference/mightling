"""
Aider CLI Provisioning & Detection for Neurr.

This module provides the AiderInstaller class which verifies Aider installation (`aider`)
and manages auto-installing `aider-chat` via pip or pipx if missing.
"""

import shutil
import subprocess
import sys
from typing import Optional

class AiderInstaller:
    """
    Installer and verifier class for Aider CLI (`aider-chat`).
    """

    @classmethod
    def get_aider_executable(cls) -> Optional[str]:
        """
        Locates executable `aider` binary in system PATH.

        Returns:
            Optional[str]: Absolute path to executable aider binary or None if not found.
        """
        return shutil.which("aider")

    @classmethod
    def is_installed(cls) -> bool:
        """
        Checks if Aider CLI binary exists.

        Returns:
            bool: True if aider executable is present.
        """
        return cls.get_aider_executable() is not None

    @classmethod
    def install_if_missing(cls) -> bool:
        """
        Auto-installs `aider-chat` package via pip in active environment or pipx.

        Returns:
            bool: True if installation succeeds.
        """
        if cls.is_installed():
            print("✅ Aider CLI (`aider`) is already installed.")
            return True

        print("📦 Installing Aider CLI (`pip install aider-chat`)...")
        try:
            res = subprocess.run([sys.executable, "-m", "pip", "install", "aider-chat"], check=False)
            if res.returncode == 0 and cls.is_installed():
                print("✅ Aider CLI installed successfully!")
                return True
        except Exception as e:
            print(f"⚠️ Failed to install Aider via pip: {e}")

        if shutil.which("pipx"):
            print("📦 Attempting Aider installation via pipx...")
            try:
                res = subprocess.run(["pipx", "install", "aider-chat"], check=False)
                if res.returncode == 0 and cls.is_installed():
                    print("✅ Aider CLI installed successfully via pipx!")
                    return True
            except Exception:
                pass

        return cls.is_installed()
