"""Build a synthetic Downloads folder (CC0): installers, archives, a disk image, videos, photos, and
the corpus's PDFs and office files with browser-style duplicates ("x (1).pdf"), plus two files whose
extension lies. Large files are random bytes behind the right magic number, so nothing here is
compressible or sparse. make_downloads.py <corpus> <out>"""
import os
import random
import shutil
import sys

corpus, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
rng = random.Random(20261007)
BLOCK = os.urandom(1 << 20)  # one random MiB, rotated per write so blocks differ


def blob(name, mb, magic=b""):
    path = os.path.join(out, name)
    if os.path.exists(path) and os.path.getsize(path) >= mb << 20:
        return
    with open(path, "wb") as f:
        f.write(magic)
        for i in range(mb):
            k = (i * 7919) % (1 << 20)
            f.write(BLOCK[k:] + BLOCK[:k])


big = [  # (name, MiB, magic)
    ("ubuntu-24.04.3-desktop-arm64.iso", 3600, b"\x00" * 32768 + b"\x01CD001"),
    ("LibreOffice_25.8_Linux_aarch64_deb.tar.gz", 210, b"\x1f\x8b"),
    ("code_1.104_arm64.deb", 110, b"!<arch>\n"),
    ("Docker Desktop Installer.exe", 620, b"MZ"),
    ("Firefox 143.0.dmg", 140, b"koly"),
    ("obsidian-1.9.12-arm64.AppImage", 130, b"\x7fELF"),
    ("zoom_arm64.rpm", 190, b"\xed\xab\xee\xdb"),
    ("conference-keynote.mp4", 1400, b"\x00\x00\x00\x20ftypisom"),
    ("screen-recording-2025-09-12.mov", 900, b"\x00\x00\x00\x14ftypqt  "),
    ("family-video.mkv", 700, b"\x1aE\xdf\xa3"),
    ("dataset-export.zip", 340, b"PK\x03\x04"),
    ("photos-backup-2024.zip", 520, b"PK\x03\x04"),
    ("linux-6.17.tar.xz", 150, b"\xfd7zXZ\x00"),
    ("node_modules_snapshot.7z", 95, b"7z\xbc\xaf\x27\x1c"),
    ("model-q4.gguf", 800, b"GGUF"),
]
for name, mb, magic in big:
    blob(name, mb, magic)
for i in range(240):  # phone photos and screenshots
    blob(f"IMG_{4000 + i}.jpg" if i % 3 else f"Screenshot {i}.png", rng.randint(1, 5),
         b"\xff\xd8\xff\xe0" if i % 3 else b"\x89PNG\r\n\x1a\n")
for i in range(60):
    blob(f"invoice-template-{i}.xlsx", 1, b"PK\x03\x04")
# Documents downloaded more than once, the way browsers name the copies.
for sub in ("pdf", "docx", "csv"):
    for f in sorted(os.listdir(os.path.join(corpus, sub))):
        stem, ext = os.path.splitext(f)
        copies = 3 if sub == "pdf" else 1
        for c in range(copies):
            name = f if c == 0 else f"{stem} ({c}){ext}"
            shutil.copyfile(os.path.join(corpus, sub, f), os.path.join(out, name))
# Extensions that lie: a saved web page named .pdf, and a binary named .txt.
open(os.path.join(out, "statement.pdf"), "w").write("<!doctype html><html><body>Session expired</body></html>")
blob("license-key.txt", 2, b"\x00\x01\x02")
total = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out))
print(len(os.listdir(out)), "files,", round(total / 2**30, 2), "GiB")
