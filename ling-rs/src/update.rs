//! `ling update`: replace this installation with the latest Mightling release's prebuilt binaries.
//!
//! Codex's own `update` picks an installer (npm, brew, OpenAI's install script) from how Codex was
//! installed, which would put upstream Codex in Mightling's place. Patch 0008 sends the subcommand
//! here instead. The release workflow attaches, per target, a gzipped `ling`, a gzipped
//! `codex-code-mode-host`, the gzipped web commands `ling-search` and `ling-fetch`, the gzipped
//! code index router `ling-code`, and a `sha256sums` file covering all of them; this downloads
//! the latest published release's assets, verifies every archive against the checksum file, and
//! swaps the binaries in next to the running executable. The web commands and `ling-code` are
//! optional, because earlier releases do not carry them (v1.3.0 has the web commands and no
//! `ling-code`); the installed ones are then kept.
//!
//! The repository is public, so no token is needed. `GH_TOKEN` or `GITHUB_TOKEN` is sent when set,
//! which raises GitHub's rate limit; a logged-in `gh`'s token is used only for a fork named by
//! `MIGHTLING_RELEASE_REPO`. A token only ever goes to `api.github.com`, never to a download URL
//! the release names (security review 2026-10).

use std::collections::HashMap;
use std::io::Read;
use std::path::Path;
use std::time::Duration;

use anyhow::Context;
use anyhow::bail;
use sha2::Digest;
use sha2::Sha256;

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

/// The commands a release may carry beside `ling`, each installed if its asset is present and
/// kept as installed if not.
pub fn optional_commands() -> [&'static str; 3] {
    [WEB_COMMANDS[0], WEB_COMMANDS[1], CODE_COMMAND]
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

/// The Rust target triple the release assets are named after.
pub fn target() -> String {
    format!("{}-unknown-linux-gnu", std::env::consts::ARCH)
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

/// Runs `ling update`.
pub async fn run() -> anyhow::Result<()> {
    if !cfg!(target_os = "linux") {
        bail!("`ling update` only has Linux release builds to install");
    }
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

    match decide(MIGHTLING_VERSION, latest) {
        Decision::UpToDate => {
            println!("✅ Mightling {latest} is the latest release.");
            return Ok(());
        }
        Decision::NewerThanRelease => {
            println!(
                "✅ This Mightling ({}) is newer than the latest release ({latest}); nothing to do.",
                MIGHTLING_VERSION.unwrap_or_default()
            );
            return Ok(());
        }
        Decision::Install => match MIGHTLING_VERSION {
            Some(current) => println!("⬆️  Updating Mightling {current} → {latest}"),
            None => println!("⬆️  This ling was built from source; installing release {latest}"),
        },
    }

    let target = target();
    let [mightling_asset, host_asset, sums_asset] = asset_names(&target);
    let assets: HashMap<&str, &str> = release["assets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|asset| Some((asset["name"].as_str()?, asset["url"].as_str()?)))
        .collect();
    for name in [&mightling_asset, &host_asset, &sums_asset] {
        if !assets.contains_key(name.as_str()) {
            bail!("release {tag} has no {name}; it carries no ling build for {target}");
        }
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
    let mut wanted = vec![(mightling_asset, exe_name), (host_asset, CODE_MODE_HOST.to_string())];
    for (asset, name) in optional_asset_names(&target).into_iter().zip(optional_commands()) {
        if assets.contains_key(asset.as_str()) {
            wanted.push((asset, name.to_string()));
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
    println!("✅ Mightling {latest} installed in {}", install_dir.display());
    Ok(())
}

/// Links `~/.local/bin/<name>` to the installed command, as `ling-admin codex build` does. Only
/// a missing file or an existing link is replaced: a real file of that name belongs to something
/// else, and is reported instead.
fn link_onto_path(install_dir: &Path, name: &str) {
    let Some(home) = std::env::var_os("HOME") else {
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
fn replace(dir: &Path, name: &str, binary: &[u8]) -> anyhow::Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let staging = dir.join(format!(".{name}.update"));
    std::fs::write(&staging, binary)
        .with_context(|| format!("could not write to {}", dir.display()))?;
    std::fs::set_permissions(&staging, std::fs::Permissions::from_mode(0o755))?;
    std::fs::rename(&staging, dir.join(name))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

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
                "ling-code-aarch64-unknown-linux-gnu.gz".to_string()
            ]
        );
        // The launcher finds the router by this name beside `ling` (code_index::binary).
        assert!(optional_commands().contains(&"ling-code"));
    }
}
