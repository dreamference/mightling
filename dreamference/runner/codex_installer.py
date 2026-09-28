"""
Codex CLI Provisioning & Detection for Dreamference.

This module provides the CodexInstaller class, which resolves the Codex executable the runner
launches: the Puffin-branded build compiled from the `codex/` submodule by CodexBrandedBuilder.
An upstream `codex` on PATH is deliberately never used, not even as a fallback -- a silent fallback
would put the unbranded agent back on screen with nothing to say it had happened.
"""

import os
from typing import Optional

from dreamference.runner.codex_branded_builder import CodexBrandedBuilder


class CodexInstaller:
    """
    Installer and verifier class for the Puffin-branded Codex (`puffin-codex`).
    """

    @classmethod
    def get_codex_executable(cls) -> Optional[str]:
        """
        Locates the branded `puffin-codex` executable.

        Returns:
            Optional[str]: Absolute path to the executable, or None if it has not been built.
        """
        path = CodexBrandedBuilder.executable_path()
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        return None

    @classmethod
    def is_installed(cls) -> bool:
        """
        Checks that the branded build is installed and matches the current submodule and patches.

        Returns:
            bool: True if no build is needed.
        """
        return CodexBrandedBuilder.is_current()

    @classmethod
    def install_if_missing(cls) -> bool:
        """
        Builds the branded Codex from the submodule if it is missing or out of date.

        Returns:
            bool: True if an up-to-date `puffin-codex` is installed afterwards.
        """
        if cls.is_installed():
            print("✅ Puffin Codex (`puffin-codex`) is already built.")
            return True
        return CodexBrandedBuilder.build()
