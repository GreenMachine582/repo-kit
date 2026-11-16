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

## Credentials: sops + age

Both Apps' Client IDs and private keys live in one [sops](https://github.com/getsops/sops) file, encrypted with
[age](https://github.com/FiloSottile/age). Three files are involved, and only one of them is shared:

| File | Where | Holds | Shared |
|---|---|---|---|
| The credentials file | OneDrive, `~/OneDrive/repo-kit/secrets.sops.json` by default | Both Apps' Client IDs and keys, encrypted | Yes: OneDrive carries it between machines |
| The age key | `%APPDATA%\sops\age\keys.txt` on Windows, `~/.config/sops/age/keys.txt` on Linux | What decrypts the credentials file on this machine | Never: rk refuses one inside a synced folder |
| The user config | `~/.config/repo-kit/config.toml` | `secrets_file`: where this machine finds the credentials file | Never: one per machine ([configuration.md](configuration.md#user-config-configrepo-kitconfigtoml)) |

`rk secrets init` sets up all three, so nothing needs editing by hand. It writes `secrets_file` from `~`, so the path
is the same on every machine even when the user names differ.

rk calls the `sops` (3.10 or newer) and `age-keygen` binaries; nothing is added to rk's Python dependencies. Keys go
to sops on stdin, never as an argument, and no command prints one.

| Command | Does |
|---|---|
| `rk secrets init [--file PATH]` | Creates this machine's age key if it has none (where sops looks: `SOPS_AGE_KEY_FILE`, else the default above) and prints its public key. Adds `secrets_file` to the user config if it's missing, creating the config if needed and backing up an existing one to `config.toml.bak`; `--file` picks another place than the OneDrive default. Creates the credentials file if no machine has yet. If it exists but this machine can't decrypt it, prints the `add-recipient` command to run on one that can. Safe to re-run |
| `rk secrets set-app {pr-bot,release-bot} --client-id … --pem …` | Stores one App's Client ID and key, then reads them back to check |
| `rk secrets add-recipient age1…` | Lets another machine's key decrypt the file (`sops rotate --add-age`) |
| `rk secrets show` | Each App's Client ID, and whether its key is present. Exits 1 if one is missing |

### First machine

1. Install the tools. On Windows: `winget install SecretsOPerationS.SOPS`, then `winget install FiloSottile.age`,
   and open a new terminal so both are on `PATH`.
2. Run `rk secrets init`. It creates the age key, the user config entry and the credentials file in OneDrive.
3. Store each App with `rk secrets set-app` ([below](#creating-gth-pr-bot-once-per-account)).

### Another machine

1. Wait until OneDrive has synced the credentials file to this machine. Otherwise `init` makes a second one.
2. Install the tools and run `rk secrets init`. It finds the file, sees it can't decrypt it yet, and prints the
   `rk secrets add-recipient age1…` command to run.
3. Run that command on a machine that can already decrypt the file.
4. Once the file has synced back, `rk secrets show` here lists both Apps.

### Moving from `[pr_bot]`

Earlier releases read `gth-pr-bot`'s key from a `.pem` path in `[pr_bot]`. That table is now an error, and every
command refuses to run while it's there. Note its `client_id` and `private_key`, delete the table, then run
`rk secrets init` and `rk secrets set-app pr-bot --client-id <client_id> --pem <private_key>`, and the same for
`release-bot`. Delete the `.pem` files once `rk secrets show` lists both Apps.

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
5. Store both, then delete the download:

   ```powershell
   rk secrets set-app pr-bot --client-id "Iv23..." --pem ~/Downloads/gth-pr-bot.<date>.private-key.pem
   ```

`gth-release-bot` already exists; store its Client ID and a key the same way, with `release-bot`.

## Enrolling a repo

1. Install both Apps on the repo: Settings → Applications → each App → Configure → **Only select repositories**,
   and add it. rk can't check this from your `gh` login, so it prints the link instead. `gth-release-bot` must be
   installed before `rk github apply`: the generated rulesets name it as a bypass actor, and GitHub accepts only an
   App that is installed on the repo. Without it, `apply` fails with `Validation Failed (HTTP 422)`.
2. In the repo, run `rk configure`. It's safe to re-run, and `--dry-run` shows what it would do. It decrypts the
   credentials file first, so nothing is written on a machine that can't. Then it:
   - creates the labels update-prs uses (`needs-merge`, `wip`, `no-auto-update`, or what `[update_prs]` names),
     leaving existing ones alone;
   - sets `GTH_PR_BOT_CLIENT_ID` and `GTH_RELEASE_BOT_CLIENT_ID` where missing or different;
   - sets `GTH_PR_BOT_PRIVATE_KEY` and `GTH_RELEASE_BOT_PRIVATE_KEY` where missing. Each key goes to
     `gh secret set` on stdin, never as an argument. `--replace-key` overwrites both after a key rotation.
3. Run the repo's update-prs caller from the Actions tab with dry-run ticked. A run that lists the open PRs proves the
   key, Client ID and installation all work.

`rk github apply` (rulesets and settings) is not part of `configure` until Phase 1D.

## Releases: `gth-release-bot`

The release caller reads the variable `GTH_RELEASE_BOT_CLIENT_ID` and the secret `GTH_RELEASE_BOT_PRIVATE_KEY`, which
`rk configure` sets ([above](#enrolling-a-repo)).

GTH's own workflows used `RELEASE_APP_CLIENT_ID` (or `RELEASE_APP_ID`) and `RELEASE_APP_PRIVATE_KEY`. Delete those
once the managed `release.yml` replaces `release-please.yml`. Without the new names, a push to `main` fails the
release run with a pointer here, rather than skipping it quietly.

### Why one key, kept to selected repos

Personal accounts can't share Actions secrets, so every enrolled repo holds a copy of the same key. Anyone who can read
one of those secrets can mint a token for every repo the App is installed on. So install the App only on repos that use
it, and rotate the key if a repo's secrets might have leaked: generate a new one, `rk secrets set-app` it,
`rk configure --replace-key` in each repo, then delete the old key in the App settings.

### Workflows permission, left out on purpose

GitHub may refuse an update when the base changes files under `.github/workflows/`, unless the App has Workflows:
write. update-prs reports such a refusal as a failed run. If it happens, grant Workflows: write in the App settings and
add `permission-workflows: write` to the token step in `update-prs.yml`. It isn't granted up front because it would let
a leaked key rewrite workflows.
