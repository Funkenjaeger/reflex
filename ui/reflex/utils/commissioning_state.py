"""Is this machine commissioned? One predicate, asked once, at startup.

WHY THIS MODULE EXISTS. Until 2026-09-21 the question never had to be asked in
the app, because the provisioning seam answered it by ORDERING: elspi's
``converge -> restore -> start`` kept the UI off the screen until
``/var/lib/reflex-config`` existed, so by the time any ``SavingDispatcher`` ran
there was always commissioned config for it to read. The amendment to call 1 of
``docs/design/seam.md`` (elspi ``8f21fd5``/``d81d04c``, 2026-09-21) bakes the
app into the image and starts it on first boot, which removes that protection.

What is left without it is specific and bad:
``reflex.dispatchers.saving_dispatcher.read_settings`` returns ``None`` for an
absent file -- no error, no refusal -- so a dispatcher whose YAML is missing
keeps its IN-CODE DEFAULTS, and ``write_settings`` treats a new file as, in its
own words, "the commissioning event for that dispatcher". A freshly flashed
card would therefore present a working-looking lathe UI on ELS geometry nobody
measured, and the first save would record those defaults as the commissioning
baseline. Worse, the 19 default files it wrote would themselves satisfy the
restore contract's bar, so the SECOND boot would find a "commissioned" machine
and the fiction would be permanent.

THE BAR IS THE RESTORE CONTRACT'S OWN, DELIBERATELY. elspi
``deltas/02-restore.sh`` gates on the CONTENT of a capture, not its path::

    [ -s "${SRC}/Els-0.yaml" ] || die "no non-empty Els-0.yaml ... REFUSING."
    NYAML="$(find "${SRC}" -maxdepth 1 -name '*.yaml' | wc -l)"
    [ "${NYAML}" -ge 15 ] || die "... partial capture. REFUSING ..."

``docs/provisioning.md`` states the same thing in prose: a non-empty
``Els-0.yaml`` must be present, and there must be at least 15 ``.yaml`` files.
:func:`is_commissioned` is that gate, re-expressed once in Python against a
LIVE config directory rather than a capture. Two answers that must agree are
two answers that will eventually disagree, so the numbers here are named
constants with the shell's values, and
:mod:`tests.utils.test_commissioning_state` pins the boundary cases the shell's
own ``deltas/tests/test-restore-contract.sh`` pins.

HOW MUCH SLACK THERE ACTUALLY IS: 2 FILES, NOT 4 (corrected 2026-09-22). The
live machine carries **17** ``.yaml`` files, so the margin over the 15-file bar
is two -- lose three dispatchers' files and a fully commissioned lathe reads as
a partial capture. It was 19 until 2026-09-19, when four stray
``ElsAdvancedBar-*.yaml`` files went away and ``Device-0.yaml`` was added.
``docs/provisioning.md`` still says 19 in its own prose; correcting the elspi
half is a separate change and is NOT quoted here as if it agreed, because a
stale quotation is indistinguishable from a checked one.

The depth matters as much as the count: ``-maxdepth 1``. The app's own
``ledger/snapshots/*.yaml`` live under the config directory, and a recursive
count would let the app's record-keeping vote itself commissioned.

WHY THERE IS A LATCH, AND WHY IT DEFAULTS TO COMMISSIONED
---------------------------------------------------------
The predicate is a pure function of a directory, but the question is asked by
code that must all get the SAME answer for the life of the process:

* Re-reading the directory per save would be a gate that opens under its own
  feet. Suppose the gate leaked and twelve files got written; save number
  thirteen through fifteen would flip the count past the bar and the app would
  quietly promote its own defaults to commissioned. The answer is taken ONCE,
  before the first dispatcher exists, and does not move.
* It is also the honest model of the machine. "Commissioned" is a property of
  the card the app booted from, not of the instant a slider moved.

:func:`latch` is called by :meth:`reflex.app.MainApp.build` as its first act.
:func:`latched` returns ``True`` -- commissioned -- when nothing ever latched,
and that default is load-bearing in two directions. On the machine the app
always latches, so the default is unreachable there. Off the machine (tests,
previews, ``tools/``) nothing has booted a card, there is no card to judge, and
a module that answered "uncommissioned" would silently disable config
persistence for every caller that never asked the question. Fail toward TODAY'S
behavior for code that did not opt in; fail toward refusal only for a real app
start that looked at a real directory and found nothing.

WHAT THIS MODULE DOES NOT DO. It never creates, repairs or completes a config
directory. Refusing to invent commissioned data is the property being
protected: only a human at the lathe with an indicator on the work knows the
backlash, and :func:`is_commissioned` returning ``False`` is a statement that
nobody has done that yet -- not a task to be worked around. The single file it
ever writes is :data:`DISMISSAL_MARKER`, which is not configuration and is
never read as any.

AND IT NEVER DELETES THAT MARKER, INCLUDING WHEN IT BECOMES REDUNDANT. Once a
hand-commissioned directory genuinely passes :func:`is_commissioned`, the
marker no longer decides anything, and tidying it away at that point is
tempting. Three reasons not to:

* it would make the state NON-MONOTONIC. Remove the marker at 15 files, then
  lose one -- a dispatcher retired in an upgrade, an operator deleting a file
  over SSH -- and a machine somebody commissioned by hand silently reverts to
  refusing writes, with the decision that authorised them already destroyed.
  The marker costs an inode; that failure costs an afternoon at the lathe with
  no terminal to diagnose it from.
* it is PROVENANCE. "These numbers came from a capture" and "these numbers were
  typed in by hand" are different claims about the same directory, and the
  marker is the only place the difference is recorded. Anyone reading this
  machine's ledger later wants it.
* deleting it is a WRITE to the config directory, taken automatically, on the
  startup path of the module whose whole purpose is not touching configuration
  it did not measure.

Removing it is a deliberate operator act -- ``rm`` the file -- and that act
means exactly what it says: put the warning back.
"""
from pathlib import Path

from kivy.logger import Logger

from reflex.utils.paths import config_dir

log = Logger.getChild(__name__)

#: The stem whose file carries the commissioned ELS geometry. A capture without
#: it is not a restore point (elspi ``deltas/02-restore.sh``), so neither is a
#: live directory without it.
ANCHOR_FILE = "Els-0.yaml"

#: Minimum ``*.yaml`` files, directly in the directory, for the configuration
#: to count as whole. 15 with the live machine at 17 -- the slack is there so
#: that removing a dispatcher does not read as a partial capture, and it is the
#: restore script's number rather than a second opinion. Two files of slack, so
#: the margin is thin: see the module docstring.
MIN_YAML_FILES = 15

#: The operator's hand-commissioning decision, recorded as a FILE WHOSE
#: EXISTENCE IS THE SETTING (the same idiom as ``gist_sync.enabled_path``).
#:
#: IT HAS NO ``.yaml`` EXTENSION, AND THAT IS A CORRECTNESS PROPERTY, not a
#: naming taste. :func:`is_commissioned` counts ``*.yaml`` files; a marker that
#: matched that glob would be a gate that votes in its own election -- drop it
#: on a directory sitting one file short of :data:`MIN_YAML_FILES` and the
#: directory would pass the restore contract on the strength of the marker
#: alone. ``tests/utils/test_commissioning_dismissal.py`` pins exactly that
#: arm.
#:
#: It also does not travel. ``commissioning_bundle.build`` globs ``*.yaml``, so
#: the marker is never exported, and elspi's capture takes ``*.yaml`` too --
#: dismissal is a decision about THIS card, and importing another machine's
#: bundle must not silently import its decision.
DISMISSAL_MARKER = "commissioning-dismissed"


def is_commissioned(directory=None) -> bool:
    """Does ``directory`` hold a commissioned machine's configuration?

    THE PREDICATE. Every caller that wants to know "is this machine
    commissioned" asks this one function -- there is no second, inline copy of
    the rule per dispatcher, because the failure mode of a duplicated gate is
    the copy nobody edits.

    :param directory: the config directory to judge. ``None`` means
        :func:`reflex.utils.paths.config_dir`, i.e. this machine.

    ``True`` requires BOTH halves of the restore contract:

    * ``Els-0.yaml`` present and NON-EMPTY -- a zero-byte file is a touched
      placeholder, not measured geometry;
    * at least :data:`MIN_YAML_FILES` ``*.yaml`` files directly in the
      directory -- a partial capture is refused rather than accepted as a
      subset.

    Never raises. An unreadable or missing directory is not commissioned, which
    is both the true answer and the safe one.
    """
    path = Path(directory) if directory is not None else config_dir()
    try:
        if not path.is_dir():
            return False
        anchor = path / ANCHOR_FILE
        # `is_file()` first: `stat()` on a dangling symlink raises, and a
        # directory named Els-0.yaml has a nonzero size on some filesystems.
        if not anchor.is_file() or anchor.stat().st_size <= 0:
            return False
        # Non-recursive on purpose -- see the module docstring on `ledger/`.
        count = sum(1 for item in path.glob("*.yaml") if item.is_file())
        return count >= MIN_YAML_FILES
    except OSError as e:
        log.error(f"commissioning state: cannot read {path} ({e})")
        return False


def dismissal_marker_path(directory=None) -> Path:
    """Where :data:`DISMISSAL_MARKER` lives for ``directory``."""
    path = Path(directory) if directory is not None else config_dir()
    return path / DISMISSAL_MARKER


def is_dismissed(directory=None) -> bool:
    """Has a human already chosen to commission this card by hand?

    A SEPARATE question from :func:`is_commissioned`, deliberately, and they
    are never folded into one predicate. ``is_commissioned`` answers "does this
    directory hold measured configuration" -- a fact about files that the
    restore script and this module must agree on to the letter. This answers
    "did the operator take responsibility for filling it in", which is a fact
    about a person. Only :func:`latch` consults both.
    """
    try:
        return dismissal_marker_path(directory).is_file()
    except OSError as e:
        log.error(f"commissioning state: cannot read the dismissal marker ({e})")
        return False


def _yaml_signature(path) -> tuple | None:
    """Name, inode, size and mtime of every ``*.yaml`` directly in ``path``.

    The thing :func:`config_changed_since_latch` compares. ``()`` for a missing
    directory -- an honest empty answer -- and ``None`` when the directory could
    not be read at all, which callers must treat as "cannot prove anything".

    THE INODE IS IN THERE FOR ``_atomic_dump``. ``commissioning_bundle`` writes
    every restored file as a temp file plus ``os.replace``, so the result is a
    NEW inode under an old name. On a filesystem with coarse mtime granularity
    a same-second replacement of same-sized content would otherwise compare
    equal, and this check exists precisely to catch that write. An unstable
    inode (some network filesystems) can only make this report "changed" when
    nothing did, which costs the operator a restart they did not need and never
    opens the gate at the wrong moment.
    """
    try:
        p = Path(path)
        if not p.is_dir():
            return ()
        out = []
        for item in sorted(p.glob("*.yaml")):
            if not item.is_file():
                continue
            st = item.stat()
            out.append((item.name, st.st_ino, st.st_size, st.st_mtime_ns))
        return tuple(out)
    except OSError as e:
        log.error(f"commissioning state: cannot read {path} ({e})")
        return None


#: The latched answer for this process. ``None`` means nobody has asked.
_latched: bool | None = None

#: The directory :func:`latch` judged, and its ``*.yaml`` content at that
#: moment. Both are ``None`` until something latches.
_latch_dir: Path | None = None
_yaml_at_latch: tuple | None = None


def latch(directory=None) -> bool:
    """Evaluate the gate ONCE and remember it. Returns it.

    Called from :meth:`reflex.app.MainApp.build` before any
    ``SavingDispatcher`` is constructed. Idempotent in effect but not in
    intent: calling it twice re-reads the directory, which is why exactly one
    caller in the application does.

    TWO WAYS THE GATE STARTS OPEN, and they are not the same statement. The
    directory meets the restore contract (:func:`is_commissioned`) -- somebody
    measured this machine and the capture is here. Or the marker is present
    (:func:`is_dismissed`) -- somebody stood at this lathe, read the modal and
    undertook to enter the numbers by hand. A machine in the second state is
    still not commissioned in the restore contract's sense, and
    :func:`is_commissioned` keeps saying so; what the marker changes is whether
    this process refuses to write.

    It also records the directory's ``*.yaml`` content, which is the baseline
    :func:`config_changed_since_latch` measures against.
    """
    global _latched, _latch_dir, _yaml_at_latch
    where = Path(directory) if directory is not None else config_dir()
    _latch_dir = where
    _yaml_at_latch = _yaml_signature(where)

    commissioned = is_commissioned(directory)
    dismissed = is_dismissed(directory)
    _latched = commissioned or dismissed

    if commissioned:
        log.info("commissioning state: commissioned")
    elif dismissed:
        log.warning(
            f"commissioning state: UNCOMMISSIONED but DISMISSED -- {where} "
            f"does not meet the restore contract, and {DISMISSAL_MARKER} says "
            f"an operator chose to commission this machine by hand. Config "
            f"writes are allowed. Delete that file to put the warning back."
        )
    else:
        log.warning(
            f"commissioning state: UNCOMMISSIONED -- {where} does not meet the "
            f"restore contract (non-empty {ANCHOR_FILE} plus >= "
            f"{MIN_YAML_FILES} .yaml files). Config writes are refused and the "
            f"UI says so on screen."
        )
    return _latched


def config_changed_since_latch() -> bool:
    """Has anything written a ``*.yaml`` into the config directory since
    :func:`latch` looked at it?

    ON AN UNCOMMISSIONED MACHINE NOTHING CAN, and that is what makes this
    worth asking. ``saving_dispatcher.write_settings`` refuses every write
    while the gate is closed, so between :func:`latch` and the first
    :func:`dismiss` the set of ``*.yaml`` files is frozen BY THE GATE ITSELF.
    A change therefore means something outside the gate put configuration on
    this card while the app was running -- an in-app bundle import, an ``scp``
    over SSH, a hand-edit -- and in every one of those cases the files on disk
    and the dispatchers in memory now disagree.

    Fails CLOSED: an unreadable directory reports "changed", because the
    question being asked is "can I prove nothing arrived", and an error is not
    a proof.
    """
    if _latch_dir is None:
        return False
    now = _yaml_signature(_latch_dir)
    if now is None or _yaml_at_latch is None:
        return True
    return now != _yaml_at_latch


def dismissal_available() -> bool:
    """May the operator open the gate by hand, right now?

    ``False`` in three situations, each for its own reason:

    * nothing ever latched -- there is no machine under this process to make a
      decision about (tests, previews, ``tools/``);
    * the gate is already open -- a commissioned machine has nothing to
      dismiss, and a card dismissed on an earlier boot is already past this;
    * **configuration arrived since the latch** -- see
      :func:`config_changed_since_latch`. This is the hard one, and it is a
      CORRECTNESS bar rather than a caution. ``commissioning_bundle.apply``
      writes restored YAML straight to disk with its own ``_atomic_dump``
      while every dispatcher in memory is still holding its in-code defaults.
      Opening the gate at that moment means the next property change -- a
      slider nudged, a format toggled -- serialises those defaults over the
      values just restored, and the operator's own import is what destroys
      their configuration. A restart is the remedy and the only one; the
      existence of a button does not change the ordering the restore contract
      is built on.
    """
    if _latched is None:
        return False
    if _latched:
        return False
    return not config_changed_since_latch()


def dismiss() -> bool:
    """THE re-latch path. Open the write gate and record why, persistently.

    This is the named function :func:`clear_latch`'s docstring says does not
    exist, and the reason it can exist now is that it is gated on
    :func:`dismissal_available` rather than on a caller's good intentions. A
    second inline gate in ``write_settings`` was the alternative and is the
    failure mode this module was written to avoid: a duplicated rule is the
    copy nobody edits.

    Returns ``True`` only if the gate is now open and the marker is on disk.
    Refuses -- loudly, in the log, and without touching the filesystem -- when
    :func:`dismissal_available` says no. A caller MUST NOT interpret ``False``
    as "try again"; it means the answer to this operator's question is a
    restart.
    """
    global _latched
    if not dismissal_available():
        log.warning(
            "commissioning state: refusing to dismiss. Either nothing latched, "
            "the gate is already open, or configuration arrived since the "
            "latch (an import or a restore) -- in which case the dispatchers "
            "in memory still hold defaults and opening the gate would write "
            "them over what was just restored. Restart the machine."
        )
        return False

    marker = dismissal_marker_path(_latch_dir)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "This machine was NOT commissioned from a capture. An operator "
            "dismissed the uncommissioned warning in the UI, undertaking to "
            "enter the machine's configuration by hand.\n"
            "While this file exists, reflex-ui saves settings on this card.\n"
            "Delete it to put the warning back and stop saving.\n"
        )
    except OSError as e:
        log.error(
            f"commissioning state: cannot write {marker} ({e}); the gate stays "
            f"shut rather than opening for one session and forgetting"
        )
        return False

    _latched = True
    log.warning(
        f"commissioning state: DISMISSED by the operator. {marker} written; "
        f"config writes are now allowed and stay allowed on the next boot. "
        f"Every value saved from here is this machine's baseline."
    )
    return True


def latched() -> bool:
    """The answer everything else gates on.

    ``True`` when :func:`latch` was never called -- see the module docstring on
    why the default is "commissioned" rather than the safer-sounding opposite.
    """
    return True if _latched is None else _latched


def clear_latch() -> None:
    """Forget the latched answer AND the directory baseline. Tests only.

    THE ONE RE-LATCH PATH IS :func:`dismiss`, AND IT IS STILL NOT AN IMPORT
    PATH. Until 2026-09-22 this docstring said there was no way to re-open the
    gate at all, because an in-app import (``Setup > Backup``) writes the
    restored YAML to disk while every dispatcher in memory is still holding
    its in-code defaults -- re-opening the gate at that moment lets the next
    property change write those defaults straight back over the values just
    restored. That hazard is unchanged and :func:`dismissal_available` is
    where it is now enforced, by measuring the directory rather than by
    refusing everybody. What :func:`dismiss` adds is the OTHER operator: the
    one with no capture to restore, who is going to measure this lathe and
    type the numbers in, and who previously had no way to use the app at all.
    """
    global _latched, _latch_dir, _yaml_at_latch
    _latched = None
    _latch_dir = None
    _yaml_at_latch = None
