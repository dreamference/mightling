//! `puffin-code`: the command the agent runs to ask about code (spec §7.1).

use std::process::ExitCode;

use clap::{Args, Parser, Subcommand};

use puffin_code::config::Settings;
use puffin_code::output::{self, Page};
use puffin_code::paths::Repo;
use puffin_code::router::Context;

#[derive(Parser)]
#[command(name = "puffin-code", about = "Answers questions about this repository's code from an index", disable_version_flag = true)]
struct Cli {
    /// Print puffin-code's version and the tool versions it was built for.
    #[arg(long, short = 'V')]
    version: bool,
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Args, Clone, Default)]
struct PageArgs {
    /// Rows to print (default 40, at most 200).
    #[arg(long)]
    limit: Option<usize>,
    /// Skip this many rows (the next page).
    #[arg(long, default_value_t = 0)]
    offset: usize,
    /// The cursor printed with the previous page; the command fails if the index changed since.
    #[arg(long)]
    cursor: Option<String>,
    /// Keep only rows under this path prefix or matching this glob.
    #[arg(long)]
    path: Option<String>,
    /// Keep only compiler-exact rows.
    #[arg(long)]
    exact_only: bool,
    /// Keep only rows of this kind: def, read, write, import.
    #[arg(long)]
    kind: Option<String>,
    /// Print JSON instead of text.
    #[arg(long)]
    json: bool,
}

#[derive(Subcommand)]
enum Command {
    /// Where a name is defined.
    Def { name: String, #[command(flatten)] page: PageArgs },
    /// Every reference to a definition.
    Refs { name: String, #[command(flatten)] page: PageArgs },
    /// The definitions that refer to a definition.
    Callers { name: String, #[command(flatten)] page: PageArgs },
    /// What a definition's body refers to.
    Callees { name: String, #[command(flatten)] page: PageArgs },
    /// Implementations of a trait, interface or method.
    Impl { name: String, #[command(flatten)] page: PageArgs },
    /// What breaks if a definition changes: its references, then theirs, to a depth.
    Impact {
        /// The definition; omit it with --diff.
        name: Option<String>,
        /// Start from every definition a diff touches: against HEAD, or the revision or range given.
        #[arg(long, num_args = 0..=1, default_missing_value = "HEAD")]
        diff: Option<String>,
        /// Levels to follow (default 3, at most 6).
        #[arg(long)]
        depth: Option<usize>,
        #[command(flatten)]
        page: PageArgs,
    },
    /// One definition's source, 100 lines at a time (`--offset` for the next).
    Show { name: String, #[command(flatten)] page: PageArgs },
    /// The definitions of a file.
    Outline { file: String, #[command(flatten)] page: PageArgs },
    /// Definitions whose name or body matches the words.
    Search { words: Vec<String>, #[command(flatten)] page: PageArgs },
    /// Which layers exist, how fresh they are, and what is excluded.
    Status,
    /// Re-index the repository (outside the sandbox), or ask the session to (inside it).
    Index {
        /// Also run the executing exact indexers (Rust, Java, .NET) in a trusted repository.
        #[arg(long)]
        exact: bool,
        /// Removed: it lasted one run. Use `puffin-code submodules include <path>`.
        #[arg(long, hide = true)]
        include_submodules: bool,
        /// Run in the foreground and wait for the result.
        #[arg(long)]
        wait: bool,
    },
    /// Which submodules are indexed and why; `include`, `exclude` or `auto <path>` records your choice.
    Submodules {
        /// include, exclude or auto.
        verb: Option<String>,
        /// The submodule's path or name.
        path: Option<String>,
    },
    /// Delete this repository's indexes.
    Forget,
    /// Print the `# Code navigation` prompt block for this repository (used by the launcher).
    PromptBlock {
        /// The block that names the `code_*` tools, for a session the launcher gives them to.
        #[arg(long)]
        tools: bool,
    },
    /// Own indexing for one repository while a `puffin` session lives (started by the launcher).
    Session {
        #[arg(long)]
        parent_pid: Option<i32>,
    },
    /// Serve the same operations over MCP (stdio).
    Mcp,
    /// Supervise one index run (internal).
    #[command(hide = true)]
    Supervise { plan: String },
    /// Add the SymbolInformation an indexer left out, so expt-convert accepts the index (internal).
    #[command(hide = true)]
    ScipRepair { input: std::path::PathBuf, output: std::path::PathBuf },
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    if cli.version {
        println!("puffin-code {}", env!("CARGO_PKG_VERSION"));
        for (tool, version) in puffin_code::PINNED_TOOLS {
            println!("  {tool} {version}");
        }
        return ExitCode::SUCCESS;
    }
    let Some(command) = cli.command else {
        eprintln!("puffin-code: a command is required; see `puffin-code --help`");
        return ExitCode::from(2);
    };
    match run(command) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("puffin-code: {error:#}");
            ExitCode::FAILURE
        }
    }
}

fn run(command: Command) -> anyhow::Result<ExitCode> {
    if let Command::ScipRepair { input, output } = &command {
        let (fixed, added) = puffin_code::scip_store::repair_missing_symbol_information(&std::fs::read(input)?)?;
        std::fs::write(output, fixed)?;
        if added > 0 {
            eprintln!("scip-repair: added {added} missing SymbolInformation entries");
        }
        return Ok(ExitCode::SUCCESS);
    }
    let cwd = std::env::current_dir()?;
    let repo = Repo::discover(&cwd)?;
    let settings = Settings::load(&repo.root);
    match command {
        Command::PromptBlock { tools } => {
            if let Some(block) = puffin_code::prompt::block(&repo, tools) {
                println!("{block}");
            }
            Ok(ExitCode::SUCCESS)
        }
        Command::Session { parent_pid } => puffin_code::session::run(repo, settings, parent_pid).map(|_| ExitCode::SUCCESS),
        Command::Index { exact, include_submodules, wait } => {
            if include_submodules {
                anyhow::bail!("--include-submodules is gone (it lasted one run): choose per submodule with `puffin-code submodules include <path>`");
            }
            puffin_code::index::request_or_run(&repo, &settings, exact, wait)?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Submodules { verb, path } => {
            let lines = match (verb, path) {
                (None, _) => puffin_code::submodules::listing(&repo, &puffin_code::submodules::evaluate(&repo, &settings)),
                (Some(verb), Some(path)) => {
                    let lines = puffin_code::submodules::change(&repo, &settings, &verb, &path)?;
                    // A change adds or drops the submodule's files: ask for a re-index, as a
                    // query that found changed files does. Without a session, the next launch or
                    // `puffin-code index` picks it up.
                    let _ = puffin_code::requests::append(&repo.state_dir(), puffin_code::requests::Request::Index);
                    lines
                }
                (Some(verb), None) => anyhow::bail!("`puffin-code submodules {verb}` needs the submodule's path"),
            };
            println!("{}", lines.join("\n"));
            Ok(ExitCode::SUCCESS)
        }
        Command::Supervise { plan } => puffin_code::index::supervise(&plan).map(|_| ExitCode::SUCCESS),
        Command::Forget => {
            puffin_code::index::forget(&repo)?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Mcp => puffin_code::mcp::serve(repo, settings).map(|_| ExitCode::SUCCESS),
        Command::Status => {
            let context = Context::load(repo, settings)?;
            println!("{}", context.status().join("\n"));
            Ok(ExitCode::SUCCESS)
        }
        query => answer(repo, settings, query),
    }
}

fn answer(repo: Repo, settings: Settings, command: Command) -> anyhow::Result<ExitCode> {
    let limit_default = settings.row_limit;
    let mut context = Context::load(repo.clone(), settings.clone())?;
    if !context.has_index() {
        if context.repo.state_dir().join("code_index.building").exists() {
            println!("index not ready yet: it is being built; use rg until `puffin-code status` reports it ready");
        } else {
            println!("no code index for this repository yet; run `puffin-code index`, and use rg meanwhile");
        }
        return Ok(ExitCode::from(3));
    }
    // One retry if codebase-memory committed a re-index while this answer was read (§7.5).
    let (mut answer, body, page) = match compute(&context, &command) {
        Ok(result) if !context.graph_changed_since_load() => result,
        _ => {
            context = Context::load(repo, settings)?;
            compute(&context, &command)?
        }
    };
    let page_options = Page {

        limit: page.limit.unwrap_or(if body.is_some() { output::SHOW_LINES } else { limit_default }),
        offset: page.offset,
        cursor: page.cursor.clone(),
        path: page.path.as_deref().map(|path| output::repository_relative(path, &context.repo.root)),
        exact_only: page.exact_only,
        kind: page.kind.clone(),
    };
    output::narrow(&mut answer, &page_options)?;
    if page.json {
        println!("{}", serde_json::to_string_pretty(&answer)?);
    } else {
        println!("{}", output::render(&answer, &page_options, body.as_deref()));
    }
    Ok(ExitCode::SUCCESS)
}

type Computed = (puffin_code::router::Answer, Option<String>, PageArgs);

fn compute(context: &Context, command: &Command) -> anyhow::Result<Computed> {
    Ok(match command {
        Command::Def { name, page } => (context.def(name)?, None, page.clone()),
        Command::Refs { name, page } => (context.refs(name)?, None, page.clone()),
        Command::Callers { name, page } => (context.callers(name)?, None, page.clone()),
        Command::Callees { name, page } => (context.callees(name)?, None, page.clone()),
        Command::Impl { name, page } => (context.implementations(name)?, None, page.clone()),
        Command::Impact { name, diff, depth, page } => {
            let depth = depth.unwrap_or(puffin_code::router::IMPACT_DEPTH);
            let answer = match (name, diff) {
                (Some(name), None) => context.impact(name, depth)?,
                (None, Some(rev)) => context.impact_of_diff(rev, depth)?,
                _ => anyhow::bail!("`puffin-code impact` takes a name or --diff, not both and not neither"),
            };
            (answer, None, page.clone())
        }
        Command::Outline { file, page } => (context.outline(file)?, None, page.clone()),
        Command::Search { words, page } => (context.search(&words.join(" "))?, None, page.clone()),
        Command::Show { name, page } => {
            let (answer, body) = context.show(name)?;
            (answer, body, page.clone())
        }
        _ => unreachable!("handled in run"),
    })
}
