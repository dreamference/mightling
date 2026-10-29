//! `/forge/`: the node's forge (Forgejo) through `ling web`, signed in
//! (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §2.2, §2.7).
//!
//! A paired device with the `control` scope, or this machine's own session, sees the forge signed
//! in as `owner` without typing a password: `ling web` signs in to the forge itself with the
//! owner's password file and keeps the forge's cookies here, in the server. The browser never gets
//! one: cookies are not kept apart by port, so a forge cookie set through `ling web` would also sign
//! the browser in on the forge's own port, and revoking a device would not end it. Forgejo's
//! reverse-proxy header sign-in stays off: any process on the node can reach the forge's port.
//!
//! The forge's `ROOT_URL` ends in `/forge/` (the Python side writes it), so its pages link under
//! `/forge/`; Forgejo itself serves at its root, so the prefix is taken off on the way in. Git over
//! HTTP (`…/info/refs`, `git-upload-pack`, `git-receive-pack`) passes through with the client's own
//! credentials and none of ours, only on the LAN and loopback names, never on an overlay front: it
//! is what the forge's own port already offers on the LAN, so the clone URLs the forge shows work.

use std::collections::BTreeMap;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use axum::body::Body;
use axum::extract::Request;
use axum::http::HeaderMap;
use axum::http::HeaderName;
use axum::http::HeaderValue;
use axum::http::Method;
use axum::http::StatusCode;
use axum::http::Uri;
use axum::http::header;
use axum::response::IntoResponse;
use axum::response::Response;
use hyper_util::rt::TokioIo;

use crate::auth;

/// Where the node's forge answers on loopback when the configuration names no other.
pub const DEFAULT_FORGE_URL: &str = "http://127.0.0.1:3200";
/// The path the forge is served under, as its `ROOT_URL` says.
pub const PREFIX: &str = "/forge";
/// The forge account a paired device is signed in as.
pub const OWNER: &str = "owner";
/// The owner's password, written by `ling-admin node enable` (FORGEJO §2.2), relative to the
/// folder of the user's configuration file (`~/.config/dreamference`).
pub const PASSWORD_FILE: &str = "forge/owner-password";
/// How long connecting to the forge may take.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(3);
/// How long a sign-in may take.
const SIGN_IN_TIMEOUT: Duration = Duration::from_secs(15);

/// The policy for the forge's pages, when the forge sends none of its own: Forgejo's templates
/// carry inline scripts and styles, so this is looser than the UI's; it still keeps everything on
/// this origin.
pub const FORGE_CSP: &str = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; \
img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; media-src 'self' blob:; \
object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'";

/// Headers that belong to one connection, never passed on (RFC 9110 §7.6.1), plus the ones this
/// proxy sets itself.
const HOP_BY_HOP: &[&str] = &[
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
];

/// The forge's cookies, kept in the server for every signed-in device alike.
#[derive(Default)]
struct Jar {
    cookies: BTreeMap<String, String>,
    signed_in: bool,
}

impl Jar {
    fn header(&self) -> Option<HeaderValue> {
        if self.cookies.is_empty() {
            return None;
        }
        let text = self.cookies.iter().map(|(name, value)| format!("{name}={value}")).collect::<Vec<_>>().join("; ");
        HeaderValue::from_str(&text).ok()
    }

    /// Takes in one `Set-Cookie`: a value, or a removal (empty, or `Max-Age` of zero or less).
    fn absorb(&mut self, set_cookie: &str) {
        let mut parts = set_cookie.split(';');
        let Some((name, value)) = parts.next().and_then(|pair| pair.split_once('=')) else { return };
        let (name, value) = (name.trim(), value.trim());
        if name.is_empty() {
            return;
        }
        let expired = parts.any(|attribute| {
            let attribute = attribute.trim().to_ascii_lowercase();
            attribute.strip_prefix("max-age=").is_some_and(|age| age.trim().parse::<i64>().map(|age| age <= 0).unwrap_or(false))
        });
        if expired || value.is_empty() {
            self.cookies.remove(name);
        } else {
            self.cookies.insert(name.to_string(), value.to_string());
        }
    }
}

/// What `ling web` keeps for the forge: the signed-in cookies.
#[derive(Default)]
pub struct ForgeProxy {
    jar: tokio::sync::Mutex<Jar>,
}

/// Where the forge answers: the configured base URL's host and port (plain HTTP only).
pub fn upstream(forge_url: Option<&str>) -> Option<String> {
    let url = forge_url.unwrap_or(DEFAULT_FORGE_URL).trim().trim_end_matches('/');
    let authority = url.strip_prefix("http://")?;
    (!authority.is_empty() && !authority.contains('/') && !authority.contains('@')).then(|| authority.to_string())
}

/// The owner's password file, beside the user's configuration file; None without one.
pub fn password_file(user_config: Option<&Path>) -> Option<PathBuf> {
    Some(user_config?.parent()?.join(PASSWORD_FILE))
}

/// Whether a path under `/forge/` is git's smart HTTP protocol, which carries its own credentials.
pub fn is_git(path: &str) -> bool {
    let Some(rest) = path.strip_prefix("/forge/") else { return false };
    let parts: Vec<&str> = rest.split('/').collect();
    let tail = match parts.as_slice() {
        [owner, repo, tail @ ..] if !owner.is_empty() && !repo.is_empty() => tail,
        _ => return false,
    };
    matches!(tail, ["info", "refs"] | ["git-upload-pack"] | ["git-receive-pack"])
}

/// A `Location` the forge sends, made relative when it points under `/forge/`: Forgejo writes its
/// `ROOT_URL`'s host, which is not the name a phone's front or an address reaches `ling web` by.
pub fn local_location(location: &str) -> String {
    for scheme in ["http://", "https://"] {
        if let Some(rest) = location.strip_prefix(scheme) {
            let path = rest.find('/').map(|at| &rest[at..]).unwrap_or("/");
            if path == PREFIX || path.starts_with("/forge/") || path.starts_with("/forge?") {
                return path.to_string();
            }
        }
    }
    location.to_string()
}

/// Encodes a form value (`application/x-www-form-urlencoded`).
fn form_value(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

fn page(status: StatusCode, title: &str, line: &str) -> Response {
    let body = format!(
        "<!doctype html><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>{title}</title>\
<link rel=\"stylesheet\" href=\"/pair.css\"><main><h1>{title}</h1><p>{line}</p><p><a href=\"/\">Back to Mightling</a></p></main>"
    );
    (status, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], body).into_response()
}

/// Sends one request to the forge and returns its response.
async fn send(authority: &str, request: axum::http::Request<Body>) -> Result<axum::http::Response<hyper::body::Incoming>, String> {
    let stream = tokio::time::timeout(CONNECT_TIMEOUT, tokio::net::TcpStream::connect(authority))
        .await
        .map_err(|_| "the forge did not answer in time".to_string())?
        .map_err(|_| "the forge is not answering".to_string())?;
    let (mut sender, connection) =
        hyper::client::conn::http1::handshake(TokioIo::new(stream)).await.map_err(|_| "the forge is not answering".to_string())?;
    tokio::spawn(async move {
        let _ = connection.await;
    });
    sender.send_request(request).await.map_err(|_| "the forge closed the connection".to_string())
}

fn is_sign_in_page(response: &axum::http::Response<hyper::body::Incoming>) -> bool {
    response.status().is_redirection()
        && response
            .headers()
            .get(header::LOCATION)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|location| local_location(location).starts_with("/forge/user/login"))
}

impl ForgeProxy {
    pub fn new() -> ForgeProxy {
        ForgeProxy::default()
    }

    /// Signs in as `owner` with the password file, keeping the cookies the forge sets.
    async fn sign_in(&self, jar: &mut Jar, authority: &str, password: &str, host: &str) -> Result<(), String> {
        jar.signed_in = false;
        // The sign-in page first, for the cookies a form post is checked against.
        let first = axum::http::Request::builder()
            .method(Method::GET)
            .uri("/user/login")
            .header(header::HOST, host)
            .body(Body::empty())
            .map_err(|_| "a bad request".to_string())?;
        let response = tokio::time::timeout(SIGN_IN_TIMEOUT, send(authority, first)).await.map_err(|_| "the forge is too slow".to_string())??;
        for value in response.headers().get_all(header::SET_COOKIE) {
            if let Ok(text) = value.to_str() {
                jar.absorb(text);
            }
        }
        let html = axum::body::to_bytes(Body::new(response.into_body()), 4 * 1024 * 1024).await.unwrap_or_default();
        let csrf = csrf_field(&String::from_utf8_lossy(&html));
        let mut form = format!("user_name={}&password={}&remember=on", form_value(OWNER), form_value(password));
        if let Some(csrf) = csrf {
            form.push_str(&format!("&_csrf={}", form_value(&csrf)));
        }
        let mut post = axum::http::Request::builder()
            .method(Method::POST)
            .uri("/user/login")
            .header(header::HOST, host)
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded");
        if let Some(cookies) = jar.header() {
            post = post.header(header::COOKIE, cookies);
        }
        let post = post.body(Body::from(form)).map_err(|_| "a bad request".to_string())?;
        let response = tokio::time::timeout(SIGN_IN_TIMEOUT, send(authority, post)).await.map_err(|_| "the forge is too slow".to_string())??;
        for value in response.headers().get_all(header::SET_COOKIE) {
            if let Ok(text) = value.to_str() {
                jar.absorb(text);
            }
        }
        // Forgejo answers a good sign-in with a redirect away from the sign-in page, and a bad one
        // with the page again (200).
        let away = response.status().is_redirection() && !is_sign_in_page(&response);
        if !away {
            return Err("the forge refused the owner's password".to_string());
        }
        jar.signed_in = true;
        Ok(())
    }

    /// Answers a request under `/forge/`. The caller has checked the credential (or that it is
    /// git's protocol on a LAN name, [`is_git`]).
    pub async fn handle(&self, forge_url: Option<&str>, user_config: Option<&Path>, request: Request) -> Response {
        let path = request.uri().path().to_string();
        if path == PREFIX {
            let query = request.uri().query().map(|query| format!("?{query}")).unwrap_or_default();
            return (StatusCode::PERMANENT_REDIRECT, [(header::LOCATION, format!("{PREFIX}/{query}"))]).into_response();
        }
        let Some(authority) = upstream(forge_url) else {
            return page(StatusCode::NOT_FOUND, "No forge here", "The forge's address is not one this server can reach.");
        };
        let git = is_git(&path);
        let password = if git {
            None
        } else {
            let Some(text) = password_file(user_config).and_then(|file| std::fs::read_to_string(file).ok()) else {
                return page(
                    StatusCode::NOT_FOUND,
                    "No forge on this node",
                    "This node has no forge set up. <code>ling-admin forge on</code> on the node starts it.",
                );
            };
            Some(text.trim().to_string())
        };
        let (parts, body) = request.into_parts();
        let rest = &path[PREFIX.len()..];
        let target = match parts.uri.query() {
            Some(query) => format!("{rest}?{query}"),
            None => rest.to_string(),
        };
        let Ok(target) = target.parse::<Uri>() else {
            return page(StatusCode::BAD_REQUEST, "Not a forge address", "That address cannot be passed to the forge.");
        };
        let host = parts.headers.get(header::HOST).and_then(|value| value.to_str().ok()).unwrap_or("localhost").to_string();
        let headers = outgoing_headers(&parts.headers, git);
        let replayable = matches!(parts.method, Method::GET | Method::HEAD);

        let Some(password) = password else {
            // Git's protocol: the client's own credentials, none of ours, the body streamed.
            let request = build(&parts.method, &target, &headers, None, body);
            return match send(&authority, request).await {
                Ok(response) => incoming(response, None),
                Err(reason) => page(StatusCode::BAD_GATEWAY, "The forge is not answering", &reason),
            };
        };

        // The lock is held to sign in and to read or take in cookies, never while the forge works.
        let cookies = {
            let mut jar = self.jar.lock().await;
            if !jar.signed_in
                && let Err(reason) = self.sign_in(&mut jar, &authority, &password, &host).await
            {
                return page(StatusCode::BAD_GATEWAY, "Signing in to the forge failed", &format!("{reason}."));
            }
            jar.header()
        };
        let mut response = match send(&authority, build(&parts.method, &target, &headers, cookies, body)).await {
            Ok(response) => response,
            Err(reason) => return page(StatusCode::BAD_GATEWAY, "The forge is not answering", &reason),
        };
        if is_sign_in_page(&response) {
            // The forge's session ended (expired, or signed out on the forge): sign in again, and
            // ask once more when the request can be repeated.
            let cookies = {
                let mut jar = self.jar.lock().await;
                if let Err(reason) = self.sign_in(&mut jar, &authority, &password, &host).await {
                    return page(StatusCode::BAD_GATEWAY, "Signing in to the forge failed", &format!("{reason}."));
                }
                jar.header()
            };
            if replayable {
                response = match send(&authority, build(&parts.method, &target, &headers, cookies, Body::empty())).await {
                    Ok(response) => response,
                    Err(reason) => return page(StatusCode::BAD_GATEWAY, "The forge is not answering", &reason),
                };
            }
        }
        let mut jar = self.jar.lock().await;
        incoming(response, Some(&mut jar))
    }
}

/// The `_csrf` field of a Forgejo form, when the page has one (older releases did).
fn csrf_field(html: &str) -> Option<String> {
    let at = html.find("name=\"_csrf\"")?;
    let rest = &html[at..];
    let value = rest.find("value=\"")? + "value=\"".len();
    let end = rest[value..].find('"')?;
    Some(rest[value..value + end].to_string())
}

/// The request's headers as the forge gets them: without this server's cookies, without the
/// connection's own headers, and without credentials unless git's protocol carries them.
fn outgoing_headers(headers: &HeaderMap, git: bool) -> HeaderMap {
    let mut out = HeaderMap::new();
    for (name, value) in headers {
        let lower = name.as_str();
        if HOP_BY_HOP.contains(&lower) || lower == "cookie" || lower.starts_with("x-forwarded-") || lower == "forwarded" {
            continue;
        }
        if lower == "authorization" && !git {
            continue;
        }
        out.append(name.clone(), value.clone());
    }
    out
}

fn build(method: &Method, target: &Uri, headers: &HeaderMap, cookies: Option<HeaderValue>, body: Body) -> axum::http::Request<Body> {
    let mut request = axum::http::Request::new(body);
    *request.method_mut() = method.clone();
    *request.uri_mut() = target.clone();
    *request.headers_mut() = headers.clone();
    if let Some(cookies) = cookies {
        request.headers_mut().insert(header::COOKIE, cookies);
    }
    request
}

/// The forge's response as the browser gets it: its cookies kept here, never passed on, a
/// `Location` under `/forge/` made relative, and a policy when the forge sent none.
fn incoming(response: axum::http::Response<hyper::body::Incoming>, jar: Option<&mut Jar>) -> Response {
    let (parts, body) = response.into_parts();
    let mut out = Response::new(Body::new(body));
    *out.status_mut() = parts.status;
    let mut jar = jar;
    for (name, value) in &parts.headers {
        let lower = name.as_str();
        if HOP_BY_HOP.contains(&lower) {
            continue;
        }
        if lower == "set-cookie" {
            if let (Some(jar), Ok(text)) = (jar.as_deref_mut(), value.to_str()) {
                jar.absorb(text);
            }
            continue;
        }
        if lower == "location"
            && let Ok(text) = value.to_str()
            && let Ok(local) = HeaderValue::from_str(&local_location(text))
        {
            out.headers_mut().append(header::LOCATION, local);
            continue;
        }
        out.headers_mut().append(name.clone(), value.clone());
    }
    out.headers_mut().entry(header::CONTENT_SECURITY_POLICY).or_insert(HeaderValue::from_static(FORGE_CSP));
    out.headers_mut().entry(HeaderName::from_static("x-frame-options")).or_insert(HeaderValue::from_static("SAMEORIGIN"));
    out
}

/// Whether a request may see the forge signed in: this machine's own session, or a paired browser
/// with the `control` scope. A `watch` device follows threads; it gets no write access anywhere.
pub fn may_sign_in(state_dir: &Path, owner_session: bool, headers: &HeaderMap) -> bool {
    if owner_session {
        return true;
    }
    let cookies = headers.get(header::COOKIE).and_then(|value| value.to_str().ok());
    auth::device_for_cookie(state_dir, cookies).is_some_and(|device| device.scope == auth::Scope::Control)
}

/// The page a `watch` device gets instead of the forge.
pub fn watch_only() -> Response {
    page(
        StatusCode::FORBIDDEN,
        "The forge needs control",
        "This device is paired to watch only. The node's owner can give it control on the Devices page.",
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn git_paths_are_recognised_and_nothing_else() {
        assert!(is_git("/forge/dreamference/mightling.git/info/refs"));
        assert!(is_git("/forge/dreamference/mightling.git/git-upload-pack"));
        assert!(is_git("/forge/dreamference/mightling/git-receive-pack"));
        assert!(!is_git("/forge/dreamference/mightling"));
        assert!(!is_git("/forge/dreamference/mightling/src/branch/main/info/refs"));
        assert!(!is_git("/forge//x/info/refs"));
        assert!(!is_git("/ws"));
        assert!(!is_git("/forge/user/login"));
    }

    #[test]
    fn locations_under_the_prefix_become_relative() {
        assert_eq!(local_location("http://gx10-1a2b.local:3100/forge/user/login"), "/forge/user/login");
        assert_eq!(local_location("https://front.example/forge/x?y=1"), "/forge/x?y=1");
        assert_eq!(local_location("/forge/"), "/forge/");
        assert_eq!(local_location("https://elsewhere.example/other"), "https://elsewhere.example/other");
    }

    #[test]
    fn the_jar_keeps_values_and_drops_removals() {
        let mut jar = Jar::default();
        jar.absorb("session=abc; Path=/forge; HttpOnly; SameSite=Lax");
        jar.absorb("lang=en-US; Path=/forge");
        jar.absorb("persistent=; Path=/forge/; Max-Age=0");
        assert_eq!(jar.header().unwrap(), "lang=en-US; session=abc");
        jar.absorb("session=; Path=/forge/; Max-Age=0; HttpOnly");
        jar.absorb("lang=fr; Max-Age=-1");
        assert!(jar.header().is_none());
    }

    #[test]
    fn upstream_takes_plain_http_only() {
        assert_eq!(upstream(None).as_deref(), Some("127.0.0.1:3200"));
        assert_eq!(upstream(Some("http://127.0.0.1:9/")).as_deref(), Some("127.0.0.1:9"));
        assert_eq!(upstream(Some("https://127.0.0.1:9")), None);
        assert_eq!(upstream(Some("http://user@127.0.0.1:9")), None);
        assert_eq!(upstream(Some("http://127.0.0.1:9/sub")), None);
    }

    #[test]
    fn form_values_are_encoded() {
        assert_eq!(form_value("a b&c=d/é"), "a%20b%26c%3Dd%2F%C3%A9");
        assert_eq!(csrf_field("<input name=\"_csrf\" value=\"tok\">").as_deref(), Some("tok"));
        assert_eq!(csrf_field("<input name=\"user_name\">"), None);
    }
}
