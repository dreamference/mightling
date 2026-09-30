"""
Code index setup for Dreamference.

This module provides the CodeIndexSetup class, behind `puffin-admin code setup`: it installs the
pinned tools `puffin-code` runs to build its index (specs/DREAMFERENCE_PUFFIN_CODE_INDEX.md §5) --
codebase-memory-mcp and the scip CLI from their release archives, each checked against the SHA-256
committed in `puffin-code-rs/code-index.sha256`, and scip-python from npm, pinned by the committed
lockfile's integrity hashes. Everything goes under Puffin's own install directory; `puffin-code`
resolves its tools from there only, never from PATH, because its schema fingerprints belong to
exactly these versions.

Nothing is downloaded at index or query time: this is the only step that uses the network.
"""

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import urllib.request
from typing import Final, List, NamedTuple, Optional

from dreamference.runner.codex_branded_builder import CODE_CRATE_DIR, INSTALL_DIR

PINS_FILE: Final[str] = os.path.join(CODE_CRATE_DIR, "code-index.sha256")
INDEXERS_SOURCE_DIR: Final[str] = os.path.join(CODE_CRATE_DIR, "indexers")
INDEXERS_DIR: Final[str] = os.path.join(INSTALL_DIR, "indexers")
DOWNLOAD_TIMEOUT_S: Final[int] = 120


class PinnedTool(NamedTuple):
    """One line of `code-index.sha256`."""

    sha256: str
    name: str
    version: str
    url: str
    member: str


class CodeIndexSetup:
    """
    Installs the pinned tools of the code index. A pure classmethod namespace.
    """

    @classmethod
    def pins(cls) -> List[PinnedTool]:
        """
        Reads the pinned tools.

        Returns:
            List[PinnedTool]: One entry per non-comment line of `code-index.sha256`.
        """
        pins = []
        with open(PINS_FILE) as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    pins.append(PinnedTool(*line.split()))
        return pins

    @classmethod
    def bin_dir(cls) -> str:
        """
        Returns where the tools are installed: beside `puffin`.

        Returns:
            str: The directory.
        """
        return os.path.join(INSTALL_DIR, "bin")

    @classmethod
    def _download(cls, url: str) -> bytes:
        """
        Downloads a release archive.

        Args:
            url (str): The archive's URL.

        Returns:
            bytes: Its content.
        """
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as response:
            return response.read()

    @classmethod
    def _stamp(cls, tool: PinnedTool) -> str:
        return os.path.join(cls.bin_dir(), f".{tool.name}.sha256")

    @classmethod
    def is_installed(cls, tool: PinnedTool) -> bool:
        """
        Checks that a tool is installed from exactly its pinned archive.

        Args:
            tool (PinnedTool): The tool.

        Returns:
            bool: True when the binary exists and its stamp names the pinned archive.
        """
        binary = os.path.join(cls.bin_dir(), tool.name)
        if not os.access(binary, os.X_OK) or not os.path.isfile(cls._stamp(tool)):
            return False
        with open(cls._stamp(tool)) as handle:
            return handle.read().strip() == tool.sha256

    @classmethod
    def install_tool(cls, tool: PinnedTool) -> bool:
        """
        Downloads, verifies and installs one pinned tool.

        Args:
            tool (PinnedTool): The tool.

        Returns:
            bool: True if the pinned version is installed afterwards.
        """
        if cls.is_installed(tool):
            print(f"✅ {tool.name} {tool.version} is installed")
            return True
        print(f"⬇️  {tool.name} {tool.version}: {tool.url}")
        try:
            archive = cls._download(tool.url)
        except OSError as error:
            print(f"❌ Could not download {tool.name}: {error}")
            return False
        digest = hashlib.sha256(archive).hexdigest()
        if digest != tool.sha256:
            # Never installed: a changed archive under a pinned URL is exactly what the pin guards.
            print(f"❌ {tool.name}: checksum {digest} does not match the pinned {tool.sha256}; not installed")
            return False
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
                member = next(
                    (m for m in tar.getmembers() if m.isfile() and os.path.basename(m.name) == tool.member), None
                )
                if member is None:
                    print(f"❌ {tool.name}: the archive has no {tool.member}")
                    return False
                content = tar.extractfile(member).read()
        except tarfile.TarError as error:
            print(f"❌ {tool.name}: unreadable archive: {error}")
            return False
        os.makedirs(cls.bin_dir(), exist_ok=True)
        staging = os.path.join(cls.bin_dir(), f".{tool.name}.new")
        with open(staging, "wb") as handle:
            handle.write(content)
        os.chmod(staging, 0o755)
        # Renamed over the old binary, so an index run in flight keeps the one it started with.
        os.replace(staging, os.path.join(cls.bin_dir(), tool.name))
        with open(cls._stamp(tool), "w") as handle:
            handle.write(f"{tool.sha256}\n")
        print(f"✅ Installed {tool.name} {tool.version}")
        return True

    @classmethod
    def node_path(cls) -> Optional[str]:
        """
        Resolves the Node.js the npm indexers run on, once, at setup.

        Returns:
            Optional[str]: The resolved `node` executable, or None without Node.js.
        """
        node = shutil.which("node")
        return os.path.realpath(node) if node else None

    @classmethod
    def install_npm_indexers(cls) -> bool:
        """
        Installs scip-python from the committed lockfile, with `npm ci --ignore-scripts` (the
        lockfile's integrity hashes are checked; no package script runs), and records which
        `node` runs it, so the index never looks Node.js up on PATH.

        Returns:
            bool: True if scip-python is installed afterwards; False without Node.js, which keeps
            Python on the universal layer (`puffin-code status` says so).
        """
        node = cls.node_path()
        npm = shutil.which("npm")
        if node is None or npm is None:
            print("⚠️ Node.js is not installed: Python will be indexed by the universal layer only")
            return False
        os.makedirs(INDEXERS_DIR, exist_ok=True)
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(os.path.join(INDEXERS_SOURCE_DIR, name), os.path.join(INDEXERS_DIR, name))
        print("⬇️  scip-python (npm ci, pinned by package-lock.json)")
        result = subprocess.run(
            [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=INDEXERS_DIR,
            check=False,
        )
        if result.returncode != 0:
            print("❌ npm ci failed; see its output above")
            return False
        link = os.path.join(INDEXERS_DIR, "node")
        staging = f"{link}.new"
        if os.path.lexists(staging):
            os.remove(staging)
        os.symlink(node, staging)
        os.replace(staging, link)
        print(f"✅ Installed scip-python 0.6.6 (node: {node})")
        return True

    @classmethod
    def install(cls) -> bool:
        """
        Installs every pinned tool of the code index.

        Returns:
            bool: True if all of them are installed afterwards.
        """
        ok = True
        for tool in cls.pins():
            ok = cls.install_tool(tool) and ok
        ok = cls.install_npm_indexers() and ok
        return ok
