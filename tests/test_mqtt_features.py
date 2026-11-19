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

    def test_solar_publishes_confirmed_and_probable_l01_l02_measures(self):
        from hub_solar import legacy_measures

        frame = (
            "01 03 1e 00 00 01 0a 01 09 00 00 00 00 01 09 00 06 00 00 "
            "00 0f 00 12 00 19 00 46 00 00 00 00 00 40 16 5a"
        )
        l02 = (
            "01 03 1e 13 fd 14 50 01 2c 01 f4 00 0a 00 0a 00 12 00 1e "
            "00 15 00 1e 00 05 00 0a 00 06 00 1e 00 01 06 4f"
        )
        state = {"entity_id": "sensor.eco_worthy_registres_modbus", "state": "30",
                 "attributes": {"trame_l01": frame, "trame_l02": l02}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            measures = legacy_measures(root, [state])
            load = legacy_measures(root, [state], "l02")
            state["attributes"]["trame_l01"] = frame[:-2] + "00"
            self.assertIsNone(legacy_measures(root, [state]))
            original_driver = {"entity_id": "sensor.eco_worthy_trame_modbus", "state": frame}
            self.assertEqual(legacy_measures(root, [state, original_driver]), measures)

        messages, fields = ha.solar_payloads("REM-0021", measures, load)
        groups = {entry["group"] for entry in fields.values()}
        self.assertEqual({group: sum(entry["group"] == group for entry in fields.values())
                          for group in groups}, {
            "État et température": 2, "Panneau solaire": 3, "Batterie": 3, "Sortie LOAD": 3,
        })
        self.assertEqual({key: (entry["value"], entry["unit"]) for key, entry in fields.items()
                          if entry["confidence"] == "confirmed"}, {
            "pv_voltage": ("26.6", "V"), "battery_voltage": ("26.5", "V"),
            "load_current": ("0.6", "A"), "battery_temperature": ("18", "°C"),
        })
        self.assertEqual({key: entry["value"] for key, entry in fields.items()
                          if entry["confidence"] == "probable"},
                         {"load_state": "ON", "load_voltage": "26.5", "pv_energy_total": "0"})
        self.assertTrue(all(entry["value"] is None for entry in fields.values()
                            if entry["confidence"] == "unmapped"))
        configs = {json.loads(payload)["unique_id"].rsplit("_eco_worthy_", 1)[1]: json.loads(payload)
                   for _, payload, retain in messages if retain}
        self.assertEqual(len(configs), 8)
        self.assertIn((configs["load_power"]["state_topic"], "15", False), messages)
        self.assertIn((configs["load_state"]["state_topic"], "ON", False), messages)
        self.assertEqual(configs["load_state"]["payload_off"], "OFF")
        self.assertNotIn("device_class", configs["pv_energy_total_raw"])
        self.assertEqual(configs["pv_energy_total_raw"]["entity_category"], "diagnostic")
        self.assertEqual(ha.solar_payloads("REM-0021", [0] * 15)[1]["load_current"]["value"], "0.0")
        self.assertEqual(ha.solar_payloads("REM-0021", None, [0] * 15)[1]["load_state"]["value"], "OFF")
        self.assertIsNone(ha.solar_payloads("REM-0021", None, [2] * 15)[1]["load_state"]["value"])
        self.assertEqual(ha.solar_payloads("REM-0021", None)[0], [])

    def test_climate_dashboard_reads_only_configured_zones(self):
        states = [{"entity_id": "sensor.r1_cool", "state": "72",
                   "attributes": {"unit_of_measurement": "°F"}},
                  {"entity_id": "sensor.r2_cool", "state": "75"},
                  {"entity_id": "sensor.r1_mode", "state": "unavailable"}]
        sources = {"sensor.r1_cool": (1, "cool_setpoint"), "sensor.r2_cool": (2, "cool_setpoint"),
                   "sensor.r1_mode": (1, "mode")}
        [zone] = ha.climate_readings(1, states, sources)
        self.assertEqual(zone["zone"], 1)
        self.assertIn({"name": "Consigne climatisation", "value": "72", "unit": "°F"},
                      zone["readings"])
        self.assertIn({"name": "Mode", "value": None}, zone["readings"])
        self.assertEqual(ha.climate_readings(0, states, sources), [])
        self.assertEqual(len(ha.climate_readings(2, [], {})), 2)

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
