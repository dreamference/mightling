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

# The word beside the mark is a *second* inline SVG -- four letter paths spelling "onyx" on a
# 152x64 viewBox -- not the `appName` text and not /logotype.png, which the bundle never
# references at all. `Logo` lays the two components side by side in a flex row.
#
# Replacing letterforms means replacing outlines: this is "Dream" set in DejaVu Sans Bold,
# converted from the font's glyph outlines to a single path with fontTools and fitted to the same
# viewBox. It is stored as a constant rather than generated at runtime so the rebrand needs no
# font-tooling dependency, and because the geometry is fixed once chosen.
DREAM_WORDMARK_PATH: Final[str] = (
    "M9.84 22.43V40.98H12.65Q17.46 40.98 20 38.6Q22.53 36.22 22.53 31.67Q22.53 27.15 20.01 24.79Q17.48 22.43 12.65 22.43ZM2 16.5H10.27Q17.2 16.5 20.59 17.49Q23.98 18.47 26.41 20.84Q28.54 22.89 29.58 25.58Q30.62 28.27 30.62 31.67Q30.62 35.12 29.58 37.82Q28.54 40.52 26.41 42.57Q23.96 44.94 20.54 45.92Q17.12 46.91 10.27 46.91H2ZM53.26 30.31Q52.3 29.86 51.35 29.65Q50.4 29.43 49.45 29.43Q46.63 29.43 45.12 31.24Q43.6 33.04 43.6 36.4V46.91H36.31V24.1H43.6V27.84Q45 25.6 46.83 24.57Q48.65 23.55 51.2 23.55Q51.56 23.55 51.99 23.58Q52.42 23.61 53.23 23.71ZM79.66 35.44V37.52H62.61Q62.87 40.09 64.46 41.37Q66.05 42.65 68.9 42.65Q71.2 42.65 73.62 41.97Q76.03 41.29 78.58 39.9V45.53Q75.99 46.5 73.4 47Q70.82 47.5 68.23 47.5Q62.04 47.5 58.6 44.36Q55.17 41.21 55.17 35.52Q55.17 29.94 58.54 26.74Q61.91 23.55 67.82 23.55Q73.2 23.55 76.43 26.78Q79.66 30.02 79.66 35.44ZM72.16 33.02Q72.16 30.94 70.95 29.67Q69.74 28.39 67.78 28.39Q65.66 28.39 64.34 29.59Q63.01 30.78 62.69 33.02ZM95.4 36.64Q93.12 36.64 91.97 37.42Q90.82 38.19 90.82 39.7Q90.82 41.09 91.75 41.87Q92.67 42.65 94.32 42.65Q96.38 42.65 97.79 41.18Q99.19 39.7 99.19 37.48V36.64ZM106.55 33.89V46.91H99.19V43.53Q97.73 45.61 95.89 46.56Q94.06 47.5 91.43 47.5Q87.89 47.5 85.68 45.44Q83.47 43.37 83.47 40.07Q83.47 36.05 86.23 34.18Q88.99 32.31 94.89 32.31H99.19V31.74Q99.19 30 97.83 29.2Q96.46 28.39 93.57 28.39Q91.23 28.39 89.21 28.86Q87.19 29.33 85.46 30.27V24.71Q87.81 24.14 90.17 23.84Q92.53 23.55 94.89 23.55Q101.07 23.55 103.81 25.98Q106.55 28.41 106.55 33.89ZM134.48 27.88Q135.86 25.77 137.77 24.66Q139.67 23.55 141.95 23.55Q145.88 23.55 147.94 25.97Q150 28.39 150 33.02V46.91H142.67V35.02Q142.69 34.75 142.7 34.46Q142.71 34.18 142.71 33.65Q142.71 31.23 141.99 30.14Q141.28 29.05 139.69 29.05Q137.61 29.05 136.48 30.76Q135.35 32.47 135.31 35.71V46.91H127.98V35.02Q127.98 31.23 127.33 30.14Q126.67 29.05 125 29.05Q122.91 29.05 121.76 30.77Q120.62 32.49 120.62 35.69V46.91H113.29V24.1H120.62V27.44Q121.97 25.5 123.71 24.52Q125.45 23.55 127.55 23.55Q129.91 23.55 131.73 24.69Q133.54 25.83 134.48 27.88Z"
)

# The four "onyx" letters, matched on a prefix because the "o" path is 1.3 KB and only its head is
# needed to identify it. The first becomes the Dream wordmark; the rest collapse to nothing.
ONYX_WORDMARK_PREFIXES: Final[dict] = {
    "M19.1795 51.2136C15.6695 51.2136 12.4353 50.3862": "DREAM",
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
    'application_name?.trim()||"Onyx"': 'application_name?.trim()||"Dream"',
    '.trim()}return"Onyx"': '.trim()}return"Dream"',
}



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
            f"const PRE={json.dumps(ONYX_WORDMARK_PREFIXES)},DREAM={json.dumps(DREAM_WORDMARK_PATH)};"
            "s=s.replace(/d:\"(M[^\"]+)\"/g,(m,dd)=>{for(const k in PRE){if(dd.startsWith(k))"
            "return 'd:\"'+(PRE[k]==='DREAM'?DREAM:'M0 0Z')+'\"'}return m});"
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
