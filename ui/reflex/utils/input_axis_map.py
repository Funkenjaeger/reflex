"""Which axis each scale input feeds.

WHY THIS EXISTS. Inputs and axes are configured on separate screens, and
nothing on the input side said what an input was FOR. To change a scale you
had to drill two levels into Axes to learn which input an axis used, back out,
then drill into that input -- every time. Evan, 2026-08-31, after the X scale
turned out to be misprovisioned: working out which config file was even the X
axis was a non-trivial step of that job.

READ-ONLY, DELIBERATELY. Evan: "I'm reluctant to create multiple disjoint ways
of doing the same thing, so I'm not saying that the axis assignments per scale
need to be settable from within the inputs menu, just read-only there for now."
This module answers "what is this input for"; it never changes anything.

A DISPLAY JOIN OVER STATE THAT ALREADY EXISTS -- no new configuration. Every
axis carries a transform whose `contributions` name the input indices it reads
(Identity uses one, Sum uses two), and `is_provisioned` says whether anyone has
named the axis. The join is those two facts inverted.

ONE INPUT CAN FEED SEVERAL AXES, which is the case worth getting right: a
summed axis's second contributor is typically some other axis's primary input,
so input N legitimately appears under both. The label lists them all rather
than picking one.
"""


def _axis_entries(axes):
    """(input_index, axis_name, is_sum) for every provisioned contribution."""
    for ax in axes or ():
        if not getattr(ax, "is_provisioned", False):
            continue
        transform = getattr(ax, "transform", None)
        if transform is None:
            continue
        try:
            indices = sorted(transform.input_indices)
        except (AttributeError, TypeError):
            continue
        is_sum = len(indices) > 1
        name = getattr(ax, "axis_name", "") or ""
        if not name:
            continue
        for idx in indices:
            yield idx, name, is_sum


def input_axis_labels(axes) -> dict:
    """Map input index -> short label naming the axis or axes it feeds.

    Sum membership is marked, because "this input IS the X axis" and "this
    input is half of what X is derived from" are different facts and only the
    second one makes a lone reading of the input misleading.

    An input no provisioned axis claims is simply absent from the map. Callers
    render that as blank rather than as "none" -- an unused input on a
    four-input board is ordinary, not a problem to announce.
    """
    by_index = {}
    for idx, name, is_sum in _axis_entries(axes):
        entry = f"{name} (sum)" if is_sum else name
        bucket = by_index.setdefault(idx, [])
        if entry not in bucket:
            bucket.append(entry)
    return {idx: ", ".join(sorted(names)) for idx, names in by_index.items()}


def input_axis_label(axes, index: int) -> str:
    """The label for one input, or "" when nothing provisioned claims it."""
    return input_axis_labels(axes).get(index, "")
