"""
Automated Goose CLI Provisioning & Detection.

This module provides the GooseInstaller class responsible for locating active `goose` binaries
or executing single-command curl installation scripts for official AAIF Goose CLI 1.45+.
"""

import os
import shutil
import subprocess
import sys
from typing import Optional

class GooseInstaller:
    """
    Installer and path resolver for official Goose CLI binaries.
    """

    @classmethod
    def get_goose_executable(cls) -> Optional[str]:
        """
        Locates executable `goose` binary across system PATH and standard installation directories.

        Returns:
            Optional[str]: Absolute path to executable goose binary or None if not found.
        """
        which_goose = shutil.which("goose")
        if which_goose:
            return which_goose

        # Check standard user local binary locations
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
        """
        Checks if Goose CLI binary exists and is executable.

        Returns:
            bool: True if goose executable is present.
        """
        return cls.get_goose_executable() is not None

    @classmethod
    def install_if_missing(cls) -> bool:
        """
        Automatically provisions official AAIF Goose CLI binary if missing from system.

        Executes non-interactive shell installer:
        `curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes`

        Returns:
            bool: True if goose installation is confirmed.
        """
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
