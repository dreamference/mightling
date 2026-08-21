"""
Telegram-style UI Typography for Onyx.

This module provides the OnyxUIFonts class, which replaces the typefaces Onyx's web UI ships with
the ones Telegram uses -- Roboto for the interface, Roboto Mono for code.

The substitution is done at the **`@font-face` source**, not at the `font-family` declarations.
Onyx's CSS names its UI face in around forty places, most of them through the
`--font-hanken-grotesk` variable that `next/font` writes onto `<html>`; rewriting all of those
means finding every one, and missing one leaves a stray Hanken Grotesk in the middle of the page.
Rewriting the four `@font-face` blocks instead leaves every declaration untouched and simply
changes which file the name resolves to. The consequence worth knowing: after this runs, the
served CSS still says `Hanken Grotesk` everywhere and renders Roboto, so grepping the bundle for
`Roboto` finds only the `src:` lines.

Roboto is fetched once from google/fonts, converted from its variable TTF to WOFF2 on the host,
and cached under `~/.cache/dreamference/fonts`. It is then served by Onyx itself out of
`/app/public/fonts`, next to the KH Teka files Onyx already ships there -- so the deployment makes
no request to a font CDN at page load, and a machine that has run `dream onyx configure` once
never needs the network for this again. Both faces are variable (`wght` 100-900 and 100-700),
which is what lets one file stand in for a `font-weight: 100 900` face.

Like the logos, these are writes into a running container's filesystem: an image upgrade or a
`deploy install --force` restores Onyx's own typefaces, and re-running `dream onyx configure`
puts Roboto back.
"""

import json
import os
import shutil
import subprocess
import time
import tempfile
import urllib.error
import urllib.request
from typing import Dict, Final, Optional, Set, Tuple

from dreamference.runner.onyx_brand_assets import WEB_BUILD_DIR, OnyxBrandAssets

# Where the host keeps the converted faces between runs, so the fetch happens once per machine.
FONT_CACHE_DIR: Final[str] = os.path.expanduser("~/.cache/dreamference/fonts")

# Onyx already serves /app/public/fonts at /fonts (that is where its KH Teka files live), so the
# rewritten `src:` URLs need no web server configuration to resolve.
WEB_FONT_DIR: Final[str] = "/app/public/fonts"
WEB_FONT_URL: Final[str] = "/fonts"

# Upstream variable TTFs. google/fonts serves Roboto under the OFL, so redistributing the
# converted file inside a container the user hosts themselves is unencumbered. The bracketed axis
# list is part of the upstream filename and has to stay percent-encoded.
FONT_SOURCES: Final[Dict[str, str]] = {
    "Roboto.woff2": (
        "https://raw.githubusercontent.com/google/fonts/main/ofl/roboto/"
        "Roboto%5Bwdth,wght%5D.ttf"
    ),
    "RobotoMono.woff2": (
        "https://raw.githubusercontent.com/google/fonts/main/ofl/robotomono/"
        "RobotoMono%5Bwght%5D.ttf"
    ),
}

# Which of Onyx's faces each Telegram face stands in for, and the weight range the replacement
# block advertises. Hanken Grotesk is the interface face; KH Teka is used for a handful of
# headings and would otherwise be the one place the old typography survived; DM Mono is code.
#
# The value is (cached filename, font-weight range). The ranges are the axis ranges of the
# variable fonts themselves -- claiming 100-900 for Roboto Mono, whose `wght` axis stops at 700,
# would have the browser synthesise the missing weight instead of clamping to the real one.
FACE_SUBSTITUTIONS: Final[Dict[str, Tuple[str, str]]] = {
    "Hanken Grotesk": ("Roboto.woff2", "100 900"),
    "KH Teka": ("Roboto.woff2", "100 900"),
    "DM Mono": ("RobotoMono.woff2", "100 700"),
}


class OnyxUIFonts:
    """
    Installs Telegram's typefaces into a running Onyx web server container.
    """

    @classmethod
    def install(cls, container: Optional[str] = None) -> bool:
        """
        Fetches the Telegram faces if needed, serves them from Onyx, and repoints its CSS at them.

        Args:
            container (Optional[str]): Onyx web server container name; discovered if omitted.

        Returns:
            bool: True if Onyx's stylesheets now resolve their faces to the Telegram ones.
        """
        target = container or OnyxBrandAssets.find_web_container()
        if not target:
            return False

        cached = cls.ensure_fonts()
        if not cached:
            return False

        present = cls.installed_faces(target)
        copied = False
        for filename, path in cached.items():
            if filename in present:
                continue
            result = subprocess.run(
                ["docker", "cp", path, f"{target}:{WEB_FONT_DIR}/{filename}"],
                capture_output=True, text=True, timeout=120, check=False,
            )
            if result.returncode != 0:
                print(f"⚠️  Could not install {filename}: {result.stderr.strip()[:160]}")
                return False
            copied = True

        patched = cls.patch_css(target)

        # Next.js walks `public/` once at boot to build the routes it will serve statically, so a
        # face copied into a running container is on disk but has no URL until the server starts
        # again. The stylesheets need no restart -- those are read per request -- which is why
        # only a copy triggers one.
        if copied and not cls.restart_web_server(target):
            print("⚠️  Onyx's web server did not come back — the new faces may 404 until it does.")
            return False

        return patched > 0

    @classmethod
    def ensure_fonts(cls) -> Optional[Dict[str, str]]:
        """
        Returns the cached WOFF2 faces, downloading and converting them on first use.

        Returns:
            Optional[Dict[str, str]]: Mapping of served filename to its path in the host cache,
                or None if a face could not be obtained.
        """
        try:
            os.makedirs(FONT_CACHE_DIR, exist_ok=True)
        except OSError as exc:
            print(f"⚠️  Could not create the font cache: {exc}")
            return None

        cached: Dict[str, str] = {}
        for filename, url in FONT_SOURCES.items():
            path = os.path.join(FONT_CACHE_DIR, filename)
            if not os.path.exists(path) and not cls._fetch_face(url, path):
                return None
            cached[filename] = path
        return cached

    @classmethod
    def _fetch_face(cls, url: str, destination: str) -> bool:
        """
        Downloads one variable TTF and writes it out as WOFF2.

        The conversion happens on the host rather than shipping a WOFF2 from upstream because
        google/fonts publishes only the TTF; fontTools does the repackaging. WOFF2 compression
        needs Brotli, which fontTools imports lazily inside `save()` -- hence ImportError being
        caught alongside the transfer failures rather than only at the top of this method.

        Args:
            url (str): Upstream TTF URL.
            destination (str): Path to write the WOFF2 to.

        Returns:
            bool: True if the file was written.
        """
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            print("⚠️  fontTools is not installed — leaving Onyx's own typefaces in place.")
            return False

        workdir = tempfile.mkdtemp(prefix="dream-fonts-")
        source = os.path.join(workdir, "source.ttf")
        try:
            print(f"⬇️  Fetching {os.path.basename(destination)} ...")
            with urllib.request.urlopen(url, timeout=60) as response:
                with open(source, "wb") as handle:
                    shutil.copyfileobj(response, handle)

            font = TTFont(source)
            font.flavor = "woff2"
            font.save(destination)
        except (OSError, urllib.error.URLError, ValueError, ImportError) as exc:
            print(f"⚠️  Could not prepare {os.path.basename(destination)}: {exc}")
            return False
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return True

    @classmethod
    def patch_css(cls, container: str) -> int:
        """
        Repoints every substituted `@font-face` in the compiled stylesheets at a Telegram face.

        Onyx's faces are split into unicode-range subsets -- four for Hanken Grotesk alone -- and
        each block is replaced by the same full-coverage one. The duplicate rules are harmless:
        they name one family and one URL, so the browser resolves them to a single download.

        Counts stylesheets that *reference* a Telegram face rather than ones it just rewrote, so
        a second run over an already-patched container reports the same number as the first
        instead of reporting that nothing happened.

        Args:
            container (str): Onyx web server container name.

        Returns:
            int: Number of stylesheets now pointing at the Telegram faces. Zero means none of the
                expected families were found, which is what a version that changed its typography
                would look like.
        """
        replacements = {
            family: (
                f"@font-face{{font-family:{family};font-style:normal;"
                f"font-weight:{weight};font-display:swap;"
                f'src:url({WEB_FONT_URL}/{filename})format("woff2")}}'
            )
            for family, (filename, weight) in FACE_SUBSTITUTIONS.items()
        }

        script = (
            "const fs=require('fs'),path=require('path');"
            f"const MAP={json.dumps(replacements)},NEEDLE={json.dumps(WEB_FONT_URL + '/')};"
            "let changed=0;"
            "const walk=(d,dep)=>{if(dep>8)return;let e=[];"
            "try{e=fs.readdirSync(d,{withFileTypes:true})}catch(x){return}"
            "for(const f of e){const p=path.join(d,f.name);"
            "if(f.isDirectory()){if(!/node_modules|cache/.test(p))walk(p,dep+1);continue}"
            "if(!/\\.css$/.test(f.name))continue;"
            "let s='';try{s=fs.readFileSync(p,'utf8')}catch(x){continue}"
            # Minified CSS puts no nested braces inside an @font-face, so the block ends at the
            # first closing brace.
            "const o=s.replace(/@font-face\\{[^}]*\\}/g,(m)=>{"
            "const n=(m.match(/font-family:\\s*([^;}]+)/)||[])[1];"
            "if(!n)return m;return MAP[n.trim().replace(/^[\"']|[\"']$/g,'')]||m});"
            "if(o!==s){try{fs.writeFileSync(p,o)}catch(x){continue}}"
            "if(o.includes(NEEDLE))changed++;}};"
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
    def installed_faces(cls, container: str) -> Set[str]:
        """
        Reports which Telegram faces the container is already serving.

        Args:
            container (str): Onyx web server container name.

        Returns:
            Set[str]: Filenames present in the web server's font directory.
        """
        script = (
            f"const fs=require('fs');let e=[];"
            f"try{{e=fs.readdirSync({json.dumps(WEB_FONT_DIR)})}}catch(x){{}}"
            "console.log(JSON.stringify(e));"
        )
        try:
            result = subprocess.run(
                ["docker", "exec", container, "node", "-e", script],
                capture_output=True, text=True, timeout=60, check=False,
            )
            names = json.loads(result.stdout.strip().splitlines()[-1])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return set()
        return {n for n in names if n in FONT_SOURCES}

    @classmethod
    def restart_web_server(cls, container: str) -> bool:
        """
        Restarts Onyx's web server and waits for it to report healthy again.

        Args:
            container (str): Onyx web server container name.

        Returns:
            bool: True if the container came back healthy.
        """
        print("🔄 Restarting Onyx's web server to publish the new typefaces...")
        subprocess.run(
            ["docker", "restart", container], capture_output=True, timeout=180, check=False
        )
        for _ in range(40):
            health = subprocess.run(
                ["docker", "inspect", container, "--format", "{{.State.Health.Status}}"],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout.strip()
            if health == "healthy":
                return True
            time.sleep(5)
        return False
