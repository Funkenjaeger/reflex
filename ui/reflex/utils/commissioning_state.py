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

``docs/provisioning.md`` states the same thing in prose: "a non-empty
``Els-0.yaml`` must be present, and there must be at least 15 ``.yaml`` files
(the live machine carried 19 at last count)". :func:`is_commissioned` is that
gate, re-expressed once in Python against a LIVE config directory rather than a
capture. Two answers that must agree are two answers that will eventually
disagree, so the numbers here are named constants with the shell's values, and
:mod:`tests.utils.test_commissioning_state` pins the boundary cases the shell's
own ``deltas/tests/test-restore-contract.sh`` pins.

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
nobody has done that yet -- not a task to be worked around.
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
#: to count as whole. 15 with the live machine at 19 -- the slack is there so
#: that removing a dispatcher does not read as a partial capture, and it is the
#: restore script's number rather than a second opinion.
MIN_YAML_FILES = 15


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


#: The latched answer for this process. ``None`` means nobody has asked.
_latched: bool | None = None


def latch(directory=None) -> bool:
    """Evaluate :func:`is_commissioned` ONCE and remember it. Returns it.

    Called from :meth:`reflex.app.MainApp.build` before any
    ``SavingDispatcher`` is constructed. Idempotent in effect but not in
    intent: calling it twice re-reads the directory, which is why exactly one
    caller in the application does.
    """
    global _latched
    _latched = is_commissioned(directory)
    if _latched:
        log.info("commissioning state: commissioned")
    else:
        where = Path(directory) if directory is not None else config_dir()
        log.warning(
            f"commissioning state: UNCOMMISSIONED -- {where} does not meet the "
            f"restore contract (non-empty {ANCHOR_FILE} plus >= "
            f"{MIN_YAML_FILES} .yaml files). Config writes are refused and the "
            f"UI says so on screen."
        )
    return _latched


def latched() -> bool:
    """The answer everything else gates on.

    ``True`` when :func:`latch` was never called -- see the module docstring on
    why the default is "commissioned" rather than the safer-sounding opposite.
    """
    return True if _latched is None else _latched


def clear_latch() -> None:
    """Forget the latched answer. For tests, and only for tests.

    There is deliberately no "re-latch after the operator imports a bundle"
    path. An in-app import (``Setup > Backup``) writes the restored YAML to
    disk, but every dispatcher in memory is still holding its in-code defaults;
    re-opening the gate at that moment would let the next property change write
    those defaults straight back over the values just restored. The restore
    contract's own ordering -- restore, THEN run -- is the answer, and the
    banner says to restart.
    """
    global _latched
    _latched = None
