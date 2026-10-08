"""
`puffin-admin docs setup`: what `ling-docs` loads at run time
(specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §7.1, §7.3).

This module provides the DocsIndexSetup class. `ling-docs` opens no socket at any step, so the
three things it needs that are not in its binary are fetched here, once, each pinned by URL and
SHA-256:

- PDFium, the pdfium-binaries build `chromium/8076` (BSD-3), the one Phase 0 measured
  (its `libpdfium.so` is byte-identical to the copy in the measured pypdfium2 5.14);
- ONNX Runtime 1.30.0 (MIT), CPU only;
- the embedding model, `Snowflake/snowflake-arctic-embed-m-v2.0`'s int8 ONNX export and its
  tokenizer (Apache-2.0), at a pinned revision.

They go where the binary looks for them, beside it: `<install>/lib/ling-docs/` and
`<install>/models/snowflake-arctic-embed-m-v2.0-int8/` (`config::lib_dir`, `config::model_dir`
in `ling-docs-rs/src/config.rs`). A file already there with the pinned hash is not fetched again.
"""

import hashlib
import io
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from typing import Dict, Final, List, Optional, Tuple

from dreamference.runner.codex_branded_builder import INSTALL_DIR

DOCS_LIB_DIR: Final[str] = os.path.join(INSTALL_DIR, "lib", "ling-docs")
DOCS_MODEL_NAME: Final[str] = "snowflake-arctic-embed-m-v2.0-int8"
DOCS_MODEL_DIR: Final[str] = os.path.join(INSTALL_DIR, "models", DOCS_MODEL_NAME)

PDFIUM_RELEASE: Final[str] = "chromium/8076"
ONNXRUNTIME_VERSION: Final[str] = "1.30.0"
MODEL_REPO: Final[str] = "Snowflake/snowflake-arctic-embed-m-v2.0"
MODEL_REVISION: Final[str] = "95c2741480856aa9666782eb4afe11959938017f"

# (archive URL, its SHA-256, member to extract, installed name), per machine.
LIBRARIES: Final[Dict[str, List[Tuple[str, str, str, str]]]] = {
    "aarch64": [
        (f"https://github.com/bblanchon/pdfium-binaries/releases/download/{PDFIUM_RELEASE.replace('/', '%2F')}/pdfium-linux-arm64.tgz",
         "d7247b33ae5545615a5e877235dd97afc879e3a8805689684f528cae3339d352", "lib/libpdfium.so", "libpdfium.so"),
        (f"https://github.com/microsoft/onnxruntime/releases/download/v{ONNXRUNTIME_VERSION}/onnxruntime-linux-aarch64-{ONNXRUNTIME_VERSION}.tgz",
         "e16a27a8ed330bbc698df7330b0cf56e722f354e3bcc92118682c74ef3c3e3da",
         f"onnxruntime-linux-aarch64-{ONNXRUNTIME_VERSION}/lib/libonnxruntime.so.{ONNXRUNTIME_VERSION}", "libonnxruntime.so"),
    ],
    "x86_64": [
        (f"https://github.com/bblanchon/pdfium-binaries/releases/download/{PDFIUM_RELEASE.replace('/', '%2F')}/pdfium-linux-x64.tgz",
         "d9d67bc40af03aef4fe28a60b19b1086f28ace019c8c9caf19cb7fe3d14ceca3", "lib/libpdfium.so", "libpdfium.so"),
        (f"https://github.com/microsoft/onnxruntime/releases/download/v{ONNXRUNTIME_VERSION}/onnxruntime-linux-x64-{ONNXRUNTIME_VERSION}.tgz",
         "a5ed5a3cac51fbb2e90da632ae43d19212faaa20e76484e62bcb7c23ddb3b3fd",
         f"onnxruntime-linux-x64-{ONNXRUNTIME_VERSION}/lib/libonnxruntime.so.{ONNXRUNTIME_VERSION}", "libonnxruntime.so"),
    ],
}

# (path in the model repository, its SHA-256, installed name).
MODEL_FILES: Final[List[Tuple[str, str, str]]] = [
    ("onnx/model_int8.onnx", "03d923bb1850ebdccb068e2f3abd8aa43fe81c50d07d037ef103fe3d0fb78e3b", "model.onnx"),
    ("tokenizer.json", "f1cc44ad7faaeec47241864835473fd5403f2da94673f3f764a77ebcb0a803ec", "tokenizer.json"),
]


class DocsIndexSetup:
    """Installs PDFium, ONNX Runtime and the embedding model for `ling-docs`."""

    @classmethod
    def sha256(cls, path: str) -> str:
        """
        Hashes a file.

        Args:
            path (str): The file.

        Returns:
            str: Its SHA-256, hex.
        """
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()

    @classmethod
    def fetch(cls, url: str, expected: str) -> bytes:
        """
        Downloads a pinned file and checks its hash.

        Args:
            url (str): Where it is.
            expected (str): Its SHA-256.

        Returns:
            bytes: The content.

        Raises:
            ValueError: The content does not match the pin.
        """
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        got = hashlib.sha256(data).hexdigest()
        if got != expected:
            raise ValueError(f"{url}: SHA-256 {got}, expected {expected}")
        return data

    @classmethod
    def _install_bytes(cls, data: bytes, target: str) -> None:
        """Writes `target` through a temporary file renamed over it, so a running query keeps its copy."""
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd, staging = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".ling-docs-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(staging, 0o644)
        os.replace(staging, target)

    @classmethod
    def stamp_path(cls, target: str) -> str:
        """Where the pin a library was installed from is recorded (the archive's hash, not the file's)."""
        return os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.pin")

    @classmethod
    def missing(cls, machine: Optional[str] = None) -> List[str]:
        """
        Says what is not installed at its pin.

        Args:
            machine (Optional[str]): `aarch64` or `x86_64`; this machine's by default.

        Returns:
            List[str]: The installed names that are missing or stale; empty when all are current.
        """
        machine = machine or platform.machine()
        out = []
        for url, sha, _, name in LIBRARIES.get(machine, []):
            target = os.path.join(DOCS_LIB_DIR, name)
            stamp = cls.stamp_path(target)
            if not os.path.isfile(target) or not os.path.isfile(stamp) or open(stamp).read().strip() != sha:
                out.append(name)
        for _, sha, name in MODEL_FILES:
            target = os.path.join(DOCS_MODEL_DIR, name)
            if not os.path.isfile(target) or cls.sha256(target) != sha:
                out.append(name)
        return out

    @classmethod
    def install(cls, machine: Optional[str] = None) -> bool:
        """
        Installs whatever is missing.

        Args:
            machine (Optional[str]): `aarch64` or `x86_64`; this machine's by default.

        Returns:
            bool: True if everything is installed at its pin afterwards.
        """
        machine = machine or platform.machine()
        if machine not in LIBRARIES:
            print(f"⚠️ ling-docs has no pinned PDFium and ONNX Runtime for {machine}.")
            return False
        todo = set(cls.missing(machine))
        if not todo:
            print("✅ ling-docs: PDFium, ONNX Runtime and the embedding model are installed.")
            return True
        try:
            for url, sha, member, name in LIBRARIES[machine]:
                if name not in todo:
                    continue
                print(f"⬇️  {name} ({url.rsplit('/', 1)[-1]})...")
                with tarfile.open(fileobj=io.BytesIO(cls.fetch(url, sha)), mode="r:gz") as archive:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ValueError(f"{member} is not in {url}")
                    target = os.path.join(DOCS_LIB_DIR, name)
                    cls._install_bytes(extracted.read(), target)
                with open(cls.stamp_path(target), "w") as handle:
                    handle.write(sha + "\n")
            for path, sha, name in MODEL_FILES:
                if name not in todo:
                    continue
                url = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{path}"
                print(f"⬇️  {name} ({MODEL_REPO}, {path})...")
                cls._install_bytes(cls.fetch(url, sha), os.path.join(DOCS_MODEL_DIR, name))
        except (OSError, ValueError, tarfile.TarError) as error:
            print(f"❌ ling-docs setup failed: {error}")
            return False
        print(f"✅ ling-docs: installed in {DOCS_LIB_DIR} and {DOCS_MODEL_DIR}")
        return True

    @classmethod
    def remove(cls) -> None:
        """Removes the libraries and the model (`ling-docs` then waits for `docs setup`)."""
        for directory in (DOCS_LIB_DIR, DOCS_MODEL_DIR):
            shutil.rmtree(directory, ignore_errors=True)
