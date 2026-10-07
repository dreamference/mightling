// The loopback forwarder that lets the window keep loading `http://localhost:3000/app` on a
// machine that is not the node (specs/DREAMFERENCE_MIGHTLING_NODE.md §7).
//
// The web UI is served by the node. A window pointed at `http://<node address>:3000` would not be
// a secure context, so the page would have no `navigator.mediaDevices` and the microphone could
// not work; `http://localhost` is exempt. So the app binds a loopback port and passes every
// connection through to the node, byte for byte:
//
// * it forwards TCP, not HTTP, so a streamed answer and a WebSocket upgrade need no handling of
//   their own, and the `Host` header reaches the node as the browser wrote it (`localhost:3000`),
//   which is what keeps redirects and absolute links pointing back at the forwarder;
// * with no node to forward to it answers every request with one page that says so, in place of
//   the webview's bare connection error.
//
// Standard library only, so it is tested on its own (`rustc --test src/forwarder.rs`).

use std::io::Read;
use std::io::Write;
use std::net::Ipv4Addr;
use std::net::Shutdown;
use std::net::TcpListener;
use std::net::TcpStream;
use std::net::ToSocketAddrs;
use std::time::Duration;

/// The port the window has always used, and the one the saved session belongs to: a web origin
/// includes its port, so the port must not change from run to run.
pub const PREFERRED_PORT: u16 = 3000;

/// Used when something else on this machine already holds the preferred port.
pub const FALLBACK_PORT: u16 = 33000;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);

/// What the forwarder does with a connection.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Upstream {
    /// Pass it through to the node's web UI at this `address:port`.
    Node(String),
    /// There is no node: answer with a page carrying this message.
    Message(String),
}

/// Binds the loopback port (the preferred one, else the fallback) and serves it on a background
/// thread for as long as the process lives. Returns the port bound.
pub fn start(upstream: Upstream) -> std::io::Result<u16> {
    let listener = bind()?;
    let port = listener.local_addr()?.port();
    std::thread::Builder::new()
        .name("mightling-forwarder".to_string())
        .spawn(move || serve(listener, upstream))?;
    Ok(port)
}

fn bind() -> std::io::Result<TcpListener> {
    TcpListener::bind((Ipv4Addr::LOCALHOST, PREFERRED_PORT))
        .or_else(|_| TcpListener::bind((Ipv4Addr::LOCALHOST, FALLBACK_PORT)))
}

/// Accepts connections until the listener fails; each one gets its own threads.
pub fn serve(listener: TcpListener, upstream: Upstream) {
    for connection in listener.incoming() {
        let Ok(client) = connection else { continue };
        let upstream = upstream.clone();
        let _ = std::thread::Builder::new()
            .name("mightling-forward".to_string())
            .spawn(move || match upstream {
                Upstream::Node(target) => forward(client, &target),
                Upstream::Message(text) => answer(client, &text),
            });
    }
}

/// Connects to the node and copies bytes both ways until either side closes. A node that does
/// not answer gets the client a page saying so, not a reset.
fn forward(client: TcpStream, target: &str) {
    let node = target
        .to_socket_addrs()
        .ok()
        .and_then(|mut addresses| addresses.next())
        .and_then(|address| TcpStream::connect_timeout(&address, CONNECT_TIMEOUT).ok());
    let Some(node) = node else {
        answer(
            client,
            &format!("The Mightling node at {target} is not answering. Is its web UI running? On the node: mling-admin chat start"),
        );
        return;
    };
    let _ = client.set_nodelay(true);
    let _ = node.set_nodelay(true);
    let (Ok(client_reader), Ok(node_reader)) = (client.try_clone(), node.try_clone()) else {
        return;
    };
    let upload = std::thread::spawn(move || pipe(client_reader, node));
    pipe(node_reader, client);
    let _ = upload.join();
}

/// Copies `from` into `to` until `from` ends, then closes `to`'s write side so the other end sees
/// the end too (a half-close, which keeps the opposite direction open until it finishes).
fn pipe(mut from: TcpStream, mut to: TcpStream) {
    let mut buffer = [0u8; 16 * 1024];
    loop {
        match from.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => {
                if to.write_all(&buffer[..count]).is_err() {
                    break;
                }
            }
        }
    }
    let _ = to.shutdown(Shutdown::Write);
    let _ = from.shutdown(Shutdown::Read);
}

/// Answers one request with the message page.
fn answer(mut client: TcpStream, message: &str) {
    // Read the request's head so the client is not reset while still writing it.
    let _ = client.set_read_timeout(Some(Duration::from_secs(2)));
    let mut head = Vec::new();
    let mut buffer = [0u8; 4096];
    while !head.windows(4).any(|window| window == b"\r\n\r\n") && head.len() < 64 * 1024 {
        match client.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => head.extend_from_slice(&buffer[..count]),
        }
    }
    let body = message_page(message);
    let response = format!(
        "HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = client.write_all(response.as_bytes());
    let _ = client.shutdown(Shutdown::Both);
}

/// The page shown in place of the web UI. It reloads itself, so the window recovers when the node
/// comes back without the user doing anything.
pub fn message_page(message: &str) -> String {
    let paragraphs: String = message
        .lines()
        .map(|line| format!("<p>{}</p>", escape(line)))
        .collect();
    format!(
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta http-equiv=\"refresh\" content=\"10\">\
         <title>Mightling</title><style>body{{font-family:Roboto,system-ui,sans-serif;background:#fff;color:#111;\
         max-width:40em;margin:18vh auto;padding:0 1.5em;line-height:1.5}}h1{{font-size:1.3em}}\
         p{{margin:.4em 0}}</style></head><body><h1>Mightling</h1>{paragraphs}</body></html>"
    )
}

fn escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A stand-in web UI on an ephemeral port; `handle` is given each connection.
    fn stand_in(handle: fn(TcpStream)) -> String {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind a stand-in server");
        let address = listener.local_addr().expect("stand-in address").to_string();
        std::thread::spawn(move || {
            for connection in listener.incoming().flatten() {
                std::thread::spawn(move || handle(connection));
            }
        });
        address
    }

    /// The forwarder on an ephemeral port, so the tests never need port 3000.
    fn forwarder(upstream: Upstream) -> String {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind the forwarder");
        let address = listener.local_addr().expect("forwarder address").to_string();
        std::thread::spawn(move || serve(listener, upstream));
        address
    }

    fn read_head(stream: &mut TcpStream) -> String {
        let mut head = Vec::new();
        let mut byte = [0u8; 1];
        while !head.ends_with(b"\r\n\r\n") {
            if stream.read(&mut byte).unwrap_or(0) == 0 {
                break;
            }
            head.push(byte[0]);
        }
        String::from_utf8_lossy(&head).into_owned()
    }

    #[test]
    fn a_streamed_response_arrives_piece_by_piece_with_the_host_header_unchanged() {
        let node = stand_in(|mut connection| {
            let head = read_head(&mut connection);
            // The node sees the Host the browser wrote, not its own address.
            let host = if head.contains("Host: localhost:3000\r\n") { "host-ok" } else { "host-rewritten" };
            let _ = connection.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n");
            for piece in ["data: one\n\n", "data: two\n\n", host] {
                let _ = connection.write_all(piece.as_bytes());
                let _ = connection.flush();
                std::thread::sleep(Duration::from_millis(150));
            }
        });
        let mut client = TcpStream::connect(forwarder(Upstream::Node(node))).expect("connect");
        let _ = client.write_all(b"GET /api/chat/stream HTTP/1.1\r\nHost: localhost:3000\r\n\r\n");
        assert!(read_head(&mut client).starts_with("HTTP/1.1 200 OK"));
        // The first event is readable before the server has sent the last: nothing is buffered
        // until the end.
        let started = std::time::Instant::now();
        let mut first = [0u8; 11];
        client.read_exact(&mut first).expect("the first event");
        assert_eq!(&first, b"data: one\n\n");
        assert!(started.elapsed() < Duration::from_millis(140), "the first event waited for later ones");
        let mut rest = String::new();
        let _ = client.read_to_string(&mut rest);
        assert_eq!(rest, "data: two\n\nhost-ok");
    }

    #[test]
    fn an_upgraded_connection_carries_bytes_both_ways_until_one_side_closes() {
        // After the upgrade a WebSocket is just bytes in both directions; the stand-in echoes
        // them upper-cased, twice, then closes.
        let node = stand_in(|mut connection| {
            let head = read_head(&mut connection);
            assert!(head.contains("Upgrade: websocket"));
            let _ = connection.write_all(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n");
            let mut buffer = [0u8; 5];
            for _ in 0..2 {
                if connection.read_exact(&mut buffer).is_err() {
                    return;
                }
                let _ = connection.write_all(&buffer.to_ascii_uppercase());
            }
        });
        let mut client = TcpStream::connect(forwarder(Upstream::Node(node))).expect("connect");
        let _ = client.write_all(b"GET /ws HTTP/1.1\r\nHost: localhost:3000\r\nUpgrade: websocket\r\n\r\n");
        assert!(read_head(&mut client).starts_with("HTTP/1.1 101"));
        let mut reply = [0u8; 5];
        for frame in [b"hello", b"again"] {
            let _ = client.write_all(frame);
            client.read_exact(&mut reply).expect("an echoed frame");
            assert_eq!(reply, frame.to_ascii_uppercase().as_slice());
        }
        // The node closed: the client sees the end, not a hang.
        let mut end = Vec::new();
        assert_eq!(client.read_to_end(&mut end).unwrap_or(1), 0);
    }

    #[test]
    fn with_no_node_every_request_gets_the_message_page() {
        let address = forwarder(Upstream::Message("No Mightling node found on this network.\nStart one on a GB10 <now>.".to_string()));
        let mut client = TcpStream::connect(address).expect("connect");
        let _ = client.write_all(b"GET /app HTTP/1.1\r\nHost: localhost:3000\r\n\r\n");
        let mut response = String::new();
        let _ = client.read_to_string(&mut response);
        assert!(response.starts_with("HTTP/1.1 503 "), "{response}");
        assert!(response.contains("<p>No Mightling node found on this network.</p>"));
        assert!(response.contains("<p>Start one on a GB10 &lt;now&gt;.</p>"));
        assert!(response.contains("http-equiv=\"refresh\""));
    }

    #[test]
    fn a_node_that_does_not_answer_is_a_page_not_a_reset() {
        // Port 9 on loopback refuses at once.
        let mut client = TcpStream::connect(forwarder(Upstream::Node("127.0.0.1:9".to_string()))).expect("connect");
        let _ = client.write_all(b"GET /app HTTP/1.1\r\nHost: localhost:3000\r\n\r\n");
        let mut response = String::new();
        let _ = client.read_to_string(&mut response);
        assert!(response.contains("127.0.0.1:9 is not answering"), "{response}");
    }
}
