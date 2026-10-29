//! The Discord adapter (MIGHTLING_DISCORD §3): like Telegram, the less private messenger, for users
//! who typed `yes` to everything passing through Discord's servers unencrypted.
//!
//! It is an outbound client only: the Gateway ([`crate::discord_gateway`]) brings the events, the
//! REST API takes the answers, and no port is opened. It answers in its own DM with a paired user
//! and nowhere else (pairing is `/pair <code>` with a code `ling chat discord pair` wrote), leaves
//! every server it is added to, and renders the hub's output: a status line with a Stop button and
//! Discord's typing indicator while a turn runs, the final answer as Discord Markdown split under
//! 2,000 characters, approvals as buttons, repo notices with a link button to `ling web`. Every
//! message it sends pings nobody (`allowed_mentions` empty) and shows no embed. The token travels
//! in a header only, and an interaction's token in a URL, so no error is shown with its URL.
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use std::time::Instant;
use reqwest::Method;
use serde_json::Value;
use serde_json::json;
use tokio::sync::Mutex;
use tokio::sync::mpsc;
use crate::attachments::Media;
use crate::blocks;
use crate::hub::HELP;
use crate::hub::Inbound;
use crate::hub::Outbound;
use crate::render;
use crate::repo_notice::AppThreadId;
use crate::repo_notice::RepoName;
use crate::store;
use crate::store::DiscordConfig;
use crate::telegram::UNPAIRED_REPLY;
use crate::telegram::approval_callback;

pub const API_ROOT: &str = "https://discord.com/api/v10";
/// Discord's limit is 2,000 characters; this leaves room for a reopened code fence.
pub const MESSAGE_LIMIT: usize = 1900;
/// An approval's text, at most (the buttons carry the choice).
const APPROVAL_TEXT_LIMIT: usize = 1000;
/// The largest attachment the bridge downloads.
pub const DOWNLOAD_LIMIT: u64 = 20 * 1024 * 1024;
/// Where attachments may come from: Discord's own CDN, fetched without the token.
const DOWNLOAD_PREFIXES: [&str; 2] = ["https://cdn.discordapp.com/", "https://media.discordapp.net/"];
/// `SUPPRESS_EMBEDS`: no link preview fetched for anything the bot sends.
const SUPPRESS_EMBEDS: u64 = 1 << 2;
/// `EPHEMERAL`: an interaction answer only the person who pressed sees.
const EPHEMERAL: u64 = 1 << 6;
/// Discord's typing indicator lasts 10 seconds.
const TYPING_EVERY: Duration = Duration::from_secs(8);
/// A command's "thinking…" answer is removed when nothing used it this long.
const PENDING_UNUSED: Duration = Duration::from_secs(30);
/// An interaction token is valid for 15 minutes.
const INTERACTION_VALID: Duration = Duration::from_secs(14 * 60);
const PAIRING_ATTEMPTS: u32 = 10;
const UNPAIRED_REPLY_EVERY: Duration = Duration::from_secs(24 * 60 * 60);
/// `BOT_DM`: the only context the bridge answers in.
const BOT_DM: u64 = 1;
const STOP_ID: &str = "stop";

/// A REST error, without the request's URL (an interaction's token is in it).
#[derive(Clone, Debug, PartialEq)]
pub struct ApiError {
    pub description: String,
    /// The HTTP status, 0 when no answer came.
    pub status: u16,
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.description)
    }
}

#[derive(Clone)]
pub struct DiscordApi {
    root: String,
    token: String,
    http: reqwest::Client,
    /// Attachments are fetched by a client that follows no redirect.
    files: reqwest::Client,
    download_prefixes: Vec<String>,
}

impl DiscordApi {
    /// `root` is `https://discord.com/api/v10`, or a test's stand-in.
    pub fn new(root: &str, token: &str) -> DiscordApi {
        let files = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).build().unwrap_or_default();
        DiscordApi {
            root: root.trim_end_matches('/').to_string(),
            token: token.to_string(),
            http: reqwest::Client::new(),
            files,
            download_prefixes: DOWNLOAD_PREFIXES.iter().map(|p| p.to_string()).collect(),
        }
    }

    /// Attachments allowed from these prefixes instead of Discord's CDN (tests).
    pub fn with_download_prefixes(mut self, prefixes: Vec<String>) -> DiscordApi {
        self.download_prefixes = prefixes;
        self
    }

    /// The token, for the Gateway's identify and resume.
    pub(crate) fn token(&self) -> &str {
        &self.token
    }

    /// A REST call. A rate limit (429) is waited out and retried, up to three times.
    pub async fn request(&self, method: Method, path: &str, body: Option<Value>) -> Result<Value, ApiError> {
        let failed = |err: reqwest::Error| ApiError { description: err.without_url().to_string(), status: 0 };
        for attempt in 0..4 {
            let mut request = self
                .http
                .request(method.clone(), format!("{}{path}", self.root))
                .header("Authorization", format!("Bot {}", self.token))
                .timeout(Duration::from_secs(30));
            if let Some(body) = &body {
                request = request.json(body);
            }
            let response = request.send().await.map_err(failed)?;
            let status = response.status().as_u16();
            let value: Value = response.json().await.unwrap_or(Value::Null);
            if (200..300).contains(&status) {
                return Ok(value);
            }
            let wait = value.get("retry_after").and_then(Value::as_f64).unwrap_or(1.0);
            if status == 429 && attempt < 3 && wait <= 60.0 {
                tokio::time::sleep(Duration::from_secs_f64(wait.max(0.0))).await;
                continue;
            }
            let message = value.get("message").and_then(Value::as_str).unwrap_or("Discord refused the request");
            return Err(ApiError { description: format!("{message} (HTTP {status})"), status });
        }
        Err(ApiError { description: "Discord kept rate-limiting the request".to_string(), status: 429 })
    }

    /// Sends a message into a channel.
    pub async fn send(&self, channel: &str, body: Value) -> Result<Value, ApiError> {
        self.request(Method::POST, &format!("/channels/{channel}/messages"), Some(body)).await
    }

    /// Sends a file into a channel (multipart, `payload_json` and `files[0]`).
    pub async fn upload(&self, channel: &str, name: &str, bytes: &[u8]) -> Result<Value, ApiError> {
        let failed = |err: reqwest::Error| ApiError { description: err.without_url().to_string(), status: 0 };
        let boundary = format!("mightling{:016x}", boundary_seed());
        let safe: String = name.chars().map(|c| if c == '"' || c.is_control() { '_' } else { c }).collect();
        let payload = json!({ "allowed_mentions": { "parse": [] }, "flags": SUPPRESS_EMBEDS, "attachments": [{ "id": 0, "filename": safe }] });
        let mut body = Vec::with_capacity(bytes.len() + 512);
        body.extend_from_slice(format!("--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\nContent-Type: application/json\r\n\r\n{payload}\r\n").as_bytes());
        body.extend_from_slice(format!("--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{safe}\"\r\nContent-Type: application/octet-stream\r\n\r\n").as_bytes());
        body.extend_from_slice(bytes);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
        let response = self
            .http
            .post(format!("{}/channels/{channel}/messages", self.root))
            .header("Authorization", format!("Bot {}", self.token))
            .header("Content-Type", format!("multipart/form-data; boundary={boundary}"))
            .body(body)
            .timeout(Duration::from_secs(300))
            .send()
            .await
            .map_err(failed)?;
        let status = response.status().as_u16();
        let value: Value = response.json().await.unwrap_or(Value::Null);
        if (200..300).contains(&status) {
            return Ok(value);
        }
        let message = value.get("message").and_then(Value::as_str).unwrap_or("Discord refused the file");
        Err(ApiError { description: format!("{message} (HTTP {status})"), status })
    }

    /// Downloads an attachment the user sent: only from Discord's CDN, without the token, at most
    /// [`DOWNLOAD_LIMIT`] bytes.
    pub async fn download(&self, url: &str) -> Result<Vec<u8>, ApiError> {
        let failed = |description: String| ApiError { description, status: 0 };
        if !self.download_prefixes.iter().any(|prefix| url.starts_with(prefix.as_str())) {
            return Err(failed("the file is not on Discord's CDN".to_string()));
        }
        let mut response = self.files.get(url).timeout(Duration::from_secs(120)).send().await.map_err(|err| failed(err.without_url().to_string()))?;
        if !response.status().is_success() {
            return Err(failed(format!("Discord refused the download (HTTP {})", response.status().as_u16())));
        }
        if response.content_length().is_some_and(|length| length > DOWNLOAD_LIMIT) {
            return Err(failed("the file is larger than the bridge downloads".to_string()));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|err| failed(err.without_url().to_string()))? {
            bytes.extend_from_slice(&chunk);
            if bytes.len() as u64 > DOWNLOAD_LIMIT {
                return Err(failed("the file is larger than the bridge downloads".to_string()));
            }
        }
        Ok(bytes)
    }

    /// Answers an interaction (within Discord's 3 seconds).
    async fn answer(&self, id: &str, token: &str, body: Value) -> Result<Value, ApiError> {
        self.request(Method::POST, &format!("/interactions/{id}/{token}/callback"), Some(body)).await
    }
}

/// A multipart boundary that differs between requests.
fn boundary_seed() -> u64 {
    use std::hash::BuildHasher;
    std::collections::hash_map::RandomState::new().hash_one(Instant::now())
}

/// What setup and the adapter refuse or warn about in `GET /applications/@me` (§3.2): an
/// Interactions Endpoint URL would take the interactions off the Gateway, and webhook events
/// would send events to a URL; a public bot can be added by anyone (a warning: it leaves).
pub fn check_application(app: &Value) -> Result<Vec<String>, String> {
    if app.get("interactions_endpoint_url").and_then(Value::as_str).is_some_and(|url| !url.trim().is_empty()) {
        return Err("the application has an Interactions Endpoint URL; clear it in the Developer Portal (General Information) so interactions come over the Gateway".to_string());
    }
    if app.get("event_webhooks_status").and_then(Value::as_u64) == Some(2) {
        return Err("the application has webhook events on; switch them off in the Developer Portal (Webhooks)".to_string());
    }
    let mut warnings = Vec::new();
    if app.get("bot_public") == Some(&Value::Bool(true)) {
        warnings.push("Public Bot is on: anyone can add the bot to a server (it leaves at once). Switch it off in the Developer Portal (Bot).".to_string());
    }
    Ok(warnings)
}

/// The slash commands, each for the bot's DM alone (`contexts: [BOT_DM]`); `/app` only when a
/// front is set.
pub fn commands(front: bool) -> Value {
    let command = |name: &str, description: &str, option: Option<(&str, &str, u64, bool)>| {
        let mut command = json!({ "name": name, "description": description, "type": 1, "integration_types": [0, 1], "contexts": [BOT_DM] });
        if let Some((option, description, kind, required)) = option {
            command["options"] = json!([{ "name": option, "description": description, "type": kind, "required": required }]);
        }
        command
    };
    let mut list = vec![
        command("new", "Start a new conversation", None),
        command("stop", "Stop the running answer", None),
        command("threads", "List recent conversations", None),
        command("use", "Continue a listed conversation", Some(("n", "Its number in /threads", 4, true))),
        command("status", "What the machine is doing", None),
        command("night", "Queue a task for Night Shift", Some(("task", "What to do overnight", 3, true))),
        command("help", "What this bot does", None),
        command("pair", "Pair with a code from `ling chat discord pair`", Some(("code", "The pairing code", 3, true))),
    ];
    if front {
        list.push(command("app", "Open Mightling in the browser", None));
    }
    Value::Array(list)
}

/// The link that installs the bot to the owner's own account (user install, no server; D3).
pub fn install_url(application_id: &str) -> String {
    format!("https://discord.com/oauth2/authorize?client_id={application_id}&integration_type=1&scope=applications.commands")
}

/// A slash command as the text the hub reads (`/use 2`, `/night …`).
fn command_text(data: &Value) -> Option<String> {
    let name = data.get("name").and_then(Value::as_str)?;
    let mut text = format!("/{name}");
    for option in data.get("options").and_then(Value::as_array).into_iter().flatten() {
        let value = match option.get("value") {
            Some(Value::String(value)) => value.trim().to_string(),
            Some(Value::Number(value)) => value.to_string(),
            _ => continue,
        };
        if !value.is_empty() {
            text.push(' ');
            text.push_str(&value);
        }
    }
    Some(text)
}

/// A message body: no mention pings anyone, no link is previewed.
fn message(content: &str) -> Value {
    json!({ "content": content, "allowed_mentions": { "parse": [] }, "flags": SUPPRESS_EMBEDS })
}

fn link_button(label: &str, url: &str) -> Value {
    json!({ "type": 2, "style": 5, "label": label, "url": url })
}

fn row(buttons: Vec<Value>) -> Value {
    json!({ "type": 1, "components": buttons })
}

/// A plain text message with buttons under it.
fn with_buttons(content: &str, buttons: Vec<Value>) -> Value {
    let mut body = message(content);
    if !buttons.is_empty() {
        body["components"] = json!([row(buttons)]);
    }
    body
}

/// Markdown to Discord's Markdown, through blocks.rs's parser: everything the model wrote that
/// Discord would read as markup is escaped, so only the structure the parser found is styled.
pub fn to_discord(markdown: &str) -> String {
    blocks::to_blocks(markdown).iter().map(block).filter(|text| !text.is_empty()).collect::<Vec<_>>().join("\n\n")
}

fn block(value: &Value) -> String {
    let text = value.get("text").cloned().unwrap_or_default();
    match value.get("type").and_then(Value::as_str).unwrap_or_default() {
        "heading" => {
            let size = value.get("size").and_then(Value::as_u64).unwrap_or(1).clamp(1, 3) as usize;
            format!("{} {}", "#".repeat(size), rich(&text).replace('\n', " "))
        }
        "pre" => {
            // Only a plain language name: a backtick or a space in it would end or change the fence.
            let language: String = value.get("language").and_then(Value::as_str).unwrap_or_default().chars().filter(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '_' | '#')).take(20).collect();
            format!("```{language}\n{}\n```", fenced(text.as_str().unwrap_or_default()))
        }
        "divider" => "──────────".to_string(),
        "table" => table(value),
        "blockquote" => {
            let inner = value.get("blocks").and_then(Value::as_array).map(|blocks| blocks.iter().map(block).collect::<Vec<_>>().join("\n\n")).unwrap_or_default();
            inner.lines().map(|line| if line.is_empty() { ">".to_string() } else { format!("> {line}") }).collect::<Vec<_>>().join("\n")
        }
        "list" => {
            let items = value.get("items").and_then(Value::as_array).cloned().unwrap_or_default();
            let mut lines = Vec::new();
            for item in &items {
                let marker = match item.get("value").and_then(Value::as_u64) {
                    Some(number) => format!("{number}."),
                    None => "-".to_string(),
                };
                let body = item.get("blocks").and_then(Value::as_array).map(|blocks| blocks.iter().map(block).collect::<Vec<_>>().join("\n")).unwrap_or_default();
                let pad = " ".repeat(marker.chars().count() + 1);
                for (n, line) in body.lines().enumerate() {
                    lines.push(match n {
                        0 => format!("{marker} {line}"),
                        _ if line.is_empty() => String::new(),
                        _ => format!("{pad}{line}"),
                    });
                }
                if body.is_empty() {
                    lines.push(marker);
                }
            }
            lines.join("\n")
        }
        _ => line_starts(&rich(&text)),
    }
}

/// Code inside a fence: a fence in the code cannot close the block.
fn fenced(code: &str) -> String {
    code.replace("```", "`\u{200b}``")
}

/// A table as a padded text block (Discord draws none).
fn table(value: &Value) -> String {
    let rows: Vec<Vec<String>> = value
        .get("cells")
        .and_then(Value::as_array)
        .map(|rows| rows.iter().map(|row| row.as_array().map(|cells| cells.iter().map(|cell| plain(cell.get("text").unwrap_or(&Value::Null)).replace('\n', " ")).collect()).unwrap_or_default()).collect())
        .unwrap_or_default();
    let columns = rows.iter().map(Vec::len).max().unwrap_or(0);
    let widths: Vec<usize> = (0..columns).map(|c| rows.iter().filter_map(|row| row.get(c)).map(|cell| cell.chars().count()).max().unwrap_or(0)).collect();
    let mut lines = Vec::new();
    for (n, row) in rows.iter().enumerate() {
        let cells: Vec<String> = (0..columns).map(|c| {
            let cell = row.get(c).map(String::as_str).unwrap_or_default();
            format!("{cell}{}", " ".repeat(widths[c].saturating_sub(cell.chars().count())))
        }).collect();
        lines.push(cells.join(" | ").trim_end().to_string());
        if n == 0 {
            lines.push(widths.iter().map(|w| "-".repeat(*w)).collect::<Vec<_>>().join("-+-"));
        }
    }
    format!("```\n{}\n```", fenced(&lines.join("\n")))
}

/// Rich text's characters with no styling (inside a code block).
fn plain(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts.iter().map(plain).collect(),
        Value::Object(object) => {
            let text = plain(object.get("text").unwrap_or(&Value::Null));
            match object.get("url").and_then(Value::as_str) {
                Some(url) if object.get("type").and_then(Value::as_str) == Some("url") => format!("{text} ({url})"),
                _ => text,
            }
        }
        _ => String::new(),
    }
}

/// Rich text (blocks.rs's `RichText`) as Discord Markdown.
fn rich(value: &Value) -> String {
    match value {
        Value::String(text) => escape(text),
        Value::Array(parts) => parts.iter().map(rich).collect(),
        Value::Object(object) => {
            let inner = object.get("text").unwrap_or(&Value::Null);
            match object.get("type").and_then(Value::as_str).unwrap_or_default() {
                "bold" => format!("**{}**", rich(inner)),
                "italic" => format!("*{}*", rich(inner)),
                "strikethrough" => format!("~~{}~~", rich(inner)),
                "code" => code_span(&plain(inner)),
                "url" => {
                    let url = object.get("url").and_then(Value::as_str).unwrap_or_default();
                    if url.contains(['<', '>']) {
                        format!("{} ({})", rich(inner), escape(url))
                    } else {
                        format!("{} (<{url}>)", rich(inner))
                    }
                }
                _ => rich(inner),
            }
        }
        _ => String::new(),
    }
}

fn code_span(code: &str) -> String {
    let code = code.replace('\n', " ");
    if code.contains('`') { format!("`` {} ``", code.replace("``", "`\u{200b}`")) } else { format!("`{code}`") }
}

/// Plain text with every character Discord reads as markup escaped, a mass mention broken, and a
/// bare address wrapped in `<…>` (no preview, nothing escaped inside it).
pub fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 16);
    let mut rest = text;
    while !rest.is_empty() {
        let next_url = ["https://", "http://"].iter().filter_map(|scheme| rest.find(scheme)).min();
        let (before, url) = match next_url {
            Some(at) => (&rest[..at], Some(at)),
            None => (rest, None),
        };
        for c in before.chars() {
            if matches!(c, '\\' | '*' | '_' | '~' | '`' | '|' | '<' | '>' | '[' | ']' | '#') {
                out.push('\\');
            }
            out.push(c);
        }
        let Some(at) = url else { break };
        let tail = &rest[at..];
        let end = tail.find(|c: char| c.is_whitespace() || c == '<' || c == '>').unwrap_or(tail.len());
        let mut address = &tail[..end];
        while let Some(stripped) = address.strip_suffix(['.', ',', ';', ':', '!', '?', ')', '\'', '"']) {
            address = stripped;
        }
        if address.len() <= "https://".len() {
            out.push_str(&tail[..1]);
            rest = &tail[1..];
            continue;
        }
        out.push('<');
        out.push_str(address);
        out.push('>');
        rest = &tail[address.len()..];
    }
    out.replace("@everyone", "@\u{200b}everyone").replace("@here", "@\u{200b}here")
}

/// A paragraph's lines that Discord would read as a list item or a quote are escaped.
fn line_starts(text: &str) -> String {
    text.lines()
        .map(|line| {
            if line.starts_with(['-', '+']) {
                return format!("\\{line}");
            }
            let digits = line.chars().take_while(char::is_ascii_digit).count();
            if digits > 0 && line[digits..].starts_with(". ") {
                return format!("{}\\{}", &line[..digits], &line[digits..]);
            }
            line.to_string()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Text cut to `limit` characters, with an ellipsis.
fn cut(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_string();
    }
    let mut cut: String = text.chars().take(limit.saturating_sub(1)).collect();
    cut.push('…');
    cut
}

/// The chat key of a Discord DM channel.
pub fn chat_key(channel: &str) -> String {
    format!("discord:{channel}")
}

/// A command's deferred answer waiting for the hub's reply (§3.4).
#[derive(Clone)]
struct PendingAnswer {
    token: String,
    at: Instant,
}

type Pending = Arc<Mutex<HashMap<String, PendingAnswer>>>;

/// The events the Gateway passes on: DMs, interactions and server joins.
pub struct Events {
    api: DiscordApi,
    dir: PathBuf,
    hub: mpsc::UnboundedSender<Inbound>,
    pending: Pending,
    front_url: Option<String>,
    /// The one server the bot stays in, when the owner shares one with it (D3's fallback).
    home_server: Option<String>,
    wrong_codes: u32,
    /// When each stranger was last told the bot is private.
    strangers: HashMap<String, Instant>,
}

impl Events {
    pub fn new(api: DiscordApi, dir: PathBuf, hub: mpsc::UnboundedSender<Inbound>, front_url: Option<String>) -> Events {
        Events { api, dir, hub, pending: Pending::default(), front_url, home_server: None, wrong_codes: 0, strangers: HashMap::new() }
    }

    pub fn with_home_server(mut self, home_server: Option<String>) -> Events {
        self.home_server = home_server;
        self
    }

    fn with_pending(mut self, pending: Pending) -> Events {
        self.pending = pending;
        self
    }

    pub async fn run(mut self, mut events: mpsc::UnboundedReceiver<(String, Value)>) {
        while let Some((name, data)) = events.recv().await {
            self.handle(&name, &data).await;
        }
    }

    pub async fn handle(&mut self, name: &str, data: &Value) {
        match name {
            "MESSAGE_CREATE" => self.on_message(data).await,
            "INTERACTION_CREATE" => self.on_interaction(data).await,
            "GUILD_CREATE" => {
                // DM-only: every server the bot is added to is left at once (§1), but the owner's own.
                if let Some(id) = data.get("id").and_then(Value::as_str).filter(|id| is_snowflake(id))
                    && self.home_server.as_deref() != Some(id)
                {
                    match self.api.request(Method::DELETE, &format!("/users/@me/guilds/{id}"), None).await {
                        Ok(_) => eprintln!("ling chat: Discord: left server {id}"),
                        Err(err) => eprintln!("ling chat: Discord: could not leave server {id} ({err})"),
                    }
                }
            }
            _ => {}
        }
    }

    fn paired(&self, user: &str, channel: &str) -> bool {
        DiscordConfig::load(&self.dir).is_some_and(|config| config.users.get(user).is_some_and(|dm| dm == channel))
    }

    fn is_known(&self, user: &str) -> bool {
        DiscordConfig::load(&self.dir).is_some_and(|config| config.users.contains_key(user))
    }

    async fn on_message(&mut self, data: &Value) {
        // A DM has no server; a bot (this one included) and a system message are never read.
        if data.get("guild_id").is_some_and(|id| !id.is_null()) || data.pointer("/author/bot") == Some(&Value::Bool(true)) || data.pointer("/author/system") == Some(&Value::Bool(true)) {
            return;
        }
        if !matches!(data.get("type").and_then(Value::as_u64), Some(0 | 19)) {
            return;
        }
        let (Some(user), Some(channel)) = (data.pointer("/author/id").and_then(Value::as_str), data.get("channel_id").and_then(Value::as_str)) else { return };
        if !is_snowflake(user) || !is_snowflake(channel) {
            return;
        }
        let text = data.get("content").and_then(Value::as_str).unwrap_or_default().trim().to_string();
        if let Some(code) = text.strip_prefix("/pair").filter(|rest| rest.is_empty() || rest.starts_with(' ')) {
            let reply = self.pair(user, channel, code.trim()).await;
            let _ = self.api.send(channel, message(&escape(&reply))).await;
            return;
        }
        if !self.paired(user, channel) {
            self.tell_stranger(user, channel).await;
            return;
        }
        let key = chat_key(channel);
        let mut dropped = Vec::new();
        if data.get("sticker_items").and_then(Value::as_array).is_some_and(|items| !items.is_empty()) {
            dropped.push("a sticker".to_string());
        }
        if data.get("poll").is_some_and(|poll| !poll.is_null()) {
            dropped.push("a poll".to_string());
        }
        if data.get("message_snapshots").and_then(Value::as_array).is_some_and(|items| !items.is_empty()) {
            dropped.push("a forwarded message".to_string());
        }
        let files = data.get("attachments").and_then(Value::as_array).cloned().unwrap_or_default();
        if files.is_empty() && dropped.is_empty() {
            if !text.is_empty() {
                let _ = self.hub.send(Inbound::Text { chat: key, text });
            }
            return;
        }
        let mut attachments = Vec::new();
        for file in &files {
            let name = file.get("filename").and_then(Value::as_str).unwrap_or("file").to_string();
            let mime = file.get("content_type").and_then(Value::as_str).unwrap_or("application/octet-stream").to_string();
            if file.get("size").and_then(Value::as_u64).is_some_and(|size| size > DOWNLOAD_LIMIT) {
                dropped.push(format!("{name} (over {} MB)", DOWNLOAD_LIMIT / 1024 / 1024));
                continue;
            }
            let Some(url) = file.get("url").and_then(Value::as_str) else { continue };
            match self.api.download(url).await {
                Ok(bytes) => attachments.push(Media { name, mime, bytes }),
                Err(err) => {
                    let _ = self.api.send(channel, message(&escape(&format!("I couldn't download {name} from Discord ({err}); send it again.")))).await;
                }
            }
        }
        let _ = self.hub.send(Inbound::Media { chat: key, text, attachments, dropped });
    }

    async fn tell_stranger(&mut self, user: &str, channel: &str) {
        let now = Instant::now();
        if self.strangers.get(user).is_some_and(|at| now.duration_since(*at) < UNPAIRED_REPLY_EVERY) {
            return;
        }
        self.strangers.insert(user.to_string(), now);
        let _ = self.api.send(channel, message(UNPAIRED_REPLY)).await;
    }

    async fn on_interaction(&mut self, data: &Value) {
        // Both go into a callback path: an id is digits, a token is URL-safe text.
        let token = data.get("token").and_then(Value::as_str).filter(|token| !token.is_empty() && token.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.')));
        let (Some(id), Some(token)) = (data.get("id").and_then(Value::as_str).filter(|id| is_snowflake(id)), token) else { return };
        let ephemeral = |text: &str| json!({ "type": 4, "data": { "content": escape(text), "flags": EPHEMERAL, "allowed_mentions": { "parse": [] } } });
        // Only the bot's own DM: no server, no other person's DM.
        let in_dm = data.get("context").and_then(Value::as_u64) == Some(BOT_DM) && data.get("guild_id").is_none_or(Value::is_null);
        let user = data.pointer("/user/id").and_then(Value::as_str).filter(|user| is_snowflake(user));
        let channel = data.get("channel_id").and_then(Value::as_str).or_else(|| data.pointer("/channel/id").and_then(Value::as_str)).filter(|channel| is_snowflake(channel));
        let (Some(user), Some(channel), true) = (user, channel, in_dm) else {
            let _ = self.api.answer(id, token, ephemeral(UNPAIRED_REPLY)).await;
            return;
        };
        let kind = data.get("type").and_then(Value::as_u64).unwrap_or_default();
        let command = data.get("data").cloned().unwrap_or_default();
        if kind == 2 && command.get("name").and_then(Value::as_str) == Some("pair") {
            let code = command_text(&command).unwrap_or_default();
            let reply = self.pair(user, channel, code.trim_start_matches("/pair").trim()).await;
            let _ = self.api.answer(id, token, ephemeral(&reply)).await;
            return;
        }
        if !self.paired(user, channel) {
            let _ = self.api.answer(id, token, ephemeral(UNPAIRED_REPLY)).await;
            return;
        }
        let key = chat_key(channel);
        match kind {
            2 => {
                let Some(text) = command_text(&command) else { return };
                if text == "/app" {
                    let body = match &self.front_url {
                        Some(front) => json!({ "type": 4, "data": with_buttons("Mightling on your own network:", vec![link_button("Open Mightling", front)]) }),
                        None => ephemeral("No address is set for the app: `[chat] discord_web_url` in the machine's config."),
                    };
                    let _ = self.api.answer(id, token, body).await;
                    return;
                }
                // "Thinking…" now; the hub's first reply replaces it (§3.4).
                if self.api.answer(id, token, json!({ "type": 5 })).await.is_ok() {
                    self.pending.lock().await.insert(key.clone(), PendingAnswer { token: token.to_string(), at: Instant::now() });
                }
                let _ = self.hub.send(Inbound::Text { chat: key, text });
            }
            3 => {
                let custom_id = command.get("custom_id").and_then(Value::as_str).unwrap_or_default();
                // The press is acknowledged by disabling the message's buttons (link buttons stay).
                let components = data.pointer("/message/components").cloned().map(disable_buttons).unwrap_or_else(|| json!([]));
                let _ = self.api.answer(id, token, json!({ "type": 7, "data": { "components": components } })).await;
                if custom_id == STOP_ID {
                    let _ = self.hub.send(Inbound::Stop { chat: key });
                } else if let Some((approval, choice)) = approval_callback(custom_id) {
                    let _ = self.hub.send(Inbound::Answer { chat: key, approval, choice });
                }
            }
            _ => {}
        }
    }

    /// `/pair <code>`, typed or as a command: the reply to show.
    async fn pair(&mut self, user: &str, channel: &str, code: &str) -> String {
        if self.is_known(user) {
            return "This Discord account is already paired.".to_string();
        }
        if !code.is_empty() && store::take_discord_pairing_code(&self.dir, code) {
            self.wrong_codes = 0;
            let Some(mut config) = DiscordConfig::load(&self.dir) else {
                return "Pairing failed: the bridge is not set up.".to_string();
            };
            config.users.insert(user.to_string(), channel.to_string());
            if config.save(&self.dir).is_err() {
                return "Pairing failed: the bridge could not save it.".to_string();
            }
            self.strangers.remove(user);
            return format!("Paired. Send a message to start.\n\n{HELP}");
        }
        self.wrong_codes += 1;
        if self.wrong_codes >= PAIRING_ATTEMPTS {
            store::withdraw_discord_pairing_codes(&self.dir);
            self.wrong_codes = 0;
        }
        "That code is not valid. Run `ling chat discord pair` on the machine for a new one.".to_string()
    }
}

/// A message's component rows with every button that is not a link disabled.
fn disable_buttons(mut rows: Value) -> Value {
    for row in rows.as_array_mut().into_iter().flatten() {
        for button in row.get_mut("components").and_then(Value::as_array_mut).into_iter().flatten() {
            if button.get("style").and_then(Value::as_u64) != Some(5) {
                button["disabled"] = json!(true);
            }
        }
    }
    rows
}

/// A Discord id: digits only, so it can go into a path.
pub fn is_snowflake(id: &str) -> bool {
    !id.is_empty() && id.len() <= 20 && id.bytes().all(|b| b.is_ascii_digit())
}

/// A running turn's status line (§3.7).
struct TurnStatus {
    message: Option<String>,
    started: Instant,
    edited: Instant,
    typed: Instant,
}

/// Renders the hub's output into DMs.
pub struct Renderer {
    api: DiscordApi,
    application_id: String,
    front_url: Option<String>,
    status_interval: Duration,
    pending: Pending,
    turns: HashMap<String, TurnStatus>,
    /// Each open approval's channel and message.
    approvals: HashMap<String, (String, String)>,
}

impl Renderer {
    pub fn new(api: DiscordApi, application_id: &str) -> Renderer {
        Renderer {
            api,
            application_id: application_id.to_string(),
            front_url: None,
            status_interval: Duration::from_secs(15),
            pending: Pending::default(),
            turns: HashMap::new(),
            approvals: HashMap::new(),
        }
    }

    pub fn with_front_url(mut self, front_url: Option<String>) -> Renderer {
        self.front_url = front_url;
        self
    }

    pub fn with_status_interval(mut self, interval: Duration) -> Renderer {
        self.status_interval = interval;
        self
    }

    fn with_pending(mut self, pending: Pending) -> Renderer {
        self.pending = pending;
        self
    }

    pub async fn run(mut self, mut outbound: mpsc::UnboundedReceiver<Outbound>) {
        let mut tick = tokio::time::interval(Duration::from_secs(1));
        loop {
            tokio::select! {
                out = outbound.recv() => match out {
                    Some(out) => self.render(out).await,
                    None => return,
                },
                _ = tick.tick() => self.tick().await,
            }
        }
    }

    /// Typing renewed and the status line's time updated; unused "thinking…" answers removed.
    pub async fn tick(&mut self) {
        let now = Instant::now();
        let chats: Vec<String> = self.turns.keys().cloned().collect();
        for chat in chats {
            let channel = chat.trim_start_matches("discord:").to_string();
            let Some(turn) = self.turns.get_mut(&chat) else { continue };
            if now.duration_since(turn.typed) >= TYPING_EVERY {
                turn.typed = now;
                let _ = self.api.request(Method::POST, &format!("/channels/{channel}/typing"), None).await;
            }
            if let Some(id) = turn.message.clone()
                && now.duration_since(turn.edited) >= self.status_interval
            {
                turn.edited = now;
                let body = Renderer::status_body(&self.front_url, &format!("Working on it… {}", elapsed(now.duration_since(turn.started))));
                let _ = self.api.request(Method::PATCH, &format!("/channels/{channel}/messages/{id}"), Some(body)).await;
            }
        }
        let stale: Vec<(String, String)> = {
            let mut pending = self.pending.lock().await;
            let stale: Vec<(String, String)> = pending.iter().filter(|(_, p)| now.duration_since(p.at) >= PENDING_UNUSED).map(|(k, p)| (k.clone(), p.token.clone())).collect();
            for (key, _) in &stale {
                pending.remove(key);
            }
            stale
        };
        for (_, token) in stale {
            let _ = self.api.request(Method::DELETE, &format!("/webhooks/{}/{token}/messages/@original", self.application_id), None).await;
        }
    }

    fn status_body(front_url: &Option<String>, text: &str) -> Value {
        let mut buttons = vec![json!({ "type": 2, "style": 4, "label": "Stop", "custom_id": STOP_ID })];
        if let Some(front) = front_url {
            buttons.push(link_button("Open Mightling", front));
        }
        with_buttons(text, buttons)
    }

    async fn end_turn(&mut self, chat: &str, channel: &str) {
        if let Some(turn) = self.turns.remove(chat)
            && let Some(id) = turn.message
        {
            let _ = self.api.request(Method::DELETE, &format!("/channels/{channel}/messages/{id}"), None).await;
        }
    }

    /// The command's "thinking…" answer, when one waits and is still valid.
    async fn take_pending(&self, chat: &str) -> Option<String> {
        let pending = self.pending.lock().await.remove(chat)?;
        (pending.at.elapsed() < INTERACTION_VALID).then_some(pending.token)
    }

    /// Sends a message, as the waiting command's answer when there is one.
    async fn send(&self, chat: &str, channel: &str, body: Value) -> Result<Value, ApiError> {
        if let Some(token) = self.take_pending(chat).await {
            let path = format!("/webhooks/{}/{token}/messages/@original", self.application_id);
            if let Ok(sent) = self.api.request(Method::PATCH, &path, Some(body.clone())).await {
                return Ok(sent);
            }
        }
        self.api.send(channel, body).await
    }

    fn open_url(&self, repo: &RepoName, app_thread: Option<&AppThreadId>, approval: bool) -> Option<String> {
        let front = self.front_url.as_deref()?;
        match app_thread {
            Some(tid) => ling_web_server::telegram_app::open_thread_url(front, tid.as_str(), approval),
            None => ling_web_server::telegram_app::open_repo_url(front, repo.as_str()),
        }
        .filter(|url| url.len() <= 512)
    }

    pub async fn render(&mut self, out: Outbound) {
        let chat = out.chat().to_string();
        let Some(channel) = chat.strip_prefix("discord:").filter(|channel| is_snowflake(channel)).map(str::to_string) else { return };
        match out {
            Outbound::Draft { .. } => {
                if self.turns.contains_key(&chat) {
                    return;
                }
                let now = Instant::now();
                let _ = self.api.request(Method::POST, &format!("/channels/{channel}/typing"), None).await;
                let sent = self.api.send(&channel, Renderer::status_body(&self.front_url, "Working on it…")).await;
                let message = sent.ok().and_then(|sent| sent.get("id").and_then(Value::as_str).map(str::to_string));
                self.turns.insert(chat, TurnStatus { message, started: now, edited: now, typed: now });
            }
            Outbound::Message { markdown, .. } => {
                self.end_turn(&chat, &channel).await;
                let text = to_discord(&markdown);
                for part in render::split(&text, MESSAGE_LIMIT) {
                    let _ = self.send(&chat, &channel, message(&part)).await;
                }
            }
            Outbound::Notice { text, .. } => {
                for part in render::split(&escape(&text), MESSAGE_LIMIT) {
                    let _ = self.send(&chat, &channel, message(&part)).await;
                }
            }
            Outbound::RepoNotice { notice, .. } => {
                let buttons = self.open_url(&notice.repo, notice.app_thread.as_ref(), notice.is_approval()).map(|url| vec![link_button("Open", &url)]).unwrap_or_default();
                let _ = self.api.send(&channel, with_buttons(&escape(&notice.text()), buttons)).await;
            }
            Outbound::RepoTypedRefusal { repo, .. } => {
                let buttons = self.open_url(&repo, None, false).map(|url| vec![link_button("Open", &url)]).unwrap_or_default();
                let _ = self.send(&chat, &channel, with_buttons("Repo work happens in the app.", buttons)).await;
            }
            Outbound::Approval { id, text, options, .. } => {
                let mut body = message(&to_discord(&cut(&text, APPROVAL_TEXT_LIMIT)));
                body["components"] = approval_rows(&id, &options);
                match self.api.send(&channel, body).await {
                    Ok(sent) => {
                        if let Some(message) = sent.get("id").and_then(Value::as_str) {
                            self.approvals.insert(id, (channel, message.to_string()));
                        }
                    }
                    Err(err) => eprintln!("ling chat: Discord refused an approval ({err})"),
                }
            }
            Outbound::ApprovalClosed { id, outcome, .. } => {
                if let Some((channel, message)) = self.approvals.remove(&id) {
                    let body = json!({ "components": [row(vec![json!({ "type": 2, "style": 2, "label": cut(&outcome, 80), "custom_id": format!("done:{id}"), "disabled": true })])] });
                    let _ = self.api.request(Method::PATCH, &format!("/channels/{channel}/messages/{message}"), Some(body)).await;
                }
            }
            Outbound::File { name, bytes, .. } => {
                if let Err(err) = self.api.upload(&channel, &name, &bytes).await {
                    let _ = self.api.send(&channel, message(&escape(&format!("Discord refused the file {name} ({err})")))).await;
                }
            }
            Outbound::ThreadNamed { .. } => {}
            Outbound::TurnEnded { .. } => self.end_turn(&chat, &channel).await,
        }
    }
}

/// An approval's buttons: the first answer green, the last (decline) red, five to a row.
fn approval_rows(id: &str, options: &[String]) -> Value {
    let last = options.len().saturating_sub(1);
    let buttons: Vec<Value> = options
        .iter()
        .enumerate()
        .take(25)
        .map(|(n, label)| {
            let style = match n {
                0 => 3,
                _ if n == last => 4,
                _ => 2,
            };
            json!({ "type": 2, "style": style, "label": cut(label, 80), "custom_id": format!("a:{id}:{n}") })
        })
        .collect();
    Value::Array(buttons.chunks(5).map(|chunk| row(chunk.to_vec())).collect())
}

/// "42 s", "3 min 05 s".
fn elapsed(duration: Duration) -> String {
    let seconds = duration.as_secs();
    if seconds < 60 { format!("{seconds} s") } else { format!("{} min {:02} s", seconds / 60, seconds % 60) }
}

/// The user's choices for Discord (`[chat] discord_web_url`, `discord_status_interval_s`,
/// `discord_identify_limit`).
#[derive(Clone, Debug)]
pub struct Options {
    /// `ling web`'s `https://` front on the owner's network; none: no `/app`, no link buttons.
    pub front_url: Option<String>,
    pub status_interval: Duration,
    /// Identifies in 24 hours before the adapter stops (Discord resets the token at 1,000).
    pub identify_limit: usize,
    /// `[chat] discord_home_server`: a server shared with the bot only so it can be messaged, which
    /// it does not leave; messages in it are still ignored.
    pub home_server: Option<String>,
}

impl Default for Options {
    fn default() -> Options {
        Options { front_url: None, status_interval: Duration::from_secs(15), identify_limit: 100, home_server: None }
    }
}

/// Checks the token and the application, registers the commands, and starts the Gateway, the
/// event handler and the renderer. Returns the bot's user name.
pub async fn start(
    api: DiscordApi,
    dir: PathBuf,
    hub: mpsc::UnboundedSender<Inbound>,
    outbound: mpsc::UnboundedReceiver<Outbound>,
    options: Options,
) -> Result<String, ApiError> {
    let me = api.request(Method::GET, "/users/@me", None).await?;
    let app = api.request(Method::GET, "/applications/@me", None).await?;
    for warning in check_application(&app).map_err(|description| ApiError { description, status: 0 })? {
        eprintln!("ling chat: Discord: {warning}");
    }
    let application_id = app.get("id").and_then(Value::as_str).filter(|id| is_snowflake(id)).unwrap_or_default().to_string();
    if let Err(err) = api.request(Method::PUT, &format!("/applications/{application_id}/commands"), Some(commands(options.front_url.is_some()))).await {
        eprintln!("ling chat: Discord: the commands were not registered ({err})");
    }
    let pending = Pending::default();
    tokio::spawn(
        Renderer::new(api.clone(), &application_id)
            .with_front_url(options.front_url.clone())
            .with_status_interval(options.status_interval)
            .with_pending(pending.clone())
            .run(outbound),
    );
    let (events_tx, events_rx) = mpsc::unbounded_channel();
    tokio::spawn(Events::new(api.clone(), dir.clone(), hub, options.front_url.clone()).with_home_server(options.home_server.clone()).with_pending(pending).run(events_rx));
    tokio::spawn(crate::discord_gateway::run(api, dir, events_tx, options.identify_limit));
    Ok(me.get("username").and_then(Value::as_str).unwrap_or_default().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markdown_becomes_discord_markdown_with_the_rest_escaped() {
        assert_eq!(to_discord("**bold** and *it* and `x*y`"), "**bold** and *it* and `x*y`");
        assert_eq!(to_discord("a_b *c* 2*3"), "a\\_b *c* 2\\*3");
        assert_eq!(to_discord("# Title\n\ntext"), "# Title\n\ntext");
        assert_eq!(to_discord("#### Deep"), "### Deep");
        assert_eq!(to_discord("```rust\nlet a = 1;\n```"), "```rust\nlet a = 1;\n```");
        assert_eq!(to_discord("- one\n- two"), "- one\n- two");
        assert_eq!(to_discord("1. one\n2. two"), "1. one\n2. two");
        assert_eq!(to_discord("> quoted"), "> quoted");
        assert_eq!(to_discord("see [docs](https://example.org/a_b)"), "see docs (<https://example.org/a_b>)");
        assert_eq!(to_discord("see https://example.org/a_b."), "see <https://example.org/a_b>.");
        assert_eq!(to_discord("[x](javascript:alert(1))"), "x (javascript:alert(1))");
        assert_eq!(to_discord("hi @everyone and @here <@123>"), "hi @\u{200b}everyone and @\u{200b}here \\<@123\\>");
        assert_eq!(to_discord("| a | bb |\n|---|---|\n| ccc | d |"), "```\na   | bb\n----+---\nccc | d\n```");
    }

    #[test]
    fn a_fence_inside_code_cannot_close_the_block() {
        assert_eq!(fenced("a\n```\n@everyone"), "a\n`\u{200b}``\n@everyone");
        let text = to_discord("````` @everyone\nx\n```");
        let opening = text.lines().next().unwrap_or_default();
        assert!(opening.starts_with("```") && opening[3..].chars().all(|c| c.is_ascii_alphanumeric()), "{text}");
        let table = to_discord("| a |\n|---|\n| ``` b |");
        assert!(table.starts_with("```\n") && !table[3..table.len() - 3].contains("```"), "{table}");
    }

    #[test]
    fn the_application_is_checked_before_anything_is_trusted() {
        assert!(check_application(&json!({ "interactions_endpoint_url": "https://example.org/i" })).is_err());
        assert!(check_application(&json!({ "event_webhooks_status": 2 })).is_err());
        assert_eq!(check_application(&json!({ "bot_public": false, "interactions_endpoint_url": null, "event_webhooks_status": 1 })), Ok(vec![]));
        assert_eq!(check_application(&json!({ "bot_public": true })).map(|w| w.len()), Ok(1));
    }

    #[test]
    fn every_command_is_for_the_bots_dm_only() {
        for front in [false, true] {
            let commands = commands(front);
            let names: Vec<&str> = commands.as_array().unwrap().iter().filter_map(|c| c["name"].as_str()).collect();
            assert_eq!(names.contains(&"app"), front);
            assert!(commands.as_array().unwrap().iter().all(|c| c["contexts"] == json!([1])));
        }
        assert_eq!(command_text(&json!({ "name": "use", "options": [{ "name": "n", "type": 4, "value": 2 }] })), Some("/use 2".to_string()));
        assert_eq!(command_text(&json!({ "name": "night", "options": [{ "name": "task", "type": 3, "value": " tidy up " }] })), Some("/night tidy up".to_string()));
    }

    #[test]
    fn approval_buttons_are_colored_and_rowed() {
        let options: Vec<String> = (0..7).map(|n| format!("choice {n}")).collect();
        let rows = approval_rows("ab12", &options);
        assert_eq!(rows.as_array().unwrap().len(), 2);
        assert_eq!(rows[0]["components"][0]["style"], 3);
        assert_eq!(rows[0]["components"][1]["style"], 2);
        assert_eq!(rows[1]["components"][1]["style"], 4);
        assert_eq!(rows[1]["components"][1]["custom_id"], "a:ab12:6");
        assert_eq!(approval_callback("a:ab12:6"), Some(("ab12".to_string(), 6)));
    }

    #[test]
    fn snowflakes_only_go_into_paths() {
        assert!(is_snowflake("123456789012345678"));
        assert!(!is_snowflake("12/../34"));
        assert!(!is_snowflake(""));
    }
}
