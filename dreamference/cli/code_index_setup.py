"""
Code index setup for Dreamference.

This module provides the CodeIndexSetup class, behind `puffin-admin code setup`: it installs the
pinned tools `puffin-code` runs to build its index (specs/DREAMFERENCE_PUFFIN_CODE_INDEX.md §5) --
codebase-memory-mcp and the scip CLI from their release archives, each checked against the SHA-256
committed in `puffin-code-rs/code-index.sha256`, and scip-python and scip-typescript from npm,
pinned by the committed lockfile's integrity hashes. Everything goes under Puffin's own install
directory; `puffin-code` resolves its tools from there only, never from PATH, because its schema
fingerprints belong to exactly these versions.

scip-go, scip-java and scip-dotnet also need a toolchain of their own (Go, a JDK 17 or newer, a
.NET SDK 8 or newer). Setup looks for each once, records what it found as a link under the
indexers directory, and installs the indexer only beside a recorded toolchain; `puffin-code
status` names what is missing. Maven and Gradle, which scip-java drives, are recorded the same way
when they are installed. scip-clang has no linux-arm64 build upstream and is not installed.

Nothing is downloaded at index or query time: this is the only step that uses the network.
"""

import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from typing import Dict, Final, List, NamedTuple, Optional

from dreamference.runner.codex_branded_builder import CODE_CRATE_DIR, INSTALL_DIR

PINS_FILE: Final[str] = os.path.join(CODE_CRATE_DIR, "code-index.sha256")
INDEXERS_SOURCE_DIR: Final[str] = os.path.join(CODE_CRATE_DIR, "indexers")
INDEXERS_DIR: Final[str] = os.path.join(INSTALL_DIR, "indexers")
DOWNLOAD_TIMEOUT_S: Final[int] = 300
# The toolchain each indexer runs on: a link of this name under the indexers directory.
TOOLCHAIN_OF: Final[Dict[str, str]] = {"scip-go": "go", "scip-java": "java", "scip-dotnet": "dotnet"}
TOOLCHAIN_NAMES: Final[Dict[str, str]] = {"go": "Go toolchain", "java": "JDK 17 or newer", "dotnet": ".NET SDK 8 or newer"}
# The oldest toolchain each indexer supports: scip-java's classes are Java 17 bytecode (class file
# version 61), and scip-dotnet ships builds for .NET 6 to 10 but is supported on 8 and newer.
# The build tools scip-java drives, each recorded as a link of its name when installed. Optional:
# a project's wrapper (`mvnw`, `gradlew`) with its distribution on disk serves as well, and a
# system package is already on the sandbox's PATH. An installation under the home directory
# (sdkman, a tarball) is neither on that PATH nor visible in the sandbox unless it is recorded.
BUILD_TOOLS: Final[Dict[str, str]] = {"maven": "mvn", "gradle": "gradle"}
MIN_JAVA: Final[int] = 17
MIN_DOTNET: Final[int] = 8
DOTNET_TOOL_DIR: Final[str] = "scip-dotnet"


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
        if tool.name == "scip-dotnet":
            installed = os.path.isfile(os.path.join(INDEXERS_DIR, DOTNET_TOOL_DIR, "scip-dotnet.dll"))
        else:
            installed = os.access(os.path.join(cls.bin_dir(), tool.name), os.X_OK)
        if not installed or not os.path.isfile(cls._stamp(tool)):
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
        if tool.name == "scip-dotnet":
            return cls._install_dotnet_tool(tool, archive)
        if tool.member == "-":
            # The download is the program itself (scip-java's launcher, a script with its jars
            # appended).
            content = archive
        else:
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
    def _install_dotnet_tool(cls, tool: PinnedTool, archive: bytes) -> bool:
        """
        Installs scip-dotnet from its verified NuGet package: the framework-dependent build for the
        newest .NET the recorded SDK runs, unpacked under the indexers directory. No `dotnet tool
        install`, which would fetch the package again from the network.

        Args:
            tool (PinnedTool): The scip-dotnet pin.
            archive (bytes): The package, already checked against the pin.

        Returns:
            bool: True if installed.
        """
        major = cls.dotnet_major(os.path.join(INDEXERS_DIR, "dotnet", "dotnet"))
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                frameworks = sorted(
                    {int(m.group(1)) for name in package.namelist() if (m := re.match(r"tools/net(\d+)\.0/any/", name))}
                )
                usable = [f for f in frameworks if major is not None and f <= major]
                if not usable:
                    print(f"❌ scip-dotnet: no build for .NET {major} (the package has {frameworks})")
                    return False
                prefix = f"tools/net{usable[-1]}.0/any/"
                staging = os.path.join(INDEXERS_DIR, f".{DOTNET_TOOL_DIR}.new")
                shutil.rmtree(staging, ignore_errors=True)
                for name in package.namelist():
                    if name.startswith(prefix) and not name.endswith("/"):
                        target = os.path.join(staging, name[len(prefix):])
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with open(target, "wb") as handle:
                            handle.write(package.read(name))
        except zipfile.BadZipFile as error:
            print(f"❌ scip-dotnet: unreadable package: {error}")
            return False
        final = os.path.join(INDEXERS_DIR, DOTNET_TOOL_DIR)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(staging, final)
        os.makedirs(cls.bin_dir(), exist_ok=True)
        with open(cls._stamp(tool), "w") as handle:
            handle.write(f"{tool.sha256}\n")
        print(f"✅ Installed scip-dotnet {tool.version} (net{usable[-1]}.0)")
        return True

    @classmethod
    def _version_of(cls, command: List[str]) -> Optional[str]:
        """
        Runs a toolchain's version command.

        Args:
            command (List[str]): The command.

        Returns:
            Optional[str]: Its stdout and stderr, or None if it could not run.
        """
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return (result.stdout or "") + (result.stderr or "") if result.returncode == 0 else None

    @classmethod
    def dotnet_major(cls, dotnet: str) -> Optional[int]:
        """
        The newest .NET major version an SDK can build with.

        Args:
            dotnet (str): The `dotnet` executable.

        Returns:
            Optional[int]: The major version, or None without an SDK.
        """
        output = cls._version_of([dotnet, "--list-sdks"])
        majors = [int(m.group(1)) for m in re.finditer(r"^(\d+)\.\d+\.\d+", output or "", re.MULTILINE)]
        return max(majors) if majors else None

    @classmethod
    def find_toolchains(cls) -> Dict[str, str]:
        """
        Finds the toolchains the language indexers need, once, at setup: Go (its GOROOT), a JDK
        17 or newer (its JAVA_HOME, which must have `javac`: a runtime alone cannot build), and a
        .NET SDK 8 or newer (the directory holding `dotnet`). Maven and Gradle are recorded
        beside the JDK when they are installed (their homes, which hold `bin/mvn`, `bin/gradle`).

        Returns:
            Dict[str, str]: Link name (`go`, `java`, `dotnet`, `maven`, `gradle`) to the directory
            found.
        """
        found: Dict[str, str] = {}
        go = shutil.which("go")
        if go:
            goroot = (cls._version_of([go, "env", "GOROOT"]) or "").strip()
            if goroot and os.path.isfile(os.path.join(goroot, "bin", "go")):
                found["go"] = os.path.realpath(goroot)
        java_home = os.environ.get("JAVA_HOME")
        javac = os.path.join(java_home, "bin", "javac") if java_home else shutil.which("javac")
        if javac and os.path.isfile(javac):
            home = os.path.dirname(os.path.dirname(os.path.realpath(javac)))
            match = re.search(r"javac (\d+)", cls._version_of([javac, "-version"]) or "")
            if match and int(match.group(1)) >= MIN_JAVA:
                found["java"] = home
        for name, executable in BUILD_TOOLS.items():
            path = shutil.which(executable)
            if path:
                # `/usr/bin/mvn` is a chain of links to the real `<home>/bin/mvn`.
                tool_home = os.path.dirname(os.path.dirname(os.path.realpath(path)))
                if os.path.isfile(os.path.join(tool_home, "bin", executable)):
                    found[name] = tool_home
        dotnet = shutil.which("dotnet")
        if dotnet:
            major = cls.dotnet_major(dotnet)
            if major is not None and major >= MIN_DOTNET:
                found["dotnet"] = os.path.dirname(os.path.realpath(dotnet))
        return found

    @classmethod
    def record_toolchains(cls, found: Dict[str, str]) -> None:
        """
        Records each toolchain as a link under the indexers directory, the way `node` is, and
        removes the link of one no longer found, so `puffin-code` never runs a stale path.

        Args:
            found (Dict[str, str]): From `find_toolchains`.
        """
        os.makedirs(INDEXERS_DIR, exist_ok=True)
        for name in sorted(set(TOOLCHAIN_OF.values()) | set(BUILD_TOOLS)):
            link = os.path.join(INDEXERS_DIR, name)
            if name in found:
                staging = f"{link}.new"
                if os.path.lexists(staging):
                    os.remove(staging)
                os.symlink(found[name], staging)
                os.replace(staging, link)
                print(f"✅ Recorded {name}: {found[name]}")
            elif os.path.islink(link):
                os.remove(link)

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
        Installs scip-python and scip-typescript from the committed lockfile, with `npm ci --ignore-scripts` (the
        lockfile's integrity hashes are checked; no package script runs), and records which
        `node` runs it, so the index never looks Node.js up on PATH.

        Returns:
            bool: True if both are installed afterwards; False without Node.js, which keeps Python,
            TypeScript and JavaScript on the universal layer (`puffin-code status` says so).
        """
        node = cls.node_path()
        npm = shutil.which("npm")
        if node is None or npm is None:
            print("⚠️ Node.js is not installed: Python, TypeScript and JavaScript will be indexed by the universal layer only")
            return False
        os.makedirs(INDEXERS_DIR, exist_ok=True)
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(os.path.join(INDEXERS_SOURCE_DIR, name), os.path.join(INDEXERS_DIR, name))
        print("⬇️  scip-python and scip-typescript (npm ci, pinned by package-lock.json)")
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
        print(f"✅ Installed scip-python 0.6.6 and scip-typescript 0.4.0 (node: {node})")
        return True

    @classmethod
    def install(cls) -> bool:
        """
        Installs every pinned tool of the code index.

        Returns:
            bool: True if all of them are installed afterwards.
        """
        ok = True
        toolchains = cls.find_toolchains()
        cls.record_toolchains(toolchains)
        for tool in cls.pins():
            needs = TOOLCHAIN_OF.get(tool.name)
            if needs is not None and needs not in toolchains:
                # Not a failure: the language stays on the universal layer, and status says why.
                print(f"⚠️ {tool.name}: no {TOOLCHAIN_NAMES[needs]} found; not installed (that language stays on the universal layer)")
                continue
            ok = cls.install_tool(tool) and ok
        ok = cls.install_npm_indexers() and ok
        return ok
