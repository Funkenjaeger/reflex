# Reflex UI

A **Kivy-based Digital Read-Out (DRO) and Electronic Leadscrew (ELS) controller UI** for lathes, designed to run on Raspberry Pi or desktop environments (Windows, macOS, Linux). Interfaces via RS-485/Modbus RTU with a dedicated STM32-based control board running the associated [Reflex firmware](https://github.com/Funkenjaeger/reflex-fw).

This software is based on the [rotary-controller-python (RCP)](https://github.com/bartei/rotary-controller-python) project. It — along with the corresponding firmware project — was hard-forked from the original primarily due to natural divergence that followed from a focus on lathe use cases, where the original rotary-controller was designed for CNC-style rotary table use cases.

> ### About this branch
>
> `main` holds the **`v1.0.0` release snapshot** (2026-06-19). Development continued on
> **[`dev`](../../tree/dev)**, this repository's default branch, which carries the later work —
> ELS safety fixes, the UI facelift, Raspberry Pi deployment, and CI-generated screenshots.
>
> **`dev` is the authoritative branch. Read it for current documentation and the full feature
> list:** [README on `dev`](../../blob/dev/README.md).
>
> Note that `main` is not an ancestor of `dev`: it branched at `488ff75` (2026-06-16) and received
> only the `1.0.0` version stamp, while `dev` went on to `1.0.0-rc.1` and `1.0.0-rc.2`. The two
> histories were never reconciled.

## Related repositories

| Repository | Role |
|---|---|
| [reflex-fw](https://github.com/Funkenjaeger/reflex-fw) | STM32 firmware — the other half of this system |
| [rotary-controller-python](https://github.com/Funkenjaeger/rotary-controller-python) | Deprecated pre-fork ancestor of this project |
| [rotary-controller-f4](https://github.com/Funkenjaeger/rotary-controller-f4) | Deprecated pre-fork ancestor of the firmware |

## License

This branch predates the `LICENSE` file; see [LICENSE on `dev`](../../blob/dev/LICENSE).
