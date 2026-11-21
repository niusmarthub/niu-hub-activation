"""NetBird status tests without Supervisor or network access."""
import json
import unittest
from unittest.mock import patch
import hub_netbird


class NetbirdStatusTest(unittest.TestCase):
    def test_tunnel_ip_detected(self):
        payload = json.dumps([{"flags": ["UP"], "addr_info": [{"family": "inet", "scope": "global", "local": "100.64.1.2"}]}])
        with patch.object(hub_netbird.subprocess, "check_output", return_value=payload):
            self.assertEqual(hub_netbird.tunnel_ipv4(), "100.64.1.2")

    def test_no_tunnel_is_not_reported_active(self):
        with patch.object(hub_netbird.subprocess, "check_output", side_effect=OSError):
            self.assertIsNone(hub_netbird.tunnel_ipv4())

    def test_running_addon_reports_active_tunnel(self):
        def supervisor(path, *args, **kwargs):
            return {"addons": [{"slug": "abc_netbird"}]} if path == "/addons" else {"state": "started"}
        with patch.object(hub_netbird, "supervisor", side_effect=supervisor), patch.object(hub_netbird, "tunnel_ipv4", return_value="100.64.1.2"):
            self.assertEqual(hub_netbird.provision_netbird({}, "REM-0021", None, lambda _: None), "Tunnel actif · 100.64.1.2")


if __name__ == "__main__":
    unittest.main()
