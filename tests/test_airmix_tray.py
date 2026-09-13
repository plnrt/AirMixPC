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


if __name__ == "__main__":
    unittest.main()
