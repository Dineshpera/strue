# Strue

**Proof of what works today.** Developer setup recipes, run from an empty folder on Linux,
macOS and Windows every day. Each result is signed by the machine that produced
it, so nobody has to take our word for it.

AI coding agents write install and setup commands from memory, and that memory
is months old. Strue is the record they can check instead: what ran today, with
which versions, and which old commands are confirmed broken.

## How it works

1. A **recipe** is one YAML file in `recipes/`: the files to create, the commands
   to run, and a check that must pass.
2. Every day a GitHub Actions workflow runs every recipe from an empty folder on
   three operating systems.
3. A second job signs the three result files (GitHub artifact attestations,
   backed by a public transparency log), commits them to `results/<date>/`, and
   rebuilds the status page, badges and JSON under `docs/`.
4. Agents read the JSON, or use the MCP server in `mcp-server/`.

The job that runs third-party install commands has no write access and no
signing rights. Only the second job, which runs this repository's own code, can
sign and commit.

## Put it live

1. Create a **public** GitHub repository named `strue`. Public matters: runners
   are free and signatures go to the public log.
2. Push these files to the `main` branch.
3. In the repository, open Settings → Pages and deploy from branch `main`,
   folder `/docs`.
4. Open the Actions tab, choose "Daily inspection" and run it once by hand.
5. When it finishes, your status page is at `https://<you>.github.io/strue/`.

After that it runs daily at 02:17 UTC (07:47 IST) with no machine of yours
switched on.

## Add a recipe

Create `recipes/<id>.yaml`. The file name must equal the `id`.

```yaml
id: express5-hello
title: Express 5 server with a JSON route and a wildcard route
intent: Create an Express server with a JSON endpoint and a catch-all path segment.
source: https://expressjs.com/en/guide/migrating-5.html   # official docs only
runtime: node            # node or python (python gets a fresh virtualenv)
tags: [node, express]
os: [ubuntu-latest, macos-latest, windows-latest]   # optional, this is the default
timeout: 600             # optional, seconds per command
files:                   # optional, written before the steps run
  app.mjs: |
    ...
steps:                   # run in order with bash, from an empty folder
  - npm init -y
  - npm install express
  - run: npm run build   # a step can also set a sub-folder
    cwd: app
check:                   # must exit 0 and print the expected text
  run: node app.mjs
  expect: strue-ok
track: [express]         # packages whose installed versions get recorded
avoid:                   # optional: outdated commands that must FAIL
  - run: node stale.mjs
    why: Express 5 rejects a bare "*" path; wildcards need a name.
```

Rules for a good recipe:

- Follow the official documentation, and link it in `source`.
- Install the latest version. Pinning defeats the purpose.
- Need no account, API key or paid service.
- Make the check prove behaviour, not just that an install finished.
- Add an `avoid` entry only for a command you have seen fail.

Try it before you push:

```bash
pip install pyyaml
python runner/run.py --only express5-hello --strict
```

## Verify a result yourself

```bash
gh attestation verify results/2026-11-14/ubuntu-latest.json --repo <you>/strue
```

This confirms the file was produced by this repository's workflow and has not
been edited since.

## Show the badge

```markdown
[![strue](https://<you>.github.io/strue/badges/fastapi-hello.svg)](https://<you>.github.io/strue/)
```

The badge carries the inspection date, so a stalled inspection cannot keep
claiming a pass.

## Use it from an agent

```bash
pip install "mcp>=2"
STRUE_RECORD=https://<you>.github.io/strue/api/latest.json python mcp-server/server.py
```

Tools: `search_recipes`, `get_recipe`, `failing_today`. Without the MCP server,
any agent can fetch `api/latest.json` or `api/recipes/<id>.json` directly.

## The rules that make this an inspector

- A recipe is **verified** only when its check passes on every listed system.
- Nobody can pay for a pass, a delay, or a removed failure.
- A failure stays in the history. The record is append-only by convention.
- Every tick links to the run that produced it.

## Layout

```
recipes/              one YAML file per inspection
runner/run.py         runs recipes, writes one result file
runner/build_site.py  results -> docs/ (page, badges, JSON)
results/<date>/       the signed daily record, one file per system
docs/                 GitHub Pages site and JSON API (generated)
mcp-server/server.py  MCP server over the JSON API
.github/workflows/    the daily inspection
```

MIT licensed.
