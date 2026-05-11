# CLAUDE.md — lianli_207_lcd (MyKEms fork)

Load-once context for Claude sessions touching this repo. Complements `AGENTS.md` (dev guidelines + build commands) — this file documents **why** the fork diverges from upstream, **how** it's deployed, and **what** design decisions were made.

If you only have time for one section, read **Fork rationale** and **Don'ts**.

---

## Fork rationale (vs `deyloop/lianli_207_lcd`)

This is a hardened, single-host-deployed fork of `deyloop/lianli_207_lcd` (Anurup Dey, India, MIT). Upstream is functionally complete but ships with three issues that mattered to the deploy target (a 24/7 Linux LLM inference rig running as a single trusted user):

1. **Service ran as root.** Upstream `systemd/lcd-driver.service.template` had no `User=` directive, so a Python USB driver got full root privileges for what's a non-privileged workload.
2. **udev `MODE="0666"`** — world-readable+writable USB device node.
3. **Hardcoded `"RTX 5070 Ti"` / `"deyolith"` labels** — upstream author's own rig, not ours.
4. **README claimed "Network I/O statistics"** that the renderer never drew (likely a previously-removed feature whose docs weren't updated).
5. **CPU package power blocked under hardened systemd** — `subprocess.check_output(["cat", "/sys/.../energy_uj"])` floods journal with `cat: Permission denied` once a second when the file is the kernel's default `0400 root:root` post-CVE-2020-8694.

The fork addresses all five and adds:
- Hardware-detected GPU label (`nvidia-smi --query-gpu=name`) + hostname-based title, both overridable via env (`LCD_GPU_LABEL`, `LCD_TITLE`).
- Total **System Power** readout in the title strip (CPU package + GPU board + optional `LCD_BASELINE_WATTS` env constant for the unmeasurable rest).
- Dashed thermal threshold lines on the temp graphs, autodetected per HW from `psutil.sensors_temperatures()[…].high` (CPU) and `nvidia-smi -q -d TEMPERATURE` "GPU Max Operating Temp" (GPU).
- Dependabot config for `pip` + `github-actions` ecosystems.

Upstream commit lineage is preserved (`git log --oneline` shows the merge point cleanly). Patches are individually upstream-able as separate PRs if the maintainer wants them.

---

## Deployment context (single host: `rig-3090`)

This fork is deployed on **one** machine. If you're working on this code, assume the target environment is:

| | |
|---|---|
| **Host** | `rig-3090.ghost-fort.ts.net` (Tailscale MagicDNS suffix) |
| **OS** | Ubuntu 24.04.3 LTS + HWE kernel 6.17 |
| **CPU** | Intel i5-13500 (TjMax 100°C, `coretemp.high` = 80°C) |
| **GPU** | RTX 2060 SUPER 8 GB now → RTX 3090 24 GB after hardware swap (the driver auto-detects the new name via `nvidia-smi`, no code change) |
| **Case** | Lian Li LANCOOL 207 (front-panel LCD = the target device) |
| **PSU** | ASUS ROG Loki SFX-L 750W Platinum — **no PMBus telemetry**, so wall-socket draw is approximated via `LCD_BASELINE_WATTS` constant; an external smart-plug integration is on the maybe-list but not implemented. |

Source clone lives at `/mnt/data/src/lianli_207_lcd/` on the rig. The host has a separate **`Local AI RIG`** workspace (different repo / Obsidian vault) where the broader rig setup is journaled — entries `Akce 15` (initial install), `Akce 16` (RAPL + System Power), `Akce 17` (Dependabot + threshold lines) cover the LCD work.

---

## LANCOOL 207 Digital LCD — hardware target

| Property | Value |
|---|---|
| USB Vendor ID | `0x1cbe` (Luminary Micro) |
| USB Product ID | `0xa065` |
| Linux device name | `lianli-207LCD-1.0` |
| Resolution | 1472 × 720 (landscape input, rotated -90° to portrait 720 × 1472 on hardware) |
| Refresh | 60 Hz on the panel; driver pushes 1 frame/sec for stats |
| Endpoints | Bulk OUT `0x01`, Bulk IN `0x81` (interface 0) |
| Encryption | **DES-CBC**, 8-byte key + IV = `b'slv3tuzx'` (hardcoded in firmware, reverse-engineered) |
| Max image bytes | 512,000 bytes per frame (JPEG or PNG) |
| Chunk size | 4096 bytes per USB write |

There is **also** a secondary device `1cbe:f000` named `USB-Daemon` on the same case — this is the case-internal USB hub controller, **not** something we talk to.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────┐
│  systemd-tmpfiles-setup     → relaxes /sys/.../energy_uj    │
│                                from 0400 to 0444 (boot)     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  systemd lcd-driver.service                                 │
│  User=${SUDO_USER}, SupplementaryGroups=plugdev             │
│  CapabilityBoundingSet=(empty), SystemCallFilter=...        │
│  ExecStart=/opt/lianli-lcd/bin/lcd-driver <bg.jpg>          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  lcd_driver/main.py (single module)                         │
│                                                             │
│  startup:                                                   │
│   ├─ TITLE = LCD_TITLE | socket.gethostname()               │
│   ├─ GPU_NAME = LCD_GPU_LABEL | nvidia-smi --query-gpu=name │
│   ├─ BASELINE_W = float(LCD_BASELINE_WATTS | 0)             │
│   ├─ CPU_WARN_TEMP = psutil sensors_temperatures().high     │
│   ├─ GPU_MAX_TEMP = nvidia-smi -q -d TEMPERATURE "Max Op"   │
│   ├─ libusb find(VID=0x1cbe, PID=0xa065)                    │
│   ├─ detach kernel driver, claim interface 0                │
│   ├─ send Rotate(0), SyncClock, StopClock                   │
│   └─ push background JPEG (cmd 0x65) once                   │
│                                                             │
│  loop (1 Hz):                                               │
│   ├─ drain Bulk IN (clear any ACKs)                         │
│   ├─ poll CPU% / freq / temp / power (psutil + RAPL native) │
│   ├─ poll GPU util / temp / power / VRAM (nvidia-smi CSV)   │
│   ├─ poll RAM (psutil.virtual_memory)                       │
│   ├─ append to deque history buffers (180 samples = 3 min)  │
│   ├─ render PNG overlay (1472×720, then rotate -90°)        │
│   │   ├─ title strip (hostname, SYS XX W, timestamp)        │
│   │   ├─ CPU bars (USAGE, TEMP)                             │
│   │   ├─ GPU bars (USAGE w/ dynamic GPU name, TEMP)         │
│   │   ├─ CPU 3-min graph + threshold line at CPU_WARN_TEMP  │
│   │   ├─ GPU 3-min graph + threshold line at GPU_MAX_TEMP   │
│   │   └─ bottom bars (RAM, GPU MEMORY)                      │
│   ├─ DES-CBC encrypt 500-byte header → 512 bytes            │
│   ├─ push chunked (4096 B/chunk) + StartPlay (0x79)         │
│   └─ sleep until next 1-second tick                         │
└─────────────────────────────────────────────────────────────┘
```

### Key design constraints

- **Single thread, single loop.** No async, no GIL games. 1 Hz is well within budget on any modern CPU.
- **All bytes shipped to LCD are encrypted** — even simple commands like Rotate go through DES-CBC. The 8-byte `slv3tuzx` key is hardcoded in Lian Li firmware; this is obfuscation, not security crypto. Don't think you can change the algorithm.
- **Image size cap is firmware-enforced.** Hit 512,000 bytes and the LCD silently drops the frame. JPEG quality 95 keeps a typical wallpaper around 17-20 KB; full-frame stats PNG is around 30-50 KB. We have lots of headroom but assertions still bound it.
- **Chunked writes must use `clear_halt()` on USBTimeoutError** — the device occasionally stalls and only recovers if you reset the endpoint. Three retries, then drop the frame.
- **`drain_in()` at top of each loop iteration** — flushes any leftover ACKs from previous tick. Skipping this leads to interleaved replies that confuse subsequent reads.

---

## Configuration surface

### Environment variables (set in systemd unit or shell)

| Variable | Default | What |
|---|---|---|
| `LCD_TITLE` | `socket.gethostname()` | Big white text on the left of the title strip |
| `LCD_GPU_LABEL` | `nvidia-smi --query-gpu=name` first GPU, or `"GPU"` fallback | Suffix in "GPU USAGE (...)" bar |
| `LCD_BASELINE_WATTS` | `0` | Constant added to CPU+GPU for the SYS power readout — accounts for mb/RAM/fans/SSD/PSU loss. Typical desktop ~25-50 W. |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR — passed straight to `logging.basicConfig` |

### Installed paths

| Path | Owner | Purpose |
|---|---|---|
| `/opt/lianli-lcd/` | root | uv venv (Python 3.12 + deps) |
| `/opt/lianli-lcd/bin/lcd-driver` | root | Entry point shim (uv-installed script) |
| `/etc/systemd/system/lcd-driver.service` | root:root 0644 | Main unit (generated from `systemd/lcd-driver.service.template`) |
| `/etc/systemd/system/lcd-driver.service.d/*.conf` | root:root 0644 | Drop-in overrides (e.g. `baseline-watts.conf` setting `LCD_BASELINE_WATTS`). Survive `reinstall.sh` since that script only re-installs the Python package. |
| `/etc/udev/rules.d/99-lianli-lcd.rules` | root:root 0644 | USB perms for `1cbe:a065` → `GROUP=plugdev`, `MODE=0660`, `TAG+=uaccess` |
| `/etc/tmpfiles.d/99-lianli-lcd-rapl.conf` | root:root 0644 | Sets `/sys/class/powercap/intel-rapl:0/energy_uj` and AMD equivalent to `0444` at boot (see CVE-2020-8694 note below) |

### Customizing the readout on this rig (no code change)

```bash
# Different displayed name
sudo systemctl edit lcd-driver
# [Service]
# Environment=LCD_TITLE=My Rig
# Environment=LCD_GPU_LABEL=RTX 3090 Suprim X
# Environment=LCD_BASELINE_WATTS=40

sudo systemctl restart lcd-driver
```

---

## HW detection logic (read this before touching `_detect_*` functions)

Three values are autodetected once at module import time and cached for the lifetime of the process:

1. **`GPU_NAME`** — `subprocess.check_output(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])`. Falls back to literal `"GPU"` on any exception. Re-detected only on service restart, so after a GPU swap you need `sudo systemctl restart lcd-driver`.

2. **`CPU_WARN_TEMP`** — `psutil.sensors_temperatures()` keyed by sensor name in order: `coretemp` (Intel), `k10temp` (AMD), `cpu_thermal` (some ARM). First entry's `.high` field (kernel-reported throttle warning, typically TjMax - 20°C). Returns `None` if no matching sensor — threshold line is then suppressed (graceful skip).

3. **`GPU_MAX_TEMP`** — `nvidia-smi -q -d TEMPERATURE` parsed line-by-line for `"GPU Max Operating Temp"`. The query also returns `"Memory Max Operating Temp"` which is often `N/A`; we filter that out. Returns `None` on any failure.

**Important**: detection runs in the **module scope**, so if `nvidia-smi` is slow or absent at startup, the service start takes 2 seconds longer (the timeout). That's acceptable for a service that runs forever. Don't move detection into the render loop — it'd cost a subprocess per second.

---

## Display layout (canvas: 1472 × 720, rotated to portrait on hardware)

```
   PAD=28                                                    canvas_w-PAD
   ┌────────────────────────────────────────────────────────┐
   │ TITLE_H=70: hostname (font_large 48)   SYS XX W  (med) │  ← title strip
   │                                        21:00:00 11 May │
   ├────────────────────────────────────────┬───────────────┤
   │ CPU_CARD_H=176                         │ GPU_CARD_H    │
   │   CPU USAGE bar (font_small label)     │  GPU USAGE    │  ← bars (cards)
   │   CPU TEMP bar  (color-coded)          │  GPU TEMP     │
   ├────────────────────────────────────────┼───────────────┤
   │                                        │                │
   │  3-min CPU history (CPU%, Temp, RAM%)  │ 3-min GPU hist │  ← graphs (cards)
   │  ─ ─ ─ ─ ─ ⚠ 80°  threshold line       │  ─ ─ ─ ⚠ 89°  │
   │                                        │                │
   ├────────────────────────────────────────┼───────────────┤
   │ RAM bar (used / total GB, %)           │ GPU MEMORY    │  ← bottom strip (cards)
   └────────────────────────────────────────┴───────────────┘
```

Layout constants are at the top of `create_stats_png()`. Changing `TITLE_H`, `CPU_CARD_H`, `RAM_NET_H` cascades into `GRAPH_H` automatically.

### Color palette in use

| Where | Color (RGB) | Note |
|---|---|---|
| Card fill | `(10, 10, 10, 150-185)` | semi-transparent dark with rounded corners |
| Gridlines | `(60, 60, 60, 120)` | light gray, 0/25/50/75/100 |
| CPU% line / bar | `(0, 180, 255)` | cyan-blue |
| GPU% line / bar | `(255, 140, 0)` | orange |
| Temp line | `(0, 210, 110)` | lime green |
| RAM% / VRAM% line | `(160, 80, 255)` | purple |
| SYS power text | `(255, 200, 0)` | amber-yellow — most prominent in title strip |
| Threshold line | `(255, 70, 70, 180)` | dashed red, label `(255, 90, 90, 220)` |
| Bar color-coded thresholds (CPU) | green <65°C, yellow 65-80°C, red >80°C | hardcoded in `draw_bar` callers |
| Bar color-coded thresholds (GPU) | green <70°C, yellow 70-85°C, red >85°C | hardcoded in `draw_bar` callers |

The bar threshold ranges are **separate** from `CPU_WARN_TEMP` / `GPU_MAX_TEMP` (which are graph dashed lines). They're hardcoded because they represent visual "warning zones" not specific HW limits. If you ever want them HW-aware too, route them through the detected constants.

---

## How to extend

### Add a new sensor (e.g. NVMe temperature)

1. Source the value in the polling section of `create_stats_png()`:
   ```python
   try:
       nvme_temp = psutil.sensors_temperatures().get("nvme", [None])[0].current
   except Exception:
       nvme_temp = None
   ```
2. Append to history if you want a graph line:
   ```python
   history["nvme_temp"].append(nvme_temp or 0.0)
   ```
   (and add `"nvme_temp": deque(...)` to the `history` dict at module level)
3. Either render as a bar (call `draw_bar(...)` somewhere in the bottom strip) or as a new series in `draw_graph(..., series=[...])`.

### Add a new env var

Read at module load (top of file, near `BASELINE_W`):
```python
NEW_THING = os.environ.get("LCD_NEW_THING", "default")
```
Document in the README under "Features", and consider adding it to this CLAUDE.md's "Configuration surface" table.

### Change layout

Edit constants in `create_stats_png()`:
- `PAD`, `GUTTER` — outer padding and inter-card spacing
- `col1_w` — left column width (default 690); right column width is derived
- `TITLE_H`, `CPU_CARD_H`, `GPU_CARD_H`, `RAM_NET_H` — heights; `GRAPH_H` derives

Test on the real LCD after every layout tweak — the panel does heavy chroma compression and what looks fine on a desktop preview can be illegible on the front-panel screen at typical viewing distances.

### Add a new USB command (e.g. set brightness)

The protocol documented in `PROTOCOL.md` shows 7 known command bytes (0x0D rotate, 0x33 sync clock, 0x34 stop clock, 0x65 jpeg, 0x66 png, 0x79 start play). Other bytes likely do things — Lian Li's Windows client probably issues brightness, sleep, and orientation commands we haven't reverse-engineered. The general shape is:
```python
header = build_base_cmd(NEW_CMD_BYTE)   # bytes after offset 8 = command-specific
struct.pack_into('>I', header, 8, ...)  # or whatever the command needs
send_and_read(encrypt_header(header), "MyCommand")
```
USB-sniff Wireshark on Windows during L-Connect 3 operations to figure out the layout, then update `PROTOCOL.md`.

---

## Security posture

### Hardening directives applied (`systemd/lcd-driver.service.template`)

| Directive | Effect |
|---|---|
| `User=${SUDO_USER}` | Service runs as the human who ran `install.sh`, not root |
| `SupplementaryGroups=plugdev` | Grants USB access via the udev `GROUP="plugdev"` rule |
| `NoNewPrivileges=true` | setuid binaries can't escalate even if invoked |
| `ProtectSystem=strict` | `/usr` `/boot` `/efi` mounted read-only; `/etc` `/var` `/sys` `/proc` invisible-or-read-only |
| `ProtectHome=read-only` | `/home` read-only (needed to read background image if it lives there) |
| `PrivateTmp=true` | private `/tmp` per service |
| `ProtectKernelTunables=true` | `/sys` and `/proc/sys` are read-only |
| `ProtectKernelModules=true` | `modprobe` blocked |
| `ProtectKernelLogs=true` | `kmsg` blocked |
| `ProtectControlGroups=true` | cgroup interface read-only |
| `ProtectClock=true` | wall clock writes blocked |
| `ProtectHostname=true` | hostname writes blocked |
| `RestrictNamespaces=true` | new namespaces blocked |
| `RestrictRealtime=true` | `SCHED_FIFO/RR` blocked |
| `RestrictSUIDSGID=true` | mounting suid filesystems blocked |
| `LockPersonality=true` | `personality(2)` syscall locked |
| `RestrictAddressFamilies=AF_UNIX AF_NETLINK` | only Unix sockets and netlink — **no network sockets** |
| `SystemCallFilter=@system-service` | ~250 syscalls whitelist (general purpose, sufficient for our workload) |
| `CapabilityBoundingSet=` (empty) | all capabilities dropped |
| `AmbientCapabilities=` (empty) | no ambient caps either |

What this means in practice: even if the driver were exploited via, say, a crafted JPEG into PIL, the attacker can't open network sockets, load modules, change the clock, write to `/usr`, or escalate privileges. The blast radius is the user's home directory (read-only) and the USB LCD itself.

### CVE-2020-8694 / Platypus relaxation (`tmpfiles.d`)

The installer drops a `tmpfiles.d` snippet that relaxes `/sys/class/powercap/intel-rapl:0/energy_uj` from the kernel's default `0400 root:root` to `0444`. This is the file the side-channel attack (Platypus, 2020) targeted: by sampling RAPL at high frequency (~1 kHz) while an attacker-controlled workload runs alongside a victim's crypto code on the same CPU package, you can leak AES keys.

Why we relaxed it here:
- We poll at **1 Hz**, three orders of magnitude below the attack's signal-to-noise threshold.
- The host is **single-user** — there's no attacker process to co-locate.
- The benefit (System Power readout that's worth showing) is real and immediate.

Don't reflexively undo this when you see it in audit. The decision and threat model are documented in commit `f2101c7` and in the user's separate Build-Log `Akce 16`.

If you're ever in doubt: removing the `tmpfiles.d` snippet via `sudo rm /etc/tmpfiles.d/99-lianli-lcd-rapl.conf && sudo systemd-tmpfiles --create` restores `0400` immediately, and the driver gracefully falls back to "no CPU watts displayed".

---

## Don'ts

- ❌ **Don't add `set -e` blindly to `install.sh` pipelines without `|| true` on filters.** Earlier iteration had `grep -v "^\[sudo\]"` that returned exit 1 on success (no matches) and killed the script before service registration. Use `sudo -S -p ''` to suppress sudo's prompt instead of grep-filtering it after the fact.
- ❌ **Don't try to bypass the DES-CBC handshake.** The LCD firmware requires it; sending an unencrypted header is silently ignored, not rejected.
- ❌ **Don't `subprocess.check_output(["cat", "/sys/..."])` for sensor reads** — the child's stderr goes to the systemd journal, so any permission failure logs spam. Use Python `open()` and catch `PermissionError` / `FileNotFoundError` silently. (Fixed in commit `cea6640`, don't reintroduce.)
- ❌ **Don't move HW detection into the render loop.** Detection is module-scope on purpose — 1 Hz × `nvidia-smi -q -d TEMPERATURE` subprocess would burn CPU and spam logs needlessly.
- ❌ **Don't enable `PrivateDevices=true` in the systemd unit.** That hides `/dev/bus/usb/*`, breaking the driver. The current `PrivateDevices=false` (default) is correct.
- ❌ **Don't print or log the DES key.** It's not secret (anyone who buys the case learns it from this codebase), but it's still pointless noise in logs.
- ❌ **Don't add a `network` series to the renderer without first widening the bottom strip.** Upstream tried, abandoned it, and left the README claim behind — that's the bug the README fix `c9f7aeb` cleaned up.
- ❌ **Don't run the driver from inside a different uv venv than `/opt/lianli-lcd`.** The systemd `ExecStart` is hardcoded to that path; if you `pip install` into a user-mode venv during development, the service still uses the system one. Use `reinstall.sh` to sync them.
- ❌ **Don't push to `master` without signed commits.** This repo's commits are SSH-signed via 1Password agent and verified by GitHub; the verification chain is the audit trail. `commit.gpgsign=true` is in user's global git config.

---

## Quick reference (commands you'll re-type)

```bash
# Status + tail logs
systemctl status lcd-driver
sudo journalctl -u lcd-driver -f

# Apply code change without disturbing service file / udev / tmpfiles
cd /mnt/data/src/lianli_207_lcd
git pull
sudo ./reinstall.sh

# Apply install.sh / template / udev / tmpfiles change
sudo ./install.sh /path/to/wallpaper.jpg     # idempotent, overwrites configs

# Verify hardening + drop-ins
systemctl show lcd-driver -p User,Group,SupplementaryGroups,Environment,CapabilityBoundingSet

# Verify USB perms
ls -la /dev/bus/usb/$(lsusb | awk '/1cbe:a065/ {print $2"/"$4}' | tr -d ':')

# Verify RAPL readability (proves tmpfiles.d applied)
ls -la /sys/class/powercap/intel-rapl:0/energy_uj   # expect: -r--r--r-- root root

# Probe what the driver currently has cached
/opt/lianli-lcd/bin/python -c "
from lcd_driver.main import TITLE, GPU_NAME, CPU_WARN_TEMP, GPU_MAX_TEMP, BASELINE_W
print(TITLE, GPU_NAME, CPU_WARN_TEMP, GPU_MAX_TEMP, BASELINE_W)
"

# Update wallpaper without touching anything else
sudo cp ~/Pictures/new.jpg /mnt/data/src/lianli_207_lcd/wallpaper.jpg
sudo systemctl restart lcd-driver

# Change one env without editing template (drop-in survives reinstall)
sudo systemctl edit lcd-driver
# [Service]
# Environment=LCD_BASELINE_WATTS=45
sudo systemctl restart lcd-driver
```

---

## Cross-references

- **Upstream**: https://github.com/deyloop/lianli_207_lcd — pull from origin to rebase improvements; consider open-PR-ing the fixes here that are generally useful (not host-specific).
- **Bigger Linux LCD project** (skipped in favor of this one): https://github.com/sgtaziz/lian-li-linux — Rust, full fan/RGB/AIO/evdi-monitor stack. Use this fork if you need just the 207 LCD with minimal deps; switch to sgtaziz if you ever want fan-curve sync or evdi-as-secondary-monitor mode.
- **Protocol details**: `PROTOCOL.md` (USB Bulk endpoints, DES-CBC layout, command bytes 0x0D / 0x33 / 0x34 / 0x65 / 0x66 / 0x79).
- **Dev guidelines**: `AGENTS.md` (code style, build commands, test layout — does not duplicate this file).
- **User's broader rig context**: in a separate workspace (`Local AI RIG`), `Build-Log.md` entries Akce 15-17 cover the deployment timeline, `CLAUDE.md` there describes the full host (BIOS, partitioning, Tailscale ACL, llama.cpp stack).

---

## Future-work parking lot

- Smart-plug integration (Shelly Plus Plug S / TP-Link Kasa) for **true** wall-socket draw — would replace the `LCD_BASELINE_WATTS` constant with a Home-Assistant API pull. Architecture sketch: a new env `LCD_WALL_POWER_API=http://homeassistant:8123/api/states/sensor.rig_plug_power` + bearer token; render as second power line "WALL 318 W" under "SYS XX W". Skipped for now — homelab doesn't need this precision.
- Windows port (PyInstaller .exe + Zadig WinUSB binding + NSSM service + LibreHardwareMonitor for CPU power). Deferred — host boots Windows ~1% of time. Architecture documented in user's separate session log.
- Memory hotspot temp (`GPU Memory Temperature` from `nvidia-smi` once 3090 is installed) — GDDR6X on Ampere is notorious for high VRAM temps. Worth a dedicated bar or graph series once the 3090 is in place.
- Upstream PR cluster — bundle the SUDO_USER install fix, RAPL native read, hardening preset, dynamic labels, threshold lines, and Dependabot into a series of small PRs to `deyloop/lianli_207_lcd`.
