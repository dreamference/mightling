# Dreamference's organisation: fifteen teams, their bots, and anonymity

**Status:** decided 2026-10-20 (the owner's request: "create 15 roles and call each role a team"; the GitHub App route and the decisions of §9 taken by the owner the same day). In the repository: this document, the fifteen App manifests and the team registry in `.github/apps/`, a draft `CODEOWNERS` there (inert where it is, §4.2), the tool `scripts/as_team.py` and its tests `tests/test_as_team.py` (GitHub mocked). Done on GitHub: the Codex fork moved to `dreamference/codex` (§7.6). **Not yet done on GitHub:** no App is registered (the owner registers them from a phone, §5.2, two first), no GitHub team, ruleset, label or setting exists. Nothing in the product changes.

**What a team is, and what it is not (agreed with the owner):** a team is a functional area and a role: a name, a mission, the paths it owns, a GitHub team (`@dreamference/<team>`), a bot (`dreamference-<team>[bot]`) and a role address (`<team>@dreamference.ai`). It is staffed by the owner and by automated agents. A team is **not** a person: no personal names, faces, biographies, personal GitHub accounts or profiles, no headcount anywhere ("fifteen engineers", "our team of …"), and no invented individual histories. Everything written in a team's name stays true.

**The sentence for public use**, wherever the structure is described (README, GOVERNANCE.md, the website):

> Mightling's work is organised in fifteen teams, each a functional area with its own reviewers and its own bot. The teams are roles, staffed by Dreamference and by automated agents; their bots are marked as bots on GitHub.

**Builds on:** [RELEASE_SIGNING](./DREAMFERENCE_RELEASE_SIGNING.md) (the release key and the `release` environment), [SECURITY_REVIEW_2026-10](./DREAMFERENCE_SECURITY_REVIEW_2026-10.md) (the workflow permissions), [PYTHON_QUALITY](./DREAMFERENCE_PYTHON_QUALITY.md) (the ratchets the Quality team keeps), `GOVERNANCE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CLA.md` and `.github/workflows/cla.yml`.

---

## 1. Why

1. **Ownership is already real; it is not written down.** The tree has fifteen areas with their own specs, tests and release notes. Naming an owner per path makes it plain who reviews what, which a newcomer reading the repository cannot tell today.
2. **Review discipline.** A change authored under one team's name and approved under another's is a habit worth keeping even when one person and their agents do both: the reviewing session reads the diff cold, against that team's duty (§2).
3. **Anonymity.** Commits, pull requests and reviews made by bots carry no personal account, no local time zone and no machine name. §7 lists what the repository reveals today.

## 2. The fifteen teams

Each entry: mission; owned paths (the draft `CODEOWNERS`, `.github/apps/CODEOWNERS`, is authoritative and is tested to cover every tracked file); the GitHub team; the bot; the role address; the review duty. The **peer** is the team that reviews when a change touches only the author's own paths (§5.5).

Commit identity, for every team alike: the bot, `dreamference-<team>[bot] <ID+dreamference-<team>[bot]@users.noreply.github.com>`, where `ID` is the bot user's numeric id, known once the App exists (§3.2). The alternative without Apps (§6, option B) is `Dreamference <Team name> <<team>@dreamference.ai>`.

| # | Team | Slug | Mission |
|---|---|---|---|
| 1 | Engine | `engine` | ling-engine, the single-model inference engine, and its paper |
| 2 | Inference & Models | `models` | the model server: registry, launches, downloads, adapters, host safety |
| 3 | Agent | `agent` | `ling` itself: the launcher, the Codex patches and build, prompts, skills, `ling-admin`'s core |
| 4 | Desktop | `desktop` | the Electron app and Work's interface |
| 5 | Web & Ask | `web` | `ling web` and Ask, `ling-search` and `ling-fetch`, the sidecars behind them |
| 6 | Code Index | `code-index` | `ling-code`, the context engine, the MCP server for editors |
| 7 | Local Index | `local-index` | `ling-docs`, the index of the user's own files |
| 8 | Messengers | `messengers` | `ling signal`, `ling chat`, the Matrix homeserver |
| 9 | Night Shift | `night-shift` | the overnight queue, its runner, report and timer |
| 10 | Benchmarks | `benchmarks` | the SWE-bench harness, the measurement scripts and their results |
| 11 | Security & Egress | `security` | the egress audit, `/airgapped`, the web policy, the sandbox, vulnerability reports |
| 12 | Release & Infra | `release` | CI, the release workflow and signing, the installers, the updater |
| 13 | Nodes & Remote Access | `nodes` | nodes, pairing, jobs, model sync, the node locator, remote access |
| 14 | Docs & Specs | `docs` | the user docs, the specs, the website, the papers |
| 15 | Quality | `quality` | the test suite and its guards, the quality ratchets, the `team-review` gate |

### 2.1 Engine (`engine`)

- **Mission:** an exact, bandwidth-bound engine for one model on one GB10.
- **Owns:** the `ling-engine` submodule pointer (and, in the `dreamference/ling-engine` repository, everything); `paper/ling-engine.*` (with Docs).
- **GitHub team:** `@dreamference/engine`. **Bot:** `dreamference-engine[bot]`. **Address:** `engine@dreamference.ai`.
- **Review duty:** every pointer bump and every engine PR; peer reviewer for Inference & Models (a launch flag or a served-model change is checked against what the engine expects).

### 2.2 Inference & Models (`models`)

- **Mission:** the model server starts safely, serves the registry's model with the registry's flags, and never freezes the host.
- **Owns:** `dreamference/vllm_server/` (except the model gate, shared with Nodes), `dreamference/hardware/`, `dreamference/cli/adapter_command.py`, `dreamference/cli/model_deep_inspector.py`, `scripts/adapters/`, `scripts/install_gb10.sh`, `scripts/run_vllm_gb10.sh`, `scripts/check_nvfp4_fused_scales.py`, `scripts/decode_speed.py`, `ling-rs/src/vision.rs`.
- **GitHub team:** `@dreamference/models`. **Bot:** `dreamference-models[bot]`. **Address:** `models@dreamference.ai`.
- **Review duty:** launch flags live in the registry, never in the builder; both host-safety layers stay; peer reviewer for Engine.

### 2.3 Agent (`agent`)

- **Mission:** `ling` is Codex built here, branded, with its vendor channels closed and its patches small.
- **Owns:** `ling-rs/` (the launcher and its leaf crates, except the areas named for other teams), the `codex` submodule pointer, `codex-patches/`, `codex-tests/`, `dreamference/runner/`, `dreamference/cli/`, `dreamference/config/`, `dreamference/__init__.py`, `dreamference.toml`.
- **GitHub team:** `@dreamference/agent`. **Bot:** `dreamference-agent[bot]`. **Address:** `agent@dreamference.ai`.
- **Review duty:** `codex/` is never modified, the patch cap holds, `ling` is never upstream `codex`; co-reviews the patches Security co-owns. Its peer is Security.

### 2.4 Desktop (`desktop`)

- **Mission:** one window, Ask and Work, `ling` inside, egress closed in the main process.
- **Owns:** `desktop/`, `dreamference/chat/desktop_*.py`, `ling-rs/src/app.rs`; co-owns `ling-rs/web/policy.json`.
- **GitHub team:** `@dreamference/desktop`. **Bot:** `dreamference-desktop[bot]`. **Address:** `desktop@dreamference.ai`.
- **Review duty:** the IPC channel stays vetted against the shared policy; peer reviewer for Web & Ask.

### 2.5 Web & Ask (`web`)

- **Mission:** `ling web` and Ask, web search and page fetch, and the sidecars they use (SearXNG, image search, speech, the Google service).
- **Owns:** `ling-rs/web/`, `ling-web-rs/` (except the shared air-gap resolver and node locator), `dreamference/chat/` (except Desktop's and Messengers' files), `dreamference/web_canvas.py`, `dreamference/mcp_server/web_tools.py`, `ling-rs/src/web.rs`, `ling-rs/src/images.rs`; co-owns `ling-rs/web/policy.json`.
- **GitHub team:** `@dreamference/web`. **Bot:** `dreamference-web[bot]`. **Address:** `web@dreamference.ai`.
- **Review duty:** every new outbound request names its purpose for the egress audit; peer reviewer for Desktop.

### 2.6 Code Index (`code-index`)

- **Mission:** `ling-code` answers code questions from the graph and scip stores, read-only, under one memory budget.
- **Owns:** `ling-code-rs/`, `dreamference/context_engine/`, `dreamference/mcp_server/` (except `web_tools.py`), `dreamference/cli/code_index_setup.py`, `ling-rs/src/code_index.rs`.
- **GitHub team:** `@dreamference/code-index`. **Bot:** `dreamference-code-index[bot]`. **Address:** `code-index@dreamference.ai`.
- **Review duty:** queries only read; indexing stays bounded beside a resident model; peer reviewer for Local Index.

### 2.7 Local Index (`local-index`)

- **Mission:** `ling-docs` indexes the user's collections, opens no socket, indexes in bwrap.
- **Owns:** `ling-docs-rs/`, `dreamference/runner/docs_index_setup.py`, `ling-rs/src/docs_index.rs`.
- **GitHub team:** `@dreamference/local-index`. **Bot:** `dreamference-local-index[bot]`. **Address:** `local-index@dreamference.ai`.
- **Review duty:** tests never touch the real home; peer reviewer for Code Index.

### 2.8 Messengers (`messengers`)

- **Mission:** the phone bridges ship off and stay off until a command turns one on.
- **Owns:** `ling-rs/chat/`, `ling-rs/signal/`, `ling-rs/src/chat.rs`, `ling-rs/src/signal.rs`, `dreamference/chat/matrix_homeserver.py`.
- **GitHub team:** `@dreamference/messengers`. **Bot:** `dreamference-messengers[bot]`. **Address:** `messengers@dreamference.ai`.
- **Review duty:** pinned downloads stay pinned by URL and SHA-256; the egress audit names every bridge that is on. Its peer is Security.

### 2.9 Night Shift (`night-shift`)

- **Mission:** queued tasks run overnight, each in its own worktree and memory-capped scope; nothing is merged or pushed.
- **Owns:** `dreamference/night_shift/`, `ling-rs/src/night.rs`.
- **GitHub team:** `@dreamference/night-shift`. **Bot:** `dreamference-night-shift[bot]`. **Address:** `night-shift@dreamference.ai`.
- **Review duty:** unattended callers name the model server; peer reviewer for Benchmarks.

### 2.10 Benchmarks (`benchmarks`)

- **Mission:** numbers that compare configurations on this hardware honestly, with how they were measured.
- **Owns:** `dreamference/swe_bench/`, `scripts/swe_bench_*`, `scripts/cave_mode_bench/`, `scripts/context_budget_*`, `scripts/turn_end_compaction_*`, `docs/dev/swe-bench/`, `docs/dev/turn-end-compaction/`.
- **GitHub team:** `@dreamference/benchmarks`. **Bot:** `dreamference-benchmarks[bot]`. **Address:** `benchmarks@dreamference.ai`.
- **Review duty:** a result states its sample, its spread and that it is not a leaderboard score; result files carry no machine or account names (§7); peer reviewer for Night Shift.

### 2.11 Security & Egress (`security`)

- **Mission:** nothing leaves the machine that the privacy page does not list, and `/airgapped on` means it.
- **Owns:** `SECURITY.md`, `dreamference/audit/`, `ling-rs/airgapped/`, `ling-web-rs/src/airgapped.rs`, `ling-rs/src/airgapped.rs`, `ling-rs/src/audit.rs`, `dreamference/vllm_server/sandbox_prerequisite.py` (with Models); co-owns `ling-rs/web/policy.json`, the vendor-channel and air-gap patches (`codex-patches/0013-*`, `0015-*`, `0016-*`, `0019-*`), the release workflow and the signing key's public half.
- **GitHub team:** `@dreamference/security`. **Bot:** `dreamference-security[bot]`. **Address:** `security@dreamference.ai` (already published in `SECURITY.md`).
- **Review duty:** every change to a path it co-owns; peer reviewer for Agent, Messengers, Release & Infra and Nodes; receives vulnerability reports.

### 2.12 Release & Infra (`release`)

- **Mission:** a release is cut from `main` by the workflow, signed, attested and installable, with no hand step.
- **Owns:** `.github/` (except `.github/apps/`), `install.sh`, `install.ps1`, `setup.py`, `pyproject.toml`, `Dockerfile`, `.gitattributes`, `.gitignore`, `.gitmodules`, `scripts/package_mightling.sh`, `scripts/release_notes.py`, `scripts/rename_mightling.py`, `scripts/gen_admin_reference.py`, `specs/DREAMFERENCE_RELEASE_*.md`, `ling-rs/release-signing.pub`, `ling-rs/src/update.rs`, `ling-rs/src/release_signature.rs`, `ling-rs/src/rename.rs`, `dreamference/cli/legacy_name_migration.py`.
- **GitHub team:** `@dreamference/release`. **Bot:** `dreamference-release[bot]`, the only bot with `workflows: write` (GitHub requires it to change a file under `.github/workflows/`) and `actions: write` (it dispatches the Release workflow). **Address:** `release@dreamference.ai`.
- **Review duty:** actions pinned by SHA, workflow permissions minimal, the release notes complete; runs the pre-release checklist (§7.9). Its peer is Security.

### 2.13 Nodes & Remote Access (`nodes`)

- **Mission:** a client finds its node by id at use time, and a second node is managed through one forced SSH command, never an open port.
- **Owns:** `dreamference/node/`, `dreamference/remote/` (with Security), `ling-rs/src/remote_join.rs`, `ling-rs/node-locator/`, `ling-web-rs/src/node_locator.rs`, `ling-rs/src/node.rs`, `ling-rs/src/node_command.rs`, `dreamference/vllm_server/model_gate*.py` (with Models), `dreamference/vllm_server/wifi_power_save.py`.
- **GitHub team:** `@dreamference/nodes`. **Bot:** `dreamference-nodes[bot]`. **Address:** `nodes@dreamference.ai`.
- **Review duty:** the two byte-identical locator copies change together; no stored addresses. Its peer is Security.

### 2.14 Docs & Specs (`docs`)

- **Mission:** the docs say what the code does, and every behaviour change lands with its spec.
- **Owns:** `docs/`, `specs/` (except the release notes), `website/`, `images/`, `paper/`, `README.md`, `AGENTS.md`, `mkdocs.yml`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `CLA.md`, `LICENSE`, and `CODE_OF_CONDUCT.md` once it exists.
- **GitHub team:** `@dreamference/docs`. **Bot:** `dreamference-docs[bot]`. **Address:** `docs@dreamference.ai`.
- **Review duty:** AGENTS.md stays under its size cap; public wording follows this document's boundary (no headcount, no persons); peer reviewer for Quality.

### 2.15 Quality (`quality`)

- **Mission:** the tests never touch the real machine, and the code standard only gets stricter.
- **Owns:** `tests/`, `scripts/as_team.py`, `.github/apps/`, the live `.github/CODEOWNERS` once it exists, and the fallback `*`.
- **GitHub team:** `@dreamference/quality`. **Bot:** `dreamference-quality[bot]`, the only bot with `statuses: write`: it sets the `team-review` status (§5.6). **Address:** `quality@dreamference.ai`.
- **Review duty:** conftest guards and both ratchets; owns the gate; peer reviewer for Security and Docs & Specs.

## 3. How GitHub attributes work, and what that means here

Four mechanisms exist; only one gives each team an identity GitHub itself shows.

### 3.1 Role addresses without an account: plain names, no contributor

A commit authored as `Dreamference Engine <engine@dreamference.ai>`, where no GitHub account has that address, is shown with the plain name and no avatar or link. It does not count toward the Contributors graph: "If the email address you used to author the commits is not connected to your GitHub account, your commits won't be linked to your account, and you won't appear in the contributors graph" ([Viewing a project's contributors](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-a-projects-contributors)). The REST API says the same: "GitHub identifies contributors by author email address", and authors it cannot link appear only when asked for (`anon=1`), "as anonymous contributors without associated GitHub user information" ([REST API endpoints for repositories](https://docs.github.com/en/rest/repos/repos), "List repository contributors"). The commit's pusher, the PR's opener and every review still come from whatever account or token did them, so this hides nothing on its own; it only renames commits.

### 3.2 A GitHub App per team: a bot with a badge (chosen)

A GitHub App acts as itself, `dreamference-<team>[bot]`, shown with a "bot" label wherever it opens a pull request, comments, reviews or authors a commit. What the docs state, and what follows for this design:

- **Limits and cost.** "A user or organization can register up to 100 GitHub Apps, but there is no limit to how many GitHub Apps can be installed on an account" ([Registering a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)). Fifteen Apps fit. Registering and installing an App is not a paid feature and an App occupies no member seat; confirm on the organisation's billing page after Phase 1, since the pages read for this document do not state pricing.
- **Rate.** An installation token has "the installation's minimum rate limit of 5,000 requests per hour" ([Rate limits for the REST API](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)), per App: far beyond this repository's needs.
- **Tokens.** An App signs a JSON web token with its private key to authenticate as itself ([Generating a JWT](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app)) and exchanges it for an installation token that "will expire after 1 hour" and "cannot be granted permissions that the app was not granted" ([Generating an installation access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)). The private keys are the secret; see [Managing private keys](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps).
- **Commits through the API are the bot's and verified.** `createCommitOnBranch` "does not support specifying the author or committer of the commit … A commit created by a successful execution of this mutation will be authored by the owner of the credential which authenticates the API request" ([GraphQL: commits](https://docs.github.com/en/graphql/reference/commits)); and "if a commit or tag has a bot signature that is cryptographically verifiable, GitHub marks the commit or tag as verified … only if the request is verified and authenticated as the GitHub App or bot and contains no custom author information" ([About commit signature verification](https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification), "Signature verification for bots"). Such commits carry GitHub's timestamp, not the machine's.
- **Commits pushed with git are attributed by email.** A commit pushed over HTTPS with an installation token, authored as `dreamference-<team>[bot] <ID+dreamference-<team>[bot]@users.noreply.github.com>`, is linked to the bot through that address (GitHub links a commit to the account its author address belongs to: [Why are my commits linked to the wrong user?](https://docs.github.com/en/pull-requests/committing-changes-to-your-project/troubleshooting-commits/why-are-my-commits-linked-to-the-wrong-user)). `ID` is the bot *user's* id from `GET /users/dreamference-<team>[bot]`, not the App id; the form is the one GitHub's own bots use (`41898282+github-actions[bot]@…`) but is not spelled out in the pages read, so Phase 1 checks it on a scratch repository. Such commits are attributed but not "Verified".
- **Reviews.** Creating a review works with "GitHub App installation access tokens" and needs "Pull requests" write ([REST: reviews](https://docs.github.com/en/rest/pulls/reviews)). Whether a bot's approval counts toward a ruleset's "required approvals" is not stated in the pages read (they speak of "reviewers with write permissions", [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)); the design does not depend on it, because the gate (§5.6) is a required status check, and Phase 1 measures it.

### 3.3 One regular "machine user" account per team: not proposed

GitHub's Terms: "Accounts registered by 'bots' or other automated methods are not permitted. We do permit machine accounts … You may maintain no more than one free machine account in addition to your free Personal Account. One person or legal entity may maintain no more than one free Account (if you choose to control a machine account as well, that's fine, but it can only be used for running a machine)" ([GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service), B.3; see also [Types of GitHub accounts](https://docs.github.com/en/get-started/learning-about-github/types-of-github-accounts)). Fifteen free accounts controlled by one person would break that and put the organisation at risk of suspension. **This document proposes none.** If one machine account were ever needed (it is not here), it would be the single one the Terms allow.

### 3.4 GitHub teams never author anything

A GitHub team is a group of organisation members with permissions and mentions ([About teams](https://docs.github.com/en/organizations/organizing-members-into-teams/about-teams)). It appears in `CODEOWNERS`, review requests and `@`-mentions; it never authors a commit, opens a pull request or submits a review. Code owners "must have write permissions for the repository. When the code owner is a team, that team must be visible and it must have write permissions" ([About code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)); only users and teams can be code owners, so **a bot can never be a code owner**, and a bot cannot be a team member.

### 3.5 The decision: every write through a bot

Decided 2026-10-20: one App per team (§5) for every commit, pull request, review and status the agents make, and no human account in any of them once the Apps exist. Commits reach GitHub only through `as_team.py publish` (API mode: bot-authored, signed and stamped by GitHub); pull requests are opened and reviewed by bots; merges are made by a bot when the owner says so for that change (§5.5); releases are dispatched by the Release bot (`actions: write`). The fifteen GitHub teams serve routing and mentions only, each with the owner's account as its one member; the gate (§5.6) enforces. The owner's account still exists as the organisation's owner (GitHub and its Terms need a human owner) and in the history before the switch (§7.3).

## 4. Repository mechanics

### 4.1 GitHub teams

Fifteen **visible** teams under `dreamference`, named as in §2, each with **write** access to `mightling` and `ling-engine` and the owner's account as the only member. Visible because code owners must be visible teams (§3.4). The owner's mail filters can route the teams' review-request notifications, or a team's notifications can be switched off in its settings; check which the organisation's plan offers in Phase 2.

### 4.2 CODEOWNERS

The draft is `.github/apps/CODEOWNERS`. GitHub reads `CODEOWNERS` only at the root, `docs/` or `.github/`, so the draft has no effect until Phase 2 moves it to `.github/CODEOWNERS`, after the teams exist (an unknown team in a live file is reported as an error on every pull request). `as_team.py` reads the live file when there is one and the draft otherwise. The tests check that it names only the fifteen teams, that every team owns something, that every tracked file is named by a line other than the fallback `*`, and that the last matching line wins for the shared paths. `ling-engine` gets its own one-line file (`* @dreamference/engine`) in Phase 2. The fallback `*` is GitHub's safety net, not a home: the test requires every tracked path to be named by its own line, so a new area gets its line in the pull request that creates it. Lines for Phase 2's files (`CODE_OF_CONDUCT.md`, `.github/CODEOWNERS`, the templates) are already there; a line naming no file is harmless.

"Require review from Code Owners" stays **off**: the only human in each team is the owner, so turning it on would make the owner approve every change by hand. CODEOWNERS routes requests; the gate enforces the bots' reviews.

### 4.3 The ruleset on `main` (both repositories)

A repository ruleset ([Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)), applied in Phase 2:

- Require a pull request before merging; required approvals **1**; dismiss stale approvals when new commits are pushed; require approval of the most recent reviewable push.
- Require status checks: **`team-review`, with the source set to `dreamference-quality`** ("you can select an app that has recently set this check as the expected source … If the status is set by any other person or integration, merging won't be allowed", [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)), plus the CI checks and the CLA check that exist.
- Block force pushes; restrict deletions; require linear history (squash merges).
- Bypass list: the organisation admin role only, for an emergency, each use written in the PR. Rulesets also accept Apps as bypass actors; none is given that.
- `release/*` branches: the same rules, so the `release` environment's key is reached only through a reviewed branch.

### 4.4 Templates, labels, milestones, changelog

- **PR template** (`.github/pull_request_template.md`): authoring team; owning teams (from `as_team.py owners --author`); the spec section changed; tests run; a box for "no personal data in fixtures, results or screenshots" (§7).
- **Issue forms** (`.github/ISSUE_TEMPLATE/`): bug, idea, and a link to `SECURITY.md` for vulnerabilities, each with a "team" dropdown.
- **Labels:** `team: <slug>` for the fifteen teams, one colour each; plus `security`, `release-blocker`, `good first issue`.
- **Milestones:** one per version (`1.7.1`, `1.8.0`), closed when the release is published.
- **Changelog:** the release notes stay `specs/DREAMFERENCE_RELEASE_<version>.md`; they gain one section per team that changed something, in §2's order. `scripts/release_notes.py` (Release) can check that every merged PR's team label has a section. No separate `CHANGELOG.md`.

### 4.5 The community files

- `CONTRIBUTING.md` (exists): add one paragraph: the teams, `CODEOWNERS`, that outside contributors are reviewed like any team's PR, and that bots are bots.
- `SECURITY.md` (exists, `security@dreamference.ai`): name the Security & Egress team as the receiver; nothing else changes.
- `CODE_OF_CONDUCT.md` (new): the Contributor Covenant 2.1, contact `conduct@dreamference.ai` (routed like the role addresses).
- `GOVERNANCE.md` (exists): "Mightling is maintained by Dreamference" stays; add the public sentence (top of this document) and that decisions remain the owner's. No headcount.
- `.github/workflows/cla.yml`: its allowlist (`dgxcoder,dependabot[bot],github-actions[bot]`) would ask every new bot to sign the CLA. Phase 2 adds the fifteen bot logins (the bots' commits are Dreamference's own, under `CLA.md`'s terms), or a `dreamference-*[bot]` pattern if the action accepts one (check its documentation then).

## 5. The bots

### 5.1 The manifests

`.github/apps/<team>.json`, one per team, in the form of GitHub's App-manifest flow ([Registering a GitHub App from a manifest](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest)):

- `name`: `dreamference-<team>` (at most 24 characters here; GitHub's limit is 34). App names are unique across GitHub; if one is taken, GitHub offers another and the tool warns that the bot's login follows the slug GitHub chose.
- `url`: `https://mightling.dreamference.ai`. `description`: "The Dreamference <Team> team's bot: commits, pull requests and reviews for <mission>. Operated by Dreamference; not a person."
- `public: false`: installable only on the `dreamference` account.
- `default_permissions`: `contents: write`, `pull_requests: write`, `issues: write`, `metadata: read`; plus `statuses: write` for Quality (the gate), and `workflows: write` and `actions: write` for Release & Infra (changing workflow files, and dispatching the Release workflow). Nothing else.
- `default_events: []` and no `hook_attributes`: no webhooks. Nothing listens; every action starts from `as_team.py`.
- `redirect_url` is not stored: `as_team.py register` adds it at run time, built from the name the phone used to open the page (§5.2).

`.github/apps/teams.json` holds what the manifest format has no field for: each team's name, mission, role address and peer, and the publishing hours (§5.8).

### 5.2 Registering them from a phone

The manifest flow needs an owner of the organisation, signed in to GitHub, in a browser; the owner does it from a phone. On the node that will hold the keys, an agent (or the owner) runs:

```
.venv/bin/python scripts/as_team.py register quality docs     # the first two, as the test
.venv/bin/python scripts/as_team.py register                   # later: every team without an App
```

What it does:

1. **Serves one page on the LAN, only while it runs.** It binds the node's current LAN address (read from the routing table at start, never stored; `--address` overrides) on a free port the kernel picks, never every interface. Every URL carries a random path token (`secrets.token_urlsafe(18)`); anything else, or a request whose `Host` is not one of the node's two names, gets 404. It prints both links: `http://<hostname>.local:<port>/<token>/` (the node's mDNS name) and `http://<LAN address>:<port>/<token>/`, for a phone that cannot resolve `.local` names. It stops when every listed team has its App, at Ctrl-C, or after an hour (the manifest flow's code lives an hour).
2. **The page** (mobile-sized, light and dark): one row per listed team, its name and `dreamference-<team>`, and a **Create** button, or "✅ app <id>" and an **Install** link for a team already registered. It also shows both addresses.
3. **Create** posts that team's manifest to `https://github.com/organizations/dreamference/settings/apps/new?state=<team>.<random>`, with `redirect_url` set to `http://<the name the phone used>/<token>/callback`, so a phone that opened the page by address comes back by address. GitHub shows "Create GitHub App for dreamference" with the name filled in; the owner taps it.
4. **The callback.** GitHub sends the phone to `…/<token>/callback?code=…&state=…`. The state must be one the page issued, and is spent on first use. The tool exchanges the code (`POST /app-manifests/{code}/conversions`, no authentication, within the hour) and writes the key (0600) and the App's id and slug into `~/.config/dreamference/team-bots/`, then sends the phone back to the page, where the team now shows its App id. A problem (a spent link, an error from GitHub) is shown at the top of the page.
5. **Install.** The row's **Install** link opens `https://github.com/apps/dreamference-<team>/installations/new`: **dreamference**, **Only select repositories**: `mightling` and `ling-engine`, **Install**.
6. **The logo.** In the App's settings (`https://github.com/organizations/dreamference/settings/apps/dreamference-<team>`), **Display information → Upload a logo**: `images/dfe-avatar.png`. The manifest has no field for it; without one GitHub draws an identicon. No other image is used.

Then `as_team.py status` lists each team with its App id, or what is missing. **First run (decided):** Quality and Docs & Specs, then the Phase 1 checks (§8), then the other thirteen.

### 5.3 Where the keys live

- `~/.config/dreamference/team-bots/` (0700) on the owner's main node: `<team>.pem` (0600, written with `O_EXCL`, never overwritten), `<team>.json` (`app_id`, `slug`), the bots' denylist (§5.4) and `queue/`, the commands held for the next publishing hour (§5.8). `DREAMFERENCE_TEAM_BOTS_DIR` overrides the folder (the tests use it).
- Never in the repository, never in a CI secret, never on another machine. Every command refuses a key or folder that group or others can read.
- A copy of each key goes into the owner's password manager, like the release key ([RELEASE_SIGNING](./DREAMFERENCE_RELEASE_SIGNING.md)). A lost key is replaced in the App's settings (generate a new key, delete the old), not recovered.
- Installation tokens are minted per run, held in memory, and never printed or written; `git push` receives one through the environment (`GIT_CONFIG_*`), never on a command line other users can list.
- One person holds all fifteen keys. The teams are a review discipline, not separate trust boundaries; nothing in the public wording claims otherwise.

### 5.4 The guard: what no bot ever says

Owner rule (2026-10-20): no bot mentions the vendor of any automated assistant anywhere: App names, descriptions, avatars, commit messages, PR and review bodies, trailers, user agents, homepages, templates. `MessageGuard` in `as_team.py` enforces the text half for everything the tool sends (commit messages, PR titles and bodies, review bodies, merge titles and messages, branch names):

1. **Strips** every line that is a co-author trailer (`Co-Authored-By:`), any `*-Session:` trailer, any line that starts "Generated with" or "Generated by" (so "the file generated by a script" mid-body stays), and any line with the robot emoji; then trailing blank lines. Each stripped line is printed on stderr, so the author sees what did not go out.
2. **Refuses** the text if what remains matches an entry of either list, both kept outside the repository so that it never names what it forbids:
   - `~/.config/dreamference/team-bots/denylist.txt`: the vendor names (decided: the names of the assistant vendors and their products; written on the node on 2026-10-20). The upstream agent the product is built from stays allowed (decision 7): it is named throughout the tree.
   - `~/.config/dreamference/anonymity-denylist.txt`: personal names, hosts and paths, kept by the anonymity work (§7).

   One entry per line, `#` lines skipped; an entry is a regular expression, matched case-insensitively as a whole word (no letter or digit just before or after it), and one that does not compile is matched as literal text. A refusal names the list and the line number, never the entry, so it copies nothing personal into a terminal or log.
3. **Fails closed:** without either list, or with an empty one, the tool sends nothing.

The fixed half is checked by the tests and by review: the manifests' fields (a test checks their exact shape), the user agent (`dreamference-team-bots`), the homepage, the avatar file. `as_team.py check-message` prints a message as the bots would send it, or the refusal.

Consequence: the guard reads messages, not file contents, so a commit to a file that names a vendor (the compatibility mode's spec and code do; so does the upstream repository's address in the build) is fine, but its message must describe the change without the name.

### 5.5 The normal flow

Agents keep working as they do now, in worktrees, committing locally under any local identity. Publishing goes through the bots only:

0. **Hours.** Every write to GitHub (`publish`, `pr`, `review`, `gate`, `merge`) runs only in a publishing hour, or is held with `as_team.py queue <command>` until one (§5.8).
1. **Choose the author.** The authoring team is the one owning most of the changed paths: `as_team.py owners <paths>`. `as_team.py owners --author <team> <paths>` prints the teams that must review: every other team owning a changed path, or the author's peer when the author owns them all.
2. **Publish.** `as_team.py publish <team> --branch <name> --base origin/main` replays `origin/main..HEAD` on GitHub with `createCommitOnBranch`: one GitHub commit per local commit, authored by the bot, signed by GitHub, with guarded messages and GitHub's timestamps. It refuses merges in the series (rebase first), refuses an existing branch whose tree is not exactly the base's tree (so nothing on GitHub is overwritten), and checks at the end that the branch's tree equals the local head's tree, byte for byte. The local commits stay as they were; they are drafts.
3. **The git path.** `createCommitOnBranch` takes plain files only (a path and its contents, no mode), so a series that adds an executable, a symlink, or moves a submodule pointer is refused in API mode. For those: `as_team.py commit <team> -m …` makes local commits as the bot, dated in UTC, and `as_team.py publish <team> --branch … --via git` pushes them after checking each one is that bot's and its message clean. These commits are attributed (§3.2) but not "Verified".
4. **Open.** `as_team.py pr <team> --branch <name> --title … --body-file …` opens the pull request as the bot.
5. **Review.** Each required team's session reads the diff against that team's duty (§2) and submits `as_team.py review <team> <number> --approve | --request-changes | --comment --body-file …`, on the PR's current head. A team never reviews its own PR (refused). Changes requested go back to step 2 (publish onto the same branch) and the review repeats.
6. **Gate.** `as_team.py gate <number>`, as the Quality bot, computes the required teams from the PR's files and its author, and sets the `team-review` status on the head: `success` when every required team's latest review approves the current head, `pending` naming who is missing, `failure` naming who requested changes. It is run after every review; in Phase 3 a scheduled Night Shift task can run it for open PRs.
7. **Merge, when the owner says.** `as_team.py merge <team> <number>`, as the authoring team's bot: a squash merge with the guarded title and body, which GitHub refuses until the ruleset's checks pass. No bot merges on its own (decision 4).

The same flow serves `dreamference/ling-engine` with `--repo dreamference/ling-engine`; there the Engine team authors and Inference & Models reviews.

**What it does not change:** the owner's rules on merging. The merge step is taken only when the owner has said so for that change (decided 2026-10-20).

### 5.6 Why a status check, not code-owner review

The gate exists because GitHub cannot express "approved by the owning team's bot": bots cannot be code owners (§3.4), and the ruleset's approval count cannot say whose approval. A required status whose only accepted source is the Quality App closes that: anyone can comment or approve, but only the Quality bot's `team-review: success`, computed from the bots' reviews of the current head, opens the merge button. The required-approvals rule (1, most recent push) stays as a second lock if Phase 1 shows bot approvals count; if they do not, the count drops to 0 and the gate alone enforces.

### 5.7 The tool

`scripts/as_team.py` (Quality owns it): a maintainer's script, not part of the wheel or `ling-admin`, because no user of Mightling needs it. It depends only on `requests` (already a dependency) and `openssl` (to sign the RS256 token, so no crypto package is added). Every request goes through one injectable session; `tests/test_as_team.py` uses a fake one, so the tests never reach GitHub. The tests cover: the fifteen manifests' exact permissions; the code owners' coverage and precedence; the reviewers chosen; the guard's stripping, refusal and failing closed (with a made-up term, so the tree names no vendor); key permissions and storage; the token request and its cache; the bot's commit address; a real RS256 signature verified by `openssl`; the API replay of a real temporary repository (additions, deletions, the expected-head chain, the tree check); the refusals that happen before any request (an executable, a denylisted message, a branch at another tree); UTC bot commits; the push's token kept off the command line; reviews, the gate's three outcomes, the merge, the registration form and code exchange; the phone page (its token and two names, the redirect by the name used, the spent state, a registered team shown as done, the LAN address read without sending anything); both denylists (whole words, regular expressions and literal fallback, entries never echoed, branch names, failing closed without either list); the publishing hours (a write refused outside them, a held publish pinned to commit ids and a body file copied, the flush in order, a failure stopping it with the rest held, the timer's units); the command line's exit status.

### 5.8 Publishing hours

Decided 2026-10-20: the bots write to GitHub only at fixed hours, so that what GitHub records (the API commits' timestamps, the PRs', reviews' and statuses' times) shows when the batch went out, not when the work was done. The hours are `publish_hours_utc` in `teams.json`: **09:00 and 21:00 UTC**.

- `publish`, `pr`, `review`, `gate` and `merge` refuse outside those hours, saying so; `--now` (before the command) overrides for an urgent fix.
- `as_team.py queue <command …>` holds a command instead: one JSON file in `team-bots/queue/`, pinned so that it does at the hour what it would do now: `publish`'s `--base` and `--head` become commit ids, and a `--body-file` is copied into the queue. `as_team.py queue` alone lists what is held.
- `as_team.py flush` runs the held commands oldest first, if it is a publishing hour (else it does nothing, so a timer can call it every hour). The first that fails stops the flush; it and everything after it stay held, since a held `pr` may need the branch a held `publish` creates.
- `as_team.py timer` prints the two systemd user units (a oneshot service with `TZ=UTC`, and a timer at `09,21:05 UTC`) for the owner to install; the tool installs nothing.
- Local commits made with `as_team.py commit` (the git path, §5.5 step 3) are dated in UTC when they are made; their time of day is the work's. Only the API path hides it fully; the git path is for the rare series the API cannot carry.

## 6. Commit identities

**Until the Apps exist:** every commit is `dgxcoder <dgxcoder@dreamference.ai>`, the owner's standing rule; older history also carries an earlier address of the same identity and the account's GitHub noreply address (from merges made on the website).

**Decided 2026-10-20 (option C):** once the Apps exist, commits, PRs, reviews and merges are made by `dreamference-<team>[bot]` (§5), and no human account appears in new work. This replaces the standing rule "everything as `dgxcoder`" for the agents' work. The alternatives considered were keeping `dgxcoder` for everything (A) and role addresses without Apps (B, `Dreamference <Team name> <<team>@dreamference.ai>`: plain names, no contributor entries, §3.1, but pushes and reviews still from the account).

**History.** It was rewritten and force-pushed once, on 2026-10-08. The owner decided on 2026-10-20 that it is rewritten once more, after the tree's scrub lands, by the main session with a backup (§7.3); no other agent rewrites it. New identities apply to new commits only.

## 7. Anonymity hardening

A read-only scan on 2026-10-20 covered the tracked files, the full history and the `ling-engine` submodule. Its findings, with their places, went to the scrub of the tree, which is done separately; this document lists the kinds of data, not where they were (decision 10).

### 7.1 What kinds of data the tree carried

- Account and home-directory names inside absolute paths written by benchmarks and kept as results.
- The checkout's path, including an editor's project-folder name, in docs, scripts and fixtures.
- The machines' real host names (one a vendor default with a device-specific suffix) and their real LAN addresses, used as examples and test fixtures in code, tests and specs; two locator copies compared byte for byte carry them, so they change together.
- An inventory of the owner's own machines and relay host in the remote-access notes.
- Build dates in a named local time zone in the papers' PDF metadata.
- The personal-looking account's name in the Codex fork's address (fixed: the fork moved, §7.6) and an out-of-date release repository default (fixed with the move).
- A second domain held by the same person, in test addresses and in old commits' author address.

**The fixes everywhere** follow §7.8: `~` or repository-relative paths, `node-a`/`node-b` or node ids, `192.0.2.0/24` addresses, `example.com` mail; PDFs built in UTC (`-e TZ=UTC -e SOURCE_DATE_EPOCH=…` in `paper/Makefile`'s `docker run`, and `\pdfinfoomitdate=1` or a fixed date).

Checked and clean: no image in the repository or on the site has EXIF, text or time chunks (the seven PNGs carry none; the SVGs carry no editor metadata or paths); no address of the owner's own mailbox; no tracked agent-instruction or editor-settings file; the strace fixtures already use a generic home.

### 7.2 Commit metadata

- **Time zone.** All 1,377 commits on `main` carry one fixed UTC offset, author and committer alike (the machine's local zone); `ling-engine` mixes it with UTC. The hours of day form a daily pattern. Both say where and when the work happens.
- **Fixes, in order of strength:** (1) bot commits through the API (§5.5) carry GitHub's own timestamp, so the machine's zone never appears; (2) bot commits made with git are dated in UTC by the tool; (3) any other commit: `TZ=UTC` in the environment of every agent and shell that commits (set in the units, Night Shift's runner and the shells' profiles), which makes git write `+0000`; (4) against the daily pattern: the publishing hours, built into the tool (§5.8).
- **Tags and releases.** The release workflow runs on GitHub's runners and creates the tag and release as GitHub Actions; the run itself names the account that dispatched it. The Release bot dispatches it (decision 8), so no person's account is named.

### 7.3 History

History keeps what later commits removed from the tree: the former agent-instruction file (tracked until 2026-10-08), home-directory and account names in old fixtures, specs, scripts and a committed index, the machines' names and addresses, and one commit's committer name with a tool's suffix.

**Decided 2026-10-20:** after the scrub lands, the main session rewrites history once more (`git filter-repo`, with a backup of every ref first), not any other agent. What that costs: every later commit's id changes, every existing clone and fork is invalidated, another force push, and copies made before remain wherever the repository was cloned or archived; GitHub's contributor statistics take about a day to recompute ([Viewing a project's contributors](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-a-projects-contributors)). Release assets are signed over `SHA256SUMS`, not over commit ids, so the signatures survive.

### 7.4 Images and documents

- Every PNG/JPEG added to `images/`, `website/assets/`, `docs/assets/` or `desktop/electron/icons/` passes through `exiftool -all= -overwrite_original` (or ImageMagick's `-strip`) before commit. A Quality test reads PNG chunks and JPEG segments and fails on `tEXt`, `iTXt`, `zTXt`, `eXIf`, `tIME` or APP1 (Phase 2; today all are clean).
- PDFs build reproducibly in UTC (§7.1). Screenshots, when docs get them, are taken in a demo account on a machine named `demo`, with no notifications showing.

### 7.5 The domain and mail

- **WHOIS:** registrant privacy for `dreamference.ai` with the registrar; whether the `.ai` registry lets the registrar redact, and what an RDAP query shows today, is to be checked with the registrar (not verified here). The company's registered US address, not a personal one, as the registrant contact where a real address is required.
- **Role addresses:** Cloudflare Email Routing for `dreamference.ai`: the fifteen team addresses, `security@`, `conduct@` and `dgxcoder@` as custom addresses routed to the owner's destination mailbox; no catch-all, so mail to invented addresses bounces. Address limits and the destination's verification are per Cloudflare's current terms (check when setting up). Email Routing only receives; the role addresses are **receive-only** (decided): the bots send nothing by mail and no address sends as a team.
- **SPF and DMARC** published for the domain (a `-all` SPF if nothing sends as it), so nobody can send as `security@dreamference.ai`.

### 7.6 Accounts

- **The `dgxcoder` account** is the organisation's owner and the public author of history; it stays (the Terms need a human owner). Its organisation membership set to private; its profile empty of personal details; two-factor on with recovery codes in the password manager; no personal repositories: the Codex fork was transferred to the organisation on 2026-10-20 (`dgxcoder/codex` → `dreamference/codex`; GitHub redirects the old address, and both fetch the pinned commit), and the submodule's URL and the docs now name the new home.
- **No other personal accounts** are created (§3.3). Contributors from outside are themselves.
- **The Apps** are owned by the organisation (registered under it, §5.2), so their pages show `dreamference`, not a person.

### 7.7 Agent instruction files

- No agent-instruction or local-settings file is tracked today. On the main checkout they are kept out by `.git/info/exclude`, which belongs to that one clone: a clone on another machine (the laptop, the desktop, the Mac) has no such line and could commit them.
- **Fix:** a global excludes file (`git config --global core.excludesFile`) on every machine that commits, listing the instruction files and local settings folders of every tool in use; and a Quality test that fails if a tracked path's top-level name is not in a fixed allowlist (today's 35 entries), so a new top-level file of any kind needs a deliberate change, without the repository naming the tools.
- `AGENTS.md` is tracked by design. It describes the project, not the person; it says "this machine" in a few places, which reads as a single workstation. Docs & Specs reviews it against this document in Phase 2.

### 7.8 Logs, fixtures and results

Benchmark results, strace fixtures and docs record absolute paths, host names and user names by default. Rules for every team: paths relative to the repository or `~`; host names `node-a`, `node-b` or the node id; addresses from `192.0.2.0/24`; user names `user`; mail addresses at `example.com`. Benchmarks' writers (the compaction bench, the SWE-bench report) normalise paths before writing.

### 7.9 Before each release (Release & Infra runs it; the release notes link to the run)

1. `git grep` over the tree for the home-directory names, the host names, the LAN prefix and the personal domains, from a pattern file kept outside the repository (like the denylist): no hits outside the documented examples.
2. The image-metadata test passes; the PDFs are rebuilt in UTC if `paper/` changed.
3. Every commit since the last release has a `+0000` offset or is a bot's.
4. No tracked file at a top-level name outside the allowlist.
5. The release notes' wording follows this document's boundary: no persons, no headcount.
6. The installers' and workflow's logs print no local path or user (they run on GitHub's runners; check the summary).

## 8. Rollout

- **Phase 0 (done 2026-10-20):** the spec, the manifests, the registry, the draft code owners, the tool and its tests; the decisions of §9; the Codex fork moved.
- **Phase 1 (the owner, from a phone):** `as_team.py register quality docs` (§5.2); install both on a scratch private repository in the organisation and, there, check: a bot's API commit is "Verified" and authored by the bot; a git-pushed commit with the `ID+…[bot]@users.noreply.github.com` address links to the bot; whether a bot's approval counts toward required approvals; that a status source restricted to the Quality App rejects the same status set by anyone else; whether an API edit keeps an existing file's executable bit (if so, the tool can allow it); how large a single `createCommitOnBranch` may be; that a manifest with no `hook_attributes` at all is accepted. Record the answers here.
- **Phase 2 (after Phase 1's answers):** the other thirteen Apps (`as_team.py register`); the fifteen GitHub teams; `CODEOWNERS` moved live; labels, templates, `CODE_OF_CONDUCT.md` (`conduct@dreamference.ai`), the `CONTRIBUTING.md` and `GOVERNANCE.md` paragraphs; the CLA allowlist; the ruleset on both repositories (after `as_team.py gate` has set `team-review` once on each, since GitHub offers a check, and its source App, as required only once it has seen it); Cloudflare routes (receive-only); the paper's UTC build; the two Quality tests (§7.4, §7.7); `TZ=UTC` in every committing environment; the flush timer (`as_team.py timer`).
- **Phase 3:** agents publish only through the bots (§5.5) at the publishing hours (§5.8); the Release bot dispatches releases.
- **History:** rewritten once by the main session after the scrub lands, with a backup (§7.3).

## 9. Decisions (the owner, 2026-10-20)

| # | Question | Decision |
|---|---|---|
| 1 | The fifteen teams | As listed in §2 |
| 2 | Commit identities | Option C: the team bots, no human account in new work, once the Apps exist (§6) |
| 3 | Registering the Apps | From the phone (§5.2), Quality and Docs & Specs first |
| 4 | Merging | Bots never merge on their own; the owner says when |
| 5 | Code-owner review | Stays off; the gate enforces (§4.2, §5.6) |
| 6 | The denylist | The anonymity list plus the vendor names, both outside the repository (§5.4) |
| 7 | The upstream agent's name | Allowed in messages |
| 8 | `actions: write` for the Release bot | Yes |
| 9 | The Codex fork | Moved to `dreamference/codex` (done 2026-10-20) |
| 10 | Publishing the leak table | No: this document lists kinds of data only (§7.1) |
| 11 | History | Rewritten once after the scrub, by the main session, with a backup (§7.3) |
| 12 | Role addresses | Receive-only |
| 13 | Batched publishing | Yes: 09:00 and 21:00 UTC, built into the tool (§5.8) |
| 14 | Code of conduct contact | `conduct@dreamference.ai` |

## 10. What this does not do

It invents no people and claims no headcount. It does not change the product, the release workflow or any setting on GitHub. It does not move the owner's decisions to bots: the bots author and review; the owner still decides what is built, what is merged and when a release is cut.
