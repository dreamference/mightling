// The repository picker on `ling web` (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §4.3, §10 G4,
// §12.4): `GET /api/repos` ordered with the most recently pushed first, filtered by name, with
// marks for private (`🔒`), archived (read-only), mirror, and open checkouts. Opening a repository
// calls `POST /api/repos/open` and starts or resumes a `chat-work` thread in the checkout's `cwd`.
// When `/api/repos` answers 404, the view says the forge is off (`ling-admin forge on`) and offers
// nothing else.

import { useEffect, useMemo, useState } from "react";

import * as bridge from "./bridge";
import { Icon } from "./icons";
import {
  chatRepos,
  sortAndFilterRepos,
  type ChatRepoOpened,
  type ChatRepoSummary,
  type ChatReposApi,
} from "./repos";

export interface ReposViewProps {
  selectedRepo?: string | null;
  initialRepos?: ChatRepoSummary[] | null;
  initialForgeOff?: boolean;
  initialSearch?: string;
  api?: ChatReposApi;
  onSelectRepo?: (repo: string) => void;
  onOpenRepo?: (repo: string) => Promise<ChatRepoOpened | void> | void;
  onClose?: () => void;
}

export function ReposView(props: ReposViewProps) {
  const api = useMemo(() => props.api ?? chatRepos(), [props.api]);
  const [repos, setRepos] = useState<ChatRepoSummary[] | null>(props.initialRepos ?? null);
  const [forgeOff, setForgeOff] = useState<boolean>(props.initialForgeOff ?? false);
  const [search, setSearch] = useState<string>(props.initialSearch ?? "");
  const [chosen, setChosen] = useState<string | null>(props.selectedRepo ?? null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (props.selectedRepo !== undefined) setChosen(props.selectedRepo);
  }, [props.selectedRepo]);

  useEffect(() => {
    if (props.initialRepos !== undefined || props.initialForgeOff !== undefined) return;
    let live = true;
    setError(null);
    api.list()
      .then((answer) => {
        if (!live) return;
        if (answer.forgeOff) {
          setForgeOff(true);
          setRepos([]);
        } else {
          setForgeOff(false);
          setRepos(answer.repos);
        }
      })
      .catch((failure: unknown) => {
        if (!live) return;
        setError(failure instanceof Error ? failure.message : String(failure));
      });
    return () => {
      live = false;
    };
  }, [api, props.initialRepos, props.initialForgeOff]);

  const filtered = useMemo(() => sortAndFilterRepos(repos ?? [], search), [repos, search]);

  if (bridge.host() !== "web") return null;

  if (forgeOff) {
    return (
      <section className="repos-view forge-off" aria-label="Repositories">
        <p className="empty">The forge is off. Run <code>ling-admin forge on</code> at the node.</p>
      </section>
    );
  }

  const open = async (repo: string) => {
    setChosen(repo);
    props.onSelectRepo?.(repo);
    if (!props.onOpenRepo) return;
    setBusy(repo);
    setError(null);
    try {
      await props.onOpenRepo(repo);
    } catch (failure: unknown) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="repos-view" aria-label="Repositories">
      <header className="repos-header">
        <h2>Repositories</h2>
        <span className="spacer" />
        {props.onClose ? (
          <button className="icon" aria-label="Close repositories" onClick={props.onClose}>
            <Icon name="x" size={16} />
          </button>
        ) : null}
      </header>
      <label className="search repos-search">
        <Icon name="search" size={14} />
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search repositories"
          aria-label="Search repositories"
        />
      </label>
      {error ? <div className="notice">{error}</div> : null}
      {repos === null ? (
        <p className="empty small">Reading repositories…</p>
      ) : filtered.length === 0 ? (
        <p className="empty small">{search.trim() ? "No repositories match." : "No repositories on the forge yet."}</p>
      ) : (
        <ul className="repos-list" role="listbox" aria-label="Repositories">
          {filtered.map((entry) => {
            const isSelected = chosen === entry.repo;
            const checkoutsCount = entry.checkouts?.length ?? 0;
            return (
              <li
                key={entry.repo}
                role="option"
                aria-selected={isSelected}
                data-repo={entry.repo}
                className={`repo-row${isSelected ? " selected" : ""}`}
              >
                <button
                  className="repo-open"
                  disabled={busy !== null}
                  onClick={() => void open(entry.repo)}
                >
                  <span className="repo-title">
                    {entry.private ? <span className="repo-mark private" aria-label="Private" title="Private repository">🔒 </span> : null}
                    {entry.repo}
                  </span>
                  <span className="repo-marks">
                    {entry.archived ? (
                      <span className="chip repo-mark archived">archived (read-only)</span>
                    ) : entry.read_only ? (
                      <span className="chip repo-mark read-only">read-only</span>
                    ) : null}
                    {entry.mirror ? <span className="chip repo-mark mirror">mirror</span> : null}
                    <span className="chip repo-checkouts">
                      {checkoutsCount} open checkout{checkoutsCount === 1 ? "" : "s"}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
