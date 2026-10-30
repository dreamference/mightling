//! `ling-search`: web search through the SearXNG instance on this machine.
//!
//! SearXNG queries the upstream engines on this machine's behalf and returns their merged results
//! as JSON, so no query leaves the host addressed to a search company and no API key is involved.
//! If the instance is down this says so plainly instead of falling back to a public engine: a
//! silent fallback would send queries somewhere the operator did not choose.
//!
//! An answer with something in it is kept for an hour and a repeated query is answered from it,
//! and searches that do go out are spaced across the machine (`cache.rs`).

use serde::Serialize;
use serde_json::Value;

use crate::agent;
use crate::airgapped::Level;
use crate::node_locator::Node;

/// Self-hosted SearXNG, published on loopback only. `DREAMFERENCE_SEARXNG_URL` overrides it.
pub const DEFAULT_SEARXNG_URL: &str = "http://127.0.0.1:8888";

/// Printed when the instance cannot be reached: the fix is one command, and the alternative is an
/// agent that quietly believes the web does not exist. It names the command, not a `docker run`
/// line: a container started by hand lands on Docker's default bridge, whose DNS servers are a
/// copy taken at start, and after a reboot on 2026-10-01 that copy was empty.
pub const SEARXNG_START_HINT: &str = "ling-admin searxng start";

/// SearXNG answers HTML, or refuses with 403, unless `json` is among its `search.formats`, which
/// the settings `ling-admin searxng start` writes include.
pub const JSON_FORMAT_HINT: &str = "SearXNG's settings do not enable its JSON API (no json in search.formats): ling-admin searxng start writes ones that do";

/// Said when SearXNG answers but no engine does, or answers with an error. It must not send the
/// model to repair anything: until 2026-10-20 it named `docker restart dreamference-searxng`, an
/// agent ran it, and nothing changed, because the engines were refusing this machine's address
/// upstream. A known page and the user are what is left. The MCP server says the same
/// (`SEARCH_UNAVAILABLE_HINT` in `dreamference/mcp_server/web_tools.py`).
pub const UNAVAILABLE_HINT: &str = "Web search is unavailable for now: the search engines are refusing this machine's queries, which usually passes within hours. Do not try to fix it or restart anything. If you know a page that answers the question, read it with ling-fetch (for example: ling-fetch \"https://en.wikipedia.org/wiki/Lisbon\"); otherwise tell the user that web search is unavailable at the moment.";

/// The categories `--category` accepts, each a SearXNG category of the settings `ling-admin
/// searxng start` writes (`dreamference/chat/searxng_settings.py`), with what it searches. A
/// general search asks the web engines and Wikipedia only, so that package names, papers and
/// videos do not drown it; the others are asked when the question is theirs.
pub const CATEGORIES: &[(&str, &str)] = &[
    ("general", "the web, Wikipedia and Wikidata (the default)"),
    (
        "news",
        "news sites and news search, newest first with --time",
    ),
    (
        "science",
        "papers: arXiv, PubMed, Semantic Scholar, OpenAlex, Crossref, Google Scholar",
    ),
    (
        "it",
        "code and packages: GitHub, GitLab, Stack Overflow, crates.io, npm, PyPI, MDN, wikis",
    ),
    ("map", "places: OpenStreetMap, Photon, Apple Maps"),
    ("images", "images"),
    ("videos", "videos: YouTube, PeerTube, Dailymotion, archives"),
    ("music", "music, lyrics, radio, podcasts"),
    (
        "files",
        "apps: F-Droid, Google Play, the App Store, APKMirror",
    ),
    ("social", "Mastodon, Lemmy, forums"),
    ("books", "books: Open Library, Goodreads"),
    ("dictionaries", "Wiktionary, Wordnik, Duden, Etymonline"),
    (
        "wikimedia",
        "Wikivoyage, Wikiquote, Wikibooks, Wikisource, Wikiversity",
    ),
    ("movies", "films: IMDb, Rotten Tomatoes"),
    ("weather", "weather"),
];

/// The time ranges `--time` accepts, as SearXNG names them. Engines that cannot filter by time
/// are left out of a search that asks for one.
pub const TIME_RANGES: &[&str] = &["day", "week", "month", "year"];

/// Asks SearXNG to detect the query's language, so a question in Portuguese or Ukrainian reaches
/// that language's Wikipedia and the engines' results in it.
pub const AUTO_LANGUAGE: &str = "auto";

/// What a search asks SearXNG for besides the query.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SearchOptions {
    /// A SearXNG category (`social` is sent as `social media`).
    pub category: String,
    /// `day`, `week`, `month` or `year`; none for any time.
    pub time_range: Option<String>,
    /// A language code (`pt`, `uk`, `de-CH`) or `auto`.
    pub language: String,
}

impl Default for SearchOptions {
    fn default() -> Self {
        SearchOptions {
            category: "general".to_string(),
            time_range: None,
            language: AUTO_LANGUAGE.to_string(),
        }
    }
}

impl SearchOptions {
    /// Checks the options a person or a model typed.
    ///
    /// Returns the error to print for a category or time range not in the lists, or a language
    /// that is not a language code.
    pub fn new(
        category: Option<&str>,
        time_range: Option<&str>,
        language: Option<&str>,
    ) -> Result<SearchOptions, String> {
        let mut options = SearchOptions::default();
        if let Some(category) = category.map(|value| value.trim().to_lowercase()) {
            let category = if category == "social media" {
                "social".to_string()
            } else {
                category
            };
            if !CATEGORIES.iter().any(|(name, _)| *name == category) {
                let names: Vec<&str> = CATEGORIES.iter().map(|(name, _)| *name).collect();
                return Err(format!(
                    "unknown category {category:?}; one of: {}",
                    names.join(", ")
                ));
            }
            options.category = category;
        }
        if let Some(time_range) = time_range.map(|value| value.trim().to_lowercase()) {
            if !TIME_RANGES.contains(&time_range.as_str()) {
                return Err(format!(
                    "unknown time range {time_range:?}; one of: {}",
                    TIME_RANGES.join(", ")
                ));
            }
            options.time_range = Some(time_range);
        }
        if let Some(language) = language.map(str::trim).filter(|value| !value.is_empty()) {
            let valid = language == AUTO_LANGUAGE
                || language == "all"
                || (language.len() <= 6
                    && language
                        .split('-')
                        .next()
                        .is_some_and(|code| (2..=3).contains(&code.len()))
                    && language
                        .chars()
                        .all(|c| c.is_ascii_alphabetic() || c == '-'));
            if !valid {
                return Err(format!(
                    "unknown language {language:?}; a code such as en, pt, uk, de-CH, or auto"
                ));
            }
            options.language = language.to_string();
        }
        Ok(options)
    }

    /// The category as SearXNG names it.
    pub fn searxng_category(&self) -> &str {
        if self.category == "social" {
            "social media"
        } else {
            &self.category
        }
    }
}

/// One result, in the field order `ling-search --json` has always printed.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct SearchResult {
    pub title: String,
    pub url: String,
    pub snippet: String,
    pub engine: String,
    /// Every engine SearXNG reports for the result; `engine` is only the first of them, which
    /// cannot show that no other engine contributed.
    pub engines: Vec<String>,
    /// When it was published, as the engine says (news, papers, videos); absent when it does not.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub published: Option<String>,
}

/// A successful search.
#[derive(Debug, Serialize, PartialEq)]
pub struct SearchPayload {
    /// The session's air-gap level the search ran at (`off`: at `on` nothing is searched).
    pub airgapped: String,
    pub query: String,
    pub result_count: usize,
    /// SearXNG's direct answers (calculators, definitions, conversions). Strings in older SearXNG,
    /// objects with an `answer` field in newer ones; kept as they came.
    pub answers: Vec<Value>,
    pub results: Vec<SearchResult>,
}

/// A search that produced no results to show, and why.
#[derive(Debug, PartialEq, Eq)]
pub struct SearchError {
    pub error: String,
    pub hint: Option<String>,
}

/// The SearXNG base URL: `DREAMFERENCE_SEARXNG_URL`, then the node's on a client that remembers one
/// (specs/DREAMFERENCE_MIGHTLING_NODE.md §6.2), then loopback.
pub fn searxng_url() -> String {
    searxng_url_from(
        std::env::var("DREAMFERENCE_SEARXNG_URL").ok().as_deref(),
        crate::node_locator::remote_node().as_ref(),
    )
}

/// [`searxng_url`] with its inputs given. A node that does not share its SearXNG leaves the
/// default in place; the error then names the node (see [`start_hint`]).
pub fn searxng_url_from(configured: Option<&str>, node: Option<&Node>) -> String {
    if let Some(url) = configured.filter(|url| !url.trim().is_empty()) {
        return url.to_string();
    }
    node.and_then(Node::search_url)
        .unwrap_or_else(|| DEFAULT_SEARXNG_URL.to_string())
}

/// What to say when SearXNG does not answer. On a node, the command that starts it. On a client,
/// the same command and where to type it: web search runs on the node, so nothing typed on the
/// client can start it.
pub fn start_hint(node: Option<&Node>) -> String {
    match node {
        None => format!("Start it with: {SEARXNG_START_HINT}"),
        Some(node) if node.search_port.is_none() => format!(
            "Web search runs on the Mightling node ({}), which does not share its SearXNG. On the node: {SEARXNG_START_HINT}, then ling-admin node enable",
            node_label(node)
        ),
        Some(node) => format!(
            "Web search runs on the Mightling node ({}). On the node: {SEARXNG_START_HINT}",
            node_label(node)
        ),
    }
}

fn node_label(node: &Node) -> &str {
    if node.name.is_empty() {
        &node.address
    } else {
        &node.name
    }
}

/// Searches the web through SearXNG.
///
/// One difference from the Python version: an HTTP error status is reported as the status SearXNG
/// answered, with the `search.formats` hint for 403 (its answer to a JSON request when the format
/// is off), rather than as "unreachable" with the start command, which sent people to start an
/// instance that was running.
///
/// At `on` nothing is sent; the binary refuses before calling this, and so does this function.
pub fn search(
    base_url: &str,
    query: &str,
    max_results: i64,
    level: Level,
) -> Result<SearchPayload, SearchError> {
    search_from(
        base_url,
        query,
        max_results,
        level,
        crate::node_locator::remote_node().as_ref(),
    )
}

/// [`search`], told which node the instance is on (`None`: this machine), for the hints.
pub fn search_from(
    base_url: &str,
    query: &str,
    max_results: i64,
    level: Level,
    node: Option<&Node>,
) -> Result<SearchPayload, SearchError> {
    search_with(
        base_url,
        query,
        max_results,
        level,
        node,
        &SearchOptions::default(),
    )
}

/// [`search_from`] with a category, a time range and a language.
pub fn search_with(
    base_url: &str,
    query: &str,
    max_results: i64,
    level: Level,
    node: Option<&Node>,
    options: &SearchOptions,
) -> Result<SearchPayload, SearchError> {
    if level == Level::On {
        return Err(SearchError {
            error: crate::AIRGAPPED_ON_MESSAGE.to_string(),
            hint: None,
        });
    }
    if query.trim().is_empty() {
        return Err(SearchError {
            error: "empty query".to_string(),
            hint: None,
        });
    }
    let cache = crate::cache::Cache::open();
    let time_range = options.time_range.as_deref().unwrap_or_default();
    let key = crate::cache::key(
        base_url,
        query,
        &format!("{}|{time_range}", options.searxng_category()),
        &options.language,
    );
    let now = std::time::SystemTime::now();
    if let Some(found) = cache
        .as_ref()
        .and_then(|cache| cache.get(&key, now))
        .and_then(|payload| from_searxng(query, &payload, max_results, level).ok())
    {
        return Ok(found);
    }
    if let Some(cache) = &cache {
        std::thread::sleep(cache.reserve(now));
    }
    let endpoint = format!("{}/search", base_url.trim_end_matches('/'));
    let mut request = agent(None)
        .get(&endpoint)
        .query("q", query)
        .query("format", "json")
        .query("categories", options.searxng_category())
        .query("language", &options.language);
    if !time_range.is_empty() {
        request = request.query("time_range", time_range);
    }
    let response = request.set("Accept", "application/json").call();
    let body = match response {
        Ok(response) => response.into_string(),
        Err(ureq::Error::Status(status, response)) => {
            return Err(SearchError {
                error: format!(
                    "SearXNG at {base_url} answered HTTP {status} {}",
                    response.status_text()
                ),
                hint: if status == 403 {
                    Some(JSON_FORMAT_HINT.to_string())
                } else {
                    Some(UNAVAILABLE_HINT.to_string())
                },
            });
        }
        Err(error) => {
            return Err(SearchError {
                error: format!("SearXNG at {base_url} is unreachable: {error}"),
                hint: Some(start_hint(node)),
            });
        }
    };
    let payload = body
        .ok()
        .and_then(|body| serde_json::from_str::<Value>(&body).ok())
        .filter(Value::is_object)
        .ok_or_else(|| SearchError {
            error: format!("SearXNG at {base_url} did not return JSON"),
            hint: Some(JSON_FORMAT_HINT.to_string()),
        })?;
    let found = from_searxng(query, &payload, max_results, level)?;
    let worth_keeping = found.result_count > 0 || !found.answers.is_empty();
    if let Some(cache) = cache.as_ref().filter(|_| worth_keeping) {
        cache.put(&key, &payload, std::time::SystemTime::now());
    }
    Ok(found)
}

/// The address a result is compared by: without the scheme, `www.`, a trailing slash, the
/// fragment, and tracking parameters (`utm_*`, `fbclid`, `gclid`, Bing's `msockid`).
pub fn comparable_url(url: &str) -> String {
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    let rest = rest.split('#').next().unwrap_or_default();
    let (address, query) = rest.split_once('?').unwrap_or((rest, ""));
    let (host, path) = address.split_once('/').unwrap_or((address, ""));
    let host = host.to_lowercase();
    let host = host.strip_prefix("www.").unwrap_or(&host);
    let kept: Vec<&str> = query
        .split('&')
        .filter(|pair| {
            let name = pair.split('=').next().unwrap_or_default().to_lowercase();
            !pair.is_empty()
                && !name.starts_with("utm_")
                && !["fbclid", "gclid", "msockid"].contains(&name.as_str())
        })
        .collect();
    let path = path.trim_end_matches('/');
    let mut comparable = format!("{host}/{path}");
    if !kept.is_empty() {
        comparable.push('?');
        comparable.push_str(&kept.join("&"));
    }
    comparable
}

/// Merges results that are the same page under addresses SearXNG keeps apart (`http` and
/// `https`, with and without `www.`, a tracking parameter), keeping the first, which SearXNG
/// ranked highest, and adding the other's engines to it. SearXNG's order stays: it scores a
/// result by its position at each engine that found it, weighted by the engine's weight, so a page
/// several engines agree on is already ranked above one only a single engine found.
pub fn merge_duplicates(results: impl IntoIterator<Item = SearchResult>) -> Vec<SearchResult> {
    let mut merged: Vec<SearchResult> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for result in results {
        let key = comparable_url(&result.url);
        if let Some(index) = seen.iter().position(|known| *known == key) {
            let kept = &mut merged[index];
            for engine in result
                .engines
                .into_iter()
                .chain(std::iter::once(result.engine))
            {
                if !engine.is_empty() && !kept.engines.contains(&engine) {
                    kept.engines.push(engine);
                }
            }
            if kept.snippet.is_empty() {
                kept.snippet = result.snippet;
            }
            if kept.published.is_none() {
                kept.published = result.published;
            }
            continue;
        }
        seen.push(key);
        merged.push(result);
    }
    merged
}

/// SearXNG's infoboxes as results. Wikipedia and Wikidata answer only with an infobox (a summary
/// of the article), never in `results`: before 2026-10-20 a search that only Wikipedia answered
/// printed nothing.
fn infobox_results(payload: &Value) -> Vec<SearchResult> {
    payload["infoboxes"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|infobox| {
            let id = infobox["id"].as_str().filter(|id| id.starts_with("http"));
            let url = id
                .or_else(|| infobox["urls"][0]["url"].as_str())?
                .to_string();
            let engine = infobox["engine"].as_str().unwrap_or_default().to_string();
            let mut engines: Vec<String> = infobox["engines"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|engine| engine.as_str().map(str::to_string))
                .collect();
            if engines.is_empty() && !engine.is_empty() {
                engines.push(engine.clone());
            }
            Some(SearchResult {
                title: infobox["infobox"].as_str().unwrap_or_default().to_string(),
                url,
                snippet: infobox["content"].as_str().unwrap_or_default().to_string(),
                engine,
                engines,
                published: None,
            })
        })
        .collect()
}

/// Turns SearXNG's JSON into a payload, or into the error for a search every engine failed.
pub fn from_searxng(
    query: &str,
    payload: &Value,
    max_results: i64,
    level: Level,
) -> Result<SearchPayload, SearchError> {
    let limit = max_results.max(1) as usize;
    let text = |item: &Value, key: &str| item[key].as_str().unwrap_or_default().to_string();
    // An infobox comes first: it is the encyclopedia's summary of the subject, and the one thing a
    // search that only Wikipedia answered has. A web result for the same page is not repeated.
    let infoboxes = infobox_results(payload);
    let boxed: Vec<String> = infoboxes
        .iter()
        .take(1)
        .map(|result| result.url.clone())
        .collect();
    let web = payload["results"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|item| {
            !boxed
                .iter()
                .any(|url| item["url"].as_str() == Some(url.as_str()))
        })
        .map(|item| SearchResult {
            title: text(item, "title"),
            url: text(item, "url"),
            snippet: text(item, "content"),
            engine: text(item, "engine"),
            engines: item["engines"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|engine| engine.as_str().map(str::to_string))
                .collect(),
            published: item["publishedDate"]
                .as_str()
                .filter(|date| !date.is_empty())
                .map(|date| date.chars().take(10).collect()),
        });
    let results: Vec<SearchResult> = merge_duplicates(infoboxes.into_iter().take(1).chain(web))
        .into_iter()
        .take(limit)
        .collect();
    let answers: Vec<Value> = payload["answers"].as_array().cloned().unwrap_or_default();
    let failed = payload["unresponsive_engines"]
        .as_array()
        .cloned()
        .unwrap_or_default();

    if results.is_empty() && answers.is_empty() && !failed.is_empty() {
        // Every engine failed, which is not the same as the web having nothing to say. A SearXNG
        // container whose DNS had gone stale once answered every query with zero results, and the
        // agent concluded the topic had no coverage.
        let reasons: Vec<String> = failed
            .iter()
            .map(|engine| match engine.as_array().map(Vec::as_slice) {
                Some([name, reason, ..]) => format!(
                    "{}: {}",
                    name.as_str().unwrap_or_default(),
                    reason.as_str().unwrap_or_default()
                ),
                _ => engine.to_string(),
            })
            .collect();
        return Err(SearchError {
            error: format!(
                "SearXNG could not reach any search engine: {}",
                reasons.join("; ")
            ),
            hint: Some(UNAVAILABLE_HINT.to_string()),
        });
    }
    Ok(SearchPayload {
        airgapped: level.name().to_string(),
        query: query.to_string(),
        result_count: results.len(),
        answers: answers.into_iter().take(3).collect(),
        results,
    })
}

/// The text `ling-search` prints for a payload.
pub fn render_text(payload: &SearchPayload) -> String {
    let mut out = String::new();
    for answer in &payload.answers {
        let answer = match answer {
            Value::String(text) => text.clone(),
            Value::Object(fields) => match fields.get("answer") {
                Some(Value::String(text)) => text.clone(),
                _ => answer.to_string(),
            },
            other => other.to_string(),
        };
        out.push_str(&format!("ANSWER: {answer}\n\n"));
    }
    for (index, result) in payload.results.iter().enumerate() {
        out.push_str(&format!(
            "{}. {}\n   {}\n",
            index + 1,
            result.title,
            result.url
        ));
        if let Some(published) = &result.published {
            out.push_str(&format!("   {published}\n"));
        }
        if !result.snippet.is_empty() {
            out.push_str(&format!("   {}\n", crate::take_chars(&result.snippet, 200)));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn searxng(results: Value, answers: Value, unresponsive: Value) -> Value {
        json!({"query": "q", "results": results, "answers": answers, "unresponsive_engines": unresponsive})
    }

    #[test]
    fn results_are_mapped_and_limited() {
        let payload = searxng(
            json!([
                {"title": "A", "url": "https://a", "content": "about a", "engine": "wikipedia", "score": 1.0},
                {"title": "B", "url": "https://b", "content": "", "engine": "bing"},
                {"title": "C", "url": "https://c"}
            ]),
            json!([]),
            json!([]),
        );
        let found = from_searxng("q", &payload, 2, Level::Off).unwrap();
        assert_eq!(found.result_count, 2);
        assert_eq!(
            found.results[0],
            SearchResult {
                title: "A".into(),
                url: "https://a".into(),
                snippet: "about a".into(),
                engine: "wikipedia".into(),
                engines: vec![],
                published: None,
            }
        );
        assert_eq!(
            from_searxng("q", &payload, 0, Level::Off)
                .unwrap()
                .result_count,
            1
        );
        assert_eq!(
            from_searxng("q", &payload, -5, Level::Off)
                .unwrap()
                .result_count,
            1
        );
    }

    #[test]
    fn every_engine_failing_is_an_error_naming_each_one() {
        let payload = searxng(
            json!([]),
            json!([]),
            json!([
                ["brave", "Suspended: too many requests"],
                ["duckduckgo", "CAPTCHA"]
            ]),
        );
        let error = from_searxng("q", &payload, 5, Level::Off).unwrap_err();
        assert_eq!(
            error.error,
            "SearXNG could not reach any search engine: brave: Suspended: too many requests; duckduckgo: CAPTCHA"
        );
        assert_eq!(error.hint.as_deref(), Some(UNAVAILABLE_HINT));
    }

    fn remote() -> Node {
        Node {
            node: "7c1e".to_string(),
            name: "gx10-1a2b".to_string(),
            address: "192.168.50.105".to_string(),
            model_port: 8000,
            web_port: Some(3000),
            search_port: Some(8888),
            ..Node::default()
        }
    }

    #[test]
    fn a_client_searches_through_its_node_and_a_node_through_itself() {
        // On a node, and on a client that remembers no node, `remote_node()` is None: loopback.
        assert_eq!(searxng_url_from(None, None), DEFAULT_SEARXNG_URL);
        assert_eq!(searxng_url_from(Some("  "), None), DEFAULT_SEARXNG_URL);
        assert_eq!(
            searxng_url_from(None, Some(&remote())),
            "http://192.168.50.105:8888"
        );
        // The override still wins, on either kind of machine.
        assert_eq!(
            searxng_url_from(Some("http://10.0.0.9:1234"), Some(&remote())),
            "http://10.0.0.9:1234"
        );
        // A node that keeps its SearXNG to itself: nothing to connect to but loopback.
        let unshared = Node {
            search_port: None,
            ..remote()
        };
        assert_eq!(searxng_url_from(None, Some(&unshared)), DEFAULT_SEARXNG_URL);
    }

    #[test]
    fn on_a_client_the_hint_says_where_to_type_the_command() {
        assert_eq!(start_hint(None), "Start it with: ling-admin searxng start");
        assert_eq!(
            start_hint(Some(&remote())),
            "Web search runs on the Mightling node (gx10-1a2b). On the node: ling-admin searxng start"
        );
        let unshared = Node {
            search_port: None,
            name: String::new(),
            ..remote()
        };
        assert!(
            start_hint(Some(&unshared))
                .contains("(192.168.50.105), which does not share its SearXNG")
        );
        assert!(start_hint(Some(&unshared)).ends_with("ling-admin node enable"));
        // Nothing tells the model to repair the container: the engines refuse upstream, and a
        // restart changes nothing (2026-10-20).
        assert!(!UNAVAILABLE_HINT.contains("docker") && UNAVAILABLE_HINT.contains("ling-fetch"));
        assert!(UNAVAILABLE_HINT.contains("tell the user"));
    }

    #[test]
    fn an_unreachable_node_is_named_in_the_error() {
        // Port 9 on loopback refuses at once; the hint is the client's, because a node was given.
        let error =
            search_from("http://127.0.0.1:9", "q", 5, Level::Off, Some(&remote())).unwrap_err();
        assert!(error.error.contains("unreachable"), "{error:?}");
        assert_eq!(
            error.hint.as_deref(),
            Some(start_hint(Some(&remote())).as_str())
        );
    }

    #[test]
    fn at_on_nothing_is_sent() {
        // Port 9 would answer "unreachable" if a request were made.
        let error = search("http://127.0.0.1:9", "q", 5, Level::On).unwrap_err();
        assert_eq!(error.error, crate::AIRGAPPED_ON_MESSAGE);
        assert_eq!(error.hint, None);
    }

    #[test]
    fn every_engine_of_a_result_is_kept() {
        let payload = searxng(
            json!([{"title": "A", "url": "https://a", "engine": "duckduckgo", "engines": ["duckduckgo", "brave"]}]),
            json!([]),
            json!([]),
        );
        let found = from_searxng("q", &payload, 5, Level::Off).unwrap();
        assert_eq!(
            found.results[0].engines,
            vec!["duckduckgo".to_string(), "brave".to_string()]
        );
        assert_eq!(found.airgapped, "off");
    }

    #[test]
    fn some_engines_failing_is_not_an_error_when_others_answered() {
        let payload = searxng(
            json!([{"title": "A", "url": "https://a"}]),
            json!([]),
            json!([["brave", "CAPTCHA"]]),
        );
        assert_eq!(
            from_searxng("q", &payload, 5, Level::Off)
                .unwrap()
                .result_count,
            1
        );
    }

    #[test]
    fn an_infobox_comes_first_and_its_page_is_not_repeated() {
        let mut payload = searxng(
            json!([
                {"title": "Lisbon - Wikipedia", "url": "https://en.wikipedia.org/wiki/Lisbon", "engine": "mwmbl"},
                {"title": "Visit Lisbon", "url": "https://visitlisboa.com", "content": "trams", "engine": "yep"}
            ]),
            json!([]),
            json!([["brave", "too many requests"]]),
        );
        payload["infoboxes"] = json!([{
            "infobox": "Lisbon", "id": "https://en.wikipedia.org/wiki/Lisbon",
            "content": "Lisbon is the capital of Portugal.", "engine": "wikipedia", "engines": ["wikipedia"]
        }]);
        let found = from_searxng("lisbon", &payload, 5, Level::Off).unwrap();
        let urls: Vec<&str> = found
            .results
            .iter()
            .map(|result| result.url.as_str())
            .collect();
        assert_eq!(
            urls,
            [
                "https://en.wikipedia.org/wiki/Lisbon",
                "https://visitlisboa.com"
            ]
        );
        assert_eq!(found.results[0].title, "Lisbon");
        assert_eq!(
            found.results[0].snippet,
            "Lisbon is the capital of Portugal."
        );
        assert_eq!(found.results[0].engines, ["wikipedia"]);

        // Only an infobox, every web engine refusing: an answer, not "could not reach any engine".
        let mut only = searxng(
            json!([]),
            json!([]),
            json!([["brave", "too many requests"]]),
        );
        only["infoboxes"] = payload["infoboxes"].clone();
        assert_eq!(
            from_searxng("lisbon", &only, 5, Level::Off)
                .unwrap()
                .result_count,
            1
        );
    }

    #[test]
    fn a_page_only_a_second_infobox_named_is_still_a_web_result() {
        // Only the first infobox is shown, so the second one's page must not be held back.
        let mut payload = searxng(
            json!([
                {"title": "Lisbon - Wikipedia", "url": "https://en.wikipedia.org/wiki/Lisbon", "engine": "mwmbl"},
                {"title": "Lisbon - Wikidata", "url": "https://www.wikidata.org/wiki/Q597", "content": "city", "engine": "yep"}
            ]),
            json!([]),
            json!([]),
        );
        payload["infoboxes"] = json!([
            {"infobox": "Lisbon", "id": "https://en.wikipedia.org/wiki/Lisbon", "content": "Capital.", "engine": "wikipedia"},
            {"infobox": "Lisbon", "id": "https://www.wikidata.org/wiki/Q597", "content": "City.", "engine": "wikidata"}
        ]);
        let found = from_searxng("lisbon", &payload, 5, Level::Off).unwrap();
        let urls: Vec<&str> = found
            .results
            .iter()
            .map(|result| result.url.as_str())
            .collect();
        assert_eq!(
            urls,
            [
                "https://en.wikipedia.org/wiki/Lisbon",
                "https://www.wikidata.org/wiki/Q597"
            ]
        );
    }

    #[test]
    fn the_same_page_under_two_addresses_is_one_result_with_both_engines() {
        let payload = searxng(
            json!([
                {"title": "Trams", "url": "https://www.example.org/trams/", "content": "", "engine": "mwmbl", "engines": ["mwmbl"]},
                {"title": "News", "url": "https://news.example/a", "content": "n", "engine": "bing news", "publishedDate": "2026-10-20T08:00:00"},
                {"title": "Trams", "url": "http://example.org/trams?utm_source=x", "content": "the 28", "engine": "duckduckgo web", "engines": ["duckduckgo web"]},
                {"title": "Trams page 2", "url": "https://example.org/trams?page=2", "engine": "yep"}
            ]),
            json!([]),
            json!([]),
        );
        let found = from_searxng("q", &payload, 10, Level::Off).unwrap();
        let urls: Vec<&str> = found
            .results
            .iter()
            .map(|result| result.url.as_str())
            .collect();
        assert_eq!(
            urls,
            [
                "https://www.example.org/trams/",
                "https://news.example/a",
                "https://example.org/trams?page=2"
            ]
        );
        assert_eq!(found.results[0].engines, ["mwmbl", "duckduckgo web"]);
        assert_eq!(found.results[0].snippet, "the 28");
        assert_eq!(found.results[1].published.as_deref(), Some("2026-10-20"));
        assert!(render_text(&found).contains("   2026-10-20\n"));
    }

    #[test]
    fn options_are_checked_and_social_is_sent_by_searxngs_name() {
        let options = SearchOptions::new(Some("Social"), Some("week"), Some("pt")).unwrap();
        assert_eq!(options.searxng_category(), "social media");
        assert_eq!(options.time_range.as_deref(), Some("week"));
        assert_eq!(options.language, "pt");
        assert_eq!(
            SearchOptions::new(None, None, None).unwrap(),
            SearchOptions::default()
        );
        assert_eq!(SearchOptions::default().language, AUTO_LANGUAGE);
        assert!(
            SearchOptions::new(Some("shopping"), None, None)
                .unwrap_err()
                .contains("one of: general, news")
        );
        assert!(SearchOptions::new(None, Some("hour"), None).is_err());
        assert!(SearchOptions::new(None, None, Some("en; rm -rf")).is_err());
        assert!(SearchOptions::new(None, None, Some("de-CH")).is_ok());
    }

    #[test]
    fn nothing_found_with_every_engine_answering_is_an_empty_result() {
        let found = from_searxng(
            "q",
            &searxng(json!([]), json!([]), json!([])),
            5,
            Level::Off,
        )
        .unwrap();
        assert_eq!(found.result_count, 0);
    }

    #[test]
    fn json_output_keeps_the_field_order() {
        let payload = searxng(
            json!([{"title": "A", "url": "https://a", "content": "s", "engine": "e"}]),
            json!(["42"]),
            json!([]),
        );
        let printed =
            serde_json::to_string(&from_searxng("q", &payload, 5, Level::Off).unwrap()).unwrap();
        assert_eq!(
            printed,
            r#"{"airgapped":"off","query":"q","result_count":1,"answers":["42"],"results":[{"title":"A","url":"https://a","snippet":"s","engine":"e","engines":[]}]}"#
        );
    }

    #[test]
    fn text_output_matches_the_python_command() {
        let payload = searxng(
            json!([
                {"title": "A", "url": "https://a", "content": "x".repeat(300), "engine": "e"},
                {"title": "B", "url": "https://b", "content": "", "engine": "e"}
            ]),
            json!(["42", {"answer": "forty-two", "url": "https://calc"}]),
            json!([]),
        );
        let text = render_text(&from_searxng("q", &payload, 5, Level::Off).unwrap());
        let expected = format!(
            "ANSWER: 42\n\nANSWER: forty-two\n\n1. A\n   https://a\n   {}\n2. B\n   https://b\n",
            "x".repeat(200)
        );
        assert_eq!(text, expected);
    }

    #[test]
    fn at_most_three_answers() {
        let payload = searxng(json!([]), json!(["1", "2", "3", "4"]), json!([]));
        assert_eq!(
            from_searxng("q", &payload, 5, Level::Off)
                .unwrap()
                .answers
                .len(),
            3
        );
    }

    #[test]
    fn an_empty_query_is_refused_before_any_request() {
        let error = search("http://127.0.0.1:9", "   ", 5, Level::Off).unwrap_err();
        assert_eq!(error.error, "empty query");
    }

    #[test]
    fn bings_tracking_parameter_is_not_part_of_the_address() {
        assert_eq!(
            comparable_url("https://a.example/x?msockid=1&id=2"),
            "a.example/x?id=2"
        );
    }

    #[test]
    fn a_host_is_compared_in_lower_case() {
        assert_eq!(comparable_url("https://WWW.A.Example/X"), "a.example/X");
    }

    #[test]
    fn the_fragment_is_not_part_of_the_address() {
        assert_eq!(
            comparable_url("https://a.example/x?id=2#top"),
            "a.example/x?id=2"
        );
    }

    #[test]
    fn a_duplicate_without_an_engine_adds_no_empty_engine() {
        let payload = searxng(
            json!([
                {"title": "A", "url": "https://a.example/x", "engine": "bing", "engines": ["bing"]},
                {"title": "A", "url": "http://a.example/x"}
            ]),
            json!([]),
            json!([]),
        );
        let found = from_searxng("q", &payload, 10, Level::Off).unwrap();
        assert_eq!(found.results.len(), 1);
        assert_eq!(found.results[0].engines, ["bing"]);
    }

    #[test]
    fn the_engine_of_a_duplicate_is_added_without_an_engines_list() {
        let payload = searxng(
            json!([
                {"title": "A", "url": "https://a.example/x", "engine": "bing", "engines": ["bing"]},
                {"title": "A", "url": "http://a.example/x", "engine": "yep"}
            ]),
            json!([]),
            json!([]),
        );
        let found = from_searxng("q", &payload, 10, Level::Off).unwrap();
        assert_eq!(found.results[0].engines, ["bing", "yep"]);
    }

    #[test]
    fn the_snippet_and_date_of_the_first_copy_of_a_page_are_kept() {
        let payload = searxng(
            json!([
                {"title": "A", "url": "https://a.example/x", "content": "first", "engine": "bing",
                 "publishedDate": "2026-10-01T08:00:00"},
                {"title": "A", "url": "http://a.example/x", "content": "second", "engine": "yep",
                 "publishedDate": "2026-10-20T08:00:00"}
            ]),
            json!([]),
            json!([]),
        );
        let found = from_searxng("q", &payload, 10, Level::Off).unwrap();
        assert_eq!(found.results[0].snippet, "first");
        assert_eq!(found.results[0].published.as_deref(), Some("2026-10-01"));
    }

    #[test]
    fn an_infobox_id_that_is_not_an_address_gives_way_to_its_link() {
        let mut payload = searxng(json!([]), json!([]), json!([]));
        payload["infoboxes"] = json!([{
            "infobox": "Lisbon", "id": "Q597", "engine": "wikidata",
            "urls": [{"title": "Wikidata", "url": "https://www.wikidata.org/wiki/Q597"}]
        }]);
        let found = from_searxng("q", &payload, 10, Level::Off).unwrap();
        assert_eq!(found.results[0].url, "https://www.wikidata.org/wiki/Q597");
    }

    #[test]
    fn an_infobox_without_an_engine_has_no_engines() {
        let mut payload = searxng(json!([]), json!([]), json!([]));
        payload["infoboxes"] =
            json!([{"infobox": "Lisbon", "id": "https://en.wikipedia.org/wiki/Lisbon"}]);
        let found = from_searxng("q", &payload, 10, Level::Off).unwrap();
        assert_eq!(found.results[0].engine, "");
        assert!(
            found.results[0].engines.is_empty(),
            "{:?}",
            found.results[0].engines
        );
    }

    #[test]
    fn only_the_first_infobox_is_a_result() {
        let mut payload = searxng(json!([]), json!([]), json!([]));
        payload["infoboxes"] = json!([
            {"infobox": "Lisbon", "id": "https://en.wikipedia.org/wiki/Lisbon", "engine": "wikipedia"},
            {"infobox": "Lisboa", "id": "https://www.wikidata.org/wiki/Q597", "engine": "wikidata"}
        ]);
        let found = from_searxng("q", &payload, 10, Level::Off).unwrap();
        let urls: Vec<&str> = found
            .results
            .iter()
            .map(|result| result.url.as_str())
            .collect();
        assert_eq!(urls, ["https://en.wikipedia.org/wiki/Lisbon"]);
    }

    #[test]
    fn answers_with_every_engine_failing_are_not_an_error() {
        let payload = searxng(json!([]), json!(["42"]), json!([["brave", "CAPTCHA"]]));
        let found = from_searxng("q", &payload, 10, Level::Off).unwrap();
        assert_eq!((found.result_count, found.answers.len()), (0, 1));
    }

    #[test]
    fn an_empty_publication_date_is_no_date() {
        let payload = searxng(
            json!([{"title": "A", "url": "https://a.example/x", "engine": "bing", "publishedDate": ""}]),
            json!([]),
            json!([]),
        );
        let found = from_searxng("q", &payload, 10, Level::Off).unwrap();
        assert_eq!(found.results[0].published, None);
    }

    #[test]
    fn a_language_is_at_most_six_characters() {
        assert!(SearchOptions::new(None, None, Some("de-chxyz")).is_err());
        assert!(SearchOptions::new(None, None, Some("de-chx")).is_ok());
    }

    #[test]
    fn a_language_holds_only_letters_and_dashes() {
        assert!(SearchOptions::new(None, None, Some("en-;x")).is_err());
        assert!(SearchOptions::new(None, None, Some("en-1")).is_err());
    }

    #[test]
    fn a_language_code_is_two_or_three_letters() {
        assert!(SearchOptions::new(None, None, Some("e")).is_err());
        assert!(SearchOptions::new(None, None, Some("engl")).is_err());
        assert!(SearchOptions::new(None, None, Some("e-ch")).is_err());
        assert!(SearchOptions::new(None, None, Some("deu")).is_ok());
    }

    #[test]
    fn social_media_is_the_social_category() {
        let options = SearchOptions::new(Some(" Social Media "), None, None).unwrap();
        assert_eq!(options.searxng_category(), "social media");
    }

    #[test]
    fn a_time_range_is_read_without_case_or_spaces() {
        let options = SearchOptions::new(None, Some(" Week "), None).unwrap();
        assert_eq!(options.time_range.as_deref(), Some("week"));
    }
}
