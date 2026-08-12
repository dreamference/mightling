"""
OpenHands Container Image Detection & Provisioning for Dreamng.

This module provides the OpenHandsInstaller class which verifies Docker availability
and manages pulling the official OpenHands docker image (`ghcr.io/all-hands-ai/openhands:main`).
"""

import shutil
import subprocess

OPENHANDS_IMAGE: str = "ghcr.io/all-hands-ai/openhands:main"

class OpenHandsInstaller:
    """
    Installer and verifier class for OpenHands container image.
    """

    @classmethod
    def is_docker_available(cls) -> bool:
        """
        Checks if Docker daemon is running and accessible.

        Returns:
            bool: True if docker command succeeds.
        """
        if shutil.which("docker") is None:
            return False
        try:
            res = subprocess.run(["docker", "ps"], capture_output=True, timeout=3)
            return res.returncode == 0
        except Exception:
            return False

    @classmethod
    def is_image_downloaded(cls) -> bool:
        """
        Checks if OpenHands container image exists in local Docker engine.

        Returns:
            bool: True if image is present locally.
        """
        if not cls.is_docker_available():
            return False

        try:
            res = subprocess.run(["docker", "images", "-q", OPENHANDS_IMAGE], capture_output=True, text=True)
            return res.returncode == 0 and len(res.stdout.strip()) > 0
        except Exception:
            return False

    @classmethod
    def pull_image_if_missing(cls) -> bool:
        """
        Pulls OpenHands docker image if missing.

        Returns:
            bool: True if image is available.
        """
        if cls.is_image_downloaded():
            print(f"✅ OpenHands container image ('{OPENHANDS_IMAGE}') is already pulled.")
            return True

        if not cls.is_docker_available():
            print("⚠️ Docker daemon is not running or current user lacks docker socket permissions.")
            return False

        print(f"📦 Pulling OpenHands container image ('{OPENHANDS_IMAGE}')...")
        try:
            res = subprocess.run(["docker", "pull", OPENHANDS_IMAGE], check=False)
            return res.returncode == 0
        except Exception as e:
            print(f"⚠️ Failed to pull OpenHands image: {e}")
            return False
