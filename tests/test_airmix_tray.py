import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AIRMIXPC_DATA_DIR", str(ROOT / "build" / "test-data"))
spec = importlib.util.spec_from_file_location("airmix_tray", ROOT / "launcher" / "airmix_tray.pyw")
tray = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tray)


class TrayTests(unittest.TestCase):
    def test_receiver_arguments_are_audio_only_and_persistent(self):
        args = tray.receiver_arguments()
        self.assertEqual(args[args.index("-vs") + 1], "0")
        self.assertEqual(args[args.index("-pin") + 1], tray.settings["pairingPin"])
        self.assertIn("-reg", args)
        self.assertIn("-key", args)
        self.assertIn("wasapi2sink low-latency=false", args)

    def test_singleton_mutex(self):
        name = "Local\\AirMixPC.Test." + next(tempfile._get_candidate_names())
        first = tray.SingleInstance(name)
        second = tray.SingleInstance(name)
        try:
            self.assertTrue(first.acquired)
            self.assertFalse(second.acquired)
        finally:
            first.close()
            second.close()

    def test_rotating_log(self):
        with tempfile.TemporaryDirectory() as directory:
            old_dir, old_path = tray.LOG_DIR, tray.LOG_PATH
            tray.LOG_DIR = Path(directory)
            tray.LOG_PATH = Path(directory) / "AirMixPC.log"
            writer = tray.create_log_writer()
            try:
                handler = writer.handlers[0]
                self.assertEqual(handler.maxBytes, tray.LOG_MAX_BYTES)
                self.assertEqual(handler.backupCount, tray.LOG_BACKUP_COUNT)
                writer.info("test")
                handler.flush()
                self.assertTrue(tray.LOG_PATH.is_file())
            finally:
                for handler in list(writer.handlers):
                    writer.removeHandler(handler)
                    handler.close()
                tray.LOG_DIR, tray.LOG_PATH = old_dir, old_path


class MultiSessionTrayTests(unittest.TestCase):
    def setUp(self):
        # Isolate the module-level session registry between tests.
        tray.sessions.clear()

    def test_receiver_arguments_include_max_clients_and_stay_audio_only(self):
        args = tray.receiver_arguments()
        self.assertEqual(args[args.index("-maxclients") + 1], "4")
        self.assertEqual(args[args.index("-vs") + 1], "0")

    def test_sessions_is_a_session_registry(self):
        self.assertIsInstance(tray.sessions, tray.core.SessionRegistry)

    def test_handle_output_line_start_populates_session_lines_and_summary(self):
        tray.handle_output_line("AIRMIX_EVENT start sid=1 device=Test%20iPad model=iPad")
        self.assertEqual(tray.session_lines(), ["Test iPad — Connected"])
        self.assertEqual(tray.sessions_text(), "Devices: Test iPad")

    def test_handle_output_line_disconnect_clears_session_lines(self):
        tray.handle_output_line("AIRMIX_EVENT start sid=1 device=Test%20iPad model=iPad")
        tray.handle_output_line("AIRMIX_EVENT disconnect sid=1 reason=ended")
        self.assertEqual(tray.session_lines(), [])


if __name__ == "__main__":
    unittest.main()
