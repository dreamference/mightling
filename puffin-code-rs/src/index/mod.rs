//! Indexing: the universal layer (codebase-memory), the static exact layers (scip-python) and the
//! executing ones (rust-analyzer), each admitted against one host-wide budget and run in a
//! network-less sandbox inside `puffin-index.slice` (spec §6, §9).

pub mod store;

use anyhow::{bail, Result};

use crate::config::Settings;
use crate::paths::Repo;

pub fn request_or_run(_repo: &Repo, _settings: &Settings, _exact: bool, _include_submodules: bool, _wait: bool) -> Result<()> {
    bail!("not implemented yet")
}

pub fn supervise(_plan: &str) -> Result<()> {
    bail!("not implemented yet")
}

pub fn forget(_repo: &Repo) -> Result<()> {
    bail!("not implemented yet")
}
