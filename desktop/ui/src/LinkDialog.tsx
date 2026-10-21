// A link in an answer (DESKTOP_PARITY_CLAUDE §2.4, decided 2026-10-21): before anything opens, the
// full address, its site on a line of its own (a link's words can say anything), and Open in your
// browser, Copy the address, Cancel. At `/airgapped on` nothing opens: the dialog says why and offers
// the copy only. The desktop app opens it through the main process, which checks both again
// (links.ts); a browser on `ling web` opens it in a new tab with no opener.

import { useEffect, useRef } from "react";

import { copyText } from "./clipboard";

/** The site an address names, as the dialog shows it apart: the host, its port when one is given. */
export function siteOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export function LinkDialog(props: { url: string; airgappedOn: boolean; onOpen: () => void; onClose: () => void; notice: (message: unknown) => void }) {
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    first.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      props.onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [props]);
  const copy = () => {
    copyText(props.url).then((copied) => (copied ? props.onClose() : props.notice("The address could not be copied: select it and copy it by hand."))).catch(props.notice);
  };
  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog link-dialog" role="dialog" aria-label="Open a link" onClick={(event) => event.stopPropagation()}>
        <h3>{props.airgappedOn ? "Airgapped is on" : "Open this link in your browser?"}</h3>
        <p className="link-site">{siteOf(props.url)}</p>
        <p className="link-url"><code>{props.url}</code></p>
        {props.airgappedOn ? <p className="dialog-note">With airgapped on, Mightling opens no address. You can copy it.</p> : null}
        <div className="dialog-actions">
          {props.airgappedOn ? null : <button ref={first} className="primary" onClick={props.onOpen}>Open in your browser</button>}
          <button ref={props.airgappedOn ? first : undefined} onClick={copy}>Copy the address</button>
          <button onClick={props.onClose}>Cancel</button>
        </div>
      </section>
    </div>
  );
}
