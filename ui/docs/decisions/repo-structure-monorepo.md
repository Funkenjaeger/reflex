# Decision doc: reflex-ui + reflex-fw repo structure (monorepo?)

**Status:** **CLOSED — MOOT.** Not adopted, and not rejected on its merits. Closed 2026-07-30.
**Date:** 2026-07-09

> ## Closing note, 2026-07-30
>
> **The decision was overtaken by the project ending rather than settled.** The revisit trigger
> fired on 2026-07-12, consolidation was never carried out, and the ELS project has since been
> wound down and archived — `reflex-ui` and `reflex-fw` are no longer under active development.
>
> That removes the premise the whole analysis rests on. Every benefit of consolidating listed
> below is a benefit *per coordinated cross-repo change*: atomic commits and reverts, compatibility
> encoded in one version number, naming parity made checkable, the register-map contract test
> living in one tree. With no further coordinated changes coming, the recurring cost the monorepo
> was meant to eliminate has gone to zero, while the one-time cost of merging two histories and
> rewriting the release flow has not. Restructuring two finished repositories would break existing
> clone URLs, release tags, and cross-repo documentation links to buy nothing.
>
> **So: no action, and the two-repo layout stands as the permanent shape of this project.**
>
> **The recommendation below is preserved and still stands as the answer if work ever resumes** —
> full monorepo with lockstep versioning via `git subtree`, not a submodule, and not
> independent per-artifact versions. Anyone picking this up should read the analysis as unchanged
> and current; only its urgency expired. The hand-maintained coupling it describes (naming parity
> between Python properties and firmware variables, the duplicated register map between
> `devices.py` and `Ramps.h`) is real and remains in the code, so a reader who resumes development
> should expect to hit exactly the friction documented here.
>
> Closed as part of the ELS archival pass; see the `Funkenjaeger/reflex-ui` and `reflex-fw` READMEs
> for the current status of each repository.

> **Update 2026-07-12:** the emulator system-test suite has landed (32/32 under WSL), clearing the
> original "not now" condition. More importantly, the planned ELS **auto-start** feature is exactly
> the revisit trigger named at the bottom of this doc: it appends registers to `Ramps.h` and
> `devices.py` in lockstep, has a contract test spanning both trees, and needs a documented
> "flash firmware before updating the Pi UI" deploy ordering — a coordinated cross-repo change that
> cannot be landed or reverted atomically. Promoted to `docs/decisions/` as a first-class repo doc.
> Decide at a release boundary, per the recommendation below.
**Context:** Building the emulator-backed system-test suite exposed a two-repo version-coupling
seam (the suite tests a contract split across both repos). Evan has long felt the split is a bit
artificial: the repos target very different hardware but are inextricably linked in interfaces
and functionality. This captures the analysis so we can revisit without re-deriving it.

---

## The two repos, as they actually are (verified 2026-07-09)

| | reflex-ui | reflex-fw |
|---|---|---|
| Owner / remote | `Funkenjaeger/reflex-ui` | `Funkenjaeger/reflex-fw` |
| Target | Raspberry Pi / desktop, Python + Kivy | STM32, C, `arm-none-eabi` |
| Build | `uv` / hatchling | CMake / arm-none-eabi-gcc |
| Tracked files | 327 | 189 |
| Submodules | none | none |
| Release tooling | **python-semantic-release** (`version_toml` → `pyproject.toml`, conventional-commit changelog, GH releases with screenshot assets, `main`/`dev` with rc prereleases) | **PaulHatch/semantic-version** GH Action (git-tag versioning from commit history, builds arm binaries, `main`/`dev`) |
| Coupling today | AGENTS.md mandates **hand-maintained naming parity** (`syncRatioNum`, `maxSpeed`, `servoMode`, …) between Python properties and firmware vars; README + ELS_STOP.md deep-link into `reflex-fw/ARCHITECTURE.md`; register map duplicated by hand (`devices.py` typedefs ↔ `Ramps.h`) | same, mirror side |

The coupling is **already a monorepo invariant enforced by prose and discipline** — a naming
convention plus a duplicated register map plus cross-repo doc links. When two repos need a
shared naming rule to stay usable, the boundary between them is largely fictional.

---

## The three questions (Evan's, 2026-07-09) — answered

### Q1: What's the real cost of monolithic release — just an extra CI build?

> **Evan clarifications (2026-07-09):** (1a) reflex-fw's PaulHatch action was only a *fallback* —
> chosen because the repo isn't Python and he didn't want to pull Python in just to standardize on
> python-semantic-release. It is NOT a committed choice, so the "fights two tools" cost below is
> overstated: there's no attachment to PaulHatch to preserve. (1b) He would NOT want to bifurcate
> UI vs FW versions under one roof — that's antithetical to merging in the first place. **So the
> independent-version option is off the table by preference; lockstep is the intended model.**
> This makes consolidation *cheaper* than the analysis first suggested: one conventional-commit →
> single-version flow, no path-scoped release juggling.

**The extra build is the trivial part.** The two repos don't currently share release tooling, so
"treat them monolithically" nominally forces a choice between two release models — but per (1b)
Evan has already picked lockstep, and per (1a) neither current tool is load-bearing:

**(a) Lockstep single-version (SIMPLE, and arguably BETTER for this project).** The whole repo
releases as one unit: one version, one tag, one release that ships both the firmware binary and
the UI. Retire both current release configs, write one.
- Cost: every release carries both version numbers even if only one side changed. You lose
  *independent* semver for the two artifacts.
- **But that "cost" is a feature here:** `reflex v1.4.0` would name a *known-good FW+UI pair* —
  exactly the compatibility guarantee you currently reconstruct by hand (and that Task 3's
  SHA-pinning amendment exists to approximate). Lockstep turns "which firmware does this UI
  expect?" from tribal knowledge into the version number. You still don't have to *flash*
  firmware that didn't change — a byte-identical binary just isn't re-deployed; only the version
  label moves.
- Extra CI build: yes, but path filters make each build conditional (don't rebuild firmware on a
  docs-only UI change). Minor.

**(b) Independent versions via path-scoped releases (FIDDLY — fights both current tools).** Keep
two version streams in one repo.
- python-semantic-release is single-package-per-repo oriented; PaulHatch computes from whole git
  history. Neither does monorepo path-scoping natively/well.
- You'd need: trigger path-filters, tag prefixes (`ui-v*` / `fw-v*`), and two release jobs each
  scoped to its own subtree. Get it wrong and a firmware `feat:` commit spuriously bumps the UI
  version (and vice versa).
- This is ongoing maintenance, not a one-time setup cost.

**Bottom line on Q1:** the real cost isn't the build, it's the release *model* — and Evan has
already chosen it (lockstep). With no attachment to PaulHatch and no desire for independent
versions, consolidation collapses to "write one conventional-commit release flow that versions
the whole repo and attaches both artifacts." The scary-sounding path-scoped-release cost was
option (b), which is now explicitly rejected.

### Q2: Isn't maintaining two build systems in two repos already the cost — no worse under one roof?

**Agreed.** Two build systems is inherent to a HW+SW product, not a consequence of repo layout.
Under one roof it's no worse — and you *stop* paying the cross-repo coordination tax: no external
directory permissions dance, no "find the sibling repo" bootstrapping (AGENTS.md currently spends
real words on this), no cross-repo checkout tokens in CI. Path-filtered CI keeps the two build
jobs independent.

### Q3: Submodule was advised against before (the detached-HEAD ergonomic cost).

**That advice still holds, and is arguably stronger for a solo dev.** The submodule pin's main
benefit — recording exactly which firmware SHA a UI commit was tested against — matters most for
*teams* coordinating across repos. As the sole developer, you get most of that benefit already
from Task 3's SHA-logging amendment, without paying the daily submodule friction (detached HEADs,
forgotten `--recursive`, easy-to-miss pointer bumps). So for you, submodule is close to
worst-of-both: you pay the ergonomic cost for a coordination benefit you don't currently need.
**If you consolidate, skip the submodule half-step and go straight to a real monorepo.**

---

## Options summary

| Option | One-time cost | Ongoing cost | Buys you | For a solo dev |
|---|---|---|---|---|
| **Status quo** (2 repos) | none | hand-maintained naming parity, register-map drift risk, cross-repo CI coordination, manual FW/UI compat tracking | familiar | fine, but the coupling keeps leaking |
| **Submodule pin** | moderate | detached-HEAD friction, pointer-bump discipline | committed record of tested FW SHA | **not recommended** — friction > benefit solo |
| **Monorepo, lockstep version** | moderate (merge histories, unify release into one config, path-filter CI) | ~none new; arguably *less* | atomic cross-repo commits/reverts; compatibility encoded in the version; naming parity becomes checkable in one tree; register-map contract test (Task 15) trivially in-repo | **recommended if/when consolidating** |
| **Monorepo, independent versions** | high (path-scoped release tooling that fights both current tools) | ongoing release-config maintenance | independent semver per artifact | only if lockstep proves genuinely wrong |

---

## Recommendation

1. **Not now.** Don't restructure mid-flight while the test suite is open on the table. Moving
   both repos out from under yourself now would be reckless.
2. **When you do consolidate, go full monorepo with lockstep versioning** — not submodule, not
   independent-version monorepo. Lockstep fits coupled HW/FW/UI, converts your hand-maintained
   compatibility contract into an automatic one, and is the simplest release model. Do it *at a
   release boundary* (e.g. just after a tagged release), never mid-feature.
3. **Migration sketch** (for when the time comes):
   - `git subtree add` (or a history-preserving merge with `--allow-unrelated-histories`) to pull
     reflex-fw into e.g. `firmware/` under reflex-ui — subtree keeps full history and avoids
     submodule ergonomics.
   - Collapse to one release config: retire python-semantic-release's repo-wide assumptions and
     PaulHatch, adopt a single conventional-commit → single-version → single-GH-release flow that
     attaches both the firmware binary and the UI artifacts.
   - Path-filter the two build jobs so each only runs when its subtree changed.
   - Move the register-map contract test (Task 15) to compare two in-repo paths — no cross-repo
     checkout needed.
   - Update AGENTS.md: naming-parity rule stays (still a good convention) but is now enforceable
     by a single test rather than by prose.

## Revisit trigger

> **Spent, 2026-07-30.** This trigger fired on 2026-07-12 and was not acted on before the project
> was archived. It cannot fire again while both repos are dormant, since it depends on a
> coordinated change being attempted. It becomes live again only if development resumes — see the
> closing note at the top. Retained as written for the record:

Reopen this the **next time a protocol/register-map change forces a coordinated edit across both
repos that you can't land or revert atomically.** That friction is the real signal; when it
recurs, the monorepo stops being a nicety and becomes the fix. Until then, the Task 3 SHA log +
Task 15 contract test cover the sharp edges cheaply.
