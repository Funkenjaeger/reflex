# Decision doc: reflex-ui + reflex-fw repo structure (monorepo?)

**Status:** **DECIDED 2026-08-12 — STAY SPLIT.** reflex-ui and reflex-fw remain two separate
repositories. Not a monorepo, not a submodule. See [The decision](#the-decision-2026-08-12).
**Date:** 2026-07-09 (analysis) / 2026-08-12 (decision)

> **Update 2026-07-12:** the emulator system-test suite has landed (32/32 under WSL), clearing the
> original "not now" condition. More importantly, the planned ELS **auto-start** feature is exactly
> the revisit trigger named at the bottom of this doc: it appends registers to `Ramps.h` and
> `devices.py` in lockstep, has a contract test spanning both trees, and needs a documented
> "flash firmware before updating the Pi UI" deploy ordering — a coordinated cross-repo change that
> cannot be landed or reverted atomically. Promoted to `docs/decisions/` as a first-class repo doc.
> Decide at a release boundary, per the recommendation below.
>
> *(Answered 2026-08-12: decided **stay split**. The auto-start register append this update
> anticipated is unblocked and should be built two-repo — see [The decision](#the-decision-2026-08-12).)*
**Context:** Building the emulator-backed system-test suite exposed a two-repo version-coupling
seam (the suite tests a contract split across both repos). Evan has long felt the split is a bit
artificial: the repos target very different hardware but are inextricably linked in interfaces
and functionality. This captures the analysis so we can revisit without re-deriving it.

---

## The decision (2026-08-12)

**Stay split.** Two repositories, as they are today. No subtree merge, no submodule, no change to
clone URLs, tags or doc links. The analysis below is preserved as written — it is still the right
analysis *if* consolidation ever becomes necessary — but the question is now closed, and the
revisit trigger is retired rather than armed.

### Why: the trigger fired, and then the coordination happened anyway — twice

The revisit trigger fired **2026-07-12**. The question then sat open for a month. That month was
not quiet: it contained exactly the coordinated cross-repo work the trigger was meant to catch,
and **the two-repo convention absorbed all of it.**

**Two confirmed register-lockstep landings, both absorbed by convention alone:**

1. **2026-08-02 — the emulator command channel.** reflex-ui `cea3ad3` (08:18:54 -0400)
   "test(system): the emulator lathe now moves — X channel, real config, re-zero" and reflex-fw
   `267f0a3` (08:19:00 -0400) "feat(emulator): serve-mode stdin command channel for driving X in
   tests". **Six seconds apart** — an atomic pair in everything but git. The UI tests do not pass
   without the firmware side.
2. **2026-08-08 — the backlash calibration register block.** reflex-ui `2842cc7` (11:08:20 -0400),
   whose own commit message states the coupling explicitly:

   > pairs with reflex-fw 3fec190 and must land with it (the register map moved)

   It moved `KNOWN_ROOT_SIZE 264 -> 300` on the UI side against the firmware's re-laid register
   block. Sixteen seconds separate it from its firmware half.

   **Honest caveat on that SHA.** The named `3fec190` **is not in the bare mirror** — verified
   2026-08-12: `git cat-file -t` on `dserver:/mnt/git/reflex-fw.git` fails for
   `3fec19071118119bf2be2e1d136555dc1e07b24e` and succeeds for
   `b98b3987bcd56d5066b1fd0920cdd5b02eb0b98a`. `3fec190` survives only as an **unreachable object
   in the local clone** (reachable from no ref), carries the identical subject
   "feat(els): closed-loop backlash calibration and take-up confirmation" and the identical author
   timestamp `11:08:04 -0400`, and its parent `aa07cff` was itself rewritten to `d9dcf98`. So it
   was almost certainly rebased to `b98b398` before push. **This is inference, not proof:** the two
   trees are *not* byte-identical — `git diff --stat 3fec190 b98b398` is `AGENTS.md | 36 ++++`, one
   file, 36 added lines. The ELS content matches; the identification does not rest on a byte-for-byte
   comparison and should not be cited as if it did. **Medium confidence.**

**Two rounds of CI-pairing rework also landed successfully**, on the same convention: first the
branch-matching resolver (`ci: pair reflex-fw by matching branch name, not a hardcoded ref` —
`738ff7d` 2026-08-10, reworked `5c782a1`/`1be9f7e` 2026-08-11), replacing the hardcoded
`ref: dev-staging` pin that `8e69f57` had installed on 2026-08-02; then the
`$BRANCH -> dev-staging -> dev` fallback chain. `277ff05` records how CI applies the pairing rule.

### The counter-evidence is the decisive part

The trigger's premise was that a coordinated cross-repo change would prove too painful to survive
without atomicity. **The coordination happened, twice, and worked.** The cost was one red CI run
(the 2026-08-02 first run, which took reflex-fw's default branch and errored all 16
reversing-matrix permutations) — a bad ref pin, found and fixed in hours, not a structural failure.

Against that, a monorepo now is a **high-churn history merge across two actively-developed
repositories in the middle of live ELS firmware work.** Both repos carry unmerged feature lines
(`feat/els-slip-attribution`, `feat/els-thread-resync`, `integration`), elspi runs `dev-staging` in
production, and firmware changes are gated on hardware testing at the machine. Restructuring under
that is precisely the "never mid-feature" case the 2026-07-09 recommendation warned against — and
the month of evidence says there is no recurring pain being bought off.

### The cost being accepted, stated plainly

**Cross-repo changes stay a convention rather than a mechanism.** Correctness depends on
commit-message discipline and CI pairing, not on atomicity:

- The register map remains hand-duplicated (`devices.py` typedefs ↔ `Ramps.h`), and naming parity
  remains prose in both `AGENTS.md` files (reflex-ui:56, reflex-fw:59): *"Cross-repo changes
  affecting the Modbus register interface are called out in commit messages."*
- A coupled pair can still be **half-reverted**. Nothing mechanical prevents it.
- Bisecting across a coupled change means bisecting two histories by hand.
- The 2026-08-08 caveat above is the failure mode in miniature: a commit message named a firmware
  SHA that a later rebase invalidated, so the written record points at an object that is not in the
  mirror. **A SHA in a commit message is not a durable link.** Prefer naming the paired *branch* and
  subject line, which survive a rebase, over a bare SHA that does not.

This is accepted deliberately, not overlooked. The mitigations already in place — the commit-message
callout convention, the branch-matching CI resolver, and conftest reporting the reflex-fw SHA in the
pytest header — are the agreed substitute for atomicity.

### Known gotcha this decision leaves in place — reflex-fw `main` release identity

Re-verified 2026-08-12, still true: **`main:.github/workflows/release.yaml:64` is
`git config --local user.email "bartei81@gmail.com"`**, with `user.name "Github Action"`. That
address is inherited from the upstream `rotary-controller-f4` fork and belongs to a stranger, so
**every release tag cut from `main` is attributed to it.** `dev`, `dev-staging` and `integration`
all use `41898282+github-actions[bot]@users.noreply.github.com`; `main` is 21 commits behind that
modernization.

**A fix is already written but unmerged:** reflex-fw `d0111c6` on branch **`ci/release-yaml-drift`**
(2026-08-11), which drops the fork identity, removes the dead `slow` branch trigger, and moves the
build out-of-tree. Verified 2026-08-12: `d0111c6` exists on `ci/release-yaml-drift` (local and
origin) and is **not** an ancestor of `main`. Staying split does not fix this and does not make it
worse — it is recorded here so it is not lost with the ADR's closure.

> **Note on the companion `[skip ci]` gotcha, which was recorded alongside this one and is now
> DISPROVEN.** The belief that "any commit to reflex-ui `main` or `dev` needs `[skip ci]` because
> `patch_without_tag = true` cuts a patch release for any commit" is **false**. reflex-ui `820b04b`
> established that `patch_without_tag`, `remove_dist` and `upload_to_pypi` are v7-schema keys that
> python-semantic-release 10.5.3 silently ignores. Worse, the habit has a real cost: `ci.yml` exists
> only on `integration`, `dev-staging` and feature branches, so on every branch except `main`/`dev`
> the marker suppresses **the test suite** and nothing else. This decision commit deliberately
> carries no skip marker.

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

## Recommendation (2026-07-09, superseded by the decision above)

> **Superseded 2026-08-12.** Point 1 ("not now") is now permanent rather than provisional: the
> answer is *not at all*, on the evidence in [The decision](#the-decision-2026-08-12). Points 2 and
> 3 are retained as the contingency plan — **they are not a plan of record and nothing is sequenced
> behind them.** If consolidation is ever reopened, start here rather than re-deriving it.

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

## Revisit trigger — RETIRED 2026-08-12

The original trigger read:

> Reopen this the **next time a protocol/register-map change forces a coordinated edit across both
> repos that you can't land or revert atomically.** That friction is the real signal; when it
> recurs, the monorepo stops being a nicety and becomes the fix. Until then, the Task 3 SHA log +
> Task 15 contract test cover the sharp edges cheaply.

**It fired, three times, and each time the convention held.** The trigger was calibrated on
predicted friction; the measured friction was one red CI run. A signal that keeps firing while the
system keeps working is a miscalibrated signal, so it is **retired rather than re-armed** — this
doc should stop re-surfacing on cross-repo coupling alone.

**What would genuinely reopen this** is a *failure*, not a coordination event:

- A coupled pair that is **half-reverted or half-deployed in a way that reaches the machine** —
  i.e. the convention actually breaks, rather than merely being exercised.
- Firmware/UI compatibility becoming undiagnosable at the lathe, where lockstep versioning naming a
  known-good FW+UI pair would have given the answer directly.
- A second developer, which is where the hand-maintained naming parity and the
  SHA-in-commit-message convention stop scaling.

Absent one of those, **stay split.**

## Nothing is sequenced behind this doc

Recorded explicitly because this ADR was cited as a sequencing gate while it sat open: **it is no
longer a gate on anything.** ELS auto-start, the phase-offset mechanism, and any other coupled
cross-repo feature are unblocked and should be built two-repo, using the commit-message callout
convention and the branch-matching CI pairing. Do not wait on a repo restructure that is not
coming.
