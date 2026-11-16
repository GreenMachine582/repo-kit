[← Back to README](../README.md)

# 🤖 GitHub Apps

Two Apps, kept apart so PR housekeeping never holds release powers.

| App | Permissions | Does |
|---|---|---|
| `gth-pr-bot` (new) | Contents RW, Pull requests RW, Issues RW, Metadata R | Pushes merge commits to feature branches; adds labels and comments |
| `gth-release-bot` (existing) | Contents RW, Pull requests RW | Opens release-please PRs, creates `v*` tags, back-merges `main` into `dev` |

Pushes and PRs made with an App token trigger CI. GitHub doesn't run CI on PRs or pushes made with `GITHUB_TOKEN`.

## Rules

- `gth-pr-bot` is on no bypass list. `gth-release-bot` bypasses the `dev` ruleset only, to push the back-merge of
  `main` after a release ([checks-and-release.md](checks-and-release.md#releaseyml)). Neither App bypasses `main`.
- Apart from that back-merge, required checks are never bypassed by either App.
- `gth-release-bot` is the only actor allowed to create `v*` tags (the tags ruleset).
- Neither App ever merges a PR.

## Creating `gth-pr-bot` (once per account)

A User-only step, done in the GitHub UI:

1. GitHub → Settings → Developer settings → GitHub Apps → **New GitHub App**.
   - Name: `gth-pr-bot`. Names are global; if it's taken, any name works, since rk uses only the Client ID.
   - Homepage URL: the repo-kit repo.
   - Webhook: untick **Active**.
2. Repository permissions: **Contents**, **Pull requests** and **Issues** set to Read and write. Metadata becomes
   Read-only automatically. Leave everything else, including Workflows, at No access.
3. Where can this App be installed: **Only on this account**. Create it.
4. Copy the **Client ID**. Under **Private keys**, generate a key; a `.pem` downloads.
5. Move the `.pem` to a local folder outside OneDrive, such as `~/.ssh/gth-pr-bot.pem`, and record both in the
   per-machine user config ([configuration.md](configuration.md#user-config-configrepo-kitconfigtoml)):

   ```toml
   [pr_bot]
   client_id = "Iv23..."
   private_key = "~/.ssh/gth-pr-bot.pem"
   ```

## Enrolling a repo

1. Install the App on the repo: Settings → Applications → `gth-pr-bot` → Configure → **Only select repositories**,
   and add it. rk can't check this from your `gh` login, so it prints the link instead.
2. In the repo, run `rk configure`. It's safe to re-run, and `--dry-run` shows what it would do. It:
   - creates the labels update-prs uses (`needs-merge`, `wip`, `no-auto-update`, or what `[update_prs]` names),
     leaving existing ones alone;
   - sets the `GTH_PR_BOT_CLIENT_ID` variable if it's missing or different;
   - sets the `GTH_PR_BOT_PRIVATE_KEY` secret if it's missing. The key goes to `gh secret set` on stdin, never as an
     argument. `--replace-key` overwrites it after a key rotation.
3. Run the repo's update-prs caller from the Actions tab with dry-run ticked. A run that lists the open PRs proves the
   key, Client ID and installation all work.

`rk github apply` (rulesets and settings) is not part of `configure` until Phase 1D.

## Releases: `gth-release-bot`

Install the App on the repo first (Settings → Applications → `gth-release-bot` → Configure). The generated rulesets
name it as a bypass actor, and GitHub accepts only an App that is installed on the repo: without it,
`rk github apply` fails with `Validation Failed (HTTP 422)`.

The release caller reads the variable `GTH_RELEASE_BOT_CLIENT_ID` and the secret `GTH_RELEASE_BOT_PRIVATE_KEY`.
Until `rk configure` covers this App (a Future item), set them by hand in each repo that has it installed. In
PowerShell:

```powershell
gh variable set GTH_RELEASE_BOT_CLIENT_ID --body "Iv23..."
Get-Content ~/.ssh/gth-release-bot.pem -Raw | gh secret set GTH_RELEASE_BOT_PRIVATE_KEY
```

In a POSIX shell, the secret is `gh secret set GTH_RELEASE_BOT_PRIVATE_KEY < ~/.ssh/gth-release-bot.pem`.

GTH's own workflows used `RELEASE_APP_CLIENT_ID` (or `RELEASE_APP_ID`) and `RELEASE_APP_PRIVATE_KEY`. Delete those
once the managed `release.yml` replaces `release-please.yml`. Without the new names, a push to `main` fails the
release run with a pointer here, rather than skipping it quietly.

### Why one key, kept to selected repos

Personal accounts can't share Actions secrets, so every enrolled repo holds a copy of the same key. Anyone who can read
one of those secrets can mint a token for every repo the App is installed on. So install the App only on repos that use
it, and rotate the key (generate a new one, `rk configure --replace-key` in each repo, then delete the old key in the
App settings) if a repo's secrets might have leaked.

### Workflows permission, left out on purpose

GitHub may refuse an update when the base changes files under `.github/workflows/`, unless the App has Workflows:
write. update-prs reports such a refusal as a failed run. If it happens, grant Workflows: write in the App settings and
add `permission-workflows: write` to the token step in `update-prs.yml`. It isn't granted up front because it would let
a leaked key rewrite workflows.
