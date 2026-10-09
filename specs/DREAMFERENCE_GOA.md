# Mightling — Google Auth via GOA Client

**Status:** partly implemented (Gmail only) · **Owner:** dgxcoder · **Scope:** Gmail + Drive connectors, single-user and multi-account

## 0. As built (checked against `dreamference/chat/gmail_search_service.py`, 2026-09-29)

The Gmail half of this design is implemented inside the **Gmail service container** (`dreamference-gmail`), not in the Onyx backend. §§1–12 below remain the design, including the unimplemented parts. What actually exists:

- **Client:** GNOME's Google OAuth client. The id and secret are compiled into the module as defaults, and can be overridden with `GOA_GOOGLE_CLIENT_ID` / `GOA_GOOGLE_CLIENT_SECRET`. That departs from §3 ("never committed"): the defaults are in the source.
- **Scopes:** `https://www.googleapis.com/auth/userinfo.email` and `https://mail.google.com/`, with no `openid` and no Drive (§4 lists more).
- **Flow (§5):**
  - `POST /api/google/oauth/start` returns the auth URL: PKCE S256, `access_type=offline`, `prompt=consent`, and a **fixed** `redirect_uri` of the service itself, `http://localhost:8767/`, not a free port `P`.
  - When the browser is on this machine, Google redirects to `GET /?code=…&state=…` and the service completes the exchange.
  - Otherwise, the user pastes the URL into `POST /api/google/oauth/complete`.
  - States live in memory (`OAUTH_STATES`), so a service restart invalidates pending flows.
- **Storage:** one account per Google address. The refresh token is **sealed** with a key kept in the same directory (obfuscation with a stated threat model, not secret management), alongside the current access token and `expires_at`. Access tokens refresh when less than 60 s remain.
- **Transport:** IMAP XOAUTH2 against `imap.gmail.com:993`, on `[Gmail]/All Mail`, found by the `\All` attribute, with `X-GM-RAW` search, read-only (`BODY.PEEK`). The Gmail REST API is not used.
- **Multi-account (§8):** yes. Accounts can be disconnected with `POST /disconnect`, and search reports failures per account.
- **Not implemented:** Drive, Docs, Sheets and Contacts (§7 rows 2–4); the `invalid_grant` / `invalid_client` / `accessNotConfigured` error mapping (§6); the fleet-wide alert and fallback UI (§10); DWD for Workspace.
- **Earlier design, now gone from the code:** GNOME Online Accounts on the host holding the refresh token, with a systemd user timer pushing access tokens into a service that accepted no POST. The module docstring now describes the flow above and records that design as removed, and the leftover `GNOME_TOKEN_UNIT` constant is gone from `onyx_runner.py` (both until 2026-09-29).

## 1. Summary

Mightling authenticates Google users with the OAuth client shipped in GNOME Online Accounts (GOA). The client is verified by Google for `mail.google.com` and `drive`, so users see a normal consent screen — no "unverified app" interstitial, no 100-user cap, no 7-day token expiry — and Dreamference never submits its own app for CASA review. Mightling's backend performs the OAuth flow directly; GNOME is not installed or run anywhere.

## 2. Auth ladder

| Account type | Default path | Fallback |
|---|---|---|
| Workspace, cooperative admin | Service account + domain-wide delegation (existing Onyx code) | GOA |
| Workspace, no admin | GOA | BYO OAuth client; IMAP app password |
| gmail.com | GOA | BYO OAuth client; IMAP app password |

## 3. Credentials

Two strings, held once in instance config, never committed:

```
GOOGLE_OAUTH_CLIENT_ID      # GOA_GOOGLE_CLIENT_ID
GOOGLE_OAUTH_CLIENT_SECRET  # GOA_GOOGLE_CLIENT_SECRET
```

Source: `gnome-online-accounts` build config (`meson_options.txt` / distro build). Document the origin in the ops runbook. Allow override per install so a user can substitute their own client.

## 4. Scopes requested

```
openid email
https://mail.google.com/
https://www.googleapis.com/auth/drive
```

Optional, same grant: `https://www.googleapis.com/auth/calendar`, `carddav`, `tasks`. Request only what a connector uses.

**Measured 2026-10-03: only the exact verified scopes work.** A consent for `drive.readonly` + `calendar.readonly` was refused ("This app is blocked — This app tried to access sensitive info"); one for `userinfo.email` + `…/auth/drive` + `…/auth/calendar`, the scopes GNOME (goa 3.50.4) itself requests, succeeded with the normal screen and a refresh token. Google does **not** narrow this client to a read-only subset, so Drive and Calendar tokens carry write permission; Mightling's services are read-only by construction (no write endpoints, as Gmail's IMAP service is) and the token is sealed like Gmail's (MIGHTLING_APPS §10).

## 5. Flow

1. **Start.** `POST /api/google/oauth/start` → backend creates `state`, PKCE `code_verifier`, picks a free loopback port `P`, returns the auth URL:
   `https://accounts.google.com/o/oauth2/v2/auth?client_id=…&redirect_uri=http://localhost:P&response_type=code&scope=…&access_type=offline&prompt=select_account%20consent&code_challenge=…&code_challenge_method=S256&state=…`
2. **Consent.** Browser shows the Google picker and a consent screen titled "GNOME". Mightling's UI states beforehand: *"The consent screen will say GNOME — Mightling authenticates through the GNOME desktop's Google integration."*
3. **Redirect.** Google sends the browser to `http://localhost:P/?code=…&state=…`.
   - **Same host:** backend listener on `P` captures the code; tab shows "Connected".
   - **Different host (common):** browser shows "localhost refused to connect". Mightling panel instructs: *"Copy the full URL from the address bar and paste it here."* `POST /api/google/oauth/complete {url}` extracts `code`, validates `state`.
4. **Exchange.** Backend POSTs `code`, `code_verifier`, client id/secret, `redirect_uri` to `https://oauth2.googleapis.com/token`. Stores `refresh_token`, `access_token`, `expires_at`.
5. **Identify.** `GET https://www.googleapis.com/oauth2/v3/userinfo` → `email`. Never trust a user-typed address.
6. **Persist.** One credential row per `(mightling_user_id, google_email)`; upsert on repeat.

Listener on `P` is bound to `127.0.0.1`, accepts one request, times out after 10 min.

## 6. Token lifecycle

- Refresh when `expires_at - now < 60s`; persist the new access token so workers share it.
- Gmail IMAP XOAUTH2: `user=<email>\x01auth=Bearer <token>\x01\x01` via `imaplib.authenticate("XOAUTH2", …)`. Reconnects must fetch a fresh token, never reuse the cached auth string.
- Error mapping:
  - `invalid_grant` → user revoked / password change → mark *this* account "reconnect needed".
  - `invalid_client` / `unauthorized_client` → GOA client rotated or revoked → fleet-wide alert, switch UI to BYO/IMAP fallback.
  - `403 accessNotConfigured` on `gmail.googleapis.com` → Gmail REST API not enabled in GNOME's project → use IMAP path for Gmail.

## 7. Data access

| Service | Transport | Notes |
|---|---|---|
| Gmail | REST API if enabled in GNOME's project (test once at install: `GET /gmail/v1/users/me/messages?maxResults=1`); else IMAP `[Gmail]/All Mail` + `X-GM-RAW` | Same token either way |
| Drive | REST API (`files.list`, `files.get`, `changes.list`) | Certainly enabled — gvfs uses it |
| Docs / Sheets / Slides | `files.export` → `text/markdown`, `text/csv`, `text/plain` | No Docs API scope needed |
| Contacts (optional) | CardDAV `https://www.googleapis.com/carddav/v1/` | |

## 8. Multi-account

- N Google accounts per Mightling user; each a separate credential + connector + sync cursor.
- Documents tagged `source_account=<google_email>` for provenance, filtering and per-account disconnect.
- Same Google account under two Mightling users → two independent credentials; no cross-user dedupe.
- `login_hint=<email>` on reconnect.

## 9. Workspace admin constraints

Tenant "Third-party app access = restricted/blocked" requires the admin to allowlist client "GNOME". Tenant-level Gmail API or IMAP disablement blocks the corresponding transport regardless of auth. Education tenants commonly block both — route to DWD.

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GNOME rotates or Google revokes the client | Low, non-zero (KDE precedent) | All GOA installs lose Google access at once | `invalid_client` detection → alert + automatic fallback UI; BYO client and IMAP app-password paths stay shipped |
| Gmail REST API not enabled in GNOME's project | Unknown until tested | Gmail on IMAP only (no `history.list`, no snippets) | One-line probe at install; IMAP adapter behind the same interface |
| Policy: reusing another app's credentials violates Google OAuth policy | Certain | Revocation trigger if Mightling traffic becomes noticeable in GNOME's project | Credentials not committed; per-install override; disclose on connect screen; keep volume per user modest (scoped queries, no full-archive crawls by default) |
| Consent screen says "GNOME" | Certain | User confusion / phishing suspicion | One-line explanation before the redirect |

## 11. Non-goals

- No Dreamference-owned OAuth client, no CASA.
- No Admin SDK scopes (`admin.directory.*`) on the GOA path; org-wide permission sync is DWD-only.
- No Docs API. No Calendar or Tasks *write endpoints*: the Calendar scope granted is the full one (§4), so read-only is enforced by the service, not the scope.

## 12. Open items

- [ ] Run the Gmail API probe with a GOA token; decide REST vs IMAP for Gmail.
- [x] Confirm `drive.readonly` is accepted as a narrowing of the client's `drive` scope. **Answered no (2026-10-03):** refused ("This app is blocked"); the full `…/auth/drive` and `…/auth/calendar` work (§4). Drive `files.list` worked for My Drive and `corpora=allDrives`; `calendarList` returned 2 calendars.
- [ ] Draft the connect-screen copy and the paste-back UI.