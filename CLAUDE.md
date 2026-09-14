# AirMix PC: development handoff

This file is the working context for an agent continuing AirMix PC development.
Read it together with `README.md`, but treat this file and the current source as
authoritative where older UxPlayEnhanced text in the README disagrees.

## Repository and current release

- Repository: `https://github.com/plnrt/AirMixPC`
- Development/default branch: `airmix-pc`
- Current application version: `1.0.2`
- Current handoff base commit: `3bdd60c`
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

## Architecture

- `launcher/airmix_core.py`: settings, mode arguments, telemetry parsing, Auto
  state machine, singleton/log helpers, and `APP_VERSION`.
- `launcher/airmix_tray.pyw`: Tk/pystray GUI, receiver watchdog, notifications,
  output monitoring, and subprocess lifecycle.
- `src/dnssd_embedded.c`: Bonjour-free `_airplay._tcp` and `_raop._tcp` mDNS.
- `patch_cmake.py`: applies integration and telemetry changes to pinned UxPlay.
- `build.sh`: builds UxPlay, PyInstaller GUI, runtime DLL closure, hashes, and
  distributable `dist/AirMixPC`.
- `installer/AirMixPC.iss`: normal Inno Setup installer.
- `installer/configure-system.ps1`: network profile, firewall, permissions, and
  startup integration.
- `tests/`: Python unit/package tests and elevated Windows installer tests.

The UxPlay core emits structured `AIRMIX_METRIC` and `AIRMIX_EVENT` lines on
stdout. The tray controller parses them and owns policy decisions. Preserve this
boundary rather than burying UI policy in the C core.

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
7. Update the stale README Bonjour section: it still says every active IPv4
   interface is advertised and that VPN appearance is picked up. That no longer
   matches the current routed-LAN policy.

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
- Never commit secrets, generated receiver keys, trusted-device registries,
  local IP configuration dumps, or logs containing private metadata.
- Ask the user to run local/hardware checks when cloud execution cannot observe
  Windows, the router, VPN, iPhone, or headphones.
