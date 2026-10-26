//! https from inside the Windows sandbox (WINDOWS_ARM §23.4 W3).
//!
//! Windows' own TLS, Schannel, refuses to acquire credentials for a restricted token:
//! `AcquireCredentialsHandle` answers `SEC_E_NO_CREDENTIALS`. Both sandboxes run commands under
//! a write-restricted token, so `curl.exe`, `Invoke-WebRequest`, `Invoke-RestMethod`, .NET's
//! `SslStream` and Git's default `schannel` backend cannot open https from inside them, while the
//! same commands work outside. Measured on a Windows 11 PC (25H2): a write-restricted copy of the
//! user's own token, with the user's profile loaded, fails whatever its restricting SIDs (the user,
//! Everyone, the logon SID, Authenticated Users, Users, INTERACTIVE, all of them together), so it
//! is neither the sandbox accounts' missing profiles nor a missing ACL entry. TLS stacks of their
//! own are not affected: `ling-search`/`ling-fetch` (rustls), Python, Node, and Git with
//! `http.sslBackend=openssl`, which Git for Windows and MinGit ship.
//!
//! What the launcher does: in a session that is not Full Access, Git is set to OpenSSL through the
//! shell environment Codex gives commands (`GIT_CONFIG_COUNT`/`KEY`/`VALUE`, after any the user
//! set), so `git clone`/`fetch`/`ls-remote` over https work; and the system prompt says which
//! tools cannot reach https, so the model uses `ling-fetch` instead of retrying `curl.exe`. Full
//! Access is left alone: there Schannel works, and it trusts the certificates in Windows' store,
//! which OpenSSL's bundle does not.

/// Added to the system prompt on Windows.
pub const PROMPT_NOTE: &str = r#"

# https on this Windows machine

Inside the sandbox, programs that use Windows' own TLS cannot open https connections: `curl.exe`,
`Invoke-WebRequest`, `Invoke-RestMethod` and other .NET or PowerShell web calls fail with
SEC_E_NO_CREDENTIALS or "the underlying connection was closed". That is the sandbox, not the
network or the site: do not retry them or try to repair it. Read a URL with `ling-fetch`, download
with Python or Node, and use `git` as usual (this session sets it to OpenSSL). Plain http works.
"#;

/// The `-c` overrides that set Git's https backend to OpenSSL in the commands' environment, as the
/// next `GIT_CONFIG_*` entry after the `existing_count` the launcher's environment already has.
pub fn git_openssl_overrides(existing_count: Option<&str>) -> Vec<String> {
    let index = existing_count.and_then(|count| count.trim().parse::<u32>().ok()).unwrap_or(0);
    let set = |key: &str, value: &str| format!("shell_environment_policy.set.{key}={}", toml::Value::String(value.to_string()));
    vec![
        set(&format!("GIT_CONFIG_KEY_{index}"), "http.sslBackend"),
        set(&format!("GIT_CONFIG_VALUE_{index}"), "openssl"),
        set("GIT_CONFIG_COUNT", &(index + 1).to_string()),
    ]
}

/// Adds the Git overrides to a Windows session that is not Full Access, after the program name so
/// the user's own `-c` still wins. Unchanged elsewhere, and when the user's arguments already set
/// Git's backend.
pub fn with_git_openssl(args: Vec<std::ffi::OsString>, user_args: &[String], full_access: bool) -> Vec<std::ffi::OsString> {
    if !cfg!(windows) || full_access || user_args.iter().any(|arg| arg.contains("GIT_CONFIG_")) {
        return args;
    }
    let overrides = git_openssl_overrides(std::env::var("GIT_CONFIG_COUNT").ok().as_deref());
    let mut args = args.into_iter();
    let mut out: Vec<std::ffi::OsString> = args.next().into_iter().collect();
    for setting in overrides {
        out.extend(["-c".into(), setting.into()]);
    }
    out.extend(args);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn git_gets_openssl_as_the_next_config_entry() {
        assert_eq!(
            git_openssl_overrides(None),
            [
                "shell_environment_policy.set.GIT_CONFIG_KEY_0=\"http.sslBackend\"",
                "shell_environment_policy.set.GIT_CONFIG_VALUE_0=\"openssl\"",
                "shell_environment_policy.set.GIT_CONFIG_COUNT=\"1\"",
            ]
        );
        let after_two = git_openssl_overrides(Some("2"));
        assert!(after_two[0].contains("GIT_CONFIG_KEY_2=") && after_two[2].ends_with("GIT_CONFIG_COUNT=\"3\""), "{after_two:?}");
        assert!(git_openssl_overrides(Some("x"))[0].contains("GIT_CONFIG_KEY_0="));
        // Each one parses as the TOML Codex reads a `-c` value with.
        for setting in git_openssl_overrides(None) {
            let (_, value) = setting.split_once('=').unwrap();
            assert!(toml::from_str::<toml::Table>(&format!("v = {value}")).is_ok(), "{setting}");
        }
    }

    #[test]
    fn the_prompt_note_names_what_fails_and_what_to_use() {
        for word in ["curl.exe", "Invoke-WebRequest", "SEC_E_NO_CREDENTIALS", "ling-fetch", "git"] {
            assert!(PROMPT_NOTE.contains(word), "{word}");
        }
    }

    #[test]
    fn full_access_and_other_platforms_are_left_alone() {
        let args: Vec<std::ffi::OsString> = ["ling", "exec", "hi"].map(Into::into).to_vec();
        assert_eq!(with_git_openssl(args.clone(), &[], true), args);
        let user: Vec<String> = vec!["-c".into(), "shell_environment_policy.set.GIT_CONFIG_COUNT=\"0\"".into()];
        assert_eq!(with_git_openssl(args.clone(), &user, false), args);
        let out = with_git_openssl(args.clone(), &[], false);
        if cfg!(windows) {
            assert_eq!(out.len(), args.len() + 6);
            assert_eq!(out[1], "-c");
        } else {
            assert_eq!(out, args);
        }
    }
}
