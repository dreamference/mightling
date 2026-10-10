//! `ling desktop install|status`: the desktop app from a release, for a client that has no
//! `ling-admin` (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §13). On a node `ling-admin
//! desktop install` builds the app from the checkout; a client downloads the release's package for
//! this machine, checks it against the release's signed `SHA256SUMS` as `ling update` checks the
//! binaries (`release_signature`), and installs it: the `.deb` through `apt-get` on Linux (one
//! `sudo`), the `.dmg` opened for the user on macOS, where the app is dragged to Applications and
//! opened once past the quarantine check (unsigned preview, DESKTOP_ELECTRON §10).
//!
//! The package's name is what the release workflow publishes: `mightling_<version>_<arm64|amd64>.deb`
//! (`desktop/electron/linux/deb-maker.js`, `app.json`'s `packageName`) and
//! `Mightling-<version>-<arm64|x64>-preview.dmg` (`.github/workflows/mac-preview.yml`). A release
//! without one for this machine says so and installs nothing.

use std::collections::HashMap;
use std::path::PathBuf;

use anyhow::Context;
use anyhow::bail;

use crate::release_signature;
use crate::update;

const USAGE: &str = "Usage: ling desktop install | ling desktop status";
/// The command the `.deb` installs, and the Mac app's bundle.
const LINUX_COMMAND: &str = "ling-app";
const MAC_APP: &str = "/Applications/Mightling.app";

/// The release asset holding the desktop app for a machine, or None where none is published.
pub fn asset_name(version: &str, os: &str, arch: &str) -> Option<String> {
    match (os, arch) {
        ("linux", "aarch64") => Some(format!("mightling_{version}_arm64.deb")),
        ("linux", "x86_64") => Some(format!("mightling_{version}_amd64.deb")),
        ("macos", "aarch64") => Some(format!("Mightling-{version}-arm64-preview.dmg")),
        ("macos", "x86_64") => Some(format!("Mightling-{version}-x64-preview.dmg")),
        _ => None,
    }
}

/// Where a downloaded package is kept: `<install>/desktop/`.
pub fn package_dir() -> Option<PathBuf> {
    crate::docs_setup::install_dir().map(|install| install.join("desktop"))
}

/// Whether the app is installed here: `ling-app` on PATH (the `.deb`) or the Mac bundle.
pub fn installed() -> bool {
    if cfg!(target_os = "macos") {
        return std::path::Path::new(MAC_APP).is_dir();
    }
    crate::audit::on_path(LINUX_COMMAND)
}

/// Checks a package against the release's `SHA256SUMS`, itself checked against its signature:
/// the signer, when every check holds.
pub fn check_package(name: &str, bytes: &[u8], all_sums: &[u8], signature: &str, transition: Option<(&str, &str)>, trusted: &[[u8; 32]]) -> anyhow::Result<String> {
    let signer = release_signature::verify_release(all_sums, signature, transition, trusted)?;
    let listed = update::parse_sums(&String::from_utf8_lossy(all_sums));
    let expected = listed.get(name).with_context(|| format!("{} has no checksum for {name}", release_signature::SUMS_ASSET))?;
    if &update::hex_sha256(bytes) != expected {
        bail!("{name} does not match its checksum in {}; nothing was installed", release_signature::SUMS_ASSET);
    }
    Ok(signer)
}

async fn install() -> anyhow::Result<()> {
    let (os, arch) = (std::env::consts::OS, std::env::consts::ARCH);
    let dir = package_dir().context("cannot find the install directory from this executable")?;
    let repo = std::env::var("MIGHTLING_RELEASE_REPO").ok().filter(|repo| !repo.is_empty()).unwrap_or_else(|| update::RELEASE_REPO.to_string());
    let token = update::github_token(&repo);
    let client = reqwest::Client::builder()
        .user_agent(format!("ling/{}", update::MIGHTLING_VERSION.unwrap_or("source")))
        .connect_timeout(std::time::Duration::from_secs(15))
        .build()?;
    let authorized = |url: &str, request: reqwest::RequestBuilder| match &token {
        Some(token) if update::sends_token(url) => request.bearer_auth(token),
        _ => request,
    };
    let latest_url = format!("{}repos/{repo}/releases/latest", update::GITHUB_API);
    let response = authorized(&latest_url, client.get(&latest_url).header("Accept", "application/vnd.github+json"))
        .send()
        .await
        .context("could not reach GitHub")?;
    if response.status() == reqwest::StatusCode::NOT_FOUND {
        bail!("no published release found in {repo}");
    }
    let release: serde_json::Value = response.error_for_status()?.json().await?;
    let tag = release["tag_name"].as_str().unwrap_or_default().to_string();
    let version = tag.trim_start_matches('v');
    let Some(name) = asset_name(version, os, arch) else {
        bail!("releases carry no desktop app for {arch} on {os}");
    };
    let assets: HashMap<&str, &str> = release["assets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|asset| Some((asset["name"].as_str()?, asset["url"].as_str()?)))
        .collect();
    if !assets.contains_key(name.as_str()) {
        let carried: Vec<&str> = assets.keys().filter(|n| n.ends_with(".deb") || n.ends_with(".dmg")).copied().collect();
        bail!(
            "release {tag} has no {name}{}",
            if carried.is_empty() { "; it carries no desktop app".to_string() } else { format!("; it carries {}", carried.join(", ")) }
        );
    }
    let download = |asset: String| {
        let url = assets[asset.as_str()];
        let request = authorized(url, client.get(url).header("Accept", "application/octet-stream"));
        async move {
            println!("📥 {asset}");
            let response = request.send().await?.error_for_status()?;
            anyhow::Ok(response.bytes().await?.to_vec())
        }
    };
    for required in [release_signature::SUMS_ASSET, release_signature::SIGNATURE_ASSET] {
        if !assets.contains_key(required) {
            bail!("release {tag} is not signed (it has no {required}); nothing was installed");
        }
    }
    let all_sums = download(release_signature::SUMS_ASSET.to_string()).await?;
    let signature = String::from_utf8(download(release_signature::SIGNATURE_ASSET.to_string()).await?)?;
    let transition = if assets.contains_key(release_signature::TRANSITION_KEY_ASSET) && assets.contains_key(release_signature::TRANSITION_SIGNATURE_ASSET) {
        Some((
            String::from_utf8(download(release_signature::TRANSITION_KEY_ASSET.to_string()).await?)?,
            String::from_utf8(download(release_signature::TRANSITION_SIGNATURE_ASSET.to_string()).await?)?,
        ))
    } else {
        None
    };
    let package = download(name.clone()).await?;
    let trusted = release_signature::parse_keys(release_signature::TRUSTED_KEYS)?;
    let signer = check_package(&name, &package, &all_sums, &signature, transition.as_ref().map(|(k, s)| (k.as_str(), s.as_str())), &trusted)
        .with_context(|| format!("release {tag} failed its signature check; nothing was installed"))?;
    println!("🔏 {} is signed by {signer}", release_signature::SUMS_ASSET);
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(&name);
    std::fs::write(&path, &package)?;
    println!("✅ {name} verified and saved to {}", path.display());
    if os == "linux" {
        println!("📦 Installing the package (apt-get needs sudo once)...");
        let status = std::process::Command::new("sudo").args(["apt-get", "install", "-y"]).arg(&path).status().context("cannot run sudo apt-get")?;
        if !status.success() {
            bail!("apt-get did not install {}; install it by hand: sudo apt-get install -y {}", name, path.display());
        }
        println!("✅ Mightling's desktop app is installed: `ling app` opens it.");
    } else {
        let _ = std::process::Command::new("open").arg(&path).status();
        println!("💡 Drag Mightling to Applications. The preview is not notarized: the first time, open it with Control-click → Open, or allow it in System Settings → Privacy & Security (Open Anyway).");
    }
    Ok(())
}

/// Runs `ling desktop …` and returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    match args.first().map(String::as_str) {
        Some("install") => match install().await {
            Ok(()) => 0,
            Err(error) => {
                println!("❌ {error:#}");
                1
            }
        },
        Some("status") => {
            if installed() {
                println!("✅ Mightling's desktop app is installed; `ling app` opens it.");
                0
            } else {
                println!("ℹ️  Mightling's desktop app is not installed here: `ling desktop install` fetches it from the release.");
                1
            }
        }
        _ => {
            println!("{USAGE}");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_package_is_named_as_the_release_publishes_it() {
        assert_eq!(asset_name("1.6.0", "linux", "aarch64").as_deref(), Some("mightling_1.6.0_arm64.deb"));
        assert_eq!(asset_name("1.6.0", "linux", "x86_64").as_deref(), Some("mightling_1.6.0_amd64.deb"));
        assert_eq!(asset_name("1.6.0", "macos", "aarch64").as_deref(), Some("Mightling-1.6.0-arm64-preview.dmg"));
        assert_eq!(asset_name("1.6.0", "macos", "x86_64").as_deref(), Some("Mightling-1.6.0-x64-preview.dmg"));
        assert_eq!(asset_name("1.6.0", "windows", "x86_64"), None);
    }

    #[test]
    fn a_package_is_installed_only_when_the_signed_sums_name_its_hash() {
        let package = b"a deb";
        let right = format!("{}  mightling_1.6.0_arm64.deb\n", update::hex_sha256(package));
        // An unsigned or wrongly signed sums file never passes, whatever it lists.
        let trusted = release_signature::parse_keys(release_signature::TRUSTED_KEYS).unwrap();
        assert!(check_package("mightling_1.6.0_arm64.deb", package, right.as_bytes(), "not a signature", None, &trusted).is_err());
    }
}
