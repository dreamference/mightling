//! Scratch folders and fixtures for this crate's tests. Nothing here reads the user's own
//! `~/.mightling`, `~/.claude` or `~/.agents`, and nothing reaches the network.

use std::path::Path;
use std::path::PathBuf;

use crate::catalog::Machine;
use crate::preflight::Host;

/// A fresh, empty folder for one test, with every link in its path resolved.
pub fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("ling-skills-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap_or_default();
    dir.canonicalize().unwrap_or(dir)
}

pub fn write_executable(path: &Path, contents: &str) {
    std::fs::write(path, contents).unwrap_or_default();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755));
    }
}

/// A home folder with a `ling` home in it, and a working directory beside it.
pub struct Fixture {
    pub home: PathBuf,
    pub codex_home: PathBuf,
    pub cwd: PathBuf,
}

impl Fixture {
    pub fn new(name: &str) -> Fixture {
        let root = scratch(name);
        let fixture = Fixture { home: root.join("home"), codex_home: root.join("home/.mightling"), cwd: root.join("work") };
        for dir in [&fixture.codex_home, &fixture.cwd] {
            std::fs::create_dir_all(dir).unwrap_or_default();
        }
        fixture
    }

    /// Writes a skill at `relative` (to the home folder) whose frontmatter has `name`, a
    /// description, and `extra` lines.
    pub fn skill(&self, relative: &str, name: &str, extra: &str) -> PathBuf {
        write_skill(&self.home.join(relative), name, extra)
    }

    /// Writes a skill under the working directory, which becomes a repository.
    pub fn repository_skill(&self, relative: &str, name: &str) -> PathBuf {
        std::fs::create_dir_all(self.cwd.join(".git")).unwrap_or_default();
        write_skill(&self.cwd.join(relative), name, "")
    }

    /// A Linux machine with nothing on `PATH`, no environment and no known context window.
    pub fn machine(&self) -> Machine {
        Machine {
            codex_home: self.codex_home.clone(),
            home: self.home.clone(),
            cwd: self.cwd.clone(),
            host: Host { os: "linux".to_string(), ..Host::default() },
            context_window: None,
        }
    }
}

pub fn write_skill(dir: &Path, name: &str, extra: &str) -> PathBuf {
    std::fs::create_dir_all(dir).unwrap_or_default();
    let text = format!("---\nname: {name}\ndescription: The {name} skill, for tests.\n{extra}---\n# {name}\n");
    std::fs::write(dir.join("SKILL.md"), text).unwrap_or_default();
    dir.to_path_buf()
}

/// A gzipped tarball of text files, as GitHub serves a repository.
pub fn tarball(files: &[(&str, &str, u32)]) -> Vec<u8> {
    let mut builder = tar::Builder::new(flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast()));
    for (path, contents, mode) in files {
        let mut header = tar::Header::new_gnu();
        header.set_size(contents.len() as u64);
        header.set_mode(*mode);
        header.set_cksum();
        let _ = builder.append_data(&mut header, path, contents.as_bytes());
    }
    builder.into_inner().and_then(|encoder| encoder.finish()).unwrap_or_default()
}

/// A tarball whose entry names are written as given, `..` included, which `append_data` refuses.
pub fn tarball_raw(files: &[(&str, &[u8], bool)]) -> Vec<u8> {
    let mut builder = tar::Builder::new(flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast()));
    for (path, contents, _) in files {
        let mut header = tar::Header::new_gnu();
        if let Some(old) = header.as_old_mut().name.get_mut(..path.len()) {
            old.copy_from_slice(path.as_bytes());
        }
        header.set_size(contents.len() as u64);
        header.set_mode(0o644);
        header.set_entry_type(tar::EntryType::Regular);
        header.set_cksum();
        let _ = builder.append(&header, *contents);
    }
    builder.into_inner().and_then(|encoder| encoder.finish()).unwrap_or_default()
}

/// A tarball of text files and symbolic links (`(link path, target)`).
pub fn tarball_links(files: &[(&str, &str)], links: &[(&str, &str)]) -> Vec<u8> {
    let mut builder = tar::Builder::new(flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast()));
    for (path, contents) in files {
        let mut header = tar::Header::new_gnu();
        header.set_size(contents.len() as u64);
        header.set_mode(0o644);
        header.set_cksum();
        let _ = builder.append_data(&mut header, path, contents.as_bytes());
    }
    for (path, target) in links {
        let mut header = tar::Header::new_gnu();
        header.set_entry_type(tar::EntryType::Symlink);
        header.set_size(0);
        header.set_mode(0o777);
        if let Some(old) = header.as_old_mut().name.get_mut(..path.len()) {
            old.copy_from_slice(path.as_bytes());
        }
        if let Some(old) = header.as_old_mut().linkname.get_mut(..target.len()) {
            old.copy_from_slice(target.as_bytes());
        }
        header.set_cksum();
        let _ = builder.append(&header, std::io::empty());
    }
    builder.into_inner().and_then(|encoder| encoder.finish()).unwrap_or_default()
}
