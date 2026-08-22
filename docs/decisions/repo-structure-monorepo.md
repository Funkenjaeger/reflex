# Decision doc: reflex repo structure — monorepo adopted (supersedes the 2026-08-12 "stay split" record)

**Status:** **DECIDED 2026-08-17 — MONOREPO ADOPTED.** reflex-fw and reflex-ui were welded into one
repository, `reflex` (`fw/` + `ui/`), on 2026-08-17. This document records that as the current
ruling and formally supersedes the 2026-08-12 "stay split" decision in
[`ui/docs/decisions/repo-structure-monorepo.md`](../../ui/docs/decisions/repo-structure-monorepo.md).
See [Why this document exists](#why-this-document-exists-2026-08-21).
**Date:** 2026-08-17 (the weld, and the effective decision date) / 2026-08-21 (this record written).
**Supersedes:** `ui/docs/decisions/repo-structure-monorepo.md` as it has read since 2026-08-12
("Status: DECIDED 2026-08-12 — STAY SPLIT"). That file is left in place, unedited — see
[Where the old record lives now](#where-the-old-record-lives-now).

---

## Why this document exists (2026-08-21)

The 2026-08-12 decision was **stay split**. On 2026-08-17, five days later, Evan reversed that
call and the weld was executed the same morning: `git filter-repo` rewrote reflex-fw's and
reflex-ui's full histories into `fw/` and `ui/` subtrees of one new repository,
`github.com/Funkenjaeger/reflex`, with old tags kept as `fw-`/`ui-` prefixes. Six branches were
seam-merged (`main`, `dev`, `dev-staging`, `integration`, `feat/els-thread-resync`,
`fix/els-mode-watch`); the old repos (`reflex-fw`, `reflex-ui`) were frozen as untouched archives —
deliberately, because they are the rollback: "if the monorepo disappoints, delete it and resume in
the old repos with nothing lost."

**This is not a new analysis reopening the question.** The weld already happened, was
Evan-approved, and is verified working (fw emulator 16/16, ui default suite 788 passed, system
suite 47/47, on the welded tree — see the reflex-ui `f386a55`-era ADR copy's own closing "Nothing
is sequenced behind this doc" section, which had already named this exact class of change as
unblocked). What was missing was the paperwork: the copy of the decision record that rode along in
the weld (`ui/docs/decisions/repo-structure-monorepo.md`, identical on every branch that carries
it) still says **"STAY SPLIT."** It has said that, uncorrected, for three nights as of 2026-08-20,
and nothing in the repo's own decision trail records the reversal. This document is that record.

## The decision

**Go full monorepo, with lockstep versioning**, exactly as sketched in the "if you ever consolidate"
contingency plan the superseded document preserved. Concretely, as executed 2026-08-17:

- One repository, `reflex`, with `fw/` (STM32 firmware, ex-reflex-fw) and `ui/` (Kivy UI, ex-reflex-ui)
  as top-level subtrees. Full history preserved on both sides via `git filter-repo` path rewrite
  (`git log -S` archaeology still works across the weld).
- Not a submodule, not a subtree-pull relationship to anything upstream — `reflex` is a deliberate
  hard fork of `rotary-controller-*` with no upstream to track, so the submodule ergonomic cost the
  superseded ADR weighed was never relevant either way.
- The old repos, `reflex-fw` and `reflex-ui`, are **frozen archives**: untouched, kept read-only,
  serving as the rollback. The archive step landed 2026-08-21: a deprecation notice at the top of
  the README on every ladder branch of both (main, dev, dev-staging, integration), pointing here,
  and the GitHub archive flag set on both.
- CI: a minimal root workflow set (`fw.yml`, `system.yml`, `ui.yml`) runs the emulator, UI-default and
  system suites on every push, path-filtered by subtree. **Lockstep release tooling is not ported
  yet** — see [Lockstep versioning — the part this record exists to preserve](#lockstep-versioning--the-part-this-record-exists-to-preserve)
  below, and the monorepo-transition task's own CI-refinement item.

## Why this reverses the 2026-08-12 call

The superseded document's own recommendation table named the trigger for reopening: *"a coupled
pair that is half-reverted or half-deployed in a way that reaches the machine"* or *"firmware/UI
compatibility becoming undiagnosable at the lathe."* This document does not re-litigate whether that
precise trigger fired — that argument, if it was made, belongs to Evan's 2026-08-17 decision, not
to this record. What this document asserts, and what it is responsible for being right about, is
narrower: **the weld happened, is verified, and the repo's own decision record disagreed with its
own repo's structure for three days.** Closing that gap is this document's whole job. The
2026-08-12 analysis is not wrong to have existed — it was the right analysis for the question it was
asked, at the time it was asked — it was simply superseded by a later decision that the record never
caught up with.

## Where the old record lives now

`ui/docs/decisions/repo-structure-monorepo.md` is **left in place, byte-for-byte unmodified**, on
every branch that already carries it. It is not deleted and its "STAY SPLIT" status line is not
edited in this change. Two reasons, both deliberate:

1. **It is the reasoning trail.** The 2026-08-12 document contains the full cost/benefit analysis,
   the three questions Evan asked and answered, the options table, and — the part that matters for
   anyone building on this repo today — the lockstep-versioning design, quoted and carried forward
   below. Deleting or rewriting it in place would destroy a record of *why* the monorepo shape looks
   the way it does, which is exactly the kind of loss an ADR exists to prevent.
2. **Editing it in place, on `integration` only, would raise a false estate alarm.** The nightly
   `check-decisions.sh` checker compares each decision-record path across every live branch. The old
   file's path (`ui/docs/decisions/repo-structure-monorepo.md`) is currently present, byte-identical,
   on `integration`, `dev`, `dev-staging` and `main`. If this change edited that file's content on
   `integration` alone, the checker would see the *same path* present on all four branches with now
   *differing* content — an immediate `DRIFT`/`!! ALERT decision-drift`, exit code 1, the next time it
   runs, and it would stay red until the edit rides the promotion ladder to every branch. A **new**
   file, by contrast, is simply *absent* from `dev`/`dev-staging`/`main` until promoted — which the
   checker's `BEHIND`/`DIVERGED` states are built to report as informational, not alarming. See
   [`check-decisions.sh` impact](#check-decisionssh-impact) below
   for the full trace. **This is the deciding reason the new-file shape is used, not merely the
   task's stated preference.**

Once this new record has ridden the normal promotion ladder to `dev-staging`, `dev` and `main` (via
Evan's normal process — nothing here is sequenced to force that), amending the old file with a short
"superseded, see `docs/decisions/repo-structure-monorepo.md`" banner in the *same* promotion wave
would close the loop without ever presenting a branch-to-branch difference. That is a follow-up, not
a precondition of landing this document.

## Lockstep versioning — the part this record exists to preserve

Task `6a82ffaa8f083a5cfcba7995` ("Finish the reflex monorepo transition") cites this file's path as
the home of the lockstep-versioning design: *"release flows deliberately not ported yet — the
lockstep-versioning design is in `ui/docs/decisions/repo-structure-monorepo.md`."* That design is
carried forward here **verbatim** (adapted only to drop forward-looking language now that it
describes what was executed, not what is proposed), so that a reader following that citation lands
on a document whose status line is correct. It is not left as a pointer into the superseded file,
because a pointer into a document whose headline still says "STAY SPLIT" is exactly the confusing
state this whole record exists to fix.

> Original text, `ui/docs/decisions/repo-structure-monorepo.md`, "Q1" answer (2026-07-09/08-12),
> lightly retitled:

**Lockstep single-version release.** The whole repo releases as one unit: one version, one tag, one
release that ships both the firmware binary and the UI. Both `reflex-fw`'s prior PaulHatch/semantic-version
tooling and `reflex-ui`'s prior `python-semantic-release` config are retired in favor of one
conventional-commit → single-version → single-release flow.

- **Cost:** every release carries both version numbers even if only one side changed. Independent
  semver per artifact is given up.
- **Why that cost is accepted:** `reflex v1.4.0` names a *known-good FW+UI pair* — turning
  "which firmware does this UI expect?" from tribal knowledge into the version number itself. A
  byte-identical binary still doesn't get re-deployed just because the version label moved; only
  actual changes ship.
- **CI shape:** path-filtered builds so a docs-only UI change doesn't force a firmware rebuild —
  already true of the current `fw.yml`/`ui.yml`/`system.yml` split; the lockstep *release* job
  (attaching both the firmware binary and UI artifacts to one GitHub release) is the piece the
  monorepo-transition task's "CI refinement" item still has to write.
- **Rejected alternative — independent versions via path-scoped releases:** neither
  `python-semantic-release` nor the PaulHatch action path-scopes a monorepo natively; doing this
  well would need trigger path-filters, tag prefixes (`ui-v*`/`fw-v*`) and two release jobs each
  scoped to its own subtree, and getting it wrong lets a firmware `feat:` commit spuriously bump the
  UI version or vice versa. Evan ruled this out by preference (2026-07-09): "he would NOT want to
  bifurcate UI vs FW versions under one roof — that's antithetical to merging in the first place."
- **Submodule was also rejected**, and stays rejected: it buys a recorded "tested-against" SHA pin at
  the cost of daily detached-HEAD friction, a benefit a solo developer gets more cheaply from
  SHA-logging in test output. "If you consolidate, skip the submodule half-step and go straight to a
  real monorepo" — which is what happened.

**Status of implementation, 2026-08-21:** not yet built. `.github/workflows/` on `integration`
currently holds only `fw.yml`, `system.yml`, `ui.yml` (test suites, path-filtered, no branch
restriction). No `release.yml` exists in the monorepo yet. `ui/pyproject.toml` still carries
`[tool.semantic_release.branches.dev]` / `[tool.semantic_release.branches.main]` blocks from the
split era, referencing a `release.yml` that has not been ported — inert configuration, not a live
release path, until that CI-refinement item lands.

## Known gotcha this decision leaves in place — reflex-fw `main` release identity

Carried forward unchanged from the superseded document, because the underlying repo fact did not
change at the weld: reflex-fw's `main:.github/workflows/release.yaml` (frozen archive, pre-weld)
cut releases as `user.email "bartei81@gmail.com"`, inherited from the upstream `rotary-controller-f4`
fork. Whether this identity issue needs fixing in the monorepo's eventual `release.yml`, or is moot
because that workflow no longer exists in its old form, is unresolved and belongs to the CI
refinement item, not to this document.

## `check-decisions.sh` impact

Summary: landing this file as a new
path on `integration` alone (nothing touched on `dev`/`dev-staging`/`main`) is expected to report
`OK` for this new record (vacuously — one live branch, agreeing with itself) with `DIVERGED`
annotations for `dev`, `dev-staging` and `main`, because each of those branches carries its own
post-weld commits not yet in `integration` (so none of them qualifies as a strict-ancestor `BEHIND`
— the escape valve that would otherwise suppress the annotation entirely). `DIVERGED` is
informational only (`!! NOTE`, not `!! ALERT`) and does not move the checker's exit code. The
existing "STAY SPLIT" record is untouched and continues to report `OK` (byte-identical everywhere)
exactly as it does today.

## Nothing is sequenced behind this doc

Same closing note the superseded document carried, restated because it is still true: this is not a
gate. The monorepo is not being reopened for debate by anyone reading this; it is already built, and
work should proceed on it exactly as the transition task already describes.
