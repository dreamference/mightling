//! `mling-search`: web search through the SearXNG instance on this machine.
//!
//! SearXNG queries the upstream engines on this machine's behalf and returns their merged results
//! as JSON, so no query leaves the host addressed to a search company and no API key is involved.
//! If the instance is down this says so plainly instead of falling back to a public engine: a
//! silent fallback would send queries somewhere the operator did not choose.

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
pub const SEARXNG_START_HINT: &str = "mling-admin searxng start";

/// SearXNG answers HTML, or refuses with 403, unless `json` is among its `search.formats`.
pub const JSON_FORMAT_HINT: &str =
    "Add 'json' to search.formats in ~/.config/searxng/settings.yml and restart it";

pub const RESTART_HINT: &str =
    "If this machine is online, restart the container: docker restart dreamference-searxng";

/// One result, in the field order `mling-search --json` has always printed.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct SearchResult {
    pub title: String,
    pub url: String,
    pub snippet: String,
    pub engine: String,
    /// Every engine SearXNG reports for the result; `engine` is only the first of them, which
    /// cannot show that no other engine contributed.
    pub engines: Vec<String>,
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
            "Web search runs on the Mightling node ({}), which does not share its SearXNG. On the node: {SEARXNG_START_HINT}, then mling-admin node enable",
            node_label(node)
        ),
        Some(node) => format!(
            "Web search runs on the Mightling node ({}). On the node: {SEARXNG_START_HINT}",
            node_label(node)
        ),
    }
}

/// [`RESTART_HINT`], said for the machine the container is on.
pub fn restart_hint(node: Option<&Node>) -> String {
    match node {
        None => RESTART_HINT.to_string(),
        Some(node) => format!(
            "If the Mightling node ({}) is online, restart the container there: docker restart dreamference-searxng",
            node_label(node)
        ),
    }
}

fn node_label(node: &Node) -> &str {
    if node.name.is_empty() { &node.address } else { &node.name }
}

/// Searches the web through SearXNG.
///
/// One difference from the Python version: an HTTP error status is reported as the status SearXNG
/// answered, with the `search.formats` hint for 403 (its answer to a JSON request when the format
/// is off), rather than as "unreachable" with the start command, which sent people to start an
/// instance that was running.
///
/// At `on` nothing is sent; the binary refuses before calling this, and so does this function.
pub fn search(base_url: &str, query: &str, max_results: i64, level: Level) -> Result<SearchPayload, SearchError> {
    search_from(base_url, query, max_results, level, crate::node_locator::remote_node().as_ref())
}

/// [`search`], told which node the instance is on (`None`: this machine), for the hints.
pub fn search_from(
    base_url: &str,
    query: &str,
    max_results: i64,
    level: Level,
    node: Option<&Node>,
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
    let endpoint = format!("{}/search", base_url.trim_end_matches('/'));
    let response = agent(None)
        .get(&endpoint)
        .query("q", query)
        .query("format", "json")
        .query("categories", "general")
        .query("language", "en")
        .set("Accept", "application/json")
        .call();
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
                    Some(restart_hint(node))
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
    from_searxng(query, &payload, max_results, level)
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
    let results: Vec<SearchResult> = payload["results"]
        .as_array()
        .into_iter()
        .flatten()
        .take(limit)
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
        })
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
            hint: Some(RESTART_HINT.to_string()),
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

/// The text `mling-search` prints for a payload.
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
                engines: vec![]
            }
        );
        assert_eq!(from_searxng("q", &payload, 0, Level::Off).unwrap().result_count, 1);
        assert_eq!(from_searxng("q", &payload, -5, Level::Off).unwrap().result_count, 1);
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
        assert_eq!(error.hint.as_deref(), Some(RESTART_HINT));
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
        assert_eq!(searxng_url_from(None, Some(&remote())), "http://192.168.50.105:8888");
        // The override still wins, on either kind of machine.
        assert_eq!(searxng_url_from(Some("http://10.0.0.9:1234"), Some(&remote())), "http://10.0.0.9:1234");
        // A node that keeps its SearXNG to itself: nothing to connect to but loopback.
        let unshared = Node { search_port: None, ..remote() };
        assert_eq!(searxng_url_from(None, Some(&unshared)), DEFAULT_SEARXNG_URL);
    }

    #[test]
    fn on_a_client_the_hint_says_where_to_type_the_command() {
        assert_eq!(start_hint(None), "Start it with: mling-admin searxng start");
        assert_eq!(
            start_hint(Some(&remote())),
            "Web search runs on the Mightling node (gx10-1a2b). On the node: mling-admin searxng start"
        );
        let unshared = Node { search_port: None, name: String::new(), ..remote() };
        assert!(start_hint(Some(&unshared)).contains("(192.168.50.105), which does not share its SearXNG"));
        assert!(start_hint(Some(&unshared)).ends_with("mling-admin node enable"));
        assert_eq!(restart_hint(None), RESTART_HINT);
        assert!(restart_hint(Some(&remote())).contains("restart the container there"));
    }

    #[test]
    fn an_unreachable_node_is_named_in_the_error() {
        // Port 9 on loopback refuses at once; the hint is the client's, because a node was given.
        let error = search_from("http://127.0.0.1:9", "q", 5, Level::Off, Some(&remote())).unwrap_err();
        assert!(error.error.contains("unreachable"), "{error:?}");
        assert_eq!(error.hint.as_deref(), Some(start_hint(Some(&remote())).as_str()));
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
        assert_eq!(found.results[0].engines, vec!["duckduckgo".to_string(), "brave".to_string()]);
        assert_eq!(found.airgapped, "off");
    }

    #[test]
    fn some_engines_failing_is_not_an_error_when_others_answered() {
        let payload = searxng(
            json!([{"title": "A", "url": "https://a"}]),
            json!([]),
            json!([["brave", "CAPTCHA"]]),
        );
        assert_eq!(from_searxng("q", &payload, 5, Level::Off).unwrap().result_count, 1);
    }

    #[test]
    fn nothing_found_with_every_engine_answering_is_an_empty_result() {
        let found = from_searxng("q", &searxng(json!([]), json!([]), json!([])), 5, Level::Off).unwrap();
        assert_eq!(found.result_count, 0);
    }

    #[test]
    fn json_output_keeps_the_field_order() {
        let payload = searxng(
            json!([{"title": "A", "url": "https://a", "content": "s", "engine": "e"}]),
            json!(["42"]),
            json!([]),
        );
        let printed = serde_json::to_string(&from_searxng("q", &payload, 5, Level::Off).unwrap()).unwrap();
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
        assert_eq!(from_searxng("q", &payload, 5, Level::Off).unwrap().answers.len(), 3);
    }

    #[test]
    fn an_empty_query_is_refused_before_any_request() {
        let error = search("http://127.0.0.1:9", "   ", 5, Level::Off).unwrap_err();
        assert_eq!(error.error, "empty query");
    }
}
