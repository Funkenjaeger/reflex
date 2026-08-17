# Deploying reflex-ui to a Raspberry Pi (fresh OSPI install)

This documents the full manual procedure to deploy **reflex-ui** to a Raspberry Pi
that ships with the OSPI image running the original **rotary-controller-python (rcp)**.
The goal: run reflex-ui **from source** out of `/reflex-ui/`, auto-started at boot via
systemd, replacing rcp as the boot application.

> A future task tracks scripting this (bash or, preferably, an Ansible playbook) —
> see `todo.md`. The companion files in this directory are the source of truth for
> that automation:
> - [`start.sh`](start.sh) — launch wrapper (Kivy env + venv activate + run)
> - [`reflex-ui.service`](reflex-ui.service) — systemd unit

## How rcp is set up on the stock image (what we're replacing)

- **`rcp.service`** (`/etc/systemd/system/rcp.service`) — enabled, `WantedBy=multi-user.target`.
- It runs **`/start.sh`** as **root**, which exports `KCFG_*` Kivy graphics vars,
  activates `/rotary-controller-python/.venv`, and runs the app.
- **No X server / lightdm** is running — Kivy renders directly via **KMS/DRM**, so
  there is no `DISPLAY`. The service runs as **root** (needed for DRM access and for
  writing logs to `/var/log`). We mirror all of this for reflex-ui.

## Prerequisites / facts about the target

- OS: Raspberry Pi OS / Raspbian 13 (trixie). Kernel aarch64, but the **userland is
  32-bit (armhf)** — `uv` installs its `armv7-unknown-linux-gnueabihf` build. Harmless.
- System Python: 3.13.x. `pyproject.toml` requires `>=3.10,<4.0`.
- All system-level SDL2/EGL/DRM libraries Kivy needs are **already present** (rcp uses
  the same Kivy), so no apt packages are required.
- `sudo` requires a password on the stock image — the root steps below are called out
  explicitly so they can be run interactively.

## Hostname resolution note (deploying from WSL)

`raspberrypi.local` (mDNS) often won't resolve from WSL. Resolve the IP from Windows
(`powershell.exe -Command "ping raspberrypi.local"`) and use the IP for SSH/git.
Substitute `<PI>` below with that IP (or hostname if mDNS works for you).

---

## Procedure

### 1. Install uv on the Pi (as the `default` user — no sudo)

```bash
ssh default@<PI> 'curl -LsSf https://astral.sh/uv/install.sh | sh'
# uv lands in ~/.local/bin/uv
```

### 2. Create the target directory (ROOT — run on the Pi)

```bash
sudo mkdir -p /reflex-ui && sudo chown default:default /reflex-ui
```

Owning it as `default` lets us push over SSH and run `uv sync` without root.
(The service still runs as root; root can read default-owned files fine.)

### 3. Transfer the source — git over SSH, no GitHub required

We deploy the **committed** state of the working branch via a push-to-deploy git
remote — no GitHub round-trip.

On the Pi, initialize `/reflex-ui` as a repo whose working tree updates on push:

```bash
ssh default@<PI> 'git init -b <BRANCH> /reflex-ui && \
  git -C /reflex-ui config receive.denyCurrentBranch updateInstead'
```

`receive.denyCurrentBranch=updateInstead` makes the working tree check out
automatically when you push to the branch it has checked out. Initializing with
`-b <BRANCH>` (e.g. `ui-facelift`) ensures HEAD matches what you push, so the first
push populates the tree.

From your dev machine:

```bash
git remote add pi default@<PI>:/reflex-ui      # one-time
git push pi <BRANCH>
```

**Iterating later:** commit on `<BRANCH>`, then `git push pi <BRANCH>` and restart the
service (step 7). Only committed content transfers — uncommitted/untracked files do not.

> **Alternative: rsync the working tree** (use if you need uncommitted/untracked files
> on the Pi). Mirrors exactly what runs locally:
> ```bash
> rsync -av --delete \
>   --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
>   --exclude '*.pyc' --exclude 'previews/' \
>   ./ default@<PI>:/reflex-ui/
> ```

### 4. Build the venv from source (as `default` — no sudo)

```bash
ssh default@<PI> 'cd /reflex-ui && ~/.local/bin/uv sync'
# creates /reflex-ui/.venv with all dependencies
```

### 5. Install the launch wrapper

`deploy/start.sh` ships in the repo, so after step 3 it's already at
`/reflex-ui/deploy/start.sh`. Place it at `/reflex-ui/start.sh` and make it
executable:

```bash
ssh default@<PI> 'cp /reflex-ui/deploy/start.sh /reflex-ui/start.sh && chmod +x /reflex-ui/start.sh'
```

(If deploying via committed-only push and `deploy/` isn't committed yet, write
`/reflex-ui/start.sh` directly from the contents of `deploy/start.sh` in this repo.)

### 6. Install the systemd unit + switch boot app from rcp to reflex-ui (ROOT — on the Pi)

```bash
sudo cp /reflex-ui/deploy/reflex-ui.service /etc/systemd/system/reflex-ui.service
sudo systemctl daemon-reload
sudo systemctl disable --now rcp.service       # stop & disable old app (kept on disk)
sudo systemctl enable reflex-ui.service        # start at boot
```

### 7. Start and verify

```bash
sudo systemctl restart reflex-ui.service       # or reboot
systemctl status reflex-ui.service --no-pager
journalctl -u reflex-ui.service -b --no-pager | tail -50
```

The UI should appear on the attached display. If not, the journal shows Python
tracebacks (import errors, etc.).

---

## Rollback to rcp

```bash
sudo systemctl disable --now reflex-ui.service
sudo systemctl enable --now rcp.service
```

`/rotary-controller-python/` and `rcp.service` are left intact, so this is instant.

## Notes / gotchas

- **Persisted settings live in `/var/lib/reflex-config`**, not `/root/.config/reflex`.
  `start.sh` exports `REFLEX_CONFIG_DIR` to put the commissioned machine config
  somewhere the operator account can read, diff and back up. Migrating an existing
  install: stop the service, `cp -a /root/.config/reflex/. /var/lib/reflex-config/`,
  then start — copy rather than move, so the old directory remains a rollback until
  the new location is verified.
- **Run-user is root**, matching rcp — required for KMS/DRM and `/var/log` writes.
  Don't switch to a non-root user without solving DRM/`video`+`render` group access
  and a writable log dir.
- **`python -m reflex.main`** relies on `WorkingDirectory=/reflex-ui/` (set in the
  unit) to put the `reflex` package on `sys.path`.
- The launch wrapper sets no `DISPLAY` on purpose — Kivy uses KMS/DRM directly.
- The committed-only push means any in-progress (uncommitted) work is **not** deployed;
  commit it first or use the rsync alternative.
