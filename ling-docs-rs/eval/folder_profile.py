"""Aggregate profile of a folder by file kind, from metadata only (lstat; no file is opened, no name is
printed): folder_profile.py <folder> [<folder> …] -> counts and bytes per kind, as JSON."""
import json
import os
import sys

SUPPORTED = {".pdf", ".md", ".txt", ".rst", ".org", ".tex", ".html", ".htm", ".docx", ".odt", ".eml", ".mbox", ".csv"}
KINDS = {
    "installer": {".deb", ".rpm", ".exe", ".msi", ".dmg", ".pkg", ".appimage", ".run", ".snap", ".flatpak"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar", ".whl", ".jar"},
    "disk image": {".iso", ".img", ".qcow2", ".vmdk", ".vdi"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".svg", ".tif", ".tiff", ".bmp"},
    "office (not v1)": {".xlsx", ".xls", ".pptx", ".ppt", ".doc", ".ods", ".odp", ".rtf", ".pages", ".numbers", ".key"},
    "model weights": {".safetensors", ".gguf", ".bin", ".pt", ".onnx", ".ckpt"},
}


def kind(name):
    ext = os.path.splitext(name.lower())[1]
    if ext in SUPPORTED:
        return "supported " + ext
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    return "other"


out = {}
for root in sys.argv[1:]:
    prof = {}
    for dp, dns, fns in os.walk(os.path.expanduser(root)):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for f in fns:
            if f.startswith("."):
                continue
            try:
                st = os.lstat(os.path.join(dp, f))
            except OSError:
                continue
            k = kind(f)
            e = prof.setdefault(k, [0, 0])
            e[0] += 1
            e[1] += st.st_size
    out[root] = {k: {"files": v[0], "mb": round(v[1] / 2**20, 1)} for k, v in sorted(prof.items())}
print(json.dumps(out, indent=1))
