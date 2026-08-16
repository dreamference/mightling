"""
Dream Brand Asset Generation and Installation for Onyx.

This module provides the OnyxBrandAssets class, which renders Dream logos and copies them over
the Onyx web server's static assets.

This is the part of the rebrand that Onyx's settings API cannot reach. `application_name`, the
custom logo and `hide_onyx_branding` all live in `ee/`, behind ENABLE_PAID_ENTERPRISE_EDITION_
FEATURES, and Dreamference does not enable a paid feature it has no licence for. The logos
themselves, though, are ordinary static files the web server hands out at /logo.png and friends --
replacing a PNG in a container you host yourself is a file change, not a licensed feature. The
window title still says Onyx, because that one *is* behind the flag.

The trade-off is impermanence: `docker cp` writes into the running container's filesystem, so an
upgrade or a `deploy install --force` restores the originals. Re-running `dream onyx configure`
puts them back, and the assets are re-rendered rather than stored in the repo so there is no
binary to keep in sync.
"""

import json
import os
import shutil
import subprocess
import tempfile
from typing import Final, List, Optional, Tuple

# Where the Next.js server keeps the images it serves from the site root.
WEB_PUBLIC_DIR: Final[str] = "/app/public"

# Dream's palette: indigo through violet, chosen to stay legible on both the light and dark
# sidebar without a separate mark for each.
BRAND_START: Final[Tuple[int, int, int]] = (79, 70, 229)
BRAND_END: Final[Tuple[int, int, int]] = (147, 51, 234)
BRAND_LIGHT: Final[Tuple[int, int, int]] = (167, 139, 250)

FONT_CANDIDATES: Final[List[str]] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]

# The sidebar mark is not one of the static files. It is an inline `SvgOnyxLogo` React component
# compiled into the JS bundle, rendered whenever no Enterprise `logoSrc` is configured -- which is
# why replacing /logo.png leaves the left panel untouched. Onyx draws it as four simple shapes on
# a 64x64 viewBox, all filled with `var(--theme-primary-05)`.
#
# Substituting the path data keeps the component, its sizing and its theme-aware fill, and changes
# only the geometry: the first path becomes a "D", the other three collapse to a degenerate path
# that renders nothing. The counter in the "D" is a second subpath wound in the opposite direction,
# so the default nonzero fill rule punches it out without needing a fill-rule attribute.
ONYX_LOGO_PATHS: Final[dict] = {
    "M10.4014 13.25L18.875 32L10.3852 50.75L2 32L10.4014 13.25Z":
        "M14 8H34A24 24 0 0 1 34 56H14ZM22 16V48H34A16 16 0 0 0 34 16H22Z",
    "M53.5264 13.25L62 32L53.5102 50.75L45.125 32L53.5264 13.25Z": "M0 0Z",
    "M32 45.125L50.75 53.5625L32 62L13.25 53.5625L32 45.125Z": "M0 0Z",
    "M32 2L50.75 10.4375L32 18.875L13.25 10.4375L32 2Z": "M0 0Z",
}

# Where Next.js keeps the compiled bundles the above lives in.
WEB_BUILD_DIR: Final[str] = "/app/.next"



class OnyxBrandAssets:
    """
    Renders Dream logo assets and installs them into a running Onyx web server container.
    """

    @classmethod
    def install(cls, container: Optional[str] = None) -> bool:
        """
        Renders the Dream assets and copies them over the web server's static files.

        Args:
            container (Optional[str]): Onyx web server container name; discovered if omitted.

        Returns:
            bool: True if every asset was copied into the container.
        """
        target = container or cls.find_web_container()
        if not target:
            return False

        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            print("⚠️  Pillow is not installed — skipping logo replacement.")
            return False

        workdir = tempfile.mkdtemp(prefix="dream-brand-")
        try:
            assets = cls.render(workdir)
            for filename, path in assets.items():
                result = subprocess.run(
                    ["docker", "cp", path, f"{target}:{WEB_PUBLIC_DIR}/{filename}"],
                    capture_output=True, text=True, timeout=60, check=False,
                )
                if result.returncode != 0:
                    print(f"⚠️  Could not replace {filename}: {result.stderr.strip()[:160]}")
                    return False
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"⚠️  Could not install Dream logos: {exc}")
            return False
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        # The sidebar mark lives in the bundle, not in public/, so it needs its own pass.
        cls.patch_inline_logo(target)
        return True

    @classmethod
    def patch_inline_logo(cls, container: str) -> int:
        """
        Rewrites the inline sidebar mark in the compiled frontend bundles.

        Runs the substitution inside the container with node rather than copying files out and
        back: the mark is duplicated across a couple of dozen chunks by the bundler, and a
        rewrite in place is one exec instead of fifty.

        Args:
            container (str): Onyx web server container name.

        Returns:
            int: Number of bundle files changed. Zero means Onyx's mark was not found, which is
                what a version that redrew its logo would look like.
        """
        script = (
            "const fs=require('fs'),path=require('path');"
            f"const MAP={json.dumps(ONYX_LOGO_PATHS)};"
            "let changed=0;"
            "const walk=(d,dep)=>{if(dep>8)return;let e=[];"
            "try{e=fs.readdirSync(d,{withFileTypes:true})}catch(x){return}"
            "for(const f of e){const p=path.join(d,f.name);"
            "if(f.isDirectory()){if(!/node_modules|cache/.test(p))walk(p,dep+1);continue}"
            "if(!/\\.(js|mjs)$/.test(f.name))continue;"
            "let s='';try{s=fs.readFileSync(p,'utf8')}catch(x){continue}"
            "const o=s;for(const k in MAP)if(s.includes(k))s=s.split(k).join(MAP[k]);"
            "if(s!==o){try{fs.writeFileSync(p,s);changed++}catch(x){}}}};"
            f"walk({json.dumps(WEB_BUILD_DIR)},0);console.log(changed);"
        )
        try:
            result = subprocess.run(
                ["docker", "exec", container, "node", "-e", script],
                capture_output=True, text=True, timeout=180, check=False,
            )
            return int(result.stdout.strip().splitlines()[-1]) if result.returncode == 0 else 0
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return 0

    @classmethod
    def find_web_container(cls) -> Optional[str]:
        """
        Locates the running Onyx web server container.

        Returns:
            Optional[str]: Container name, or None if it is not running.
        """
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", "name=web_server", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        names = [n for n in result.stdout.split() if "onyx" in n]
        return names[0] if names else None

    @classmethod
    def render(cls, workdir: str) -> dict:
        """
        Draws every Dream asset at the dimensions Onyx ships.

        Sizes match the originals because the frontend lays them out against those aspect ratios;
        a square wordmark or an oversized mark reflows the sidebar rather than simply looking
        different.

        Args:
            workdir (str): Directory to write the rendered files into.

        Returns:
            dict: Mapping of served filename to the path it was rendered at.
        """
        assets = {
            "logo.png": cls._mark(workdir, "logo.png", (400, 400), dark=False),
            "logo-dark.png": cls._mark(workdir, "logo-dark.png", (400, 400), dark=True),
            "logotype.png": cls._wordmark(workdir, "logotype.png", (2640, 733), dark=False),
            "logotype-dark.png": cls._wordmark(workdir, "logotype-dark.png", (720, 320), dark=True),
            "logo.svg": cls._svg(workdir),
            "onyx.ico": cls._favicon(workdir),
        }
        return assets

    @classmethod
    def _font(cls, size: int):
        """
        Loads a bold sans font at the requested size, falling back to PIL's built-in.

        Args:
            size (int): Point size to load.

        Returns:
            ImageFont: The loaded font.
        """
        from PIL import ImageFont

        for path in FONT_CANDIDATES:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    @classmethod
    def _gradient_square(cls, size: Tuple[int, int], dark: bool):
        """
        Builds the rounded gradient tile both the mark and the favicon are cut from.

        Args:
            size (Tuple[int, int]): Tile dimensions.
            dark (bool): Whether to render the lighter palette for dark backgrounds.

        Returns:
            Image: An RGBA tile with rounded corners.
        """
        from PIL import Image, ImageDraw

        width, height = size
        start = BRAND_LIGHT if dark else BRAND_START
        end = BRAND_START if dark else BRAND_END

        gradient = Image.new("RGBA", size)
        draw = ImageDraw.Draw(gradient)
        for y in range(height):
            ratio = y / max(height - 1, 1)
            draw.line(
                [(0, y), (width, y)],
                fill=tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3)) + (255,),
            )

        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, width - 1, height - 1], radius=int(min(size) * 0.23), fill=255
        )
        gradient.putalpha(mask)
        return gradient

    @classmethod
    def _mark(cls, workdir: str, filename: str, size: Tuple[int, int], dark: bool) -> str:
        """
        Renders the square app mark: a "D" monogram on the gradient tile.

        Args:
            workdir (str): Output directory.
            filename (str): File to write.
            size (Tuple[int, int]): Output dimensions.
            dark (bool): Whether to use the dark-background palette.

        Returns:
            str: Path to the rendered file.
        """
        from PIL import ImageDraw

        image = cls._gradient_square(size, dark)
        draw = ImageDraw.Draw(image)
        font = cls._font(int(size[1] * 0.62))
        box = draw.textbbox((0, 0), "D", font=font)
        draw.text(
            ((size[0] - (box[2] - box[0])) / 2 - box[0],
             (size[1] - (box[3] - box[1])) / 2 - box[1]),
            "D", font=font, fill=(255, 255, 255, 255),
        )
        path = os.path.join(workdir, filename)
        image.save(path)
        return path

    @classmethod
    def _wordmark(cls, workdir: str, filename: str, size: Tuple[int, int], dark: bool) -> str:
        """
        Renders the horizontal "Dream" wordmark on a transparent background.

        Args:
            workdir (str): Output directory.
            filename (str): File to write.
            size (Tuple[int, int]): Output dimensions.
            dark (bool): Whether to render light text for dark backgrounds.

        Returns:
            str: Path to the rendered file.
        """
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        colour = (237, 233, 254, 255) if dark else BRAND_START + (255,)

        # Fit the word to the canvas rather than guessing a point size: the two wordmark files
        # ship at very different aspect ratios.
        point = int(size[1] * 0.7)
        while point > 8:
            font = cls._font(point)
            box = draw.textbbox((0, 0), "Dream", font=font)
            if box[2] - box[0] <= size[0] * 0.88 and box[3] - box[1] <= size[1] * 0.72:
                break
            point = int(point * 0.92)

        box = draw.textbbox((0, 0), "Dream", font=font)
        draw.text(
            ((size[0] - (box[2] - box[0])) / 2 - box[0],
             (size[1] - (box[3] - box[1])) / 2 - box[1]),
            "Dream", font=font, fill=colour,
        )
        path = os.path.join(workdir, filename)
        image.save(path)
        return path

    @classmethod
    def _svg(cls, workdir: str) -> str:
        """
        Writes the vector mark, kept on the 56x56 viewBox Onyx's own logo.svg uses.

        Args:
            workdir (str): Output directory.

        Returns:
            str: Path to the rendered file.
        """
        start = "#%02x%02x%02x" % BRAND_START
        end = "#%02x%02x%02x" % BRAND_END
        svg = f'''<svg viewBox="0 0 56 56" fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <linearGradient id="dream" x1="0" y1="0" x2="0" y2="56" gradientUnits="userSpaceOnUse">
            <stop stop-color="{start}"/>
            <stop offset="1" stop-color="{end}"/>
        </linearGradient>
    </defs>
    <rect width="56" height="56" rx="13" fill="url(#dream)"/>
    <path d="M18 14h9.5c8 0 13.5 5.5 13.5 14s-5.5 14-13.5 14H18V14zm7.5 6.5v15h2c4.6 0 7.5-2.9
             7.5-7.5s-2.9-7.5-7.5-7.5h-2z" fill="#ffffff"/>
</svg>
'''
        path = os.path.join(workdir, "logo.svg")
        with open(path, "w") as handle:
            handle.write(svg)
        return path

    @classmethod
    def _favicon(cls, workdir: str) -> str:
        """
        Renders the browser-tab icon at the sizes a favicon is expected to carry.

        Args:
            workdir (str): Output directory.

        Returns:
            str: Path to the rendered file.
        """
        from PIL import Image

        source = Image.open(cls._mark(workdir, "_favicon_src.png", (256, 256), dark=False))
        path = os.path.join(workdir, "onyx.ico")
        source.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
        return path
