//! What a phone message with attachments becomes (specs/DREAMFERENCE_MIGHTLING_CHAT.md §16).
//!
//! An adapter downloads what the user sent ([`Media`]) and names what it could not take
//! (`dropped`: a voice message, a sticker). The hub uploads each file into the thread's Ask folder
//! through `ling web`, which reads an image's text and says whether the served model takes images;
//! [`compose`] then builds the turn: an image as an image when the model takes images, its text as
//! `[Screenshot text: …]` whenever any was read, another file named. With nothing the model could
//! use, the bridge answers itself and no turn starts: an empty message never reaches the model.

use serde_json::Value;
use serde_json::json;

use ling_web_server::ask_client::Saved;

/// One file from the messenger.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Media {
    pub name: String,
    pub mime: String,
    pub bytes: Vec<u8>,
}

impl Media {
    pub fn is_image(&self) -> bool {
        self.mime.starts_with("image/")
    }
}

/// An attachment after its upload: its name, whether it is an image, and what `ling web` said.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Uploaded {
    pub name: String,
    pub image: bool,
    pub saved: Result<Saved, String>,
}

pub const UNREADABLE_IMAGE: &str = "I can't read this image; describe it or paste the text.";
pub const NOT_RECEIVED: &str = "I couldn't receive that attachment; send it again, or describe it or paste the text.";

/// The line saying what an adapter dropped, e.g. "I can't take a voice message here, so it was
/// dropped; send text, an image or a file."
pub fn dropped_notice(dropped: &[String]) -> Option<String> {
    if dropped.is_empty() {
        return None;
    }
    Some(format!("I can't take {} here, so it was dropped; send text, an image or a file.", dropped.join(" or ")))
}

/// The turn's input (`turn/start`'s `input`), or the bridge's own reply when nothing is usable.
pub fn compose(text: &str, uploads: &[Uploaded]) -> Result<Value, String> {
    let mut notes: Vec<String> = Vec::new();
    let mut images: Vec<String> = Vec::new();
    let mut files: Vec<String> = Vec::new();
    let (mut unreadable, mut missing) = (0, 0);
    for upload in uploads {
        let Ok(saved) = &upload.saved else {
            missing += 1;
            continue;
        };
        if !upload.image {
            files.push(std::path::Path::new(&saved.path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| upload.name.clone()));
            continue;
        }
        if saved.vision {
            images.push(saved.path.clone());
        }
        match saved.text.as_deref().map(str::trim).filter(|t| !t.is_empty()) {
            Some(read) => notes.push(format!("[Screenshot text: {read}]")),
            None if !saved.vision => unreadable += 1,
            None => {}
        }
    }
    if !files.is_empty() {
        notes.push(format!("[Attached and saved in this thread's folder: {}]", files.join(", ")));
    }
    let asked = text.trim();
    if asked.is_empty() && notes.is_empty() && images.is_empty() {
        return Err(if unreadable > 0 || missing == 0 { UNREADABLE_IMAGE } else { NOT_RECEIVED }.to_string());
    }
    if unreadable > 0 {
        notes.push("[An attached image could not be read, and this model does not take images.]".to_string());
    }
    if missing > 0 {
        notes.push(format!("[{missing} attachment(s) could not be received.]"));
    }
    let mut turn = asked.to_string();
    for note in notes {
        if !turn.is_empty() {
            turn.push_str("\n\n");
        }
        turn.push_str(&note);
    }
    let mut input = Vec::new();
    if !turn.is_empty() {
        input.push(json!({ "type": "text", "text": turn, "text_elements": [] }));
    }
    for image in images {
        input.push(json!({ "type": "localImage", "path": image }));
    }
    Ok(Value::Array(input))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shot(text: Option<&str>, vision: bool) -> Uploaded {
        Uploaded { name: "photo.jpg".to_string(), image: true, saved: Ok(Saved { path: "/ask/q/photo.jpg".to_string(), text: text.map(str::to_string), vision }) }
    }

    #[test]
    fn an_image_gives_its_text_and_itself_when_the_model_takes_images() {
        assert_eq!(
            compose("", &[shot(Some("Air purifier"), true)]).unwrap(),
            json!([{"type": "text", "text": "[Screenshot text: Air purifier]", "text_elements": []}, {"type": "localImage", "path": "/ask/q/photo.jpg"}])
        );
        assert_eq!(
            compose("compare it", &[shot(Some("Model X"), false)]).unwrap(),
            json!([{"type": "text", "text": "compare it\n\n[Screenshot text: Model X]", "text_elements": []}])
        );
        assert_eq!(compose("", &[shot(None, true)]).unwrap(), json!([{"type": "localImage", "path": "/ask/q/photo.jpg"}]));
    }

    #[test]
    fn nothing_usable_is_answered_by_the_bridge_and_never_sent_empty() {
        assert_eq!(compose("", &[shot(None, false)]), Err(UNREADABLE_IMAGE.to_string()));
        let lost = Uploaded { name: "photo.jpg".to_string(), image: true, saved: Err("refused".to_string()) };
        assert_eq!(compose("  ", &[lost.clone()]), Err(NOT_RECEIVED.to_string()));
        let words = compose("what is it?", &[lost]).unwrap();
        assert!(words[0]["text"].as_str().unwrap().starts_with("what is it?\n\n[1 attachment(s) could not be received.]"), "{words}");
        assert_eq!(compose("", &[]), Err(UNREADABLE_IMAGE.to_string()));
    }

    #[test]
    fn files_are_named_and_drops_are_said() {
        let pdf = Uploaded { name: "r.pdf".to_string(), image: false, saved: Ok(Saved { path: "/ask/q/r.pdf".to_string(), text: None, vision: true }) };
        assert_eq!(compose("", &[pdf]).unwrap()[0]["text"], json!("[Attached and saved in this thread's folder: r.pdf]"));
        assert_eq!(dropped_notice(&[]), None);
        assert_eq!(
            dropped_notice(&["a voice message".to_string()]).unwrap(),
            "I can't take a voice message here, so it was dropped; send text, an image or a file."
        );
    }
}
