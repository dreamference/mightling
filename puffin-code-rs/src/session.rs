//! `puffin-code session`: the process that owns indexing for one repository (spec §4.2).

pub fn run(_repo: crate::paths::Repo, _settings: crate::config::Settings, _parent: Option<i32>) -> anyhow::Result<()> {
    anyhow::bail!("not implemented yet")
}
