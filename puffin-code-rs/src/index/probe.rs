//! Is the model busy? (spec §9.2) The supervisor asks the served model's `/metrics` for the
//! engine's request gauges, and treats a model container that exists but does not answer
//! `/health` as loading. Behind a trait, so tests serve fake gauges and never run docker.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::process::{Command, Stdio};
use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModelState {
    /// Serving, with no request running or queued.
    Idle,
    /// Serving a request.
    Busy,
    /// A model container exists but does not answer: its weights are being loaded.
    Loading,
    /// No model server at all; counts as idle.
    Absent,
}

pub trait Probe {
    fn state(&self) -> ModelState;
}

/// The real probe: HTTP to the model server, `docker ps` for the container.
pub struct HttpProbe {
    pub base_url: String,
    /// Whether to ask docker for a loading container when nothing answers (off in tests).
    pub docker: bool,
}

impl HttpProbe {
    pub fn new(base_url: &str) -> Self {
        HttpProbe { base_url: base_url.to_string(), docker: true }
    }
}

/// The request gauges of both engines.
const GAUGES: &[&str] = &["sglang:num_running_reqs", "sglang:num_queue_reqs", "vllm:num_requests_running", "vllm:num_requests_waiting"];

impl Probe for HttpProbe {
    fn state(&self) -> ModelState {
        match http_get(&self.base_url, "/metrics") {
            Some((200, body)) => {
                if busy(&body) {
                    ModelState::Busy
                } else {
                    ModelState::Idle
                }
            }
            Some(_) => ModelState::Loading,
            None => {
                if self.docker && model_container_present() {
                    ModelState::Loading
                } else {
                    ModelState::Absent
                }
            }
        }
    }
}

/// Whether any engine gauge in a Prometheus text body is above zero.
pub fn busy(body: &str) -> bool {
    body.lines().filter(|l| !l.starts_with('#')).any(|line| {
        GAUGES.iter().any(|gauge| {
            line.starts_with(gauge)
                && line.rsplit(' ').next().and_then(|v| v.parse::<f64>().ok()).map(|v| v > 0.0).unwrap_or(false)
        })
    })
}

/// A container named like the model server (`dreamference-vllm-<port>`, both engines) exists.
fn model_container_present() -> bool {
    Command::new("docker")
        .args(["ps", "--filter", "name=dreamference-vllm-", "--format", "{{.Names}}"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map(|o| !String::from_utf8_lossy(&o.stdout).trim().is_empty())
        .unwrap_or(false)
}

/// A minimal HTTP/1.1 GET: `(status, body)`, or None when nothing answers within 2 s.
pub fn http_get(base_url: &str, path: &str) -> Option<(u16, String)> {
    let rest = base_url.strip_prefix("http://")?;
    let host_port = rest.split('/').next()?;
    let address = if host_port.contains(':') { host_port.to_string() } else { format!("{host_port}:80") };
    let socket = address.to_socket_addrs().ok()?.next()?;
    let mut stream = TcpStream::connect_timeout(&socket, Duration::from_secs(2)).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    let request = format!("GET {path} HTTP/1.1\r\nHost: {host_port}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).ok();
    let text = String::from_utf8_lossy(&response);
    let status = text.split_whitespace().nth(1)?.parse().ok()?;
    let body = text.split_once("\r\n\r\n").map(|(_, b)| b.to_string()).unwrap_or_default();
    Some((status, body))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    fn serve(body: &'static str, status: u16) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                // Read the whole request before answering, or closing with unread input resets
                // the connection under the client.
                let mut request = Vec::new();
                let mut buffer = [0u8; 1024];
                while !request.windows(4).any(|w| w == b"\r\n\r\n") {
                    match stream.read(&mut buffer) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => request.extend_from_slice(&buffer[..n]),
                    }
                }
                let _ = write!(stream, "HTTP/1.1 {status} OK\r\nContent-Length: {}\r\n\r\n{body}", body.len());
            }
        });
        format!("http://{address}")
    }

    #[test]
    fn gauges_of_both_engines() {
        assert!(busy("# HELP x\nsglang:num_running_reqs{model_name=\"q\"} 1.0\n"));
        assert!(busy("vllm:num_requests_waiting{model_name=\"q\"} 3\n"));
        assert!(!busy("sglang:num_running_reqs{model_name=\"q\"} 0.0\nsglang:num_queue_reqs{model_name=\"q\"} 0.0\n"));
        assert!(!busy("vllm:num_requests_running{model_name=\"q\"} 0.0\n"));
    }

    #[test]
    fn idle_and_busy_over_http() {
        let probe = |url: String| HttpProbe { base_url: url, docker: false };
        let idle = probe(serve("sglang:num_running_reqs{m=\"q\"} 0.0\n", 200));
        assert_eq!(idle.state(), ModelState::Idle);
        let busy = probe(serve("vllm:num_requests_running{m=\"q\"} 2.0\n", 200));
        assert_eq!(busy.state(), ModelState::Busy);
        let loading = probe(serve("", 503));
        assert_eq!(loading.state(), ModelState::Loading);
        assert_eq!(probe("http://127.0.0.1:9".into()).state(), ModelState::Absent);
    }
}
