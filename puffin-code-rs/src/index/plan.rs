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
    /// `(node, scip-typescript's main.js)`.
    pub scip_typescript: Option<(PathBuf, PathBuf)>,
    /// scip-clang. Nothing installs it here: upstream ships no linux-arm64 build (§13).
    pub scip_clang: Option<PathBuf>,
    /// `(scip-go, GOROOT)`: the pinned binary and the Go toolchain recorded at setup.
    pub scip_go: Option<(PathBuf, PathBuf)>,
    /// `(scip-java launcher, JAVA_HOME)`: a JDK 17 or newer recorded at setup.
    pub scip_java: Option<(PathBuf, PathBuf)>,
    /// `(DOTNET_ROOT, scip-dotnet.dll)`: the .NET SDK recorded at setup and the tool it runs.
    pub scip_dotnet: Option<(PathBuf, PathBuf)>,
}

/// The pinned versions of the indexers, by name (the manifest records them per run).
pub fn indexer_version(indexer: &str) -> &'static str {
    match indexer {
        "scip-python" => "0.6.6",
        "rust-analyzer" => "1.95.0",
        "scip-typescript" => "0.4.0",
        "scip-clang" => "0.4.0",
        "scip-go" => "0.2.7",
        "scip-java" => "0.13.1",
        "scip-dotnet" => "0.2.14",
        _ => "unknown",
    }
}

impl Tools {
    pub fn find() -> Tools {
        let bin = paths::install_bin_dir();
        let existing = |p: PathBuf| p.is_file().then_some(p);
        let indexers = paths::indexers_dir();
        let node = existing(indexers.join("node"));
        let scip_python = existing(indexers.join("node_modules/@sourcegraph/scip-python/index.js")).and_then(|js| node.clone().map(|n| (n, js)));
        let scip_typescript =
            existing(indexers.join("node_modules/@sourcegraph/scip-typescript/dist/src/main.js")).and_then(|js| node.clone().map(|n| (n, js)));
        let rustup_home = std::env::var_os("RUSTUP_HOME").map(PathBuf::from).unwrap_or_else(|| paths::home().join(".rustup"));
        let toolchain = std::fs::read_dir(rustup_home.join("toolchains"))
            .ok()
            .and_then(|entries| entries.flatten().map(|e| e.path()).find(|p| p.file_name().map(|n| n.to_string_lossy().starts_with("1.95.0-")).unwrap_or(false)));
        let rust_analyzer = toolchain.and_then(|t| existing(t.join("bin/rust-analyzer")).map(|ra| (ra, rustup_home.clone(), t.join("bin"))));
        // The toolchains are links `puffin-admin code setup` made to what it found, resolved here
        // so the sandbox binds the real directories.
        let toolchain_dir = |name: &str, probe: &str| {
            std::fs::canonicalize(indexers.join(name)).ok().filter(|dir| dir.join(probe).is_file())
        };
        let scip_go = existing(bin.join("scip-go")).zip(toolchain_dir("go", "bin/go"));
        let scip_java = existing(bin.join("scip-java")).zip(toolchain_dir("java", "bin/javac"));
        let scip_dotnet = toolchain_dir("dotnet", "dotnet").zip(existing(indexers.join("scip-dotnet/scip-dotnet.dll")));
        Tools {
            codebase_memory: existing(bin.join("codebase-memory-mcp")),
            scip: existing(bin.join("scip")),
            scip_python,
            rust_analyzer,
            scip_typescript,
            scip_clang: existing(bin.join("scip-clang")),
            scip_go,
            scip_java,
            scip_dotnet,
        }
    }

    /// Why a target's indexer cannot run here, or None when it can. One function for both the
    /// plan's skipped list and `puffin-code status`, so the two never disagree.
    pub fn unavailable(&self, indexer: &str) -> Option<String> {
        let setup = "`puffin-admin code setup`";
        if self.scip.is_none() {
            return Some(format!("the scip CLI is not installed ({setup})"));
        }
        let (missing, needs) = match indexer {
            "scip-python" => (self.scip_python.is_none(), "Node.js"),
            "rust-analyzer" => (self.rust_analyzer.is_none(), "the Rust 1.95.0 toolchain"),
            "scip-typescript" => (self.scip_typescript.is_none(), "Node.js"),
            "scip-clang" => {
                return self.scip_clang.is_none().then(|| {
                    "scip-clang has no linux-arm64 release upstream (v0.4.0 ships x86_64-linux and arm64-darwin only); C and C++ stay on the universal layer".to_string()
                })
            }
            "scip-go" => (self.scip_go.is_none(), "a Go toolchain"),
            "scip-java" => (self.scip_java.is_none(), "a JDK 17 or newer"),
            "scip-dotnet" => (self.scip_dotnet.is_none(), "a .NET SDK 8 or newer"),
            _ => return Some(format!("{indexer} is not an indexer puffin-code knows")),
        };
        missing.then(|| format!("{indexer} is not installed: it needs {needs}, then {setup}"))
    }
}

/// An indexer that applies to the repository.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Target {
    pub kind: Kind,
    pub indexer: &'static str,
    pub root: String,
    /// Runs only on an explicit `puffin-code index` (scip-clang, scip-go: §6.1), not at launch.
    pub on_demand: bool,
    /// A project file the indexer is pointed at, relative to the root: the compilation database
    /// for scip-clang, the solution or project for scip-dotnet. Empty otherwise.
    pub file: String,
}

impl Target {
    pub fn new(kind: Kind, indexer: &'static str, root: &str) -> Target {
        Target { kind, indexer, root: root.to_string(), on_demand: false, file: String::new() }
    }
}

/// The build files scip-java recognises (Gradle and Maven; `scip-java index` does not drive sbt).
const JAVA_BUILD_FILES: &[&str] = &["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "pom.xml"];

/// Source extensions scip-typescript indexes.
const TS_EXTENSIONS: &[&str] = &[".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"];

fn first_with_extension(dir: &Path, extensions: &[&str]) -> Option<String> {
    let mut names: Vec<String> = std::fs::read_dir(dir)
        .ok()?
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_file()).unwrap_or(false))
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| extensions.iter().any(|ext| n.ends_with(ext)))
        .collect();
    names.sort();
    names.into_iter().next()
}

/// How many Python files at a root get an exact index of their own. Each is a scip-python run
/// (`--target-only <file>`), so a root full of loose scripts must not become a hundred runs.
pub const ROOT_PYTHON_FILES: usize = 12;

/// The Python files directly at the repository's root, which no directory root covers: the ones
/// indexed, largest first (they hold the most definitions), and how many more there are past
/// [`ROOT_PYTHON_FILES`]. Tracked files in a git repository, as for the directory roots.
pub fn root_python_files(repo: &Repo) -> (Vec<String>, usize) {
    let tracked = if repo.is_git { crate::paths::git_z(&repo.root, &["ls-files", "-z"]).unwrap_or_default() } else { Vec::new() };
    root_python_files_of(repo, &tracked)
}

fn root_python_files_of(repo: &Repo, tracked: &[String]) -> (Vec<String>, usize) {
    let is_python = |name: &str| name.ends_with(".py") || name.ends_with(".pyi");
    let mut files: Vec<(u64, String)> = if repo.is_git {
        tracked.iter().filter(|f| !f.contains('/') && is_python(f)).cloned().map(|f| (0, f)).collect()
    } else {
        std::fs::read_dir(&repo.root)
            .map(|entries| entries.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| is_python(n) && !n.starts_with('.')).map(|n| (0, n)).collect())
            .unwrap_or_default()
    };
    // A file git lists but the checkout lacks (deleted, not yet committed) is not a root.
    files.retain_mut(|(size, name)| match std::fs::metadata(repo.root.join(name.as_str())) {
        Ok(meta) if meta.is_file() => {
            *size = meta.len();
            true
        }
        _ => false,
    });
    files.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
    let more = files.len().saturating_sub(ROOT_PYTHON_FILES);
    files.truncate(ROOT_PYTHON_FILES);
    (files.into_iter().map(|(_, name)| name).collect(), more)
}

/// The line that says some root-level Python files have no exact index, or None when all do.
pub fn root_python_note(more: usize, root: &str) -> Option<String> {
    (more > 0).then(|| {
        format!(
            "scip-python for {more} more Python file{} at the root of {}: only the {ROOT_PYTHON_FILES} largest get an index of their own",
            if more == 1 { "" } else { "s" },
            display_root(root)
        )
    })
}

/// Detects the exact-layer indexers by project files at the root and in immediate
/// subdirectories (§6.1). A Python file at the root itself is a root of its own (it is in no
/// directory, and indexing `.` would index every directory a second time). Submodules are left out, and so are crates that inherit their manifest
/// from a workspace elsewhere (they index only from that workspace). Where the root is itself a
/// TypeScript, Java or .NET project, its subdirectories are part of it and are not roots of their
/// own; Go modules are separate whatever their nesting, so every `go.mod` is a root.
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
    // A top-level directory holding Python files git knows about, package or not (tests/,
    // scripts/): scip-python indexes each as a root of its own.
    // Tracked files only: an untracked scratch directory is the user's, not the project's.
    let tracked = if repo.is_git { crate::paths::git_z(&repo.root, &["ls-files", "-z"]).unwrap_or_default() } else { Vec::new() };
    let python_dirs: std::collections::BTreeSet<String> = tracked
        .iter()
        .filter(|f| f.ends_with(".py") || f.ends_with(".pyi"))
        .filter_map(|f| f.split_once('/').map(|(top, _)| top.to_string()))
        .collect();
    // Top-level directories holding tracked TypeScript or JavaScript outside node_modules: a
    // directory with only a package.json is a JavaScript project when it has sources of its own.
    let ts_dirs: std::collections::BTreeSet<String> = tracked
        .iter()
        .filter(|f| TS_EXTENSIONS.iter().any(|ext| f.ends_with(ext)) && !f.ends_with(".d.ts") && !f.contains("node_modules/"))
        .map(|f| f.split_once('/').map(|(top, _)| top.to_string()).unwrap_or_default())
        .collect();
    let has_ts_sources = |dir: &str| if dir.is_empty() { !ts_dirs.is_empty() } else { ts_dirs.contains(dir) };
    let ts_config = |path: &Path| path.join("tsconfig.json").is_file() || path.join("jsconfig.json").is_file();
    let java_build = |path: &Path| JAVA_BUILD_FILES.iter().any(|f| path.join(f).is_file());
    let dotnet_file = |path: &Path| {
        first_with_extension(path, &[".sln", ".slnx"]).or_else(|| first_with_extension(path, &[".csproj", ".vbproj"]))
    };
    let root = &repo.root;
    let (ts_root, java_root, dotnet_root) = (ts_config(root), java_build(root), dotnet_file(root).is_some());
    for dir in &dirs {
        let path = if dir.is_empty() { repo.root.clone() } else { repo.root.join(dir) };
        let sub = !dir.is_empty();
        let is_python = if repo.is_git { python_dirs.contains(dir) } else { path.join("__init__.py").is_file() };
        if sub && is_python {
            out.push(Target::new(Kind::Static, "scip-python", dir));
        }
        if !sub {
            for file in root_python_files_of(repo, &tracked).0 {
                out.push(Target::new(Kind::Static, "scip-python", &file));
            }
        }
        if let Ok(manifest) = std::fs::read_to_string(path.join("Cargo.toml")) {
            let member_elsewhere = manifest.contains(".workspace = true") && !manifest.contains("[workspace]");
            if !member_elsewhere && (manifest.contains("[package]") || manifest.contains("[workspace]")) {
                out.push(Target::new(Kind::Executing, "rust-analyzer", dir));
            }
        }
        // TypeScript and JavaScript: a tsconfig or jsconfig, or a package.json over sources of its
        // own (indexed with a configuration inferred in scratch, never written into the tree).
        if !(sub && ts_root) && (ts_config(&path) || (path.join("package.json").is_file() && has_ts_sources(dir))) {
            out.push(Target::new(Kind::Static, "scip-typescript", dir));
        }
        // C and C++: only with a compilation database, at the root of the directory or in its
        // build/ (where CMake writes it). On demand: §6.1.
        for compdb in ["compile_commands.json", "build/compile_commands.json"] {
            if path.join(compdb).is_file() {
                out.push(Target { file: compdb.to_string(), on_demand: true, ..Target::new(Kind::Static, "scip-clang", dir) });
                break;
            }
        }
        if path.join("go.mod").is_file() {
            out.push(Target { on_demand: true, ..Target::new(Kind::Static, "scip-go", dir) });
        }
        if !(sub && java_root) && java_build(&path) {
            out.push(Target::new(Kind::Executing, "scip-java", dir));
        }
        if !(sub && dotnet_root) {
            if let Some(file) = dotnet_file(&path) {
                out.push(Target { file, ..Target::new(Kind::Executing, "scip-dotnet", dir) });
            }
        }
    }
    out
}

/// The targets inside the submodules §4.3 includes, with each root carrying the submodule's path
/// as its prefix, and what was left out and why.
///
/// A submodule is treated as the superproject is: its root and its immediate subdirectories.
/// Only the static indexers run there. The executing ones (rust-analyzer, scip-java,
/// scip-dotnet) build the project, and a build tool must never write into a submodule's
/// checkout; they need a scratch copy of it, which is not built yet.
pub fn detect_in_submodules(repo: &Repo, decisions: &[crate::submodules::Submodule]) -> (Vec<Target>, Vec<String>) {
    let mut targets = Vec::new();
    let mut skipped = Vec::new();
    for submodule in decisions.iter().filter(|s| s.indexed) {
        let dir = repo.root.join(&submodule.path);
        let inside = Repo { root: dir.clone(), main_root: dir, is_git: true };
        skipped.extend(root_python_note(root_python_files(&inside).1, &submodule.path));
        for target in detect(&inside) {
            let root = if target.root.is_empty() { submodule.path.clone() } else { format!("{}/{}", submodule.path, target.root) };
            if target.kind == Kind::Executing {
                skipped.push(format!("{} for {root}: not run in a submodule yet (it needs a scratch copy of the checkout)", target.indexer));
            } else {
                targets.push(Target { root, ..target });
            }
        }
    }
    (targets, skipped)
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
    let raw_file = out.join("raw.scip");
    let scip_file = out.join("index.scip");
    let converted = out.join("index.db");
    let this = self_exe();
    let root_dir = if target.root.is_empty() { repo.root.clone() } else { repo.root.join(&target.root) };
    let convert = format!(
        "&& {} scip-repair {} {} && {} expt-convert {} --output {}",
        sh_quote(&this.to_string_lossy()),
        sh_quote(&raw_file.to_string_lossy()),
        sh_quote(&scip_file.to_string_lossy()),
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
                // A root inside a submodule has a `/` in it, which a project name must not.
                name = sh_quote(&if target.root.is_empty() { "root".to_string() } else { target.root.replace('/', "-") }),
                root = sh_quote(if target.root.is_empty() { "." } else { &target.root }),
                out = sh_quote(&raw_file.to_string_lossy()),
            );
            let mut env = base_env(&home, &format!("{}:/usr/bin:/bin", node_dir.to_string_lossy()));
            env.extend([
                ("PYTHONSAFEPATH".into(), "1".into()),
                ("PYTHONNOUSERSITE".into(), "1".into()),
                ("npm_config_offline".into(), "true".into()),
            ]);
            let read_only = vec![
                repo.root.clone(),
                paths::indexers_dir(),
                node_real.parent().unwrap().parent().unwrap().to_path_buf(),
                scip.parent().unwrap().to_path_buf(),
                this.parent().unwrap().to_path_buf(),
            ];
            (command, env, read_only, "0.6.6", settings.small_ceiling_mb << 20)
        }
        "rust-analyzer" => {
            let (ra, rustup_home, toolchain_bin) = tools.rust_analyzer.clone()?;
            let cargo_home = std::env::var_os("CARGO_HOME").map(PathBuf::from).unwrap_or_else(|| home.join(".cargo"));
            let command = format!(
                "{ra} scip {src} --output {out} {convert}",
                ra = sh_quote(&ra.to_string_lossy()),
                src = sh_quote(&root_dir.to_string_lossy()),
                out = sh_quote(&raw_file.to_string_lossy()),
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
            let read_only = vec![rustup_home, cargo_home, root_dir.clone(), scip.parent().unwrap().to_path_buf(), this.parent().unwrap().to_path_buf()];
            (command, env, read_only, "1.95.0", settings.memory_ceiling_mb << 20)
        }
        "scip-typescript" => {
            let (node, js) = tools.scip_typescript.clone()?;
            let node_real = std::fs::canonicalize(&node).unwrap_or(node.clone());
            let node_dir = node_real.parent().unwrap().to_path_buf();
            let ceiling = settings.small_ceiling_mb << 20;
            // The project: the root's own tsconfig or jsconfig, or a configuration inferred into
            // scratch (scip-typescript's `--infer-tsconfig` would write it into the tree, which is
            // read-only here).
            let (project, write_config) = if root_dir.join("tsconfig.json").is_file() {
                (root_dir.clone(), String::new())
            } else if root_dir.join("jsconfig.json").is_file() {
                (root_dir.join("jsconfig.json"), String::new())
            } else {
                let config = scratch.join("tsconfig.json");
                let inferred = serde_json::json!({
                    "compilerOptions": { "allowJs": true, "checkJs": false, "noEmit": true },
                    "include": [format!("{}/**/*", root_dir.display())],
                    "exclude": [format!("{}/**/node_modules", root_dir.display())],
                });
                let write = format!("printf '%s' {} > {} && ", sh_quote(&inferred.to_string()), sh_quote(&config.to_string_lossy()));
                (config, write)
            };
            let command = format!(
                "{write_config}{node} --max-old-space-size={heap} {js} index {project} --cwd {root} --no-progress-bar --output {out} {convert}",
                node = sh_quote(&node_real.to_string_lossy()),
                // Node's heap below the cgroup's cap: a too-large project fails in Node, with a
                // message, rather than being killed by the kernel.
                heap = (ceiling >> 20) * 3 / 4,
                js = sh_quote(&js.to_string_lossy()),
                project = sh_quote(&project.to_string_lossy()),
                root = sh_quote(&root_dir.to_string_lossy()),
                out = sh_quote(&raw_file.to_string_lossy()),
            );
            let mut env = base_env(&home, &format!("{}:/usr/bin:/bin", node_dir.to_string_lossy()));
            env.push(("npm_config_offline".into(), "true".into()));
            // The whole repository is readable: a tsconfig may extend one above its root.
            let read_only = vec![
                repo.root.clone(),
                paths::indexers_dir(),
                node_dir.parent().unwrap().to_path_buf(),
                scip.parent().unwrap().to_path_buf(),
                this.parent().unwrap().to_path_buf(),
            ];
            (command, env, read_only, indexer_version("scip-typescript"), ceiling)
        }
        "scip-clang" => {
            let clang = tools.scip_clang.clone()?;
            let command = format!(
                "cd {root} && {clang} --compdb-path={compdb} --index-output-path={out} --jobs=4 {convert}",
                root = sh_quote(&root_dir.to_string_lossy()),
                clang = sh_quote(&clang.to_string_lossy()),
                compdb = sh_quote(&root_dir.join(&target.file).to_string_lossy()),
                out = sh_quote(&raw_file.to_string_lossy()),
            );
            let env = base_env(&home, "/usr/bin:/bin");
            let read_only = vec![repo.root.clone(), clang.parent().unwrap().to_path_buf(), scip.parent().unwrap().to_path_buf(), this.parent().unwrap().to_path_buf()];
            (command, env, read_only, indexer_version("scip-clang"), settings.small_ceiling_mb << 20)
        }
        "scip-go" => {
            let (scip_go, goroot) = tools.scip_go.clone()?;
            let mod_cache = go_mod_cache(&home);
            let command = format!(
                "mkdir -p {empty} && cd {root} && {scip_go} index --quiet --module-root . --repository-remote local --module-version {version} --output {out} {convert}",
                empty = sh_quote(&scratch.join("gomodcache").to_string_lossy()),
                root = sh_quote(&root_dir.to_string_lossy()),
                scip_go = sh_quote(&scip_go.to_string_lossy()),
                version = sh_quote(&repo.head().map(|h| h[..h.len().min(12)].to_string()).unwrap_or_else(|| "local".into())),
                out = sh_quote(&raw_file.to_string_lossy()),
            );
            let mut env = base_env(&home, &format!("{}:/usr/bin:/bin", goroot.join("bin").to_string_lossy()));
            env.extend([
                ("GOROOT".into(), goroot.to_string_lossy().into_owned()),
                ("GOPATH".into(), scratch.join("gopath").to_string_lossy().into_owned()),
                ("GOCACHE".into(), scratch.join("gocache").to_string_lossy().into_owned()),
                // The user's module cache, read-only; an empty one in scratch when there is none.
                ("GOMODCACHE".into(), mod_cache.clone().unwrap_or_else(|| scratch.join("gomodcache")).to_string_lossy().into_owned()),
                // Offline is enforced, not assumed (§9.1): no proxy, no checksum database, no
                // toolchain download for a go.mod that names a newer Go, no edit of go.mod.
                ("GOFLAGS".into(), "-mod=readonly".into()),
                ("GOPROXY".into(), "off".into()),
                ("GOSUMDB".into(), "off".into()),
                ("GOTOOLCHAIN".into(), "local".into()),
                ("GOWORK".into(), "off".into()),
                ("GOTELEMETRY".into(), "off".into()),
                // cgo would run the C compiler over the module's C sources: not code execution,
                // but nothing the index needs.
                ("CGO_ENABLED".into(), "0".into()),
            ]);
            let mut read_only = vec![repo.root.clone(), goroot, scip_go.parent().unwrap().to_path_buf(), scip.parent().unwrap().to_path_buf(), this.parent().unwrap().to_path_buf()];
            read_only.extend(mod_cache);
            (command, env, read_only, indexer_version("scip-go"), settings.small_ceiling_mb << 20)
        }
        "scip-java" => {
            let (launcher, java_home) = tools.scip_java.clone()?;
            // Gradle and Maven write their output into the project, which is read-only here: the
            // build runs in a copy of the tracked sources in scratch (as §6.2 does for codex/).
            let copy = scratch.join("src");
            let m2 = home.join(".m2/repository");
            let gradle_caches = home.join(".gradle/caches");
            let maven = root_dir.join("pom.xml").is_file() && !JAVA_BUILD_FILES[..4].iter().any(|f| root_dir.join(f).is_file());
            // Maven: a local repository in scratch with the user's as its read-only tail (Maven
            // 3.9+), offline. Gradle: its home in scratch, the user's dependency cache read-only.
            let build_args = if maven {
                format!(
                    " -- --batch-mode --offline -DskipTests -Dmaven.repo.local={} -Dmaven.repo.local.tail={} clean verify",
                    sh_quote(&scratch.join("m2").to_string_lossy()),
                    sh_quote(&m2.to_string_lossy())
                )
            } else {
                String::new()
            };
            let command = format!(
                "{copy_sources} && cd {copy} && {launcher} index --output {out}{build_args} {convert}",
                copy_sources = copy_sources(repo, &root_dir, &copy),
                copy = sh_quote(&copy.to_string_lossy()),
                launcher = sh_quote(&launcher.to_string_lossy()),
                out = sh_quote(&raw_file.to_string_lossy()),
            );
            let mut env = base_env(&home, &format!("{}:/usr/bin:/bin", java_home.join("bin").to_string_lossy()));
            env.extend([
                ("JAVA_HOME".into(), java_home.to_string_lossy().into_owned()),
                ("GRADLE_USER_HOME".into(), scratch.join("gradle-home").to_string_lossy().into_owned()),
                ("GRADLE_OPTS".into(), "-Dorg.gradle.daemon=false".into()),
            ]);
            if gradle_caches.is_dir() {
                env.push(("GRADLE_RO_DEP_CACHE".into(), gradle_caches.to_string_lossy().into_owned()));
            }
            let mut read_only = vec![repo.root.clone(), java_home, launcher.parent().unwrap().to_path_buf(), scip.parent().unwrap().to_path_buf(), this.parent().unwrap().to_path_buf()];
            read_only.extend([m2, gradle_caches].into_iter().filter(|p| p.is_dir()));
            (command, env, read_only, indexer_version("scip-java"), settings.memory_ceiling_mb << 20)
        }
        "scip-dotnet" => {
            let (dotnet_root, dll) = tools.scip_dotnet.clone()?;
            let copy = scratch.join("src");
            let packages = home.join(".nuget/packages");
            // Restore only from the user's package folder, read-only, into a folder in scratch:
            // no feed on the network is configured, so nothing can be fetched.
            let nuget_config = scratch.join("nuget.config");
            let sources = if packages.is_dir() { format!("<add key=\"local\" value=\"{}\" />", xml_escape(&packages.to_string_lossy())) } else { String::new() };
            let config = format!("<?xml version=\"1.0\" encoding=\"utf-8\"?><configuration><packageSources><clear />{sources}</packageSources></configuration>");
            let command = format!(
                "{copy_sources} && printf '%s' {config} > {nuget_config} && cd {copy} && {dotnet} {dll} index {file} --working-directory {copy} --output {out} --nuget-config-path {nuget_config} {convert}",
                copy_sources = copy_sources(repo, &root_dir, &copy),
                config = sh_quote(&config),
                nuget_config = sh_quote(&nuget_config.to_string_lossy()),
                copy = sh_quote(&copy.to_string_lossy()),
                dotnet = sh_quote(&dotnet_root.join("dotnet").to_string_lossy()),
                dll = sh_quote(&dll.to_string_lossy()),
                file = sh_quote(&copy.join(&target.file).to_string_lossy()),
                out = sh_quote(&raw_file.to_string_lossy()),
            );
            let mut env = base_env(&home, &format!("{}:/usr/bin:/bin", dotnet_root.to_string_lossy()));
            env.extend([
                ("DOTNET_ROOT".into(), dotnet_root.to_string_lossy().into_owned()),
                ("DOTNET_CLI_HOME".into(), scratch.join("dotnet-home").to_string_lossy().into_owned()),
                ("NUGET_PACKAGES".into(), scratch.join("nuget-packages").to_string_lossy().into_owned()),
                ("DOTNET_CLI_TELEMETRY_OPTOUT".into(), "1".into()),
                ("DOTNET_NOLOGO".into(), "1".into()),
                ("DOTNET_SKIP_FIRST_TIME_EXPERIENCE".into(), "1".into()),
                // No build server or node that outlives the run.
                ("MSBUILDDISABLENODEREUSE".into(), "1".into()),
                ("DOTNET_CLI_DO_NOT_USE_MSBUILD_SERVER".into(), "1".into()),
            ]);
            let mut read_only = vec![repo.root.clone(), dotnet_root, dll.parent().unwrap().to_path_buf(), scip.parent().unwrap().to_path_buf(), this.parent().unwrap().to_path_buf()];
            read_only.extend(Some(packages).filter(|p| p.is_dir()));
            (command, env, read_only, indexer_version("scip-dotnet"), settings.memory_ceiling_mb << 20)
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

/// The shell step that copies a root's sources into `copy` for an indexer that builds the project
/// and so writes into it: the files git tracks (no build output, no secrets the user left
/// untracked), or, outside git, everything but the usual build and dependency directories.
fn copy_sources(repo: &Repo, root_dir: &Path, copy: &Path) -> String {
    let (root, copy) = (sh_quote(&root_dir.to_string_lossy()), sh_quote(&copy.to_string_lossy()));
    let list = if repo.is_git {
        format!("git -C {root} ls-files -z | tar -C {root} --null -T - -cf -")
    } else {
        format!("tar -C {root} --exclude=./target --exclude=./build --exclude=./bin --exclude=./obj --exclude=./node_modules --exclude=./.gradle -cf - .")
    };
    format!("rm -rf {copy} && mkdir -p {copy} && {list} | tar -C {copy} -xf -")
}

/// The user's Go module cache, when there is one: `$GOMODCACHE`, else `$GOPATH/pkg/mod`, else
/// `~/go/pkg/mod`.
fn go_mod_cache(home: &Path) -> Option<PathBuf> {
    let from_env = std::env::var_os("GOMODCACHE").map(PathBuf::from);
    let from_gopath = std::env::var_os("GOPATH").and_then(|p| std::env::split_paths(&p).next()).map(|p| p.join("pkg/mod"));
    from_env.or(from_gopath).unwrap_or_else(|| home.join("go/pkg/mod")).canonicalize().ok().filter(|p| p.is_dir())
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('"', "&quot;").replace('<', "&lt;").replace('>', "&gt;")
}

/// This program, which the sandbox runs for `scip-repair` (`PUFFIN_CODE_SELF` in tests, whose
/// own executable is the test harness).
fn self_exe() -> PathBuf {
    std::env::var_os("PUFFIN_CODE_SELF").map(PathBuf::from).unwrap_or_else(|| std::env::current_exe().unwrap_or_else(|_| PathBuf::from("puffin-code")))
}

/// Quotes a string for `sh`.
pub fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// The plan for a repository: the universal run, the static runs, and, when `exact` is set and
/// the repository is trusted, the executing runs.
pub fn build(repo: &Repo, settings: &Settings, exact: bool) -> (Plan, Vec<String>) {
    build_with(repo, settings, exact, exact)
}

/// [`build`], with the on-demand static indexers (scip-clang, scip-go: §6.1) included when
/// `on_demand` is set: on an explicit `puffin-code index` or an exact run, never at launch.
pub fn build_with(repo: &Repo, settings: &Settings, exact: bool, on_demand: bool) -> (Plan, Vec<String>) {
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
    skipped.extend(root_python_note(root_python_files(repo).1, ""));
    let (in_submodules, skipped_in_submodules) = detect_in_submodules(repo, &crate::submodules::evaluate(repo, settings));
    skipped.extend(skipped_in_submodules);
    for target in detect(repo).into_iter().chain(in_submodules) {
        if target.kind == Kind::Executing && !(exact && trusted) {
            if !trusted {
                skipped.push(format!("{} for {}: the repository is not trusted", target.indexer, display_root(&target.root)));
            }
            continue;
        }
        if target.on_demand && !on_demand {
            skipped.push(format!("{} for {}: runs on demand (`puffin-code index`)", target.indexer, display_root(&target.root)));
            continue;
        }
        if let Some(why) = tools.unavailable(target.indexer) {
            skipped.push(format!("{} for {}: {why}", target.indexer, display_root(&target.root)));
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

pub fn display_root(root: &str) -> &str {
    if root.is_empty() {
        "the repository"
    } else {
        root
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::sandbox;
    use std::process::Command;

    fn git(root: &Path, args: &[&str]) {
        let ok = Command::new("git")
            .args(["-c", "user.name=t", "-c", "user.email=t@localhost", "-c", "commit.gpgsign=false"])
            .args(args)
            .current_dir(root)
            .output()
            .unwrap()
            .status
            .success();
        assert!(ok, "git {args:?}");
    }

    /// A git repository holding `files` (path, content), committed.
    fn make_repo(files: &[(&str, &str)]) -> (tempfile::TempDir, Repo) {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("repo");
        for (path, content) in files {
            let path = root.join(path);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, content).unwrap();
        }
        git(&root, &["init", "-q"]);
        git(&root, &["add", "-A"]);
        git(&root, &["commit", "-qm", "init"]);
        let repo = Repo { root: root.clone(), main_root: root, is_git: true };
        (dir, repo)
    }

    fn found(targets: &[Target]) -> Vec<(String, String, bool, String)> {
        targets.iter().map(|t| (t.indexer.to_string(), t.root.clone(), t.on_demand, t.file.clone())).collect()
    }

    fn t(indexer: &str, root: &str, on_demand: bool, file: &str) -> (String, String, bool, String) {
        (indexer.to_string(), root.to_string(), on_demand, file.to_string())
    }

    #[test]
    fn each_language_is_detected_by_its_project_files() {
        let (_dir, repo) = make_repo(&[
            ("app/tsconfig.json", "{}"),
            ("app/src/a.ts", "export const a = 1;\n"),
            ("web/package.json", "{\"name\":\"web\"}"),
            ("web/index.js", "module.exports = 1;\n"),
            ("deps-only/package.json", "{\"name\":\"tooling\"}"),
            ("native/compile_commands.json", "[]"),
            ("cpp/build/compile_commands.json", "[]"),
            ("svc/go.mod", "module example.com/svc\n\ngo 1.22\n"),
            ("jvm/pom.xml", "<project/>"),
            ("gradle/build.gradle.kts", ""),
            ("net/App.sln", ""),
            ("net/App.csproj", "<Project/>"),
            ("vb/Lib.vbproj", "<Project/>"),
        ]);
        let mut got = found(&detect(&repo));
        got.sort();
        let mut want = vec![
            t("scip-typescript", "app", false, ""),
            t("scip-typescript", "web", false, ""),
            t("scip-clang", "native", true, "compile_commands.json"),
            t("scip-clang", "cpp", true, "build/compile_commands.json"),
            t("scip-go", "svc", true, ""),
            t("scip-java", "jvm", false, ""),
            t("scip-java", "gradle", false, ""),
            // A solution is preferred over the projects it lists.
            t("scip-dotnet", "net", false, "App.sln"),
            t("scip-dotnet", "vb", false, "Lib.vbproj"),
        ];
        want.sort();
        assert_eq!(got, want);
        // A package.json with no sources of its own is tooling, not a project.
        assert!(detect(&repo).iter().all(|t| t.root != "deps-only"));
        let kinds: Vec<(&str, Kind)> = detect(&repo).iter().map(|t| (t.indexer, t.kind)).collect();
        for (indexer, kind) in kinds {
            let executing = matches!(indexer, "scip-java" | "scip-dotnet");
            assert_eq!(kind == Kind::Executing, executing, "{indexer}");
        }
    }

    #[test]
    fn a_python_file_at_the_root_is_a_root_of_its_own() {
        let (_dir, repo) = make_repo(&[
            ("setup.py", "from pkg import f\n"),
            ("big.py", "def a():\n    return 1\n\n\ndef b():\n    return a()\n"),
            ("stubs.pyi", "def a() -> int: ...\n"),
            ("notes.txt", "x"),
            ("pkg/__init__.py", "def f():\n    return 1\n"),
            ("pkg/deep/mod.py", "X = 1\n"),
        ]);
        // Untracked: the user's scratch file, not the project's.
        std::fs::write(repo.root.join("scratch.py"), "Y = 2\n").unwrap();
        let python: Vec<String> = detect(&repo).into_iter().filter(|t| t.indexer == "scip-python").map(|t| t.root).collect();
        // The directory root, then the files, largest first.
        assert_eq!(python, ["big.py", "stubs.pyi", "setup.py", "pkg"]);
        assert_eq!(root_python_files(&repo), (vec!["big.py".to_string(), "stubs.pyi".to_string(), "setup.py".to_string()], 0));
        // The run names the file, and its documents map back to it and to what it imports.
        std::env::set_var("PUFFIN_CODE_SCRATCH_DIR", _dir.path().join("scratch"));
        let tools = Tools { scip_python: Some((_dir.path().join("node/bin/node"), _dir.path().join("indexers/index.js"))), ..fake_tools(_dir.path()) };
        let run = run_for(&repo, &tools, Target::new(Kind::Static, "scip-python", "setup.py"));
        assert!(run.spec.argv[2].contains("--target-only 'setup.py'"), "{}", run.spec.argv[2]);
        assert_eq!(run.path_prefix, "setup.py/");
        assert_eq!(crate::scip_store::join_normalized(&run.path_prefix, ""), "setup.py");
        assert_eq!(crate::scip_store::join_normalized(&run.path_prefix, "../pkg/__init__.py"), "pkg/__init__.py");
        assert_eq!(super::super::store::slug("scip-python", "setup.py"), "scip-python-setup.py");
    }

    #[test]
    fn only_the_largest_root_files_are_indexed_and_the_rest_are_named() {
        let files: Vec<(String, String)> = (0..ROOT_PYTHON_FILES + 3).map(|i| (format!("s{i:02}.py"), "x = 1\n".repeat(i + 1))).collect();
        let borrowed: Vec<(&str, &str)> = files.iter().map(|(n, c)| (n.as_str(), c.as_str())).collect();
        let (_dir, repo) = make_repo(&borrowed);
        let (kept, more) = root_python_files(&repo);
        assert_eq!((kept.len(), more), (ROOT_PYTHON_FILES, 3));
        assert_eq!(kept[0], format!("s{:02}.py", ROOT_PYTHON_FILES + 2));
        assert!(!kept.contains(&"s00.py".to_string()));
        assert_eq!(detect(&repo).iter().filter(|t| t.indexer == "scip-python").count(), ROOT_PYTHON_FILES);
        let note = root_python_note(more, "").unwrap();
        assert!(note.contains("3 more Python files at the root of the repository"), "{note}");
        assert_eq!(root_python_note(0, ""), None);
    }

    #[test]
    fn a_root_project_covers_its_subdirectories_except_for_go() {
        let (_dir, repo) = make_repo(&[
            ("tsconfig.json", "{}"),
            ("index.ts", "export {};\n"),
            ("pkg/tsconfig.json", "{}"),
            ("pkg/a.ts", "export {};\n"),
            ("settings.gradle", ""),
            ("lib/build.gradle", ""),
            ("App.sln", ""),
            ("Lib/Lib.csproj", "<Project/>"),
            ("go.mod", "module example.com/m\n"),
            ("tools/go.mod", "module example.com/m/tools\n"),
        ]);
        let mut got = found(&detect(&repo));
        got.sort();
        let mut want = vec![
            t("scip-typescript", "", false, ""),
            t("scip-java", "", false, ""),
            t("scip-dotnet", "", false, "App.sln"),
            t("scip-go", "", true, ""),
            t("scip-go", "tools", true, ""),
        ];
        want.sort();
        assert_eq!(got, want);
    }

    fn fake_tools(dir: &Path) -> Tools {
        let p = |name: &str| dir.join(name);
        Tools {
            codebase_memory: None,
            scip: Some(p("bin/scip")),
            scip_python: None,
            rust_analyzer: None,
            scip_typescript: Some((p("node/bin/node"), p("indexers/node_modules/@sourcegraph/scip-typescript/dist/src/main.js"))),
            scip_clang: Some(p("bin/scip-clang")),
            scip_go: Some((p("bin/scip-go"), p("go"))),
            scip_java: Some((p("bin/scip-java"), p("jdk"))),
            scip_dotnet: Some((p("dotnet"), p("indexers/scip-dotnet/scip-dotnet.dll"))),
        }
    }

    fn run_for(repo: &Repo, tools: &Tools, target: Target) -> Run {
        exact_run(repo, &Settings::default(), tools, &target, 0).unwrap_or_else(|| panic!("no run for {}", target.indexer))
    }

    fn env_of<'a>(run: &'a Run, key: &str) -> Option<&'a str> {
        run.spec.env.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_str())
    }

    #[test]
    fn every_run_writes_only_into_its_scratch_directory() {
        let (dir, repo) = make_repo(&[("a/tsconfig.json", "{}"), ("a/x.ts", "export {};\n")]);
        std::env::set_var("PUFFIN_CODE_SCRATCH_DIR", dir.path().join("scratch"));
        let tools = fake_tools(dir.path());
        let targets = [
            Target::new(Kind::Static, "scip-typescript", "a"),
            Target { file: "compile_commands.json".into(), on_demand: true, ..Target::new(Kind::Static, "scip-clang", "a") },
            Target { on_demand: true, ..Target::new(Kind::Static, "scip-go", "a") },
            Target::new(Kind::Executing, "scip-java", "a"),
            Target { file: "App.sln".into(), ..Target::new(Kind::Executing, "scip-dotnet", "a") },
        ];
        for target in targets {
            let run = run_for(&repo, &tools, target.clone());
            let args = sandbox::bwrap_args(&run.spec);
            assert!(sandbox::unexpected_writable(&args, &run.spec).is_empty(), "{}: {args:?}", target.indexer);
            assert!(run.spec.writable_outside_scratch.is_empty(), "{}", target.indexer);
            assert!(args.contains(&"--unshare-net".to_string()));
            // The source is bound read-only, never read-write.
            let script = &run.spec.argv[2];
            assert!(script.contains(&run.scip_output.as_ref().unwrap().parent().unwrap().to_string_lossy().to_string()));
            assert_eq!(run.version, indexer_version(target.indexer));
            assert!(run.spec.read_only.contains(&repo.root), "{}", target.indexer);
        }
    }

    #[test]
    fn go_is_offline_by_construction() {
        let (dir, repo) = make_repo(&[("svc/go.mod", "module example.com/svc\n\ngo 1.99\n"), ("svc/main.go", "package main\n")]);
        std::env::set_var("PUFFIN_CODE_SCRATCH_DIR", dir.path().join("scratch"));
        let run = run_for(&repo, &fake_tools(dir.path()), Target { on_demand: true, ..Target::new(Kind::Static, "scip-go", "svc") });
        for (key, value) in [("GOPROXY", "off"), ("GOSUMDB", "off"), ("GOFLAGS", "-mod=readonly"), ("GOTOOLCHAIN", "local"), ("GOWORK", "off"), ("CGO_ENABLED", "0")] {
            assert_eq!(env_of(&run, key), Some(value), "{key}");
        }
        // Build and module caches of its own, in scratch.
        for key in ["GOCACHE", "GOPATH"] {
            assert!(Path::new(env_of(&run, key).unwrap()).starts_with(&run.spec.scratch), "{key}");
        }
    }

    #[test]
    fn typescript_without_a_config_infers_one_into_scratch() {
        let (dir, repo) = make_repo(&[("web/package.json", "{}"), ("web/index.js", "module.exports = 1;\n")]);
        std::env::set_var("PUFFIN_CODE_SCRATCH_DIR", dir.path().join("scratch"));
        let run = run_for(&repo, &fake_tools(dir.path()), Target::new(Kind::Static, "scip-typescript", "web"));
        let script = &run.spec.argv[2];
        let config = run.spec.scratch.join("tsconfig.json");
        assert!(script.contains(&format!("> {}", sh_quote(&config.to_string_lossy()))), "{script}");
        assert!(script.contains("allowJs"), "{script}");
        assert!(!script.contains("--infer-tsconfig"), "it would write into the tree: {script}");
        // With a tsconfig, the project's own is used and nothing is written.
        let (dir2, repo2) = make_repo(&[("app/tsconfig.json", "{}"), ("app/a.ts", "export {};\n")]);
        std::env::set_var("PUFFIN_CODE_SCRATCH_DIR", dir2.path().join("scratch"));
        let run = run_for(&repo2, &fake_tools(dir2.path()), Target::new(Kind::Static, "scip-typescript", "app"));
        assert!(!run.spec.argv[2].contains("printf"), "{}", run.spec.argv[2]);
    }

    #[test]
    fn jvm_and_dotnet_build_a_copy_offline() {
        let (dir, repo) = make_repo(&[("jvm/pom.xml", "<project/>"), ("net/App.csproj", "<Project/>")]);
        std::env::set_var("PUFFIN_CODE_SCRATCH_DIR", dir.path().join("scratch"));
        let tools = fake_tools(dir.path());
        let java = run_for(&repo, &tools, Target::new(Kind::Executing, "scip-java", "jvm"));
        let script = &java.spec.argv[2];
        // Maven builds in a copy of the tracked sources, offline, with its own local repository.
        assert!(script.contains("ls-files -z"), "{script}");
        assert!(script.contains(&format!("cd {}", sh_quote(&java.spec.scratch.join("src").to_string_lossy()))), "{script}");
        assert!(script.contains("--offline") && script.contains("maven.repo.local.tail"), "{script}");
        assert_eq!(env_of(&java, "JAVA_HOME"), Some(dir.path().join("jdk").to_string_lossy().as_ref()));
        assert!(Path::new(env_of(&java, "GRADLE_USER_HOME").unwrap()).starts_with(&java.spec.scratch));
        let dotnet = run_for(&repo, &tools, Target { file: "App.csproj".into(), ..Target::new(Kind::Executing, "scip-dotnet", "net") });
        let script = &dotnet.spec.argv[2];
        assert!(script.contains("--nuget-config-path") && script.contains("<clear />"), "{script}");
        assert!(script.contains(&sh_quote(&dotnet.spec.scratch.join("src/App.csproj").to_string_lossy())), "{script}");
        for key in ["NUGET_PACKAGES", "DOTNET_CLI_HOME"] {
            assert!(Path::new(env_of(&dotnet, key).unwrap()).starts_with(&dotnet.spec.scratch), "{key}");
        }
        assert_eq!(env_of(&dotnet, "DOTNET_CLI_TELEMETRY_OPTOUT"), Some("1"));
    }

    #[test]
    fn unavailable_indexers_say_why() {
        let none = Tools { scip_typescript: None, scip_clang: None, scip_go: None, scip_java: None, scip_dotnet: None, ..fake_tools(Path::new("/x")) };
        assert!(none.unavailable("scip-clang").unwrap().contains("no linux-arm64 release"));
        assert!(none.unavailable("scip-go").unwrap().contains("Go toolchain"));
        assert!(none.unavailable("scip-java").unwrap().contains("JDK 17"));
        assert!(none.unavailable("scip-dotnet").unwrap().contains(".NET SDK"));
        assert!(none.unavailable("scip-typescript").unwrap().contains("Node.js"));
        let all = fake_tools(Path::new("/x"));
        for indexer in ["scip-typescript", "scip-clang", "scip-go", "scip-java", "scip-dotnet"] {
            assert_eq!(all.unavailable(indexer), None, "{indexer}");
        }
        let no_scip = Tools { scip: None, ..fake_tools(Path::new("/x")) };
        assert!(no_scip.unavailable("scip-go").unwrap().contains("scip CLI"));
    }
}
