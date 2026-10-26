//! `ling update`: replace this installation with the latest Mightling release's prebuilt binaries.
//!
//! Codex's own `update` picks an installer (npm, brew, OpenAI's install script) from how Codex was
//! installed, which would put upstream Codex in Mightling's place. Patch 0008 sends the subcommand
//! here instead. The release workflow attaches, per target, a gzipped `ling`, a gzipped
//! `codex-code-mode-host`, the gzipped web commands `ling-search` and `ling-fetch`, the gzipped
//! code index router `ling-code`, the gzipped local file index `ling-docs` (Linux), and a
//! `sha256sums` file covering all of them; this downloads
//! the latest published release's assets, checks the release's signature (from 1.5.0 on, the
//! release-wide `SHA256SUMS` signed by Mightling's release key, which lists the per-target
//! checksum file; src/release_signature.rs), verifies every archive against the checksum file, and
//! swaps the binaries in next to the running executable. The web commands and `ling-code` are
//! optional, because earlier releases do not carry them (v1.3.0 has the web commands and no
//! `ling-code`); the installed ones are then kept.
//!
//! On a Mac and on Windows the one-line installers also install the desktop app, and leave a
//! record of it ([`APP_RECORD`], beside the install folder's `bin`). When that record is there and
//! the app is still where it says, `ling update` updates the app too, from the same release and
//! under the same checks: the dmg or the NSIS installer must match its line in the signed
//! `SHA256SUMS`, and it is downloaded here rather than by a browser, so it carries no quarantine
//! mark or Mark of the Web (the owner's decision of 2026-10-26, specs/DREAMFERENCE_SETUP.md §3.2).
//! An app the user installed by hand, with no record, is left alone, and so is one that is running.
//!
//! The repository is public, so no token is needed. `GH_TOKEN` or `GITHUB_TOKEN` is sent when set,
//! which raises GitHub's rate limit; a logged-in `gh`'s token is used only for a fork named by
//! `MIGHTLING_RELEASE_REPO`. A token only ever goes to `api.github.com`, never to a download URL
//! the release names (security review 2026-10).

use std::collections::HashMap;
use std::ffi::OsStr;
use std::io::Read;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use anyhow::Context;
use anyhow::bail;
use sha2::Digest;
use sha2::Sha256;

use crate::release_signature;

/// Where releases are published. `MIGHTLING_RELEASE_REPO` overrides it, e.g. for a fork.
pub const RELEASE_REPO: &str = "dreamference/mightling";

/// The Mightling release this binary was built as. The release workflow sets `MIGHTLING_VERSION` when it
/// builds; a local `ling-admin codex build` does not, and such a binary counts as a source build.
pub const MIGHTLING_VERSION: Option<&str> = option_env!("MIGHTLING_VERSION");

/// Code Mode's host process, which Codex looks for next to its own executable under this name.
const CODE_MODE_HOST: &str = "codex-code-mode-host";

/// The agent's web commands (`ling-web-rs/`), installed beside `ling` and linked into
/// `~/.local/bin`, because the prompt names them and the model's shell must find them.
pub const WEB_COMMANDS: [&str; 2] = ["ling-search", "ling-fetch"];

/// The code index router (`ling-code-rs/`). The launcher looks for it beside `ling`
/// (`code_index::binary`), and the prompt names it, so it is linked into `~/.local/bin` as well.
/// The release carries the router only: the indexers it runs are fetched by
/// `ling-admin code setup`.
pub const CODE_COMMAND: &str = "ling-code";

/// The local file index (`ling-docs-rs/`, Linux only). The launcher looks for it beside `ling`
/// (`docs_index::binary`). The release carries the binary only: PDFium, ONNX Runtime and the
/// embedding model it loads are fetched, pinned, by `ling-admin docs setup`.
pub const DOCS_COMMAND: &str = "ling-docs";

/// Mightling over Signal's bridge (`ling-rs/signal/`, Linux only). `ling signal setup` copies it
/// for the bridge's system account (`signal::bridge_beside`), and `ling signal setup --refresh`
/// copies it again after an update. The release carries the binary only: signal-cli and its Java
/// runtime are fetched, pinned, by setup. It is never linked onto PATH: the commands are
/// `ling signal …`.
pub const SIGNAL_COMMAND: &str = "ling-signal";

/// The commands a release may carry beside `ling`, each installed if its asset is present and
/// kept as installed if not.
pub fn optional_commands() -> [&'static str; 5] {
    [WEB_COMMANDS[0], WEB_COMMANDS[1], CODE_COMMAND, DOCS_COMMAND, SIGNAL_COMMAND]
}

/// Whether an optional command is linked into `~/.local/bin` beside `ling`.
pub fn linked_onto_path(name: &str) -> bool {
    name != SIGNAL_COMMAND
}

/// What `ling update` should do, given this build's version and the latest release's.
#[derive(Debug, PartialEq, Eq)]
pub enum Decision {
    UpToDate,
    NewerThanRelease,
    Install,
}

/// Compares versions as semver where both parse, and installs in every other case: a source
/// build has no version, and a tag that is not semver is still the release the user asked for.
pub fn decide(current: Option<&str>, latest: &str) -> Decision {
    let Some(current) = current else {
        return Decision::Install;
    };
    if current == latest {
        return Decision::UpToDate;
    }
    match (semver::Version::parse(current), semver::Version::parse(latest)) {
        (Ok(current), Ok(latest)) if current > latest => Decision::NewerThanRelease,
        (Ok(current), Ok(latest)) if current == latest => Decision::UpToDate,
        _ => Decision::Install,
    }
}

/// The Rust target triple the release assets are named after: this machine's architecture on
/// Linux (glibc), macOS or Windows. `None` where no release build exists.
pub fn target() -> Option<String> {
    target_for(std::env::consts::ARCH, std::env::consts::OS)
}

/// [`target`] for a given architecture and operating system, as `std::env::consts` names them.
pub fn target_for(arch: &str, os: &str) -> Option<String> {
    let system = match os {
        "linux" => "unknown-linux-gnu",
        "macos" => "apple-darwin",
        "windows" => "pc-windows-msvc",
        _ => return None,
    };
    matches!(arch, "aarch64" | "x86_64").then(|| format!("{arch}-{system}"))
}

/// The Windows sandbox's helpers, which Codex looks for beside its own executable. Windows
/// releases carry them; they are optional like the web commands.
pub const WINDOWS_SANDBOX_HELPERS: [&str; 2] = ["codex-windows-sandbox-setup", "codex-command-runner"];

/// The file name a command is installed under: `ling-search.exe` on Windows. The release's
/// `.gz` assets keep the bare name and hold the `.exe`.
pub fn installed_name(name: &str) -> String {
    format!("{name}{}", std::env::consts::EXE_SUFFIX)
}

/// Asset names for a target: the two gzipped binaries and the checksum file.
pub fn asset_names(target: &str) -> [String; 3] {
    [
        format!("ling-{target}.gz"),
        format!("{CODE_MODE_HOST}-{target}.gz"),
        format!("ling-{target}.sha256sums"),
    ]
}

/// Asset names of the optional commands for a target, in `optional_commands` order.
pub fn optional_asset_names(target: &str) -> [String; 5] {
    optional_commands().map(|name| format!("{name}-{target}.gz"))
}

/// Parses `sha256sum` output: `<hex>  <name>` per line, with an optional `*` before binary names.
pub fn parse_sums(text: &str) -> HashMap<String, String> {
    text.lines()
        .filter_map(|line| {
            let mut parts = line.split_whitespace();
            let digest = parts.next()?.to_lowercase();
            let name = parts.next()?.trim_start_matches('*').to_string();
            Some((name, digest))
        })
        .collect()
}

fn hex_sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// The only host a token is ever sent to. The release JSON names each asset's download URL, and
/// a token must not follow such a URL anywhere else: a release that was tampered with, or an old
/// repository name taken over by someone else, would otherwise collect the user's GitHub token.
const GITHUB_API: &str = "https://api.github.com/";

/// Whether a request to `url` may carry the token.
fn sends_token(url: &str) -> bool {
    url.starts_with(GITHUB_API)
}

/// The token to send, if any. `GH_TOKEN` and `GITHUB_TOKEN` are used when set; the token of a
/// logged-in `gh` is asked for only when `MIGHTLING_RELEASE_REPO` names another repository (a
/// private fork). The public repository needs none, and a `gh` login usually carries far more
/// access than reading a release.
fn github_token(repo: &str) -> Option<String> {
    for name in ["GH_TOKEN", "GITHUB_TOKEN"] {
        if let Ok(token) = std::env::var(name)
            && !token.trim().is_empty()
        {
            return Some(token.trim().to_string());
        }
    }
    if repo == RELEASE_REPO {
        return None;
    }
    let output = std::process::Command::new("gh")
        .args(["auth", "token"])
        .output()
        .ok()?;
    let token = String::from_utf8(output.stdout).ok()?.trim().to_string();
    (output.status.success() && !token.is_empty()).then_some(token)
}

/// The file the one-line installers write beside the install folder's `bin` when they installed the
/// desktop app: `path=<the app>` and `version=<the release tag>`, one per line.
pub const APP_RECORD: &str = "desktop-app";

/// What [`APP_RECORD`] says: where the installer put the app, and which release it was.
#[derive(Debug, PartialEq, Eq)]
pub struct AppRecord {
    pub path: PathBuf,
    pub version: String,
}

/// Reads an [`APP_RECORD`]'s text; `None` without a path. Windows line endings and the byte-order
/// mark Windows PowerShell's UTF-8 writes are allowed.
pub fn parse_app_record(text: &str) -> Option<AppRecord> {
    let (mut path, mut version) = (String::new(), String::new());
    for line in text.trim_start_matches('\u{feff}').lines() {
        match line.trim().split_once('=') {
            Some(("path", value)) => path = value.trim().to_string(),
            Some(("version", value)) => version = value.trim().to_string(),
            _ => {}
        }
    }
    (!path.is_empty()).then(|| AppRecord { path: PathBuf::from(path), version })
}

/// The text of an [`APP_RECORD`], as the installers write it.
pub fn format_app_record(record: &AppRecord) -> String {
    format!("path={}\nversion={}\n", record.path.display(), record.version)
}

/// Whether an app of `installed` (a tag, `v` or not) is older than the release `latest`.
pub fn app_is_behind(installed: &str, latest: &str) -> bool {
    decide(Some(installed.trim_start_matches('v')), latest.trim_start_matches('v')) == Decision::Install
}

/// The release's desktop app for an operating system and architecture, as `std::env::consts` names
/// them: the dmg mac-preview.yml builds (`Mightling-<version>-<arm64|x64>[-preview].dmg`) or the
/// installer windows.yml builds (`Mightling-Setup-<version>-<arm64|x64>.exe`).
pub fn desktop_app_asset<'a>(names: impl IntoIterator<Item = &'a str>, os: &str, arch: &str) -> Option<String> {
    let arch = match arch {
        "aarch64" => "arm64",
        "x86_64" => "x64",
        _ => return None,
    };
    names
        .into_iter()
        .find(|name| match os {
            "macos" => {
                !name.starts_with("Mightling-Setup-")
                    && name.starts_with("Mightling-")
                    && (name.ends_with(&format!("-{arch}.dmg")) || name.ends_with(&format!("-{arch}-preview.dmg")))
            }
            "windows" => name.starts_with("Mightling-Setup-") && name.ends_with(&format!("-{arch}.exe")),
            _ => false,
        })
        .map(str::to_string)
}

/// Runs a tool and fails with its last line of output when it fails.
fn run_tool(program: &str, args: &[&OsStr]) -> anyhow::Result<()> {
    let output = std::process::Command::new(program)
        .args(args)
        .output()
        .with_context(|| format!("could not run {program}"))?;
    if !output.status.success() {
        let text = String::from_utf8_lossy(&output.stderr);
        bail!("{program} failed: {}", text.lines().last().unwrap_or("no output"));
    }
    Ok(())
}

/// Whether the desktop app is running: it is then not replaced under itself (and on Windows the
/// installer would close it, with any `ling update` running in one of its terminals).
fn app_is_running() -> bool {
    let probe = if cfg!(windows) {
        std::process::Command::new("tasklist")
            .args(["/FI", "IMAGENAME eq Mightling.exe", "/NH"])
            .output()
    } else {
        std::process::Command::new("pgrep").args(["-x", "Mightling"]).output()
    };
    match probe {
        Ok(output) if cfg!(windows) => String::from_utf8_lossy(&output.stdout).contains("Mightling.exe"),
        Ok(output) => output.status.success(),
        Err(_) => false,
    }
}

/// Puts the app from a checked dmg at `app`, as install.sh does: the dmg mounted read-only at a
/// folder of `work` and never shown in the Finder, `Mightling.app` copied out with `ditto` under a
/// hidden name and renamed over the old one (moved aside first, so a failure leaves it in place),
/// the dmg detached whatever happened, and any quarantine mark cleared. `run` runs a tool; the
/// tests pass a stand-in.
pub fn install_app_from_dmg(
    dmg: &Path,
    work: &Path,
    app: &Path,
    run: &dyn Fn(&str, &[&OsStr]) -> anyhow::Result<()>,
) -> anyhow::Result<()> {
    let dir = app.parent().context("the app's path has no folder")?;
    let name = app.file_name().context("the app's path has no name")?.to_string_lossy().into_owned();
    let staging = dir.join(format!(".{name}.new"));
    let aside = dir.join(format!(".{name}.old"));
    let mount = work.join("dmg");
    std::fs::create_dir_all(&mount)?;
    let _ = run("xattr", &[OsStr::new("-d"), OsStr::new("com.apple.quarantine"), dmg.as_os_str()]);
    run(
        "hdiutil",
        &[
            OsStr::new("attach"),
            dmg.as_os_str(),
            OsStr::new("-nobrowse"),
            OsStr::new("-readonly"),
            OsStr::new("-noverify"),
            OsStr::new("-noautoopen"),
            OsStr::new("-quiet"),
            OsStr::new("-mountpoint"),
            mount.as_os_str(),
        ],
    )?;
    let _ = std::fs::remove_dir_all(&staging);
    let copied = run("ditto", &[mount.join("Mightling.app").as_os_str(), staging.as_os_str()]);
    if run("hdiutil", &[OsStr::new("detach"), mount.as_os_str(), OsStr::new("-quiet")]).is_err() {
        let _ = run("hdiutil", &[OsStr::new("detach"), mount.as_os_str(), OsStr::new("-force"), OsStr::new("-quiet")]);
    }
    if let Err(error) = copied {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(error);
    }
    let _ = std::fs::remove_dir_all(&aside);
    if app.exists() {
        std::fs::rename(app, &aside).with_context(|| format!("could not move {} aside", app.display()))?;
    }
    std::fs::rename(&staging, app)?;
    let _ = std::fs::remove_dir_all(&aside);
    let _ = run("xattr", &[OsStr::new("-dr"), OsStr::new("com.apple.quarantine"), app.as_os_str()]);
    Ok(())
}

/// Runs a checked NSIS installer silently (`/S`: for this user, no administrator, no window, the
/// app not started), as install.ps1 does.
fn install_app_from_setup(setup: &Path) -> anyhow::Result<()> {
    let status = std::process::Command::new(setup)
        .arg("/S")
        .status()
        .with_context(|| format!("could not run {}", setup.display()))?;
    if !status.success() {
        bail!("the installer exited with {status}");
    }
    Ok(())
}

/// The installers' record of the desktop app, when this is a Mac or Windows and the app is still
/// where it says. A record whose app is gone (removed by the user) is deleted, so the app is not
/// brought back.
fn installed_app(install_dir: &Path) -> Option<(PathBuf, AppRecord)> {
    if !(cfg!(target_os = "macos") || cfg!(windows)) {
        return None;
    }
    let file = install_dir.parent()?.join(APP_RECORD);
    let record = parse_app_record(&std::fs::read_to_string(&file).ok()?)?;
    if !record.path.exists() {
        println!(
            "ℹ️  The desktop app is no longer at {}; `ling update` stops updating it",
            record.path.display()
        );
        let _ = std::fs::remove_file(&file);
        return None;
    }
    Some((file, record))
}

/// Runs `ling update`.
pub async fn run() -> anyhow::Result<()> {
    let Some(target) = target() else {
        bail!(
            "`ling update` has no release builds for {} on {}; releases carry Linux, macOS and Windows builds",
            std::env::consts::ARCH,
            std::env::consts::OS
        );
    };
    let repo = std::env::var("MIGHTLING_RELEASE_REPO")
        .ok()
        .filter(|repo| !repo.is_empty())
        .unwrap_or_else(|| RELEASE_REPO.to_string());
    let token = github_token(&repo);
    let client = reqwest::Client::builder()
        .user_agent(format!("ling/{}", MIGHTLING_VERSION.unwrap_or("source")))
        .connect_timeout(Duration::from_secs(15))
        .build()?;
    let authorized = |url: &str, request: reqwest::RequestBuilder| match &token {
        Some(token) if sends_token(url) => request.bearer_auth(token),
        _ => request,
    };

    let latest_url = format!("{GITHUB_API}repos/{repo}/releases/latest");
    let response = authorized(
        &latest_url,
        client
            .get(&latest_url)
            .header("Accept", "application/vnd.github+json"),
    )
    .send()
    .await
    .context("could not reach GitHub")?;
    if response.status() == reqwest::StatusCode::NOT_FOUND {
        bail!(
            "no published release found in {repo}. Drafts and pre-releases are not offered as \
             updates; a private repository needs a token with access to it (GH_TOKEN, \
             GITHUB_TOKEN or `gh auth login`)"
        );
    }
    let release: serde_json::Value = response.error_for_status()?.json().await?;
    let tag = release["tag_name"].as_str().unwrap_or_default();
    let latest = tag.trim_start_matches('v');

    let exe = std::env::current_exe()?.canonicalize()?;
    let install_dir = exe
        .parent()
        .context("the running executable has no parent directory")?;
    let exe_name = exe
        .file_name()
        .context("the running executable has no file name")?
        .to_string_lossy()
        .into_owned();
    let app = installed_app(install_dir).filter(|(_, record)| app_is_behind(&record.version, latest));

    let binaries_due = match decide(MIGHTLING_VERSION, latest) {
        Decision::UpToDate if app.is_none() => {
            println!("✅ Mightling {latest} is the latest release.");
            return Ok(());
        }
        Decision::NewerThanRelease if app.is_none() => {
            println!(
                "✅ This Mightling ({}) is newer than the latest release ({latest}); nothing to do.",
                MIGHTLING_VERSION.unwrap_or_default()
            );
            return Ok(());
        }
        Decision::UpToDate | Decision::NewerThanRelease => {
            println!("⬆️  ling is current; updating the desktop app to {latest}");
            false
        }
        Decision::Install => {
            match MIGHTLING_VERSION {
                Some(current) => println!("⬆️  Updating Mightling {current} → {latest}"),
                None => println!("⬆️  This ling was built from source; installing release {latest}"),
            }
            true
        }
    };

    let [mightling_asset, host_asset, sums_asset] = asset_names(&target);
    let assets: HashMap<&str, &str> = release["assets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|asset| Some((asset["name"].as_str()?, asset["url"].as_str()?)))
        .collect();
    if !assets.contains_key(sums_asset.as_str()) {
        bail!("release {tag} has no {sums_asset}; it carries no ling build for {target}");
    }

    let download = |name: String| {
        let url = assets[name.as_str()];
        let request = authorized(
            url,
            client
                .get(url)
                .header("Accept", "application/octet-stream"),
        );
        async move {
            println!("📥 {name}");
            let response = request.send().await?.error_for_status()?;
            anyhow::Ok(response.bytes().await?.to_vec())
        }
    };

    let sums_bytes = download(sums_asset.clone()).await?;
    let signed = signature_expected(
        latest,
        assets.contains_key(release_signature::SUMS_ASSET),
        assets.contains_key(release_signature::SIGNATURE_ASSET),
    )
    .with_context(|| format!("release {tag}"))?;
    let all_sums = if signed {
        Some(verify_signed_sums(&assets, &download, tag, &sums_asset, &sums_bytes).await?)
    } else {
        println!(
            "⚠️  release {tag} predates signed releases ({}); it is checked against its \
             checksums only",
            release_signature::SIGNED_SINCE
        );
        None
    };
    let sums = parse_sums(&String::from_utf8(sums_bytes)?);

    // The desktop app, downloaded and checked against the signed SHA256SUMS with everything else,
    // before anything is replaced. Written here, not by a browser: no quarantine mark, no Mark of
    // the Web.
    let work = std::env::temp_dir().join(format!("ling-update-{}", std::process::id()));
    let mut staged_app: Option<(PathBuf, PathBuf, AppRecord)> = None;
    if let Some((record_file, record)) = app {
        let asset = desktop_app_asset(assets.keys().copied(), std::env::consts::OS, std::env::consts::ARCH);
        match (asset, &all_sums) {
            (None, _) => println!("ℹ️  release {tag} carries no desktop app for this machine; keeping the installed one"),
            (Some(_), None) => println!("ℹ️  release {tag} is not signed, so its desktop app is not installed"),
            (Some(_), Some(_)) if app_is_running() => println!(
                "⚠️  The desktop app is running, so it was not updated: quit Mightling, then run `ling update` again"
            ),
            (Some(asset), Some(all_sums)) => {
                let expected = parse_sums(&String::from_utf8_lossy(all_sums))
                    .remove(&asset)
                    .with_context(|| format!("{} has no checksum for {asset}; nothing was installed", release_signature::SUMS_ASSET))?;
                let bytes = download(asset.clone()).await?;
                if hex_sha256(&bytes) != expected {
                    bail!("{asset} does not match its checksum in {}; nothing was installed", release_signature::SUMS_ASSET);
                }
                std::fs::create_dir_all(&work)?;
                let staged = work.join(&asset);
                std::fs::write(&staged, &bytes).with_context(|| format!("could not write {}", staged.display()))?;
                staged_app = Some((staged, record_file, record));
            }
        }
    }
    if !binaries_due {
        return finish_app(staged_app, &work, tag);
    }
    let result = install_binaries(
        &assets, &download, &sums, &sums_asset, &target, tag, install_dir, (mightling_asset, exe_name), host_asset,
    )
    .await;
    if let Err(error) = result {
        let _ = std::fs::remove_dir_all(&work);
        return Err(error);
    }
    if cfg!(windows) {
        println!("ℹ️  Windows builds are a preview and are not signed: Smart App Control must be off to run them.");
    }
    println!("✅ Mightling {latest} installed in {}", install_dir.display());
    finish_app(staged_app, &work, tag)
}

/// Installs the staged desktop app, if any, records its release, and removes the work folder.
fn finish_app(staged: Option<(PathBuf, PathBuf, AppRecord)>, work: &Path, tag: &str) -> anyhow::Result<()> {
    let Some((staged, record_file, record)) = staged else {
        let _ = std::fs::remove_dir_all(work);
        return Ok(());
    };
    println!("📦 Updating the desktop app at {}", record.path.display());
    let installed = if staged.extension() == Some(OsStr::new("dmg")) {
        install_app_from_dmg(&staged, work, &record.path, &run_tool)
    } else {
        install_app_from_setup(&staged)
    };
    let _ = std::fs::remove_dir_all(work);
    installed.context(
        "the desktop app was not updated (it stays as it was); run `ling update` again, or the \
         one-line installer",
    )?;
    let updated = AppRecord { path: record.path, version: tag.to_string() };
    std::fs::write(&record_file, format_app_record(&updated))
        .with_context(|| format!("could not write {}", record_file.display()))?;
    println!("✅ Desktop app {tag} installed at {}", updated.path.display());
    Ok(())
}

/// Downloads, checks and swaps in the binaries. Everything is downloaded and verified before
/// anything is replaced, so a failure part-way leaves the installation as it was.
#[allow(clippy::too_many_arguments)]
async fn install_binaries<F, Fut>(
    assets: &HashMap<&str, &str>,
    download: &F,
    sums: &HashMap<String, String>,
    sums_asset: &str,
    target: &str,
    tag: &str,
    install_dir: &Path,
    (mightling_asset, exe_name): (String, String),
    host_asset: String,
) -> anyhow::Result<()>
where
    F: Fn(String) -> Fut,
    Fut: std::future::Future<Output = anyhow::Result<Vec<u8>>>,
{
    for name in [&mightling_asset, &host_asset] {
        if !assets.contains_key(name.as_str()) {
            bail!("release {tag} has no {name}; it carries no ling build for {target}");
        }
    }
    let mut wanted = vec![(mightling_asset, exe_name), (host_asset, installed_name(CODE_MODE_HOST))];
    let mut optional: Vec<&str> = optional_commands().to_vec();
    if cfg!(windows) {
        optional.extend(WINDOWS_SANDBOX_HELPERS);
    }
    for name in optional {
        // Only Linux releases build the file index and the Signal bridge; elsewhere their absence
        // is not news.
        if (name == DOCS_COMMAND || name == SIGNAL_COMMAND) && !cfg!(target_os = "linux") {
            continue;
        }
        let asset = format!("{name}-{target}.gz");
        if assets.contains_key(asset.as_str()) {
            wanted.push((asset, installed_name(name)));
        } else {
            println!("ℹ️  release {tag} carries no {name}; keeping the installed one");
        }
    }
    let mut binaries = Vec::new();
    for (asset, installed) in wanted {
        let archive = download(asset.clone()).await?;
        let expected = sums
            .get(&asset)
            .with_context(|| format!("{sums_asset} has no checksum for {asset}"))?;
        if &hex_sha256(&archive) != expected {
            bail!("{asset} does not match its checksum in {sums_asset}; nothing was installed");
        }
        let mut binary = Vec::new();
        flate2::read::GzDecoder::new(archive.as_slice()).read_to_end(&mut binary)?;
        binaries.push((installed, binary));
    }
    for (installed, binary) in binaries {
        replace(install_dir, &installed, &binary)?;
        if optional_commands().contains(&installed.as_str()) && linked_onto_path(&installed) {
            link_onto_path(install_dir, &installed);
        }
    }
    Ok(())
}

/// Whether a release's signature is checked: always when it carries one. A release of
/// `SIGNED_SINCE` or later without one is refused, since an unsigned one did not come from
/// Mightling's release job; only an older release installs on its checksums alone.
pub fn signature_expected(
    version: &str,
    has_sums: bool,
    has_signature: bool,
) -> anyhow::Result<bool> {
    let missing = match (has_sums, has_signature) {
        (true, true) => return Ok(true),
        (false, true) => release_signature::SUMS_ASSET,
        (_, false) => release_signature::SIGNATURE_ASSET,
    };
    if has_signature || release_signature::signing_required(version) {
        bail!(
            "is not signed (it has no {missing}); nothing was installed. Every release from {} on \
             is signed by Mightling's release key, so an unsigned one did not come from its \
             release job",
            release_signature::SIGNED_SINCE
        );
    }
    Ok(false)
}

/// Downloads a release's `SHA256SUMS`, its signature and any key transition, and checks them with
/// [`check_signed_sums`].
async fn verify_signed_sums<F, Fut>(
    assets: &HashMap<&str, &str>,
    download: &F,
    tag: &str,
    sums_asset: &str,
    sums_bytes: &[u8],
) -> anyhow::Result<Vec<u8>>
where
    F: Fn(String) -> Fut,
    Fut: std::future::Future<Output = anyhow::Result<Vec<u8>>>,
{
    let all_sums = download(release_signature::SUMS_ASSET.to_string()).await?;
    let signature = String::from_utf8(download(release_signature::SIGNATURE_ASSET.to_string()).await?)?;
    let transition = if assets.contains_key(release_signature::TRANSITION_KEY_ASSET)
        && assets.contains_key(release_signature::TRANSITION_SIGNATURE_ASSET)
    {
        Some((
            String::from_utf8(download(release_signature::TRANSITION_KEY_ASSET.to_string()).await?)?,
            String::from_utf8(
                download(release_signature::TRANSITION_SIGNATURE_ASSET.to_string()).await?,
            )?,
        ))
    } else {
        None
    };
    let trusted = release_signature::parse_keys(release_signature::TRUSTED_KEYS)?;
    let signer = check_signed_sums(
        sums_asset,
        sums_bytes,
        &all_sums,
        &signature,
        transition.as_ref().map(|(key, sig)| (key.as_str(), sig.as_str())),
        &trusted,
    )
    .with_context(|| format!("release {tag} failed its signature check; nothing was installed"))?;
    println!("🔏 {} is signed by {signer}", release_signature::SUMS_ASSET);
    Ok(all_sums)
}

/// The signature check without the network: `SHA256SUMS` must be signed by a trusted key (or one a
/// trusted key endorses), and the per-target checksum file must be the one it lists, which is what
/// ties every binary to the signature.
pub fn check_signed_sums(
    sums_asset: &str,
    sums_bytes: &[u8],
    all_sums: &[u8],
    signature: &str,
    transition: Option<(&str, &str)>,
    trusted: &[[u8; 32]],
) -> anyhow::Result<String> {
    let signer = release_signature::verify_release(all_sums, signature, transition, trusted)?;
    let listed = parse_sums(&String::from_utf8_lossy(all_sums));
    let expected = listed.get(sums_asset).with_context(|| {
        format!("{} has no checksum for {sums_asset}", release_signature::SUMS_ASSET)
    })?;
    if &hex_sha256(sums_bytes) != expected {
        bail!(
            "{sums_asset} does not match its checksum in {}",
            release_signature::SUMS_ASSET
        );
    }
    Ok(signer)
}

/// Links `~/.local/bin/<name>` to the installed command, as `ling-admin codex build` does. Only
/// a missing file or an existing link is replaced: a real file of that name belongs to something
/// else, and is reported instead. Nothing on Windows, where install.ps1 puts the install folder
/// itself on PATH.
#[cfg(windows)]
fn link_onto_path(_install_dir: &Path, _name: &str) {}

#[cfg(not(windows))]
fn link_onto_path(install_dir: &Path, name: &str) {
    let Some(home) = ling_node_locator::home_dir() else {
        return;
    };
    let bin = Path::new(&home).join(".local").join("bin");
    let link = bin.join(name);
    if link.exists() && !link.is_symlink() {
        println!("⚠️  {} exists and is not a link; leaving it", link.display());
        return;
    }
    let staging = bin.join(format!(".{name}.link"));
    let _ = std::fs::remove_file(&staging);
    let linked = std::fs::create_dir_all(&bin)
        .and_then(|()| std::os::unix::fs::symlink(install_dir.join(name), &staging))
        .and_then(|()| std::fs::rename(&staging, &link));
    if let Err(error) = linked {
        println!("⚠️  could not link {}: {error}", link.display());
    }
}

/// Writes next to the target and renames over it, so a running `ling` keeps its own file and a
/// new one never starts from a half-written binary.
#[cfg(unix)]
fn replace(dir: &Path, name: &str, binary: &[u8]) -> anyhow::Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let staging = dir.join(format!(".{name}.update"));
    std::fs::write(&staging, binary)
        .with_context(|| format!("could not write to {}", dir.display()))?;
    std::fs::set_permissions(&staging, std::fs::Permissions::from_mode(0o755))?;
    std::fs::rename(&staging, dir.join(name))?;
    Ok(())
}

/// Windows cannot rename over a running `.exe`, but it can rename the running file itself. So the
/// old file is moved aside to `<name>.old` (or `<name>.<n>.old` while an older one is still in
/// use), the new one takes its name, and [`sweep_replaced_binaries`] deletes the leftovers at the
/// next start.
#[cfg(not(unix))]
fn replace(dir: &Path, name: &str, binary: &[u8]) -> anyhow::Result<()> {
    let staging = dir.join(format!(".{name}.update"));
    std::fs::write(&staging, binary)
        .with_context(|| format!("could not write to {}", dir.display()))?;
    replace_aside(dir, name, &staging)
}

/// Puts `staging` in place of `dir/name`, moving any existing file aside first. Portable, so its
/// test runs on Linux too.
pub fn replace_aside(dir: &Path, name: &str, staging: &Path) -> anyhow::Result<()> {
    let target = dir.join(name);
    if target.exists() {
        let mut aside = dir.join(format!("{name}{OLD_SUFFIX}"));
        let mut copy = 1;
        while aside.exists() && std::fs::remove_file(&aside).is_err() {
            copy += 1;
            aside = dir.join(format!("{name}.{copy}{OLD_SUFFIX}"));
        }
        std::fs::rename(&target, &aside).with_context(|| format!("could not move {} aside", target.display()))?;
    }
    std::fs::rename(staging, &target)?;
    Ok(())
}

/// The suffix [`replace_aside`] gives a binary it moved aside.
pub const OLD_SUFFIX: &str = ".old";

/// Deletes the binaries a Windows update moved aside, now that they are no longer running. Called
/// first thing at start; one still in use (another `ling` running) stays until a later start.
pub fn sweep_replaced_binaries() {
    let Some(dir) = std::env::current_exe().ok().and_then(|exe| exe.parent().map(Path::to_path_buf)) else {
        return;
    };
    sweep_old_in(&dir);
}

/// [`sweep_replaced_binaries`] for one folder.
pub fn sweep_old_in(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        if entry.file_name().to_string_lossy().ends_with(OLD_SUFFIX) {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::release_signature::testing::{key_line, sign};
    use std::ffi::OsString;
    use ed25519_dalek::SigningKey;

    /// A release's per-target sums file and the SHA256SUMS that lists it, as the release job
    /// writes them.
    fn signed_release(key: &SigningKey) -> (Vec<u8>, Vec<u8>, String) {
        let per_target = b"aaaa  ling-x.gz\nbbbb  codex-code-mode-host-x.gz\n".to_vec();
        let all = format!(
            "{}  ling-x.sha256sums\ncccc  ling-admin.whl\n",
            hex_sha256(&per_target)
        )
        .into_bytes();
        let signature = sign(&all, key, release_signature::RELEASE_NAMESPACE);
        (per_target, all, signature)
    }

    #[test]
    fn the_installers_record_of_the_app_reads_back() {
        let record = AppRecord { path: PathBuf::from("/Applications/Mightling.app"), version: "v1.7.2".into() };
        assert_eq!(parse_app_record(&format_app_record(&record)), Some(record));
        // install.ps1 writes Windows line endings.
        let windows = parse_app_record("\u{feff}path=C:\\Users\\a\\AppData\\Local\\Programs\\Mightling\\App\r\nversion=v1.7.2\r\n");
        assert_eq!(windows.map(|record| record.version), Some("v1.7.2".to_string()));
        assert_eq!(parse_app_record("version=v1.7.2\n"), None);
        assert_eq!(parse_app_record("path=\n"), None);
    }

    #[test]
    fn the_app_is_updated_only_when_its_release_is_older() {
        assert!(app_is_behind("v1.7.2", "1.7.3"));
        assert!(!app_is_behind("v1.7.2", "1.7.2"));
        assert!(!app_is_behind("v1.8.0", "1.7.2"));
        // A record without a version is of an unknown release: updated.
        assert!(app_is_behind("", "1.7.2"));
    }

    #[test]
    fn the_app_asset_is_the_one_for_this_machine() {
        let names = [
            "ling-aarch64-apple-darwin.sha256sums",
            "Mightling-1.7.2-arm64-preview.dmg",
            "Mightling-1.7.2-x64-preview.dmg",
            "Mightling-Setup-1.7.2-arm64.exe",
            "Mightling-Setup-1.7.2-x64.exe",
            "mightling_1.7.2_arm64.deb",
        ];
        let pick = |os, arch| desktop_app_asset(names, os, arch);
        assert_eq!(pick("macos", "aarch64").as_deref(), Some("Mightling-1.7.2-arm64-preview.dmg"));
        assert_eq!(pick("macos", "x86_64").as_deref(), Some("Mightling-1.7.2-x64-preview.dmg"));
        assert_eq!(pick("windows", "aarch64").as_deref(), Some("Mightling-Setup-1.7.2-arm64.exe"));
        assert_eq!(pick("windows", "x86_64").as_deref(), Some("Mightling-Setup-1.7.2-x64.exe"));
        assert_eq!(pick("linux", "aarch64"), None);
        // A signed dmg will drop "-preview".
        assert_eq!(
            desktop_app_asset(["Mightling-1.8.0-arm64.dmg"], "macos", "aarch64").as_deref(),
            Some("Mightling-1.8.0-arm64.dmg")
        );
    }

    /// A stand-in for hdiutil, ditto and xattr: attach "mounts" an app whose Info.plist says
    /// `new`, ditto copies it (or fails when `ditto_fails`), and every call is recorded.
    fn fake_tools(calls: &std::sync::Mutex<Vec<String>>, ditto_fails: bool) -> impl Fn(&str, &[&OsStr]) -> anyhow::Result<()> + '_ {
        move |program: &str, args: &[&OsStr]| {
            let args: Vec<String> = args.iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
            calls.lock().unwrap().push(format!("{program} {}", args.join(" ")));
            match (program, args.first().map(String::as_str)) {
                ("hdiutil", Some("attach")) => {
                    let mount = Path::new(&args[args.len() - 1]);
                    std::fs::create_dir_all(mount.join("Mightling.app/Contents"))?;
                    std::fs::write(mount.join("Mightling.app/Contents/Info.plist"), "new")?;
                }
                ("hdiutil", Some("detach")) => std::fs::remove_dir_all(Path::new(&args[1]).join("Mightling.app"))?,
                ("ditto", _) if ditto_fails => bail!("ditto: No space left on device"),
                ("ditto", _) => {
                    let to = Path::new(&args[1]);
                    std::fs::create_dir_all(to.join("Contents"))?;
                    std::fs::copy(Path::new(&args[0]).join("Contents/Info.plist"), to.join("Contents/Info.plist"))?;
                }
                _ => {}
            }
            Ok(())
        }
    }

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-update-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("Applications/Mightling.app/Contents")).unwrap();
        std::fs::write(dir.join("Applications/Mightling.app/Contents/Info.plist"), "old").unwrap();
        std::fs::write(dir.join("Applications/Mightling.app/Contents/left-over"), "old").unwrap();
        dir
    }

    #[test]
    fn a_dmg_replaces_the_app_in_place_and_is_detached() {
        let dir = scratch("dmg");
        let app = dir.join("Applications/Mightling.app");
        let calls = std::sync::Mutex::new(Vec::new());
        install_app_from_dmg(&dir.join("app.dmg"), &dir.join("work"), &app, &fake_tools(&calls, false)).unwrap();
        assert_eq!(std::fs::read_to_string(app.join("Contents/Info.plist")).unwrap(), "new");
        assert!(!app.join("Contents/left-over").exists());
        let names: Vec<_> = std::fs::read_dir(dir.join("Applications")).unwrap().flatten().map(|entry| entry.file_name()).collect();
        assert_eq!(names, vec![OsString::from("Mightling.app")]);
        let calls = calls.into_inner().unwrap();
        let attach = calls.iter().position(|call| call.starts_with("hdiutil attach")).unwrap();
        for flag in ["-nobrowse", "-readonly", "-noautoopen", "-mountpoint"] {
            assert!(calls[attach].split(' ').any(|word| word == flag), "{}", calls[attach]);
        }
        assert!(calls.iter().any(|call| call.starts_with("hdiutil detach")));
        assert_eq!(calls.last().unwrap(), &format!("xattr -dr com.apple.quarantine {}", app.display()));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_copy_that_fails_keeps_the_old_app_and_still_detaches() {
        let dir = scratch("dmg-fails");
        let app = dir.join("Applications/Mightling.app");
        let calls = std::sync::Mutex::new(Vec::new());
        let result = install_app_from_dmg(&dir.join("app.dmg"), &dir.join("work"), &app, &fake_tools(&calls, true));
        assert!(result.is_err());
        assert_eq!(std::fs::read_to_string(app.join("Contents/Info.plist")).unwrap(), "old");
        assert!(!dir.join("Applications/.Mightling.app.new").exists());
        assert!(calls.into_inner().unwrap().iter().any(|call| call.starts_with("hdiutil detach")));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_unsigned_release_from_the_cut_over_on_is_refused() {
        assert!(signature_expected("1.5.0", true, true).unwrap());
        assert!(signature_expected("1.4.1", true, true).unwrap());
        assert!(!signature_expected("1.4.1", false, false).unwrap());
        for (version, sums, signature) in
            [("1.5.0", true, false), ("1.6.0", false, false), ("1.4.1", false, true)]
        {
            let error = signature_expected(version, sums, signature).unwrap_err();
            assert!(format!("{error:#}").contains("is not signed"), "{version}");
        }
    }

    #[test]
    fn a_signed_release_ties_the_target_sums_to_the_signature() {
        let key = SigningKey::from_bytes(&[3; 32]);
        let trusted = [key.verifying_key().to_bytes()];
        let (per_target, all, signature) = signed_release(&key);
        check_signed_sums("ling-x.sha256sums", &per_target, &all, &signature, None, &trusted)
            .unwrap();
    }

    #[test]
    fn a_swapped_target_sums_file_is_refused_even_with_a_good_signature() {
        let key = SigningKey::from_bytes(&[3; 32]);
        let trusted = [key.verifying_key().to_bytes()];
        let (_, all, signature) = signed_release(&key);
        let swapped = b"ffff  ling-x.gz\n";
        let error =
            check_signed_sums("ling-x.sha256sums", swapped, &all, &signature, None, &trusted)
                .unwrap_err();
        assert!(format!("{error:#}").contains("does not match its checksum in SHA256SUMS"));
    }

    #[test]
    fn a_release_signed_by_another_key_is_refused() {
        let key = SigningKey::from_bytes(&[3; 32]);
        let attacker = SigningKey::from_bytes(&[4; 32]);
        let trusted = [key.verifying_key().to_bytes()];
        let (per_target, all, signature) = signed_release(&attacker);
        assert!(
            check_signed_sums("ling-x.sha256sums", &per_target, &all, &signature, None, &trusted)
                .is_err()
        );
    }

    #[test]
    fn a_release_after_a_key_rotation_installs_through_the_transition() {
        let old = SigningKey::from_bytes(&[3; 32]);
        let new = SigningKey::from_bytes(&[5; 32]);
        let trusted = [old.verifying_key().to_bytes()];
        let (per_target, all, signature) = signed_release(&new);
        let line = key_line(&new);
        let endorsement = sign(line.as_bytes(), &old, release_signature::KEY_NAMESPACE);
        check_signed_sums(
            "ling-x.sha256sums",
            &per_target,
            &all,
            &signature,
            Some((&line, &endorsement)),
            &trusted,
        )
        .unwrap();
    }

    #[test]
    fn windows_assets_are_named_for_the_msvc_targets() {
        assert_eq!(target_for("aarch64", "windows").as_deref(), Some("aarch64-pc-windows-msvc"));
        assert_eq!(target_for("x86_64", "windows").as_deref(), Some("x86_64-pc-windows-msvc"));
        assert_eq!(target_for("aarch64", "linux").as_deref(), Some("aarch64-unknown-linux-gnu"));
        assert_eq!(asset_names("aarch64-pc-windows-msvc")[0], "ling-aarch64-pc-windows-msvc.gz");
    }

    #[test]
    fn a_running_binary_is_moved_aside_and_swept_later() {
        let dir = std::env::temp_dir().join(format!("mightling-update-aside-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap_or_default();
        std::fs::write(dir.join("ling.exe"), b"old").unwrap_or_default();
        std::fs::write(dir.join(".mightling.exe.update"), b"new").unwrap_or_default();
        replace_aside(&dir, "ling.exe", &dir.join(".mightling.exe.update")).unwrap_or_default();
        assert_eq!(std::fs::read(dir.join("ling.exe")).unwrap_or_default(), b"new");
        assert_eq!(std::fs::read(dir.join("ling.exe.old")).unwrap_or_default(), b"old");
        // A second update before the next start finds the first leftover removable.
        std::fs::write(dir.join(".mightling.exe.update"), b"newer").unwrap_or_default();
        replace_aside(&dir, "ling.exe", &dir.join(".mightling.exe.update")).unwrap_or_default();
        assert_eq!(std::fs::read(dir.join("ling.exe")).unwrap_or_default(), b"newer");
        assert_eq!(std::fs::read(dir.join("ling.exe.old")).unwrap_or_default(), b"new");
        sweep_old_in(&dir);
        assert!(!dir.join("ling.exe.old").exists());
        assert!(dir.join("ling.exe").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn versions_decide_whether_to_install() {
        assert_eq!(decide(Some("1.3.0"), "1.3.0"), Decision::UpToDate);
        assert_eq!(decide(Some("1.2.0"), "1.3.0"), Decision::Install);
        assert_eq!(decide(Some("1.4.0"), "1.3.0"), Decision::NewerThanRelease);
        assert_eq!(decide(Some("1.3.0-rc.1"), "1.3.0"), Decision::Install);
        assert_eq!(decide(None, "1.3.0"), Decision::Install);
    }

    #[test]
    fn the_token_goes_to_the_github_api_and_nowhere_else() {
        assert!(sends_token("https://api.github.com/repos/o/r/releases/assets/1"));
        assert!(!sends_token("https://objects.githubusercontent.com/x"));
        assert!(!sends_token("https://api.github.com.evil.example/repos/o/r"));
        assert!(!sends_token("http://api.github.com/repos/o/r"));
        assert!(!sends_token("https://evil.example/?https://api.github.com/"));
    }

    #[test]
    fn checksum_lines_parse_in_both_sha256sum_modes() {
        let sums = parse_sums("ABC123  ling-x.gz\ndef456 *codex-code-mode-host-x.gz\n\n");
        assert_eq!(sums.get("ling-x.gz").map(String::as_str), Some("abc123"));
        assert_eq!(
            sums.get("codex-code-mode-host-x.gz").map(String::as_str),
            Some("def456")
        );
    }

    #[test]
    fn assets_are_named_after_the_target() {
        let [ling, host, sums] = asset_names("aarch64-unknown-linux-gnu");
        assert_eq!(ling, "ling-aarch64-unknown-linux-gnu.gz");
        assert_eq!(host, "codex-code-mode-host-aarch64-unknown-linux-gnu.gz");
        assert_eq!(sums, "ling-aarch64-unknown-linux-gnu.sha256sums");
        assert_eq!(hex_sha256(b"abc").len(), 64);
        assert_eq!(
            optional_asset_names("aarch64-unknown-linux-gnu"),
            [
                "ling-search-aarch64-unknown-linux-gnu.gz".to_string(),
                "ling-fetch-aarch64-unknown-linux-gnu.gz".to_string(),
                "ling-code-aarch64-unknown-linux-gnu.gz".to_string(),
                "ling-docs-aarch64-unknown-linux-gnu.gz".to_string(),
                "ling-signal-aarch64-unknown-linux-gnu.gz".to_string()
            ]
        );
        // `ling signal setup` finds the bridge by this name beside `ling` (signal::bridge_beside),
        // and nothing puts it on PATH.
        assert!(optional_commands().contains(&"ling-signal"));
        assert!(!linked_onto_path("ling-signal") && linked_onto_path("ling-code"));
        // The launcher finds the router and the file index by these names beside `ling`
        // (code_index::binary, docs_index::binary).
        assert!(optional_commands().contains(&"ling-code"));
        assert!(optional_commands().contains(&"ling-docs"));
    }

    #[test]
    fn every_released_platform_has_a_target_and_others_have_none() {
        // The same names install.sh and the release workflow give the assets.
        assert_eq!(target_for("aarch64", "linux").as_deref(), Some("aarch64-unknown-linux-gnu"));
        assert_eq!(target_for("x86_64", "linux").as_deref(), Some("x86_64-unknown-linux-gnu"));
        assert_eq!(target_for("aarch64", "macos").as_deref(), Some("aarch64-apple-darwin"));
        assert_eq!(target_for("x86_64", "macos").as_deref(), Some("x86_64-apple-darwin"));
        assert_eq!(target_for("riscv64", "linux"), None);
        assert_eq!(target_for("x86_64", "freebsd"), None);
        assert!(target().is_some());
    }
}
