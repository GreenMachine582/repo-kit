# Changelog

## [0.4.0](https://github.com/GreenMachine582/repo-kit/compare/v0.3.0...v0.4.0) (2026-11-17)


### Features

* pin generated callers by commit as well as tag ([87a4c18](https://github.com/GreenMachine582/repo-kit/commit/87a4c182fb84efb657293f312bf7aa02a26de03f))
* require signed commits on dev and main ([7d383a9](https://github.com/GreenMachine582/repo-kit/commit/7d383a924877e9f3d03540430e98d74a25530cf9))


### Bug Fixes

* let wt done remove a branch with no commits, and point wt sync at setup ([ede99fd](https://github.com/GreenMachine582/repo-kit/commit/ede99fdba78ff9a238856a4b35cc8f4209863ca1))
* refuse rk github in a checkout without .repo-kit.toml ([4410069](https://github.com/GreenMachine582/repo-kit/commit/441006975aef53964b7d76fc57a10a33f39ab29c))

## [0.3.0](https://github.com/GreenMachine582/repo-kit/compare/v0.2.0...v0.3.0) (2026-11-17)


### Features

* add [project].extra_files for release-please extra-files ([8835c66](https://github.com/GreenMachine582/repo-kit/commit/8835c66a30794af20a170c6f447066307fb3db0b))
* add rk fleet for doctor, sync --check, configure and upgrade ([f18b82e](https://github.com/GreenMachine582/repo-kit/commit/f18b82e6ec0f83d0040b288c5970ec42135a7af3))
* add rk secrets rotate ([a737cb2](https://github.com/GreenMachine582/repo-kit/commit/a737cb26bcda1d25bc238cb6059302aecd8e3ecc))
* apply removed and renamed templates in rk sync and upgrade ([64dd778](https://github.com/GreenMachine582/repo-kit/commit/64dd778c03b1ec01bfc18bb8ed8857c905bc5d25))
* **doctor:** add --json output ([2bd315b](https://github.com/GreenMachine582/repo-kit/commit/2bd315b32744c745922ee7dda804b38a178e612f))
* **doctor:** add schema_version, stable check ids and error JSON ([17ffda3](https://github.com/GreenMachine582/repo-kit/commit/17ffda31fed06685d975b567a499e7ea4fd3582c))
* **doctor:** warn when a stored App key is old ([6177349](https://github.com/GreenMachine582/repo-kit/commit/6177349765947bb2164e0af93c26abdf821a9cc8))
* list and remove secrets recipients, and validate the App's .pem ([942c6d3](https://github.com/GreenMachine582/repo-kit/commit/942c6d36edaafa3b4fb6bd62a5762fa350c3f8eb))


### Bug Fixes

* compare the recorded mode as well as the hash in the lock ([6e85768](https://github.com/GreenMachine582/repo-kit/commit/6e8576890e9f75ba62caacb4ef5a098cbcb0d8d5))
* name the file and the way out when a removed template's file was edited ([c876f4a](https://github.com/GreenMachine582/repo-kit/commit/c876f4a6262d7c7868b9bf72740af6e8a41e5dda))

## [0.2.0](https://github.com/GreenMachine582/repo-kit/compare/v0.1.0...v0.2.0) (2026-11-17)


### ⚠ BREAKING CHANGES

* rk needs Python 3.12 or newer.

### Features

* add rk wt list to show each worktree's PR and unsaved work ([dcbd6d8](https://github.com/GreenMachine582/repo-kit/commit/dcbd6d8ca1250a8d214b228ab676f8f8988aecbc))
* record App key fingerprints and warn about conflict copies ([6e69494](https://github.com/GreenMachine582/repo-kit/commit/6e69494321fbc604f63aa5506b9edccc5c33d654))


### Bug Fixes

* keep the App keys in branch-locked environments ([12e2649](https://github.com/GreenMachine582/repo-kit/commit/12e264957443113bdcf8b0c4427ab367efef7993))
* reconcile PRs by number, clear resolved conflicts, respect deleted seeded files ([0202314](https://github.com/GreenMachine582/repo-kit/commit/0202314fe46e6cb798b104081e58e19b981e79bb))
* spell out the [pr_bot] migration and how to install sops and age ([fdfddc4](https://github.com/GreenMachine582/repo-kit/commit/fdfddc4a0d28c289617b7444632a318b833ce700))
* **worktree:** stop wt done and wt sync losing or resurrecting work ([49da404](https://github.com/GreenMachine582/repo-kit/commit/49da404b308c1e330628e16aae29f287144a0404))


### Build

* require Python 3.12 ([9190e73](https://github.com/GreenMachine582/repo-kit/commit/9190e73f742290151fb19cfd519d615391a78460))

## 0.1.0 (2026-11-16)


### ⚠ BREAKING CHANGES

* the user config's `[pr_bot]` table is gone; a leftover one is an error that names the `rk secrets` migration.

### Features

* add git and GitHub wrappers with a fake PR store ([1174e58](https://github.com/GreenMachine582/repo-kit/commit/1174e584224fe9e10521a47c0fe01a2a51fcb900))
* add rk branches prune ([b1ba64a](https://github.com/GreenMachine582/repo-kit/commit/b1ba64acd18cc71cfdbf224d74b97484b49bf80d))
* add rk configure to create the labels and store the PR bot credentials ([3ceb89f](https://github.com/GreenMachine582/repo-kit/commit/3ceb89f9ad5c9cbb76a4e5f92a329bed98815837))
* add rk doctor and run it at the end of rk bootstrap ([2a60ddc](https://github.com/GreenMachine582/repo-kit/commit/2a60ddc33a1c7e4a02d72ee14252f22272802bcf))
* add rk github apply ([f8a6038](https://github.com/GreenMachine582/repo-kit/commit/f8a60388a0492787b801f32276747a79390e803b))
* add rk github diff for rulesets ([04a9e54](https://github.com/GreenMachine582/repo-kit/commit/04a9e54c1bae2d48277c1c432d347ea36c26f75f))
* add rk install and rk bootstrap to adopt a repo ([37bad9a](https://github.com/GreenMachine582/repo-kit/commit/37bad9ad8b36ed492f34f16087caffbd096301b3))
* add rk sync and the shared-files engine ([6ea7525](https://github.com/GreenMachine582/repo-kit/commit/6ea752560ae2ac381babcf2b85eb6f1711ccbffc))
* add rk update-prs to keep open PRs current with their base ([6dfc22c](https://github.com/GreenMachine582/repo-kit/commit/6dfc22cdfc096305b7e8bcece07371f1dbb9f241))
* add rk upgrade for templates added in a release ([740a21d](https://github.com/GreenMachine582/repo-kit/commit/740a21d5ed20ceb4e2a556c01888022f89062832))
* add rk wt start, sync and done ([41cb5a3](https://github.com/GreenMachine582/repo-kit/commit/41cb5a39b7f945d20f705a133f691b833697b729))
* add the commit and PR title rules ([b35a448](https://github.com/GreenMachine582/repo-kit/commit/b35a448d4bdc8c02e650f1b87ff68df902f2fc14))
* add the CONTRIBUTING blocks to the dev-main profile ([7010882](https://github.com/GreenMachine582/repo-kit/commit/70108821057b18b29f60021ddeaf9b61954e79a3))
* add the PR listing, status, update and comment calls for update-prs ([7b52a8f](https://github.com/GreenMachine582/repo-kit/commit/7b52a8f8ac3aa18b8f781e79fb7091d7f10deb1a))
* add the pr-checks caller to every repo ([ee5bbed](https://github.com/GreenMachine582/repo-kit/commit/ee5bbed9005e4aa3cdcc8993260100ea2d91be00))
* add the release caller to dev-main repos ([d8b3c66](https://github.com/GreenMachine582/repo-kit/commit/d8b3c669b2c7752ebf4575b6fd936a9d8c9a4c82))
* build and diff the repo settings ([177eed0](https://github.com/GreenMachine582/repo-kit/commit/177eed09e0b94cf3a0d83d943ab7ad3272c182a6))
* build the dev-main rulesets in code and diff them ([c134e12](https://github.com/GreenMachine582/repo-kit/commit/c134e12940c60bc7d4d96a19e84b38b43a4d5407))
* enrol only the Apps a repo uses ([b76e052](https://github.com/GreenMachine582/repo-kit/commit/b76e05250587b7ca7d7d3e2b2cb4a5315d15039a))
* install the commit-msg hook with rk install --hooks ([c78119c](https://github.com/GreenMachine582/repo-kit/commit/c78119ceae640b9123123c03b4e80041c7c60695))
* keep the Apps' credentials in a sops + age file and enrol both bots ([6f032a6](https://github.com/GreenMachine582/repo-kit/commit/6f032a61be49022f77afd1a5ea4ca6253686341b))
* let a file hold several blocks, and tighten what rk sync refuses ([99d6d4a](https://github.com/GreenMachine582/repo-kit/commit/99d6d4a604b2684e11a54b2be82b079fc4516f75))
* let rk secrets init write the user config entry itself ([dc22f89](https://github.com/GreenMachine582/repo-kit/commit/dc22f893f4966413521d2db2ac22596427661dfd))
* list every PR for a branch ([6a792eb](https://github.com/GreenMachine582/repo-kit/commit/6a792ebd66b24895b5c9af4084c65ac6b267bfdb))
* load .repo-kit.toml and the user config, rejecting unknown keys ([706fc1c](https://github.com/GreenMachine582/repo-kit/commit/706fc1cd37443b60d74b258252351c3933246ac2))
* manage immutable releases with rk github ([e174f1d](https://github.com/GreenMachine582/repo-kit/commit/e174f1d680c91ebefecf9cae819a96820319d74f))
* manage release-please-config.json in dev-main repos ([6124de8](https://github.com/GreenMachine582/repo-kit/commit/6124de8ca3bd66940753758516f85e44bd801df0))
* offer the project's version to seeded templates ([f9aa140](https://github.com/GreenMachine582/repo-kit/commit/f9aa140d1c9baabca60394cf502a5648c36535b8))
* report ruleset drift in rk doctor ([5fac589](https://github.com/GreenMachine582/repo-kit/commit/5fac5892aa0b50db59abc70d14009d2e77b40151))
* report settings drift in rk doctor ([7862ea8](https://github.com/GreenMachine582/repo-kit/commit/7862ea858cabba16263d1686021efd19eca4b7e8))
* show overrides on reusable workflows as OK in doctor ([9e9fc8a](https://github.com/GreenMachine582/repo-kit/commit/9e9fc8a61c268b452d134538385f663f6c9b4829))


### Bug Fixes

* exit 1 from rk secrets init until this machine can decrypt ([3899e15](https://github.com/GreenMachine582/repo-kit/commit/3899e15f89f92f2311d9da8b8ec8e57387d79eb7))
* give the [pr_bot] migration steps in an order that works ([92d9973](https://github.com/GreenMachine582/repo-kit/commit/92d99730762b4ee506922a424db10d94e7ea549c))
* harden rk upgrade's pin handling and messages ([7a56e31](https://github.com/GreenMachine582/repo-kit/commit/7a56e31b2dbe82c8c8ad3d9dd3e653f6430765ba))
* make rk secrets init's next step clear and suggest a path from ~ ([90b30b0](https://github.com/GreenMachine582/repo-kit/commit/90b30b0747d8b9a2ee94231a5a5cf46bc9fe9cf3))
* run rk with -P in the reusable workflows so a PR can't shadow it ([72c373d](https://github.com/GreenMachine582/repo-kit/commit/72c373df62d183e4cbfedfe7b5fb3f2542e9e1be))
* say that the plugin skills need rk on PATH ([9026374](https://github.com/GreenMachine582/repo-kit/commit/90263741145125492b3c82138f9654fdcc250537))


### Build

* add strict mypy and ruff's bugbear, simplify, pathlib and bandit rules ([563453b](https://github.com/GreenMachine582/repo-kit/commit/563453b1d6a836f9a8c221c3584114dee626b6c3))

## Changelog
