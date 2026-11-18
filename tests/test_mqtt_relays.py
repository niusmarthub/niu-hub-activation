import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "niusmart_activation"))
import hub_modules as hub


def module(device_id, datapoints, values):
    return {
        "id": device_id, "name": device_id, "product_id": None, "seen": True, "read": True,
        "datapoints": hub.public_datapoints({"datapoints": datapoints}, values),
    }


class MqttRelayTests(unittest.TestCase):
    def test_switch_discovery_only_replaces_relays_across_modules(self):
        modules = [
            module("control1", [{"dp_id": 1, "status_code": "switch_1"},
                                {"dp_id": 2, "status_code": "child_lock"}], {"1": True, "2": True}),
            module("control2", [{"dp_id": 1, "status_code": "switch"}], {"1": False}),
        ]
        with patch.object(hub, "mqtt_service"), patch.object(hub, "publish_samples") as publish:
            hub.publish_module_states("REM-0021", modules, 90, energy_enabled=False)

        messages = {topic: payload for topic, payload, _ in publish.call_args.args[1]}
        for device, state in (("control1", "on"), ("control2", "off")):
            config = json.loads(messages[f"homeassistant/switch/niusmart_{device}_dp1/config"])
            self.assertEqual(config["device"], {"identifiers": [f"niusmart_tuya_{device}"], "name": device})
            self.assertEqual(config["command_topic"], f"niusmart/REM-0021/tuya/{device}/1/set")
            self.assertEqual(messages[config["state_topic"]], state)
            self.assertEqual(messages[f"homeassistant/sensor/niusmart_{device}_dp1/config"], "")
        sensor = json.loads(messages["homeassistant/sensor/niusmart_control1_dp2/config"])
        self.assertEqual(sensor["device"]["identifiers"], ["niusmart_tuya_control1"])
        self.assertEqual(modules[0]["datapoints"][0]["mqtt_name"], "Switch 1")
        self.assertEqual(modules[0]["datapoints"][1]["mqtt_mode"], "sensor")

    def test_offline_and_removed_relays(self):
        tracked = {("control1", "1"), ("removed", "1")}
        offline = module("control1", [{"dp_id": 1, "status_code": "switch_1"}], {})
        offline.update(seen=False, read=False)
        save = Mock()
        with patch.object(hub, "mqtt_service"), patch.object(hub, "publish_samples") as publish:
            hub.publish_module_states("REM-0021", [offline], 90, energy_enabled=True,
                                      tracked_switches=tracked, save_switches=save)

        messages = {topic: payload for topic, payload, _ in publish.call_args.args[1]}
        self.assertEqual(messages["niusmart/REM-0021/tuya/control1/1/availability"], "offline")
        self.assertNotIn("niusmart/REM-0021/tuya/control1/1/state", messages)
        self.assertEqual(messages["homeassistant/switch/niusmart_removed_dp1/config"], "")
        save.assert_called_once_with({("control1", "1")})

    def test_commands_validate_module_datapoint_and_confirm_the_state(self):
        inventory = [{"tuya_device_id": "control1"}, {"tuya_device_id": "control2"}]
        endpoints = {"control1": {"discovered": True}, "control2": {"discovered": True}}
        datapoints = [{"dp_id": 1, "status_code": "switch_1"}, {"dp_id": 2, "status_code": "child_lock"}]
        modules = [
            module("control1", datapoints, {"1": False, "2": False}),
            module("control2", datapoints, {"1": True, "2": False}),
        ]
        now = monotonic()
        commands = [("control1", "1", True, now), ("control1", "2", True, now),
                    ("control2", "1", False, now - 60), ("unknown", "1", True, now)]
        with patch.object(hub, "set_confirmed_relay", return_value=True) as write:
            with ThreadPoolExecutor() as executor:
                hub.apply_relay_commands({"modules": inventory}, endpoints, modules, commands, executor,
                                         lambda: True, self.fail)
        write.assert_called_once_with(inventory[0], endpoints["control1"], "1", True)
        self.assertIs(modules[0]["datapoints"][0]["value"], True)
        self.assertIs(modules[0]["datapoints"][1]["value"], False)

    def test_unconfirmed_tuya_write_is_an_error(self):
        device = Mock()
        device.status.side_effect = [{"dps": {"1": False}}, {"dps": {"1": False}}]
        with patch.object(hub, "local_device", return_value=device):
            with self.assertRaisesRegex(RuntimeError, "confirmé"):
                hub.set_confirmed_relay({}, {"discovered": True}, "1", True)
        device.set_status.assert_called_once_with(True, switch=1)
