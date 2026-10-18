//! What `ling web` serves from the sidecars on this machine (specs/DREAMFERENCE_MIGHTLING_ASK.md
//! §6, §7): the images the image search service kept, read from its store, and the microphone's
//! audio, passed to the speech-to-text service for text. Both services are started by `ling-admin
//! images start` and `ling-admin voice start`; neither is reached anywhere but on loopback.

use std::path::Path;
use std::time::Duration;

use tokio::io::AsyncReadExt;
use tokio::io::AsyncWriteExt;
use tokio::net::TcpStream;

/// The image store, under the user's home folder (`image_search_sidecar.py` IMAGE_STORE_DIR).
pub const IMAGE_STORE: &str = ".config/dreamference/image-search/data/images";

/// The speech-to-text service (`speech_sidecar.py`), published on loopback only.
pub const SPEECH_ADDR: &str = "127.0.0.1:8100";

/// The model it transcribes with (`speech_sidecar.py` STT_MODEL).
pub const SPEECH_MODEL: &str = "Systran/faster-whisper-small";

/// The largest recording `/api/transcribe` takes: minutes of compressed speech.
pub const AUDIO_CAP: u64 = 25 * 1024 * 1024;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
/// A first transcription may wait for the model to load.
const ANSWER_TIMEOUT: Duration = Duration::from_secs(300);
const MAX_ANSWER_BYTES: u64 = 1024 * 1024;

/// Whether `name` is a stored image's file name: 16 lowercase hex digits and `.jpg`, which is all
/// the service writes. Nothing else is looked up, so no path can leave the store.
pub fn image_name_ok(name: &str) -> bool {
    name.strip_suffix(".jpg")
        .is_some_and(|stem| stem.len() == 16 && stem.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')))
}

/// Reads a stored image. A link is refused: the store holds only files the service wrote.
pub async fn read_image(store: &Path, name: &str) -> Option<Vec<u8>> {
    if !image_name_ok(name) {
        return None;
    }
    let path = store.join(name);
    let metadata = tokio::fs::symlink_metadata(&path).await.ok()?;
    if !metadata.is_file() {
        return None;
    }
    tokio::fs::read(&path).await.ok()
}

/// The media type of a recording, without parameters, when it is audio.
pub fn audio_type(content_type: Option<&str>) -> Option<String> {
    let media = content_type?.split(';').next()?.trim().to_ascii_lowercase();
    (media.starts_with("audio/") && media.len() > "audio/".len()).then_some(media)
}

/// Why a transcription failed.
#[derive(Debug, PartialEq, Eq)]
pub enum SpeechError {
    /// Nothing answers: the service is not running.
    Unavailable,
    /// It answered, but not with text.
    Failed(String),
}

fn extension(media: &str) -> &'static str {
    match media {
        "audio/ogg" => "ogg",
        "audio/mp4" | "audio/m4a" | "audio/x-m4a" | "audio/aac" => "m4a",
        "audio/wav" | "audio/x-wav" | "audio/wave" => "wav",
        "audio/mpeg" | "audio/mp3" => "mp3",
        _ => "webm",
    }
}

/// The `multipart/form-data` body the service's `/v1/audio/transcriptions` takes.
pub fn multipart(boundary: &str, audio: &[u8], media: &str) -> Vec<u8> {
    let mut body = Vec::with_capacity(audio.len() + 512);
    for (name, value) in [("model", SPEECH_MODEL), ("response_format", "json")] {
        body.extend_from_slice(format!("--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").as_bytes());
    }
    body.extend_from_slice(
        format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"speech.{}\"\r\nContent-Type: {media}\r\n\r\n",
            extension(media)
        )
        .as_bytes(),
    );
    body.extend_from_slice(audio);
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
    body
}

/// Splits a raw HTTP response into its status and body, decoding a chunked body.
pub fn parse_response(raw: &[u8]) -> Result<(u16, Vec<u8>), String> {
    let split = raw.windows(4).position(|window| window == b"\r\n\r\n").ok_or("no end of headers")?;
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
    Ok((status, if chunked { dechunk(body)? } else { body.to_vec() }))
}

fn dechunk(mut body: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let end = body.windows(2).position(|window| window == b"\r\n").ok_or("bad chunk")?;
        let size_text = String::from_utf8_lossy(&body[..end]);
        let size = usize::from_str_radix(size_text.split(';').next().unwrap_or_default().trim(), 16).map_err(|_| "bad chunk size")?;
        body = &body[end + 2..];
        if size == 0 {
            return Ok(out);
        }
        if body.len() < size {
            return Err("short chunk".into());
        }
        out.extend_from_slice(&body[..size]);
        body = body.get(size + 2..).unwrap_or_default();
    }
}

/// Sends a recording to the service at `addr` and returns the text.
pub async fn transcribe(addr: &str, audio: &[u8], media: &str) -> Result<String, SpeechError> {
    let mut stream = match tokio::time::timeout(CONNECT_TIMEOUT, TcpStream::connect(addr)).await {
        Ok(Ok(stream)) => stream,
        _ => return Err(SpeechError::Unavailable),
    };
    let boundary = format!("mightling-{:016x}", rand::random::<u64>());
    let body = multipart(&boundary, audio, media);
    let head = format!(
        "POST /v1/audio/transcriptions HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\nAccept: application/json\r\n\
Content-Type: multipart/form-data; boundary={boundary}\r\nContent-Length: {}\r\n\r\n",
        body.len()
    );
    let exchange = async {
        stream.write_all(head.as_bytes()).await?;
        stream.write_all(&body).await?;
        let mut raw = Vec::new();
        (&mut stream).take(MAX_ANSWER_BYTES).read_to_end(&mut raw).await?;
        Ok::<_, std::io::Error>(raw)
    };
    let raw = match tokio::time::timeout(ANSWER_TIMEOUT, exchange).await {
        Ok(Ok(raw)) => raw,
        Ok(Err(err)) => return Err(SpeechError::Failed(err.to_string())),
        Err(_) => return Err(SpeechError::Failed("no answer in time".into())),
    };
    let (status, body) = parse_response(&raw).map_err(SpeechError::Failed)?;
    if status != 200 {
        let detail = String::from_utf8_lossy(&body).chars().take(200).collect::<String>();
        return Err(SpeechError::Failed(format!("HTTP {status}: {detail}")));
    }
    let answer: serde_json::Value = serde_json::from_slice(&body).map_err(|err| SpeechError::Failed(err.to_string()))?;
    answer
        .get("text")
        .and_then(|text| text.as_str())
        .map(|text| text.trim().to_string())
        .ok_or_else(|| SpeechError::Failed("no text in the answer".into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_names_the_service_writes_are_images() {
        assert!(image_name_ok("0123456789abcdef.jpg"));
        for bad in ["0123456789ABCDEF.jpg", "0123456789abcde.jpg", "../0123456789abcdef.jpg", "0123456789abcdef.png", "a.jpg", ""] {
            assert!(!image_name_ok(bad), "{bad}");
        }
    }

    #[test]
    fn only_audio_is_transcribed() {
        assert_eq!(audio_type(Some("audio/webm;codecs=opus")), Some("audio/webm".to_string()));
        assert_eq!(audio_type(Some("Audio/OGG")), Some("audio/ogg".to_string()));
        assert_eq!(audio_type(Some("text/plain")), None);
        assert_eq!(audio_type(Some("audio/")), None);
        assert_eq!(audio_type(None), None);
    }

    #[test]
    fn the_form_names_the_model_and_carries_the_audio() {
        let body = String::from_utf8_lossy(&multipart("b", b"RIFFdata", "audio/wav")).into_owned();
        assert!(body.starts_with("--b\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nSystran/faster-whisper-small\r\n"));
        assert!(body.contains("filename=\"speech.wav\"\r\nContent-Type: audio/wav\r\n\r\nRIFFdata\r\n--b--\r\n"));
    }

    #[test]
    fn responses_are_parsed_plain_and_chunked() {
        assert_eq!(parse_response(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi").unwrap(), (200, b"hi".to_vec()));
        let chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nhe\r\n3\r\nllo\r\n0\r\n\r\n";
        assert_eq!(parse_response(chunked).unwrap(), (200, b"hello".to_vec()));
    }
}
