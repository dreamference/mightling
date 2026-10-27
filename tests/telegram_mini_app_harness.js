// Runs ling web's bridge.js in Node's `vm` with a stand-in window (tests/test_telegram_mini_app.py).
const vm = require("vm");
const fs = require("fs");
const [source, hash, answerRaw, optionsRaw] = [
  fs.readFileSync(process.argv[2], "utf8"),
  process.argv[3],
  JSON.parse(process.argv[4]),
  process.argv[5] ? JSON.parse(process.argv[5]) : {},
];
const answers = Array.isArray(answerRaw) ? answerRaw.slice() : [answerRaw];
const record = {
  fetches: [],
  events: [],
  sockets: 0,
  replaced: null,
  replacedHistory: [],
  body: null,
  theme: null,
  openTarget: null,
  openTargetBeforeCheck: null,
};
const storage = new Map();
const documentElement = { dataset: optionsRaw.unpaired ? { unpaired: "1" } : {} };
const body = {
  textContent: "x",
  append(...nodes) {
    record.body = nodes.map((n) => n.children.map((c) => c.textContent).join(" | ")).join(" ");
  },
};
const window = {
  __mightlingPollMs: 5,
  location: {
    hash,
    pathname: "/",
    search: optionsRaw.search || "",
    protocol: "https:",
    host: "node.example.ts.net",
  },
  history: {
    replaceState(_s, _t, url) {
      record.replaced = url;
      record.replacedHistory.push(url);
      const q = url.indexOf("?");
      const h = url.indexOf("#");
      if (q !== -1) {
        window.location.search = h !== -1 ? url.slice(q, h) : url.slice(q);
      } else {
        window.location.search = "";
      }
    },
  },
  sessionStorage: { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v) },
  localStorage: { getItem: () => null },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  TelegramWebviewProxy: { postEvent(type, _data) { record.events.push(type); } },
  addEventListener() {},
  dispatchEvent(e) {
    if (e.data && e.data.channel === "theme") record.theme = e.data.payload;
    if (e.data && e.data.channel === "open-target") record.openTargetEvent = e.data.payload;
  },
  fetch(url, init) {
    const entry = { url };
    if (init && init.body) entry.body = JSON.parse(init.body);
    if (init && init.headers && init.headers["X-Telegram-Init-Data"]) {
      entry.headers = init.headers;
    }
    record.fetches.push(entry);
    const nextAnswer = answers.length > 1 ? answers.shift() : answers[0];
    const status = nextAnswer && typeof nextAnswer.__status === "number" ? nextAnswer.__status : 200;
    return Promise.resolve({ status, json: () => Promise.resolve(nextAnswer) });
  },
};
class WebSocket { constructor() { record.sockets++; this.readyState = 0; } addEventListener() {} send() {} }
WebSocket.OPEN = 1;
const document = {
  documentElement,
  body,
  addEventListener() {},
  createElement() { const n = { children: [], textContent: "", style: {}, append(...c) { n.children.push(...c); } }; return n; },
};
const context = {
  ...window,
  window,
  document,
  WebSocket,
  URLSearchParams,
  JSON,
  Promise,
  setTimeout,
  encodeURIComponent,
  MessageEvent: class { constructor(_t, init) { Object.assign(this, init); } },
  parseInt,
};
context.window = context;
context.history = window.history;
context.location = window.location;
vm.createContext(context);
vm.runInContext(source, context);
record.openTargetBeforeCheck =
  typeof context.electronBridge.openTarget === "function" ? context.electronBridge.openTarget() : null;
context.electronBridge.sendMessageFromView({ hello: 1 });
setTimeout(() => {
  record.dataTheme = documentElement.dataset.theme;
  record.host = documentElement.dataset.host || null;
  record.openTarget =
    typeof context.electronBridge.openTarget === "function" ? context.electronBridge.openTarget() : null;
  record.hasReceiver = !!(context.Telegram && context.Telegram.WebView);
  if (record.hasReceiver) context.Telegram.WebView.receiveEvent("theme_changed", { theme_params: { bg_color: "#ffffff" } });
  console.log(JSON.stringify(record));
}, 50);
