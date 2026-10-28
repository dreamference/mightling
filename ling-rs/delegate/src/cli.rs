//! Command-line entry point for `ling delegate` and `ling-delegate`
//! (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §4.2).

use crate::classes::{CLASSES, format_table};
use crate::lookup::{LookupRequest, SystemIndex, execute as lookup_execute};
use crate::mcp::serve_stdio;
use crate::watch::{Probe, SystemProbe, WatchRequest, execute};

const USAGE: &str = "\
Usage: ling delegate <command> [options]

Commands:
  mcp                       Run the Mightling delegation MCP stdio server (Phase P1: watch and lookup)
  lookup <op> <name> [opts] Exact answers from the code index, no model: op is def, refs, callers, impl
                            or outline (name is then a file); opts: --scope <path>, --repo <dir>, --limit <n>
  watch [options]           Wait for one condition from the closed list and print {met, observed, at}
  classes [--json]          Show delegation task classes and their Phase P1 status

Watch options:
  --kind <kind>             Condition kind: file_line, pid_exit, http_status, gh_run,
                            systemd_unit, command, night_task, git_ref, timeout
  --target <target>         Generic target (path, pid, url, run_id, unit, command, id, ref, seconds)
  --pattern <pattern>       Substring or regex pattern (file_line, command)
  --regex                   Treat --pattern as a regular expression
  --path <path>             File path for file_line
  --pid <pid>               Process ID for pid_exit
  --url <url>               Loopback http:// URL for http_status
  --status <code>           Expected HTTP status code for http_status (default: 200)
  --run <id>                GitHub Actions run ID for gh_run (alias: --run-id)
  --repo <owner/name>       Repository slug for gh_run or path for git_ref
  --unit <unit>             Systemd user unit name for systemd_unit
  --state <state>           Expected unit state for systemd_unit (default: active)
  --command <cmd>           Allowlisted command for command
  --exit-code <code>        Expected exit code for command
  --id <task-id>            Night Shift task ID for night_task
  --ref <git-ref>           Git ref name for git_ref
  --from <oid>              Baseline commit hash for git_ref
  --timeout <seconds>       Maximum wait duration in seconds (default: 1800, max: 14400)
  --interval <seconds>      Poll interval in seconds (command/gh_run/systemd_unit/git_ref >= 15s)
  --json <payload>          Full WatchRequest JSON object
";

/// Runs `ling delegate <args>` and returns the process exit code.
pub fn run(args: &[String]) -> i32 {
    let Some((sub, rest)) = args.split_first() else {
        eprint!("{USAGE}");
        return 2;
    };

    match sub.as_str() {
        "-h" | "--help" | "help" => {
            print!("{USAGE}");
            0
        }
        "mcp" => {
            if rest.iter().any(|a| a == "-h" || a == "--help") {
                print!("{USAGE}");
                return 0;
            }
            if !rest.is_empty() {
                eprintln!("unknown arguments for `ling delegate mcp`: {}", rest.join(" "));
                return 2;
            }
            serve_stdio()
        }
        "watch" => {
            let mut probe = SystemProbe::new();
            run_watch_with_probe(rest, &mut probe)
        }
        "classes" => run_classes(rest),
        "lookup" => run_lookup(rest),
        "ask" | "task" | "night" => {
            eprintln!(
                "ling delegate {sub}: disabled in Phase P1 (only the model-free classes W1 `watch` and L1 `lookup` are active)."
            );
            2
        }
        other => {
            eprintln!("unknown subcommand for `ling delegate`: {other}\n\n{USAGE}");
            2
        }
    }
}

fn run_lookup(args: &[String]) -> i32 {
    if args.iter().any(|a| a == "-h" || a == "--help") {
        print!("{USAGE}");
        return 0;
    }
    let req = match parse_lookup_args(args) {
        Ok(r) => r,
        Err(msg) => {
            eprintln!("{msg}");
            return 2;
        }
    };
    match lookup_execute(&req, &SystemIndex) {
        Ok(res) => {
            println!("{}", serde_json::to_string(&res).unwrap_or_else(|_| "{}".to_string()));
            0
        }
        Err(err) => {
            eprintln!("{err}");
            2
        }
    }
}

/// Parses `ling delegate lookup <op> <name> [--scope P] [--repo R] [--limit N]`.
pub fn parse_lookup_args(args: &[String]) -> Result<LookupRequest, String> {
    let (Some(op), Some(name)) = (args.first(), args.get(1)) else {
        return Err(format!("usage: ling delegate lookup <op> <name> [options]\n\n{USAGE}"));
    };
    let mut req = LookupRequest { op: op.clone(), name: name.clone(), ..Default::default() };
    let mut i = 2;
    while i < args.len() {
        let flag = args[i].as_str();
        i += 1;
        let value = args.get(i).cloned().ok_or_else(|| format!("missing value for {flag}"))?;
        match flag {
            "--scope" => req.scope = Some(value),
            "--repo" => req.repo = Some(value),
            "--limit" => {
                req.limit = Some(
                    value.parse::<usize>().map_err(|_| format!("invalid --limit value: {value:?}"))?,
                );
            }
            other => return Err(format!("unknown option for `ling delegate lookup`: {other}")),
        }
        i += 1;
    }
    Ok(req)
}

fn run_classes(args: &[String]) -> i32 {
    let mut as_json = false;
    for arg in args {
        match arg.as_str() {
            "--json" => as_json = true,
            "-h" | "--help" => {
                print!("{USAGE}");
                return 0;
            }
            other => {
                eprintln!("unknown option for `ling delegate classes`: {other}");
                return 2;
            }
        }
    }
    if as_json {
        println!(
            "{}",
            serde_json::to_string_pretty(CLASSES).unwrap_or_else(|_| "[]".to_string())
        );
    } else {
        print!("{}", format_table());
    }
    0
}

/// Parses `watch` CLI arguments, executes the watch against `probe`, prints the JSON result,
/// and returns exit code `0` (`met: true`), `1` (`met: false`), or `2` (validation error).
pub fn run_watch_with_probe(args: &[String], probe: &mut dyn Probe) -> i32 {
    if args.iter().any(|a| a == "-h" || a == "--help") {
        print!("{USAGE}");
        return 0;
    }
    let req = match parse_watch_args(args) {
        Ok(r) => r,
        Err(msg) => {
            eprintln!("{msg}");
            return 2;
        }
    };
    match execute(&req, probe) {
        Ok(res) => {
            println!(
                "{}",
                serde_json::to_string(&res).unwrap_or_else(|_| "{}".to_string())
            );
            if res.met { 0 } else { 1 }
        }
        Err(err) => {
            eprintln!("{err}");
            2
        }
    }
}

/// Parses CLI flags for `ling delegate watch` into a [`WatchRequest`].
pub fn parse_watch_args(args: &[String]) -> Result<WatchRequest, String> {
    if args.is_empty() {
        return Err(format!("missing arguments for `ling delegate watch`\n\n{USAGE}"));
    }

    let mut req = WatchRequest::default();
    let mut i = 0;
    while i < args.len() {
        let flag = args[i].as_str();
        let next_val = |idx: &mut usize, name: &str| -> Result<String, String> {
            *idx += 1;
            args.get(*idx)
                .cloned()
                .ok_or_else(|| format!("missing value for {name}"))
        };

        match flag {
            "--json" => {
                let raw = next_val(&mut i, "--json")?;
                req = serde_json::from_str(&raw)
                    .map_err(|e| format!("invalid --json payload: {e}"))?;
            }
            "--kind" | "-k" => req.kind = next_val(&mut i, flag)?,
            "--target" | "-t" => req.target = Some(next_val(&mut i, flag)?),
            "--pattern" | "-p" => req.pattern = Some(next_val(&mut i, flag)?),
            "--regex" => req.regex = true,
            "--path" => req.path = Some(next_val(&mut i, flag)?),
            "--pid" => {
                let v = next_val(&mut i, flag)?;
                req.pid = Some(
                    v.parse::<i32>()
                        .map_err(|_| format!("invalid --pid value: {v:?}"))?,
                );
            }
            "--url" => req.url = Some(next_val(&mut i, flag)?),
            "--status" => {
                let v = next_val(&mut i, flag)?;
                req.status = Some(
                    v.parse::<u16>()
                        .map_err(|_| format!("invalid --status value: {v:?}"))?,
                );
            }
            "--run" | "--run-id" => req.run_id = Some(next_val(&mut i, flag)?),
            "--repo" => req.repo = Some(next_val(&mut i, flag)?),
            "--unit" => req.unit = Some(next_val(&mut i, flag)?),
            "--state" => req.state = Some(next_val(&mut i, flag)?),
            "--command" | "--cmd" => req.command = Some(next_val(&mut i, flag)?),
            "--exit-code" => {
                let v = next_val(&mut i, flag)?;
                req.exit_code = Some(
                    v.parse::<i32>()
                        .map_err(|_| format!("invalid --exit-code value: {v:?}"))?,
                );
            }
            "--id" | "--task-id" => req.id = Some(next_val(&mut i, flag)?),
            "--ref" | "--git-ref" => req.git_ref = Some(next_val(&mut i, flag)?),
            "--from" => req.from = Some(next_val(&mut i, flag)?),
            "--timeout" | "--timeout-s" => {
                let v = next_val(&mut i, flag)?;
                req.timeout_s = Some(
                    v.parse::<u64>()
                        .map_err(|_| format!("invalid --timeout value: {v:?}"))?,
                );
            }
            "--interval" | "--poll-interval-s" => {
                let v = next_val(&mut i, flag)?;
                req.poll_interval_s = Some(
                    v.parse::<u64>()
                        .map_err(|_| format!("invalid --interval value: {v:?}"))?,
                );
            }
            other => {
                return Err(format!(
                    "unknown option for `ling delegate watch`: {other}"
                ));
            }
        }
        i += 1;
    }

    Ok(req)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_watch_flags_and_json_payload() {
        let args: Vec<String> = vec![
            "--kind",
            "gh_run",
            "--run",
            "123456",
            "--repo",
            "dreamference/mightling",
            "--timeout",
            "600",
        ]
        .into_iter()
        .map(String::from)
        .collect();
        let req = parse_watch_args(&args).unwrap();
        assert_eq!(req.kind, "gh_run");
        assert_eq!(req.run_id.as_deref(), Some("123456"));
        assert_eq!(req.repo.as_deref(), Some("dreamference/mightling"));
        assert_eq!(req.timeout_s, Some(600));

        let json_args: Vec<String> = vec![
            "--json".to_string(),
            r#"{"kind":"pid_exit","pid":321,"timeout_s":30}"#.to_string(),
        ];
        let req2 = parse_watch_args(&json_args).unwrap();
        assert_eq!(req2.kind, "pid_exit");
        assert_eq!(req2.pid, Some(321));
        assert_eq!(req2.timeout_s, Some(30));
    }

    #[test]
    fn parse_lookup_flags() {
        let args: Vec<String> = ["def", "Foo", "--scope", "src", "--limit", "5"]
            .into_iter()
            .map(String::from)
            .collect();
        let req = parse_lookup_args(&args).unwrap();
        assert_eq!((req.op.as_str(), req.name.as_str()), ("def", "Foo"));
        assert_eq!(req.scope.as_deref(), Some("src"));
        assert_eq!(req.limit, Some(5));
        assert!(parse_lookup_args(&["def".to_string()]).is_err());
        assert!(parse_lookup_args(&["def".into(), "x".into(), "--bogus".into(), "1".into()]).is_err());
    }

    #[test]
    fn unknown_flag_is_rejected() {
        let args: Vec<String> = vec!["--kind".to_string(), "pid_exit".to_string(), "--bogus".to_string()];
        assert!(parse_watch_args(&args).is_err());
    }
}
