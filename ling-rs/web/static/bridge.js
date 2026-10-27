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

  // Opened as a Telegram Mini App (TELEGRAM_MOONSHOT §15): Telegram puts its launch parameters in
  // the address's fragment. The page is still `ling web` over the user's own HTTPS front, signed in
  // with this device's credential; on top of that the server checks the launch was signed by this
  // node's bot for a paired Telegram user, and nothing connects until it says so. Telegram's own
  // script is not loaded (the CSP allows only this server's): its two calls are made directly.
  const launch = (() => {
    const params = new URLSearchParams(location.hash.slice(1));
    let initData = params.get("tgWebAppData");
    let themeParams = params.get("tgWebAppThemeParams");
    if (initData) {
      try {
        history.replaceState(null, "", location.pathname + location.search);
      } catch (_) {
        // Ignore if history is unavailable.
      }
      try {
        window.sessionStorage.setItem("mightling.telegram", JSON.stringify({ initData, themeParams }));
      } catch (_) {
        // No storage: the current launch still proceeds with initData in memory.
      }
    } else {
      try {
        const kept = JSON.parse(window.sessionStorage.getItem("mightling.telegram") || "null");
        if (kept) ({ initData, themeParams } = kept);
      } catch (_) {
        // No storage: a reload outside Telegram's launch is an ordinary browser tab.
      }
    }
    return initData ? { initData, themeParams } : null;
  })();

  function telegramEvent(type, data) {
    const proxy = window.TelegramWebviewProxy;
    if (proxy && typeof proxy.postEvent === "function") proxy.postEvent(type, JSON.stringify(data || {}));
  }

  function telegramTheme(themeParams) {
    let theme = null;
    try {
      theme = typeof themeParams === "string" ? JSON.parse(themeParams) : themeParams;
    } catch (_) {
      return null;
    }
    const color = theme && /^#[0-9a-f]{6}$/i.test(theme.bg_color || "") ? theme.bg_color : null;
    if (!color) return null;
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128 ? "dark" : "light";
  }

  function refuse(reason) {
    const show = () => {
      document.body.textContent = "";
      const main = document.createElement("main");
      main.style.cssText = "font: 16px system-ui, sans-serif; padding: 24px; line-height: 1.5;";
      const title = document.createElement("h1");
      title.textContent = "Mightling";
      const text = document.createElement("p");
      text.textContent = reason;
      main.append(title, text);
      document.body.append(main);
    };
    if (document.body) show();
    else document.addEventListener("DOMContentLoaded", show);
  }

  function showPairingCode(code) {
    const show = () => {
      document.body.textContent = "";
      const main = document.createElement("main");
      main.style.cssText = "font: 16px system-ui, sans-serif; padding: 24px; line-height: 1.5; text-align: center;";
      const title = document.createElement("h1");
      title.textContent = "Approve this device in the bot";
      const text = document.createElement("p");
      text.style.cssText = "font: 600 28px ui-monospace, monospace; letter-spacing: 0.08em; margin: 16px 0;";
      text.textContent = code;
      main.append(title, text);
      document.body.append(main);
    };
    if (document.body) show();
    else document.addEventListener("DOMContentLoaded", show);
  }

  function validRepoPart(part) {
    return (
      typeof part === "string" &&
      part.length >= 1 &&
      part.length <= 64 &&
      !part.startsWith(".") &&
      !part.startsWith("-") &&
      /^[A-Za-z0-9._-]+$/.test(part)
    );
  }

  function validRepoName(name) {
    if (typeof name !== "string") return false;
    const slash = name.indexOf("/");
    if (slash <= 0 || name.indexOf("/", slash + 1) !== -1) return false;
    return validRepoPart(name.slice(0, slash)) && validRepoPart(name.slice(slash + 1));
  }

  function validOpenThreadId(id) {
    if (typeof id !== "string" || id.length === 0) return false;
    if (/^[0-9a-f]{8}$/.test(id)) return true;
    return (
      id.length >= 2 &&
      id.length <= 64 &&
      !id.startsWith("-") &&
      !id.endsWith("-") &&
      id.includes("-") &&
      /^[0-9a-fA-F-]+$/.test(id)
    );
  }

  function parseOpenTarget(search) {
    const params = new URLSearchParams(search || "");
    const raw = params.get("open");
    if (!raw) return null;
    const approval = params.get("approval") === "1";
    if (raw === "repos") return { kind: "repos", approval };
    if (raw.startsWith("repo:")) {
      const repo = raw.slice(5);
      return validRepoName(repo) ? { kind: "repo", repo, approval } : null;
    }
    if (raw.startsWith("thread:")) {
      const threadId = raw.slice(7);
      return validOpenThreadId(threadId) ? { kind: "thread", threadId, approval } : null;
    }
    return null;
  }

  let resolvedOpenTarget = null;
  let openTargetConsumed = false;

  function consumeOpenTarget() {
    if (openTargetConsumed) return resolvedOpenTarget;
    openTargetConsumed = true;
    const search = location.search || "";
    const params = new URLSearchParams(search);
    if (!params.has("open") && !params.has("approval")) return null;
    resolvedOpenTarget = parseOpenTarget(search);
    params.delete("open");
    params.delete("approval");
    const rest = params.toString();
    // Telegram's launch data never goes back into the address (it was cleared above).
    const hash = location.hash && !location.hash.includes("tgWebApp") ? location.hash : "";
    const nextUrl = location.pathname + (rest ? `?${rest}` : "") + hash;
    try {
      history.replaceState(null, "", nextUrl);
    } catch (_) {
      // Ignore if history is unavailable.
    }
    if (resolvedOpenTarget) {
      dispatch({ channel: "open-target", payload: resolvedOpenTarget });
    }
    return resolvedOpenTarget;
  }

  function runCheck() {
    return fetch("/api/telegram/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: launch.initData }),
    })
      .then((response) => {
        if (response && response.status === 401) return { unpaired: true };
        return response.json();
      })
      .then((answer) => {
        if (answer && answer.unpaired) return launchPair();
        if (answer && answer.ok) return true;
        refuse((answer && answer.reason) || "This page could not be checked.");
        return false;
      })
      .catch(() => {
        refuse("ling web did not answer the launch check.");
        return false;
      });
  }

  let reloadingLockedPage = false;

  function pollLaunchPair(pendingId) {
    return fetch(`/api/telegram/launch-pair?pending=${encodeURIComponent(pendingId)}`, {
      method: "GET",
      headers: { "X-Telegram-Init-Data": launch.initData },
    })
      .then((response) => response.json())
      .then((answer) => {
        if (answer && (answer.ok || answer.status === "approved")) {
          const wasLockedPage = document.documentElement.dataset.unpaired === "1";
          delete document.documentElement.dataset.unpaired;
          return runCheck().then((ok) => {
            if (ok && wasLockedPage && typeof location.reload === "function") {
              reloadingLockedPage = true;
              location.reload();
            }
            return ok;
          });
        }
        if (answer && answer.status === "pending") {
          const delay = typeof window.__mightlingPollMs === "number" ? window.__mightlingPollMs : 1500;
          return new Promise((resolve) => setTimeout(resolve, delay)).then(() => pollLaunchPair(pendingId));
        }
        refuse((answer && answer.reason) || "That pairing request has expired or was declined.");
        return false;
      })
      .catch(() => {
        refuse("ling web did not answer the pairing check.");
        return false;
      });
  }

  function launchPair() {
    return fetch("/api/telegram/launch-pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: launch.initData }),
    })
      .then((response) => response.json())
      .then((answer) => {
        if (!answer || !answer.pending || !answer.code) {
          refuse((answer && answer.reason) || "This device could not ask to pair.");
          return false;
        }
        showPairingCode(answer.code);
        return pollLaunchPair(answer.pending);
      })
      .catch(() => {
        refuse("ling web did not answer the pairing request.");
        return false;
      });
  }

  const checked = (launch
    ? document.documentElement.dataset.unpaired === "1"
      ? launchPair()
      : runCheck()
    : Promise.resolve(true)
  ).then((ok) => {
    if (ok && !reloadingLockedPage) consumeOpenTarget();
    return ok;
  });

  if (launch) {
    document.documentElement.dataset.host = "telegram";
    window.Telegram = window.Telegram || {};
    window.Telegram.WebView = window.Telegram.WebView || {
      receiveEvent(type, data) {
        if (type !== "theme_changed") return;
        const theme = telegramTheme(data && data.theme_params);
        if (theme) dispatch({ channel: "theme", payload: theme });
      },
    };
    telegramEvent("web_app_ready");
    telegramEvent("web_app_expand");
  }

  function send(frame) {
    checked.then((ok) => {
      if (!ok) return;
      if (!socket) connect();
      if (socket.readyState === WebSocket.OPEN) socket.send(frame);
      else queue.push(frame);
    });
  }

  window.electronBridge = {
    sendMessageFromView(message) {
      const call = next++;
      return new Promise((resolve, reject) => {
        pending.set(call, { resolve, reject });
        send(JSON.stringify({ call, message }));
      });
    },
    openTarget() {
      return resolvedOpenTarget;
    },
  };
  window.mightlingWindowType = "web";

  // The theme for the first paint, as the desktop preload sets it (the page's theme.ts takes over):
  // the choice this browser kept in Settings › Appearance (localStorage, prefs.ts), else the
  // system's; and the system's changes.
  const scheme = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  let chosen = null;
  try {
    const stored = JSON.parse(window.localStorage.getItem("mightling.prefs") || "null");
    if (stored && (stored.theme === "light" || stored.theme === "dark")) chosen = stored.theme;
  } catch (_) {
    // No storage (a private window, blocked site data): the system's theme.
  }
  // Inside Telegram with no choice of its own, the page follows Telegram's theme.
  if (!chosen && launch) chosen = telegramTheme(launch.themeParams);
  document.documentElement.dataset.theme = chosen || (scheme && scheme.matches ? "dark" : "light");
  if (scheme) scheme.addEventListener("change", (event) => dispatch({ channel: "theme", payload: event.matches ? "dark" : "light" }));
})();
