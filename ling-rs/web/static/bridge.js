// `window.electronBridge` in a browser: the object the desktop app's preload exposes, over `ling
// web`'s WebSocket instead of Electron's IPC, so the Mightling UI runs unchanged in both hosts
// (specs/DREAMFERENCE_MIGHTLING_ASK.md §2.2; ling-rs/web/src/relay.rs has the frames). Loaded
// before the UI's own scripts; the page's CSP allows nothing else.
(() => {
  "use strict";
  if (window.electronBridge) return;
  const pending = new Map();
  let next = 1;
  let socket = null;
  let queue = [];

  const dispatch = (data) => window.dispatchEvent(new MessageEvent("message", { data, origin: "mightling-web" }));

  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws`);
    socket.addEventListener("open", () => {
      for (const frame of queue) socket.send(frame);
      queue = [];
    });
    socket.addEventListener("message", (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (data && "answer" in data) {
        const call = pending.get(data.answer);
        if (!call) return;
        pending.delete(data.answer);
        if ("error" in data) call.reject(new Error(String(data.error)));
        else call.resolve(data.result);
      } else if (data && "event" in data) {
        dispatch(data.event);
      }
    });
    socket.addEventListener("close", () => {
      socket = null;
      queue = [];
      for (const call of pending.values()) call.reject(new Error("the connection to ling web closed"));
      pending.clear();
      dispatch({ channel: "work://exit", payload: null });
    });
  }

  function send(frame) {
    if (!socket) connect();
    if (socket.readyState === WebSocket.OPEN) socket.send(frame);
    else queue.push(frame);
  }

  window.electronBridge = {
    sendMessageFromView(message) {
      const call = next++;
      return new Promise((resolve, reject) => {
        pending.set(call, { resolve, reject });
        send(JSON.stringify({ call, message }));
      });
    },
  };
  window.mightlingWindowType = "web";

  // The system theme, as the desktop preload sets it for the first paint, and its changes.
  const scheme = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  document.documentElement.dataset.theme = scheme && scheme.matches ? "dark" : "light";
  if (scheme) scheme.addEventListener("change", (event) => dispatch({ channel: "theme", payload: event.matches ? "dark" : "light" }));
})();
