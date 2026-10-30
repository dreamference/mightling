//! `ling uninstall [--purge] [--yes]`: removes a release install of the client
//! (specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md §16.4, bug W5 of §23.4).
//!
//! It removes exactly what the release installers write, and only release files by name, so a
//! folder that holds anything else keeps it:
//!
//! - **Windows** (`install.ps1`): the binaries in `%LOCALAPPDATA%\Programs\Mightling\bin` (the
//!   folder of the running `ling.exe`), that folder's entry in the user's `Path`, and the mDNS
//!   firewall rules whose program is in this install's folders (an Administrator step, as in
//!   `install.ps1`; over SSH it prints the command for an administrator instead). A running `.exe`
//!   cannot delete itself, so a detached PowerShell waits for this process to end and removes the
//!   rest. The desktop app has its own uninstaller (Settings, Apps), and while it is installed its
//!   firewall rules stay; the sandbox accounts stay too, since upstream Codex may use them.
//! - **Linux and macOS** (`install.sh`'s client): the binaries in
//!   `~/.local/share/dreamference/mightling/bin`, its `VERSION`, and the links in `~/.local/bin`
//!   that point there (on a Mac the desktop app install.sh put in Applications stays, as on
//!   Windows; the installers' record of it goes once the app is gone). `install.sh` writes no PATH line into a shell's rc file, so there is none to
//!   take out. A node's `ling` is part of the node (Night Shift, jobs, the web UI), so a node is
//!   refused; so is a `ling` built from a checkout (`ling-admin codex build`, no release version),
//!   whose files the checkout's build owns (it installs into the same folder).
//!
//! `~/.mightling` (`CODEX_HOME`: sessions, settings, the remembered node) stays unless `--purge`.
//! The pure parts (rule names, PATH editing, which files are the release's, the scripts) are
//! tested on every platform; only the registry and the spawning are Windows code.

use std::path::Path;
use std::path::PathBuf;

use sha2::Digest;
use sha2::Sha256;

const USAGE: &str = "Usage: ling uninstall [--purge] [--yes]";

/// Every mDNS firewall rule of an install is named with this prefix (install.ps1).
pub const RULE_PREFIX: &str = "Mightling-mDNS-";

/// What the command line asked for.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Options {
    pub purge: bool,
    pub yes: bool,
}

/// Parses `ling uninstall`'s arguments; `None` for anything else.
pub fn parse(args: &[String]) -> Option<Options> {
    let mut options = Options::default();
    for arg in args {
        match arg.as_str() {
            "--purge" => options.purge = true,
            "--yes" | "-y" => options.yes = true,
            _ => return None,
        }
    }
    Some(options)
}

/// The firewall rule of one program (§16.4): the prefix and the first 8 hex digits of the SHA-256
/// of its full path, lowercased, in UTF-8. install.ps1 computes the same in PowerShell
/// (`RuleName`); tests/test_install_ps1.py holds the same examples.
pub fn rule_name(program: &str) -> String {
    let digest = Sha256::digest(program.to_lowercase().as_bytes());
    let hex: String = digest[..4].iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{RULE_PREFIX}{hex}")
}

/// The program names a release installs beside `ling`, by both installers and `ling update`.
fn release_names() -> Vec<&'static str> {
    let mut names = vec!["ling", crate::update::CODE_MODE_HOST];
    names.extend(crate::update::optional_commands());
    names.extend(crate::update::WINDOWS_SANDBOX_HELPERS);
    // Listed in §16.2; no release carries it yet, and one that does must not leave it behind.
    names.push("codex-windows-sandbox-service");
    names
}

/// The release's file names with this platform's suffix (`.exe` on Windows).
pub fn release_files(exe_suffix: &str) -> Vec<String> {
    release_names().into_iter().map(|name| format!("{name}{exe_suffix}")).collect()
}

/// Whether a file in the install folder is the release's: one of `files`, or what an install or an
/// update left of one (`<name>.old` and `<name>.<n>.old` moved aside on Windows, `.<name>.update`
/// staged by `ling update`, `.<name>.new` staged by install.sh).
pub fn is_release_file(file: &str, files: &[String]) -> bool {
    files.iter().any(|name| {
        if file == name || file == format!(".{name}.update") || file == format!(".{name}.new") {
            return true;
        }
        let Some(middle) = file.strip_prefix(name.as_str()).and_then(|rest| rest.strip_suffix(crate::update::OLD_SUFFIX)) else {
            return false;
        };
        middle.is_empty() || middle.strip_prefix('.').is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
    })
}

/// Expands `%NAME%` in a PATH entry as Windows does, leaving a name `lookup` does not know as written.
pub fn expand_vars(text: &str, lookup: impl Fn(&str) -> Option<String>) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(start) = rest.find('%') {
        out.push_str(&rest[..start]);
        let after = &rest[start + 1..];
        match after.find('%') {
            Some(end) if end > 0 => match lookup(&after[..end]) {
                Some(value) => {
                    out.push_str(&value);
                    rest = &after[end + 1..];
                }
                None => {
                    out.push('%');
                    rest = after;
                }
            },
            _ => {
                out.push('%');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// One PATH entry as compared: expanded, unquoted, without a trailing separator, `/` as `\`, and
/// lowercased, since Windows paths ignore case.
fn path_key(entry: &str) -> String {
    entry.trim().trim_matches('"').replace('/', "\\").trim_end_matches('\\').to_lowercase()
}

/// The user's PATH value, as stored (`%VARS%` unexpanded), without every entry naming `dir`; the
/// other entries stay exactly as written, in order. `None` when no entry names it, so nothing is
/// written.
pub fn path_without(value: &str, dir: &str, lookup: impl Fn(&str) -> Option<String>) -> Option<String> {
    let wanted = path_key(dir);
    if wanted.is_empty() {
        return None;
    }
    let entries: Vec<&str> = value.split(';').collect();
    let kept: Vec<&str> = entries.iter().copied().filter(|entry| path_key(&expand_vars(entry, &lookup)) != wanted).collect();
    (kept.len() != entries.len()).then(|| kept.join(";"))
}

/// A PowerShell single-quoted string.
fn quoted(text: &str) -> String {
    format!("'{}'", text.replace('\'', "''"))
}

/// The PowerShell that removes the mDNS rules whose program is in one of `dirs`, whatever their
/// name (a 1.7.1 install's `Mightling-mDNS-ling` too), as install.ps1 does before it adds its own.
/// One line, so it is also what an administrator pastes.
pub fn firewall_command(dirs: &[PathBuf]) -> String {
    let mine: Vec<String> = dirs
        .iter()
        .map(|dir| quoted(&format!("{}\\", dir.to_string_lossy().trim_end_matches('\\'))))
        .collect();
    format!(
        "$Mine = @({}); Get-NetFirewallRule | Where-Object {{ $_.Name -like '{RULE_PREFIX}*' }} | Where-Object {{ $Program = [string]($_ | Get-NetFirewallApplicationFilter).Program; @($Mine | Where-Object {{ $Program.StartsWith($_, 'OrdinalIgnoreCase') }}).Count -gt 0 }} | Remove-NetFirewallRule -ErrorAction Stop",
        mine.join(", ")
    )
}

/// What `powershell.exe -EncodedCommand` takes: the script in UTF-16LE, in base64. No quoting of
/// a command line can then go wrong.
pub fn encoded(script: &str) -> String {
    use base64::Engine;
    let bytes: Vec<u8> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

/// Exit codes of [`elevation_script`].
pub const ELEVATED_DONE: i32 = 0;
pub const ELEVATED_FAILED: i32 = 1;
pub const NO_DESKTOP: i32 = 3;
pub const DECLINED: i32 = 4;

/// Runs `command` with Administrator rights the way install.ps1 does: directly in an elevated
/// process, through the UAC prompt in a desktop session, and not at all in a session with no
/// desktop (SSH, a service: session 0), where the prompt cannot appear (W7).
pub fn elevation_script(command: &str) -> String {
    let inner = format!("$ErrorActionPreference = 'Stop'; try {{ {command}; exit {ELEVATED_DONE} }} catch {{ exit {ELEVATED_FAILED} }}");
    format!(
        "$Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())\n\
         if ($Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{ powershell.exe -NoProfile -NonInteractive -EncodedCommand {code}; exit $LASTEXITCODE }}\n\
         if (-not [Environment]::UserInteractive) {{ exit {NO_DESKTOP} }}\n\
         try {{ $Run = Start-Process -FilePath powershell.exe -ArgumentList '-NoProfile -NonInteractive -EncodedCommand {code}' -Verb RunAs -Wait -PassThru -WindowStyle Hidden; exit $Run.ExitCode }} catch {{ exit {DECLINED} }}\n",
        code = encoded(&inner)
    )
}

/// The PowerShell a detached process runs once `pid` (this `ling`) has exited: it removes the
/// files left in `dir` (the running `ling.exe` at least), `dir` and `parent` when empty, and
/// `purge` with everything in it, and writes what it could not remove to `log`.
pub fn cleanup_script(pid: u32, dir: &Path, files: &[String], parent: Option<&Path>, purge: Option<&Path>, log: &Path) -> String {
    let mut lines = vec![
        format!("Wait-Process -Id {pid} -Timeout 120 -ErrorAction SilentlyContinue"),
        "Start-Sleep -Milliseconds 500".to_string(),
        "$Left = @()".to_string(),
        format!("$Dir = {}", quoted(&dir.to_string_lossy())),
    ];
    for file in files {
        lines.push(format!(
            "$File = Join-Path $Dir {}; if (Test-Path -LiteralPath $File) {{ Remove-Item -LiteralPath $File -Force -ErrorAction SilentlyContinue; if (Test-Path -LiteralPath $File) {{ $Left += $File }} }}",
            quoted(file)
        ));
    }
    lines.push("if ((Test-Path -LiteralPath $Dir) -and -not (Get-ChildItem -LiteralPath $Dir -Force)) { Remove-Item -LiteralPath $Dir -Force -ErrorAction SilentlyContinue }".to_string());
    if let Some(parent) = parent {
        lines.push(format!(
            "$Parent = {}; if ((Test-Path -LiteralPath $Parent) -and -not (Get-ChildItem -LiteralPath $Parent -Force)) {{ Remove-Item -LiteralPath $Parent -Force -ErrorAction SilentlyContinue }}",
            quoted(&parent.to_string_lossy())
        ));
    }
    if let Some(home) = purge {
        lines.push(format!(
            "$Home2 = {}; if (Test-Path -LiteralPath $Home2) {{ Remove-Item -LiteralPath $Home2 -Recurse -Force -ErrorAction SilentlyContinue; if (Test-Path -LiteralPath $Home2) {{ $Left += $Home2 }} }}",
            quoted(&home.to_string_lossy())
        ));
    }
    lines.push(format!(
        "if ($Left.Count -gt 0) {{ Set-Content -LiteralPath {} -Value $Left -Encoding UTF8 }}",
        quoted(&log.to_string_lossy())
    ));
    lines.join("\n") + "\n"
}

/// The links in `link_dir` that point into `bin` under one of `names` (install.sh's, and
/// `ling update`'s): the ones to remove. A real file of that name, or a link elsewhere, is not ours.
pub fn links_into(link_dir: &Path, bin: &Path, names: &[String]) -> Vec<PathBuf> {
    let resolved_bin = bin.canonicalize().unwrap_or_else(|_| bin.to_path_buf());
    names
        .iter()
        .map(|name| link_dir.join(name))
        .filter(|link| {
            link.is_symlink()
                && std::fs::read_link(link).is_ok_and(|target| {
                    let target = if target.is_absolute() { target } else { link_dir.join(target) };
                    let parent = target.parent().map(Path::to_path_buf).unwrap_or_default();
                    parent == bin || parent.canonicalize().is_ok_and(|parent| parent == resolved_bin)
                })
        })
        .collect()
}

/// The installers' record of the desktop app beside `bin` (`update::APP_RECORD`, which `ling update`
/// reads): the record's path, and the app it names when that is still there. The app has its own way
/// out (Settings > Apps on Windows, the Trash on a Mac), so a present app and its record stay; the
/// record of an app that is gone goes with the client.
pub fn app_record(bin: &Path) -> Option<(PathBuf, Option<PathBuf>)> {
    let record = bin.parent()?.join(crate::update::APP_RECORD);
    let text = std::fs::read_to_string(&record).ok()?;
    let app = crate::update::parse_app_record(&text).map(|parsed| parsed.path).filter(|path| path.exists());
    Some((record, app))
}

/// The desktop app's folder as its installer registered it (or its one place), for `ling app`.
#[cfg(windows)]
pub fn registered_app_dir() -> Option<PathBuf> {
    platform::app_dir()
}

/// The release files present in `dir`, by file name, sorted.
pub fn release_files_in(dir: &Path, files: &[String]) -> Vec<String> {
    let mut found: Vec<String> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .filter(|name| is_release_file(name, files))
        .collect();
    found.sort();
    found
}

/// Why this `ling` cannot uninstall itself, if it cannot: a build from a checkout where such a build
/// installs into the release's folder (`builds_share_the_folder`: Linux and macOS, where
/// `ling-admin codex build` and install.sh both use `~/.local/share/dreamference/mightling/bin`),
/// a binary not in an installer's `bin` folder, or (Linux, macOS) a node. A release sets the version
/// it compiles in; an empty one is a source build too (CI's builds outside a release).
pub fn refusal(version: Option<&str>, bin: &Path, is_node: bool, builds_share_the_folder: bool) -> Option<String> {
    if builds_share_the_folder && version.is_none_or(str::is_empty) {
        return Some(format!(
            "this ling was built from source (`ling-admin codex build`), not installed from a release; its files in {} belong to that build, so `ling uninstall` leaves them",
            bin.display()
        ));
    }
    if bin.file_name().is_none_or(|name| !name.eq_ignore_ascii_case("bin")) {
        return Some(format!("{} is not an installer's bin folder; remove this ling by hand", bin.display()));
    }
    if is_node {
        return Some(
            "this machine is a Mightling node (it has ~/.config/dreamference/node-id): its ling is part of the node \
             (Night Shift, jobs, the web UI), so `ling uninstall` removes client installs only"
                .to_string(),
        );
    }
    None
}

/// Runs `ling uninstall …` and returns the exit code.
pub fn run_cli(args: &[String]) -> i32 {
    let Some(options) = parse(args) else {
        println!("{USAGE}");
        println!("Removes this Mightling install; --purge also removes ~/.mightling (sessions, settings); --yes asks nothing.");
        return 2;
    };
    let Ok(exe) = std::env::current_exe() else {
        eprintln!("❌ ling uninstall: could not find the running ling");
        return 1;
    };
    let exe = resolved_exe(&exe);
    let Some(bin) = exe.parent().map(Path::to_path_buf) else {
        eprintln!("❌ ling uninstall: {} has no folder", exe.display());
        return 1;
    };
    let is_node = !cfg!(windows) && ling_node_locator::is_node();
    if let Some(reason) = refusal(crate::update::MIGHTLING_VERSION, &bin, is_node, !cfg!(windows)) {
        eprintln!("❌ Not uninstalled: {reason}.");
        return 1;
    }
    let codex_home = ling_node_locator::codex_home();
    platform::run(&options, &exe, &bin, codex_home.as_deref())
}

/// The running `ling` as the install folder holds it. On macOS `current_exe` is the path as invoked,
/// `~/.local/bin/ling` when run through install.sh's link, so links are resolved there (and on
/// Linux, where it changes nothing). On Windows the path stays as written, without the `\\?\` a
/// resolution would add: the rule names and the Path entry are of install.ps1's form.
pub fn resolved_exe(exe: &Path) -> PathBuf {
    if cfg!(windows) { strip_verbatim(exe) } else { exe.canonicalize().unwrap_or_else(|_| exe.to_path_buf()) }
}

/// `\\?\C:\…` as `C:\…`: the form install.ps1 wrote, which the rule names and PATH entries are of.
pub fn strip_verbatim(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC\\") => PathBuf::from(rest),
        _ => path.to_path_buf(),
    }
}

/// Shows what is about to be removed and asks; without a terminal the answer has to be `--yes`.
fn confirmed(plan: &[String], yes: bool) -> Result<bool, String> {
    use std::io::IsTerminal;
    println!("ling uninstall will remove:");
    for line in plan {
        println!("  {line}");
    }
    if yes {
        return Ok(true);
    }
    if !std::io::stdin().is_terminal() {
        return Err("Not uninstalled: nobody to ask. Run it again with --yes after reading the above.".to_string());
    }
    print!("Remove Mightling? [y/N] ");
    let _ = std::io::Write::flush(&mut std::io::stdout());
    let mut answer = String::new();
    std::io::stdin().read_line(&mut answer).map_err(|error| error.to_string())?;
    Ok(matches!(answer.trim().to_ascii_lowercase().as_str(), "y" | "yes"))
}

/// Asks, and says why nothing happened when the answer is no.
fn go_ahead(plan: &[String], yes: bool) -> Option<i32> {
    match confirmed(plan, yes) {
        Ok(true) => None,
        Ok(false) => {
            println!("Nothing removed.");
            Some(1)
        }
        Err(message) => {
            eprintln!("{message}");
            Some(1)
        }
    }
}

#[cfg(not(windows))]
mod platform {
    use super::*;

    /// install.sh's client: links, binaries, `VERSION`, the folders when empty; `~/.mightling` with
    /// `--purge`.
    pub fn run(options: &Options, _exe: &Path, bin: &Path, codex_home: Option<&Path>) -> i32 {
        let files = release_files("");
        let present = release_files_in(bin, &files);
        let install_dir = bin.parent().map(Path::to_path_buf);
        let version = install_dir.as_ref().map(|dir| dir.join("VERSION")).filter(|file| file.is_file());
        let link_dir = ling_node_locator::home_dir().map(|home| home.join(".local").join("bin"));
        let links = link_dir.as_ref().map(|dir| links_into(dir, bin, &files)).unwrap_or_default();
        let record = app_record(bin);

        let mut plan: Vec<String> = links.iter().map(|link| format!("the link {}", link.display())).collect();
        plan.push(format!("{} ({} file(s): {})", bin.display(), present.len(), present.join(", ")));
        if let Some(version) = &version {
            plan.push(version.display().to_string());
        }
        match &record {
            Some((_, Some(app))) => println!("Kept: the desktop app, {} (move it to the Trash to remove it).", app.display()),
            Some((file, None)) => plan.push(format!("{} (the record of a desktop app that is gone)", file.display())),
            None => {}
        }
        let stale_record = record.and_then(|(file, app)| app.is_none().then_some(file));
        match (options.purge, codex_home) {
            (true, Some(home)) => plan.push(format!("{} (--purge: sessions, settings, the remembered node)", home.display())),
            (false, Some(home)) => println!("Kept: {} (sessions, settings; --purge removes it).", home.display()),
            _ => {}
        }
        if let Some(code) = go_ahead(&plan, options.yes) {
            return code;
        }

        let mut left = Vec::new();
        for path in links.iter().cloned().chain(present.iter().map(|file| bin.join(file))).chain(version).chain(stale_record) {
            if let Err(error) = std::fs::remove_file(&path) {
                left.push(format!("{}: {error}", path.display()));
            }
        }
        // Each folder only once it is empty: anything else in it is not the release's.
        let _ = std::fs::remove_dir(bin);
        if let Some(dir) = &install_dir {
            let _ = std::fs::remove_dir(dir);
        }
        if options.purge
            && let Some(home) = codex_home
            && home.exists()
            && let Err(error) = std::fs::remove_dir_all(home)
        {
            left.push(format!("{}: {error}", home.display()));
        }
        finish(&left, bin)
    }
}

/// The closing lines.
fn finish(left: &[String], bin: &Path) -> i32 {
    if left.is_empty() {
        println!("✅ Mightling removed from {}.", bin.display());
        return 0;
    }
    println!("⚠️  Mightling removed from {}, except:", bin.display());
    for line in left {
        println!("   - {line}");
    }
    1
}

#[cfg(windows)]
mod platform {
    use super::*;
    use std::os::windows::process::CommandExt;

    /// `CREATE_BREAKAWAY_FROM_JOB`: an SSH session's job may end its processes when the session
    /// ends, which can come right after this one exits.
    const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
    /// `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`: the removal's PowerShell gets a console of its
    /// own, hidden. Not `DETACHED_PROCESS`: powershell.exe started with no console at all exits
    /// before it runs its script (CI and the test PC, 2026-10-27), which left ling.exe behind.
    const HIDDEN_CONSOLE: u32 = 0x0800_0000 | 0x0000_0200;

    /// The desktop app's folder: where its installer says it is, else its one place.
    pub fn app_dir() -> Option<PathBuf> {
        registry::read_string(r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Mightling", "InstallLocation")
            .map(|(text, _)| PathBuf::from(text))
            .filter(|dir| !dir.as_os_str().is_empty())
            .or_else(|| std::env::var_os("LOCALAPPDATA").map(|dir| PathBuf::from(dir).join(r"Programs\Mightling\App")))
    }

    /// install.ps1's client: the firewall rules, the PATH entry, the binaries, the folders when
    /// empty; `~/.mightling` with `--purge`.
    pub fn run(options: &Options, exe: &Path, bin: &Path, codex_home: Option<&Path>) -> i32 {
        let files = release_files(".exe");
        let present = release_files_in(bin, &files);
        let app = app_dir();
        let app_installed = app.as_ref().is_some_and(|dir| dir.join("Mightling.exe").is_file());
        // The app's rules go only with the app; while it is installed it still listens.
        let mut rule_dirs = vec![bin.to_path_buf()];
        if let Some(app) = app.as_ref().filter(|_| !app_installed) {
            rule_dirs.push(app.clone());
        }
        let parent = bin.parent().map(Path::to_path_buf);
        // install.ps1's record of the app it installed (update.rs, APP_RECORD), in `parent`.
        let stale_record = app_record(bin).filter(|_| !app_installed).map(|(file, _)| file);

        let mut plan = vec![
            format!("{} ({} file(s): {})", bin.display(), present.len(), present.join(", ")),
            format!("its entry in your Path ({})", bin.display()),
            format!("the mDNS firewall rules of {} (an Administrator step)", rule_dirs.iter().map(|dir| dir.display().to_string()).collect::<Vec<_>>().join(" and ")),
        ];
        if let Some(file) = &stale_record {
            plan.push(format!("{} (the record of a desktop app that is gone)", file.display()));
        }
        match (options.purge, codex_home) {
            (true, Some(home)) => plan.push(format!("{} (--purge: sessions, settings, the remembered node)", home.display())),
            (false, Some(home)) => println!("Kept: {} (sessions, settings; --purge removes it).", home.display()),
            _ => {}
        }
        if app_installed {
            println!("Kept: the desktop app and its firewall rules (remove it in Settings > Apps > Installed apps).");
        }
        println!("Kept: the sandbox accounts CodexSandboxOffline and CodexSandboxOnline (upstream Codex may use them).");
        if let Some(code) = go_ahead(&plan, options.yes) {
            return code;
        }

        let mut left = Vec::new();
        // The firewall first: it is the step that may need someone at the screen.
        let command = firewall_command(&rule_dirs);
        match run_elevated(&command) {
            Ok(()) => println!("✅ Firewall: this install's mDNS rules removed."),
            Err(why) => {
                println!("⚠️  Firewall: {why}. An administrator removes this install's rules with, in an Administrator PowerShell:");
                println!("   {command}");
                left.push("the mDNS firewall rules (the command above)".to_string());
            }
        }
        match registry::remove_from_user_path(bin) {
            Ok(true) => println!("✅ Path: {} removed (new terminals see it).", bin.display()),
            Ok(false) => {}
            Err(why) => left.push(format!("the Path entry {}: {why}", bin.display())),
        }
        // What is not running goes now; the running ling.exe, and anything another ling still
        // holds, goes once this process has ended.
        for file in &present {
            let path = bin.join(file);
            if path != exe {
                let _ = std::fs::remove_file(&path);
            }
        }
        if let Some(file) = &stale_record {
            let _ = std::fs::remove_file(file);
        }
        let remaining = release_files_in(bin, &files);
        let log = std::env::temp_dir().join("mightling-uninstall-left.txt");
        let _ = std::fs::remove_file(&log);
        let purge = if options.purge { codex_home } else { None };
        let script = cleanup_script(std::process::id(), bin, &remaining, parent.as_deref(), purge, &log);
        let spawn = |flags: u32| {
            std::process::Command::new("powershell.exe")
                .args(["-NoProfile", "-NonInteractive", "-EncodedCommand", &encoded(&script)])
                .current_dir(std::env::temp_dir())
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .creation_flags(flags)
                .spawn()
        };
        let spawned = spawn(HIDDEN_CONSOLE | CREATE_BREAKAWAY_FROM_JOB).or_else(|_| spawn(HIDDEN_CONSOLE));
        match spawned {
            Ok(_) => println!(
                "The rest of {}{} is removed as soon as this command exits; anything that could not be is listed in {}.",
                bin.display(),
                purge.map(|home| format!(" and {}", home.display())).unwrap_or_default(),
                log.display()
            ),
            Err(error) => left.push(format!("{} (could not start the removal: {error}); delete it once ling has exited", bin.display())),
        }
        finish(&left, bin)
    }

    /// Runs `command` with Administrator rights through [`elevation_script`].
    fn run_elevated(command: &str) -> Result<(), String> {
        let status = std::process::Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-EncodedCommand", &encoded(&elevation_script(command))])
            .status()
            .map_err(|error| format!("could not start PowerShell ({error})"))?;
        match status.code() {
            Some(ELEVATED_DONE) => Ok(()),
            Some(NO_DESKTOP) => Err("this session (SSH, or a service) has no desktop to show the Administrator prompt on".to_string()),
            Some(DECLINED) => Err("the Administrator prompt was declined".to_string()),
            _ => Err("removing them failed".to_string()),
        }
    }

    /// The user's environment in the registry, read and written as stored.
    mod registry {
        use std::path::Path;

        use windows_sys::Win32::Foundation::ERROR_FILE_NOT_FOUND;
        use windows_sys::Win32::Foundation::ERROR_SUCCESS;
        use windows_sys::Win32::System::Registry::HKEY;
        use windows_sys::Win32::System::Registry::HKEY_CURRENT_USER;
        use windows_sys::Win32::System::Registry::KEY_QUERY_VALUE;
        use windows_sys::Win32::System::Registry::KEY_SET_VALUE;
        use windows_sys::Win32::System::Registry::REG_EXPAND_SZ;
        use windows_sys::Win32::System::Registry::REG_SZ;
        use windows_sys::Win32::System::Registry::RegCloseKey;
        use windows_sys::Win32::System::Registry::RegOpenKeyExW;
        use windows_sys::Win32::System::Registry::RegQueryValueExW;
        use windows_sys::Win32::System::Registry::RegSetValueExW;
        use windows_sys::Win32::UI::WindowsAndMessaging::HWND_BROADCAST;
        use windows_sys::Win32::UI::WindowsAndMessaging::SMTO_ABORTIFHUNG;
        use windows_sys::Win32::UI::WindowsAndMessaging::SendMessageTimeoutW;
        use windows_sys::Win32::UI::WindowsAndMessaging::WM_SETTINGCHANGE;

        fn wide(text: &str) -> Vec<u16> {
            text.encode_utf16().chain(Some(0)).collect()
        }

        /// An open key of HKEY_CURRENT_USER, closed when dropped.
        struct Key(HKEY);

        impl Key {
            fn open(path: &str, access: u32) -> Result<Key, u32> {
                let path = wide(path);
                let mut key: HKEY = 0;
                // SAFETY: `path` is NUL-terminated and outlives the call; `key` is a valid out pointer.
                let status = unsafe { RegOpenKeyExW(HKEY_CURRENT_USER, path.as_ptr(), 0, access, &mut key) };
                if status == ERROR_SUCCESS { Ok(Key(key)) } else { Err(status) }
            }

            /// A string value and its kind (`REG_SZ`, `REG_EXPAND_SZ`, ...), exactly as stored.
            fn query(&self, name: &str) -> Result<Option<(String, u32)>, String> {
                let name = wide(name);
                let mut kind = 0u32;
                let mut size = 0u32;
                // SAFETY: a size query: no data pointer, `kind` and `size` valid out pointers.
                let status = unsafe { RegQueryValueExW(self.0, name.as_ptr(), std::ptr::null(), &mut kind, std::ptr::null_mut(), &mut size) };
                if status == ERROR_FILE_NOT_FOUND {
                    return Ok(None);
                }
                if status != ERROR_SUCCESS {
                    return Err(format!("could not read it (error {status})"));
                }
                let mut buffer = vec![0u16; size as usize / 2 + 1];
                let mut bytes = (buffer.len() * 2) as u32;
                // SAFETY: `buffer` holds `bytes` bytes; the other pointers are valid.
                let status = unsafe {
                    RegQueryValueExW(self.0, name.as_ptr(), std::ptr::null(), &mut kind, buffer.as_mut_ptr().cast::<u8>(), &mut bytes)
                };
                if status != ERROR_SUCCESS {
                    return Err(format!("could not read it (error {status})"));
                }
                let mut length = (bytes as usize / 2).min(buffer.len());
                while length > 0 && buffer[length - 1] == 0 {
                    length -= 1;
                }
                let text = String::from_utf16(&buffer[..length]).map_err(|_| "it is not valid text".to_string())?;
                Ok(Some((text, kind)))
            }

            fn set(&self, name: &str, text: &str, kind: u32) -> Result<(), String> {
                let name = wide(name);
                let data = wide(text);
                // SAFETY: `data` holds `data.len() * 2` bytes, NUL included, as the API expects.
                let status = unsafe { RegSetValueExW(self.0, name.as_ptr(), 0, kind, data.as_ptr().cast::<u8>(), (data.len() * 2) as u32) };
                if status == ERROR_SUCCESS { Ok(()) } else { Err(format!("could not write it (error {status})")) }
            }
        }

        impl Drop for Key {
            fn drop(&mut self) {
                // SAFETY: the key was opened by `Key::open` and is closed once.
                unsafe { RegCloseKey(self.0) };
            }
        }

        /// A string value of a key under HKEY_CURRENT_USER.
        pub fn read_string(path: &str, name: &str) -> Option<(String, u32)> {
            Key::open(path, KEY_QUERY_VALUE).ok()?.query(name).ok().flatten()
        }

        /// Takes `dir` out of `HKCU\Environment\Path`, keeping the value's kind and every other
        /// entry as written (`%USERPROFILE%\…` unexpanded), and tells running programs, as install.ps1's
        /// write did. `Ok(false)` when it was not there.
        pub fn remove_from_user_path(dir: &Path) -> Result<bool, String> {
            let key = Key::open("Environment", KEY_QUERY_VALUE | KEY_SET_VALUE).map_err(|status| format!("could not open it (error {status})"))?;
            let Some((value, kind)) = key.query("Path")? else {
                return Ok(false);
            };
            if kind != REG_SZ && kind != REG_EXPAND_SZ {
                return Err(format!("it is of kind {kind}, not a string; left as it is"));
            }
            let Some(updated) = super::super::path_without(&value, &dir.to_string_lossy(), |name| std::env::var(name).ok()) else {
                return Ok(false);
            };
            key.set("Path", &updated, kind)?;
            let environment = wide("Environment");
            let mut result = 0usize;
            // SAFETY: `environment` is NUL-terminated and outlives the call; `result` is valid.
            unsafe {
                SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, environment.as_ptr() as isize, SMTO_ABORTIFHUNG, 5000, &mut result);
            }
            Ok(true)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The same pairs as tests/test_install_ps1.py, computed there in Python: the scheme is one.
    #[test]
    fn rule_names_follow_the_scheme_install_ps1_uses() {
        assert_eq!(rule_name(r"C:\Users\Émile Test\AppData\Local\Programs\Mightling\bin\ling.exe"), "Mightling-mDNS-ed821dee");
        assert_eq!(rule_name(r"C:\Users\Jane\AppData\Local\Programs\Mightling\App\Mightling.exe"), "Mightling-mDNS-557757bc");
        assert_eq!(rule_name(r"C:\USERS\JANE\APPDATA\LOCAL\PROGRAMS\MIGHTLING\APP\MIGHTLING.EXE"), "Mightling-mDNS-557757bc");
    }

    #[test]
    fn arguments_are_purge_and_yes_only() {
        assert_eq!(parse(&[]), Some(Options::default()));
        assert_eq!(parse(&["--purge".into(), "-y".into()]), Some(Options { purge: true, yes: true }));
        assert_eq!(parse(&["--yes".into()]), Some(Options { purge: false, yes: true }));
        assert_eq!(parse(&["now".into()]), None);
    }

    const BIN: &str = r"C:\Users\Jane\AppData\Local\Programs\Mightling\bin";

    fn lookup(name: &str) -> Option<String> {
        match name {
            "USERPROFILE" => Some(r"C:\Users\Jane".to_string()),
            "LOCALAPPDATA" => Some(r"C:\Users\Jane\AppData\Local".to_string()),
            _ => None,
        }
    }

    /// Row 2 of §23.3: the value install.ps1 leaves, `REG_EXPAND_SZ` with the first entry unexpanded.
    #[test]
    fn only_the_install_folder_leaves_the_path_and_the_rest_stays_as_written() {
        let value = format!(r"%USERPROFILE%\AppData\Local\Microsoft\WindowsApps;{BIN}");
        assert_eq!(path_without(&value, BIN, lookup).as_deref(), Some(r"%USERPROFILE%\AppData\Local\Microsoft\WindowsApps"));
        let value = format!(r"C:\Tools;{BIN};%UNKNOWN%\x;;D:\bin");
        assert_eq!(path_without(&value, BIN, lookup).as_deref(), Some(r"C:\Tools;%UNKNOWN%\x;;D:\bin"));
    }

    #[test]
    fn the_entry_is_found_however_it_is_written() {
        for entry in [
            BIN.to_string(),
            format!("{BIN}\\"),
            BIN.to_uppercase(),
            format!("\"{BIN}\""),
            r"%LOCALAPPDATA%\Programs\Mightling\bin".to_string(),
            BIN.replace('\\', "/"),
        ] {
            let value = format!(r"C:\Tools;{entry}");
            assert_eq!(path_without(&value, BIN, lookup).as_deref(), Some(r"C:\Tools"), "{entry}");
        }
        // Twice (an older install wrote it again): both go.
        assert_eq!(path_without(&format!("{BIN};C:\\Tools;{BIN}"), BIN, lookup).as_deref(), Some(r"C:\Tools"));
        // The only entry: the value is left empty, not removed.
        assert_eq!(path_without(BIN, BIN, lookup).as_deref(), Some(""));
    }

    #[test]
    fn a_path_without_the_entry_is_not_rewritten() {
        assert_eq!(path_without(r"C:\Tools;C:\Users\Jane\AppData\Local\Programs\Mightling\bin2", BIN, lookup), None);
        assert_eq!(path_without("", BIN, lookup), None);
        assert_eq!(path_without(r"C:\Tools", "", lookup), None);
    }

    #[test]
    fn variables_expand_as_windows_does() {
        assert_eq!(expand_vars(r"%USERPROFILE%\x", lookup), r"C:\Users\Jane\x");
        assert_eq!(expand_vars(r"%NOPE%\x;100%", lookup), r"%NOPE%\x;100%");
        assert_eq!(expand_vars("%%LOCALAPPDATA%", lookup), r"%C:\Users\Jane\AppData\Local");
    }

    #[test]
    fn only_the_releases_files_and_their_leftovers_are_ours() {
        let files = release_files(".exe");
        for name in [
            "ling.exe",
            "codex-code-mode-host.exe",
            "ling-search.exe",
            "ling-fetch.exe",
            "ling-code.exe",
            "codex-windows-sandbox-setup.exe",
            "codex-command-runner.exe",
            "ling.exe.old",
            "ling.exe.2.old",
            ".ling.exe.update",
        ] {
            assert!(is_release_file(name, &files), "{name}");
        }
        for name in ["notes.txt", "ling.exe.bak", "ling.exe.x.old", "ling.exe..old", "my-ling.exe", "git.exe"] {
            assert!(!is_release_file(name, &files), "{name}");
        }
        let files = release_files("");
        for name in ["ling", "ling-docs", "ling-signal", ".ling.new", ".ling-search.update"] {
            assert!(is_release_file(name, &files), "{name}");
        }
        assert!(!is_release_file("VERSION", &files));
    }

    #[test]
    fn the_firewall_command_matches_programs_in_these_folders_only() {
        let command = firewall_command(&[PathBuf::from(BIN), PathBuf::from(r"C:\Users\O'Brien\App\")]);
        assert!(command.starts_with(r"$Mine = @('C:\Users\Jane\AppData\Local\Programs\Mightling\bin\', 'C:\Users\O''Brien\App\');"), "{command}");
        assert!(command.contains("$_.Name -like 'Mightling-mDNS-*'"));
        assert!(command.contains("$Program.StartsWith($_, 'OrdinalIgnoreCase')"));
        assert!(command.ends_with("Remove-NetFirewallRule -ErrorAction Stop"));
        assert!(!command.contains('\n'), "one line, to paste");
    }

    #[test]
    fn scripts_travel_as_utf16_base64() {
        assert_eq!(encoded("a"), "YQA=");
        assert_eq!(encoded("é"), "6QA=");
    }

    #[test]
    fn the_elevation_never_asks_where_no_prompt_can_appear() {
        let script = elevation_script("Remove-Thing");
        let admin = script.find("IsInRole").unwrap();
        let desktop = script.find("[Environment]::UserInteractive").unwrap();
        let prompt = script.find("-Verb RunAs").unwrap();
        assert!(admin < desktop && desktop < prompt);
        assert!(script.contains(&format!("exit {NO_DESKTOP}")) && script.contains(&format!("exit {DECLINED}")));
        let inner = format!("$ErrorActionPreference = 'Stop'; try {{ Remove-Thing; exit {ELEVATED_DONE} }} catch {{ exit {ELEVATED_FAILED} }}");
        assert!(script.contains(&encoded(&inner)));
    }

    #[test]
    fn the_cleanup_waits_for_this_process_and_removes_only_what_it_is_given() {
        let script = cleanup_script(
            4242,
            Path::new(BIN),
            &["ling.exe".to_string(), "ling.exe.old".to_string()],
            Some(Path::new(r"C:\Users\Jane\AppData\Local\Programs\Mightling")),
            Some(Path::new(r"C:\Users\Jane\.mightling")),
            Path::new(r"C:\Temp\left.txt"),
        );
        assert!(script.starts_with("Wait-Process -Id 4242 "));
        assert!(script.contains(r"$Dir = 'C:\Users\Jane\AppData\Local\Programs\Mightling\bin'"));
        assert!(script.contains("Join-Path $Dir 'ling.exe';") && script.contains("Join-Path $Dir 'ling.exe.old';"));
        assert!(script.contains(r"$Parent = 'C:\Users\Jane\AppData\Local\Programs\Mightling'; if ((Test-Path -LiteralPath $Parent) -and -not (Get-ChildItem"));
        assert!(script.contains(r"$Home2 = 'C:\Users\Jane\.mightling'; if (Test-Path -LiteralPath $Home2) { Remove-Item -LiteralPath $Home2 -Recurse"));
        assert_eq!(script.matches("-Recurse").count(), 1, "only the purge recurses");
        let kept = cleanup_script(1, Path::new(BIN), &[], None, None, Path::new("left.txt"));
        assert!(!kept.contains("$Home2") && !kept.contains("$Parent"));
    }

    #[test]
    fn verbatim_paths_are_written_as_install_ps1_wrote_them() {
        assert_eq!(strip_verbatim(Path::new(r"\\?\C:\Users\Jane\ling.exe")), PathBuf::from(r"C:\Users\Jane\ling.exe"));
        assert_eq!(strip_verbatim(Path::new(r"\\?\UNC\server\share\ling.exe")), PathBuf::from(r"\\?\UNC\server\share\ling.exe"));
        assert_eq!(strip_verbatim(Path::new("/usr/bin/ling")), PathBuf::from("/usr/bin/ling"));
    }

    #[test]
    fn source_builds_odd_folders_and_nodes_are_refused() {
        let bin = Path::new("/home/jane/.local/share/dreamference/mightling/bin");
        assert!(refusal(None, bin, false, true).unwrap().contains("built from source"));
        assert!(refusal(Some(""), bin, false, true).unwrap().contains("built from source"));
        assert!(refusal(Some("1.7.2"), Path::new("/home/jane/tools"), false, true).unwrap().contains("not an installer's bin folder"));
        assert!(refusal(Some("1.7.2"), bin, true, true).unwrap().contains("node"));
        assert_eq!(refusal(Some("1.7.2"), bin, false, true), None);
        // On Windows no build from source installs into install.ps1's folder (CI's install.ps1
        // step installs a source build, and uninstalls it).
        assert_eq!(refusal(None, bin, false, false), None);
    }

    #[test]
    fn the_apps_record_goes_only_with_the_app() {
        let root = std::env::temp_dir().join(format!("ling-uninstall-record-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bin = root.join("mightling").join("bin");
        let app = root.join("App");
        std::fs::create_dir_all(&bin).unwrap();
        assert_eq!(app_record(&bin), None, "no record: the app, if any, was installed by hand");
        let record = root.join("mightling").join(crate::update::APP_RECORD);
        std::fs::write(&record, format!("\u{feff}path={}\r\nversion=v1.7.2\r\n", app.display())).unwrap();
        assert_eq!(app_record(&bin), Some((record.clone(), None)), "the app is gone: the record goes");
        std::fs::create_dir_all(&app).unwrap();
        assert_eq!(app_record(&bin), Some((record, Some(app))), "the app is there: both stay");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[cfg(unix)]
    #[test]
    fn the_install_folder_is_where_the_link_leads() {
        let root = std::env::temp_dir().join(format!("ling-uninstall-exe-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bin = root.join("share/mightling/bin");
        let links = root.join("local/bin");
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::create_dir_all(&links).unwrap();
        std::fs::write(bin.join("ling"), b"").unwrap();
        std::os::unix::fs::symlink(bin.join("ling"), links.join("ling")).unwrap();
        let exe = resolved_exe(&links.join("ling"));
        assert_eq!(exe.parent().unwrap(), bin.canonicalize().unwrap());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[cfg(unix)]
    #[test]
    fn only_links_into_the_install_folder_are_ours() {
        let root = std::env::temp_dir().join(format!("ling-uninstall-links-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bin = root.join("share/mightling/bin");
        let links = root.join("local-bin");
        let elsewhere = root.join("elsewhere");
        for dir in [&bin, &links, &elsewhere] {
            std::fs::create_dir_all(dir).unwrap();
        }
        for name in ["ling", "ling-search", "notes"] {
            std::fs::write(bin.join(name), b"").unwrap();
        }
        std::fs::write(elsewhere.join("ling-fetch"), b"").unwrap();
        std::os::unix::fs::symlink(bin.join("ling"), links.join("ling")).unwrap();
        std::os::unix::fs::symlink("../share/mightling/bin/ling-search", links.join("ling-search")).unwrap();
        std::os::unix::fs::symlink(elsewhere.join("ling-fetch"), links.join("ling-fetch")).unwrap();
        std::fs::write(links.join("ling-code"), b"a real file").unwrap();
        std::os::unix::fs::symlink(bin.join("ling-docs"), links.join("ling-docs")).unwrap(); // dangling, still ours
        std::os::unix::fs::symlink(bin.join("notes"), links.join("notes")).unwrap(); // not a release name
        let files = release_files("");
        let mut found = links_into(&links, &bin, &files);
        found.sort();
        let names: Vec<_> = found.iter().map(|path| path.file_name().unwrap().to_string_lossy().into_owned()).collect();
        assert_eq!(names, ["ling", "ling-docs", "ling-search"]);
        assert_eq!(release_files_in(&bin, &files), ["ling", "ling-search"]);
        let _ = std::fs::remove_dir_all(&root);
    }
}
