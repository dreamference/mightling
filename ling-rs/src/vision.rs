//! Whether the served model takes images, asked of the model server, never assumed
//! (specs/DREAMFERENCE_MIGHTLING_CHAT.md §16).
//!
//! SGLang answers `/model_info` (and its older name `/get_model_info`) with
//! `has_image_understanding`. The answer goes into the catalog as Codex's `input_modalities`:
//! `["text", "image"]` or `["text"]`, so Codex itself refuses an image a text-only model cannot
//! read, and `ling web` tells the phone bridges whether to attach a photo or only its text. A
//! server that does not say (vLLM, llama.cpp) leaves the field out: Codex keeps its own default,
//! and the bridges, which never take silence for a yes, send the text alone.

use std::sync::Mutex;

/// The model server's answer, once the launcher has asked: Some(true) or Some(false), or None when
/// it did not say.
static IMAGE_INPUT: Mutex<Option<bool>> = Mutex::new(None);

/// `has_image_understanding` from a `/model_info` answer.
pub fn image_input_from_info(body: &serde_json::Value) -> Option<bool> {
    body.get("has_image_understanding").and_then(serde_json::Value::as_bool)
}

/// Asks the server at `host`; None when neither endpoint answers with the field.
pub async fn served_image_input(client: &reqwest::Client, host: &str) -> Option<bool> {
    let base = host.trim_end_matches('/');
    for path in ["/model_info", "/get_model_info"] {
        let Ok(response) = client.get(format!("{base}{path}")).send().await else { continue };
        if !response.status().is_success() {
            continue;
        }
        if let Ok(body) = response.json::<serde_json::Value>().await
            && let Some(answer) = image_input_from_info(&body)
        {
            return Some(answer);
        }
    }
    None
}

/// Records this process's answer, before the catalog is written.
pub fn set_image_input(answer: Option<bool>) {
    *IMAGE_INPUT.lock().unwrap_or_else(|p| p.into_inner()) = answer;
}

/// The catalog's `input_modalities` for an answer, or None to leave the field out.
pub fn input_modalities(answer: Option<bool>) -> Option<serde_json::Value> {
    answer.map(|images| if images { serde_json::json!(["text", "image"]) } else { serde_json::json!(["text"]) })
}

/// The catalog's `input_modalities` for this process's answer.
pub fn current_input_modalities() -> Option<serde_json::Value> {
    input_modalities(*IMAGE_INPUT.lock().unwrap_or_else(|p| p.into_inner()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_servers_own_answer_decides_and_silence_says_nothing() {
        assert_eq!(image_input_from_info(&json!({"model_path": "m", "has_image_understanding": true})), Some(true));
        assert_eq!(image_input_from_info(&json!({"has_image_understanding": false})), Some(false));
        assert_eq!(image_input_from_info(&json!({"model_path": "m"})), None);
        assert_eq!(input_modalities(Some(true)), Some(json!(["text", "image"])));
        assert_eq!(input_modalities(Some(false)), Some(json!(["text"])));
        assert_eq!(input_modalities(None), None);
    }

    #[tokio::test]
    async fn the_launcher_asks_model_info_then_its_older_name() {
        use std::io::BufRead;
        use std::io::BufReader;
        use std::io::Write;
        // vLLM-like: no `/model_info`; an older SGLang's `/get_model_info`.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { continue };
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new();
                let _ = reader.read_line(&mut line);
                let mut header = String::new();
                while reader.read_line(&mut header).is_ok_and(|n| n > 2) {
                    header.clear();
                }
                let (status, body) = if line.starts_with("GET /get_model_info ") {
                    ("200 OK", r#"{"has_image_understanding":true}"#)
                } else {
                    ("404 Not Found", "{}")
                };
                let _ = write!(stream, "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            }
        });
        let client = reqwest::Client::builder().no_proxy().build().unwrap();
        assert_eq!(served_image_input(&client, &format!("http://127.0.0.1:{port}/")).await, Some(true));
        assert_eq!(served_image_input(&client, "http://127.0.0.1:9").await, None);
    }
}
