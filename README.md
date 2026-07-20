# sa1dasari.github.io

This repo contains both my personal portfolio website and an automated portfolio agent.

## Portfolio Agent

Automatically scans your GitHub repos every 2 weeks, uses Claude to assess new projects, and opens a PR to add them to your portfolio's projects page.

## How it works

1. **Cron trigger** — GitHub Actions runs on the 1st and 15th of every month
2. **Repo scan** — fetches all public repos via GitHub API
3. **Diff** — compares against `known_projects.json` to find new repos
4. **Claude assessment** — for each new repo, Claude reads the README + recent commits and decides:
   - Should it be included in the portfolio?
   - What's the state? (`🌱 early` / `⚡ in progress` / `✓ completed`)
   - What's a good title and 2-3 sentence description?
   - What are the right tech tags?
5. **HTML patch** — new project cards are injected into `index.html`
6. **PR** — changes are committed to a `portfolio-agent/YYYY-MM-DD` branch and a PR is opened for review

After review, tweak if needed, and merge. GitHub Pages redeploys automatically.

## State badges

The agent adds a small badge next to each auto-added project title:

- `🌱 early` — fewer than 5 commits or scaffolding only
- `⚡ in progress` — active work, incomplete features  
- `✓ completed` — stable, usable, meaningful README

These are styled via CSS auto-injected into `index.html` on first run.
