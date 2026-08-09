"""
Continue IDE Extension Detection & Provisioning for Neurr.

This module provides the ContinueInstaller class which verifies VS Code / VSCodium installation
and manages auto-installing the Continue extension (`Continue.continue`).
"""

import shutil
import subprocess
from typing import Optional

CONTINUE_EXTENSION_ID: str = "Continue.continue"

class ContinueInstaller:
    """
    Installer and verifier class for Continue extension in VS Code / VSCodium.
    """

    @classmethod
    def get_vscode_executable(cls) -> Optional[str]:
        """
        Locates VS Code (`code`) or VSCodium (`codium`) binary.

        Returns:
            Optional[str]: Executable string or None.
        """
        if shutil.which("code"):
            return "code"
        elif shutil.which("codium"):
            return "codium"
        return None

    @classmethod
    def is_vscode_installed(cls) -> bool:
        """
        Checks if VS Code or VSCodium CLI is available in system PATH.

        Returns:
            bool: True if executable exists.
        """
        return cls.get_vscode_executable() is not None

    @classmethod
    def is_continue_extension_installed(cls) -> bool:
        """
        Checks if Continue extension is installed in VS Code/VSCodium.

        Returns:
            bool: True if Continue.continue is present in extension list.
        """
        code_bin = cls.get_vscode_executable()
        if not code_bin:
            return False

        try:
            res = subprocess.run([code_bin, "--list-extensions"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                installed = [line.strip().lower() for line in res.stdout.splitlines()]
                return CONTINUE_EXTENSION_ID.lower() in installed
        except Exception:
            pass

        return False

    @classmethod
    def install_continue_if_missing(cls) -> bool:
        """
        Auto-installs the Continue extension via `code --install-extension Continue.continue`.

        Returns:
            bool: True if extension is installed successfully.
        """
        if cls.is_continue_extension_installed():
            print(f"✅ Continue extension ('{CONTINUE_EXTENSION_ID}') is already installed in VS Code.")
            return True

        code_bin = cls.get_vscode_executable()
        if not code_bin:
            print("⚠️ VS Code CLI (`code` or `codium`) is not found in PATH.")
            print("💡 Install VS Code or add `code` to PATH to use Continue.")
            return False

        print(f"📦 Installing Continue extension ('{CONTINUE_EXTENSION_ID}')...")
        try:
            res = subprocess.run([code_bin, "--install-extension", CONTINUE_EXTENSION_ID], check=False)
            if res.returncode == 0 or cls.is_continue_extension_installed():
                print("✅ Continue extension installed successfully!")
                return True
        except Exception as e:
            print(f"⚠️ Failed to install Continue extension: {e}")

        return cls.is_continue_extension_installed()
