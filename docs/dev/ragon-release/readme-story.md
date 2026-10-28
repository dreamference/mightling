Drop-in for `README.md` at release time. The header block replaces the first eight lines (logo block
through the heading); the story goes right under the badges row, before `## Install`. The puffin logo
files are kept: only the wordmark inside them changes (`images/ragon-logo-*.svg`).

---

```markdown
<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/ragon-logo-dark-outlined.svg">
  <img src="images/ragon-logo-light-outlined.svg" alt="Ragon" width="320">
</picture>

## Private AI on your DGX Spark. One command.

*Ragon is a puffin that thinks it is a dragon.*
```

Under the table of numbers, before `## Install`:

```markdown
## Mightling is now Ragon

Mightling was renamed **Ragon** in 1.9.0. Same puffin, same private AI on your own GB10; a name that
fits its ambitions. The command is `ragon`. Moving from Mightling takes one `ling update` and one run
of `ragon`; the [release notes](https://github.com/dreamference/ragon/releases/tag/v1.9.0) list what
to do.
```

Then replace the product word and links as the script does: `mightling` in URLs becomes `ragon`, the
install line reads `https://github.com/dreamference/ragon/releases/latest/download/install.sh`, and
the example `cd ~/your-project && ling` becomes `cd ~/your-project && ragon`.
