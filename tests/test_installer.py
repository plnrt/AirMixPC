from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.setup = (ROOT / "launcher" / "AirMixPC-Setup.ps1").read_text(encoding="utf-8")
        cls.uninstall = (ROOT / "launcher" / "AirMixPC-Uninstall.ps1").read_text(encoding="utf-8")
        cls.iss = (ROOT / "installer" / "AirMixPC.iss").read_text(encoding="utf-8")
        cls.helper = (ROOT / "installer" / "configure-system.ps1").read_text(encoding="utf-8")

    def test_firewall_is_program_scoped_private_only(self):
        self.assertIn("-Program $exe", self.setup)
        self.assertIn("-Profile Private", self.setup)
        self.assertNotIn("-Profile Public", self.setup)
        self.assertIn("-Protocol TCP", self.setup)
        self.assertIn("-Protocol UDP", self.setup)

    def test_known_wifi_profiles_are_private(self):
        self.assertIn("admin_new", self.setup)
        self.assertIn("admin_new_5g", self.setup)
        self.assertIn("-Name Category -Type DWord -Value 1", self.setup)

    def test_uninstall_is_surgically_scoped(self):
        self.assertIn("Get-NetFirewallRule -Group 'AirMix PC'", self.uninstall)
        self.assertNotIn("Bluetooth Audio Receiver", self.setup)
        self.assertNotIn("pnputil", self.uninstall.lower())

    def test_gui_installer_has_normal_lifecycle(self):
        self.assertIn("WizardStyle=modern", self.iss)
        self.assertIn("PrivilegesRequired=admin", self.iss)
        self.assertIn("[UninstallRun]", self.iss)
        self.assertIn("Repair AirMix PC", self.iss)
        self.assertIn("Ukrainian.isl", self.iss)

    def test_gui_installer_scopes_network_changes(self):
        self.assertIn("-Program $exe", self.helper)
        self.assertIn("-Profile Private", self.helper)
        self.assertNotIn("-Profile Public", self.helper)
        self.assertNotIn("pnputil", self.helper.lower())
        self.assertNotIn("Bluetooth Audio Receiver", self.helper)


if __name__ == "__main__":
    unittest.main()
