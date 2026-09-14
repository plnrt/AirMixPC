"""Pure configuration and adaptive-policy logic for AirMix PC."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import unquote


APP_NAME = "AirMix PC"
APP_VERSION = "1.1.0"
APP_SLUG = "AirMixPC"
DEFAULT_MAX_CLIENTS = 4
MIN_CLIENTS = 1
MAX_CLIENTS = 12
DEFAULT_SETTINGS = {
    "receiverName": APP_NAME,
    "mode": "auto",
    "launchAtLogin": True,
    "followDefaultOutput": True,
    "pausePhoneLink": False,
    "maxClients": DEFAULT_MAX_CLIENTS,
}
VALID_MODES = ("auto", "stable", "balanced", "lowLatency")
MODE_LABELS = {
    "auto": "Auto",
    "stable": "Stable (~2 s)",
    "balanced": "Balanced (~0.5 s)",
    "lowLatency": "Low latency (~0.25 s)",
}
METRIC_PATTERN = re.compile(r"^AIRMIX_METRIC\s+(.+)$")
EVENT_PATTERN = re.compile(r"^AIRMIX_EVENT\s+(.+)$")


def user_data_dir() -> Path:
    override = os.environ.get("AIRMIXPC_DATA_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_SLUG


def generate_pin() -> str:
    return f"{secrets.randbelow(9000) + 1000:04d}"


def normalize_max_clients(value) -> int:
    if isinstance(value, bool):
        return DEFAULT_MAX_CLIENTS
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value)
        except ValueError:
            return DEFAULT_MAX_CLIENTS
    else:
        return DEFAULT_MAX_CLIENTS
    return max(MIN_CLIENTS, min(MAX_CLIENTS, number))


def max_clients_arguments(max_clients: int) -> list[str]:
    return ["-maxclients", str(max_clients)]


def normalize_settings(raw: dict | None) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        for key in DEFAULT_SETTINGS:
            if key in raw:
                settings[key] = raw[key]
    if settings["mode"] not in VALID_MODES:
        settings["mode"] = "auto"
    name = str(settings["receiverName"]).strip()
    settings["receiverName"] = name[:64] or APP_NAME
    for key in ("launchAtLogin", "followDefaultOutput", "pausePhoneLink"):
        settings[key] = bool(settings[key])
    settings["maxClients"] = normalize_max_clients(settings["maxClients"])
    pin = raw.get("pairingPin") if isinstance(raw, dict) else None
    settings["pairingPin"] = str(pin) if str(pin or "").isdigit() and len(str(pin)) == 4 else generate_pin()
    return settings


def load_settings(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        raw = None
    return normalize_settings(raw)


def save_settings(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_settings(settings)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def effective_mode(configured_mode: str, auto_stable: bool = False) -> str:
    if configured_mode == "auto":
        return "stable" if auto_stable else "balanced"
    return configured_mode


def mode_arguments(mode: str) -> list[str]:
    """Return UxPlay arguments for one concrete (non-auto) profile."""
    if mode == "stable":
        return ["-async", "-al", "2.0", "-as", "wasapi2sink low-latency=false"]
    if mode == "balanced":
        return ["-async", "no", "-al", "0.5", "-as", "wasapi2sink low-latency=false"]
    if mode == "lowLatency":
        return ["-async", "no", "-al", "0.25", "-as", "wasapi2sink low-latency=true"]
    raise ValueError(f"Unknown concrete mode: {mode}")


def parse_fields(payload: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in payload.split():
        if "=" not in token:
            fields[token] = ""
            continue
        key, value = token.split("=", 1)
        fields[key] = value
    return fields


def decode_field(value: str) -> str:
    return unquote(value)


def session_id(payload: dict) -> int:
    try:
        return int(payload.get("sid", 0))
    except (TypeError, ValueError):
        return 0


def parse_telemetry(line: str) -> tuple[str, dict] | None:
    metric = METRIC_PATTERN.match(line.strip())
    if metric:
        fields = parse_fields(metric.group(1))
        parsed = {}
        for key, value in fields.items():
            try:
                parsed[key] = int(value)
            except ValueError:
                parsed[key] = value
        return "metric", parsed
    event = EVENT_PATTERN.match(line.strip())
    if event:
        return "event", parse_fields(event.group(1))
    return None


@dataclass
class SessionState:
    """One connected AirPlay client, as tracked from AIRMIX telemetry."""

    sid: int
    device: str = ""
    model: str = ""
    codec: str = ""
    connected: bool = False
    error: bool = False
    metrics: dict = field(default_factory=dict)

    def label(self) -> str:
        return self.device or self.model or f"Session {self.sid}"


class SessionRegistry:
    """Tracks per-session AirPlay client state from AIRMIX telemetry lines."""

    def __init__(self):
        self._sessions: dict[int, SessionState] = {}

    def apply(self, kind: str, payload: dict) -> SessionState | None:
        if kind == "metric":
            return self._apply_metric(payload)
        if kind == "event":
            return self._apply_event(payload)
        return None

    def _apply_metric(self, payload: dict) -> SessionState | None:
        sid = session_id(payload)
        if sid <= 0:
            return None
        state = self._sessions.get(sid)
        if state is None:
            state = SessionState(sid=sid, connected=True)
            self._sessions[sid] = state
        state.metrics = {key: value for key, value in payload.items() if key != "sid"}
        return state

    def _apply_event(self, payload: dict) -> SessionState | None:
        sid = session_id(payload)
        if "start" in payload:
            if sid <= 0:
                return None
            state = self._sessions.get(sid)
            if state is None:
                state = SessionState(sid=sid)
                self._sessions[sid] = state
            state.device = decode_field(payload.get("device", ""))
            state.model = decode_field(payload.get("model", ""))
            state.codec = decode_field(payload.get("codec", ""))
            state.connected = True
            state.error = False
            return state
        if "error" in payload:
            if sid <= 0:
                return None
            state = self._sessions.get(sid)
            if state is None:
                return None
            state.error = True
            return state
        if "disconnect" in payload:
            if sid <= 0:
                self.clear()
                return None
            return self._sessions.pop(sid, None)
        return None

    def apply_line(self, line: str) -> SessionState | None:
        telemetry = parse_telemetry(line)
        if telemetry is None:
            return None
        kind, payload = telemetry
        return self.apply(kind, payload)

    def get(self, sid: int) -> SessionState | None:
        return self._sessions.get(sid)

    def active(self) -> list[SessionState]:
        return [self._sessions[sid] for sid in sorted(self._sessions)]

    def count(self) -> int:
        return len(self._sessions)

    def clear(self) -> None:
        self._sessions.clear()

    def lines(self) -> list[str]:
        result = []
        for state in self.active():
            line = f"{state.label()} — {'Error' if state.error else 'Connected'}"
            if state.codec:
                line += f" ({state.codec})"
            result.append(line)
        return result

    def summary(self) -> str:
        labels = [state.label() for state in self.active()]
        if not labels:
            return "No devices"
        if len(labels) == 1:
            return labels[0]
        return f"{len(labels)} devices: " + ", ".join(labels)


@dataclass
class AutoPolicy:
    """Escalate Auto mode once per session when transport is demonstrably poor."""

    now: callable = time.monotonic
    stable: bool = False
    last_metrics: dict[int, dict[str, int]] = field(default_factory=dict)
    disconnects: list[float] = field(default_factory=list)

    def observe_metric(self, metric: dict) -> bool:
        if self.stable:
            return False
        sid = session_id(metric)
        numeric = {key: int(metric.get(key, 0)) for key in ("received", "missing", "late", "flushes")}
        baseline = self.last_metrics.get(sid)
        self.last_metrics[sid] = numeric
        if baseline is None:
            return False
        delta = {key: max(0, numeric[key] - baseline.get(key, 0)) for key in numeric}
        received = max(1, delta["received"])
        poor = delta["flushes"] >= 1 or delta["late"] >= 3 or delta["missing"] / received >= 0.01
        if poor:
            self.stable = True
        return poor

    def observe_disconnect(self, reason: str) -> bool:
        if self.stable or reason not in {"network", "audio_error", "unexpected"}:
            return False
        cutoff = self.now() - 300.0
        self.disconnects = [stamp for stamp in self.disconnects if stamp >= cutoff]
        self.disconnects.append(self.now())
        if len(self.disconnects) >= 2:
            self.stable = True
            return True
        return False

    def reset_session(self, sid: int | None = None) -> None:
        if sid is None:
            self.last_metrics = {}
        else:
            self.last_metrics.pop(sid, None)
