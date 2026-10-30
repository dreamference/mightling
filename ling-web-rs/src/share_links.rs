//! Google share links: `ling-fetch` resolves them to what they open without loading Google's page.
//!
//! A phone's "share" button on a Google search or a Maps place gives a short link
//! (`share.google/<id>`, `maps.app.goo.gl/<id>`, the older `goo.gl/maps/<id>`) that redirects to
//! `www.google.<tld>/search?q=…` or `www.google.<tld>/maps/place/…`. Google answers anything that
//! is not a browser at those addresses with a "click here if you are not redirected" or captcha
//! page, so fetching the link left the model with nothing and it went on trying for minutes. The
//! search or place is already in the redirect's `Location`, though: the redirects of a share link
//! are walked one at a time, each `Location` is read before it is requested, and the walk stops at
//! the first one that names a Google search or place, which is then never requested.

use crate::fetch::FetchedPage;

/// The redirects a share link may take before the walk gives up, as many as a fetch follows.
pub const MAX_HOPS: u32 = 10;

/// What a Google URL opens.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Target {
    /// A Google search for `query`; `kgmid` names the Knowledge Graph entity it is about.
    Search {
        query: String,
        kgmid: Option<String>,
    },
    /// A Google Maps place or search; `coordinates` is `"lat, lng"` when the URL carries them.
    Place {
        name: String,
        coordinates: Option<String>,
    },
}

/// Where a walk ended: at a Google target (never requested), or at a response that is not a
/// redirect, whose body the fetch reads as it reads any page.
pub enum Walked {
    Target {
        final_url: String,
        status: u16,
        target: Target,
    },
    Response(ureq::Response),
}

/// The parts of an http(s) URL this module reads: the host (lower case), the path and the query.
struct Parts<'a> {
    scheme: &'a str,
    host: String,
    path: &'a str,
    query: &'a str,
}

fn parts(url: &str) -> Option<Parts<'_>> {
    let (scheme, rest) = url.split_once("://")?;
    let rest = rest.split('#').next().unwrap_or_default();
    let (before_query, query) = rest.split_once('?').unwrap_or((rest, ""));
    let (authority, path) = match before_query.find('/') {
        Some(slash) => before_query.split_at(slash),
        None => (before_query, ""),
    };
    let host_port = authority
        .rsplit_once('@')
        .map_or(authority, |(_, host)| host);
    let host = host_port
        .split(':')
        .next()
        .unwrap_or_default()
        .to_ascii_lowercase();
    Some(Parts {
        scheme,
        host,
        path,
        query,
    })
}

/// True for a link a phone's Google "share" button makes: `share.google/…`, `maps.app.goo.gl/…`
/// and `goo.gl/maps/…`. Only these start a walk; every other URL is fetched as it always was.
pub fn is_share_link(url: &str) -> bool {
    let Some(parts) = parts(url) else {
        return false;
    };
    match parts.host.as_str() {
        "share.google" | "maps.app.goo.gl" => true,
        "goo.gl" | "www.goo.gl" => parts.path.starts_with("/maps/"),
        _ => false,
    }
}

/// True for `google.<tld>` (`google.com`, `google.pt`, `google.co.uk`), with or without `www.`
/// or `maps.` in front.
fn is_google_host(host: &str) -> bool {
    let host = host
        .strip_prefix("www.")
        .or_else(|| host.strip_prefix("maps."))
        .unwrap_or(host);
    let Some(tld) = host.strip_prefix("google.") else {
        return false;
    };
    let labels: Vec<&str> = tld.split('.').collect();
    labels.len() <= 2
        && labels.iter().all(|label| {
            (2..=3).contains(&label.len()) && label.chars().all(|c| c.is_ascii_alphabetic())
        })
}

/// Percent-decodes `text`, reading `+` as a space as a query string means it.
pub fn percent_decode(text: &str, plus_is_space: bool) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        let hex = || {
            let digits = bytes.get(index + 1..index + 3)?;
            digits
                .iter()
                .all(u8::is_ascii_hexdigit)
                .then(|| std::str::from_utf8(digits).ok())?
        };
        match byte {
            b'%' => match hex().and_then(|digits| u8::from_str_radix(digits, 16).ok()) {
                Some(decoded) => {
                    out.push(decoded);
                    index += 3;
                    continue;
                }
                None => out.push(byte),
            },
            b'+' if plus_is_space => out.push(b' '),
            _ => out.push(byte),
        }
        index += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The decoded value of the first non-empty `name` parameter in a query string.
fn query_param(query: &str, name: &str) -> Option<String> {
    query.split('&').find_map(|pair| {
        let (key, value) = pair.split_once('=')?;
        let value = percent_decode(value, true);
        (percent_decode(key, true) == name && !value.trim().is_empty())
            .then(|| value.trim().to_string())
    })
}

/// `"lat, lng"` from a Maps path segment such as `@38.7139,-9.1394,17z`.
fn coordinates_in(segment: &str) -> Option<String> {
    let mut numbers = segment.strip_prefix('@')?.split(',');
    let (lat, lng) = (numbers.next()?, numbers.next()?);
    let is_number = |text: &str| !text.is_empty() && text.parse::<f64>().is_ok();
    (is_number(lat) && is_number(lng)).then(|| format!("{lat}, {lng}"))
}

/// What a Google URL opens, read from the URL alone: a search (`/search?q=…`, with `kgmid` when
/// present) or a Maps place (`/maps/place/<name>/@lat,lng,…`, `/maps/search/<name>/…`, or a Maps
/// URL with `q=` or `query=`). `None` for anything else, Google or not.
pub fn classify(url: &str) -> Option<Target> {
    let parts = parts(url)?;
    if !matches!(parts.scheme.to_ascii_lowercase().as_str(), "http" | "https")
        || !is_google_host(&parts.host)
    {
        return None;
    }
    if parts.path == "/search" {
        let kgmid = query_param(parts.query, "kgmid");
        let query = query_param(parts.query, "q").or_else(|| kgmid.clone())?;
        return Some(Target::Search { query, kgmid });
    }
    let maps_path = parts
        .path
        .strip_prefix("/maps")
        .filter(|rest| rest.is_empty() || rest.starts_with('/'));
    let maps_path = match maps_path {
        Some(rest) => rest,
        None if parts.host.starts_with("maps.") => parts.path,
        None => return None,
    };
    let segments: Vec<&str> = maps_path
        .split('/')
        .filter(|segment| !segment.is_empty())
        .collect();
    let coordinates = segments.iter().find_map(|segment| coordinates_in(segment));
    let named = match segments.as_slice() {
        ["place" | "search", name, ..] if !name.starts_with('@') => {
            Some(percent_decode(name, true))
        }
        _ => None,
    };
    let name = named
        .or_else(|| query_param(parts.query, "q"))
        .or_else(|| query_param(parts.query, "query"))
        .or_else(|| coordinates.clone())?;
    Some(Target::Place { name, coordinates })
}

/// `location` resolved against `base`, the URL that answered with it: absolute, scheme-relative
/// (`//host/…`), host-relative (`/…`) or relative to the base's directory.
pub fn resolve_location(base: &str, location: &str) -> String {
    let location = location.trim();
    // Any scheme makes it absolute (`file:///etc`, `mailto:`), so the walk sees what it names.
    let has_scheme = location.split_once(':').is_some_and(|(scheme, _)| {
        scheme.starts_with(|c: char| c.is_ascii_alphabetic())
            && scheme
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c))
    });
    if has_scheme {
        return location.to_string();
    }
    // Only a query, or only a fragment, replaces that part of the same address.
    if location.starts_with('?') || location.starts_with('#') {
        let end = if location.starts_with('?') {
            base.find(['?', '#'])
        } else {
            base.find('#')
        };
        return format!("{}{location}", &base[..end.unwrap_or(base.len())]);
    }
    let (scheme, rest) = base.split_once("://").unwrap_or(("http", base));
    if let Some(after) = location.strip_prefix("//") {
        return format!("{scheme}://{after}");
    }
    let authority_end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let origin = format!("{scheme}://{}", &rest[..authority_end]);
    if location.starts_with('/') {
        return format!("{origin}{location}");
    }
    let path = rest[authority_end..]
        .split(['?', '#'])
        .next()
        .unwrap_or_default();
    let directory = path.rsplit_once('/').map_or("", |(directory, _)| directory);
    format!("{origin}{directory}/{location}")
}

/// Walks the redirects from `start` with `agent`, which must not follow redirects itself, reading
/// each `Location` with `classify` before it is requested. Errors are worded as a fetch's are.
pub fn walk(
    agent: &ureq::Agent,
    start: &str,
    classify: impl Fn(&str) -> Option<Target>,
) -> Result<Walked, String> {
    walk_with(agent, start, &[("Accept-Language", "en")], classify)
}

/// [`walk`], sending `headers` with every request. A `Location` that is not an http or https URL
/// with a host is refused before it is requested: ureq's own redirects panic on one without a host.
pub fn walk_with(
    agent: &ureq::Agent,
    start: &str,
    headers: &[(&str, &str)],
    classify: impl Fn(&str) -> Option<Target>,
) -> Result<Walked, String> {
    let mut current = start.to_string();
    for _ in 0..=MAX_HOPS {
        let request = headers
            .iter()
            .fold(agent.get(&current), |request, (name, value)| {
                request.set(name, value)
            });
        let response = request.call().map_err(crate::fetch::request_error)?;
        let status = response.status();
        let location = response
            .header("Location")
            .filter(|_| (300..400).contains(&status));
        let Some(location) = location else {
            return Ok(Walked::Response(response));
        };
        let next = resolve_location(&current, location);
        if let Some(target) = classify(&next) {
            return Ok(Walked::Target {
                final_url: next,
                status,
                target,
            });
        }
        if let Err(error) = crate::fetch::require_http_url(&next) {
            return Err(format!(
                "request failed: redirect to {next} refused: {error}"
            ));
        }
        current = next;
    }
    Err(format!(
        "request failed: more than {MAX_HOPS} redirects from {start}"
    ))
}

/// The page `ling-fetch` returns for a share link that opens `target`: what it is, in words, and
/// the command that finds it, since Google's own page cannot be read without a browser.
pub fn page(url: &str, final_url: String, status: u16, target: &Target) -> FetchedPage {
    let (title, text) = match target {
        Target::Search { query, kgmid } => {
            let entity = kgmid
                .as_ref()
                .filter(|kgmid| *kgmid != query)
                .map(|kgmid| format!(" (Knowledge Graph id {kgmid})"))
                .unwrap_or_default();
            let text = format!(
                "This Google share link opens a Google search for \"{query}\"{entity}. Google's result page \
                 is not readable without a browser, so it was not fetched; search for it with \
                 ling-search \"{query}\"."
            );
            (query.clone(), text)
        }
        Target::Place { name, coordinates } => {
            let at = coordinates
                .as_ref()
                .filter(|coordinates| *coordinates != name)
                .map(|coordinates| format!(" at coordinates {coordinates}"))
                .unwrap_or_default();
            let text = format!(
                "This Google Maps share link opens the place \"{name}\"{at}. Google Maps is not readable \
                 without a browser, so it was not fetched; search for the place with ling-search \"{name}\"."
            );
            (name.clone(), text)
        }
    };
    FetchedPage {
        url: url.to_string(),
        final_url,
        status,
        content_type: String::new(),
        title,
        text,
        truncated: false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn search(query: &str, kgmid: Option<&str>) -> Option<Target> {
        Some(Target::Search {
            query: query.into(),
            kgmid: kgmid.map(Into::into),
        })
    }

    fn place(name: &str, coordinates: Option<&str>) -> Option<Target> {
        Some(Target::Place {
            name: name.into(),
            coordinates: coordinates.map(Into::into),
        })
    }

    #[test]
    fn only_phone_share_links_start_a_walk() {
        assert!(is_share_link("https://share.google/AbCdEf123"));
        assert!(is_share_link("https://maps.app.goo.gl/XyZ987"));
        assert!(is_share_link("https://goo.gl/maps/XyZ987"));
        assert!(!is_share_link("https://goo.gl/abc"));
        assert!(!is_share_link("https://www.google.com/search?q=x"));
        assert!(!is_share_link("https://example.com/share.google"));
        assert!(!is_share_link("https://notshare.google.example/x"));
    }

    #[test]
    fn a_search_url_gives_its_query_and_entity() {
        assert_eq!(
            classify(
                "https://www.google.com/search?kgmid=/g/11synth01&hl=pt-PT&q=Some+Synthetic+Song&shem=abc"
            ),
            search("Some Synthetic Song", Some("/g/11synth01"))
        );
        assert_eq!(
            classify("https://www.google.pt/search?q=caf%C3%A9%20%26%20bar&kgmid=%2Fm%2F0test"),
            search("café & bar", Some("/m/0test"))
        );
        assert_eq!(
            classify("https://google.co.uk/search?kgmid=/g/11only"),
            search("/g/11only", Some("/g/11only"))
        );
        assert_eq!(classify("https://www.google.com/search?hl=en"), None);
    }

    #[test]
    fn a_maps_url_gives_its_place_and_coordinates() {
        assert_eq!(
            classify(
                "https://www.google.com/maps/place/Caf%C3%A9+Synthetic/@38.7139,-9.1394,17z/data=!3m1!4b1"
            ),
            place("Café Synthetic", Some("38.7139, -9.1394"))
        );
        assert_eq!(
            classify(
                "https://www.google.pt/maps/search/Synthetic+Bakery/@41.15,-8.61,15z?entry=ttu"
            ),
            place("Synthetic Bakery", Some("41.15, -8.61"))
        );
        assert_eq!(
            classify("https://maps.google.com/?q=Synthetic+Park&ftid=0x1:0x2"),
            place("Synthetic Park", None)
        );
        assert_eq!(
            classify("https://www.google.com/maps/search/?api=1&query=Synthetic+Museum"),
            place("Synthetic Museum", None)
        );
        assert_eq!(
            classify("https://www.google.com/maps/@38.7,-9.1,14z"),
            place("38.7, -9.1", Some("38.7, -9.1"))
        );
        assert_eq!(classify("https://www.google.com/maps"), None);
    }

    #[test]
    fn other_urls_are_not_targets() {
        assert_eq!(classify("https://example.com/search?q=x"), None);
        assert_eq!(classify("https://www.google.com/share.google?q=AbC"), None);
        assert_eq!(classify("https://www.google.com/mapsfoo?q=x"), None);
        assert_eq!(classify("https://google.evil.example.com/search?q=x"), None);
        assert_eq!(classify("https://accounts.google.com/search?q=x"), None);
        assert_eq!(classify("ftp://www.google.com/search?q=x"), None);
    }

    #[test]
    fn locations_are_resolved_against_the_url_that_sent_them() {
        let base = "https://share.google/dir/AbC?x=1";
        assert_eq!(
            resolve_location(base, "https://www.google.com/search?q=a"),
            "https://www.google.com/search?q=a"
        );
        assert_eq!(
            resolve_location(base, "//www.google.com/x"),
            "https://www.google.com/x"
        );
        assert_eq!(
            resolve_location(base, "/next?y=2"),
            "https://share.google/next?y=2"
        );
        assert_eq!(
            resolve_location(base, "file:///etc/passwd"),
            "file:///etc/passwd"
        );
        assert_eq!(resolve_location(base, "mailto:a@b"), "mailto:a@b");
        assert_eq!(
            resolve_location(base, "other"),
            "https://share.google/dir/other"
        );
        assert_eq!(
            resolve_location("http://127.0.0.1:9/s", "/page"),
            "http://127.0.0.1:9/page"
        );
    }

    #[test]
    fn a_location_of_only_a_query_or_a_fragment_keeps_the_path() {
        let base = "https://share.google/dir/AbC?x=1#top";
        assert_eq!(
            resolve_location(base, "?y=2"),
            "https://share.google/dir/AbC?y=2"
        );
        assert_eq!(
            resolve_location(base, "#end"),
            "https://share.google/dir/AbC?x=1#end"
        );
    }

    #[test]
    fn percent_decoding_keeps_what_is_not_an_escape() {
        assert_eq!(percent_decode("a+b%20c", true), "a b c");
        assert_eq!(percent_decode("a+b", false), "a+b");
        assert_eq!(percent_decode("100%", true), "100%");
        assert_eq!(percent_decode("%zz%+1%C3%A9", true), "%zz% 1é");
    }

    #[test]
    fn the_page_says_what_the_link_opens_and_how_to_find_it() {
        let found = page(
            "https://share.google/AbC",
            "https://www.google.com/search?q=Song".into(),
            302,
            &Target::Search {
                query: "Some Song".into(),
                kgmid: Some("/g/11synth".into()),
            },
        );
        assert_eq!(found.title, "Some Song");
        assert!(
            found
                .text
                .contains("\"Some Song\" (Knowledge Graph id /g/11synth)"),
            "{}",
            found.text
        );
        assert!(
            found.text.contains("ling-search \"Some Song\""),
            "{}",
            found.text
        );
        assert_eq!((found.status, found.truncated), (302, false));
        let at = page(
            "u",
            "f".into(),
            302,
            &Target::Place {
                name: "Café".into(),
                coordinates: Some("1, 2".into()),
            },
        );
        assert_eq!(at.title, "Café");
        assert!(
            at.text.contains("\"Café\" at coordinates 1, 2"),
            "{}",
            at.text
        );
    }

    #[test]
    fn a_google_host_has_at_most_two_labels_after_google() {
        assert_eq!(classify("https://google.com.ab.cd/search?q=x"), None);
        assert_eq!(
            classify("https://www.google.co.uk/search?q=x"),
            search("x", None)
        );
    }

    #[test]
    fn a_label_after_google_is_a_two_or_three_letter_code() {
        assert_eq!(classify("https://google.example/search?q=x"), None);
        assert_eq!(classify("https://www.google.coms/search?q=x"), None);
        assert_eq!(classify("https://google.c/search?q=x"), None);
    }

    #[test]
    fn an_upper_case_host_is_still_google() {
        assert_eq!(
            classify("https://WWW.Google.COM/search?q=x"),
            search("x", None)
        );
        assert!(is_share_link("https://SHARE.GOOGLE/AbC"));
    }

    #[test]
    fn only_the_search_path_itself_is_a_search() {
        assert_eq!(classify("https://www.google.com/searchfoo?q=x"), None);
        assert_eq!(classify("https://www.google.com/search/x?q=x"), None);
    }

    #[test]
    fn an_empty_query_parameter_is_skipped_for_the_next() {
        assert_eq!(
            classify("https://www.google.com/search?q=&q=+&q=x"),
            search("x", None)
        );
        assert_eq!(classify("https://www.google.com/search?q="), None);
    }

    #[test]
    fn a_coordinate_segment_does_not_name_the_place() {
        assert_eq!(
            classify("https://www.google.com/maps/place/@38.7,-9.1,14z?q=Synthetic+Park"),
            place("Synthetic Park", Some("38.7, -9.1"))
        );
    }
}
