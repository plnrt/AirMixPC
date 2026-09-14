from pathlib import Path
import unittest


SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "dnssd_embedded.c"
).read_text(encoding="utf-8")


class MdnsInterfacePolicyTests(unittest.TestCase):
    def test_windows_requests_gateway_information(self):
        self.assertIn("GAA_FLAG_INCLUDE_GATEWAYS", SOURCE)

    def test_windows_rejects_unrouted_and_tunnel_adapters(self):
        self.assertIn("adapter_has_ipv4_gateway", SOURCE)
        self.assertIn("if (!adapter_has_ipv4_gateway(adapter)) continue;", SOURCE)
        self.assertIn("adapter->IfType == IF_TYPE_PPP", SOURCE)
        self.assertIn("adapter->IfType == IF_TYPE_TUNNEL", SOURCE)

    def test_vpn_friendly_all_interface_comment_was_removed(self):
        self.assertNotIn("raising a VPN is picked up", SOURCE)


if __name__ == "__main__":
    unittest.main()
