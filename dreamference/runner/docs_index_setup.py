"""`ling-admin docs setup`: what `ling-docs` loads at run time
(specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §7.1, §7.3).

This module provides the DocsIndexSetup class. `ling-docs` opens no socket at any step, so the
three things it needs that are not in its binary are fetched here, once, each pinned by URL and
SHA-256:

- PDFium, the pdfium-binaries build `chromium/8076` (BSD-3), the one Phase 0 measured
  (its `libpdfium.so` is byte-identical to the copy in the measured pypdfium2 5.14);
- ONNX Runtime 1.30.0 (MIT), CPU only;
- the embedding model, `Snowflake/snowflake-arctic-embed-m-v2.0`'s int8 ONNX export and its
  tokenizer (Apache-2.0), at a pinned revision;
- for OCR (spec §20): RapidOCR 3.9.2's PP-OCR ONNX models (Apache-2.0) as RapidOCR publishes them,
  Tesseract's script detection model `osd.traineddata` from `tessdata_fast` at a pinned commit
  (Apache-2.0), and Tesseract 5.3.4 and Leptonica 1.82.0 as Ubuntu 24.04 builds them
  (`libtesseract5`, Apache-2.0; `liblept5`, Leptonica's BSD-style licence), each package pinned by
  the SHA-256 Ubuntu's signed index gives and unpacked with `dpkg-deb`. Their own dependencies
  (libarchive, libcurl's library, libgomp and the image codecs) are the system's.

They go where the binary looks for them, beside it: `<install>/lib/ling-docs/`,
`<install>/models/snowflake-arctic-embed-m-v2.0-int8/` and `<install>/models/rapidocr-3.9.2/`
(`config::lib_dir`, `config::model_dir`, `config::ocr_dir` in `ling-docs-rs/src/config.rs`). A file
already there with the pinned hash is not fetched again. OCR is optional: a failed OCR download
leaves the rest installed, and `ling-docs status` says what OCR lacks.
"""

import hashlib
import io
import os
import pathlib
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from typing import Final

from dreamference.runner.codex_branded_builder import INSTALL_DIR

DOCS_LIB_DIR: Final[str] = os.path.join(INSTALL_DIR, "lib", "ling-docs")
DOCS_MODEL_NAME: Final[str] = "snowflake-arctic-embed-m-v2.0-int8"
DOCS_MODEL_DIR: Final[str] = os.path.join(INSTALL_DIR, "models", DOCS_MODEL_NAME)

PDFIUM_RELEASE: Final[str] = "chromium/8076"
ONNXRUNTIME_VERSION: Final[str] = "1.30.0"
MODEL_REPO: Final[str] = "Snowflake/snowflake-arctic-embed-m-v2.0"
MODEL_REVISION: Final[str] = "95c2741480856aa9666782eb4afe11959938017f"

# (archive URL, its SHA-256, member to extract, installed name), per machine.
LIBRARIES: Final[dict[str, list[tuple[str, str, str, str]]]] = {
    "aarch64": [
        (
            f"https://github.com/bblanchon/pdfium-binaries/releases/download/{PDFIUM_RELEASE.replace('/', '%2F')}/pdfium-linux-arm64.tgz",
            "d7247b33ae5545615a5e877235dd97afc879e3a8805689684f528cae3339d352",
            "lib/libpdfium.so",
            "libpdfium.so",
        ),
        (
            f"https://github.com/microsoft/onnxruntime/releases/download/v{ONNXRUNTIME_VERSION}/onnxruntime-linux-aarch64-{ONNXRUNTIME_VERSION}.tgz",
            "e16a27a8ed330bbc698df7330b0cf56e722f354e3bcc92118682c74ef3c3e3da",
            f"onnxruntime-linux-aarch64-{ONNXRUNTIME_VERSION}/lib/libonnxruntime.so.{ONNXRUNTIME_VERSION}",
            "libonnxruntime.so",
        ),
    ],
    "x86_64": [
        (
            f"https://github.com/bblanchon/pdfium-binaries/releases/download/{PDFIUM_RELEASE.replace('/', '%2F')}/pdfium-linux-x64.tgz",
            "d9d67bc40af03aef4fe28a60b19b1086f28ace019c8c9caf19cb7fe3d14ceca3",
            "lib/libpdfium.so",
            "libpdfium.so",
        ),
        (
            f"https://github.com/microsoft/onnxruntime/releases/download/v{ONNXRUNTIME_VERSION}/onnxruntime-linux-x64-{ONNXRUNTIME_VERSION}.tgz",
            "a5ed5a3cac51fbb2e90da632ae43d19212faaa20e76484e62bcb7c23ddb3b3fd",
            f"onnxruntime-linux-x64-{ONNXRUNTIME_VERSION}/lib/libonnxruntime.so.{ONNXRUNTIME_VERSION}",
            "libonnxruntime.so",
        ),
    ],
}

RAPIDOCR_VERSION: Final[str] = "3.9.2"
OCR_DIR: Final[str] = str(pathlib.Path(INSTALL_DIR) / "models" / f"rapidocr-{RAPIDOCR_VERSION}")
_MODELSCOPE: Final[str] = (
    f"https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v{RAPIDOCR_VERSION}/onnx"
)
TESSDATA_COMMIT: Final[str] = "87416418657359cb625c412a48b6e1d6d41c29bd"

# (URL, its SHA-256, path under OCR_DIR): the models of the route Phase 0 measured (§15.7), the
# hashes RapidOCR 3.9.2's `default_models.yaml` records, checked on download.
OCR_FILES: Final[list[tuple[str, str, str]]] = [
    (
        f"{_MODELSCOPE}/PP-OCRv6/det/PP-OCRv6_det_small.onnx",
        "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
        "PP-OCRv6_det_small.onnx",
    ),
    (
        f"{_MODELSCOPE}/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
        "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
        "PP-OCRv6_rec_small.onnx",
    ),
    (
        f"{_MODELSCOPE}/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    ),
    (
        f"{_MODELSCOPE}/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx",
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
        "ch_PP-OCRv5_det_mobile.onnx",
    ),
    (
        f"{_MODELSCOPE}/PP-OCRv5/rec/eslav_PP-OCRv5_rec_mobile.onnx",
        "08705d6721849b1347d26187f15a5e362c431963a2a62bfff4feac578c489aab",
        "eslav_PP-OCRv5_rec_mobile.onnx",
    ),
    (
        f"{_MODELSCOPE}/PP-OCRv5/rec/korean_PP-OCRv5_rec_mobile.onnx",
        "cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b",
        "korean_PP-OCRv5_rec_mobile.onnx",
    ),
    (
        f"{_MODELSCOPE}/PP-OCRv5/rec/arabic_PP-OCRv5_rec_mobile.onnx",
        "c1192e632d0baa9146ae5b756a0e635e3dc63c1733737ebfd1629e87144e9295",
        "arabic_PP-OCRv5_rec_mobile.onnx",
    ),
    (
        f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{TESSDATA_COMMIT}/osd.traineddata",
        "9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff",
        "tessdata/osd.traineddata",
    ),
]

# (package URL, its SHA-256 from Ubuntu noble's signed `Packages` index, member, installed name).
_UBUNTU: Final[dict[str, str]] = {
    "aarch64": "http://ports.ubuntu.com/ubuntu-ports/pool/universe",
    "x86_64": "http://archive.ubuntu.com/ubuntu/pool/universe",
}
TESSERACT_PACKAGES: Final[dict[str, list[tuple[str, str, str, str]]]] = {
    "aarch64": [
        (
            f"{_UBUNTU['aarch64']}/l/leptonlib/liblept5_1.82.0-3build4_arm64.deb",
            "ade7e6886511ab34b36ce5207ce4f00f1b030a6939fcdac4a9c1d6eaf3d83284",
            "./usr/lib/aarch64-linux-gnu/liblept.so.5.0.4",
            "liblept.so.5",
        ),
        (
            f"{_UBUNTU['aarch64']}/t/tesseract/libtesseract5_5.3.4-1build5_arm64.deb",
            "e0c53e9b7f5b42f0250b288d5a4d3590ea5bf09ab5e937a8b05a0fbc56d63eea",
            "./usr/lib/aarch64-linux-gnu/libtesseract.so.5.0.3",
            "libtesseract.so.5",
        ),
    ],
    "x86_64": [
        (
            f"{_UBUNTU['x86_64']}/l/leptonlib/liblept5_1.82.0-3build4_amd64.deb",
            "c2bb81d58f032b09917c0d50994cce905287a1596304a4e99681a03601456ea8",
            "./usr/lib/x86_64-linux-gnu/liblept.so.5.0.4",
            "liblept.so.5",
        ),
        (
            f"{_UBUNTU['x86_64']}/t/tesseract/libtesseract5_5.3.4-1build5_amd64.deb",
            "a38438115dc203b5abc1e6a6b24eb5273ba82161a11faa2f619bf5f7622efb14",
            "./usr/lib/x86_64-linux-gnu/libtesseract.so.5.0.3",
            "libtesseract.so.5",
        ),
    ],
}

# (path in the model repository, its SHA-256, installed name).
MODEL_FILES: Final[list[tuple[str, str, str]]] = [
    (
        "onnx/model_int8.onnx",
        "03d923bb1850ebdccb068e2f3abd8aa43fe81c50d07d037ef103fe3d0fb78e3b",
        "model.onnx",
    ),
    (
        "tokenizer.json",
        "f1cc44ad7faaeec47241864835473fd5403f2da94673f3f764a77ebcb0a803ec",
        "tokenizer.json",
    ),
]


class DocsIndexSetup:
    """Installs PDFium, ONNX Runtime and the embedding model for `ling-docs`."""

    @classmethod
    def sha256(cls, path: str) -> str:
        """Hashes a file.

        Args:
            path (str): The file.

        Returns:
            str: Its SHA-256, hex.
        """
        digest = hashlib.sha256()
        with pathlib.Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()

    @classmethod
    def fetch(cls, url: str, expected: str) -> bytes:
        """Downloads a pinned file and checks its hash.

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
        pathlib.Path(os.path.dirname(target)).mkdir(exist_ok=True, parents=True)
        fd, staging = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".ling-docs-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        pathlib.Path(staging).chmod(0o644)
        pathlib.Path(staging).replace(target)

    @classmethod
    def _say(cls, message: str) -> None:
        """One line of the setup's progress for the person running it."""
        print(message)

    @classmethod
    def stamp_path(cls, target: str) -> str:
        """Where the pin a library was installed from is recorded (the archive's hash, not the file's)."""
        return os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.pin")

    @classmethod
    def missing(cls, machine: str | None = None) -> list[str]:
        """Says what is not installed at its pin.

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
            if (
                not pathlib.Path(target).is_file()
                or not pathlib.Path(stamp).is_file()
                or pathlib.Path(stamp).open().read().strip() != sha
            ):
                out.append(name)
        for _, sha, name in MODEL_FILES:
            target = os.path.join(DOCS_MODEL_DIR, name)
            if not pathlib.Path(target).is_file() or cls.sha256(target) != sha:
                out.append(name)
        return out + cls.missing_ocr(machine)

    @classmethod
    def missing_ocr(cls, machine: str | None = None) -> list[str]:
        """Says which of OCR's files are not installed at their pins (spec §20).

        Args:
            machine (Optional[str]): `aarch64` or `x86_64`; this machine's by default.

        Returns:
            List[str]: The installed names that are missing or stale; empty when all are current.
        """
        machine = machine or platform.machine()
        out = []
        for _, sha, _, name in TESSERACT_PACKAGES.get(machine, []):
            target = pathlib.Path(DOCS_LIB_DIR) / name
            stamp = pathlib.Path(cls.stamp_path(str(target)))
            if not target.is_file() or not stamp.is_file() or stamp.read_text().strip() != sha:
                out.append(name)
        for _, sha, name in OCR_FILES:
            target = pathlib.Path(OCR_DIR) / name
            if not target.is_file() or cls.sha256(str(target)) != sha:
                out.append(name)
        return out

    @classmethod
    def deb_member(cls, package: bytes, member: str) -> bytes:
        """One file of a Debian package, through `dpkg-deb --fsys-tarfile`.

        The package's data archive is zstd-compressed, which Python's tarfile cannot read
        before 3.14.

        Args:
            package (bytes): The `.deb`.
            member (str): The file's path in it (`./usr/lib/…`).

        Returns:
            bytes: The file.

        Raises:
            ValueError: dpkg-deb is missing or failed, or the package has no such file.
        """
        dpkg = shutil.which("dpkg-deb")
        if dpkg is None:
            raise ValueError(
                "OCR's Tesseract packages need dpkg-deb to unpack (a Debian or Ubuntu system)"
            )
        result = subprocess.run(
            [dpkg, "--fsys-tarfile", "/dev/stdin"], input=package, capture_output=True, check=False
        )
        if result.returncode != 0:
            raise ValueError(
                f"dpkg-deb could not read the package: {result.stderr.decode(errors='replace').strip()}"
            )
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"{member} is not in the package")
            return extracted.read()

    @classmethod
    def install_ocr(cls, machine: str, todo: set) -> bool:
        """Installs OCR's missing files: the Tesseract and Leptonica libraries, the models.

        Args:
            machine (str): `aarch64` or `x86_64`.
            todo (set): The installed names to fetch.

        Returns:
            bool: True if OCR is installed at its pins afterwards.
        """
        try:
            for url, sha, member, name in TESSERACT_PACKAGES[machine]:
                if name not in todo:
                    continue
                cls._say(f"⬇️  {name} ({url.rsplit('/', 1)[-1]})...")
                target = pathlib.Path(DOCS_LIB_DIR) / name
                cls._install_bytes(cls.deb_member(cls.fetch(url, sha), member), str(target))
                pathlib.Path(cls.stamp_path(str(target))).write_text(sha + "\n")
            for url, sha, name in OCR_FILES:
                if name not in todo:
                    continue
                cls._say(f"⬇️  {name} (OCR)...")
                cls._install_bytes(cls.fetch(url, sha), str(pathlib.Path(OCR_DIR) / name))
        except (OSError, ValueError, tarfile.TarError) as error:
            cls._say(
                f"⚠️ ling-docs OCR setup failed (the index works without it, scanned pages stay unread): {error}"
            )
            return False
        return True

    @classmethod
    def install(cls, machine: str | None = None) -> bool:
        """Installs whatever is missing.

        Args:
            machine (Optional[str]): `aarch64` or `x86_64`; this machine's by default.

        Returns:
            bool: True if everything is installed at its pin afterwards.
        """
        machine = machine or platform.machine()
        if machine not in LIBRARIES:
            cls._say(f"⚠️ ling-docs has no pinned PDFium and ONNX Runtime for {machine}.")
            return False
        todo = set(cls.missing(machine))
        if not todo:
            cls._say(
                "✅ ling-docs: PDFium, ONNX Runtime, the embedding model and OCR are installed."
            )
            return True
        try:
            for url, sha, member, name in LIBRARIES[machine]:
                if name not in todo:
                    continue
                cls._say(f"⬇️  {name} ({url.rsplit('/', 1)[-1]})...")
                with tarfile.open(fileobj=io.BytesIO(cls.fetch(url, sha)), mode="r:gz") as archive:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ValueError(f"{member} is not in {url}")
                    target = os.path.join(DOCS_LIB_DIR, name)
                    cls._install_bytes(extracted.read(), target)
                with pathlib.Path(cls.stamp_path(target)).open("w") as handle:
                    handle.write(sha + "\n")
            for path, sha, name in MODEL_FILES:
                if name not in todo:
                    continue
                url = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{path}"
                cls._say(f"⬇️  {name} ({MODEL_REPO}, {path})...")
                cls._install_bytes(cls.fetch(url, sha), os.path.join(DOCS_MODEL_DIR, name))
        except (OSError, ValueError, tarfile.TarError) as error:
            cls._say(f"❌ ling-docs setup failed: {error}")
            return False
        # OCR is optional: its failure is a warning, and the rest stays installed.
        ocr = cls.install_ocr(machine, todo) if machine in TESSERACT_PACKAGES else False
        cls._say(
            f"✅ ling-docs: installed in {DOCS_LIB_DIR} and {DOCS_MODEL_DIR}"
            + (f", OCR in {OCR_DIR}" if ocr else " (without OCR)")
        )
        return True

    @classmethod
    def remove(cls) -> None:
        """Removes the libraries and the model (`ling-docs` then waits for `docs setup`)."""
        for directory in (DOCS_LIB_DIR, DOCS_MODEL_DIR, OCR_DIR):
            shutil.rmtree(directory, ignore_errors=True)
