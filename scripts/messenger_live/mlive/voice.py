"""The voice-note fixture: one spoken question as OGG/Opus, made once on this machine and cached.

Piper (`piper-tts`, pinned) speaks the sentence with a pinned voice and `soundfile` (libsndfile's
Opus encoder) writes the OGG/Opus file both messengers send as a voice note. Both run in a venv of
their own under the cache; nothing binary is kept in the repository.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Final

SENTENCE: Final = "What is the capital of France? Answer in one word."
EXPECTED_WORDS: Final = ("capital", "france")
EXPECTED_ANSWER: Final = "paris"
PACKAGES: Final = ("piper-tts==1.3.0", "soundfile==0.13.1")
VOICE_ROOT: Final = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/"
)
VOICE_FILES: Final = {
    "en_US-lessac-medium.onnx": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
    "en_US-lessac-medium.onnx.json": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
}
SPEAK: Final = """
import io, sys, wave
import numpy, soundfile
from piper import PiperVoice
voice = PiperVoice.load(sys.argv[1])
buffer = io.BytesIO()
with wave.open(buffer, "wb") as out:
    voice.synthesize_wav(sys.argv[2], out)
buffer.seek(0)
with wave.open(buffer) as wav:
    rate = wav.getframerate()
    samples = numpy.frombuffer(wav.readframes(wav.getnframes()), dtype=numpy.int16)
# Opus takes 8, 12, 16, 24 or 48 kHz; Piper's medium voices speak at 22.05 kHz.
target = 48000
positions = numpy.arange(0, len(samples), rate / target)
resampled = numpy.interp(positions, numpy.arange(len(samples)), samples.astype(numpy.float32)).astype(numpy.int16)
soundfile.write(sys.argv[3], resampled, target, format="OGG", subtype="OPUS")
print(round(len(resampled) / target, 2))
"""


def heard_ok(heard: str) -> bool:
    """Says whether a transcript is the fixture's sentence (its key words, any case)."""
    lower = heard.lower()
    return all(word in lower for word in EXPECTED_WORDS)


def ensure_fixture(cache: Path) -> tuple[Path, int]:
    """Returns the cached voice note and its duration in seconds, making it the first time."""
    folder = cache / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    ogg = folder / "question.ogg"
    duration = folder / "question.seconds"
    if ogg.exists() and duration.exists():
        return ogg, max(1, round(float(duration.read_text())))
    venv = cache / "tts-venv"
    python = venv / "bin" / "python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-q", *PACKAGES], check=True)
    for name, sha256 in VOICE_FILES.items():
        target = folder / name
        if not target.exists():
            urllib.request.urlretrieve(VOICE_ROOT + name, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
            target.unlink()
            raise RuntimeError(f"the Piper voice file {name} does not match its pin")
    out = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            SPEAK,
            str(folder / "en_US-lessac-medium.onnx"),
            SENTENCE,
            str(ogg),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration.write_text(out.stdout.strip())
    return ogg, max(1, round(float(out.stdout.strip())))
