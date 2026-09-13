import json
from pathlib import Path
import tempfile
import unittest

from launcher import airmix_core as core


class SettingsTests(unittest.TestCase):
    def test_invalid_settings_fall_back_and_pin_is_created(self):
        settings = core.normalize_settings({"mode": "turbo", "receiverName": "  "})
        self.assertEqual(settings["mode"], "auto")
        self.assertEqual(settings["receiverName"], "AirMix PC")
        self.assertRegex(settings["pairingPin"], r"^\d{4}$")

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            value = core.normalize_settings({"mode": "stable", "receiverName": "Living Room"})
            core.save_settings(path, value)
            loaded = core.load_settings(path)
            self.assertEqual(loaded, value)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["mode"], "stable")


class ModeTests(unittest.TestCase):
    def test_profiles_have_explicit_wasapi_sink(self):
        for mode in ("stable", "balanced", "lowLatency"):
            args = core.mode_arguments(mode)
            self.assertIn("-as", args)
            self.assertTrue(any("wasapi2sink" in value for value in args))

    def test_auto_effective_mode(self):
        self.assertEqual(core.effective_mode("auto", False), "balanced")
        self.assertEqual(core.effective_mode("auto", True), "stable")


class TelemetryTests(unittest.TestCase):
    def test_parse_metric_and_event(self):
        self.assertEqual(
            core.parse_telemetry("AIRMIX_METRIC received=512 missing=2 retransmitted=2 late=0 flushes=0"),
            ("metric", {"received": 512, "missing": 2, "retransmitted": 2, "late": 0, "flushes": 0}),
        )
        self.assertEqual(core.parse_telemetry("AIRMIX_EVENT disconnect reason=network"),
                         ("event", {"disconnect": "", "reason": "network"}))

    def test_auto_promotes_on_packet_loss_or_two_disconnects(self):
        policy = core.AutoPolicy(now=lambda: 100.0)
        self.assertFalse(policy.observe_metric({"received": 100, "missing": 0, "late": 0, "flushes": 0}))
        self.assertTrue(policy.observe_metric({"received": 200, "missing": 2, "late": 0, "flushes": 0}))
        self.assertTrue(policy.stable)

        policy = core.AutoPolicy(now=lambda: 100.0)
        self.assertFalse(policy.observe_disconnect("network"))
        self.assertTrue(policy.observe_disconnect("network"))
        self.assertTrue(policy.stable)


if __name__ == "__main__":
    unittest.main()
