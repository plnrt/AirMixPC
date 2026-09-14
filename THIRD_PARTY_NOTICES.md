# Third-party notices

AirMix PC is distributed under GNU GPL version 3. Its source is based on
UxPlayEnhanced and the pinned FDH2/UxPlay submodule. See `LICENSE` and the
copyright headers in the corresponding source files.

`src/audio_renderer.c` and `src/audio_renderer.h` are a modified, GPL-3.0
replacement of UxPlay's `renderers/audio_renderer.c`/`.h` (itself derived
from RPiPlay), adding per-session audio pipelines so multiple Apple devices
can stream to AirMix PC at once. `build.sh` copies both files into
`lib/uxplay/renderers/` before the build, replacing the upstream sources.

The binary package contains runtime components from these projects:

- UxPlay — GPLv3; https://github.com/FDH2/UxPlay
- GStreamer and its plugins — predominantly LGPL-2.1-or-later, with individual
  plugin licenses documented by GStreamer; https://gstreamer.freedesktop.org/
- GLib and supporting MinGW libraries — LGPL and other compatible free-software
  licenses; https://www.gtk.org/
- OpenSSL — Apache License 2.0; https://www.openssl.org/
- libplist — LGPL-2.1-or-later; https://github.com/libimobiledevice/libplist
- FFmpeg — LGPL/GPL depending on configured components; https://ffmpeg.org/
- Python — Python Software Foundation License; https://www.python.org/
- PyInstaller — GPL-2.0-or-later with its bootloader exception;
  https://pyinstaller.org/
- pystray — LGPL-3.0; https://github.com/moses-palmer/pystray
- Pillow — HPND License; https://python-pillow.org/
- pycaw — MIT License; https://github.com/AndreMiras/pycaw
- comtypes — MIT License; https://github.com/enthought/comtypes

This notice is informational and does not replace the license texts or source
copyright headers. Complete corresponding source is the contents of this
repository at the revisions in `SOURCE_REVISIONS.txt`.
