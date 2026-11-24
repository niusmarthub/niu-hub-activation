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

    def test_solar_publishes_validated_l01_l02_measures(self):
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
            "État de la sortie": 1, "Panneau solaire": 2, "Batterie": 1, "Sortie LOAD": 3,
        })
        self.assertEqual({key: (entry["value"], entry["unit"]) for key, entry in fields.items()}, {
            "pv_voltage": ("26.6", "V"), "battery_voltage": ("26.5", "V"),
            "load_voltage": ("26.5", "V"), "load_state": ("ON", None),
            "pv_power": ("15", "W"),
            "load_current": ("0.6", "A"), "load_power": ("15", "W"),
        })
        configs = {json.loads(payload)["unique_id"].rsplit("_eco_worthy_", 1)[1]: json.loads(payload)
                   for _, payload, retain in messages if retain}
        self.assertEqual(len(configs), 8)
        for key, expected in (("pv_voltage", "26.6"), ("battery_voltage", "26.5"),
                              ("load_voltage", "26.5")):
            voltage = configs[key]
            self.assertEqual(voltage["suggested_display_precision"], 1)
            self.assertIn((voltage["state_topic"], expected, False), messages)
        temperature = configs["battery_temperature"]
        self.assertEqual(temperature["device_class"], "temperature")
        self.assertEqual(temperature["unit_of_measurement"], "°C")
        self.assertNotIn("entity_category", temperature)
        self.assertIn((temperature["state_topic"], "18", False), messages)
        self.assertNotIn("battery_temperature", fields)
        for key in ("load_power", "pv_power"):
            self.assertIn((configs[key]["state_topic"], "15", False), messages)
            self.assertEqual(configs[key]["unit_of_measurement"], "W")
        self.assertNotIn("entity_category", configs["load_voltage"])
        self.assertNotIn("entity_category", configs["load_state"])
        self.assertIn((configs["load_state"]["state_topic"], "ON", False), messages)
        self.assertEqual(configs["load_state"]["payload_off"], "OFF")
        self.assertNotIn("pv_energy_total_raw", configs)
        self.assertEqual(ha.solar_payloads("REM-0021", [0] * 15)[1]["load_current"]["value"], "0.0")
        self.assertEqual(ha.solar_payloads("REM-0021", None, [0] * 15)[1]["load_state"]["value"], "OFF")
        self.assertIsNone(ha.solar_payloads("REM-0021", None, [2] * 15)[1]["load_state"]["value"])
        self.assertEqual(ha.solar_payloads("REM-0021", None)[0], [])

    def test_web_solar_omits_unmapped_values_and_rejects_stale_reports(self):
        from datetime import datetime, timezone
        from hub_solar import recently_reported, web_solar_readings

        def iso(timestamp):
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

        now = 1760000000
        fresh = {"last_reported": iso(now - 3), "last_updated": iso(now - 100)}
        stale = {"last_reported": iso(now - 21), "last_updated": iso(now - 1)}
        fallback = {"last_updated": iso(now - 2)}
        malformed = {"last_reported": "invalid"}
        self.assertEqual(list(recently_reported([fresh, stale, fallback, malformed, {}], now)),
                         [fresh, fallback])
        measures, load = [0] * 15, [0] * 15
        measures[1], measures[8], measures[9] = 263, 15, 25
        _, fields = ha.solar_payloads("REM-0021", measures, load)
        readings = web_solar_readings(fields)
        self.assertEqual(readings["pv_voltage"], 26.3)
        self.assertEqual(readings["load_power"], 15.0)
        self.assertNotIn("pv_power", readings)
        self.assertNotIn("battery_temperature", readings)
        self.assertEqual(readings["load_state"], "OFF")
        self.assertNotIn("pv_energy_total", readings)
        self.assertNotIn("load_energy_total", readings)

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
                    ha.configure_inputs(root, flag, lambda path, data: path.write_text(json.dumps(data)), "REM-0021")
                grouped = path.read_text()
                self.assertTrue(flag.exists())
                ha.configure_inputs(root, flag, lambda path, data: self.fail("YAML was rewritten"), "REM-0021")
            self.assertEqual(grouped.count('name: "NDIH-432 module"'), 32)
            self.assertEqual(path.read_text(), grouped)
            self.assertEqual((root / "configuration.yaml.niu-before-ndih432").read_text(), configuration)
            self.assertFalse(flag.exists())
            self.assertEqual(core.call_count, 2)
