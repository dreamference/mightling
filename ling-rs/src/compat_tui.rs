//! The compatibility mode's terminal interface: which renderer a session starts in, and the key
//! bindings, as `-c tui.*` overrides on the command line of that session only
//! (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §6.5.4, §16.2).
//!
//! Nothing here is written into `$CODEX_HOME/config.toml`, so a plain `ling` start is unchanged,
//! and everything is skipped outside [`crate::compat::Mode::Claude`].
//!
//! **The renderer** follows Claude Code's own switches in the order its documentation gives,
//! with two of Mightling's settings between them. The first that applies decides:
//!
//! 1. `--no-alt-screen` on the command line: Codex decides, inline.
//! 2. `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1` or `CLAUDE_CODE_NO_FLICKER=0`: classic.
//! 3. `CLAUDE_CODE_NO_FLICKER=1`: fullscreen.
//! 4. `tui.alternate_screen` or `tui.fullscreen_transcript` in `$CODEX_HOME/config.toml` (Codex's
//!    `/tui` picker writes the second): Codex decides, as for any session.
//! 5. `tui` in Claude Code's settings files (`"fullscreen"` or `"default"`).
//! 6. Classic, by the owner's decision D5.
//!
//! Classic is Codex's inline viewport (`tui.alternate_screen = "never"`): the conversation goes to
//! the terminal's own scrollback. Fullscreen is the alternate screen with the transcript owned by
//! the session (`alternate_screen = "auto"`, `fullscreen_transcript = true`).
//!
//! **The key map** binds `Ctrl+O` to the transcript, as Claude Code does, and takes it off Codex's
//! copy action. The answer is copied with `/copy`, as in Claude Code. The pager gains `g`, `G` and
//! `b`. Most of Claude Code's pager keys are Codex's defaults already. Phase 0 found that `esc`
//! and `q` cannot close the transcript (§16.2). `Shift+Tab` is a fixed binding of Codex's and
//! needs a hook (§16.1). An action the user bound in `[tui.keymap]` is left alone, and so is any
//! binding of ours that uses a key the user gave another action: the user's own map wins.

use std::collections::BTreeSet;
use std::ffi::OsString;
use std::path::Path;

/// How a session in the mode draws.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Renderer {
    /// The normal screen: finished output scrolls into the terminal's scrollback.
    Classic,
    /// The alternate screen, the conversation scrolled inside the session.
    Fullscreen,
}

/// Claude Code's variables that choose the renderer, as its documentation names them.
pub const DISABLE_ALTERNATE_SCREEN_ENV: &str = "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN";
pub const NO_FLICKER_ENV: &str = "CLAUDE_CODE_NO_FLICKER";

/// One binding the mode writes: the context and action of Codex's `[tui.keymap]`, and its keys.
pub struct Binding {
    pub context: &'static str,
    pub action: &'static str,
    pub keys: &'static [&'static str],
}

/// The mode's key map. Each list is written whole (an override replaces Codex's list), so it
/// carries Codex's defaults beside the additions.
pub const KEYMAP: &[Binding] = &[
    Binding { context: "global", action: "open_transcript", keys: &["ctrl-o"] },
    Binding { context: "global", action: "copy", keys: &[] },
    Binding { context: "pager", action: "close_transcript", keys: &["ctrl-t", "ctrl-o"] },
    Binding { context: "pager", action: "jump_top", keys: &["home", "g"] },
    Binding { context: "pager", action: "jump_bottom", keys: &["end", "shift-g"] },
    Binding { context: "pager", action: "page_up", keys: &["page-up", "shift-space", "ctrl-b", "b"] },
];

fn truthy(value: &str) -> bool {
    matches!(value.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on")
}

fn falsy(value: &str) -> bool {
    matches!(value.trim().to_ascii_lowercase().as_str(), "0" | "false" | "no" | "off")
}

/// What decides the renderer, gathered so the choice can be tested as a value.
#[derive(Clone, Debug, Default)]
pub struct RendererInputs {
    pub no_alt_screen_flag: bool,
    pub disable_alternate_screen: Option<String>,
    pub no_flicker: Option<String>,
    /// Whether `$CODEX_HOME/config.toml` sets `tui.alternate_screen` or `tui.fullscreen_transcript`.
    pub codex_config_sets_screen: bool,
    /// The `tui` key of Claude Code's settings files, the highest-precedence file that has it.
    pub claude_tui: Option<String>,
}

/// The renderer to force, or None when Codex's own settings decide.
pub fn choose_renderer(inputs: &RendererInputs) -> Option<Renderer> {
    if inputs.no_alt_screen_flag {
        return None;
    }
    if inputs.disable_alternate_screen.as_deref().is_some_and(truthy) || inputs.no_flicker.as_deref().is_some_and(falsy) {
        return Some(Renderer::Classic);
    }
    if inputs.no_flicker.as_deref().is_some_and(truthy) {
        return Some(Renderer::Fullscreen);
    }
    if inputs.codex_config_sets_screen {
        return None;
    }
    match inputs.claude_tui.as_deref().map(str::trim) {
        Some("fullscreen") => Some(Renderer::Fullscreen),
        _ => Some(Renderer::Classic),
    }
}

/// The `-c` settings for a renderer.
pub fn renderer_settings(renderer: Renderer) -> Vec<String> {
    match renderer {
        Renderer::Classic => vec!["tui.alternate_screen=\"never\"".to_string()],
        Renderer::Fullscreen => {
            vec!["tui.alternate_screen=\"auto\"".to_string(), "tui.fullscreen_transcript=true".to_string()]
        }
    }
}

/// The user's `[tui.keymap]`: the actions bound (`context.action`) and every key they use.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct UserKeymap {
    pub actions: BTreeSet<String>,
    pub keys: BTreeSet<String>,
}

/// Reads the user's key map from a `config.toml`'s text.
pub fn user_keymap(config_text: &str) -> UserKeymap {
    let mut found = UserKeymap::default();
    let Ok(table) = config_text.parse::<toml::Table>() else { return found };
    let Some(keymap) = table.get("tui").and_then(|tui| tui.get("keymap")).and_then(toml::Value::as_table) else {
        return found;
    };
    for (context, actions) in keymap {
        let Some(actions) = actions.as_table() else { continue };
        for (action, keys) in actions {
            found.actions.insert(format!("{context}.{action}"));
            let keys: Vec<&str> = match keys {
                toml::Value::String(key) => vec![key.as_str()],
                toml::Value::Array(list) => list.iter().filter_map(toml::Value::as_str).collect(),
                _ => vec![],
            };
            found.keys.extend(keys.into_iter().map(|key| key.trim().to_ascii_lowercase()));
        }
    }
    found
}

/// The `-c` settings for the mode's key map, without the bindings the user's own map decides.
pub fn keymap_settings(user: &UserKeymap) -> Vec<String> {
    KEYMAP
        .iter()
        .filter(|binding| !user.actions.contains(&format!("{}.{}", binding.context, binding.action)))
        .filter(|binding| !binding.keys.iter().any(|key| user.keys.contains(*key)))
        .map(|binding| {
            let keys: Vec<String> = binding.keys.iter().map(|key| format!("\"{key}\"")).collect();
            format!("tui.keymap.{}.{}=[{}]", binding.context, binding.action, keys.join(", "))
        })
        .collect()
}

/// Whether a `config.toml`'s text chooses the screen itself.
pub fn config_sets_screen(config_text: &str) -> bool {
    let Ok(table) = config_text.parse::<toml::Table>() else { return false };
    table
        .get("tui")
        .and_then(toml::Value::as_table)
        .is_some_and(|tui| tui.contains_key("alternate_screen") || tui.contains_key("fullscreen_transcript"))
}

/// The `tui` key of Claude Code's settings files, in their documented precedence (the project's
/// local file, then the project's shared file, then the user's), the first one that sets it.
/// Read only, never written.
pub fn claude_tui_setting(project: &Path, home: Option<&Path>) -> Option<String> {
    let mut files = vec![project.join(".claude").join("settings.local.json"), project.join(".claude").join("settings.json")];
    if let Some(home) = home {
        files.push(home.join(".claude").join("settings.json"));
    }
    files.iter().find_map(|path| {
        let text = std::fs::read_to_string(path).ok()?;
        let value: serde_json::Value = serde_json::from_str(&text).ok()?;
        value.get("tui")?.as_str().map(str::to_string)
    })
}

/// The project a session works in: the git top level of its folder, or the folder itself.
pub fn project_root(dir: &Path) -> std::path::PathBuf {
    let mut current = Some(dir);
    while let Some(folder) = current {
        if folder.join(".git").exists() {
            return folder.to_path_buf();
        }
        current = folder.parent();
    }
    dir.to_path_buf()
}

/// Whether a session gets the interface settings: a TUI session (the interactive one, `resume`,
/// `fork`) in the mode. `exec`, the app server and a plain `ling` get none.
pub fn applies(mode: crate::compat::Mode, tui: bool) -> bool {
    tui && mode == crate::compat::Mode::Claude
}

/// Every `-c` setting the mode adds to a TUI session.
pub fn settings(inputs: &RendererInputs, user: &UserKeymap) -> Vec<String> {
    let mut settings = choose_renderer(inputs).map(renderer_settings).unwrap_or_default();
    settings.extend(keymap_settings(user));
    settings
}

/// Puts the settings right after the program name, so a `-c` the user typed, later on the line,
/// still wins.
pub fn with_settings(args: Vec<OsString>, settings: &[String]) -> Vec<OsString> {
    if settings.is_empty() {
        return args;
    }
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    for setting in settings {
        out.extend(["-c".into(), OsString::from(setting)]);
    }
    out.extend(args);
    out
}

/// The interface settings for this process's TUI session in the mode: the variables, the user's
/// `config.toml` in `codex_home`, Claude Code's settings files for the session's folder.
pub fn prepare(args: Vec<OsString>, user_args: &[String], codex_home: &Path, session_dir: &Path) -> Vec<OsString> {
    let config_text = std::fs::read_to_string(codex_home.join("config.toml")).unwrap_or_default();
    let home = ling_node_locator::home_dir();
    let inputs = RendererInputs {
        no_alt_screen_flag: user_args.iter().take_while(|arg| *arg != "--").any(|arg| arg == "--no-alt-screen"),
        disable_alternate_screen: std::env::var(DISABLE_ALTERNATE_SCREEN_ENV).ok(),
        no_flicker: std::env::var(NO_FLICKER_ENV).ok(),
        codex_config_sets_screen: config_sets_screen(&config_text),
        claude_tui: claude_tui_setting(&project_root(session_dir), home.as_deref()),
    };
    with_settings(args, &settings(&inputs, &user_keymap(&config_text)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inputs() -> RendererInputs {
        RendererInputs::default()
    }

    #[test]
    fn the_mode_starts_classic_and_follows_claude_codes_switches_in_their_order() {
        assert_eq!(choose_renderer(&inputs()), Some(Renderer::Classic), "D5");
        let fullscreen = RendererInputs { no_flicker: Some("1".into()), ..inputs() };
        assert_eq!(choose_renderer(&fullscreen), Some(Renderer::Fullscreen));
        // Disabling the alternate screen beats asking for no flicker.
        let both = RendererInputs { disable_alternate_screen: Some("1".into()), ..fullscreen.clone() };
        assert_eq!(choose_renderer(&both), Some(Renderer::Classic));
        let zero = RendererInputs { no_flicker: Some("0".into()), claude_tui: Some("fullscreen".into()), ..inputs() };
        assert_eq!(choose_renderer(&zero), Some(Renderer::Classic));
        // The settings files' `tui` key, below the variables.
        let file = RendererInputs { claude_tui: Some("fullscreen".into()), ..inputs() };
        assert_eq!(choose_renderer(&file), Some(Renderer::Fullscreen));
        let file = RendererInputs { claude_tui: Some("default".into()), ..inputs() };
        assert_eq!(choose_renderer(&file), Some(Renderer::Classic));
        // Mightling's own choice (`/tui`, config.toml) beats the settings file, not the variables.
        let picked = RendererInputs { codex_config_sets_screen: true, claude_tui: Some("fullscreen".into()), ..inputs() };
        assert_eq!(choose_renderer(&picked), None);
        let picked = RendererInputs { codex_config_sets_screen: true, no_flicker: Some("1".into()), ..inputs() };
        assert_eq!(choose_renderer(&picked), Some(Renderer::Fullscreen));
        // `--no-alt-screen` wins over everything, as in Codex.
        let flag = RendererInputs { no_alt_screen_flag: true, no_flicker: Some("1".into()), ..inputs() };
        assert_eq!(choose_renderer(&flag), None);
    }

    #[test]
    fn the_settings_are_what_codex_reads() {
        // Every key the mode writes deserializes into the pinned Codex's own types: a renamed
        // keymap action or context fails here (each keymap table denies unknown fields).
        let user = UserKeymap::default();
        for renderer in [Renderer::Classic, Renderer::Fullscreen] {
            let mut lines: Vec<String> = renderer_settings(renderer);
            lines.extend(keymap_settings(&user));
            let text = lines.iter().map(|line| line.strip_prefix("tui.").unwrap().to_string()).collect::<Vec<_>>().join("\n");
            let tui: codex_config::types::Tui = toml::from_str(&text).unwrap_or_else(|err| panic!("{text}\n{err}"));
            match renderer {
                Renderer::Classic => assert_eq!(tui.alternate_screen, codex_config::types::AltScreenMode::Never),
                Renderer::Fullscreen => {
                    assert_eq!(tui.alternate_screen, codex_config::types::AltScreenMode::Auto);
                    assert!(tui.fullscreen_transcript);
                }
            }
            let keymap = toml::to_string(&tui.keymap).unwrap();
            assert!(keymap.contains("ctrl-o") && keymap.contains("shift-g"), "{keymap}");
        }
        // The TOML of a dotted `-c` key, the way Codex applies it, reaches the same table.
        let joined = keymap_settings(&user).join("\n");
        let parsed: toml::Table = toml::from_str(&joined).unwrap();
        assert!(parsed["tui"]["keymap"]["global"]["copy"].as_array().unwrap().is_empty());
    }

    #[test]
    fn the_users_own_key_map_wins() {
        let user = user_keymap("[tui.keymap.global]\nopen_transcript = \"ctrl-y\"\n[tui.keymap.pager]\nfind = [\"ctrl-g\", \"G\"]\n");
        let settings = keymap_settings(&user);
        assert!(!settings.iter().any(|s| s.contains("global.open_transcript")), "{settings:?}");
        // Codex lower-cases a key as it reads it, so the user's `G` is `g`: `jump_top`, which would
        // use it too, is left out, and `jump_bottom` (`shift-g`) stays.
        assert!(!settings.iter().any(|s| s.contains("pager.jump_top")), "{settings:?}");
        assert!(settings.iter().any(|s| s.contains("pager.jump_bottom")));
        let user = user_keymap("[tui.keymap.global]\ncopy = \"ctrl-o\"\n");
        let settings = keymap_settings(&user);
        // The user's copy keeps ctrl-o, so nothing of ours that uses ctrl-o is written.
        assert!(settings.iter().all(|s| !s.contains("ctrl-o")), "{settings:?}");
        assert!(settings.iter().any(|s| s.contains("pager.jump_top")));
        assert_eq!(user_keymap("not toml ["), UserKeymap::default());
    }

    #[test]
    fn mightlings_own_screen_setting_is_seen() {
        assert!(config_sets_screen("[tui]\nfullscreen_transcript = false\n"));
        assert!(config_sets_screen("[tui]\nalternate_screen = \"always\"\n"));
        assert!(!config_sets_screen("[tui]\ntheme = \"x\"\n"));
        assert!(!config_sets_screen("model = \"x\"\n"));
    }

    #[test]
    fn only_a_tui_session_in_the_mode_gets_them() {
        use crate::compat::Mode;
        assert!(applies(Mode::Claude, true));
        assert!(!applies(Mode::Claude, false), "exec and the app server");
        assert!(!applies(Mode::Off, true), "a plain ling start is unchanged");
    }

    #[test]
    fn the_settings_go_first_so_the_users_dash_c_wins() {
        let args: Vec<OsString> = ["ling", "-c", "tui.alternate_screen=\"always\"", "fix it"].iter().map(OsString::from).collect();
        let out = with_settings(args, &["tui.alternate_screen=\"never\"".to_string()]);
        let out: Vec<String> = out.iter().map(|arg| arg.to_string_lossy().into_owned()).collect();
        assert_eq!(out, ["ling", "-c", "tui.alternate_screen=\"never\"", "-c", "tui.alternate_screen=\"always\"", "fix it"]);
        assert_eq!(with_settings(vec!["ling".into()], &[]), vec![OsString::from("ling")]);
    }

    #[test]
    fn claude_codes_tui_key_is_read_from_the_settings_files_in_their_order() {
        let root = std::env::temp_dir().join(format!("mightling-compat-tui-{}", std::process::id()));
        let project = root.join("repo");
        let home = root.join("home");
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(project.join(".git")).unwrap();
        std::fs::create_dir_all(project.join(".claude")).unwrap();
        std::fs::create_dir_all(project.join("sub")).unwrap();
        std::fs::create_dir_all(home.join(".claude")).unwrap();
        assert_eq!(claude_tui_setting(&project, Some(&home)), None);
        std::fs::write(home.join(".claude").join("settings.json"), r#"{"tui": "fullscreen"}"#).unwrap();
        assert_eq!(claude_tui_setting(&project, Some(&home)).as_deref(), Some("fullscreen"));
        std::fs::write(project.join(".claude").join("settings.json"), r#"{"tui": "default", "hooks": {}}"#).unwrap();
        assert_eq!(claude_tui_setting(&project, Some(&home)).as_deref(), Some("default"));
        std::fs::write(project.join(".claude").join("settings.local.json"), r#"{"tui": "fullscreen"}"#).unwrap();
        assert_eq!(claude_tui_setting(&project, Some(&home)).as_deref(), Some("fullscreen"));
        assert_eq!(project_root(&project.join("sub")), project);
        let _ = std::fs::remove_dir_all(&root);
    }
}
