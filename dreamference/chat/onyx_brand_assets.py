"""
Puffin Brand Asset Generation and Installation for Onyx.

This module provides the OnyxBrandAssets class, which renders Puffin logos and copies them over
the Onyx web server's static assets.

This is the part of the rebrand that Onyx's settings API cannot reach. `application_name`, the
custom logo and `hide_onyx_branding` all live in `ee/`, behind ENABLE_PAID_ENTERPRISE_EDITION_
FEATURES, and Dreamference does not enable a paid feature it has no licence for. The logos
themselves, though, are ordinary static files the web server hands out at /logo.png and friends --
replacing a PNG in a container you host yourself is a file change, not a licensed feature. The
window title still says Onyx, because that one *is* behind the flag.

The trade-off is impermanence: `docker cp` writes into the running container's filesystem, so an
upgrade or a `deploy install --force` restores the originals. Re-running `puffin-admin puffin configure`
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

# Puffin's palette. `TIFFANY_BLUE` is the brand colour's single definition -- `onyx_ui_overrides.py`
# imports it from here for the selected sidebar row and the unread badge, so the favicon and the UI
# cannot drift apart.
#
# The mark is a flat fill rather than a gradient, and that is the point: a gradient means only one
# scanline of the tile is actually Tiffany, so the favicon never quite matched the sidebar row it
# sits beside. One colour, used everywhere, is the whole brief.
TIFFANY_BLUE: Final[str] = "#0ABAB5"
BRAND_START: Final[Tuple[int, int, int]] = (10, 186, 181)

# The wordmark on a dark background: Tiffany lightened to near-white rather than the mark's pale
# end, which is too close to the sidebar tint to read as type.
BRAND_WORDMARK_DARK: Final[Tuple[int, int, int, int]] = (206, 240, 238, 255)

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
# only the geometry: the first path becomes a "P", the other three collapse to a degenerate path
# that renders nothing. The counter in the "P" is a second subpath wound in the opposite direction,
# so the default nonzero fill rule punches it out without needing a fill-rule attribute.
ONYX_LOGO_PATHS: Final[dict] = {
    "M10.4014 13.25L18.875 32L10.3852 50.75L2 32L10.4014 13.25Z":
        "M14 8H34A14 14 0 0 1 34 36H22V56H14ZM22 16V28H34A6 6 0 0 0 34 16H22Z",
    "M53.5264 13.25L62 32L53.5102 50.75L45.125 32L53.5264 13.25Z": "M0 0Z",
    "M32 45.125L50.75 53.5625L32 62L13.25 53.5625L32 45.125Z": "M0 0Z",
    "M32 2L50.75 10.4375L32 18.875L13.25 10.4375L32 2Z": "M0 0Z",
}

# Where Next.js keeps the compiled bundles the above lives in.
WEB_BUILD_DIR: Final[str] = "/app/.next"

# The word beside the mark is a *second* inline SVG -- four letter paths spelling "onyx" on a
# 152x64 viewBox -- not the `appName` text and not /logotype.png, which the bundle never
# references at all. `Logo` lays the two components side by side in a flex row.
#
# Replacing letterforms means replacing outlines: this is "Puffin" set in DejaVu Sans Bold,
# converted from the font's glyph outlines to a single path with fontTools and fitted to the same
# viewBox. It is stored as a constant rather than generated at runtime so the rebrand needs no
# font-tooling dependency, and because the geometry is fixed once chosen.
PUFFIN_WORDMARK_PATH: Final[str] = (
    "M12.95 17.96H25.25Q30.73 17.96 33.67 20.4Q36.6 22.83 36.6 27.33Q36.6 31.86 33.67 34.29Q30.73 36.72 25.25 36.72H20.36V46.69H12.95ZM20.36 23.33V31.36H24.46Q26.61 31.36 27.79 30.31Q28.96 29.26 28.96 27.33Q28.96 25.41 27.79 24.37Q26.61 23.33 24.46 23.33ZM41.3 38.3V25.14H48.22V27.3Q48.22 29.05 48.2 31.69Q48.18 34.34 48.18 35.22Q48.18 37.82 48.32 38.97Q48.45 40.11 48.78 40.63Q49.2 41.3 49.89 41.67Q50.57 42.04 51.46 42.04Q53.61 42.04 54.84 40.38Q56.07 38.73 56.07 35.78V25.14H62.96V46.69H56.07V43.57Q54.52 45.46 52.77 46.36Q51.03 47.25 48.93 47.25Q45.2 47.25 43.25 44.96Q41.3 42.67 41.3 38.3ZM83.76 16.75V21.27H79.95Q78.49 21.27 77.91 21.8Q77.34 22.33 77.34 23.64V25.14H83.23V30.07H77.34V46.69H70.45V30.07H67.02V25.14H70.45V23.64Q70.45 20.12 72.41 18.43Q74.37 16.75 78.49 16.75ZM100.91 16.75V21.27H97.1Q95.64 21.27 95.06 21.8Q94.48 22.33 94.48 23.64V25.14H100.37V30.07H94.48V46.69H87.59V30.07H84.17V25.14H87.59V23.64Q87.59 20.12 89.56 18.43Q91.52 16.75 95.64 16.75ZM103.87 25.14H110.76V46.69H103.87ZM103.87 16.75H110.76V22.37H103.87ZM139.05 33.57V46.69H132.12V44.56V36.65Q132.12 33.86 132 32.8Q131.87 31.74 131.56 31.24Q131.16 30.57 130.47 30.19Q129.77 29.82 128.89 29.82Q126.73 29.82 125.5 31.48Q124.27 33.14 124.27 36.09V46.69H117.38V25.14H124.27V28.3Q125.83 26.41 127.58 25.52Q129.33 24.62 131.45 24.62Q135.18 24.62 137.12 26.91Q139.05 29.2 139.05 33.57Z"
)

# The four "onyx" letters, matched on a prefix because the "o" path is 1.3 KB and only its head is
# needed to identify it. The first becomes the Puffin wordmark; the rest collapse to nothing.
ONYX_WORDMARK_PREFIXES: Final[dict] = {
    "M19.1795 51.2136C15.6695 51.2136 12.4353 50.3862": "PUFFIN",
    "M42.6413 50.4614V12.4031H50.6891V17.7433L55.5028 12.7039": "HIDE",
    "M82.3035 64V56.0273H89.9753C91.2288 56.0273 92.2066 55.7264": "HIDE",
    "M115.657 50.4614L129.045 31.2066L116.033 12.4031H125.435": "HIDE",
}

# The name beside the mark is text, not an image: the frontend renders
# `application_name?.trim() || "Onyx"`, and `application_name` is the Enterprise whitelabel
# setting, so without a licence every render falls through to the literal. These substitutions
# change that fallback -- a default string in the community-edition bundle -- rather than enabling
# the paid feature; ENABLE_PAID_ENTERPRISE_EDITION_FEATURES stays off and no `ee/` code runs.
#
# Deliberately surgical. The bundle uses the bare string "Onyx" for unrelated things -- the author
# of a builtin skill, the fallback owner of a shared agent -- and rewriting those would state
# something untrue rather than rebrand anything.
ONYX_APP_NAME_STRINGS: Final[dict] = {
    'application_name?.trim()||"Onyx"': 'application_name?.trim()||"Puffin"',
    '.trim()}return"Onyx"': '.trim()}return"Puffin"',
}



class OnyxBrandAssets:
    """
    Renders Puffin logo assets and installs them into a running Onyx web server container.
    """

    @classmethod
    def install(cls, container: Optional[str] = None) -> bool:
        """
        Renders the Puffin assets and copies them over the web server's static files.

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
            print(f"⚠️  Could not install Puffin logos: {exc}")
            return False
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        # The sidebar mark lives in the bundle, not in public/, so it needs its own pass.
        cls.patch_inline_logo(target)
        return True

    @classmethod
    def patch_inline_logo(cls, container: str) -> int:
        """
        Rewrites the inline sidebar mark and the app-name fallback in the compiled bundles.

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
            f"const MAP={json.dumps({**ONYX_LOGO_PATHS, **ONYX_APP_NAME_STRINGS})};"
            "let changed=0;"
            "const walk=(d,dep)=>{if(dep>8)return;let e=[];"
            "try{e=fs.readdirSync(d,{withFileTypes:true})}catch(x){return}"
            "for(const f of e){const p=path.join(d,f.name);"
            "if(f.isDirectory()){if(!/node_modules|cache/.test(p))walk(p,dep+1);continue}"
            "if(!/\\.(js|mjs)$/.test(f.name))continue;"
            "let s='';try{s=fs.readFileSync(p,'utf8')}catch(x){continue}"
            "const o=s;for(const k in MAP)if(s.includes(k))s=s.split(k).join(MAP[k]);"
            # The wordmark is matched by prefix, so it needs a rewrite of the whole d:"..." rather
            # than a literal substring swap.
            f"const PRE={json.dumps(ONYX_WORDMARK_PREFIXES)},PUFFIN={json.dumps(PUFFIN_WORDMARK_PATH)};"
            "s=s.replace(/d:\"(M[^\"]+)\"/g,(m,dd)=>{for(const k in PRE){if(dd.startsWith(k))"
            "return 'd:\"'+(PRE[k]==='PUFFIN'?PUFFIN:'M0 0Z')+'\"'}return m});"
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
                ["docker", "ps", "--filter", "label=com.docker.compose.service=web_server",
                 "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        # The compose service label survives a `docker rename`; the container name does not.
        names = result.stdout.split()
        return names[0] if names else None

    @classmethod
    def render(cls, workdir: str) -> dict:
        """
        Draws every Puffin asset at the dimensions Onyx ships.

        Sizes match the originals because the frontend lays them out against those aspect ratios;
        a square wordmark or an oversized mark reflows the sidebar rather than simply looking
        different.

        Args:
            workdir (str): Directory to write the rendered files into.

        Returns:
            dict: Mapping of served filename to the path it was rendered at.
        """
        assets = {
            "logo.png": cls._mark(workdir, "logo.png", (400, 400)),
            "logo-dark.png": cls._mark(workdir, "logo-dark.png", (400, 400)),
            "logotype.png": cls._wordmark(workdir, "logotype.png", (2640, 733), dark=False),
            "logotype-dark.png": cls._wordmark(workdir, "logotype-dark.png", (720, 320), dark=True),
            "logo.svg": cls._svg(workdir),
            "onyx.ico": cls._favicon(workdir),
        }
        return assets

    @classmethod
    def render_app_icon(cls, destination: str, size: int) -> bool:
        """
        Renders the square Puffin mark to an arbitrary path and size.

        The web UI's own assets are produced by `render()` at the dimensions Onyx expects. This is
        the same artwork for callers outside it -- the desktop app's icon set -- so that the window
        icon, the launcher entry and the browser favicon all come from one definition of the mark.

        Args:
            destination (str): Path to write the PNG to.
            size (int): Edge length in pixels.

        Returns:
            bool: True if the file was written.
        """
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            print("⚠️  Pillow is not installed — cannot render the app icon.")
            return False

        workdir = tempfile.mkdtemp(prefix="puffin-icon-")
        try:
            rendered = cls._mark(workdir, os.path.basename(destination), (size, size))
            shutil.copyfile(rendered, destination)
        except OSError as exc:
            print(f"⚠️  Could not render {destination}: {exc}")
            return False
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return True

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
    def _brand_tile(cls, size: Tuple[int, int]):
        """
        Builds the rounded Tiffany tile both the mark and the favicon are cut from.

        Args:
            size (Tuple[int, int]): Tile dimensions.

        Returns:
            Image: An RGBA tile with rounded corners, filled with the brand colour exactly.
        """
        from PIL import Image, ImageDraw

        width, height = size
        tile = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(tile).rounded_rectangle(
            [0, 0, width - 1, height - 1], radius=int(min(size) * 0.23), fill=BRAND_START + (255,)
        )
        return tile

    @classmethod
    def _mark(cls, workdir: str, filename: str, size: Tuple[int, int]) -> str:
        """
        Renders the square app mark: a white "P" monogram on the Tiffany tile.

        There is no dark variant: the tile is the brand colour on both themes, and a white monogram
        reads on it either way.

        Args:
            workdir (str): Output directory.
            filename (str): File to write.
            size (Tuple[int, int]): Output dimensions.

        Returns:
            str: Path to the rendered file.
        """
        from PIL import ImageDraw

        image = cls._brand_tile(size)
        draw = ImageDraw.Draw(image)
        font = cls._font(int(size[1] * 0.62))
        box = draw.textbbox((0, 0), "P", font=font)
        draw.text(
            ((size[0] - (box[2] - box[0])) / 2 - box[0],
             (size[1] - (box[3] - box[1])) / 2 - box[1]),
            "P", font=font, fill=(255, 255, 255, 255),
        )
        path = os.path.join(workdir, filename)
        image.save(path)
        return path

    @classmethod
    def _wordmark(cls, workdir: str, filename: str, size: Tuple[int, int], dark: bool) -> str:
        """
        Renders the horizontal "Puffin" wordmark on a transparent background.

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
        colour = BRAND_WORDMARK_DARK if dark else BRAND_START + (255,)

        # Fit the word to the canvas rather than guessing a point size: the two wordmark files
        # ship at very different aspect ratios.
        point = int(size[1] * 0.7)
        while point > 8:
            font = cls._font(point)
            box = draw.textbbox((0, 0), "Puffin", font=font)
            if box[2] - box[0] <= size[0] * 0.88 and box[3] - box[1] <= size[1] * 0.72:
                break
            point = int(point * 0.92)

        box = draw.textbbox((0, 0), "Puffin", font=font)
        draw.text(
            ((size[0] - (box[2] - box[0])) / 2 - box[0],
             (size[1] - (box[3] - box[1])) / 2 - box[1]),
            "Puffin", font=font, fill=colour,
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
        svg = f'''<svg viewBox="0 0 56 56" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect width="56" height="56" rx="13" fill="{TIFFANY_BLUE}"/>
    <path d="M18 14h9.5c7 0 11.5 4.2 11.5 10.5s-4.5 10.5-11.5 10.5h-2v7H18V14zm7.5 6.5v8h2c2.9 0
             4.5-1.5 4.5-4s-1.6-4-4.5-4h-2z" fill="#ffffff"/>
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

        source = Image.open(cls._mark(workdir, "_favicon_src.png", (256, 256)))
        path = os.path.join(workdir, "onyx.ico")
        source.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
        return path
