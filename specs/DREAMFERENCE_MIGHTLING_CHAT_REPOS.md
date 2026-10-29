# Mightling Chat Repos: Work mode on a forge repository from a phone

**Status:** proposed and decided by the owner on 2026-10-27 (§0.1). The same day the owner ordered the Mini App's missing pieces built (§10), together with the `ling web` repository service they need (§4). Phase 1 (G1–G10, G12–G14) and Phase 3's Full Access, worktree pruning and `allow_main` forge branch protection were built and merged on 2026-10-27–2026-10-28 (`ling-rs/web/src/chat_repos.rs`, `repo_limits.rs`, `telegram_app.rs`, `ling-rs/src/chat.rs`, `ling-rs/chat/src/repo_notice.rs`, `desktop/ui/`, `dreamference/forge/forge_phone.py`). Phase 0's live measurements, Phase 2 (Signal and Matrix text commands) and G11 (Discord's front) remain (§13).

**Spec owner:** the owner. **Names:** post-rename (`ling`, `ling-admin`, `~/.mightling`). Machines use the repository's placeholders: `gx10-1a2b` is the main node, `node-b` another node, and `<front>` is `ling web`'s HTTPS front on the owner's tailnet. Real names and addresses are never written here.

**Builds on:**
- [MIGHTLING_FORGEJO](./DREAMFERENCE_MIGHTLING_FORGEJO.md): the forge of record on the main node, read-only mirrors elsewhere, private repositories never mirrored out, the pre-receive anonymity hook (§3.2);
- [MIGHTLING_CHAT](./DREAMFERENCE_MIGHTLING_CHAT.md): `ling chat` with its hub and adapters, approvals (§3), the fixes of §16;
- [MIGHTLING_TELEGRAM_MOONSHOT](./DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md): topics (§14), nonces, and the Mini App (§15), which loads `ling web` directly from the node;
- [MIGHTLING_DISCORD](./DREAMFERENCE_MIGHTLING_DISCORD.md): the link button to `ling web` (§3.11), never an Activity;
- [MIGHTLING_SIGNAL](./DREAMFERENCE_MIGHTLING_SIGNAL.md): Note to Self, YES/NO approvals (§8), the benchmark refusal (§17.3), Night Shift from the phone (§19.7);
- [MIGHTLING_SYNC](./DREAMFERENCE_MIGHTLING_SYNC.md): one writer per thread, answers from any paired device, hand-over;
- [MIGHTLING_WORKTREE](./DREAMFERENCE_MIGHTLING_WORKTREE.md) §3.3: git writes fail inside workspace-write;
- [MIGHTLING_DESKTOP](./DREAMFERENCE_MIGHTLING_DESKTOP.md) backlog B9: two threads on one checkout;
- [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md) §14.7: Full Access refused at `on`.

**Supersedes:**
- SIGNAL §14's "Work threads" (`/work <project>`, `[signal] projects`);
- CHAT §13's Phase 3 Work threads;
- CHAT §10's `night_repos` (§11.2).

---

## 0. Decisions, stated first

1. **A phone chat can switch between forge repositories and work in the chosen one in full Work mode,** as in the desktop app's Work view. The agent edits a checkout of the repository, and approvals are answered on the phone. `/repo off` goes back to Ask.
2. **Every forge repository is reachable, private ones included,** the pre-rewrite archive among them. Nothing outside the forge is reachable: no GitHub URL, no folder path, no other host.
3. **Phone edits land in a checkout of their own, on their own branch.** Each thread gets a worktree under `~/.mightling/chat-repos/<org>/<repo>/<thread>` on the node, on the branch `phone/<thread>`, cloned from the forge of record. The owner's own working tree is never touched. `/merge` brings the branch in when the owner is happy. Working on `main` directly is an opt-in per repository, never the default (§3.3).
4. **On Telegram and Discord, repo work is private by design, not by consent.**
   - **Where the work happens:** Work mode, diffs, code, file contents and any approval that shows code happen only inside `ling web`'s own page. On Telegram that's the Mini App, which the phone loads straight from the node over the tailnet (MOONSHOT §15). On Discord it's the link button that opens `<front>` (DISCORD §3.11).
   - **What the bot sends:** only fixed-template notices, such as "Turn finished in mightling: 3 files changed", with a button that opens the page. It never sends code, file text, file names, commands or the model's words. A typed test proves this (§12.2).
   - **No consent line:** because no code reaches Telegram's or Discord's servers, private repositories need none.
5. **On Signal and Matrix, code can be shown inline.**
   - **Signal** is end-to-end encrypted. Matrix stays on the owner's own machines (CHAT §5.4).
   - **Text commands** carry the whole flow on both, including diffs, approvals with commands, and the agent's answers.
6. **`ling web` does all the git work.**
   - **Why the agent can't:** inside workspace-write, `.git` is read-only (WORKTREE §3.3).
   - **Why the bridges can't:** the Signal bridge runs as a system account that can't see the user's home.
   - **So:** cloning, worktrees, commit, push, merge and pull requests are `ling web` routes (`chat_repos.rs`, the `night_phone.rs` pattern). Every bridge and the page are clients of those routes.
7. **Pushes go to the forge and nowhere else.** The forge mirrors every branch of a non-private repository to GitHub (FORGEJO §3.2, this project's fleet). So `ling web` runs the anonymity check before every push, and the forge's pre-receive hook runs it again. Private repositories are never mirrored.
8. **The existing rules still hold:**
   - the air-gap levels, and Full Access refused at `on`;
   - approvals answered once, by nonce, with nothing approved by silence;
   - SWE-bench nights reserve the model;
   - Night Shift gives way to a person.

   Full Access from a phone also needs the node's own policy to allow it, plus a typed confirmation (§6.3).

### 0.1 The owner's decisions (2026-10-27)

| # | Question | Answer |
|---|---|---|
| R1 | What may a chat do in a repository? | **Full Work mode**, as the desktop app's Work: edits in a checkout, approvals on the phone |
| R2 | Which repositories? | **Every forge repository, private ones included** (the pre-rewrite archive too) |
| R3 | Where phone edits land | **A separate checkout, on `phone/<thread>`**, so the owner's desk work is never clobbered; `/merge` brings them in; working on `main` is not the default |
| R4 | Private repositories on Telegram and Discord | **Reachable, and private by design:** repo work runs in the Mini App (Telegram) or the `ling web` link (Discord), which load from the node; bot messages carry titles and short status only. No consent line |
| R5 | Signal and Matrix | **Code inline**, as specified (§5.2) |
| R6 | The Mini App's gaps | **Build them** (§10), in the order given there |

---

## 1. What the owner gets

- **From Signal or Matrix:**
  - `/repo` lists the forge's repositories, with private ones marked.
  - `/repo mightling` switches the conversation to that repository: a Work thread starts in a fresh checkout on `phone/3f2a9c1e`, and the status line reads `mightling · phone/3f2a9c1e`.
  - Every message after that is a Work turn. Diffs, approvals and answers arrive inline.
  - `/commit`, `/push` and `/merge` finish the job, and `/repo off` goes back to Ask.
- **From Telegram:**
  - `/repo` (or the menu button) opens the Mini App on its **Repos** view.
  - Picking a repository opens the same Work view the desktop app has: the thread, diffs, approvals, command output, files, branch and merge.
  - Each repository has its own topic in the bot's chat. While the owner is away from the page, notices land there, such as "Approval waiting in mightling" with an **Open** button.
- **From Discord:** the same as Telegram, with the link button in place of the Mini App, and the DM's status line in place of topics.
- **From any device:** with sync on, the phone's thread appears on the laptop and in the desktop app live (SYNC §7.3). It can be followed, answered, or handed over there.

## 2. Commands (the same names on every bridge)

| Command | What it does | Signal, Matrix | Telegram, Discord bot |
|---|---|---|---|
| `/repo` | Lists forge repositories, private ones marked 🔒, archived ones marked read-only, the chat's recent ones first | Numbered list; `/repo 2` or `/repo <name>` picks one | A button that opens the page's Repos view |
| `/repo <name>` | Switches to that repository: it resumes the chat's last thread there, or starts one in a new checkout | Inline | Opens the page on that repository |
| `/repo off` | Back to Ask (the last Ask thread, or a new one by the gap rule). The checkout and its changes stay, and one line counts the uncommitted files | Inline | Inline (no content) |
| `/new` | In a repository: a new Work thread in a new checkout, on its own branch from the default branch's tip | Inline | Opens the page |
| `/branch` | The branch, its base, and commits ahead and behind. `/branch main` asks to work on `main`, when allowed (§3.3) | Inline | Opens the page |
| `/status` | Today's `/status`, plus the repository, branch, uncommitted and unpushed counts, the access mode and the budget left | Inline | Inline, counts only |
| `/diff` | Summary: files changed with lines added and removed, against the branch's base. `/diff <path>` shows one file's diff, cut at the bridge's limit | Inline | Opens the page's review pane |
| `/commit [message]` | Commits everything in the checkout (§4.4); with no message, the thread's name | Inline, with confirmation | Opens the page |
| `/push` | Pushes the branch to the forge, after the anonymity check (§4.5) | Inline, with confirmation | Confirmation with counts only ("Push 2 commits of mightling to the forge?") |
| `/update` | Merges the default branch into the phone branch. On a conflict it stops and names the files (§4.6) | Inline | Opens the page |
| `/merge` | Merges `phone/<thread>` into the default branch on the forge through a pull request (§4.6) | Inline, with confirmation | Confirmation with counts only |
| `/pr` | Opens or shows the forge pull request for the branch, with a link through `ling web`'s `/forge/` | Inline | Button to the page |
| `/access [read\|workspace\|full]` | The thread's sandbox. `full` only as in §6.3 | Inline | Opens the page |
| `/budget` | The chat's model budget today (§7.2) | Inline | Inline |
| `/night add <task>` | Queues a Night Shift task in the current repository (§11.2) | Inline, as SIGNAL §19.7 | Confirmation, repository name only |
| `/stop` | Stops the running turn of the chat's current thread, Ask or Work | Inline | Inline, and the page's Stop |

- **Parsing is shared.** It lives in the hub (`ling-rs/chat`), which serves Matrix, Telegram and Discord, and in the Signal bridge.
- **The routes behind each command are one set,** `ling web`'s (§4.7).
- **A message typed in the Telegram or Discord chat while a repository is current** is never sent to a Work thread. The bot answers "Repo work happens in the app" with the Open button, because an answer there could carry code. Ask works there as before.

### 2.1 Each bridge's own affordances

- **Telegram:**
  - **Buttons:** the Mini App's `web_app` button on each notice, and inline buttons for counts-only confirmations. `callback_data` is limited to 64 bytes, so a button never carries a repository name. Buttons use nonces (`r:<nonce>`), as approvals do (`a:<nonce>:<choice>`, MOONSHOT §14).
  - **Topics per repository:** when the bot has topics, each repository gets one topic, named after the repository. Its notices go there, and its Open button opens that repository.
- **Discord:**
  - **Commands:** slash commands with `contexts: [BOT_DM]`. `/repo` answers with a link button, never a select menu of names. A select would be possible, but it's capped at 25 options and gains nothing over the page.
  - **Acknowledgement:** every interaction is acknowledged within 3 s (DISCORD §3.4).
- **Signal:** text commands only, YES/NO for confirmations and approvals (SIGNAL §8), and numbers for picking from a list.
- **Matrix:** text commands, with ✅/❌ or yes/no for confirmations and approvals (CHAT §1), and numbers for lists.

### 2.2 The status line: the current repository, always visible

| Surface | Where |
|---|---|
| Signal | The "Working on it…" line (SIGNAL §19.8) becomes `mightling · phone/3f2a9c1e · working…`, and `/status` |
| Matrix | The room's topic (`m.room.topic`), set to `mightling · phone/3f2a9c1e` on a switch and cleared by `/repo off`; and the typing line |
| Telegram | The repository's topic name; the page's Work header |
| Discord | The status line of DISCORD §3.7: `mightling · phone/3f2a9c1e`, with Stop and Open |
| The page (Mini App, `ling web`, desktop) | The Work header shows the repository, the branch and a 🔒 for private ones, with a "main" badge when working on `main` |

Only names and counts appear in a status line: the repository, the branch, files changed, and commits ahead.

## 3. Where the checkout lives

### 3.1 The decision: one worktree per thread, beside a shared clone

```
~/.mightling/chat-repos/
  index.json                         thread ↔ checkout ↔ chat, written whole (temp + rename)
  <org>/<repo>/base.git              one bare clone per repository: the shared object store
  <org>/<repo>/<bridge>-<thread>/    one worktree per thread, on phone/<thread>
```

- **`<thread>`** is eight hex digits chosen at random when `ling web` opens the checkout, and kept with the thread in `index.json`. It names both the folder and the branch: `phone/3f2a9c1e`.
- **`<bridge>`** is the surface that started the thread: `signal`, `matrix`, `telegram` (the Mini App), `discord`, or `web`.
- **Opening a checkout** goes in this order:
  1. `git fetch` into `base.git` from the forge;
  2. `git worktree add -b phone/<thread> <dir> <default branch tip>`;
  3. `thread/start` with that `cwd` and the `chat-work` prompt (§6.1).

  The thread exists only once its checkout does.
- **Why not the owner's own checkout** (R3): the owner may be at the desk in it, mid-edit. A phone thread editing there would mix two people's changes in one working tree, and the desk's editor would see files change under it. A worktree per thread is also what lets two phone threads run at once in the same repository without touching each other.
- **Why not one checkout per chat:** two threads in one chat (`/new`) would share a working tree. That's B9's warning case, built in by default.
- **Why worktrees and not full clones:** the main node runs near its disk floor (FORGEJO §5.2). Worktrees share `base.git`'s objects, so a new thread costs one working tree, which is a few MiB for `mightling`.

### 3.2 Where it runs, and from which forge

- **Checkouts live on the node whose `ling web` the bridge talks to,** which is normally the main node, also the forge of record.
- **On a mirror node** (`node-b`), fetches come from the local read-only mirror, and pushes go to the forge of record over the LAN.
- **Private repositories have no mirror** (FORGEJO §3.1), so they're fetched from the record. A private repository's checkout on `node-b` puts its contents on that node's disk. That's the owner's own node, but it's a second copy that FORGEJO §6 otherwise allows only through backups. `/repo` on a mirror node marks such repositories "fetched from gx10-1a2b".
- **No forge, no repositories.** With the forge off or unreachable, `/repo` says so and names `ling-admin forge on`. No other source is ever used.
- **Submodules** are initialised only when their URL names a repository on the forge (rewritten to the forge's clone URL). Any other submodule is left empty, and the opening line names it. A recursive clone would otherwise reach GitHub.
- **Git LFS** isn't fetched in Phase 1, and the opening line says so when `.gitattributes` uses it.
- **No hooks run.** A fresh clone has no hooks, and `core.hooksPath` is never taken from a repository, so a cloned repository can't run code through git.

### 3.3 Working on `main`: an opt-in per repository

- **The default is `phone/<thread>`, always.**
- **Turning it on happens at the node's own terminal:** `ling chat repos allow-main <org>/<repo>`, which needs a typed `yes` (not built yet in `ling-rs/src/chat.rs`, which currently implements `ling chat repos full-access on|off`; `[chat_repos] allow_main` is read from `~/.config/dreamference/config.toml` by `ChatReposSettings` and `ForgePhone`). It adds the repository to `[chat_repos] allow_main` in the user's config file, never a repository's.
- **The forge enforces it.** The phone account (§4.2) is left off `main`'s push allowlist on every repository, except those in `allow_main`.
- **From the phone,** `/branch main` (or the page's branch menu) on an allowed repository asks for confirmation, then moves the thread's checkout to track `main`.
  - `/push` then pushes `main`, and refuses a non-fast-forward with "the forge's main moved; /update first".
  - On a repository that isn't allowed, it says how to allow it.
- **The B9 warning is stronger on `main`** (§8.1).

### 3.4 Disk

- **Caps:** at most `[chat_repos] max_checkouts` worktrees per node (default 30).
- **Floor check:** a new checkout is refused while the node is below its floor plus 5 GB (150 GB, and 175 GB during a SWE-bench night, FORGEJO §5.2). The refusal names the floor.
- **Pruning:** a worktree is removed (`git worktree remove`) when all of these hold:
  - its thread has been idle for `[chat_repos] idle_days` (default 14);
  - it has no uncommitted change;
  - its branch is pushed or merged.

  The branch stays on the forge, and `/use` on that thread opens a fresh worktree on the same branch. Anything else is kept, and `/repo` shows how many are kept.
- **`base.git`** is pruned with `git gc --auto` after a fetch, under the side-service memory rules (`docs/dev/host-safety.md`).

## 4. `ling web`'s repository service

### 4.1 Why in `ling web`

`ling web` already does three things this needs:
- it runs as the user, outside any sandbox;
- it holds every bridge's device credential;
- it enforces `policy.json`, and it already runs commands for the phone (`night_phone.rs`).

The repository service is one more module, `ling-rs/web/src/chat_repos.rs`. Its routes are behind the device credential and the `Origin` check like every route. They're refused to devices with the `watch` scope (SYNC §5.1).

### 4.2 Reaching the forge

- **An account for the phone surface.** `ForgePhone.ensure()` (`dreamference/forge/forge_phone.py`, called by `ForgeService` during `ling-admin node enable`, `forge on` and `forge tick` in `dreamference/forge/forge_service.py`) creates a forge account, `mightling-phone`, alongside the forge's other generated accounts (FORGEJO §2.2). It has write access to every repository, private ones included (R2), and appears on no protected branch's push allowlist except where `allow_main` puts it.
- **Its token** sits in `ling web`'s state folder (0600) and is handed to git only through `GIT_ASKPASS` for the one command. It's never in a remote URL, `.git/config`, a log or a message. A worktree's remote is the plain forge URL, so a `git push` the agent tries inside the sandbox has no credential, and `.git` is read-only there anyway.
- **Connections:** `ling web` reaches the forge the way its `/forge/` proxy does (FORGEJO §2.2), on the LAN only. The egress audit already declares the forge as a node service. These calls add no destination outside the owner's nodes.
- **Stated plainly, as CHAT §6 does:** Codex's sandbox limits writes, not reads, so a command the agent runs can read the token at `/airgapped off`, as it can read the forge's owner password file. Branch protection keeps `main` safe from a phone token. Nothing keeps a prompt-injected command from using the token on other branches, or from reading private repositories with it. `/airgapped on` (which masks the user's runtime folders and takes the network away) is the defence, as for every credential on the node. This is accepted for now; moving the token to a system account, as Signal's keys are, is left to a later phase (Q3, decided 2026-10-27).

### 4.3 Listing

- **`GET /api/repos`** returns the forge's repositories visible to `mightling-phone`, each with:
  - its name, its default branch, and when it was last pushed;
  - `private`, `archived`, and `mirror` (where it would be fetched from);
  - this device's open checkouts of it.
- **Cached for 60 s.**
- **The list is the only source of names.** `/repo <name>` and `POST /api/repos/open` take a name from the list, never a URL or a path.

### 4.4 Commit

`POST /api/repos/commit {thread, message}`:
1. `git add -A` in the thread's worktree.
2. `git commit -m <message>` with the owner's git identity on the node (`user.name` and `user.email` of the user `ling web` runs as; for this project's fleet, `dgxcoder`).
   - It's refused, with the reason, when no identity is set.
   - It never uses a messenger's display name, and adds no trailer.
3. The answer: the commit id, the files and the line counts.

### 4.5 Push, and the anonymity check

`POST /api/repos/push {thread}`:
- **The first layer, on the node:** before pushing a non-private repository, `ling web` runs the same check the forge's pre-receive hook runs (FORGEJO §3.2) on the commits being pushed, against the denylist kept on the node. A refusal names the **file, the line and the denylist entry's number**, never the matched text. Otherwise the identifier would sit in a Telegram or Discord notice, or in Signal's history. On Telegram and Discord the bot says only "Push refused by the anonymity check; open the app", and the page shows the file and line.
- **The second layer** is the forge's own hook. A push it refuses is reported the same way.
- **Private and archived repositories:** a private repository skips the GitHub-oriented check, because it's never mirrored. An archived repository refuses every write (§5.4).
- **Only `phone/<thread>` is pushed,** or `main` under §3.3. Never a force push, never a deletion.
- **Exactly the confirmed commit is pushed:** the refspec is `<confirmed sha>:refs/heads/<branch>`, so a commit made after the confirmation never rides along; if the worktree's head moved, the push is refused with "the branch moved since you confirmed; confirm again" (§4.7).

### 4.6 Update, merge and pull requests

- **`/update`** runs `git merge <default branch>` in the worktree.
  - On a conflict, the merge is left in progress: the conflict markers are in the files, and the answer names the files.
  - The agent, which may edit files, resolves them in the next turn, and `/commit` completes the merge.
  - `/update abort` runs `git merge --abort`.
- **`/merge`:**
  1. pushes the branch if it's ahead of the forge (§4.5);
  2. opens the pull request if there's none;
  3. merges it through the forge's API with a merge commit, as the phone account. Protected-branch rules allow merges, not pushes.
  - **Requirements:** the forge's required status checks must be green when the repository has them (the forge's gate, FORGEJO §4.2).
  - **On a conflict or a red check,** it's refused with the reason ("conflicts with main: /update, then /merge").
  - **Afterwards,** the owner's desk gets the change with an ordinary pull. Nothing touches the desk's tree.
- **The merge is pinned to the confirmed commit:** the forge's merge call carries `head_commit_id`, and an open pull request is reused only when both its head and its base match.
- **`/pr`** opens a pull request `phone/<thread>` → default branch on the forge, titled with the thread's name, and answers with its link through `ling web`'s `/forge/`. If one is already open, it answers with that link.

### 4.7 Routes

| Route | Body | What it does |
|---|---|---|
| `GET /api/repos` | | §4.3 |
| `POST /api/repos/open` | `{repo, bridge, chat, thread?}` | A new checkout and Work thread, or the chat's existing one; answers `{thread, cwd, branch}` |
| `POST /api/repos/off` | `{bridge, chat, thread?}` | Clears the chat's current repository in `current.json` and answers `{repo, branch, uncommitted}` |
| `GET /api/repos/notices?chat=&after=` | | Bridge-only: typed repository events (`RepoEventKind`) since `after` for `RepoNotice` delivery (§5.3) |
| `GET /api/repos/current?bridge=&chat=&repo=` | | Bridge-only: the chat's current repository (`current.json`) and `checkout {thread, repo, branch, base, notified}`: the chat's own latest checkout of the repository, else the latest from any chat; `notified` says whether its notices reach this chat |
| `GET /api/repos/state?thread=` | | Branch, base, `head`, ahead and behind, `unpushed`, uncommitted files with counts, access mode, budget (and a confirmation nonce with `?intent=…`) |
| `GET /api/repos/diff?thread=&path=&scope=` | | `scope`: `uncommitted`, `branch` (against the base) or `turn`; a stat, or one file's diff |
| `GET /api/repos/log?thread=` | | The branch's commits since its base |
| `POST /api/repos/commit` | `{thread, message}` | §4.4 |
| `POST /api/repos/push` | `{thread}` | §4.5 |
| `POST /api/repos/update` | `{thread, abort?}` | §4.6 |
| `POST /api/repos/merge` | `{thread}` | §4.6 |
| `POST /api/repos/pr` | `{thread}` | §4.6 |
| `POST /api/repos/main` | `{thread}` | §3.3 |
| `POST /api/repos/budget` | `{thread, add, confirm}` | Raises the chat's daily token budget until local midnight (§7.2) |
| `POST /api/repos/access` | `{thread, level, typed?, confirm?}` | Grants (`level: "full"`) or ends (`level: "workspace"`) Full Access on the checkout (§6.3) |

- **Thread checks:** every route that takes a thread checks that `index.json` maps it to a checkout under `chat-repos/`. No route takes a path.
- **Confirmation:** the routes that change something take a `confirm` nonce, issued by a preceding `GET` with `?intent=…`. A bridge's confirmation (button, YES, ✅) carries that nonce, so a confirmation can't be replayed or aimed at another thread.
  - **The nonce:** 128 random bits, single use, valid 10 minutes, bound to the caller (the owner's session or the bridge's device), the thread and the intent. For `push`, `pr` and `merge` it's also bound to the worktree's head commit, the branch and the base at issue time. It's taken under the repository's lock before any git or forge call: a missing, expired, reused or other caller's nonce answers 409 `expired`, and a head, branch or base that moved answers 409 `moved` (the nonce is spent either way).
  - **`/push` and `/merge` from the bots** (`ling-chat`'s hub): the hub reads the chat's checkout from `/current`, asks `/state?intent=…`, and shows a counts-only prompt with two buttons ("Push 2 commits of org/widget (phone/co-1) to the forge?"). Only a press of its button, in the same chat, within five minutes (inside the nonce's ten), sends the nonce. The outcome is said in fixed words, never the server's text; success is said only when the checkout's own notices (§5.3) don't reach that chat. On Telegram the air gap blocks the send.
  - **Discord** has no adapter yet. When it gets one, a button press must be acknowledged within three seconds, so the bot defers the interaction and answers with a follow-up.

## 5. Work threads from a phone

### 5.1 The policy

- **A new prompt name, `chat-work`,** joins `namedPrompts` in `ling-rs/web/policy.json`. It's the `default` prompt plus a short phone preamble:
  - answer briefly, ending with what changed;
  - run commands with `exec_command`;
  - never try `git add`, `git commit` or `git push`, which the owner's commands do.
- **Where a `chat-work` thread may run:** `ling web` accepts `thread/start` with `prompt: "chat-work"` only with a `cwd` that `index.json` maps to a checkout of that thread. A bridge device (`signal`, `chat`) may start a non-scratch thread only this way.
- **This is how "no repositories outside the forge" is enforced.** The policy's conformance cases (run by `ling web` and the desktop app, DESKTOP §4.3) gain the vectors:
  - a bridge `thread/start` with another `cwd` is refused;
  - a `chat-work` thread whose `cwd` isn't in the index is refused.
- **What the policy doesn't do:** at `/airgapped off` the agent's own commands reach the network as any Work thread's do. The policy limits where a thread starts, not what its commands fetch.

### 5.2 Rendering on Signal and Matrix

- **Answers:** as today (SIGNAL §8, CHAT §3), streamed by edits on Signal.
- **`/diff`:** a monospace stat. `/diff <path>` is the file's unified diff in a code block, cut at 3,500 characters on Signal and 16,000 on Matrix, ending "(cut: N more lines; open in the app to see all)".
- **File changes the agent proposes** (`item/fileChange/requestApproval`): the files and a stat, plus the first 1,000 characters of the patch.
- **A command's output** is shown on request: replying `/out` after a command item gives its last 3,000 characters.

### 5.3 What Telegram and Discord receive

The bot receives only `RepoNotice` values (§12.2), each built from a fixed template:

| Notice | Text (all of it) |
|---|---|
| Switched | `Working in <repo> on <branch>.` |
| Turn finished | `Turn finished in <repo>: <n> files changed.` |
| Turn stopped or failed | `Turn stopped in <repo>.` / `Turn failed in <repo>; open the app.` |
| Approval waiting | `Approval waiting in <repo>.` (and `It will be declined in 10 minutes.` 9 minutes in) |
| Pushed / merged | `Pushed <n> commits of <repo>.` / `Merged <branch> into <default> in <repo>.` |
| Refused | `<action> refused in <repo>; open the app.` |
| Budget | `<repo>: 80% of today's model budget used.` |
| Busy | The benchmark line of SIGNAL §17.3, which names the run, never the repository's content |

- **What a notice can contain:** `<repo>` and `<branch>` are forge names, `<n>` is a count, and `<action>` is one of a fixed set of words.
- **What it never contains:**
  - the thread's name (taken from the owner's first message, which was typed in the page and never went through Telegram);
  - file names, paths or commit messages;
  - commands;
  - the model's words.
- **Every notice carries an Open button.** On Telegram that's a `web_app` button to `<front>?open=thread:<id>`; on Discord, a link button to the same address.
- **Approvals are answered in the page.** The 10-minute decline of CHAT §3 still applies.

### 5.4 Archived and read-only repositories

- **What opens read-only:** a repository the forge marks archived (`archive-pre-rewrite` among them), or one where `mightling-phone` has read access only.
- **How it behaves:** the thread's sandbox is `read-only`; `/commit`, `/push`, `/update`, `/merge` and `/pr` answer "this repository is read-only on the forge"; and `/branch` shows the default branch, with no phone branch made. A worktree is still made, detached at the default branch's tip, so the agent can read and run read-only commands.
- **FORGEJO §5.3's "never cloned by agents"** now has one exception: a checkout made by `ling web` at the owner's request from a phone (R2). Runners still never check it out.
- The archive stays read-only from the phone (Q1, decided 2026-10-27).

## 6. Approvals and access

### 6.1 The sandbox

Phone Work threads start in **workspace-write** with **on-request** approvals, the desktop Work defaults.

Every turn on a repository thread carries an explicit sandbox, written by the policy in both enforcers whatever the page sent: the user's own `[sandbox_workspace_write]` from `$CODEX_HOME/config.toml` (its writable roots, and the network on, as the launcher writes it, unless the thread's air gap is `on`), `readOnly` with the same network in a read-only checkout, and `dangerFullAccess` only under §6.3. A turn's sandbox holds for the turns after it, so writing it on every turn is what keeps a thread from keeping a wider one.

Approvals, on every bridge:
- **Commands and file changes:** relayed as today, Approve means once, with nonces. `acceptForSession` and policy amendments are never offered from a phone.
- **`item/permissions/requestApproval`** (more network or file access) is relayed with `scope: "turn"`, on every bridge for Work threads. This changes SIGNAL §8, which declines it, and the change applies to `chat-work` threads only.
- **On Telegram and Discord** every approval is answered in the page, since it shows a command or a patch. The bot sends the "Approval waiting" notice.
- **A secret question** (`isSecret`) is still refused from a messenger. In the page it's answered like any field, since the page loads straight from the node.

### 6.2 The air gap

- **The thread's level** follows the user's default and its own `/airgapped` setting, as every thread does. At `on` its commands get an empty network namespace (patch `0019`).
- **The bridges keep their rules:**
  - at a user-level `on`, Telegram's and Discord's bots pause;
  - Signal acts on nothing;
  - Matrix keeps working.
- **The Mini App and the Discord link keep working at `on`,** because the page loads from the node over the tailnet and nothing passes the messenger. Repo work at `on` is therefore possible from Matrix, the Mini App and the page, and not from the Signal, Telegram or Discord chats.
- **No phone command changes the level** (SIGNAL §5.2). The page's per-thread `/airgapped on` may raise a thread's level; lowering it needs the node.
- **Pushing at `on`:** `ling web`'s push to the forge is a LAN call made by the owner's command, not the agent's, and is allowed. The forge's own outbound mirrors stop at `on` (FORGEJO §5.4).

### 6.3 Full Access

- **Off by default for phone threads:** `[chat_repos] full_access = false`. It's turned on only at the node's terminal, with `ling chat repos full-access on` and a typed `yes`.
- **When allowed,** `/access full` (or the page's sandbox menu) asks the owner to type `FULL ACCESS <repo>` exactly. It lasts for the thread until `/access workspace`.
- **The Telegram and Discord bots never offer it;** the page does.
- **At `on` it's refused regardless,** by the app-server (patch `0023`, AIRGAPPED §14.7), with the same message.
- **What grants it:** `POST /api/repos/access {thread, level: "full", typed, confirm}` from the page, after the node's setting, a writable checkout, its air gap off, the exact phrase and a single-use `access` nonce all hold. The grant is in memory: a restart of `ling web` ends it.
- **Only the page's turns run with it.** A messenger bridge (a `bridge=1` session or a Signal device) can neither grant nor end it (403), and a bridge's turn on a granted thread still gets the workspace sandbox. A read-only checkout never gets it.
- **Ending it** (`level: "workspace"`, no nonce, so it works when the forge is down) takes the checkout's loaded threads back to the workspace sandbox at once (`thread/settings/update` over a connection of its own) and interrupts a turn still running.
- **Revocation when node settings change:** `sweep_full_access` (`ling-rs/web/src/chat_repos.rs`, run every `FULL_ACCESS_SWEEP_INTERVAL = 5s` by `ling-rs/web/src/server.rs`) re-evaluates every held grant against the node's `[chat_repos] full_access` and the air gap; when a grant no longer stands, the checkout's threads are taken back to their workspace sandbox within 5 s and any turn started before the grant dropped is interrupted (`96bc232a`, replacing the earlier limit where revocation waited until the next turn or `/api/repos/state`).

## 7. Bounds and budget

### 7.1 Bounds per turn

The Ask bounds of CHAT §16.3 (180 s, 25 tool calls) would cut an edit in half, so `chat-work` threads have their own:

| Key | Default | At the bound |
|---|---|---|
| `work_answer_seconds` | 1800 | Steer "stop here and summarise what changed", interrupt 60 s later, keep the partial |
| `work_tool_calls` | 200 | The same |
| `work_command_seconds` | 600 | The same |

- **No final turn without tools,** unlike Ask: a summary of an unfinished edit is what the owner needs.
- **Where they're enforced:** in `ling web` (`repo_limits.rs`, sharing `limits.rs`'s `Verdict`), so they apply whether a turn came from a bridge or from the page.
- **The model gate** recognises a phone Work thread by its `cwd` under `$CODEX_HOME/chat-repos/`. It gives it the desktop Work treatment: no 4,096-token response cap, since one `apply_patch` can be longer, and the loop detector kept (`docs/dev/loop-guard.md`).

### 7.2 A model budget per chat

- **The budget:** `[chat_repos] tokens_per_day`, default 3,000,000 per chat, counted from the threads' token usage notifications. It covers all of a chat's `chat-work` threads, from midnight on the node.
- **At 80%:** one notice.
- **At 100%:**
  - a running turn is steered to summarise, as in §7.1;
  - new turns are refused with the time the budget resets;
  - git commands keep working.
- **Raising it:** `POST /api/repos/budget` (the page's Budget control), or `ling chat repos budget <chat> <tokens>` at the node / `/budget +<n>` from Signal or Matrix confirmed with YES (the CLI subcommand and Signal/Matrix text commands are P2/unbuilt). A raise lasts until midnight.

### 7.3 Stop

- **`/stop`** stops the current thread's running turn and drops queued messages. It works on every bridge, Telegram's and Discord's bots included (no content involved).
- **The page's Stop** does the same.
- **The partial is kept** (MOONSHOT §14), and in Work mode so is every file change made so far.

## 8. Concurrency, nights and the desk

### 8.1 Two threads on one repository (B9, extended)

- **Before a turn starts, `ling web` looks for other running turns:**
  - **The same checkout** (B9 exactly): "Another thread is running in this checkout: <name> (desktop)." This can happen when a device that follows the phone's thread through sync starts a second thread there.
  - **The same repository elsewhere:** a running turn in any checkout whose `forge` or `origin` remote names this forge repository. It looks among the node's app-servers' threads and, with sync on, the hub's live table (SYNC §18): "mightling is also being worked on at the desk (thread <name>, branch main, running). Phone edits stay on phone/3f2a9c1e."
  - **When working on `main`,** the warning says both may push `main`, and `/push` refuses a non-fast-forward.
- **Nothing is blocked,** as in B9.
- **On Telegram and Discord** the bot's version is `Another thread is running in <repo>.`, with Open.

### 8.2 Nights

- **While a SWE-bench run holds the model gate,** a Work turn is refused, as in SIGNAL §17.3: "The model on gx10-1a2b is busy with a benchmark run (…), so this was not run." It's never queued, and the gate is never paused from a phone.
- **Git commands keep working,** because they don't need the model: `/status`, `/diff`, `/commit`, `/push`, `/merge`, `/pr`.
- **The page** shows the same as a banner.
- **Night Shift's task runner gives way** to a phone Work turn, as to any person (CHAT §2, `ling web`'s busy marker).
- **Disk:** new checkouts follow the night floor (§3.4).

### 8.3 The desk

- **The owner's tree is never written** (R3).
- **What the desk sees:** the phone's branch on the forge (`git fetch forge` shows `phone/*`), its pull request, and, with sync on, the thread live.
- **Taking a thread to the desk:** SYNC §6.4's hand-over finds the branch on the forge and checks it out on the receiving device, so a pushed branch needs no bundle. Before a hand-over, the phone is offered `/push` when the branch is ahead.

## 9. Private repositories

- **Reachable on every bridge (R2, R4).**
- **Signal and Matrix show their code inline** (R5). Signal keeps it in the owner's end-to-end encrypted history, including Note to Self on every linked device. Matrix keeps it on the node's homeserver.
- **Telegram and Discord never see it,** by construction (§5.3, §12.2).
- **What those two servers do learn** is listed here once:
  - repository and branch names (one is `archive-pre-rewrite`);
  - counts, and the times of turns and approvals;
  - that the Mini App or link was opened.
- **Not mirrored out:** a private repository's `phone/*` branches stay on the forge (FORGEJO §5.3). Its checkout on a mirror node is §3.2's second copy.
- **`/repo` marks private repositories with 🔒** on every surface. The page shows the mark in the Work header.
- **A per-bridge switch:** `[chat_repos] bridges` (default `["signal", "matrix", "telegram", "discord"]`) can leave a bridge out of repo work. Repo commands there then answer that repo work is off for that messenger. There is no consent prompt, because nothing a consent prompt would warn about leaves the page.

## 10. The Mini App and the `ling web` page: what's missing for full Work mode

**What the Mini App is today** (MOONSHOT §15): `ling web`'s own page in Telegram's webview, signed in with the device credential, plus a check of Telegram's signed launch data. The page is the desktop UI's web host (`desktop/ui`, `bridge.host() === "web"`).

**What the web host lacks:** everything that goes through the Electron main process. In `desktop/ui/src/bridge.ts`, each of these has `available: () => host() === "electron"`:
- `git` (status, diff by scope, stage, commit, push, pull request);
- `worktrees`;
- `terminals` and tasks;
- `prs`;
- the folder chooser.

The owner ordered every gap built (R6). G1–G10 and G12–G14 are now built (2026-10-27–2026-10-28); G11 waits for the Discord adapter. Each row says what existed before the build and what was needed (and where it shipped):

| # | Capability | Existed before | Needed / Shipped |
|---|---|---|---|
| G1 | **The repository service** | Nothing. `night_phone.rs` is the pattern | **Built** (`ling-rs/web/src/chat_repos.rs`, `repo_limits.rs`, `ling-rs/prompts/chat-work.md`, `ling-rs/web/policy.json`): `chat_repos.rs` with the routes of §4.7, the policy rules of §5.1, `repo_limits.rs` (§7), and a forge client tested against a stand-in |
| G2 | **Sign-in from the launch data** | The launch check (`POST /api/telegram/check`: signed `initData`, a paired Telegram user, a day's freshness) runs *on top of* the device credential. A new phone still needs `ling web pair` typed at the node | **Built** (`ling-rs/web/src/telegram_app.rs`, `server.rs`, `ling-rs/chat/src/hub.rs`, `desktop/ui/src/init.ts`): **Launch pairing:** a paired Telegram user's first launch from the configured front, with no device credential, gets a pending device and a 6-digit code shown on the page. The bot sends "A Mini App on a new device asks to pair (code 482 913)" with Approve and Decline. On Approve, `ling web` issues a `control` device credential, labelled `telegram-app`, revocable with `ling web revoke`. **The trust, stated plainly:** Telegram issued the bot token and carries both the launch data and the Approve press, so Telegram could forge both. The one thing it lacks is reach to `<front>` on the tailnet. Q6 asks whether that is enough |
| G3 | **Deep links** | `#work` and `#popout=<thread>` in the address's fragment. Telegram owns the fragment (`tgWebAppData`), and `bridge.js` clears it | **Built** (`desktop/ui/src/init.ts`, `deeplink.test.ts`): `?open=repos`, `?open=repo:<org>/<repo>` and `?open=thread:<id>` (with `&approval=1` for a waiting approval), read after the launch check. Used by every notice's button, and by Discord's link (G11) |
| G4 | **Repository picker** | Work's project list (`project/list`) and the folders threads used. "Add folder…" and the chooser are Electron-only | **Built** (`desktop/ui/src/ReposView.tsx`, `repos.ts`): A **Repos** view: `/api/repos` with 🔒, archived, mirror and open-checkout marks, a search box, and recent ones first. Opening one calls `/api/repos/open` and shows its thread |
| G5 | **Work view on a phone** | The same Work UI, with one `@media (max-width: 720px)` rule in `styles.css`. Telegram's back button and safe-area insets are unused (MOONSHOT §15 "Not done") | **Built** (`desktop/ui/src/phone.ts`, `App.tsx`, `styles.css`, `ling-rs/web/src/bridge.js`): A narrow layout: the thread list as a drawer, the review pane and file view as full-screen sheets, the composer pinned above the keyboard, and the approval card kept in view. Telegram's back button and safe area through `postEvent` (`web_app_setup_back_button`, `safe_area_changed`), since Telegram's script isn't loaded |
| G6 | **Diffs** | The review pane in the web host shows the `turn` scope only (from the app-server). The unstaged, staged and branch scopes need the window's git | **Built** (`desktop/ui/src/bridge.ts`, `repos.ts`, `ReviewPane.tsx`): For a `chat-repos` thread, `git` in the web host is implemented over `/api/repos/diff` and `/state`: scopes `uncommitted`, `branch` and `turn`. `git.available()` becomes true for such a thread only. Stage, unstage and discard aren't offered, since commits take everything (§4.4) |
| G7 | **Approvals** | Command and file-change approval cards answered through the policy's pending set, as in a browser | **Built** (`desktop/ui/src/ApprovalStrip.tsx`, `approvals.ts`, `ling-rs/src/chat.rs`, `ling-rs/web/src/chat_repos.rs`): A "Waiting for you" strip at the top when `?approval=1` opens the page. The permissions-request card is checked in the web host, and built if it's missing there. The Full Access typed confirmation (§6.3) |
| G8 | **Command output** | The agent's commands and their streamed output show in the thread. The user's own terminal panel and tasks are Electron-only | **Built** (`desktop/ui/src/OutputView.tsx`, `output.test.ts`): A full-screen, read-only output view for one command item (long logs, search, copy). **No interactive terminal from a phone:** it would be an unsandboxed shell |
| G9 | **Branch, commit, push, update, merge, PR** | Electron-only (`git.commit`, `git.push`, `git.pullRequest`, `prs`) | **Built** (`desktop/ui/src/GitBar.tsx`, `repos.ts`): A git bar for `chat-repos` threads: branch and ahead/behind, Commit (message), Push, Update, Merge, PR (forge link through `/forge/`), "Work on main" where allowed, each over §4.7 with its confirmation nonce |
| G10 | **Live turns** | The page is an app-server client of the node, so node-owned phone threads stream directly. Threads owned by another device show through sync's live view (`sync.ts`, `/api/sync/live`) when sync is on | **Built** (`desktop/ui/src/resume.ts`, `resume.test.ts`): Resuming when the webview returns from the background: on `visibilitychange`, re-subscribe (`thread/resume`) and read back missed turns (`thread/turns/list`), as the bridges do (CHAT §2). Checked in Phase 0, built if it's missing |
| G11 | **Discord's front** | Specified (DISCORD §3.11): `[chat] discord_web_url` served like the Mini App's address. Not built: `telegram_app::mini_app_url` reads only the Telegram setting | **Not built yet:** Read `discord_web_url`, add its host to `Config::public_names`, and use G3's deep links. Discord's launch carries nothing signed, so first pairing there is `ling web pair` or a code confirmed from Signal or Matrix |
| G12 | **The file viewer** | File tabs read through `fs/readFile` (works in the web host). "Open in editor" is Electron-only | **Built** (`desktop/ui/src/FileTree.tsx`, `FileView.tsx`, `phone.ts`): Nothing for reading. The narrow layout (G5) and the file tree in the drawer |
| G13 | **The Work header** | `WorktreeHeader` shows a branch | **Built** (`desktop/ui/src/WorktreeControls.tsx`, `repos.ts`): The repository, `phone/<thread>`, 🔒, the "main" badge, the budget left, and the B9 warning of §8.1 |
| G14 | **Bot notices and topics** | Topics as threads, `web_app` buttons and `/app` (MOONSHOT §14–§15) | **Built** (`ling-rs/chat/src/repo_notice.rs`, `hub.rs`, `telegram.rs`, `ling-rs/chat/tests/telegram.rs`): `RepoNotice` (§5.3) in the hub; a topic per repository; Open buttons with G3's links; the counts-only confirmations of §2; the bot's refusal to forward typed text in a repository's topic |

**Build order:**
1. **G1** (everything else calls it).
2. **G3** and **G2** (a phone can reach the page and land in the right place).
3. **G4**, **G6**, **G9** and **G13** (a whole Work loop: pick a repository, see the diff, commit, push, merge).
4. **G7**, **G8** and **G10** (approvals, long output, resuming).
5. **G5** and **G12** (the phone layout over the working pieces).
6. **G14** and the test of §12.2 (the notices).
7. **G11** (with the Discord adapter, when DISCORD's build starts).

Until the forge exists (FORGEJO P0), G1 runs against `[chat_repos] forge_url` pointing at a stand-in in tests only. The feature stays off on a node whose forge doesn't answer.

## 11. Integration

### 11.1 Sync

- **A phone thread is a node thread.** With sync on, it's listed on every device under its repository with a "(phone)" mark, followed live, and answerable from the desktop or laptop (SYNC §6.3). An approval answered there shows on the phone as "approved at the desk".
- **The owner is the node:** a turn always runs in the node's checkout. Hand-over follows §8.3.

### 11.2 Night Shift

- **`/night add <task>` in a repository queues the task in that repository.**
  1. `ling web` offers to push the phone branch first, so the night's base is on the forge.
  2. It runs `ling night add` in the thread's checkout. Night Shift makes its own worktree from that checkout's `HEAD`, as it does from any checkout.
  3. The result branch `night/<id>` is pushed to the forge, where it shows as a pull request (FORGEJO §2.4).
- **In Ask, `/night add`** answers "Choose a repository with /repo first".
- **`[chat] night_repos` is retired.** For one release, a name in it that matches a forge repository is offered as a shortcut, with one line saying the setting is going. Then it's ignored. Repositories with tasks queued before are still listed by `/night status`.

### 11.3 The forge's pull requests

`/pr` and `/merge` (§4.6) use the forge's pull requests. The page opens them through `ling web`'s `/forge/`, signed in (FORGEJO §2.2). Night Shift's results already arrive the same way.

## 12. Tests (nothing touches the real machine)

All tests use a scratch `HOME`, a forge stand-in, local bare repositories, and stand-ins for the Bot API, Discord's Gateway, signal-cli and the app-server. None reaches a real forge (port 3200), Telegram, Discord, Signal, a homeserver, the model server or the network. A test's "forge" git URL is a `file://` path under its temporary folder, and the `GIT_ASKPASS` helper is checked for being called, never for a real token.

### 12.1 `ling web` (`cargo test` for `ling-web-server`)

- **Checkouts:**
  - open makes one bare clone and one worktree per thread on `phone/<8 hex>`, recorded in `index.json`;
  - a second thread shares the objects;
  - no credential appears in `.git/config`, the remote URL, logs or answers;
  - submodules from outside the forge are left empty and named;
  - no hook runs.
- **Refusals:**
  - a name not in the listing, a URL or a path;
  - a node below its floor (`statvfs` stubbed);
  - the forge off;
  - more than `max_checkouts`.
- **Pruning:** only idle, clean and pushed worktrees are removed, and the branch stays.
- **Commit:** with the node's identity, refused without one, no trailer.
- **Push:**
  - only `phone/<thread>`, never forced;
  - a denylisted string in a pushed file is refused with the file, line and entry number, and the answer doesn't contain the string;
  - a private repository skips the check, and an archived one refuses.
- **Update and merge:** a conflict leaves markers and names files, `/update abort` restores; merge through the stand-in's pull-request API; a red required check refuses.
- **`main`:** `/api/repos/main` is refused unless the repository is in `allow_main`, and a non-fast-forward is refused.
- **Policy:** the conformance vectors of §5.1. Full Access is refused unless `full_access`, the exact typed string and `off` all hold.
- **Bounds and budget:** a scripted turn over `work_tool_calls` is steered, then interrupted, with no final turn; the budget's 80% notice, refusal at 100% and raise until midnight.
- **Concurrency:** a second thread in the same checkout, and in another checkout of the same forge repository, gives §8.1's warnings and blocks nothing.
- **Nights:** a turn failing with the gate's benchmark 503 is reported with SIGNAL §17.3's line, and `/push` still works.
- **G2:** launch pairing is refused for an unpaired Telegram user, a stale or forged launch, or a host other than the front, and isn't approved without the bot's Approve. An approved device can be revoked.

### 12.2 No file content reaches Telegram or Discord (`cargo test -p ling-chat`)

- **By type:** the Telegram and Discord adapters take repository events only as `RepoNotice`. Its fields are a forge name, a branch name, counts, and an enum of actions. The type has no free-text field, so no code path can put a string from a file, a diff, a command or the model into it.
- **By test**, `repo_turns_send_no_content_to_telegram_or_discord`:
  - **Setup:** a scripted agent runs a full repository turn (a command, a file change, an approval, a streamed answer, a commit, a push, a refused push, a merge) in a stand-in checkout. Distinct sentinel strings sit in a file's contents, the file's name, its diff, the command, the command's output, the model's answer, the commit message, the thread's name and a denylisted line.
  - **Checked:** every request body the Telegram stand-in and the Discord stand-in receive, including `callback_data`, button labels, topic names, drafts and rich blocks.
  - **The assertion:** no sentinel appears anywhere. The same turn through the Signal and Matrix stand-ins does show the answer and the diff, which proves the sentinels were really produced.
- **A typed message in a repository's topic,** Telegram or Discord, is never forwarded to a Work thread.

### 12.3 Bridges

- **The hub (Matrix, Telegram, Discord):** command parsing for every row of §2; `/repo` lists with 🔒; `r:<nonce>` buttons; a forged or closed nonce ignored; topics per repository; the status line per surface (§2.2); `/stop` in a repository.
- **Signal** (`cargo test -p ling-signal`):
  - `/repo`, numbered picks, YES confirmations carrying the route's nonce;
  - inline diffs cut at 3,500 characters;
  - `item/permissions/requestApproval` relayed for a `chat-work` thread and still declined for Ask;
  - the "working…" line with the repository.

### 12.4 The page (`npm test` in `desktop/ui`, and Node's `vm` for `bridge.js`)

- **G3 deep links** are read after the launch check and never before.
- **G4:** the picker against a mocked `/api/repos`.
- **G6 and G9:** the git bar and review scopes in the web host against mocked routes, offered only for a `chat-repos` thread.
- **G7:** the approval strip.
- **G8:** the output view.
- **G10:** resuming on `visibilitychange`.
- **G5:** the narrow layout and the back-button and safe-area events (as `tests/test_telegram_mini_app.py` drives `bridge.js`).
- **An ordinary browser tab** stays unchanged.

### 12.5 Python

`tests/test_anonymity.py` already covers this spec's text. `ForgePhone` (`dreamference/forge/forge_phone.py`, called by `ForgeService` in `node enable`, `forge on` and `forge tick`) creates and maintains the `mightling-phone` account, its `0600` token file and default-branch protection, and is tested in `tests/test_forge_phone.py` with Docker mocked.

## 13. Phases

| Phase | Delivers | Gate |
|---|---|---|
| **P0** (measure, on a throwaway bot and the owner's phone) | Whether Telegram's webview keeps `ling web`'s device cookie across launches; the back button and safe-area events through `postEvent`; what a backgrounded webview does to the WebSocket; Discord's in-app browser and the link; a worktree from a bare clone under workspace-write (the agent's edits pass, `.git` read-only); `mightling`'s clone time and disk | A short results section here (owed) |
| **P1** (the Mini App build, R6; **built 2026-10-27–2026-10-28**) | §10 in its build order: G1–G10, G12–G14 | §12.1, §12.2, §12.4 pass; a whole loop on stand-ins: pick, turn, approval, diff, commit, push, merge |
| **P2** | Signal and Matrix commands (§2, §5.2); the extended B9 (§8.1, built in `chat_repos.rs`); Night Shift in the repository and `night_repos` retired (§11.2) | §12.3 passes; one live loop from Signal once the forge exists |
| **P3** (Full Access, pruning and `allow_main` built 2026-10-27–2026-10-28; G11 and `ling chat repos allow-main` CLI remaining) | Full Access by node policy (§6.3); pruning (§3.4); `allow_main` (§3.3); G11 with the Discord adapter | Their tests; a week of real use with no write to the owner's tree and nothing refused that should have passed |

**Live use needs the forge:**
- FORGEJO P0 for the forge of record;
- FORGEJO P2 for the pre-receive anonymity hook, without which `ling web`'s own check is the only layer before GitHub.

Until then the feature is off, and the tests run on stand-ins.

## 14. Configuration

`[chat_repos]` in the user's config file (`~/.config/dreamference/config.toml`), never a repository's:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | true when a forge of record answers | Repository commands at all |
| `bridges` | `["signal", "matrix", "telegram", "discord"]` | Which messengers may do repo work (§9) |
| `allow_main` | `[]` | Repositories a phone thread may work on `main` in (§3.3); set by `ling chat repos allow-main` |
| `full_access` | false | Whether `/access full` can be offered (§6.3) |
| `max_checkouts` | 30 | Worktrees per node (§3.4) |
| `idle_days` | 14 | When a clean, pushed worktree is removed |
| `tokens_per_day` | 3000000 | A chat's model budget (§7.2) |
| `work_answer_seconds`, `work_tool_calls`, `work_command_seconds` | 1800, 200, 600 | Bounds per Work turn (§7.1) |
| `forge_url` | (tests only) | A stand-in forge for tests; ignored outside them |

## 15. Open questions

| # | Question | Decided by the owner (2026-10-27) |
|---|---|---|
| Q1 | Should the archived `archive-pre-rewrite` be writable from a phone? | **Decided (2026-10-27):** read-only from the phone (§5.4): it's an archive, and writes to it have no use |
| Q2 | Full Access for phone threads | **Decided (2026-10-27):** off until the node allows it, with a typed confirmation (§6.3) |
| Q3 | The forge token (and the owner password file) are readable by the agent's commands at `off` | **Decided (2026-10-27):** accept for now, as CHAT §6 does; move the token to a system account in a later phase |
| Q4 | Bounds and budget numbers | **Decided (2026-10-27):** 30 min and 200 tool calls per turn, 10 min per command, 3M tokens per chat per day |
| Q5 | `/push` and `/merge` from the Telegram and Discord bots, with counts-only confirmations | **Decided (2026-10-27):** `/push` and `/merge` allowed from the bots, to the forge only (§2) |
| Q6 | G2's launch pairing trusts Telegram plus tailnet reach as its two factors | **Decided (2026-10-27):** build it so; Telegram launch data plus tailnet reach are both required |
