//! `ling web …`: the Mightling web server (specs/DREAMFERENCE_MIGHTLING_ASK.md §4). The server is
//! the `ling-web-server` crate (`web/`); this module only tells it where things are: the home
//! folder, this binary (it starts `ling app-server` and reads `ling prompt show --composed`), and
//! the model server, named to the app-server it starts so that child never browses for a node.

use std::path::PathBuf;

use crate::node;

/// The model server the app-server child is pointed at: the configured one, or loopback on a node.
/// A client with nothing configured leaves it to the child's own tiers, as `ling` does by itself.
fn child_env() -> Vec<(String, String)> {
    let inputs = node::inputs();
    let host = inputs.configured.or_else(|| inputs.is_node.then(|| crate::DEFAULT_VLLM_HOST.to_string()));
    host.map(|host| vec![("DREAMFERENCE_VLLM_HOST".to_string(), host)]).unwrap_or_default()
}

/// `ling web …` from a shell. Returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let Some(codex_home) = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf()) else {
        eprintln!("Could not resolve CODEX_HOME.");
        return 1;
    };
    let Some(home) = std::env::var_os("HOME").map(PathBuf::from) else {
        eprintln!("HOME is not set.");
        return 1;
    };
    let ling = match std::env::current_exe() {
        Ok(path) => path,
        Err(err) => {
            eprintln!("Could not find this program's path: {err}");
            return 1;
        }
    };
    let environment = ling_web_server::Environment { codex_home, home, ling, child_env: child_env() };
    ling_web_server::run_cli(args, environment).await
}
