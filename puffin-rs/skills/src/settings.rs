//! `$CODEX_HOME/puffin-skills.toml`: what the user decided with `puffin skill`, and what the
//! launcher has already told them (spec §3, §4, §8.6).
//!
//! ```toml
//! glossary = true                 # the tool glossary of §5, on or off
//! enabled = ["deploy"]            # offered although the preflight says otherwise
//! disabled = ["caveman-stats"]    # never offered
//!
//! [sources]
//! hermes = false                  # a linked source switched off
//!
//! [announced]
//! pdf = "9f2c…"                   # content hash last reported as added or changed
//! ```

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::io;
use std::path::Path;
use std::path::PathBuf;

pub const FILE_NAME: &str = "puffin-skills.toml";

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Settings {
    /// `None` leaves the launcher's default.
    pub glossary: Option<bool>,
    /// Skill names or paths the user wants offered whatever the preflight or a collision says.
    pub enabled: BTreeSet<String>,
    /// Skill names or paths the user wants withheld.
    pub disabled: BTreeSet<String>,
    /// Agents whose skills folder is not linked.
    pub sources_off: BTreeSet<String>,
    /// Skill name to the content hash that was announced as added or changed outside
    /// `puffin skill add`.
    pub announced: BTreeMap<String, String>,
}

impl Settings {
    pub fn path(codex_home: &Path) -> PathBuf {
        codex_home.join(FILE_NAME)
    }

    /// Reads the file; a missing or unreadable one is the defaults.
    pub fn load(codex_home: &Path) -> Settings {
        let Ok(text) = std::fs::read_to_string(Self::path(codex_home)) else {
            return Settings::default();
        };
        Self::from_toml(&text)
    }

    pub fn from_toml(text: &str) -> Settings {
        let Ok(table) = text.parse::<toml::Table>() else { return Settings::default() };
        let list = |key: &str| -> BTreeSet<String> {
            table
                .get(key)
                .and_then(toml::Value::as_array)
                .map(|items| items.iter().filter_map(|item| item.as_str().map(str::to_string)).collect())
                .unwrap_or_default()
        };
        Settings {
            glossary: table.get("glossary").and_then(toml::Value::as_bool),
            enabled: list("enabled"),
            disabled: list("disabled"),
            sources_off: table
                .get("sources")
                .and_then(toml::Value::as_table)
                .map(|sources| {
                    sources
                        .iter()
                        .filter(|(_, on)| on.as_bool() == Some(false))
                        .map(|(agent, _)| agent.clone())
                        .collect()
                })
                .unwrap_or_default(),
            announced: table
                .get("announced")
                .and_then(toml::Value::as_table)
                .map(|announced| {
                    announced
                        .iter()
                        .filter_map(|(name, hash)| Some((name.clone(), hash.as_str()?.to_string())))
                        .collect()
                })
                .unwrap_or_default(),
        }
    }

    pub fn to_toml(&self) -> String {
        let strings = |set: &BTreeSet<String>| toml::Value::Array(set.iter().cloned().map(toml::Value::String).collect());
        let mut table = toml::Table::new();
        if let Some(glossary) = self.glossary {
            table.insert("glossary".to_string(), toml::Value::Boolean(glossary));
        }
        if !self.enabled.is_empty() {
            table.insert("enabled".to_string(), strings(&self.enabled));
        }
        if !self.disabled.is_empty() {
            table.insert("disabled".to_string(), strings(&self.disabled));
        }
        if !self.sources_off.is_empty() {
            let sources = self.sources_off.iter().map(|agent| (agent.clone(), toml::Value::Boolean(false))).collect();
            table.insert("sources".to_string(), toml::Value::Table(sources));
        }
        if !self.announced.is_empty() {
            let announced = self
                .announced
                .iter()
                .map(|(name, hash)| (name.clone(), toml::Value::String(hash.clone())))
                .collect();
            table.insert("announced".to_string(), toml::Value::Table(announced));
        }
        toml::to_string(&table).unwrap_or_default()
    }

    /// Replaces the file whole (write, then rename), like every other file the launcher keeps.
    pub fn save(&self, codex_home: &Path) -> io::Result<()> {
        std::fs::create_dir_all(codex_home)?;
        crate::write_atomically(&Self::path(codex_home), self.to_toml().as_bytes())
    }

    /// Whether `name`, or any of `paths`, is in `set`.
    pub fn names(set: &BTreeSet<String>, name: &str, paths: &[&Path]) -> bool {
        set.contains(name) || paths.iter().any(|path| set.contains(&*path.to_string_lossy()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_round_trip_and_defaults_write_nothing() {
        assert_eq!(Settings::default().to_toml(), "");
        let settings = Settings {
            glossary: Some(false),
            enabled: ["deploy".to_string()].into(),
            disabled: ["caveman-stats".to_string(), "/x/SKILL.md".to_string()].into(),
            sources_off: ["hermes".to_string()].into(),
            announced: [("pdf".to_string(), "abc".to_string())].into(),
        };
        assert_eq!(Settings::from_toml(&settings.to_toml()), settings);
        assert_eq!(Settings::from_toml("this is = not [toml"), Settings::default());
        // `on` is the default, so only `false` is a decision.
        assert!(Settings::from_toml("[sources]\nclaude = true\n").sources_off.is_empty());
    }

    #[test]
    fn they_are_saved_and_loaded_from_codex_home() {
        let home = crate::testing::scratch("settings");
        assert_eq!(Settings::load(&home), Settings::default());
        let settings = Settings { sources_off: ["gemini".to_string()].into(), ..Settings::default() };
        assert!(settings.save(&home).is_ok());
        assert_eq!(Settings::load(&home), settings);
    }
}
