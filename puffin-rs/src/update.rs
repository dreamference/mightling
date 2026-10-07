//! `puffin update`: replace this installation with the latest Puffin release's prebuilt binaries.
//!
//! Codex's own `update` picks an installer (npm, brew, OpenAI's install script) from how Codex was
//! installed, which would put upstream Codex in Puffin's place. Patch 0008 sends the subcommand
//! here instead. The release workflow attaches, per target, a gzipped `puffin`, a gzipped
//! `codex-code-mode-host`, the gzipped web commands `puffin-search` and `puffin-fetch`, the gzipped
//! code index router `puffin-code`, and a `sha256sums` file covering all of them; this downloads
//! the latest published release's assets, verifies every archive against the checksum file, and
//! swaps the binaries in next to the running executable. The web commands and `puffin-code` are
//! optional, because earlier releases do not carry them (v1.3.0 has the web commands and no
//! `puffin-code`); the installed ones are then kept.
//!
//! The repository is public, so no token is needed; one is sent when present (`GH_TOKEN`,
//! `GITHUB_TOKEN`, or whatever `gh auth token` prints), which raises GitHub's rate limit and reads
//! a private fork named by `PUFFIN_RELEASE_REPO`.

use std::collections::HashMap;
use std::io::Read;
use std::path::Path;
use std::time::Duration;

use anyhow::Context;
use anyhow::bail;
use sha2::Digest;
use sha2::Sha256;

/// Where releases are published. `PUFFIN_RELEASE_REPO` overrides it, e.g. for a fork.
pub const RELEASE_REPO: &str = "dreamference/dgx-lunny";

/// The Puffin release this binary was built as. The release workflow sets `PUFFIN_VERSION` when it
/// builds; a local `puffin-admin codex build` does not, and such a binary counts as a source build.
pub const PUFFIN_VERSION: Option<&str> = option_env!("PUFFIN_VERSION");

/// Code Mode's host process, which Codex looks for next to its own executable under this name.
const CODE_MODE_HOST: &str = "codex-code-mode-host";

/// The agent's web commands (`puffin-web-rs/`), installed beside `puffin` and linked into
/// `~/.local/bin`, because the prompt names them and the model's shell must find them.
pub const WEB_COMMANDS: [&str; 2] = ["puffin-search", "puffin-fetch"];

/// The code index router (`puffin-code-rs/`). The launcher looks for it beside `puffin`
/// (`code_index::binary`), and the prompt names it, so it is linked into `~/.local/bin` as well.
/// The release carries the router only: the indexers it runs are fetched by
/// `puffin-admin code setup`.
pub const CODE_COMMAND: &str = "puffin-code";

/// The commands a release may carry beside `puffin`, each installed if its asset is present and
/// kept as installed if not.
pub fn optional_commands() -> [&'static str; 3] {
    [WEB_COMMANDS[0], WEB_COMMANDS[1], CODE_COMMAND]
}

/// What `puffin update` should do, given this build's version and the latest release's.
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

/// The Rust target triple the release assets are named after.
pub fn target() -> String {
    target_for(std::env::consts::ARCH, std::env::consts::OS)
}

/// [`target`] for a given architecture and operating system, as `std::env::consts` names them.
pub fn target_for(arch: &str, os: &str) -> String {
    match os {
        "windows" => format!("{arch}-pc-windows-msvc"),
        _ => format!("{arch}-unknown-linux-gnu"),
    }
}

/// The Windows sandbox's helpers, which Codex looks for beside its own executable. Windows
/// releases carry them; they are optional like the web commands.
pub const WINDOWS_SANDBOX_HELPERS: [&str; 2] = ["codex-windows-sandbox-setup", "codex-command-runner"];

/// The file name a command is installed under: `puffin-search.exe` on Windows. The release's
/// `.gz` assets keep the bare name and hold the `.exe`.
pub fn installed_name(name: &str) -> String {
    format!("{name}{}", std::env::consts::EXE_SUFFIX)
}

/// Asset names for a target: the two gzipped binaries and the checksum file.
pub fn asset_names(target: &str) -> [String; 3] {
    [
        format!("puffin-{target}.gz"),
        format!("{CODE_MODE_HOST}-{target}.gz"),
        format!("puffin-{target}.sha256sums"),
    ]
}

/// Asset names of the optional commands for a target, in `optional_commands` order.
pub fn optional_asset_names(target: &str) -> [String; 3] {
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

fn github_token() -> Option<String> {
    for name in ["GH_TOKEN", "GITHUB_TOKEN"] {
        if let Ok(token) = std::env::var(name)
            && !token.trim().is_empty()
        {
            return Some(token.trim().to_string());
        }
    }
    let output = std::process::Command::new("gh")
        .args(["auth", "token"])
        .output()
        .ok()?;
    let token = String::from_utf8(output.stdout).ok()?.trim().to_string();
    (output.status.success() && !token.is_empty()).then_some(token)
}

/// Runs `puffin update`.
pub async fn run() -> anyhow::Result<()> {
    if !cfg!(any(target_os = "linux", windows)) {
        bail!("`puffin update` only has Linux and Windows release builds to install");
    }
    let repo = std::env::var("PUFFIN_RELEASE_REPO")
        .ok()
        .filter(|repo| !repo.is_empty())
        .unwrap_or_else(|| RELEASE_REPO.to_string());
    let token = github_token();
    let client = reqwest::Client::builder()
        .user_agent(format!("puffin/{}", PUFFIN_VERSION.unwrap_or("source")))
        .connect_timeout(Duration::from_secs(15))
        .build()?;
    let authorized = |request: reqwest::RequestBuilder| match &token {
        Some(token) => request.bearer_auth(token),
        None => request,
    };

    let response = authorized(
        client
            .get(format!("https://api.github.com/repos/{repo}/releases/latest"))
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

    match decide(PUFFIN_VERSION, latest) {
        Decision::UpToDate => {
            println!("✅ Puffin {latest} is the latest release.");
            return Ok(());
        }
        Decision::NewerThanRelease => {
            println!(
                "✅ This Puffin ({}) is newer than the latest release ({latest}); nothing to do.",
                PUFFIN_VERSION.unwrap_or_default()
            );
            return Ok(());
        }
        Decision::Install => match PUFFIN_VERSION {
            Some(current) => println!("⬆️  Updating Puffin {current} → {latest}"),
            None => println!("⬆️  This puffin was built from source; installing release {latest}"),
        },
    }

    let target = target();
    let [puffin_asset, host_asset, sums_asset] = asset_names(&target);
    let assets: HashMap<&str, &str> = release["assets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|asset| Some((asset["name"].as_str()?, asset["url"].as_str()?)))
        .collect();
    for name in [&puffin_asset, &host_asset, &sums_asset] {
        if !assets.contains_key(name.as_str()) {
            bail!("release {tag} has no {name}; it carries no puffin build for {target}");
        }
    }

    let download = |name: String| {
        let request = authorized(
            client
                .get(assets[name.as_str()])
                .header("Accept", "application/octet-stream"),
        );
        async move {
            println!("📥 {name}");
            let response = request.send().await?.error_for_status()?;
            anyhow::Ok(response.bytes().await?.to_vec())
        }
    };

    let sums = parse_sums(&String::from_utf8(download(sums_asset.clone()).await?)?);
    let exe = std::env::current_exe()?.canonicalize()?;
    let install_dir = exe
        .parent()
        .context("the running executable has no parent directory")?;
    let exe_name = exe
        .file_name()
        .context("the running executable has no file name")?
        .to_string_lossy()
        .into_owned();

    // Everything is downloaded and verified before anything is replaced, so a failure part-way
    // leaves the installation as it was.
    let mut wanted = vec![(puffin_asset, exe_name), (host_asset, installed_name(CODE_MODE_HOST))];
    let mut optional: Vec<&str> = optional_commands().to_vec();
    if cfg!(windows) {
        optional.extend(WINDOWS_SANDBOX_HELPERS);
    }
    for name in optional {
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
        if optional_commands().contains(&installed.as_str()) {
            link_onto_path(install_dir, &installed);
        }
    }
    if cfg!(windows) {
        println!("ℹ️  Windows builds are a preview and are not signed: Smart App Control must be off to run them.");
    }
    println!("✅ Puffin {latest} installed in {}", install_dir.display());
    Ok(())
}

/// Links `~/.local/bin/<name>` to the installed command, as `puffin-admin codex build` does. Only
/// a missing file or an existing link is replaced: a real file of that name belongs to something
/// else, and is reported instead. Nothing on Windows, where install.ps1 puts the install folder
/// itself on PATH.
#[cfg(windows)]
fn link_onto_path(_install_dir: &Path, _name: &str) {}

#[cfg(not(windows))]
fn link_onto_path(install_dir: &Path, name: &str) {
    let Some(home) = puffin_node_locator::home_dir() else {
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

/// Writes next to the target and renames over it, so a running `puffin` keeps its own file and a
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
/// first thing at start; one still in use (another `puffin` running) stays until a later start.
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

    #[test]
    fn windows_assets_are_named_for_the_msvc_targets() {
        assert_eq!(target_for("aarch64", "windows"), "aarch64-pc-windows-msvc");
        assert_eq!(target_for("x86_64", "windows"), "x86_64-pc-windows-msvc");
        assert_eq!(target_for("aarch64", "linux"), "aarch64-unknown-linux-gnu");
        assert_eq!(asset_names("aarch64-pc-windows-msvc")[0], "puffin-aarch64-pc-windows-msvc.gz");
    }

    #[test]
    fn a_running_binary_is_moved_aside_and_swept_later() {
        let dir = std::env::temp_dir().join(format!("puffin-update-aside-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap_or_default();
        std::fs::write(dir.join("puffin.exe"), b"old").unwrap_or_default();
        std::fs::write(dir.join(".puffin.exe.update"), b"new").unwrap_or_default();
        replace_aside(&dir, "puffin.exe", &dir.join(".puffin.exe.update")).unwrap_or_default();
        assert_eq!(std::fs::read(dir.join("puffin.exe")).unwrap_or_default(), b"new");
        assert_eq!(std::fs::read(dir.join("puffin.exe.old")).unwrap_or_default(), b"old");
        // A second update before the next start finds the first leftover removable.
        std::fs::write(dir.join(".puffin.exe.update"), b"newer").unwrap_or_default();
        replace_aside(&dir, "puffin.exe", &dir.join(".puffin.exe.update")).unwrap_or_default();
        assert_eq!(std::fs::read(dir.join("puffin.exe")).unwrap_or_default(), b"newer");
        assert_eq!(std::fs::read(dir.join("puffin.exe.old")).unwrap_or_default(), b"new");
        sweep_old_in(&dir);
        assert!(!dir.join("puffin.exe.old").exists());
        assert!(dir.join("puffin.exe").exists());
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
    fn checksum_lines_parse_in_both_sha256sum_modes() {
        let sums = parse_sums("ABC123  puffin-x.gz\ndef456 *codex-code-mode-host-x.gz\n\n");
        assert_eq!(sums.get("puffin-x.gz").map(String::as_str), Some("abc123"));
        assert_eq!(
            sums.get("codex-code-mode-host-x.gz").map(String::as_str),
            Some("def456")
        );
    }

    #[test]
    fn assets_are_named_after_the_target() {
        let [puffin, host, sums] = asset_names("aarch64-unknown-linux-gnu");
        assert_eq!(puffin, "puffin-aarch64-unknown-linux-gnu.gz");
        assert_eq!(host, "codex-code-mode-host-aarch64-unknown-linux-gnu.gz");
        assert_eq!(sums, "puffin-aarch64-unknown-linux-gnu.sha256sums");
        assert_eq!(hex_sha256(b"abc").len(), 64);
        assert_eq!(
            optional_asset_names("aarch64-unknown-linux-gnu"),
            [
                "puffin-search-aarch64-unknown-linux-gnu.gz".to_string(),
                "puffin-fetch-aarch64-unknown-linux-gnu.gz".to_string(),
                "puffin-code-aarch64-unknown-linux-gnu.gz".to_string()
            ]
        );
        // The launcher finds the router by this name beside `puffin` (code_index::binary).
        assert!(optional_commands().contains(&"puffin-code"));
    }
}
