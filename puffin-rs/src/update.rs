//! `puffin update`: replace this installation with the latest Puffin release's prebuilt binaries.
//!
//! Codex's own `update` picks an installer (npm, brew, OpenAI's install script) from how Codex was
//! installed, which would put upstream Codex in Puffin's place. Patch 0008 sends the subcommand
//! here instead. The release workflow attaches, per target, a gzipped `puffin`, a gzipped
//! `codex-code-mode-host` and a `sha256sums` file covering both; this downloads the latest
//! published release's three assets, verifies both archives against the checksum file, and swaps
//! the two binaries in next to the running executable.
//!
//! The repository is private, so the GitHub API needs a token: `GH_TOKEN`, `GITHUB_TOKEN`, or
//! whatever `gh auth token` prints.

use std::collections::HashMap;
use std::io::Read;
use std::path::Path;
use std::time::Duration;

use anyhow::Context;
use anyhow::bail;
use sha2::Digest;
use sha2::Sha256;

/// Where releases are published. `PUFFIN_RELEASE_REPO` overrides it, e.g. for a fork.
pub const RELEASE_REPO: &str = "dgxcoder/dgxcoder";

/// The Puffin release this binary was built as. The release workflow sets `PUFFIN_VERSION` when it
/// builds; a local `puffin-admin codex build` does not, and such a binary counts as a source build.
pub const PUFFIN_VERSION: Option<&str> = option_env!("PUFFIN_VERSION");

/// Code Mode's host process, which Codex looks for next to its own executable under this name.
const CODE_MODE_HOST: &str = "codex-code-mode-host";

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
    format!("{}-unknown-linux-gnu", std::env::consts::ARCH)
}

/// Asset names for a target: the two gzipped binaries and the checksum file.
pub fn asset_names(target: &str) -> [String; 3] {
    [
        format!("puffin-{target}.gz"),
        format!("{CODE_MODE_HOST}-{target}.gz"),
        format!("puffin-{target}.sha256sums"),
    ]
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
    if !cfg!(target_os = "linux") {
        bail!("`puffin update` only has Linux release builds to install");
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
            "no published release found in {repo}. The repository is private, so this needs a \
             token with access to it (GH_TOKEN, GITHUB_TOKEN or `gh auth login`); drafts and \
             pre-releases are not offered as updates"
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

    // Both are downloaded and verified before either is replaced, so a failure part-way leaves
    // the installation as it was.
    let mut binaries = Vec::new();
    for (asset, installed) in [(puffin_asset, exe_name), (host_asset, CODE_MODE_HOST.to_string())] {
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
    }
    println!("✅ Puffin {latest} installed in {}", install_dir.display());
    Ok(())
}

/// Writes next to the target and renames over it, so a running `puffin` keeps its own file and a
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
    }
}
