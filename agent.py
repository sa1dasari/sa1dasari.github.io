import os
import json
import re
import sys
import datetime
import subprocess
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────
GITHUB_USERNAME = "sa1dasari"
PORTFOLIO_REPO  = "sa1dasari.github.io"
KNOWN_FILE      = "known_projects.json"   # tracks already-added repos
INDEX_FILE      = "index.html"

GH_TOKEN        = os.environ["GH_TOKEN"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]

GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

ANTHROPIC_HEADERS = {
    "x-api-key": ANTHROPIC_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

# ── Exclusion list ────────────────────────────────────────────────────────────
# Load exclusion list from JSON file
def load_exclude_repos():
    exclude_file = Path("exclude_repos.json")
    if exclude_file.exists():
        return set(json.loads(exclude_file.read_text()).get("excluded_repos", []))
    return set()

EXCLUDE_REPOS = load_exclude_repos()


# ── GitHub helpers ─────────────────────────────────────────────────────────────
def get_repos():
    """Fetch all repos (public only) for the user via the authenticated /user/repos endpoint."""
    repos, page = [], 1
    while True:
        r = requests.get(
            "https://api.github.com/user/repos",
            headers=GH_HEADERS,
            params={"per_page": 100, "page": page, "visibility": "public", "affiliation": "owner"},
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        # Extra safety: skip any private repos that may have slipped through
        repos.extend([repo for repo in batch if not repo.get("private", False)])
        page += 1
    return repos


def get_readme(repo_name):
    """Fetch README content for a repo (returns empty string if none)."""
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/readme",
        headers={**GH_HEADERS, "Accept": "application/vnd.github.raw+json"},
    )
    if r.status_code == 200:
        return r.text[:3000]  # cap at 3k chars to stay within token budget
    return ""


def get_recent_commits(repo_name, n=5):
    """Fetch last N commit messages."""
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/commits",
        headers=GH_HEADERS,
        params={"per_page": n},
    )
    if r.status_code == 200:
        return [c["commit"]["message"].split("\n")[0] for c in r.json()]
    return []


def load_known():
    p = Path(KNOWN_FILE)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_known(known):
    Path(KNOWN_FILE).write_text(json.dumps(known, indent=2))


# ── Claude helpers ─────────────────────────────────────────────────────────────
def claude(prompt, max_tokens=1500):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=ANTHROPIC_HEADERS,
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def assess_repo(repo, readme, commits):
    """Ask Claude to assess repo state and generate portfolio card HTML."""
    prompt = f"""You are helping update a software engineer's portfolio website.

Analyze this GitHub repository and respond ONLY with a JSON object — no markdown, no backticks, no preamble.

Repository info:
- Name: {repo['name']}
- Description: {repo.get('description') or 'none'}
- Topics: {', '.join(repo.get('topics') or [])}
- Stars: {repo.get('stargazers_count', 0)}
- Language: {repo.get('language') or 'unknown'}
- Created: {repo.get('created_at', '')[:10]}
- Last pushed: {repo.get('pushed_at', '')[:10]}
- README (first 3000 chars):
{readme or '(no readme)'}
- Recent commits:
{chr(10).join(f'  - {c}' for c in commits) or '  (none)'}

Return exactly this JSON shape:
{{
  "include": true or false,
  "reason": "one sentence why or why not to include in portfolio",
  "state": "beginning" or "in-progress" or "completed",
  "title": "clean display title (not the raw repo name)",
  "description": "2-3 sentence description written for a portfolio audience — what it does, why it's interesting, what problem it solves. Do NOT start with 'I'. Do NOT use buzzwords.",
  "tags": ["tag1", "tag2", "tag3"],  // 3-5 tech stack tags
  "github_url": "https://github.com/{GITHUB_USERNAME}/{repo['name']}"
}}

Set include=false if: it's a fork, a coursework submission with no real code, a config/dotfiles repo, or clearly abandoned with no meaningful commits.
State:
- beginning = <5 commits or clearly scaffolding only
- in-progress = active work, incomplete features
- completed = stable, usable, meaningful README"""

    raw = claude(prompt)
    # Strip any accidental markdown fences
    raw = re.sub(r"```(?:json)?|```", "", raw).strip()
    return json.loads(raw)


def generate_card_html(assessment, card_num):
    """Generate the project card HTML for the portfolio."""
    # State is tracked internally but not displayed on portfolio

    tags_html = "\n            ".join(
        f'<span class="project-card-tag">{t}</span>'
        for t in assessment["tags"]
    )

    num_str = str(card_num).zfill(2)

    return f"""
      <div class="project-card">
        <a class="project-link" href="{assessment['github_url']}" target="_blank">↗ github</a>
        <div class="project-card-num">{num_str}</div>
        <div class="project-card-title">{assessment['title']}</div>
        <div class="project-card-desc">{assessment['description']}</div>
        <div class="project-card-tags">
            {tags_html}
        </div>
      </div>"""


# ── Portfolio HTML patcher ─────────────────────────────────────────────────────
def patch_index(new_cards_html, new_state_css_needed):
    """
    Insert new project cards into index.html before the closing </div>
    of the projects-grid, and inject state-badge CSS if not already present.
    """
    html = Path(INDEX_FILE).read_text()


    # Find the end of the projects-grid div and insert before it
    # Look for the closing pattern after the last project card
    marker = "    </div>\n  </div>\n  <footer>\n    <p>sa1dasari.github.io</p>\n    <a class=\"btn btn-ghost\" onclick=\"showPage('education')\""

    if marker not in html:
        # Fallback: find projects page footer
        marker = "    </div>\n  </div>\n  <footer>\n    <p>sa1dasari.github.io</p>\n    <a class=\"btn btn-ghost\" onclick=\"showPage('education')\""

    insert_point = html.find(marker)
    if insert_point == -1:
        # Last resort: find projects-grid closing
        insert_point = html.rfind("    </div>\n  </div>\n  <footer>")

    if insert_point == -1:
        print("ERROR: Could not find insertion point in index.html")
        sys.exit(1)

    patched = html[:insert_point] + new_cards_html + "\n" + html[insert_point:]
    Path(INDEX_FILE).write_text(patched)
    print(f"✓ Patched index.html with {len(new_cards_html)} chars of new content")


def get_next_card_num(html_content):
    """Find the highest existing project card number and return next."""
    nums = re.findall(r'class="project-card-num">(\d+)<', html_content)
    if not nums:
        return 10  # start after existing manually-added cards
    return max(int(n) for n in nums) + 1


# ── Git / PR helpers ───────────────────────────────────────────────────────────
def run(cmd, **kwargs):
    print(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")
    return result.stdout.strip()


def create_pr(branch, added_titles):
    today = datetime.date.today().isoformat()
    title = f"[portfolio-agent] Add {len(added_titles)} new project(s) — {today}"
    body = (
            "## Portfolio auto-update\n\n"
            "The portfolio agent found new GitHub repos and added them to the projects page.\n\n"
            "### Added projects\n"
            + "\n".join(f"- **{t}**" for t in added_titles)
            + "\n\n### Review checklist\n"
              "- [ ] Descriptions are accurate\n"
              "- [ ] Tags are appropriate\n"
              "- [ ] Card order makes sense\n\n"
              "_Merge to publish to GitHub Pages._"
    )

    r = requests.post(
        f"https://api.github.com/repos/{GITHUB_USERNAME}/{PORTFOLIO_REPO}/pulls",
        headers=GH_HEADERS,
        json={
            "title": title,
            "body": body,
            "head": branch,
            "base": "main",
        },
    )
    r.raise_for_status()
    pr = r.json()
    print(f"✓ PR created: {pr['html_url']}")
    return pr["html_url"]


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("=== Portfolio agent starting ===")

    # Load previously seen repos
    known = load_known()
    print(f"Known repos: {list(known.keys())}")

    # Fetch current repos
    repos = get_repos()
    print(f"Found {len(repos)} public repos")

    # Filter to new / updated repos worth checking
    candidates = [
        r for r in repos
        if r["name"] not in EXCLUDE_REPOS          # not in manual exclusion list
           and r["name"] not in known                  # not already processed
           and not r.get("fork", False)                # not a fork
           and not r.get("private", False)             # not private (double-check)
    ]
    print(f"New candidates: {[r['name'] for r in candidates]}")

    if not candidates:
        print("No new repos found. Nothing to do.")
        return

    # Assess each candidate with Claude
    to_add = []
    for repo in candidates:
        print(f"\nAssessing: {repo['name']}")
        readme  = get_readme(repo["name"])
        commits = get_recent_commits(repo["name"])
        try:
            assessment = assess_repo(repo, readme, commits)
            print(f"  → include={assessment['include']}, state={assessment.get('state')}, title={assessment.get('title')}")
            # Always mark as known so we don't re-check next run
            known[repo["name"]] = {
                "included": assessment["include"],
                "state": assessment.get("state"),
                "title": assessment.get("title"),
                "checked_at": datetime.date.today().isoformat(),
            }
            if assessment["include"]:
                to_add.append(assessment)
        except Exception as e:
            print(f"  ERROR assessing {repo['name']}: {e}")
            continue

    if not to_add:
        print("\nNo repos worth adding to portfolio.")
        save_known(known)
        return

    # Read current index.html to find next card number
    current_html = Path(INDEX_FILE).read_text()
    next_num = get_next_card_num(current_html)

    # Generate HTML for new cards
    new_cards_html = ""
    for i, assessment in enumerate(to_add):
        new_cards_html += generate_card_html(assessment, next_num + i)

    # Patch index.html
    patch_index(new_cards_html, new_state_css_needed=True)

    # Save updated known_projects.json
    save_known(known)

    # Git: create branch, commit, push, open PR
    today = datetime.date.today().isoformat()
    branch = f"portfolio-agent/{today}"

    run(f'git config user.name "portfolio-agent[bot]"')
    run(f'git config user.email "portfolio-agent@users.noreply.github.com"')
    run(f"git checkout -b {branch}")
    run(f"git add {INDEX_FILE} {KNOWN_FILE}")

    titles = [a["title"] for a in to_add]
    commit_msg = f"[portfolio-agent] Add {', '.join(titles)}"
    run(f'git commit -m "{commit_msg}"')
    run(f"git push origin {branch}")

    pr_url = create_pr(branch, titles)
    print(f"\n=== Done. PR ready for review: {pr_url} ===")


if __name__ == "__main__":
    main()