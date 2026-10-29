// What the preview tab may load (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.9, Phase 3 item
// 4). The preview is for the user's own pages: a dev server on this machine, a page on the LAN, a
// file. Those load; a site out on the internet loads only once the user allows it (once, or always
// in Settings › Browser), and a blocked one never does. At `/airgapped on` only this machine
// (loopback and files) is reachable, whatever was allowed. Decided before any connection or DNS
// lookup is made, from the URL alone.

export type SiteChoice = "allow" | "block";

export type Reach = "local" | "loopback" | "lan" | "internet" | "refused";

export interface Decision {
  verdict: "allow" | "ask" | "block";
  /** The site the choice is kept under (the host name), "" for a file or an inner page. */
  site: string;
  reach: Reach;
  reason: string;
}

/** IPv4 private, link-local and loopback ranges, and their IPv6 counterparts. */
function ipReach(host: string): Reach | null {
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    if (a === 127) return "loopback";
    if (a === 0) return "loopback";
    if (a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254)) return "lan";
    if (a === 100 && b >= 64 && b <= 127) return "lan"; // carrier-grade NAT: Tailscale's addresses
    return "internet";
  }
  if (host.includes(":")) {
    const bare = host.replace(/^\[|\]$/g, "").toLowerCase();
    if (bare === "::1" || bare === "::") return "loopback";
    if (/^f[cd]/.test(bare) || /^fe[89ab]/.test(bare)) return "lan";
    return "internet";
  }
  return null;
}

/** Where a host is: this machine, the local network, or the internet. */
export function reachOf(host: string): Reach {
  const name = host.toLowerCase().replace(/\.$/, "");
  const ip = ipReach(name);
  if (ip) return ip;
  if (name === "localhost" || name.endsWith(".localhost")) return "loopback";
  // mDNS names and single-label names are the LAN's (a node is reached by its name, never its address).
  if (name.endsWith(".local") || name.endsWith(".lan") || name.endsWith(".home.arpa") || !name.includes(".")) return "lan";
  return "internet";
}

/**
 * Whether the preview may load `url`, given the user's per-site choices, the sites allowed for this
 * session only, and the thread's air-gap level.
 */
export function decide(url: string, sites: Record<string, SiteChoice>, allowedOnce: ReadonlySet<string>, airgapOn: boolean): Decision {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return { verdict: "block", site: "", reach: "refused", reason: "not an address" };
  }
  const scheme = parsed.protocol;
  if (scheme === "file:" || scheme === "data:" || scheme === "blob:" || scheme === "about:") {
    return { verdict: "allow", site: "", reach: "local", reason: "on this machine" };
  }
  if (scheme === "devtools:") return { verdict: "allow", site: "", reach: "local", reason: "the developer tools" };
  if (!["http:", "https:", "ws:", "wss:"].includes(scheme)) {
    return { verdict: "block", site: "", reach: "refused", reason: `${scheme} addresses do not open in the preview` };
  }
  // `example.com.` (the root's dot) is `example.com`: the site its choice is kept under.
  const site = parsed.hostname.replace(/^\[|\]$/g, "").toLowerCase().replace(/\.+$/, "");
  const reach = reachOf(site);
  if (reach === "loopback") return { verdict: "allow", site, reach, reason: "this machine" };
  if (airgapOn) return { verdict: "block", site, reach, reason: "airgapped is on: only this machine is reachable" };
  if (reach === "lan") return { verdict: "allow", site, reach, reason: "the local network" };
  if (sites[site] === "block") return { verdict: "block", site, reach, reason: `${site} is blocked in Settings › Browser` };
  if (sites[site] === "allow" || allowedOnce.has(site)) return { verdict: "allow", site, reach, reason: `${site} is allowed` };
  return { verdict: "ask", site, reach, reason: `${site} is on the internet` };
}

/** What the address bar's text means: a URL as typed, a local address without its scheme, or a file path. */
export function addressOf(typed: string): string | null {
  const text = typed.trim();
  if (!text) return null;
  if (text.startsWith("/")) return `file://${text}`;
  // A Windows path, `C:\site\index.html`: a file URL has forward slashes.
  if (/^[a-z]:[\\/]/i.test(text)) return `file:///${text.replace(/\\/g, "/")}`;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text) || /^(about|data):/i.test(text)) return text;
  // `localhost:5173`, `gx10.local/x`, `example.com`: http for this machine and the LAN, https beyond.
  const host = text.split(/[/:?#]/)[0];
  return `${reachOf(host) === "internet" ? "https" : "http"}://${text}`;
}

/** Copied into the previewed page's isolated world for comment mode: resolves with the element clicked, or null on Escape. */
export const PICK_SCRIPT = `new Promise((resolve) => {
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #0abab5;background:rgba(10,186,181,0.12);border-radius:3px;transition:all 60ms";
  document.documentElement.appendChild(box);
  const selectorOf = (el) => {
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && parts.length < 5; node = node.parentElement) {
      let part = node.localName;
      if (node.id) { parts.unshift(part + "#" + CSS.escape(node.id)); break; }
      const classes = [...node.classList].slice(0, 2).map((c) => "." + CSS.escape(c)).join("");
      part += classes;
      const parent = node.parentElement;
      if (parent) {
        const same = [...parent.children].filter((child) => child.localName === node.localName);
        if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(node) + 1) + ")";
      }
      parts.unshift(part);
    }
    return parts.join(" > ");
  };
  // A click picks an element; a drag over the page picks an area (DESKTOP §16 B29).
  let start = null;
  let dragged = false;
  const over = (event) => {
    if (start) return;
    const rect = event.target.getBoundingClientRect();
    Object.assign(box.style, { left: rect.left + "px", top: rect.top + "px", width: rect.width + "px", height: rect.height + "px" });
  };
  const area = (event) => ({ x: Math.round(Math.min(start.x, event.clientX)), y: Math.round(Math.min(start.y, event.clientY)), width: Math.round(Math.abs(event.clientX - start.x)), height: Math.round(Math.abs(event.clientY - start.y)) });
  const down = (event) => { if (event.button === 0) { start = { x: event.clientX, y: event.clientY }; dragged = false; event.preventDefault(); } };
  const move = (event) => {
    if (!start) return;
    const a = area(event);
    if (a.width + a.height < 12) return;
    dragged = true;
    Object.assign(box.style, { left: a.x + "px", top: a.y + "px", width: a.width + "px", height: a.height + "px" });
  };
  const up = (event) => {
    if (!start) return;
    if (dragged) {
      event.preventDefault();
      event.stopPropagation();
      const a = area(event);
      const inside = document.elementFromPoint(a.x + a.width / 2, a.y + a.height / 2);
      done({ selector: inside && inside !== box ? selectorOf(inside) : "body", tag: "area", text: "", url: location.href, area: a });
    }
    start = null;
  };
  const done = (value) => {
    removeEventListener("mouseover", over, true);
    removeEventListener("click", click, true);
    removeEventListener("keydown", key, true);
    removeEventListener("mousedown", down, true);
    removeEventListener("mousemove", move, true);
    removeEventListener("mouseup", up, true);
    box.remove();
    resolve(value);
  };
  const click = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (dragged) return;
    const el = event.target;
    done({ selector: selectorOf(el), tag: el.localName, text: (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ").slice(0, 160), url: location.href });
  };
  const key = (event) => { if (event.key === "Escape") { event.preventDefault(); done(null); } };
  addEventListener("mouseover", over, true);
  addEventListener("click", click, true);
  addEventListener("keydown", key, true);
  addEventListener("mousedown", down, true);
  addEventListener("mousemove", move, true);
  addEventListener("mouseup", up, true);
})`;

/** How long one console line of a preview may be (the agent's console tool). */
const CONSOLE_LINE = 2000;

/** A console message as one line: its level, its text, where it came from. Both of Electron's event forms. */
export function consoleLine(first: unknown, ...legacy: unknown[]): string {
  const details = (typeof first === "object" && first !== null && "message" in first ? first : null) as { level?: unknown; message?: unknown; lineNumber?: unknown; sourceId?: unknown } | null;
  const levels = ["debug", "info", "warning", "error"];
  const level = details ? String(details.level ?? "info") : levels[Number(legacy[0])] ?? "info";
  const message = String(details ? details.message : legacy[1] ?? "");
  const line = details ? details.lineNumber : legacy[2];
  const source = String(details ? details.sourceId ?? "" : legacy[3] ?? "");
  const where = source ? ` (${source.split("/").pop()}${line ? `:${line}` : ""})` : "";
  return `[${level}] ${message}${where}`.slice(0, CONSOLE_LINE);
}

