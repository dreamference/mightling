"""
A copy of `puffin` that starts inside a SWE-bench instance image
(specs/DREAMFERENCE_PUFFIN_SWE_BENCH.md §12).

The installed binary is linked against this machine's C library (glibc 2.39 on the GB10); the
instance images are Ubuntu 22.04 with glibc 2.35, where it fails at load ("version `GLIBC_2.38'
not found", measured 2026-10-01). The runtime is a directory mounted read-only at `/opt/puffin`:

```text
bin/puffin, bin/codex-code-mode-host   copies whose ELF interpreter and rpath point at lib/
lib/                                    the host's loader, libc, libm and libgcc_s
```

Only the two binaries use the copied libraries (an rpath is per binary, and no `LD_LIBRARY_PATH`
is set), so everything the agent runs, the repository's Python and its tests, uses the image's
own. Copying also keeps a benchmark that is running apart from a `codex build` that replaces the
installed binary under it.
"""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Final, List, Optional

from dreamference.runner.codex_branded_builder import CODE_MODE_HOST_NAME, CodexBrandedBuilder
from dreamference.swe_bench import swe_bench_settings

# Where the runtime is mounted in every agent container.
CONTAINER_MOUNT: Final[str] = "/opt/puffin"

# The loader and the three libraries `ldd puffin` lists.
RUNTIME_LIBRARIES: Final[tuple] = ("libc.so.6", "libm.so.6", "libgcc_s.so.1")

STAMP_NAME: Final[str] = "source-hash"


class SweBenchRuntime:
    """Builds and locates the relocated runtime."""

    @classmethod
    def directory(cls) -> Path:
        """
        Returns:
            Path: The runtime directory under the benchmark's cache.
        """
        return swe_bench_settings.CACHE_DIR / "runtime"

    @classmethod
    def source_hash(cls, puffin_bin: str) -> str:
        """
        Identifies the installed binaries the runtime is copied from.

        Args:
            puffin_bin: The installed `puffin` executable.

        Returns:
            str: SHA-256 over `puffin` and `codex-code-mode-host`, hex.
        """
        digest = hashlib.sha256()
        for path in cls._binaries(puffin_bin):
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
        return digest.hexdigest()

    @classmethod
    def current_hash(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: The source hash the existing runtime was built from, if there is one.
        """
        try:
            return (cls.directory() / STAMP_NAME).read_text().strip() or None
        except OSError:
            return None

    @classmethod
    def ensure(cls, puffin_bin: str, patchelf: str) -> Optional[str]:
        """
        Builds the runtime unless the one on disk was made from the installed binaries.

        Args:
            puffin_bin: The installed `puffin` executable.
            patchelf: The `patchelf` executable (installed in the harness's virtualenv).

        Returns:
            Optional[str]: The runtime's source hash, or None when it could not be built.
        """
        wanted = cls.source_hash(puffin_bin)
        if cls.current_hash() == wanted:
            return wanted
        target = cls.directory()
        staging = target.with_name(f".runtime.{os.getpid()}.tmp")
        shutil.rmtree(staging, ignore_errors=True)
        (staging / "bin").mkdir(parents=True)
        (staging / "lib").mkdir()
        try:
            loader, libraries = cls.host_libraries(puffin_bin)
            for library in [loader, *libraries]:
                shutil.copy(os.path.realpath(library), staging / "lib" / os.path.basename(library))
            for binary in cls._binaries(puffin_bin):
                copy = staging / "bin" / os.path.basename(binary)
                shutil.copy(binary, copy)
                subprocess.run(
                    [patchelf, "--set-interpreter", f"{CONTAINER_MOUNT}/lib/{os.path.basename(loader)}",
                     "--set-rpath", f"{CONTAINER_MOUNT}/lib", str(copy)],
                    check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError, ValueError) as error:
            shutil.rmtree(staging, ignore_errors=True)
            print(f"❌ Could not build the puffin runtime for the instance images: {error}")
            return None
        (staging / STAMP_NAME).write_text(wanted + "\n")
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
        return wanted

    @classmethod
    def host_libraries(cls, puffin_bin: str) -> tuple:
        """
        Finds the loader and libraries the installed binary is linked against, from `ldd`.

        Args:
            puffin_bin: The installed `puffin` executable.

        Returns:
            tuple: (loader path, [library paths]).

        Raises:
            ValueError: When `ldd` names a library this module does not expect, so a new
                dependency is noticed instead of silently missing in the container.
        """
        output = subprocess.run(["ldd", puffin_bin], capture_output=True, text=True, check=True).stdout
        loader: Optional[str] = None
        libraries: List[str] = []
        for line in output.splitlines():
            words = line.split()
            if not words or words[0].startswith("linux-vdso"):
                continue
            if "=>" in words:
                name, path = words[0], words[words.index("=>") + 1]
                if name not in RUNTIME_LIBRARIES:
                    raise ValueError(f"puffin needs {name}, which the runtime does not carry")
                libraries.append(path)
            elif "ld-linux" in words[0]:
                loader = words[0]
        if loader is None or len(libraries) != len(RUNTIME_LIBRARIES):
            raise ValueError(f"unexpected `ldd {puffin_bin}` output")
        return loader, libraries

    @classmethod
    def _binaries(cls, puffin_bin: str) -> List[str]:
        host = os.path.join(os.path.dirname(os.path.realpath(puffin_bin)), CODE_MODE_HOST_NAME)
        return [os.path.realpath(puffin_bin)] + ([host] if os.path.exists(host) else [])

    @classmethod
    def installed_puffin(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: The installed branded `puffin`, or None if it has not been built.
        """
        path = CodexBrandedBuilder.executable_path()
        return path if os.path.exists(path) else None
