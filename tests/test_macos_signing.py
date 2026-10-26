"""The Developer ID signing of the macOS builds, gated on the Apple secrets (docs/dev/macos-signing.md).

The workflows are read, never run: their `run:` blocks pass `bash -n` and their shape is asserted.
The gate script runs here with each mix of secrets; the entitlements are read from their plists and,
where Node is installed, from desktop/electron/mac/entitlements.mjs itself.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
MACOS = ROOT / "scripts" / "macos"
SECRETS = (
    "APPLE_DEVELOPER_ID_P12",
    "APPLE_DEVELOPER_ID_P12_PASSWORD",
    "APPLE_TEAM_ID",
    "APPLE_API_KEY_ID",
    "APPLE_API_ISSUER_ID",
    "APPLE_API_KEY_P8",
)
SIGNED = "${{ steps.apple.outputs.sign == 'true' }}"
CLEANUP = "${{ always() && steps.apple.outputs.sign == 'true' }}"
ENVIRONMENT = "${{ inputs.sign && 'release' || '' }}"
JIT = "com.apple.security.cs.allow-jit"
MICROPHONE = "com.apple.security.device.audio-input"


def workflow(name: str) -> dict[str, object]:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    assert isinstance(data, dict)
    return data


def trigger(data: dict[str, object]) -> dict[str, object]:
    # PyYAML reads the bare key `on` as True.
    value = data.get("on", data.get(True))  # type: ignore[call-overload]
    assert isinstance(value, dict)
    return value


def build_job(name: str) -> dict[str, object]:
    jobs = workflow(name)["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["build"]
    assert isinstance(job, dict)
    return job


def steps(name: str) -> list[dict[str, object]]:
    return list(build_job(name)["steps"])  # type: ignore[call-overload]


def names(name: str) -> list[str]:
    return [str(s.get("name", s.get("uses", ""))) for s in steps(name)]


def step(name: str, title: str) -> dict[str, object]:
    return next(s for s in steps(name) if s.get("name") == title)


@pytest.mark.parametrize("name", ["mac-preview.yml", "build-clients.yml", "release.yml"])
def test_every_run_block_parses_with_bash_n(tmp_path: Path, name: str) -> None:
    jobs = workflow(name)["jobs"]
    assert isinstance(jobs, dict)
    blocks = [str(s["run"]) for job in jobs.values() for s in job.get("steps", []) if "run" in s]
    assert blocks
    for number, block in enumerate(blocks):
        script = tmp_path / f"run-{number}.sh"
        script.write_text(re.sub(r"\$\{\{[^}]*\}\}", "placeholder", block))
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{name} block {number}: {result.stderr}"


@pytest.mark.parametrize("script", sorted(MACOS.glob("*.sh")), ids=lambda p: p.name)
def test_the_scripts_are_executable_and_parse(script: Path) -> None:
    assert os.access(script, os.X_OK)
    assert script.read_text().startswith("#!/usr/bin/env bash\n")
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    # macOS's own bash is 3.2: nothing of bash 4 that would break there.
    assert not re.search(r"\b(mapfile|readarray)\b|declare -A|\$\{[^}]*,,\}", script.read_text())


@pytest.mark.parametrize("name", ["mac-preview.yml", "build-clients.yml"])
def test_the_release_environment_is_asked_for_only_when_signing_is(name: str) -> None:
    assert build_job(name)["environment"] == ENVIRONMENT
    on = trigger(workflow(name))
    call = on["workflow_call"]
    assert isinstance(call, dict)
    assert call["inputs"]["sign"] == {  # type: ignore[index]
        "description": call["inputs"]["sign"]["description"],  # type: ignore[index]
        "type": "boolean",
        "default": False,
    }
    # A push (CI on desktop/mac** or clients/**) has no inputs: no environment, no signing.
    assert "branches" in on["push"]  # type: ignore[operator]


@pytest.mark.parametrize("name", ["mac-preview.yml", "build-clients.yml"])
def test_every_step_that_reads_an_apple_secret_is_the_gate_or_runs_only_when_signing(
    name: str,
) -> None:
    readers = [s for s in steps(name) if "secrets.APPLE_" in json.dumps(s)]
    assert readers
    gate = step(name, "Apple signing")
    assert gate["id"] == "apple"
    assert gate["run"] == "scripts/macos/signing_gate.sh"
    assert set(gate["env"]) == {"SIGN", *SECRETS}  # type: ignore[arg-type]
    for reader in readers:
        if reader["name"] != "Apple signing":
            assert reader["if"] == SIGNED, reader["name"]
        for key, value in reader.get("env", {}).items():  # type: ignore[attr-defined]
            if key.startswith("APPLE_"):
                assert value == f"${{{{ secrets.{key} }}}}"
    # The secret names, and nothing else Apple, in the job.
    used = set(re.findall(r"secrets\.(APPLE_\w+)", (WORKFLOWS / name).read_text()))
    assert used == set(SECRETS)
    # The keychain goes whatever happened.
    assert step(name, "Remove the Developer ID")["if"] == CLEANUP


def test_no_other_workflow_reads_the_apple_secrets() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        if path.name not in {"mac-preview.yml", "build-clients.yml"}:
            assert "APPLE_" not in path.read_text(), path.name


def test_the_clients_are_signed_before_they_are_tested_and_notarised_before_they_are_packaged() -> (
    None
):
    order = names("build-clients.yml")
    sign = order.index("Sign the binaries with the Developer ID")
    assert order.index("Bin directory") < order.index("Apple signing") < sign
    assert sign < order.index("Smoke test") < order.index("Seatbelt takes the network away")
    assert order.index("Notarise the binaries") < order.index("Package")
    assert order[-1] == "Remove the Developer ID"
    assert step("build-clients.yml", "Apple signing")["if"] == "runner.os == 'macOS'"
    assert "scripts/macos/sign_binaries.sh" in str(
        step("build-clients.yml", "Sign the binaries with the Developer ID")["run"]
    )


def test_the_app_is_signed_after_packaging_run_then_stapled_before_the_dmg() -> None:
    order = names("mac-preview.yml")
    assert order.index("Apple signing") < order.index("Bundled binaries")
    assert (
        order.index("Build the app")
        < order.index("Import the Developer ID")
        < order.index("Sign the app with the Developer ID")
        < order.index("Inspect the bundle")
        < order.index("Start the app")
        < order.index("Notarise and staple the app")
        < order.index("Make the dmg")
        < order.index("Sign, notarise and staple the dmg")
    )
    # The dmg uploaded, and so summed into the release's SHA256SUMS, is the stapled one.
    upload = next(
        i for i, s in enumerate(steps("mac-preview.yml")) if "upload-artifact" in str(s.get("uses"))
    )
    dmg_step = order.index("Sign, notarise and staple the dmg")
    assert dmg_step < upload and dmg_step < order.index("Remove the Developer ID")
    app = str(step("mac-preview.yml", "Notarise and staple the app")["run"])
    assert (
        "notarize.sh" in app and 'xcrun stapler staple "$APP"' in app and "spctl -a -t exec" in app
    )
    dmg = str(step("mac-preview.yml", "Sign, notarise and staple the dmg")["run"])
    assert dmg.index("codesign --force") < dmg.index("notarize.sh") < dmg.index("stapler staple")
    assert 'xcrun stapler validate "$mount/Mightling.app"' in dmg
    signing = str(step("mac-preview.yml", "Sign the app with the Developer ID")["run"])
    assert 'node mac/sign.mjs "$APP"' in signing
    assert build_job("mac-preview.yml")["outputs"] == {"signed": "${{ steps.apple.outputs.sign }}"}


def test_the_release_asks_for_signing_and_says_which_mac_app_it_carries() -> None:
    jobs = workflow("release.yml")["jobs"]
    assert isinstance(jobs, dict)
    assert jobs["ling-clients"]["with"]["sign"] is True
    assert jobs["desktop-mac"]["with"]["sign"] is True
    # Environment secrets reach a called job through its own `environment`, not from the caller.
    assert "secrets" not in jobs["ling-clients"] and "secrets" not in jobs["desktop-mac"]
    publish = next(s for s in jobs["release"]["steps"] if s.get("name") == "Publish GitHub release")
    assert publish["env"]["MAC_SIGNED"] == "${{ needs.desktop-mac.outputs.signed }}"
    run = str(publish["run"])
    assert (
        run.index('[[ "$MAC_SIGNED" == "true" ]]')
        < run.index("notarised by Apple")
        < run.index("(preview, unsigned)")
    )


def run_gate(tmp_path: Path, sign: str, secrets: tuple[str, ...]) -> tuple[int, str, str]:
    output = tmp_path / "output"
    output.write_text("")
    env = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output), "SIGN": sign}
    env.update(dict.fromkeys(secrets, "x"))
    result = subprocess.run(
        [str(MACOS / "signing_gate.sh")], env=env, capture_output=True, text=True
    )
    return result.returncode, output.read_text(), result.stdout


def test_the_gate_signs_only_when_asked_and_every_secret_is_set(tmp_path: Path) -> None:
    assert run_gate(tmp_path, "true", SECRETS)[:2] == (0, "sign=true\n")
    # Not asked (a push, a hand run): unsigned whatever is set.
    assert run_gate(tmp_path, "", SECRETS)[:2] == (0, "sign=false\n")
    assert run_gate(tmp_path, "false", ())[:2] == (0, "sign=false\n")
    # Asked, nothing set: today's unsigned preview, with a warning in the run.
    code, output, stdout = run_gate(tmp_path, "true", ())
    assert (code, output) == (0, "sign=false\n")
    assert "::warning::" in stdout
    # Asked, half set: an error naming what is missing, never a silent unsigned build.
    code, output, stdout = run_gate(tmp_path, "true", SECRETS[:3])
    assert (code, output) == (1, "")
    assert "::error::" in stdout and "APPLE_API_KEY_P8" in stdout and "APPLE_TEAM_ID" not in stdout


def test_the_entitlements_are_only_what_the_hardened_runtime_needs() -> None:
    def keys(name: str) -> dict[str, object]:
        return plistlib.loads((MACOS / "entitlements" / name).read_bytes())

    assert keys("codex-code-mode-host.plist") == {JIT: True}
    assert keys("electron-helper.plist") == {JIT: True}
    assert keys("electron-app.plist") == {JIT: True, MICROPHONE: True}
    assert sorted(p.name for p in (MACOS / "entitlements").iterdir()) == [
        "codex-code-mode-host.plist",
        "electron-app.plist",
        "electron-helper.plist",
    ]
    signer = (MACOS / "sign_binaries.sh").read_text()
    assert "--options runtime" in signer and "--timestamp" in signer
    assert 'if [[ "$name" == codex-code-mode-host ]]; then' in signer


def test_the_app_asks_for_the_microphone_in_its_info_plist() -> None:
    forge = (ROOT / "desktop" / "electron" / "forge.config.js").read_text()
    assert "NSMicrophoneUsageDescription:" in forge


def test_osx_sign_is_a_pinned_dependency_of_the_app() -> None:
    package = json.loads((ROOT / "desktop" / "electron" / "package.json").read_text())
    lock = json.loads((ROOT / "desktop" / "electron" / "package-lock.json").read_text())
    pinned = package["devDependencies"]["@electron/osx-sign"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned)
    assert lock["packages"]["node_modules/@electron/osx-sign"]["version"] == pinned


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_each_piece_of_the_app_gets_its_entitlements() -> None:
    app = "/b/out/Mightling-darwin-arm64/Mightling.app"
    frameworks = f"{app}/Contents/Frameworks"
    cases = {
        app: "electron-app.plist",
        f"{app}/Contents/MacOS/Mightling": "electron-app.plist",
        f"{frameworks}/Mightling Helper.app": "electron-app.plist",
        f"{frameworks}/Mightling Helper.app/Contents/MacOS/Mightling Helper": "electron-app.plist",
        f"{frameworks}/Mightling Helper (Renderer).app": "electron-helper.plist",
        f"{frameworks}/Mightling Helper (GPU).app/Contents/MacOS/Mightling Helper (GPU)": "electron-helper.plist",
        f"{frameworks}/Mightling Helper (Plugin).app": "electron-helper.plist",
        f"{app}/Contents/Resources/codex-code-mode-host": "codex-code-mode-host.plist",
        f"{app}/Contents/Resources/ling": "",
        f"{frameworks}/Electron Framework.framework": "",
        f"{frameworks}/Electron Framework.framework/Versions/A/Libraries/libffmpeg.dylib": "",
        f"{frameworks}/Electron Framework.framework/Versions/A/Helpers/chrome_crashpad_handler": "",
        f"{frameworks}/Squirrel.framework/Versions/A/Resources/ShipIt": "",
    }
    module = ROOT / "desktop" / "electron" / "mac" / "entitlements.mjs"
    script = (
        f"import {{ entitlementsFor }} from {json.dumps(module.as_uri())};"
        f"const files = {json.dumps(list(cases))};"
        "console.log(JSON.stringify(files.map((f) => entitlementsFor(f, 'Mightling'))));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True
    )
    answers = json.loads(result.stdout)
    for (file, expected), answer in zip(cases.items(), answers, strict=True):
        if expected:
            assert answer == str(MACOS / "entitlements" / expected), file
        else:
            assert answer == [], file
