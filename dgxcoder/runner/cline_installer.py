"""
Cline Extension & VS Code Detection and Provisioning for DGXCoder.

This module provides the ClineInstaller class which verifies VS Code / VSCodium installation
and manages auto-installing the Cline VS Code extension (`saoudrizwan.claude-dev`).
"""

import shutil
import subprocess
from typing import Optional

# Official VS Code Marketplace extension ID for Cline
CLINE_EXTENSION_ID: str = "saoudrizwan.claude-dev"

class ClineInstaller:
    """
    Installer and verifier class for VS Code / VSCodium and the Cline extension.
    """

    @classmethod
    def get_vscode_executable(cls) -> Optional[str]:
        """
        Locates VS Code (`code`) or VSCodium (`codium`) binary.

        Returns:
            Optional[str]: Command string or executable path or None if not found.
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
            bool: True if code or codium executable exists.
        """
        return cls.get_vscode_executable() is not None

    @classmethod
    def is_cline_extension_installed(cls) -> bool:
        """
        Checks if the Cline extension is installed in VS Code/VSCodium.

        Returns:
            bool: True if saoudrizwan.claude-dev is present in extension list.
        """
        code_bin = cls.get_vscode_executable()
        if not code_bin:
            return False

        try:
            res = subprocess.run([code_bin, "--list-extensions"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                installed = [line.strip().lower() for line in res.stdout.splitlines()]
                return CLINE_EXTENSION_ID.lower() in installed
        except Exception:
            pass

        return False

    @classmethod
    def install_cline_if_missing(cls) -> bool:
        """
        Auto-installs the Cline VS Code extension via `code --install-extension saoudrizwan.claude-dev`.

        Returns:
            bool: True if extension is installed successfully.
        """
        if cls.is_cline_extension_installed():
            print(f"✅ Cline extension ('{CLINE_EXTENSION_ID}') is already installed in VS Code.")
            return True

        code_bin = cls.get_vscode_executable()
        if not code_bin:
            print("⚠️ VS Code CLI (`code` or `codium`) is not found in PATH.")
            print("💡 Install VS Code or add `code` to PATH to use Cline.")
            return False

        print(f"📦 Installing Cline VS Code extension ('{CLINE_EXTENSION_ID}')...")
        try:
            res = subprocess.run([code_bin, "--install-extension", CLINE_EXTENSION_ID], check=False)
            if res.returncode == 0 or cls.is_cline_extension_installed():
                print("✅ Cline VS Code extension installed successfully!")
                return True
        except Exception as e:
            print(f"⚠️ Failed to install Cline extension: {e}")

        return cls.is_cline_extension_installed()
