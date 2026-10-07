//! The user's `ling app-server`, reached over its Unix socket (specs/DREAMFERENCE_MIGHTLING_ASK.md
//! §2.1). In `--listen unix://PATH` mode the server takes any number of clients, one WebSocket
//! each, so every browser tab gets a connection of its own and none of them owns the server.
//!
//! When nothing answers on the socket, `ling web` starts the server itself, as its child: the
//! same command the desktop app runs, with the model server named in `DREAMFERENCE_VLLM_HOST` so
//! the child never browses for a node (an mDNS query is egress). Its stderr, where the launcher
//! says it is waiting for a model server, goes to every tab and to `app-server.log`.

use std::path::Path;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use tokio::io::AsyncBufReadExt;
use tokio::io::AsyncWriteExt;
use tokio::net::UnixStream;
use tokio::process::Child;
use tokio::process::Command;
use tokio::sync::Mutex;
use tokio::sync::broadcast;
use tokio_tungstenite::WebSocketStream;

/// A connection to the app-server.
pub type Upstream = WebSocketStream<UnixStream>;

/// How long a start may take: the launcher first waits for a model server that may still be loading.
pub const START_TIMEOUT: Duration = Duration::from_secs(15 * 60);

/// What starts `ling app-server` and where it listens.
#[derive(Clone, Debug)]
pub struct Launch {
    /// The `ling` binary.
    pub ling: PathBuf,
    /// Environment added to the child's.
    pub env: Vec<(String, String)>,
    /// Where its stderr is kept.
    pub log: PathBuf,
}

pub struct AppServer {
    socket: PathBuf,
    launch: Launch,
    child: Mutex<Option<Child>>,
    stderr: broadcast::Sender<String>,
}

/// Creates the socket's folder, private to the user: the app-server refuses a parent others could
/// write to.
pub fn prepare_socket_dir(socket: &Path) -> std::io::Result<()> {
    match socket.parent() {
        Some(parent) => crate::auth::private_dir(parent),
        None => Ok(()),
    }
}

/// The socket to use: `$XDG_RUNTIME_DIR/mightling/app-server.sock`, else one in the state folder.
pub fn default_socket(state_dir: &Path) -> PathBuf {
    match std::env::var_os("XDG_RUNTIME_DIR").filter(|dir| !dir.is_empty()) {
        Some(dir) => PathBuf::from(dir).join("mightling").join("app-server.sock"),
        None => state_dir.join("run").join("app-server.sock"),
    }
}

impl AppServer {
    pub fn new(socket: PathBuf, launch: Launch) -> AppServer {
        let (stderr, _) = broadcast::channel(256);
        AppServer { socket, launch, child: Mutex::new(None), stderr }
    }

    pub fn socket(&self) -> &Path {
        &self.socket
    }

    /// The launcher's lines from a server this process started, for the tabs' start-up screen.
    pub fn stderr(&self) -> broadcast::Receiver<String> {
        self.stderr.subscribe()
    }

    /// One attempt to connect.
    pub async fn connect_once(&self) -> std::io::Result<Upstream> {
        let stream = UnixStream::connect(&self.socket).await?;
        let (upstream, _) = tokio_tungstenite::client_async("ws://localhost/", stream)
            .await
            .map_err(|err| std::io::Error::other(err.to_string()))?;
        Ok(upstream)
    }

    /// Connects, starting the server first when nothing answers. Returns the connection and
    /// whether this call started the server.
    pub async fn connect(&self) -> Result<(Upstream, bool), String> {
        if let Ok(upstream) = self.connect_once().await {
            return Ok((upstream, false));
        }
        let mut child = self.child.lock().await;
        // Another tab may have started it while this one waited for the lock.
        if let Ok(upstream) = self.connect_once().await {
            return Ok((upstream, false));
        }
        let running = match child.as_mut() {
            Some(process) => matches!(process.try_wait(), Ok(None)),
            None => false,
        };
        if !running {
            *child = Some(self.spawn()?);
        }
        let deadline = tokio::time::Instant::now() + START_TIMEOUT;
        loop {
            if let Ok(upstream) = self.connect_once().await {
                return Ok((upstream, true));
            }
            if let Some(process) = child.as_mut()
                && let Ok(Some(status)) = process.try_wait()
            {
                *child = None;
                return Err(format!(
                    "ling app-server exited ({status}) before it listened; see {}",
                    self.launch.log.display()
                ));
            }
            if tokio::time::Instant::now() >= deadline {
                return Err("ling app-server did not start listening in time".to_string());
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
    }

    fn spawn(&self) -> Result<Child, String> {
        prepare_socket_dir(&self.socket).map_err(|err| format!("could not prepare {}: {err}", self.socket.display()))?;
        let listen = format!("unix://{}", self.socket.display());
        let mut command = Command::new(&self.launch.ling);
        command
            .args(["-c", "features.code_mode_host=true", "app-server", "--listen", &listen])
            .envs(self.launch.env.iter().map(|(key, value)| (key.as_str(), value.as_str())))
            .env("RUST_LOG", "warn")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let mut child =
            command.spawn().map_err(|err| format!("could not start {} app-server: {err}", self.launch.ling.display()))?;
        if let Some(stderr) = child.stderr.take() {
            let sender = self.stderr.clone();
            let log = self.launch.log.clone();
            tokio::spawn(async move {
                let mut file = tokio::fs::OpenOptions::new().create(true).append(true).open(&log).await.ok();
                let mut lines = tokio::io::BufReader::new(stderr).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    if let Some(file) = file.as_mut() {
                        let _ = file.write_all(format!("{line}\n").as_bytes()).await;
                    }
                    let _ = sender.send(line);
                }
            });
        }
        Ok(child)
    }

    /// Stops a server this process started; one started elsewhere is left alone.
    pub async fn stop_child(&self) {
        if let Some(mut child) = self.child.lock().await.take() {
            let _ = child.kill().await;
        }
    }
}
