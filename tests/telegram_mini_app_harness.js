// Runs ling web's bridge.js in Node's `vm` with a stand-in window (tests/test_telegram_mini_app.py).
const vm = require("vm");
const fs = require("fs");
const [source, hash, answer] = [fs.readFileSync(process.argv[2], "utf8"), process.argv[3], JSON.parse(process.argv[4])];
const record = { fetches: [], events: [], sockets: 0, replaced: null, body: null, theme: null };
const storage = new Map();
const listeners = {};
const documentElement = { dataset: {} };
const body = { textContent: "x", append(...nodes) { record.body = nodes.map((n) => n.children.map((c) => c.textContent).join(" | ")).join(" "); } };
const window = {
  location: { hash, pathname: "/", search: "", protocol: "https:", host: "node.example.ts.net" },
  history: { replaceState(_s, _t, url) { record.replaced = url; } },
  sessionStorage: { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v) },
  localStorage: { getItem: () => null },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  TelegramWebviewProxy: { postEvent(type, data) { record.events.push(type); } },
  addEventListener() {},
  dispatchEvent(e) { if (e.data && e.data.channel === "theme") record.theme = e.data.payload; },
  fetch(url, init) { record.fetches.push({ url, body: JSON.parse(init.body) }); return Promise.resolve({ json: () => Promise.resolve(answer) }); },
};
class WebSocket { constructor() { record.sockets++; this.readyState = 0; } addEventListener() {} send() {} }
WebSocket.OPEN = 1;
const document = {
  documentElement, body,
  addEventListener() {},
  createElement() { const n = { children: [], textContent: "", style: {}, append(...c) { n.children.push(...c); } }; return n; },
};
const context = { ...window, window, document, WebSocket, URLSearchParams, JSON, Promise, MessageEvent: class { constructor(_t, init) { Object.assign(this, init); } }, parseInt };
context.window = context;
context.history = window.history;
context.location = window.location;
vm.createContext(context);
vm.runInContext(source, context);
context.electronBridge.sendMessageFromView({ hello: 1 });
setTimeout(() => {
  record.dataTheme = documentElement.dataset.theme;
  record.host = documentElement.dataset.host || null;
  record.hasReceiver = !!(context.Telegram && context.Telegram.WebView);
  if (record.hasReceiver) context.Telegram.WebView.receiveEvent("theme_changed", { theme_params: { bg_color: "#ffffff" } });
  console.log(JSON.stringify(record));
}, 50);
