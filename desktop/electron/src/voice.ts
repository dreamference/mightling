// The microphone in the app window (specs/DREAMFERENCE_MIGHTLING_ASK.md §7): the page records with
// `MediaRecorder` and sends the recording here over the window's one channel; the main process
// passes it to the speech-to-text service on this machine (`ling-admin voice start`, loopback
// only) and answers the text, which the page puts in the composer. Nothing is sent to the model,
// and nothing leaves the machine, so it works at `/airgapped on`. No Electron here, so this tests
// on its own.

export const SPEECH_URL = "http://127.0.0.1:8100/v1/audio/transcriptions";
/** The model `dreamference/chat/speech_sidecar.py` fetches (STT_MODEL). */
export const SPEECH_MODEL = "Systran/faster-whisper-small";
/** The largest recording taken: minutes of compressed speech, as `ling web`'s `/api/transcribe`. */
export const AUDIO_CAP = 25 * 1024 * 1024;

export interface Recording {
  /** The recording's media type, as `MediaRecorder` names it (`audio/webm;codecs=opus`). */
  mime: string;
  /** The bytes, base64. */
  data: string;
}

const EXTENSIONS: Record<string, string> = { "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/wav": "wav", "audio/mpeg": "mp3" };

/** Sends a recording to the speech-to-text service and resolves with its text. */
export async function transcribe(recording: Recording, fetcher: typeof fetch = fetch): Promise<{ text: string }> {
  const media = (recording.mime ?? "").split(";")[0].trim().toLowerCase();
  if (!media.startsWith("audio/") || media.length === "audio/".length) throw new Error("the recording is not audio");
  const audio = Buffer.from(recording.data ?? "", "base64");
  if (audio.length === 0) throw new Error("the recording is empty");
  if (audio.length > AUDIO_CAP) throw new Error(`the recording is over ${AUDIO_CAP >> 20} MB`);
  const form = new FormData();
  form.append("model", SPEECH_MODEL);
  form.append("response_format", "json");
  form.append("file", new Blob([audio], { type: media }), `speech.${EXTENSIONS[media] ?? "webm"}`);
  let response: Response;
  try {
    response = await fetcher(SPEECH_URL, { method: "POST", body: form, signal: AbortSignal.timeout(300_000) });
  } catch {
    throw new Error("Speech-to-text is not running on this machine: start it with `ling-admin voice start`.");
  }
  if (!response.ok) throw new Error(`Speech-to-text failed: HTTP ${response.status}`);
  const answer = (await response.json()) as { text?: unknown };
  if (typeof answer.text !== "string") throw new Error("Speech-to-text answered without text");
  return { text: answer.text.trim() };
}
