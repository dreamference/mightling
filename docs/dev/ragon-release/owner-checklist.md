# Ragon rename: manual steps at release time

The steps no script can do (spec `specs/DREAMFERENCE_RENAME_RAGON.md` §9.4, decisions 3, 4, 7, 25). **None of
them has been done.** Each is a GitHub, registry, DNS or platform setting. Order matters where marked.
Day numbers are the spec's §11: day 1 is the day the script runs on a fresh `main`.

## Before day 1

- [ ] Optional, free: search "RAGON" in classes 9 and 42 in USPTO Trademark Search and EUIPO eSearch (§10, open question 4). Not required.
- [ ] Check that `mightling.dreamference.ai` resolves (the DNS finding in §10). If it never did, the redirect in step D3 protects only links in old documents and can be skipped.
- [ ] Confirm the GHCR package `ragon-serve` can be created under the organisation (a package cannot be renamed; it is a new publish).

## Day 2: repositories (after the rename commit is merged and tested)

1. [ ] Rename `dreamference/mightling` to `dreamference/ragon` (Settings, General). GitHub redirects the web, git and API addresses.
2. [ ] Rename `dreamference/ling-engine` to `dreamference/ragon-engine`. Update the submodule URL and path in the main repository the same day.
3. [ ] Repository descriptions, both repositories: say Ragon (and keep the plain statement of what it is).
4. [ ] Topics, both repositories: add `ragon`, remove `mightling` and `ling`.
5. [ ] Social preview image of both repositories: the RAGON wordmark card.
6. [ ] Organisation profile README (`dreamference/.github`, `profile/README.md`): Ragon, the puffin line, the new links.
7. [ ] Organisation profile: the pinned repositories, the description and the website field (`https://ragon.dreamference.ai`).
8. [ ] Release names: title 1.9.0 and later "Ragon 1.x.y". Past titles stay (open question 1, default). Old release bodies stay as written.
9. [ ] Publish the `ragon-serve` image to GHCR at the pinned digest. The `ling-serve` package stays until 2.0 (1.8.x installs pull it by digest).
10. [ ] Tag 1.9.0 and let the release workflow publish the three asset sets (`ragon-*`, `ling-*`, `puffin-*`) under the kept signature namespace.
11. [ ] Settings, Actions, Environments: confirm the release-signing environment and its secret still apply to the renamed repository.
12. [ ] Settings, Pages: set the custom domain to `ragon.dreamference.ai` and enable HTTPS once the certificate is issued.

## Day 2 to 3: DNS (Cloudflare, zone `dreamference.ai`)

- [ ] D1. Add the record `ragon` (CNAME to the Pages host of the organisation).
- [ ] D2. Wait for Pages to issue the certificate for `ragon.dreamference.ai`.
- [ ] D3. On `mightling.dreamference.ai` (a proxied record must exist for the rule to answer): a Redirect Rule, permanent (301), keeping path and query, to `https://ragon.dreamference.ai`. Pages serves one custom domain, so this is a Cloudflare rule, not a Pages setting.
- [ ] D4. Keep the old record for as long as old links matter; review at 2.0.

## Day 3: messengers

- [ ] Telegram: the bridge calls `setMyName` and `setChatMenuButton` itself at its next start. In BotFather, check the bot's display name reads "Ragon" and the About text and description are updated. **Do not change the username** (decision 7); nothing is re-paired.
- [ ] Telegram: BotFather `/setdescription`, `/setabouttext`, `/setuserpic` if the picture carries the old wordmark.
- [ ] Matrix: the DM room name changes through one state event made by the node; check it in the client.
- [ ] Signal: the device name is renamed where signal-cli allows; otherwise leave it (decision 18). Do not re-scan.

## Day 3 to 4: machines and platforms

- [ ] Install day by day, no Night Shift that evening (decision 12): the main node, then the others.
- [ ] Windows test machine: update, check the Start menu and desktop shortcuts, re-pin, notification prompt.
- [ ] Mac: update, check the permission prompts, the Dock, the old app removed.
- [ ] Linux `.deb`: the old package replaced, the launcher entry, the AppArmor profile `ragon`.
- [ ] Claude Code plugin: reinstall once with `/plugin install ragon@ragon`.

## Website, docs and paper (day 2 to 3, with the drop-in text in this folder)

- [ ] Website: apply `website-snippet.html`, the renamed logos and `website/CNAME`; check `/docs/` builds under the new name.
- [ ] README: apply `readme-story.md`.
- [ ] Paper: apply `paper-titlepage.tex`, rebuild, copy the PDF into `website/`.
- [ ] Release notes: paste `release-notes-section.md` into `specs/DREAMFERENCE_RELEASE_1.9.0.md` before tagging.

## Not on this list (nothing to do)

The SSHSIG namespaces, the lock files, existing NetBird peer names and the relay box stay as they are (spec §3). The company, the `dreamference` organisation, the Python package and `DREAMFERENCE_*` variables do not change. No npm, PyPI or crates.io publishing is involved.
