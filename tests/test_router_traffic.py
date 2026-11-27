"""QX210 traffic collector regression tests (offline, no router required)."""
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import hub_router_traffic as traffic


class RouterTrafficTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.object(traffic, "STATE_PATH", Path(self.tmp.name) / "usage.json")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_first_sample_is_baseline(self):
        with patch.object(traffic, "read_counters", return_value=(1000, 2000)):
            result = traffic.sample()
        self.assertEqual((result["tracked_rx_bytes"], result["tracked_tx_bytes"]), (0, 0))

    def test_deltas_survive_restart(self):
        with patch.object(traffic, "read_counters", side_effect=[(1000, 2000), (1300, 2500)]):
            traffic.sample()
            result = traffic.sample()
        self.assertEqual((result["tracked_rx_bytes"], result["tracked_tx_bytes"]), (300, 500))
        self.assertEqual(json.loads(traffic.STATE_PATH.read_text())["totals"], {"rx": 300, "tx": 500})

    def test_cellular_starts_its_own_baseline_without_erasing_ethernet(self):
        with patch.object(traffic, "read_counters", side_effect=[
            (1000, 2000), (1300, 2500), (10000, 20000), (10350, 20750)
        ]) as read_counters:
            traffic.sample()
            traffic.sample()
            baseline = traffic.sample(traffic.CELLULAR_INTERFACE)
            cellular = traffic.sample(traffic.CELLULAR_INTERFACE)

        self.assertEqual(baseline["interface"], "wwan0")
        self.assertEqual((baseline["tracked_rx_bytes"], baseline["tracked_tx_bytes"]), (0, 0))
        self.assertEqual((cellular["tracked_rx_bytes"], cellular["tracked_tx_bytes"]), (350, 750))
        self.assertEqual(json.loads(traffic.STATE_PATH.read_text())["totals"], {"rx": 300, "tx": 500})
        read_counters.assert_called_with("wwan0")

    def test_polling_selects_cellular_for_every_qx210(self):
        stop = Event()
        with (
            patch.object(traffic, "_status", {"status": "waiting"}),
            patch.object(traffic, "sample", return_value={"interface": "wwan0"}) as sample,
            patch.object(stop, "wait", side_effect=lambda _seconds: stop.set()),
        ):
            traffic.polling_loop(stop)

        sample.assert_called_once_with("wwan0")

    def test_reset_does_not_count_fake_traffic(self):
        with patch.object(traffic, "read_counters", side_effect=[(1000, 2000), (1300, 2500), (10, 20), (40, 50)]):
            traffic.sample()
            traffic.sample()
            reset = traffic.sample()
            result = traffic.sample()
        self.assertTrue(reset["counter_reset"])
        self.assertEqual((result["tracked_rx_bytes"], result["tracked_tx_bytes"]), (330, 530))


if __name__ == "__main__":
    unittest.main()
