//! A minimal HTTP/1.1 GET over a std `TcpStream`, for the loopback service only.
//!
//! The crate is called from the TUI and the app server, which must not gain an HTTP client or an
//! async runtime through a patch; the service is a Python `http.server` that answers each request
//! with a `Content-Length` and closes, so reading to the end is the whole protocol.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

/// The header the service requires on everything but `/status` and the connect pages.
pub const AUTH_HEADER: &str = "X-Puffin-Gmail-Token";

/// The largest answer read; the service caps a message or a file at 20,000 characters, so this is
/// a guard against a runaway, not a limit a correct answer reaches.
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

/// Sends `GET path` to `addr` and returns the status code and the body.
///
/// Args: `secret` goes in [`AUTH_HEADER`] when given; `timeout` bounds the connect, each read and
/// each write.
pub fn get(addr: &str, path: &str, secret: Option<&str>, timeout: Duration) -> Result<(u16, String), String> {
    let target = addr
        .to_socket_addrs()
        .map_err(|error| format!("bad address {addr}: {error}"))?
        .next()
        .ok_or_else(|| format!("bad address {addr}"))?;
    let mut stream = TcpStream::connect_timeout(&target, timeout).map_err(|error| error.to_string())?;
    stream.set_read_timeout(Some(timeout)).map_err(|error| error.to_string())?;
    stream.set_write_timeout(Some(timeout)).map_err(|error| error.to_string())?;
    let mut request = format!("GET {path} HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\nAccept: application/json\r\n");
    if let Some(secret) = secret {
        request.push_str(&format!("{AUTH_HEADER}: {secret}\r\n"));
    }
    request.push_str("\r\n");
    stream.write_all(request.as_bytes()).map_err(|error| error.to_string())?;
    let mut raw = Vec::new();
    stream
        .take(MAX_RESPONSE_BYTES as u64)
        .read_to_end(&mut raw)
        .map_err(|error| error.to_string())?;
    parse_response(&raw)
}

/// Splits a raw HTTP response into its status code and body (chunked bodies are decoded).
pub fn parse_response(raw: &[u8]) -> Result<(u16, String), String> {
    let split = raw
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or("no end of headers")?;
    let head = String::from_utf8_lossy(&raw[..split]);
    let body = &raw[split + 4..];
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or("no status line")?;
    let chunked = head.lines().any(|line| {
        let lower = line.to_ascii_lowercase();
        lower.starts_with("transfer-encoding:") && lower.contains("chunked")
    });
    let body = if chunked { dechunk(body)? } else { body.to_vec() };
    Ok((status, String::from_utf8_lossy(&body).into_owned()))
}

fn dechunk(mut body: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let end = body.windows(2).position(|window| window == b"\r\n").ok_or("bad chunk")?;
        let size_text = String::from_utf8_lossy(&body[..end]);
        let size = usize::from_str_radix(size_text.split(';').next().unwrap_or("").trim(), 16)
            .map_err(|_| "bad chunk size")?;
        body = &body[end + 2..];
        if size == 0 {
            return Ok(out);
        }
        if body.len() < size {
            return Err("truncated chunk".into());
        }
        out.extend_from_slice(&body[..size]);
        body = body.get(size + 2..).unwrap_or_default();
    }
}

/// Percent-encodes a query value or a path segment (RFC 3986 unreserved characters stay).
pub fn encode(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn plain_and_chunked_bodies() {
        let plain = b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}";
        assert_eq!(parse_response(plain), Ok((200, "{}".to_string())));
        let chunked = b"HTTP/1.1 401 Unauthorized\r\nTransfer-Encoding: chunked\r\n\r\n3\r\n{\"a\r\n4\r\n\":1}\r\n0\r\n\r\n";
        assert_eq!(parse_response(chunked), Ok((401, "{\"a\":1}".to_string())));
        assert!(parse_response(b"garbage").is_err());
    }

    #[test]
    fn encoding_keeps_unreserved_characters_only() {
        assert_eq!(encode("a@x.com|17f"), "a%40x.com%7C17f");
        assert_eq!(encode("from:alice newer_than:7d"), "from%3Aalice%20newer_than%3A7d");
    }

    #[test]
    fn a_request_carries_the_secret_and_reads_the_answer() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr").to_string();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let mut buffer = [0u8; 1024];
            let read = stream.read(&mut buffer).unwrap_or(0);
            let request = String::from_utf8_lossy(&buffer[..read]).into_owned();
            let _ = stream.write_all(b"HTTP/1.0 200 OK\r\nContent-Length: 11\r\n\r\n{\"ok\":true}");
            request
        });
        let answer = get(&addr, "/search?query=x", Some("s3cret"), Duration::from_secs(2));
        assert_eq!(answer, Ok((200, "{\"ok\":true}".to_string())));
        let request = server.join().unwrap_or_default();
        assert!(request.starts_with("GET /search?query=x HTTP/1.1"));
        assert!(request.contains("X-Puffin-Gmail-Token: s3cret"));
    }

    #[test]
    fn a_closed_port_is_an_error_not_a_hang() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr").to_string();
        drop(listener);
        assert!(get(&addr, "/status", None, Duration::from_millis(500)).is_err());
    }
}
