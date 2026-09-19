System Storage
==============

View storage information on a Raspberry Pi. This screen is only
available on Raspberry Pi hardware. Everything here is read-only.

## Storage Devices

Read-only information about the Pi's storage:
- **Root Device** — the partition mounted as /
- **Disk Device** — the physical storage device (e.g., /dev/mmcblk0)
- **Partition Number** — which partition on the disk
- **Disk Size** — total capacity of the storage device
- **Partition Size** — current size of the root partition

## Filesystem Usage

Current disk space usage of the root filesystem:
- **Total** — total available space on the partition
- **Used** — space currently in use
- **Free** — remaining free space

### Refresh Storage Info
Re-reads the current storage state.

## Notes

- The root partition is expanded to fill the card automatically on
  first boot; nothing needs doing here.
- Only available on Raspberry Pi (hidden on other platforms)
