# AirMix PC: development handoff

This file is the working context for an agent continuing AirMix PC development.
Read it together with `README.md`, but treat this file and the current source as
authoritative where older UxPlayEnhanced text in the README disagrees.

## Repository and current release

- Repository: `https://github.com/plnrt/AirMixPC`
- Development/default branch: `airmix-pc`
- Current application version: `1.1.0`
- Current handoff base commit: `fe5134c` (tip of the multi-session-audio work;
  see "Current installed/tested state" below for what has and has not been
  verified at this version)
- License: GPL-3.0; preserve all upstream notices and corresponding source.
- Pinned UxPlayEnhanced base: `77b88a7b05c67c7d4a388d2fe8fe15c8078b564d`
- Pinned UxPlay submodule: `5de48c3d7ba07de396639dd3704de908b649f37f`

Clone with submodules and stay on the `airmix-pc` branch:

```bash
git clone --recurse-submodules --branch airmix-pc https://github.com/plnrt/AirMixPC.git
cd AirMixPC
```

Do not commit generated `build/` or `dist/` content. Do not commit incidental
changes made inside `lib/uxplay`; `build.sh` copies and patches integration
sources into that submodule during a local build, so it can appear dirty.

## Product intent

AirMix PC is a Windows tray application that receives audio from an iPhone over
AirPlay/Wi-Fi and plays it through the current default Windows WASAPI endpoint.
Windows therefore mixes iPhone and PC audio before sending both to headphones,
currently Sony WH-1000XM6.

Version 1 is deliberately audio-only:

- UxPlay runs with `-vs 0`.
- Audio uses an explicit `wasapi2sink` in shared mode.
- Screen mirroring, video, Hi-Res, and FairPlay DRM are out of scope.
- The receiver name is `AirMix PC`.
- Pairing uses a random PIN on first connection and retains trusted devices.
- Settings, logs, the receiver key, and trust registry live in
  `%LOCALAPPDATA%\AirMixPC` and must never be committed or printed.
- Do not close Phone Link, change device drivers, or force a default output.
- Warn when the default output is not WH-1000XM6 or Hands-Free audio activates.

Modes are defined in `launcher/airmix_core.py`:

- Stable: about 2 s synchronous AirPlay buffer; WASAPI low latency off.
- Balanced: 0.5 s buffer; standard WASAPI.
- Low latency: 0.25 s buffer and `wasapi2sink low-latency=true`.
- Auto (default): starts Balanced and promotes a subsequent controlled
  connection to Stable after repeated loss, late packets, flushes, or network
  disconnects.

AirMix PC accepts more than one Apple device at once under the single
receiver name `AirMix PC`. The core is started with `-maxclients N`
(audio-only; the UxPlay core itself defaults to 1 and forces 1 if video is
ever enabled, since `-vs 0` is fixed in v1). The tray always passes an
explicit value from `settings.json`'s `maxClients` (default 4, valid range
1-12; 12 is the underlying `httpd` connection-slot limit). Each admitted
device gets its own GStreamer pipeline and `wasapi2sink`; Windows mixes the
outputs. Devices are independent, unsynchronized sources — this is not
multi-room/multi-device sync. Changing the latency mode or the Windows
default output still restarts the whole core and drops every device.

## Architecture

- `launcher/airmix_core.py`: settings, mode arguments, telemetry parsing, Auto
  state machine, singleton/log helpers, and `APP_VERSION`.
- `launcher/airmix_tray.pyw`: Tk/pystray GUI, receiver watchdog, notifications,
  output monitoring, and subprocess lifecycle.
- `src/dnssd_embedded.c`: Bonjour-free `_airplay._tcp` and `_raop._tcp` mDNS.
- `src/audio_renderer.c`, `src/audio_renderer.h`: full-file replacement of
  UxPlay's `renderers/audio_renderer.c`/`.h`; adds a per-session pipeline
  registry (one GStreamer pipeline and `wasapi2sink` per connected device)
  instead of upstream's single global pipeline. `build.sh` copies both files
  into `lib/uxplay/renderers/` before the build, alongside the
  `dnssd_embedded.c` copy.
- `patch_cmake.py`: applies integration and telemetry changes to pinned UxPlay,
  including a per-connection slot table in `uxplay.cpp` (`airmix_slot`, one
  slot per admitted connection, holding `cls`, `sid`, clock offset, missed
  feedback count, device/model strings) and the `-maxclients` admission and
  disconnect wiring described below.
- `build.sh`: builds UxPlay, PyInstaller GUI, runtime DLL closure, hashes, and
  distributable `dist/AirMixPC`.
- `installer/AirMixPC.iss`: normal Inno Setup installer.
- `installer/configure-system.ps1`: network profile, firewall, permissions, and
  startup integration.
- `tests/`: Python unit/package tests and elevated Windows installer tests.

The UxPlay core emits structured `AIRMIX_METRIC` and `AIRMIX_EVENT` lines on
stdout. The tray controller parses them and owns policy decisions. Preserve this
boundary rather than burying UI policy in the C core.

### Core/tray telemetry contract

Every connected device gets a monotonically increasing `unsigned int`
session id (`sid`, starting at 1, assigned once per connection and never
reused within a process run). The wire format is frozen:

```text
AIRMIX_EVENT start sid=1 device=Artur%27s%20iPhone model=iPhone17,1 codec=ALAC
AIRMIX_METRIC sid=1 received=… missing=… retransmitted=… late=… flushes=… decoder_errors=0 sink_errors=0
AIRMIX_EVENT error sid=1 type=sink reason=audio_error count=1
AIRMIX_EVENT error sid=1 type=decoder reason=audio_error count=1
AIRMIX_EVENT disconnect sid=1 reason=ended
AIRMIX_EVENT disconnect sid=1 reason=network
```

Rules for anyone touching this boundary:

- `sid=` is always the first key on `AIRMIX_EVENT start`/`error` and every
  `AIRMIX_METRIC` line, including when only one device is connected.
- One exception, kept byte-identical to upstream on purpose: when
  `maxClients` is 1 (or `-maxclients` is omitted), the single-client
  feedback-timeout path in `feedback_callback` still prints the legacy
  `AIRMIX_EVENT disconnect reason=network` with **no** `sid`. This is a
  process-wide event (the whole receiver is about to reset), not a
  per-device one, and the multi-session (`maxClients > 1`) feedback-timeout
  and sink/decoder-error paths always include `sid=` instead.
- Existing keys (`received`, `missing`, `type`, `reason`, …) are never
  renamed; new fields are only ever appended.
- `device` and `model` are percent-encoded byte-for-byte (everything outside
  `[A-Za-z0-9._~-]` becomes `%XX` over UTF-8); the Python side decodes with
  `launcher/airmix_core.py`'s `decode_field` (`urllib.parse.unquote`, where
  `+` is not treated as a space).
- A `disconnect` line with no `sid` (the legacy `reason=network` line above,
  or the tray's own `disconnect reason=unexpected`) is process-wide and
  clears the entire session registry, not just one device.

On the Python side, `launcher/airmix_core.py` owns a `SessionRegistry`
(`SessionState` per `sid`, keyed by `sid`, exposing `apply`, `lines()`,
`summary()`) that consumes this telemetry independently of Auto mode's own
per-session baselines (`AutoPolicy.last_metrics` is keyed by `sid` too, so
loss/late/flush counters do not bleed between devices). The tray's device
list and menu render `SessionRegistry.lines()` / `.summary()`.

## Current installed/tested state

The user's live installation was updated in place to `D:\AirMixPC` with v1.0.2.
Its GUI and `uxplay.exe` hashes matched the freshly built package. Existing
settings, one trusted iPhone registration, and private receiver key were
preserved outside the install directory.

Version 1.0.2 is visible in:

- the GUI window title and top-right header;
- the tray tooltip and a disabled tray menu item;
- each new application log-session header;
- Windows installer metadata.

The v1.0.2 package build verified 164 PE files: 162 DLLs and 2 EXEs. The Python
suite passed 26 tests, with 2 optional live shutdown tests skipped unless a
built package is supplied. The local installer was built as
`AirMixPC-Setup-1.0.2.exe`; it has not yet been published as a GitHub release.

Version 1.1.0 (multi-session audio, `-maxclients`) is installed and live-tested:
`./build.sh` verified 164 PE files (162 DLLs, 2 EXEs), and
`python -m unittest discover -s tests -v` passed 74 tests (2 skipped, the same
optional live-shutdown tests as above). The installer was compiled with Inno
Setup 6.7.3 as `dist\installer\AirMixPC-Setup-1.1.0.exe` and applied silently
to `D:\AirMixPC` on 2026-09-14 (registry DisplayVersion 1.1.0; installed
`AirMixPC.exe`/`uxplay.exe` hashes match the package; `receiver.pem`,
`settings.json` and `trusted-devices.register` were preserved, and
`settings.json` gained `maxClients: 4`). The user confirmed on real hardware
that an iPhone and an iPad connect to `AirMix PC` and play at the same time.
The 1.1.0 installer has not been published as a GitHub release.

Known cosmetic limitation carried from the WP-B implementation: metadata-text
deduplication (`launcher`/core console output that suppresses repeated,
unchanged song metadata blocks) is still process-global rather than per
session, so with two simultaneous devices playing identical metadata text the
second device's metadata block can be suppressed in the console/log. This does
not affect the frozen `AIRMIX_METRIC`/`AIRMIX_EVENT` telemetry contract and
does not affect the `-md` metadata file output, which was already
non-deduplicated.

## Current discovery problem

The receiver previously advertised both the home LAN and WatchGuard Mobile VPN
TAP addresses. The VPN address is in `10.80.10.x` and changes between sessions;
the home Ethernet address observed during the latest test was
`192.168.51.209/24`, gateway `192.168.51.1`, network profile `admin_new`
(Private). Wi-Fi was disconnected during that snapshot.

Commits `0214d83` and `3bdd60c` changed embedded mDNS to:

1. ignore PPP/tunnel/no-multicast adapters;
2. on Windows, advertise only active IPv4 adapters with an IPv4 gateway;
3. rescan interfaces every 15 seconds;
4. send unsolicited active-service announcements every 30 seconds.

The periodic announcement is intentional. Windows allows multiple processes to
share UDP 5353, and another local mDNS socket may receive the iPhone's browse
query instead of AirMix. At the last local check, ChatGPT/Codex also owned a
5353 socket. AirMix's latest log showed only:

```text
=== AirMix PC v1.0.2 ... ===
embedded mDNS: responder started (hostname: AirMix-PC.local)
embedded mDNS: advertising on 192.168.51.209
```

The WatchGuard TAP interface was active at `10.80.10.3` with no gateway and was
correctly absent from the new AirMix advertisement. The UxPlay core was alive
and listening for AirPlay connections. Existing inbound firewall rules covered
the installed `D:\AirMixPC\uxplay.exe` on Private networks (legacy Public rules
also existed). Despite those checks, the user reported that the iPhone still
did not show `AirMix PC` before/around the v1.0.2 update. Do not claim discovery
is solved until the user confirms it on the iPhone.

Most useful next investigation:

1. Reproduce with iPhone and PC on the same `admin_new` LAN while WatchGuard is
   connected, then do a short A/B test with WatchGuard disconnected.
2. Capture actual Ethernet multicast traffic externally or with an elevated
   packet capture and verify that valid `_raop._tcp.local` and
   `_airplay._tcp.local` announcements leave `192.168.51.209` with IP TTL 255.
3. Check `sendto` and `setsockopt(IP_MULTICAST_IF)` return values in
   `src/dnssd_embedded.c`; they are currently not surfaced in logs.
4. Validate the emitted DNS packet with a strict parser and an Apple client,
   including cache-flush bits, TTLs, record names, TXT data, SRV target, port,
   and the interface-specific A record.
5. Test whether Windows socket sharing on 5353 requires a different strategy
   (for example the native Windows DNS-SD API or a dedicated broker) rather than
   relying indefinitely on periodic unsolicited announcements.
6. Verify the installer creates rules for the process that actually owns the
   sockets (`uxplay.exe`), not only the PyInstaller controller, while retaining
   program-scoped, inbound-only, Private-network rules.
7. Done: the README Bonjour section now describes the routed-LAN, gateway-only
   policy instead of the stale "every active IPv4 interface" / VPN-appears
   claim.

Do not disable or stop the VPN without explicit user authorization. Do not
change router isolation, firewall policy, or network category without first
showing evidence that it is the blocking layer.

## Build and verification

The complete Windows build requires MSYS2 MinGW64, GStreamer, Windows Python
3.11, PyInstaller, pystray, Pillow, and the dependencies listed in
`requirements-build.txt` and `build.sh`.

From an MSYS2 MinGW64 shell:

```bash
./build.sh
```

From PowerShell after package creation:

```powershell
python -m unittest discover -s tests -v
installer\build-installer.ps1
```

Installer integration tests are intentionally elevated and mutate installed
files, shortcuts, firewall rules, and network profile state. Run them only on a
designated Windows machine with the user's approval. Cloud CI can perform
source/unit/static tests but cannot prove iPhone discovery, Windows multicast,
WASAPI output switching, installer elevation, or 30-minute playback stability.

Before any release:

- update `APP_VERSION`, Inno Setup version, legacy setup metadata, README path,
  installer output path, and checksum filename together;
- run all unit and package checks;
- verify DLL imports and SHA-256 manifests;
- confirm GPL license and third-party notices are present;
- run install/repair/uninstall and real iPhone/WASAPI tests on Windows;
- preserve `%LOCALAPPDATA%\AirMixPC` settings/trust on upgrade and uninstall;
- publish a GitHub release only after explicit user approval.

## Collaboration rules

- Lead with evidence from current logs/tests; do not infer that a network fix
  works merely because the code builds.
- Preserve unrelated user changes and never reset or clean a dirty worktree.
- Use small, reviewable commits on `airmix-pc` and push them to `plnrt/AirMixPC`.
  In this checkout, the `fork` remote points at `plnrt/AirMixPC` and is the one
  to push to; `origin` points at the upstream `Kylepossible/UxPlayEnhanced`
  repository and must never be pushed to.
- `.workflow/` holds local orchestration artifacts (plans, decisions,
  acceptance criteria, execution reports) for agent-driven runs. It is listed
  in `.gitignore` and must never be committed.
- Never commit secrets, generated receiver keys, trusted-device registries,
  local IP configuration dumps, or logs containing private metadata.
- Ask the user to run local/hardware checks when cloud execution cannot observe
  Windows, the router, VPN, iPhone, or headphones.
