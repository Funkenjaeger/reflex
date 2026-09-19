"""Small platform helpers.

Until 2026-09-19 this module also probed the root device, disk, partition
and block sizes (findmnt / lsblk / df) for the System screen's upstream
partition-resize feature. That feature and its readout were removed -- the
operator UI does no system administration -- and free space now comes from
``shutil.disk_usage``, so only the formatter is left.
"""


def format_bytes(n: int | None) -> str:
    """Format a byte count as a human-readable string (GiB or MiB)."""
    if n is None:
        return "N/A"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GiB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.2f} MiB"
    return f"{n} bytes"
