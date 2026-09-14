"""AirMix PC tray controller for the patched UxPlay receiver."""

import ctypes
from ctypes import wintypes
import datetime
import logging
import logging.handlers
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
import uuid

from PIL import Image, ImageDraw
import pystray

try:
    from launcher import airmix_core as core
except ModuleNotFoundError:
    import airmix_core as core


def application_dir():
    executable = sys.executable if getattr(sys, "frozen", False) else __file__
    return os.path.dirname(os.path.abspath(executable))


def resource_path(*parts):
    if getattr(sys, "frozen", False):
        base_dir = sys._MEIPASS
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, *parts)


SCRIPT_DIR = application_dir()
UXPLAY_EXE = os.path.join(SCRIPT_DIR, "uxplay.exe")
GST_PLUGIN_PATH = os.path.join(SCRIPT_DIR, "lib", "gstreamer-1.0")
USER_DATA_DIR = core.user_data_dir()
LOG_DIR = USER_DATA_DIR / "Logs"
LOG_PATH = LOG_DIR / "AirMixPC.log"
SETTINGS_PATH = USER_DATA_DIR / "settings.json"
REGISTER_PATH = USER_DATA_DIR / "trusted-devices.register"
PRIVATE_KEY_PATH = USER_DATA_DIR / "receiver.pem"
ICON_PATH = resource_path("assets", "AirMixPC-icon.png")
VERBOSE_LOGGING = "--verbose" in sys.argv[1:]
UI_TEST_MODE = "--ui-test" in sys.argv[1:]
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 2

settings = core.load_settings(SETTINGS_PATH)
core.save_settings(SETTINGS_PATH, settings)
auto_policy = core.AutoPolicy()
sessions = core.SessionRegistry()
process = None
reader_thread = None
shutdown_signal = None
log_writer = None
state_lock = threading.RLock()
stop_monitor = threading.Event()
auto_restart_requested = threading.Event()
intentional_stop = False
receiver_enabled = not UI_TEST_MODE
last_output_warning = ""
last_default_output = ""
restart_attempts = []
main_window = None


class SingleInstance:
    def __init__(self, name=r"Local\AirMixPC.AudioReceiver"):
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel32.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(error)
        self.acquired = error != 183
        if not self.acquired:
            self.close()

    def close(self):
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


class ShutdownSignal:
    def __init__(self):
        self.name = "Local\\AirMixPC.Stop." + uuid.uuid4().hex
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateEventW.restype = wintypes.HANDLE
        self.kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel32.CreateEventW(None, True, False, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def request(self):
        if not self.kernel32.SetEvent(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


class AirMixTrayIcon(pystray.Icon):
    _WM_RBUTTONUP = 0x0205

    def __init__(self, *args, **kwargs):
        self.menu_open = False
        super().__init__(*args, **kwargs)

    def _on_notify(self, wparam, lparam):
        opening = lparam == self._WM_RBUTTONUP
        if opening:
            self.menu_open = True
            self.update_menu()
        try:
            return super()._on_notify(wparam, lparam)
        finally:
            if opening:
                self.menu_open = False


def create_icon_image():
    try:
        with Image.open(ICON_PATH) as source:
            return source.convert("RGBA")
    except OSError:
        image = Image.new("RGBA", (64, 64), (15, 23, 42, 255))
        draw = ImageDraw.Draw(image)
        for x, height in ((16, 18), (24, 32), (32, 44), (40, 32), (48, 18)):
            draw.rounded_rectangle((x - 2, 32 - height // 2, x + 2, 32 + height // 2), 2, fill=(34, 211, 238, 255))
        return image


def create_log_writer():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8", errors="replace", delay=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    writer = logging.getLogger("airmixpc.session")
    writer.propagate = False
    writer.setLevel(logging.INFO)
    for existing in list(writer.handlers):
        writer.removeHandler(existing)
        existing.close()
    writer.addHandler(handler)
    return writer


CLIENT_PATTERN = re.compile(r"connection request from\s+(.+?)\s+\(([^)]+)\)\s+with deviceID", re.I)
QUALITY_PATTERN = re.compile(
    r"codec=([^;\s]+)(?:\s+\([^)]*\))?;\s*quality=([^;]+);\s*"
    r"resolution=(\d+)-bit/(\d+)\s*Hz;\s*channels=(\d+)", re.I,
)
METADATA_PATTERN = re.compile(r"^(Title|Artist|Album):\s*(.*)$", re.I)


def new_state():
    return {
        "client": "", "title": "", "artist": "", "album": "", "quality": "",
        "audio_connected": False, "audio_error": False, "metrics": {},
        "output": "Detecting...", "effective_mode": core.effective_mode(settings["mode"]),
    }


session_state = new_state()


def format_quality(line):
    match = QUALITY_PATTERN.search(line)
    if not match:
        return "Codec: Audio format available in logs"
    codec, quality, bit_depth, sample_rate, channels = match.groups()
    return (f"Codec: {codec} | {quality.strip()} | {bit_depth}-bit / "
            f"{int(sample_rate) / 1000:g} kHz | {channels} ch")[:160]


def apply_log_line(state, line):
    event = line.strip()
    telemetry = core.parse_telemetry(event)
    if telemetry:
        kind, payload = telemetry
        if kind == "metric":
            state["metrics"] = payload
        elif "start" in payload:
            # A fresh start for this session means it is no longer in error,
            # even if another session's error/disconnect had set this flag.
            state["audio_error"] = False
        elif payload.get("reason") in {"network", "audio_error", "unexpected"}:
            state["audio_error"] = True
        return state
    if event == "audio session ended":
        # The per-sid AIRMIX_EVENT disconnect line (handled above, via
        # sessions.apply in handle_output_line) always precedes this legacy
        # text line, so the registry already reflects any other session that
        # is still connected. Only collapse the aggregated single-session
        # view (and drop the Auto baseline) once no session remains.
        if sessions.count() == 0:
            client, output, mode = state["client"], state["output"], state["effective_mode"]
            state.update(new_state())
            state.update(client=client, output=output, effective_mode=mode)
            auto_policy.reset_session()
        return state
    if event == "audio session started":
        client, output, mode = state["client"], state["output"], state["effective_mode"]
        state.update(new_state())
        state.update(client=client, output=output, effective_mode=mode, audio_connected=True)
    if event.startswith("====") and "Audio Metadata" in event:
        for field in ("title", "artist", "album"):
            state[field] = ""
    match = CLIENT_PATTERN.search(line)
    if match:
        state["client"] = f"{match.group(1).strip()} ({match.group(2).strip()})"[:110]
    if "audio error" in line.lower():
        state["audio_error"] = True
    if "start audio connection" in line or "changed audio connection" in line:
        state["audio_connected"] = True
        state["audio_error"] = False
    if "audio quality:" in line:
        state["quality"] = format_quality(line)
    match = METADATA_PATTERN.match(event)
    if match:
        state[match.group(1).lower()] = match.group(2).strip()
    return state


def read_state():
    with state_lock:
        return dict(session_state)


def current_song(state):
    title, artist, album = state["title"], state["artist"], state["album"]
    song = f"{artist} - {title}" if title and artist else title or artist or "No metadata"
    return (f"{song} [{album}]" if album else song)[:110]


def receiver_arguments():
    concrete = core.effective_mode(settings["mode"], auto_policy.stable)
    with state_lock:
        session_state["effective_mode"] = concrete
    args = [
        UXPLAY_EXE, "-n", settings["receiverName"], "-nh", "-vs", "0",
        "-pin", settings["pairingPin"], "-reg", str(REGISTER_PATH),
        "-key", str(PRIVATE_KEY_PATH), "-reset", "15",
    ]
    args.extend(core.mode_arguments(concrete))
    args.extend(core.max_clients_arguments(settings["maxClients"]))
    if not VERBOSE_LOGGING:
        args.append("-no-progress")
    return args


def handle_output_line(line):
    if log_writer:
        log_writer.info(line)
    telemetry = core.parse_telemetry(line)
    promote = False
    with state_lock:
        apply_log_line(session_state, line)
        if telemetry:
            kind, payload = telemetry
            sessions.apply(kind, payload)
            if kind == "event" and "disconnect" in payload:
                sid = core.session_id(payload)
                if sid > 0:
                    # Only this session's Auto baseline is stale; other
                    # connected sessions keep tracking their own.
                    auto_policy.reset_session(sid)
        if settings["mode"] == "auto" and telemetry:
            kind, payload = telemetry
            if kind == "metric":
                promote = auto_policy.observe_metric(payload)
            elif kind == "event" and payload.get("reason"):
                promote = auto_policy.observe_disconnect(payload["reason"])
    if promote:
        auto_restart_requested.set()


def pump_output(stream, on_line):
    try:
        for line in stream:
            on_line(line.rstrip("\r\n"))
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def start_uxplay():
    global process, reader_thread, shutdown_signal, log_writer, intentional_stop
    stop_uxplay()
    intentional_stop = False
    if log_writer is None:
        log_writer = create_log_writer()
    log_writer.info(f"\n=== {core.APP_NAME} v{core.APP_VERSION} {datetime.datetime.now().astimezone().isoformat(timespec='seconds')} ===")
    env = os.environ.copy()
    env["GST_PLUGIN_PATH"] = GST_PLUGIN_PATH
    env["PATH"] = SCRIPT_DIR + os.pathsep + env.get("PATH", "")
    signal = ShutdownSignal()
    env["UXPLAYENHANCED_STOP_EVENT"] = signal.name
    env["UXPLAYENHANCED_PARENT_PID"] = str(os.getpid())
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    with state_lock:
        session_state.update(new_state())
        sessions.clear()
        try:
            process = subprocess.Popen(
                receiver_arguments(), cwd=SCRIPT_DIR, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
        except BaseException:
            signal.close()
            raise
        shutdown_signal = signal
        started = process
    reader_thread = threading.Thread(target=pump_output, args=(started.stdout, handle_output_line), daemon=True)
    reader_thread.start()


def stop_uxplay():
    global process, reader_thread, shutdown_signal, intentional_stop
    intentional_stop = True
    with state_lock:
        current, process = process, None
        reader, reader_thread = reader_thread, None
        signal, shutdown_signal = shutdown_signal, None
        sessions.clear()
    if current and current.poll() is None:
        try:
            if signal:
                signal.request()
            current.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            current.kill()
            current.wait(timeout=5)
    if signal:
        signal.close()
    if reader and reader is not threading.current_thread():
        reader.join(timeout=5)


def default_output_name():
    try:
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetSpeakers().FriendlyName or "Unknown output"
    except Exception as error:
        return f"Unavailable ({type(error).__name__})"


def save_current_settings():
    core.save_settings(SETTINGS_PATH, settings)


def set_mode(icon, mode):
    settings["mode"] = mode
    auto_policy.stable = False
    auto_policy.reset_session()
    with state_lock:
        sessions.clear()
    save_current_settings()
    if receiver_enabled:
        start_uxplay()
    icon.notify(f"Mode changed to {core.MODE_LABELS[mode]}; reconnect AirPlay if needed.", core.APP_NAME)
    icon.update_menu()


def mode_handler(mode):
    return lambda icon, item: set_mode(icon, mode)


def mode_checked(mode):
    return lambda item: settings["mode"] == mode


def sync_startup_shortcut(enabled):
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    shortcut = startup / "AirMix PC.lnk"
    if not enabled:
        shortcut.unlink(missing_ok=True)
        return
    shortcut_q = str(shortcut).replace("'", "''")
    target_q = str(Path(SCRIPT_DIR) / "AirMixPC.exe").replace("'", "''")
    work_q = SCRIPT_DIR.replace("'", "''")
    command = (
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{shortcut_q}');"
        f"$s.TargetPath='{target_q}';"
        f"$s.WorkingDirectory='{work_q}';$s.Save()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def toggle_startup(icon, item):
    settings["launchAtLogin"] = not settings["launchAtLogin"]
    save_current_settings()
    try:
        sync_startup_shortcut(settings["launchAtLogin"])
    except (OSError, subprocess.CalledProcessError) as error:
        icon.notify(f"Could not update startup shortcut: {error}", core.APP_NAME)
    icon.update_menu()


def reset_trust(icon, item):
    was_running = receiver_enabled
    if was_running:
        stop_uxplay()
    REGISTER_PATH.unlink(missing_ok=True)
    settings["pairingPin"] = core.generate_pin()
    save_current_settings()
    if was_running:
        start_uxplay()
    icon.notify(f"Pairing reset. New PIN: {settings['pairingPin']}", core.APP_NAME)


def on_start(icon, item):
    global receiver_enabled, restart_attempts
    receiver_enabled = True
    restart_attempts = []
    start_uxplay()
    icon.update_menu()


def on_stop(icon, item):
    global receiver_enabled
    receiver_enabled = False
    stop_uxplay()
    icon.update_menu()


def on_restart(icon, item):
    global restart_attempts
    restart_attempts = []
    if receiver_enabled:
        start_uxplay()
        icon.notify("Receiver restarted; reconnect AirPlay if needed.", core.APP_NAME)


def on_view_logs(icon, item):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.touch(exist_ok=True)
    subprocess.Popen(["notepad.exe", str(LOG_PATH)])


def on_open_settings(icon, item):
    save_current_settings()
    subprocess.Popen(["notepad.exe", str(SETTINGS_PATH)])


def on_open_folder(icon, item):
    os.startfile(SCRIPT_DIR)


def on_quit(icon, item):
    stop_monitor.set()
    stop_uxplay()
    icon.stop()
    if main_window:
        main_window.root.after(0, main_window.root.destroy)


def host_text(item=None):
    return f"AirPlay: {settings['receiverName']}"


def client_text(item=None):
    return "Client: " + (read_state()["client"] or "Waiting")


def session_lines():
    with state_lock:
        return sessions.lines()


def sessions_text(item=None):
    with state_lock:
        summary = sessions.summary()
    return "Devices: " + summary


def status_text(item=None):
    state = read_state()
    with state_lock:
        running = process is not None and process.poll() is None
    if not running:
        return "Status: Stopped"
    if state["audio_error"]:
        return "Status: Audio error — view log"
    return "Status: Connected" if state["audio_connected"] else "Status: Waiting for AirPlay"


def output_text(item=None):
    return "Output: " + read_state()["output"][:100]


def mode_text(item=None):
    state = read_state()
    configured = core.MODE_LABELS[settings["mode"]]
    effective = core.MODE_LABELS[state["effective_mode"]]
    return f"Mode: {configured}" + (f" → {effective}" if settings["mode"] == "auto" else "")


def song_text(item=None):
    return "Song: " + current_song(read_state())


def quality_text(item=None):
    return read_state()["quality"] or "Codec: Waiting for audio"


def pin_text(item=None):
    return f"Pairing PIN: {settings['pairingPin']}"


def monitor_loop(icon):
    global last_output_warning, last_default_output, receiver_enabled, restart_attempts
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        comtypes = None
    try:
        while not stop_monitor.wait(1):
            if auto_restart_requested.is_set() and receiver_enabled:
                auto_restart_requested.clear()
                icon.notify("Network instability detected. Switching Auto to Stable; reconnect AirPlay.", core.APP_NAME)
                start_uxplay()
            with state_lock:
                current = process
            if receiver_enabled and current is not None and current.poll() is not None and not intentional_stop:
                if log_writer:
                    log_writer.warning("AIRMIX_EVENT disconnect reason=unexpected")
                now = time.monotonic()
                restart_attempts = [stamp for stamp in restart_attempts if now - stamp < 60]
                restart_attempts.append(now)
                if len(restart_attempts) >= 5:
                    receiver_enabled = False
                    icon.notify("Receiver stopped after repeated startup failures. Open AirMix PC and view the log.", core.APP_NAME)
                else:
                    time.sleep(2)
                    start_uxplay()
                    if len(restart_attempts) <= 2:
                        icon.notify("Receiver recovered after an unexpected exit.", core.APP_NAME)
            output = default_output_name()
            with state_lock:
                session_state["output"] = output
            if last_default_output and output != last_default_output and settings["followDefaultOutput"]:
                if receiver_enabled:
                    icon.notify(f"Default output changed to {output}. Reconnecting AirPlay audio.", core.APP_NAME)
                    start_uxplay()
            last_default_output = output
            warning = ""
            if "Hands-Free" in output:
                warning = "Hands-Free output is active; select Headphones (WH-1000XM6) for stereo audio."
            elif "WH-1000XM6" not in output:
                warning = f"Default output is {output}, not WH-1000XM6."
            if warning and warning != last_output_warning:
                icon.notify(warning, core.APP_NAME)
            last_output_warning = warning
            if not icon.menu_open:
                icon.update_menu()
    finally:
        if comtypes:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


def setup(icon):
    icon.visible = True
    if not UI_TEST_MODE:
        start_uxplay()
        if not REGISTER_PATH.exists():
            icon.notify(f"Select AirMix PC on your iPhone or iPad. First-time PIN: {settings['pairingPin']}", core.APP_NAME)
    threading.Thread(target=monitor_loop, args=(icon,), daemon=True).start()


PALETTE = {
    "bg": "#0f141d", "card": "#182130", "field": "#222e41", "line": "#26334a",
    "text": "#eef3fa", "muted": "#8ea3bb", "accent": "#3b82f6", "accent_active": "#2f6fd6",
    "ok": "#34d399", "warn": "#fbbf24", "error": "#f87171", "idle": "#6b7a90",
}
METRIC_KEYS = ("received", "missing", "retransmitted", "late", "flushes")


def enable_dpi_awareness():
    """Render crisp text on scaled displays instead of letting Windows stretch a 96-DPI bitmap."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


class AirMixWindow:
    def __init__(self, icon):
        self.icon = icon
        enable_dpi_awareness()
        self.root = tk.Tk()
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.root.title(f"{core.APP_NAME} v{core.APP_VERSION}")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.configure(bg=PALETTE["bg"])
        try:
            self.root.iconbitmap(resource_path("assets", "UxPlayEnhanced.ico"))
        except tk.TclError:
            pass
        self.values = {key: tk.StringVar() for key in ("status", "song", "quality", "output", "pin", "device_count")}
        self.metric_values = {key: tk.StringVar(value="0") for key in METRIC_KEYS}
        self.mode = tk.StringVar(value=core.MODE_LABELS[settings["mode"]])
        self.receiver_name = tk.StringVar(value=settings["receiverName"])
        self.launch_at_login = tk.BooleanVar(value=settings["launchAtLogin"])
        self.rendered_devices = None
        self._build()
        self.refresh()
        self._fit_to_content(force=True)

    def px(self, value):
        return int(round(value * self.scale))

    def _build(self):
        c = PALETTE
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=c["bg"], foreground=c["text"], font=("Segoe UI", 10), borderwidth=0)
        style.configure("TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["card"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"], font=("Segoe UI", 10))
        style.configure("Header.TLabel", foreground="#ffffff", font=("Segoe UI Semibold", 24))
        style.configure("Sub.TLabel", foreground=c["muted"], font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=c["card"], foreground=c["muted"], font=("Segoe UI Semibold", 9))
        style.configure("CardValue.TLabel", background=c["card"], foreground=c["text"], font=("Segoe UI Semibold", 12))
        style.configure("CardMuted.TLabel", background=c["card"], foreground=c["muted"], font=("Segoe UI", 10))
        style.configure("Device.TLabel", background=c["card"], foreground=c["text"], font=("Segoe UI Semibold", 12))
        style.configure("Metric.TLabel", background=c["card"], foreground=c["text"], font=("Segoe UI Semibold", 15))
        style.configure("MetricName.TLabel", background=c["card"], foreground=c["muted"], font=("Segoe UI", 9))
        style.configure("TButton", background=c["field"], foreground=c["text"], font=("Segoe UI", 10),
                        padding=(14, 8), borderwidth=0, focuscolor=c["field"])
        style.map("TButton", background=[("active", c["line"]), ("disabled", c["card"])],
                  foreground=[("disabled", c["idle"])])
        style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff",
                        font=("Segoe UI Semibold", 10), padding=(18, 8), borderwidth=0, focuscolor=c["accent"])
        style.map("Accent.TButton", background=[("active", c["accent_active"]), ("disabled", c["idle"])])
        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"], focuscolor=c["bg"])
        style.map("TCheckbutton", background=[("active", c["bg"])],
                  indicatorcolor=[("selected", c["accent"]), ("!selected", c["field"])])
        style.configure("TCombobox", fieldbackground=c["field"], background=c["field"], foreground=c["text"],
                        arrowcolor=c["text"], bordercolor=c["field"], lightcolor=c["field"], darkcolor=c["field"],
                        selectbackground=c["field"], selectforeground=c["text"], padding=(8, 6))
        style.map("TCombobox", fieldbackground=[("readonly", c["field"])], foreground=[("readonly", c["text"])],
                  selectbackground=[("readonly", c["field"])], selectforeground=[("readonly", c["text"])])
        self.root.option_add("*TCombobox*Listbox.background", c["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", "{Segoe UI} 10")

        body = ttk.Frame(self.root, padding=self.px(24))
        body.pack(fill="both", expand=True)

        header = ttk.Frame(body)
        header.pack(fill="x")
        titles = ttk.Frame(header)
        titles.pack(side="left")
        ttk.Label(titles, text="AirMix PC", style="Header.TLabel").pack(anchor="w")
        ttk.Label(titles, text="iPhone, iPad and Windows audio, mixed in your headphones",
                  style="Sub.TLabel").pack(anchor="w")
        badges = ttk.Frame(header)
        badges.pack(side="right", anchor="n", pady=(self.px(6), 0))
        self.status_badge = tk.Label(badges, textvariable=self.values["status"], bg=c["idle"], fg="#0b1020",
                                     font=("Segoe UI Semibold", 10), padx=self.px(12), pady=self.px(4))
        self.status_badge.pack(anchor="e")
        ttk.Label(badges, text=f"v{core.APP_VERSION}", style="Sub.TLabel").pack(anchor="e", pady=(self.px(6), 0))

        devices = self._card(body, pady=(self.px(20), self.px(8)))
        head = ttk.Frame(devices, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="DEVICES", style="CardTitle.TLabel").pack(side="left")
        ttk.Label(head, textvariable=self.values["device_count"], style="CardMuted.TLabel").pack(side="right")
        self.devices_frame = ttk.Frame(devices, style="Card.TFrame")
        self.devices_frame.pack(fill="x", pady=(self.px(8), 0))

        playback = self._card(body)
        self._row(playback, "NOW PLAYING", self.values["song"])
        self._row(playback, "FORMAT", self.values["quality"])

        output = self._card(body)
        self._row(output, "WINDOWS OUTPUT", self.values["output"])
        ttk.Label(output, text="PACKET HEALTH", style="CardTitle.TLabel").pack(anchor="w", pady=(self.px(10), self.px(4)))
        metrics = ttk.Frame(output, style="Card.TFrame")
        metrics.pack(fill="x")
        for column, key in enumerate(METRIC_KEYS):
            tile = ttk.Frame(metrics, style="Card.TFrame")
            tile.grid(row=0, column=column, sticky="w", padx=(0, self.px(28)))
            ttk.Label(tile, textvariable=self.metric_values[key], style="Metric.TLabel").pack(anchor="w")
            ttk.Label(tile, text=key, style="MetricName.TLabel").pack(anchor="w")

        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=(self.px(16), 0))
        mode_box = ttk.Frame(controls)
        mode_box.pack(side="left")
        ttk.Label(mode_box, text="Audio mode", style="Sub.TLabel").pack(anchor="w")
        combo = ttk.Combobox(mode_box, state="readonly", width=22, textvariable=self.mode,
                             values=[core.MODE_LABELS[m] for m in core.VALID_MODES])
        combo.pack(anchor="w", pady=(self.px(4), 0))
        combo.bind("<<ComboboxSelected>>", self.change_mode)
        buttons = ttk.Frame(controls)
        buttons.pack(side="right", anchor="s")
        ttk.Button(buttons, text="Start", style="Accent.TButton",
                   command=lambda: on_start(self.icon, None)).pack(side="left", padx=(0, self.px(8)))
        ttk.Button(buttons, text="Stop", command=lambda: on_stop(self.icon, None)).pack(side="left", padx=(0, self.px(8)))
        ttk.Button(buttons, text="Restart", command=lambda: on_restart(self.icon, None)).pack(side="left")

        pairing = ttk.Frame(body)
        pairing.pack(fill="x", pady=(self.px(18), 0))
        ttk.Label(pairing, textvariable=self.values["pin"], font=("Segoe UI Semibold", 11)).pack(side="left")
        ttk.Label(pairing, text="  Enter it on a new iPhone or iPad the first time it connects.",
                  style="Sub.TLabel").pack(side="left")
        ttk.Button(pairing, text="Reset trusted devices", command=lambda: reset_trust(self.icon, None)).pack(side="right")

        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(self.px(18), 0))
        ttk.Checkbutton(footer, text="Launch at sign-in", variable=self.launch_at_login,
                        command=self.toggle_startup).pack(side="left")
        ttk.Button(footer, text="Logs", command=lambda: on_view_logs(self.icon, None)).pack(side="right", padx=(self.px(8), 0))
        ttk.Button(footer, text="Install folder", command=lambda: on_open_folder(self.icon, None)).pack(side="right")
        ttk.Label(body, text="Closing this window keeps AirMix PC running in the system tray.",
                  style="Sub.TLabel").pack(anchor="w", pady=(self.px(12), 0))

    def _card(self, parent, pady=None):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(self.px(18), self.px(16)))
        card.pack(fill="x", pady=pady if pady is not None else (self.px(8), self.px(8)))
        return card

    def _row(self, parent, title, variable):
        line = ttk.Frame(parent, style="Card.TFrame")
        line.pack(fill="x", pady=self.px(4))
        ttk.Label(line, text=title, style="CardTitle.TLabel", width=18).pack(side="left", anchor="n", pady=(self.px(3), 0))
        ttk.Label(line, textvariable=variable, style="CardValue.TLabel", wraplength=self.px(470),
                  justify="left").pack(side="left", fill="x", expand=True)

    def _render_devices(self, devices):
        for child in self.devices_frame.winfo_children():
            child.destroy()
        if not devices:
            ttk.Label(self.devices_frame, text="No devices connected", style="Device.TLabel").pack(anchor="w")
            ttk.Label(self.devices_frame, text="Pick \u201cAirMix PC\u201d in the AirPlay menu on an iPhone or iPad.",
                      style="CardMuted.TLabel").pack(anchor="w", pady=(self.px(2), 0))
            return
        for label, connected, error, codec in devices:
            line = ttk.Frame(self.devices_frame, style="Card.TFrame")
            line.pack(fill="x", pady=(0, self.px(6)))
            color = PALETTE["error"] if error else (PALETTE["ok"] if connected else PALETTE["idle"])
            tk.Label(line, text="\u25cf", fg=color, bg=PALETTE["card"], font=("Segoe UI", 12)).pack(side="left", padx=(0, self.px(8)))
            ttk.Label(line, text=label, style="Device.TLabel").pack(side="left")
            detail = "Audio error" if error else ("Playing" if connected else "Connected")
            if codec:
                detail += f"  \u00b7  {codec}"
            ttk.Label(line, text=detail, style="CardMuted.TLabel").pack(side="left", padx=(self.px(12), 0), pady=(self.px(2), 0))

    def _fit_to_content(self, force=False):
        self.root.update_idletasks()
        width = max(self.px(760), self.root.winfo_reqwidth())
        height = self.root.winfo_reqheight()
        if force or self.root.winfo_height() < height:
            self.root.geometry(f"{width}x{height}")
        self.root.minsize(width, height)

    def change_mode(self, event=None):
        selected = next(key for key, label in core.MODE_LABELS.items() if label == self.mode.get())
        set_mode(self.icon, selected)

    def toggle_startup(self):
        settings["launchAtLogin"] = self.launch_at_login.get()
        save_current_settings()
        try:
            sync_startup_shortcut(settings["launchAtLogin"])
        except (OSError, subprocess.CalledProcessError) as error:
            self.icon.notify(f"Could not update startup shortcut: {error}", core.APP_NAME)

    def show(self, icon=None, item=None):
        self.root.after(0, self._show_now)

    def _show_now(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()

    def refresh(self):
        state = read_state()
        with state_lock:
            running = process is not None and process.poll() is None
            devices = tuple((s.label(), s.connected, s.error, s.codec) for s in sessions.active())
        if state["audio_error"] and not devices:
            status, color = "Audio error", PALETTE["error"]
        elif state["audio_connected"] or devices:
            status, color = "Connected", PALETTE["ok"]
        elif running:
            status, color = "Waiting for AirPlay", PALETTE["warn"]
        else:
            status, color = "Stopped", PALETTE["idle"]
        self.values["status"].set(status)
        self.status_badge.configure(bg=color)
        count = len(devices)
        self.values["device_count"].set("" if not count else ("1 device" if count == 1 else f"{count} devices"))
        if devices != self.rendered_devices:
            self._render_devices(devices)
            self.rendered_devices = devices
            self._fit_to_content()
        self.values["song"].set(current_song(state))
        self.values["quality"].set(state["quality"] or "Waiting for audio")
        self.values["output"].set(state["output"])
        metric = state.get("metrics", {})
        for key in METRIC_KEYS:
            self.metric_values[key].set(str(metric.get(key, 0)))
        self.values["pin"].set(f"Pairing PIN: {settings['pairingPin']}")
        self.mode.set(core.MODE_LABELS[settings["mode"]])
        self.root.after(1000, self.refresh)


def run_tray():
    global main_window
    modes = pystray.Menu(*(pystray.MenuItem(core.MODE_LABELS[m], mode_handler(m), checked=mode_checked(m), radio=True)
                           for m in core.VALID_MODES))
    icon = AirMixTrayIcon(
        "AirMixPC", create_icon_image(), f"{core.APP_NAME} v{core.APP_VERSION} ({settings['receiverName']})",
        menu=pystray.Menu(
            pystray.MenuItem("Open AirMix PC", lambda icon, item: main_window.show(), default=True),
            pystray.MenuItem(f"Version {core.APP_VERSION}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(host_text, None, enabled=False),
            pystray.MenuItem(sessions_text, None, enabled=False),
            pystray.MenuItem(status_text, None, enabled=False),
            pystray.MenuItem(output_text, None, enabled=False),
            pystray.MenuItem(mode_text, None, enabled=False),
            pystray.MenuItem(song_text, None, enabled=False),
            pystray.MenuItem(quality_text, None, enabled=False),
            pystray.MenuItem(pin_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Audio mode", modes),
            pystray.MenuItem("Start receiver", on_start, enabled=lambda item: not receiver_enabled),
            pystray.MenuItem("Stop receiver", on_stop, enabled=lambda item: receiver_enabled),
            pystray.MenuItem("Restart", on_restart, enabled=lambda item: receiver_enabled),
            pystray.MenuItem("Launch at sign-in", toggle_startup, checked=lambda item: settings["launchAtLogin"]),
            pystray.MenuItem("Reset trusted devices", reset_trust),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("View logs", on_view_logs),
            pystray.MenuItem("Edit settings", on_open_settings),
            pystray.MenuItem("Open install folder", on_open_folder),
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    main_window = AirMixWindow(icon)
    icon_thread = threading.Thread(target=icon.run, args=(setup,), daemon=True)
    icon_thread.start()
    try:
        main_window.root.mainloop()
    finally:
        stop_monitor.set()
        stop_uxplay()
        icon.stop()
        icon_thread.join(timeout=3)


def main():
    instance = SingleInstance()
    if not instance.acquired:
        return
    try:
        run_tray()
    finally:
        stop_monitor.set()
        stop_uxplay()
        instance.close()


if __name__ == "__main__":
    main()
