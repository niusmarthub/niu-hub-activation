import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "niusmart_activation"))
import hub_homeassistant as ha
from test_mqtt_relays import hub, module


class MqttFeaturesTests(unittest.TestCase):
    def test_two_modules_numbered_without_changing_command_topics(self):
        channels = [{"dp_id": i + 10, "status_code": f"switch_{i}"} for i in range(1, 7)]
        states = {str(i + 10): False for i in range(1, 7)}
        with patch.object(hub, "mqtt_service"), patch.object(hub, "publish_samples") as publish:
            hub.publish_module_states("REM-0021", [module("one", channels, states),
                                                    module("two", channels, states)], 90, energy_enabled=False)
        configs = [json.loads(payload) for topic, payload, _ in publish.call_args.args[1]
                   if topic.startswith("homeassistant/switch/")]
        self.assertEqual([entry["name"] for entry in configs], [f"Switch {i}" for i in range(1, 13)])
        self.assertEqual(configs[6]["command_topic"], "niusmart/REM-0021/tuya/two/11/set")

    def test_solar_groups_only_measured_or_explicitly_calculated_values(self):
        def sensor(object_id, state, unit):
            return {"entity_id": f"sensor.eco_worthy_{object_id}", "state": state,
                    "attributes": {"unit_of_measurement": unit}}
        states = [sensor("battery_voltage", "26.4", "V"),
                  sensor("battery_charging_current", "2", "A"),
                  sensor("pv_voltage", "83", "V"),
                  sensor("daily_generation", "240", "Wh"),
                  sensor("load_current", "0.4", "A")]
        with tempfile.TemporaryDirectory() as directory:
            messages, fields = ha.solar_payloads(Path(directory), "REM-0021", states)
        self.assertEqual(len(fields), 9)
        self.assertEqual(fields["pv_voltage"]["value"], "83")
        self.assertIsNone(fields["pv_energy_total"]["value"])
        self.assertIsNone(fields["load_energy_total"]["value"])
        self.assertEqual(fields["battery_charge_power"]["value"], "52.8")
        self.assertTrue(fields["battery_charge_power"]["estimated"])
        self.assertTrue(any(topic.endswith("/calculated/battery_charge_power/state") and value == "52.8"
                            for topic, value, _ in messages))

    def test_reload_is_retried_without_rewriting_grouped_yaml(self):
        configuration = "mqtt:\n  binary_sensor:\n" + "".join(
            f"    - name: DI{number:02d}\n      unique_id: rem-0021_n4dih32_di{number:02d}\n"
            "      state_topic: niusmart/data\n" for number in range(1, 33))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "configuration.yaml"
            path.write_text(configuration)
            flag = root / "pending"
            with patch.object(ha, "core_api", side_effect=[RuntimeError("unavailable"), []]) as core:
                with self.assertRaises(RuntimeError):
                    ha.configure_inputs(root, flag, lambda path, data: path.write_text(json.dumps(data)))
                grouped = path.read_text()
                self.assertTrue(flag.exists())
                ha.configure_inputs(root, flag, lambda path, data: self.fail("YAML was rewritten"))
            self.assertEqual(grouped.count('name: "NDIH-432 module"'), 32)
            self.assertEqual(path.read_text(), grouped)
            self.assertEqual((root / "configuration.yaml.niu-before-ndih432").read_text(), configuration)
            self.assertFalse(flag.exists())
            self.assertEqual(core.call_count, 2)
