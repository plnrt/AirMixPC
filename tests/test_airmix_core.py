import json
from pathlib import Path
import tempfile
import unittest

from launcher import airmix_core as core


ROOT = Path(__file__).resolve().parents[1]


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
    def test_application_version_is_semver(self):
        self.assertRegex(core.APP_VERSION, r"^\d+\.\d+\.\d+$")

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


class MaxClientsTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(core.DEFAULT_SETTINGS["maxClients"], 4)
        self.assertEqual(core.MIN_CLIENTS, 1)
        self.assertEqual(core.MAX_CLIENTS, 12)

    def test_normalize_max_clients_invalid_inputs_fall_back_to_default(self):
        for value in (None, True, "abc", 2.5):
            with self.subTest(value=value):
                self.assertEqual(core.normalize_max_clients(value), 4)

    def test_normalize_max_clients_clamps_below_minimum(self):
        self.assertEqual(core.normalize_max_clients(0), 1)
        self.assertEqual(core.normalize_max_clients(-5), 1)

    def test_normalize_max_clients_clamps_above_maximum(self):
        self.assertEqual(core.normalize_max_clients(99), 12)

    def test_normalize_max_clients_accepts_numeric_string(self):
        self.assertEqual(core.normalize_max_clients("3"), 3)

    def test_normalize_max_clients_passes_through_valid_int(self):
        self.assertEqual(core.normalize_max_clients(7), 7)

    def test_normalize_settings_default_max_clients(self):
        self.assertEqual(core.normalize_settings({})["maxClients"], 4)

    def test_normalize_settings_numeric_string_max_clients(self):
        self.assertEqual(core.normalize_settings({"maxClients": "7"})["maxClients"], 7)

    def test_max_clients_arguments(self):
        self.assertEqual(core.max_clients_arguments(4), ["-maxclients", "4"])


class TelemetryFieldHelpersTests(unittest.TestCase):
    def test_decode_field_percent_encoding(self):
        self.assertEqual(core.decode_field("Artur%27s%20iPhone"), "Artur's iPhone")

    def test_decode_field_plus_is_not_space(self):
        self.assertEqual(core.decode_field("a+b"), "a+b")

    def test_session_id_from_int(self):
        self.assertEqual(core.session_id({"sid": 3}), 3)

    def test_session_id_from_numeric_string(self):
        self.assertEqual(core.session_id({"sid": "3"}), 3)

    def test_session_id_missing_defaults_to_zero(self):
        self.assertEqual(core.session_id({}), 0)

    def test_session_id_non_numeric_defaults_to_zero(self):
        self.assertEqual(core.session_id({"sid": "x"}), 0)


class SessionStateLabelTests(unittest.TestCase):
    def test_label_prefers_device(self):
        state = core.SessionState(sid=5, device="Artur's iPhone", model="iPhone17,1")
        self.assertEqual(state.label(), "Artur's iPhone")

    def test_label_falls_back_to_model_when_device_missing(self):
        state = core.SessionState(sid=5, model="iPhone17,1")
        self.assertEqual(state.label(), "iPhone17,1")

    def test_label_falls_back_to_session_id_when_device_and_model_missing(self):
        state = core.SessionState(sid=5)
        self.assertEqual(state.label(), "Session 5")


class SessionRegistryTests(unittest.TestCase):
    def test_start_creates_session_with_connected_state_and_codec_in_lines(self):
        registry = core.SessionRegistry()
        registry.apply_line("AIRMIX_EVENT start sid=1 device=Test%20iPad model=iPad codec=ALAC")
        self.assertEqual(registry.count(), 1)
        self.assertEqual(registry.get(1).device, "Test iPad")
        self.assertIs(registry.get(1).connected, True)
        self.assertEqual(registry.lines(), ["Test iPad — Connected (ALAC)"])

    def test_repeat_start_updates_device_without_losing_metrics(self):
        registry = core.SessionRegistry()
        registry.apply_line("AIRMIX_EVENT start sid=1 device=A model=M codec=ALAC")
        registry.apply_line(
            "AIRMIX_METRIC sid=1 received=100 missing=0 retransmitted=0 late=0 "
            "flushes=0 decoder_errors=0 sink_errors=0"
        )
        registry.apply_line("AIRMIX_EVENT start sid=1 device=B model=M2 codec=AAC_LC")
        state = registry.get(1)
        self.assertEqual(state.device, "B")
        self.assertEqual(state.metrics.get("received"), 100)

    def test_metric_for_unknown_sid_creates_session(self):
        registry = core.SessionRegistry()
        registry.apply_line(
            "AIRMIX_METRIC sid=2 received=500 missing=0 retransmitted=0 late=0 "
            "flushes=0 decoder_errors=0 sink_errors=0"
        )
        state = registry.get(2)
        self.assertIsNotNone(state)
        self.assertIs(state.connected, True)
        self.assertEqual(state.metrics.get("received"), 500)

    def test_error_marks_known_session_without_removing_it(self):
        registry = core.SessionRegistry()
        registry.apply_line(
            "AIRMIX_METRIC sid=2 received=1 missing=0 retransmitted=0 late=0 "
            "flushes=0 decoder_errors=0 sink_errors=0"
        )
        registry.apply_line("AIRMIX_EVENT error sid=2 type=sink reason=audio_error count=1")
        self.assertEqual(registry.count(), 1)
        self.assertIs(registry.get(2).error, True)

    def test_error_for_unknown_sid_returns_none_and_creates_nothing(self):
        registry = core.SessionRegistry()
        self.assertIsNone(registry.apply_line("AIRMIX_EVENT error sid=9 type=sink reason=audio_error count=1"))
        self.assertEqual(registry.count(), 0)

    def test_disconnect_removes_session_and_returns_removed_state(self):
        registry = core.SessionRegistry()
        registry.apply_line("AIRMIX_EVENT start sid=2 device=X model=Y codec=ALAC")
        removed = registry.apply_line("AIRMIX_EVENT disconnect sid=2 reason=ended")
        self.assertEqual(registry.count(), 0)
        self.assertEqual(removed.sid, 2)

    def test_repeat_disconnect_returns_none(self):
        registry = core.SessionRegistry()
        registry.apply_line("AIRMIX_EVENT start sid=2 device=X model=Y codec=ALAC")
        registry.apply_line("AIRMIX_EVENT disconnect sid=2 reason=ended")
        self.assertIsNone(registry.apply_line("AIRMIX_EVENT disconnect sid=2 reason=ended"))

    def test_disconnect_for_unknown_sid_returns_none(self):
        registry = core.SessionRegistry()
        self.assertIsNone(registry.apply_line("AIRMIX_EVENT disconnect sid=9 reason=ended"))

    def test_disconnect_without_sid_clears_entire_registry(self):
        registry = core.SessionRegistry()
        registry.apply_line("AIRMIX_EVENT start sid=1 device=A model=M codec=ALAC")
        registry.apply_line("AIRMIX_EVENT start sid=2 device=B model=M codec=ALAC")
        registry.apply_line("AIRMIX_EVENT disconnect reason=unexpected")
        self.assertEqual(registry.count(), 0)

    def test_zero_sid_is_ignored_for_start_metric_and_error(self):
        registry = core.SessionRegistry()
        self.assertIsNone(registry.apply_line("AIRMIX_EVENT start sid=0 device=A model=M codec=ALAC"))
        self.assertIsNone(
            registry.apply_line(
                "AIRMIX_METRIC sid=0 received=1 missing=0 retransmitted=0 late=0 "
                "flushes=0 decoder_errors=0 sink_errors=0"
            )
        )
        self.assertIsNone(registry.apply_line("AIRMIX_EVENT error sid=0 type=sink reason=audio_error count=1"))
        self.assertEqual(registry.count(), 0)

    def test_active_is_sorted_by_session_id(self):
        registry = core.SessionRegistry()
        registry.apply_line("AIRMIX_EVENT start sid=3 device=C model=M codec=ALAC")
        registry.apply_line("AIRMIX_EVENT start sid=1 device=A model=M codec=ALAC")
        self.assertEqual([state.sid for state in registry.active()], [1, 3])

    def test_lines_omit_codec_suffix_when_codec_is_empty(self):
        registry = core.SessionRegistry()
        registry.apply_line(
            "AIRMIX_METRIC sid=2 received=1 missing=0 retransmitted=0 late=0 "
            "flushes=0 decoder_errors=0 sink_errors=0"
        )
        self.assertEqual(registry.lines(), ["Session 2 — Connected"])

    def test_lines_show_error_state_without_codec_suffix(self):
        registry = core.SessionRegistry()
        registry.apply_line("AIRMIX_EVENT start sid=3 device=iPad model=iPad")
        registry.apply_line("AIRMIX_EVENT error sid=3 type=sink reason=audio_error count=1")
        self.assertEqual(registry.lines(), ["iPad — Error"])

    def test_summary_no_devices(self):
        self.assertEqual(core.SessionRegistry().summary(), "No devices")

    def test_summary_single_device_is_bare_label(self):
        registry = core.SessionRegistry()
        registry.apply_line("AIRMIX_EVENT start sid=1 device=iPad model=iPad codec=ALAC")
        self.assertEqual(registry.summary(), "iPad")

    def test_summary_two_devices(self):
        registry = core.SessionRegistry()
        registry.apply_line("AIRMIX_EVENT start sid=1 device=iPhone model=iPhone codec=ALAC")
        registry.apply_line("AIRMIX_EVENT start sid=2 device=iPad model=iPad codec=ALAC")
        self.assertEqual(registry.summary(), "2 devices: iPhone, iPad")


class AutoPolicyPerSessionTests(unittest.TestCase):
    def test_alternating_sessions_without_loss_do_not_promote(self):
        policy = core.AutoPolicy(now=lambda: 100.0)
        self.assertFalse(policy.observe_metric({"sid": 1, "received": 100, "missing": 0, "late": 0, "flushes": 0}))
        self.assertFalse(policy.observe_metric({"sid": 2, "received": 50, "missing": 0, "late": 0, "flushes": 0}))
        self.assertFalse(policy.observe_metric({"sid": 1, "received": 200, "missing": 0, "late": 0, "flushes": 0}))
        self.assertFalse(policy.observe_metric({"sid": 2, "received": 100, "missing": 0, "late": 0, "flushes": 0}))
        self.assertFalse(policy.stable)

    def test_bad_interval_on_one_session_promotes(self):
        policy = core.AutoPolicy(now=lambda: 100.0)
        self.assertFalse(policy.observe_metric({"sid": 1, "received": 100, "missing": 0, "late": 0, "flushes": 0}))
        self.assertFalse(policy.observe_metric({"sid": 2, "received": 50, "missing": 0, "late": 0, "flushes": 0}))
        self.assertTrue(policy.observe_metric({"sid": 2, "received": 150, "missing": 5, "late": 0, "flushes": 0}))
        self.assertTrue(policy.stable)

    def test_legacy_sidless_metric_keys_baseline_under_zero(self):
        policy = core.AutoPolicy(now=lambda: 100.0)
        policy.observe_metric({"received": 100, "missing": 0, "late": 0, "flushes": 0})
        self.assertIn(0, policy.last_metrics)

    def test_reset_session_clears_only_that_sessions_baseline(self):
        policy = core.AutoPolicy(now=lambda: 100.0)
        policy.observe_metric({"sid": 1, "received": 100, "missing": 0, "late": 0, "flushes": 0})
        policy.observe_metric({"sid": 2, "received": 50, "missing": 0, "late": 0, "flushes": 0})
        policy.reset_session(1)
        self.assertNotIn(1, policy.last_metrics)
        self.assertIn(2, policy.last_metrics)

    def test_reset_session_without_argument_clears_all_baselines(self):
        policy = core.AutoPolicy(now=lambda: 100.0)
        policy.observe_metric({"sid": 1, "received": 100, "missing": 0, "late": 0, "flushes": 0})
        policy.observe_metric({"sid": 2, "received": 50, "missing": 0, "late": 0, "flushes": 0})
        policy.reset_session()
        self.assertEqual(policy.last_metrics, {})


class VersionConsistencyTests(unittest.TestCase):
    def test_app_version_is_1_1_0(self):
        self.assertEqual(core.APP_VERSION, "1.1.0")

    def test_inno_setup_script_version(self):
        text = (ROOT / "installer" / "AirMixPC.iss").read_text(encoding="utf-8")
        self.assertIn('#define AppVersion "1.1.0"', text)

    def test_build_installer_script_names_output_twice(self):
        text = (ROOT / "installer" / "build-installer.ps1").read_text(encoding="utf-8")
        self.assertEqual(text.count("AirMixPC-Setup-1.1.0.exe"), 2)

    def test_readme_mentions_current_installer_name(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("AirMixPC-Setup-1.1.0.exe", text)

    def test_legacy_setup_script_display_version(self):
        text = (ROOT / "launcher" / "AirMixPC-Setup.ps1").read_text(encoding="utf-8")
        self.assertIn("DisplayVersion '1.1.0'", text)


if __name__ == "__main__":
    unittest.main()
