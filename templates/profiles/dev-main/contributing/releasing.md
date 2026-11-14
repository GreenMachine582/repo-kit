## 🚢 Releasing

Release whenever `dev` is worth shipping. There's no fixed cadence.

1. Open a PR from `dev` to `main`, titled `release: …`. Wait for the checks, and merge it with a merge commit.
2. release-please opens or updates **`chore(main): release X.Y.Z`** on `main`, with the version bump and the new
   `CHANGELOG.md` section worked out from the commits. It's opened by the `gth-release-bot` GitHub App, so the
   checks run on it.
3. Review the notes and merge it. That tags `vX.Y.Z`, publishes the GitHub Release with the same notes, and merges
   `main` back into `dev`.

Never bump a version or edit a released `CHANGELOG.md` section by hand, and never move a `v*` tag: consumers pin
exact tags.
