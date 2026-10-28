"""The ling-serve image carries the shared libraries the engine's image input links."""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "docker" / "ling-serve" / "Dockerfile"


def test_image_installs_the_vision_decoder_libraries():
    text = DOCKERFILE.read_text()
    for package in ("libpng16-16t64", "libwebp7", "libjpeg-turbo8"):
        assert f"        {package} \\\n" in text
