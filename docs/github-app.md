[← Back to README](../README.md)

# 🤖 GitHub Apps

Two Apps, kept apart so PR housekeeping never holds release powers.

| App | Permissions | Does |
|---|---|---|
| `gth-pr-bot` (new) | Contents RW, Pull requests RW, Issues RW, Metadata R | Pushes merge commits to feature branches; adds labels and comments |
| `gth-release-bot` (existing) | Contents RW, Pull requests RW | Opens release-please PRs, creates `v*` tags, back-merges `main` into `dev` |

Pushes and PRs made with an App token trigger CI. GitHub doesn't run CI on PRs or pushes made with `GITHUB_TOKEN`.

## Rules

- Neither App is on a bypass list for `dev` or `main`.
- Required checks are never bypassed by either App.
- `gth-release-bot` is the only actor allowed to create `v*` tags (the tags ruleset).
- Neither App ever merges a PR.

## Setting up a repo

Credentials are per repo: personal accounts can't share Actions secrets between repos. `rk configure` does this:

1. Checks both Apps are installed on the repo.
2. Asks for each App's Client ID and private key, validates them by minting a token, then stores them as repo
   variables and secrets.
3. Creates the labels (`needs-merge`, `wip`, `no-auto-update`).
4. Runs `rk github apply` (see [rulesets.md](rulesets.md)).

To get the values by hand: GitHub → Settings → Developer settings → GitHub Apps → the App. Copy its Client ID. Under
**Private keys**, generate a key (existing keys keep working), hand it to `rk configure`, then delete the downloaded
`.pem`.

## Creating `gth-pr-bot` (once)

A User-only step. Create a GitHub App owned by the account with the permissions above, no webhook, installable only on
this account. Install it on each adopting repo.
