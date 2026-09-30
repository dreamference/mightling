//! What to index and how: detection by project files (spec §6.1), the pinned tools (§5), and one
//! [`Run`] per indexer and root, with the sandbox each gets (§9.1).
//!
//! Tools are resolved from Puffin's own install directories only, never from `PATH`: a fallback to
//! whatever is on `PATH` would silently change the version whose output the schema fingerprints
//! pin (the same rule `CodexInstaller` keeps for `puffin`).

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::host::Kind;
use super::sandbox::Spec;
use crate::config::Settings;
use crate::manifest::Manifest;
use crate::paths::{self, Repo};

/// One indexer run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Run {
    pub kind: Kind,
    /// `codebase-memory`, `scip-python`, `rust-analyzer`.
    pub indexer: String,
    pub version: String,
    /// The indexed root, repository-relative (empty for the whole repository).
    pub root: String,
    pub path_prefix: String,
    /// The sandbox, with the command it runs.
    pub spec: Spec,
    /// The `.scip` and converted store the command writes, for exact-layer runs.
    pub scip_output: Option<PathBuf>,
    pub converted: Option<PathBuf>,
    pub need: u64,
    pub ceiling: u64,
    /// The systemd unit name.
    pub unit: String,
}

/// A batch of runs for one repository, executed in order by one supervisor.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Plan {
    pub repo_root: PathBuf,
    pub main_root: PathBuf,
    pub state_dir: PathBuf,
    pub vllm_host: String,
    /// How long to wait for the model to go idle before a run is recorded `deferred: busy`.
    pub wait_idle_s: u64,
    pub runs: Vec<Run>,
}

/// The pinned tools.
#[derive(Debug, Clone)]
pub struct Tools {
    pub codebase_memory: Option<PathBuf>,
    pub scip: Option<PathBuf>,
    /// `(node, scip-python's index.js)`.
    pub scip_python: Option<(PathBuf, PathBuf)>,
    /// `(rust-analyzer, RUSTUP_HOME, toolchain bin dir)`.
    pub rust_analyzer: Option<(PathBuf, PathBuf, PathBuf)>,
}

impl Tools {
    pub fn find() -> Tools {
        let bin = paths::install_bin_dir();
        let existing = |p: PathBuf| p.is_file().then_some(p);
        let indexers = paths::indexers_dir();
        let scip_python = existing(indexers.join("node_modules/@sourcegraph/scip-python/index.js"))
            .and_then(|js| existing(indexers.join("node")).map(|node| (node, js)));
        let rustup_home = std::env::var_os("RUSTUP_HOME").map(PathBuf::from).unwrap_or_else(|| paths::home().join(".rustup"));
        let toolchain = std::fs::read_dir(rustup_home.join("toolchains"))
            .ok()
            .and_then(|entries| entries.flatten().map(|e| e.path()).find(|p| p.file_name().map(|n| n.to_string_lossy().starts_with("1.95.0-")).unwrap_or(false)));
        let rust_analyzer = toolchain.and_then(|t| existing(t.join("bin/rust-analyzer")).map(|ra| (ra, rustup_home.clone(), t.join("bin"))));
        Tools { codebase_memory: existing(bin.join("codebase-memory-mcp")), scip: existing(bin.join("scip")), scip_python, rust_analyzer }
    }
}

/// An indexer that applies to the repository.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Target {
    pub kind: Kind,
    pub indexer: &'static str,
    pub root: String,
}

/// Detects the exact-layer indexers by project files at the root and in immediate
/// subdirectories (§6.1). Submodules are left out, and so are crates that inherit their manifest
/// from a workspace elsewhere (they index only from that workspace).
pub fn detect(repo: &Repo) -> Vec<Target> {
    let submodules = repo.submodules();
    let mut out = Vec::new();
    let mut dirs: Vec<String> = vec![String::new()];
    if let Ok(entries) = std::fs::read_dir(&repo.root) {
        let mut names: Vec<String> = entries
            .flatten()
            .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| !n.starts_with('.') && !matches!(n.as_str(), "target" | "node_modules" | "venv" | "build" | "dist"))
            .filter(|n| !submodules.contains(n))
            .collect();
        names.sort();
        dirs.extend(names);
    }
    for dir in &dirs {
        let path = if dir.is_empty() { repo.root.clone() } else { repo.root.join(dir) };
        if !dir.is_empty() && path.join("__init__.py").is_file() {
            out.push(Target { kind: Kind::Static, indexer: "scip-python", root: dir.clone() });
        }
        if let Ok(manifest) = std::fs::read_to_string(path.join("Cargo.toml")) {
            let member_elsewhere = manifest.contains(".workspace = true") && !manifest.contains("[workspace]");
            if !member_elsewhere && (manifest.contains("[package]") || manifest.contains("[workspace]")) {
                out.push(Target { kind: Kind::Executing, indexer: "rust-analyzer", root: dir.clone() });
            }
        }
    }
    out
}

/// A short, stable id for a repository, for unit and directory names.
pub fn repo_id(repo: &Repo) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(repo.main_root.to_string_lossy().as_bytes());
    crate::manifest::hex(&digest[..5])
}

fn scratch_for(repo: &Repo, slug: &str) -> PathBuf {
    let base = std::env::var_os("PUFFIN_CODE_SCRATCH_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| paths::home().join(".cache/dreamference/puffin-code/runs"));
    base.join(format!("{}-{slug}", repo_id(repo)))
}

fn base_env(home: &Path, path: &str) -> Vec<(String, String)> {
    vec![
        ("HOME".into(), home.to_string_lossy().into_owned()),
        ("PATH".into(), path.to_string()),
        ("LANG".into(), "C.UTF-8".into()),
    ]
}

/// The universal run: codebase-memory over the repository.
pub fn universal_run(repo: &Repo, settings: &Settings, tools: &Tools, recorded_peak_mb: u64) -> Option<Run> {
    let cbm = tools.codebase_memory.clone()?;
    let home = paths::home();
    let scratch = scratch_for(repo, "codebase-memory");
    let cache = crate::graph::cache_dir();
    let ceiling = settings.small_ceiling_mb << 20;
    let mut env = base_env(&home, "/usr/bin:/bin");
    env.extend([
        ("CBM_CACHE_DIR".into(), cache.to_string_lossy().into_owned()),
        // Short on purpose: codebase-memory's socket path must fit in 108 bytes, and /tmp is the
        // sandbox's own tmpfs.
        ("CBM_RUNTIME_DIR".into(), "/tmp/cbm".into()),
        ("CBM_WORKERS".into(), "4".into()),
        ("CBM_SEMANTIC_ENABLED".into(), if settings.semantic { "1" } else { "0" }.into()),
        ("CBM_MEM_BUDGET_MB".into(), ((ceiling >> 20) * 3 / 4).to_string()),
    ]);
    let request = serde_json::json!({ "repo_path": repo.main_root.to_string_lossy() }).to_string();
    Some(Run {
        kind: Kind::Universal,
        indexer: "codebase-memory".into(),
        version: "0.11.0".into(),
        root: String::new(),
        path_prefix: String::new(),
        spec: Spec {
            home: home.clone(),
            scratch: scratch.clone(),
            read_only: vec![repo.main_root.clone(), cbm.parent().unwrap().to_path_buf()],
            writable: vec![],
            writable_outside_scratch: vec![(cache, "codebase-memory's own graph store".into())],
            env,
            cwd: repo.main_root.clone(),
            argv: vec![
                "/bin/sh".into(),
                "-c".into(),
                "mkdir -p -m 700 /tmp/cbm && exec \"$0\" cli --quiet index_repository \"$1\"".into(),
                cbm.to_string_lossy().into_owned(),
                request,
            ],
        },
        scip_output: None,
        converted: None,
        need: super::host::need(Kind::Universal, recorded_peak_mb),
        ceiling,
        unit: format!("puffin-index-{}-codebase-memory", repo_id(repo)),
    })
}

/// An exact-layer run for a detected target.
pub fn exact_run(repo: &Repo, settings: &Settings, tools: &Tools, target: &Target, recorded_peak_mb: u64) -> Option<Run> {
    let scip = tools.scip.clone()?;
    let home = paths::home();
    let slug = super::store::slug(target.indexer, &target.root);
    let scratch = scratch_for(repo, &slug);
    let out = scratch.join("out");
    let scip_file = out.join("index.scip");
    let converted = out.join("index.db");
    let root_dir = if target.root.is_empty() { repo.root.clone() } else { repo.root.join(&target.root) };
    let convert = format!(
        "&& {} expt-convert {} --output {}",
        sh_quote(&scip.to_string_lossy()),
        sh_quote(&scip_file.to_string_lossy()),
        sh_quote(&converted.to_string_lossy())
    );
    let (argv_cmd, env, read_only, version, ceiling) = match target.indexer {
        "scip-python" => {
            let (node, js) = tools.scip_python.clone()?;
            let node_real = std::fs::canonicalize(&node).unwrap_or(node.clone());
            let node_dir = node_real.parent().unwrap().to_path_buf();
            // An empty environment file: no package of the repository's venv is read, and nothing
            // from it runs (§6.1).
            let environment = scratch.join("environment.json");
            let command = format!(
                "echo '[]' > {env} && {node} {js} index --quiet --project-name {name} --target-only {root} --environment {env} --output {out} {convert}",
                env = sh_quote(&environment.to_string_lossy()),
                node = sh_quote(&node_real.to_string_lossy()),
                js = sh_quote(&js.to_string_lossy()),
                name = sh_quote(if target.root.is_empty() { "root" } else { &target.root }),
                root = sh_quote(if target.root.is_empty() { "." } else { &target.root }),
                out = sh_quote(&scip_file.to_string_lossy()),
            );
            let mut env = base_env(&home, &format!("{}:/usr/bin:/bin", node_dir.to_string_lossy()));
            env.extend([
                ("PYTHONSAFEPATH".into(), "1".into()),
                ("PYTHONNOUSERSITE".into(), "1".into()),
                ("npm_config_offline".into(), "true".into()),
            ]);
            let read_only = vec![repo.root.clone(), paths::indexers_dir(), node_real.parent().unwrap().parent().unwrap().to_path_buf(), scip.parent().unwrap().to_path_buf()];
            (command, env, read_only, "0.6.6", settings.small_ceiling_mb << 20)
        }
        "rust-analyzer" => {
            let (ra, rustup_home, toolchain_bin) = tools.rust_analyzer.clone()?;
            let cargo_home = std::env::var_os("CARGO_HOME").map(PathBuf::from).unwrap_or_else(|| home.join(".cargo"));
            let command = format!(
                "{ra} scip {src} --output {out} {convert}",
                ra = sh_quote(&ra.to_string_lossy()),
                src = sh_quote(&root_dir.to_string_lossy()),
                out = sh_quote(&scip_file.to_string_lossy()),
            );
            let mut env = base_env(&home, &format!("{}:/usr/bin:/bin", toolchain_bin.to_string_lossy()));
            env.extend([
                ("RUSTUP_HOME".into(), rustup_home.to_string_lossy().into_owned()),
                ("RUSTUP_TOOLCHAIN".into(), "1.95.0".into()),
                ("CARGO_HOME".into(), cargo_home.to_string_lossy().into_owned()),
                ("CARGO_NET_OFFLINE".into(), "true".into()),
                ("CARGO_TARGET_DIR".into(), scratch.join("target").to_string_lossy().into_owned()),
                ("CARGO_BUILD_JOBS".into(), "4".into()),
            ]);
            let read_only = vec![rustup_home, cargo_home, root_dir.clone(), scip.parent().unwrap().to_path_buf()];
            (command, env, read_only, "1.95.0", settings.memory_ceiling_mb << 20)
        }
        _ => return None,
    };
    let script = format!("rm -rf {out} && mkdir -p {out} && {argv_cmd}", out = sh_quote(&out.to_string_lossy()));
    Some(Run {
        kind: target.kind,
        indexer: target.indexer.to_string(),
        version: version.to_string(),
        root: target.root.clone(),
        path_prefix: if target.root.is_empty() { String::new() } else { format!("{}/", target.root) },
        spec: Spec {
            home,
            scratch: scratch.clone(),
            read_only,
            writable: vec![],
            writable_outside_scratch: vec![],
            env,
            cwd: repo.root.clone(),
            argv: vec!["/bin/sh".into(), "-c".into(), script],
        },
        scip_output: Some(scip_file),
        converted: Some(converted),
        need: super::host::need(target.kind, recorded_peak_mb),
        ceiling,
        unit: format!("puffin-index-{}-{slug}", repo_id(repo)),
    })
}

/// Quotes a string for `sh`.
pub fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// The plan for a repository: the universal run, the static runs, and, when `exact` is set and
/// the repository is trusted, the executing runs.
pub fn build(repo: &Repo, settings: &Settings, exact: bool) -> (Plan, Vec<String>) {
    let tools = Tools::find();
    let manifest = Manifest::load(&repo.scip_dir());
    let graph = crate::manifest::GraphSnapshot::load(&repo.scip_dir());
    let mut runs = Vec::new();
    let mut skipped = Vec::new();
    match universal_run(repo, settings, &tools, graph.map(|g| g.peak_rss_mb).unwrap_or(0)) {
        Some(run) => runs.push(run),
        None => skipped.push("codebase-memory-mcp is not installed (`puffin-admin code setup`)".to_string()),
    }
    let trusted = crate::config::is_trusted(&repo.main_root);
    for target in detect(repo) {
        if target.kind == Kind::Executing && !(exact && trusted) {
            if !trusted {
                skipped.push(format!("{} for {}: the repository is not trusted", target.indexer, display_root(&target.root)));
            }
            continue;
        }
        let peak = manifest.runs.get(&super::store::key(target.indexer, &target.root)).map(|e| e.peak_rss_mb).unwrap_or(0);
        match exact_run(repo, settings, &tools, &target, peak) {
            Some(run) => runs.push(run),
            None => skipped.push(format!("{} for {}: not installed (`puffin-admin code setup`)", target.indexer, display_root(&target.root))),
        }
    }
    let plan = Plan {
        repo_root: repo.root.clone(),
        main_root: repo.main_root.clone(),
        state_dir: repo.state_dir(),
        vllm_host: settings.vllm_host.clone(),
        wait_idle_s: 600,
        runs,
    };
    (plan, skipped)
}

fn display_root(root: &str) -> &str {
    if root.is_empty() {
        "the repository"
    } else {
        root
    }
}
