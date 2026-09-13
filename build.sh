#!/bin/bash
# Build UxPlay with embedded mDNS (no Bonjour required)
# Requires MSYS2 with mingw-w64-x86_64 packages installed.
#
# Prerequisites (run in MSYS2 MinGW64 shell):
#   pacman -S --needed mingw-w64-x86_64-toolchain mingw-w64-x86_64-cmake \
#     mingw-w64-x86_64-gstreamer mingw-w64-x86_64-gst-plugins-base \
#     mingw-w64-x86_64-gst-plugins-good mingw-w64-x86_64-gst-plugins-bad \
#     mingw-w64-x86_64-gst-libav mingw-w64-x86_64-openssl \
#     mingw-w64-x86_64-libplist mingw-w64-x86_64-pkg-config

set -e

# Make the script work from either the MSYS2 MinGW64 shell or a plain MSYS2
# shell launched by a Windows shortcut.
export PATH="/mingw64/bin:/usr/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
DIST_DIR="$SCRIPT_DIR/dist/AirMixPC"

if [ -n "${AIRMIX_BUILD_PYTHON:-}" ] && [ -f "$AIRMIX_BUILD_PYTHON" ]; then
    BUILD_PYTHON="$AIRMIX_BUILD_PYTHON"
elif [ -f /mingw64/bin/python.exe ]; then
    BUILD_PYTHON="/mingw64/bin/python.exe"
elif [ -f /c/Python311/python.exe ]; then
    BUILD_PYTHON="/c/Python311/python.exe"
else
    BUILD_PYTHON="$(command -v python || command -v python.exe || true)"
fi
if [ -z "$BUILD_PYTHON" ]; then
    echo "ERROR: Python is required to apply the source patch"
    exit 1
fi

echo "=== Copying embedded mDNS source ==="
cp "$SCRIPT_DIR/src/dnssd_embedded.c" "$SCRIPT_DIR/lib/uxplay/lib/dnssd_embedded.c"

echo "=== Patching CMakeLists.txt for embedded mDNS ==="
"$BUILD_PYTHON" "$SCRIPT_DIR/patch_cmake.py"

echo "=== Configuring build ==="
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"
cmake "$SCRIPT_DIR/lib/uxplay" \
    -G "MinGW Makefiles" \
    -DUSE_EMBEDDED_MDNS=ON \
    -DNO_MARCH_NATIVE=ON \
    -DCMAKE_BUILD_TYPE=Release

echo "=== Building ==="
mingw32-make -j$(nproc)

echo "=== Packaging ==="
if [ "$DIST_DIR" != "$SCRIPT_DIR/dist/AirMixPC" ]; then
    echo "ERROR: refusing to clean unexpected package path: $DIST_DIR"
    exit 1
fi
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR/lib/gstreamer-1.0"

# Copy executable
cp "$BUILD_DIR/uxplay.exe" "$DIST_DIR/"
cp "$SCRIPT_DIR/README.md" "$SCRIPT_DIR/LICENSE" \
   "$SCRIPT_DIR/THIRD_PARTY_NOTICES.md" "$SCRIPT_DIR/SOURCE_REVISIONS.txt" "$DIST_DIR/"

# Copy GStreamer runtime libs
for lib in libgstvideo-1.0-0 libgstsdp-1.0-0 libgstpbutils-1.0-0 \
           libgstaudio-1.0-0 libgsttag-1.0-0 libgstrtp-1.0-0 \
           libgstgl-1.0-0 libgstcodecparsers-1.0-0 liborc-0.4-0; do
    cp /mingw64/bin/${lib}.dll "$DIST_DIR/"
done

# FFmpeg's avcodec can import xvidcore.dll. On the build PC, ldd may resolve
# it from Windows/System32, so copy the MinGW runtime explicitly for portable
# installs on machines without that system DLL.
cp /mingw64/bin/xvidcore.dll "$DIST_DIR/"

# Copy GStreamer plugins
for plugin in libgstcoreelements libgstplayback libgstvideoconvertscale \
              libgstautodetect libgstaudioconvert libgstaudioresample \
              libgstvolume libgsttypefindfunctions libgstapp \
              libgstvideoparsersbad libgstisomp4 libgstrtp libgstrtpmanager \
              libgstudp libgstopengl libgstrtsp libgstlibav \
              libgstlevel libgstaudioparsers libgstvideofilter \
              libgstd3d11 libgstwasapi2 libgstd3d12 \
              libgstdirectsound libgstwasapi \
              libgstalaw libgstmulaw libgstsubparse libgstencoding; do
    cp /mingw64/lib/gstreamer-1.0/${plugin}.dll "$DIST_DIR/lib/gstreamer-1.0/"
done

# Build the standalone tray launcher. This uses the regular Windows Python
# installation, not MSYS2's build Python. The resulting executable bundles
# pystray and Pillow so users do not need Python or pip-installed packages.
TRAY_PYTHON="${AIRMIX_BUILD_PYTHON:-/c/Python311/python.exe}"
if [ ! -f "$TRAY_PYTHON" ]; then
    TRAY_PYTHON="$(command -v python.exe || true)"
fi
if [ -n "$TRAY_PYTHON" ] && "$TRAY_PYTHON" -m PyInstaller --version >/dev/null 2>&1; then
    echo "=== Building standalone tray launcher ==="
    ICON_ICO_WIN="$(cygpath -w "$SCRIPT_DIR/assets/UxPlayEnhanced.ico")"
    ICON_PNG_WIN="$(cygpath -w "$SCRIPT_DIR/assets/UxPlayEnhanced-icon.png")"
    "$TRAY_PYTHON" -m PyInstaller --noconfirm --clean --onefile --noconsole \
        --name AirMixPC \
        --icon "$ICON_ICO_WIN" \
        --add-data "$ICON_PNG_WIN;assets" \
        --paths "$(cygpath -w "$SCRIPT_DIR")" \
        --hidden-import pycaw.pycaw \
        --hidden-import comtypes \
        --distpath "$BUILD_DIR/tray-dist" \
        --workpath "$BUILD_DIR/tray-work" \
        --specpath "$BUILD_DIR/tray-spec" \
        "$SCRIPT_DIR/launcher/airmix_tray.pyw"
    cp "$BUILD_DIR/tray-dist/AirMixPC.exe" "$DIST_DIR/"
else
    echo "ERROR: Windows Python with PyInstaller is required to build AirMixPC.exe"
    exit 1
fi

# Copy only AirMix launch/install sources; legacy UxPlayEnhanced installers are
# intentionally excluded so they cannot modify an older receiver installation.
for launcher_name in AirMixPC-Setup.cmd AirMixPC-Setup.ps1 AirMixPC-Uninstall.ps1 \
                     AirMixPC.bat airmix_core.py airmix_tray.pyw; do
    cp "$SCRIPT_DIR/launcher/$launcher_name" "$DIST_DIR/"
done

# Resolve the complete import graph directly from the selected MinGW runtime;
# do not let ldd resolve third-party libraries from this PC's System32 or PATH.
"$TRAY_PYTHON" "$SCRIPT_DIR/scripts/verify_package.py" "$(cygpath -w "$DIST_DIR")" \
    --runtime-dir "$(cygpath -w /mingw64/bin)"

echo "=== Writing SHA-256 manifest ==="
(cd "$DIST_DIR" && find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS.txt)

echo ""
echo "=== Build complete ==="
echo "Output: $DIST_DIR"
echo "Files: $(find "$DIST_DIR" -name '*.dll' | wc -l) DLLs, $(find "$DIST_DIR" -name '*.exe' | wc -l) EXE"
echo ""
echo "To use:"
echo "  1. Run AirMixPC-Setup.cmd; it requests Administrator access"
echo "  2. Start AirMix PC from the Start menu"
