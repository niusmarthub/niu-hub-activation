import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "niusmart_activation"
sys.path.insert(0, str(SRC))
import hub_eco_diagnostic as eco
import hub_homeassistant as ha
import hub_mqtt


class OptionalModuleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = {}

    def load(self, key):
        return self.ledger.get(key)

    def save(self, key, value):
        self.ledger[key] = value

    def test_existing_matching_sensor_has_automatic_backup(self):
        config = self.root / "custom_components" / "eco_worthy"
        config.mkdir(parents=True)
        sensor = config / "sensor.py"
        sensor.write_bytes(b"original\r\n")
        base = self.root / "approved.sha256"
        base.write_text(eco.hashlib.sha256(b"original\n").hexdigest())
        payload = SRC / "eco_worthy_sensor.py"
        self.assertEqual(eco.install_eco_diagnostics(self.root, payload=payload, base_hash=base), "installed")
        self.assertEqual((config / "sensor.py.niu-before-registers").read_bytes(), b"original\r\n")
        self.assertEqual(eco.install_eco_diagnostics(self.root, payload=payload, base_hash=base), "installed")
        sensor.write_text("modification locale")
        self.assertEqual(eco.install_eco_diagnostics(self.root, payload=payload, base_hash=base), "different_version")
        self.assertEqual(sensor.read_text(), "modification locale")

    def test_direct_bluetooth_reader_exposes_two_blocks_with_stable_numbers(self):
        from hub_solar import direct_snapshot

        values = {"battery_voltage": 26.6, "load_voltage": 26.6, "temperature": 18, "load_state": "ON"}
        states, rows = direct_snapshot(values, list(range(15)), list(range(15, 30)))
        self.assertEqual(len(states), 4)
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[0]["decimale"], 0)
        self.assertEqual(rows[15]["adresse"], "0x0401")
        self.assertEqual(rows[29]["numero"], 30)

    def test_diagnostics_require_complete_blocks_and_keep_zeros(self):
        rows = [{"numero": i + 1, "adresse": f"0x{i if i < 15 else 0x0401 + i - 15:04X}",
                 "decimale": 0} for i in range(30)]
        state = {"entity_id": "sensor.eco_worthy_registres_modbus", "state": "30",
                 "attributes": {"registres": rows}}
        result = eco.diagnostic_rows([state])
        self.assertEqual(len(result), 30)
        self.assertEqual(result[15]["adresse"], "0x0401")
        self.assertEqual(result[3]["decimale"], 0)
        rows[6] = None
        self.assertEqual(eco.diagnostic_rows([state]), [])

    def test_solar_disable_retracts_owned_retained_config_and_can_reenable(self):
        states = [{"entity_id": "sensor.eco_worthy_pv_voltage", "state": "26.7",
                   "attributes": {"unit_of_measurement": "V"}}]
        with patch.object(ha, "mqtt_service"), patch.object(ha, "publish_samples") as broker:
            run = lambda enabled: ha.publish_solar(self.root, "REM-0021", enabled, "solar",
                                                   self.load, self.save, states=states)
            run(True)
            [topic] = self.ledger["solar"]["topics"]
            run(False)
            self.assertEqual(broker.call_args.args[1], [(topic, "", True)])
            self.assertEqual(self.ledger["solar"]["topics"], [])
            run(True)
            self.assertEqual(self.ledger["solar"]["topics"], [topic])

    def test_climate_removes_one_zone_and_all_when_disabled(self):
        storage = self.root / ".storage"
        storage.mkdir()
        entity_ids = [f"sensor.niu_dometic_r{zone}_temperature" for zone in (1, 2)]
        (storage / "core.entity_registry").write_text(json.dumps({"data": {"entities": [
            {"entity_id": entity_id, "platform": "niu_can", "unique_id": entity_id.split(".")[1]}
            for entity_id in entity_ids
        ]}}))
        states = [{"entity_id": entity_id, "state": "21", "attributes": {"unit_of_measurement": "°C"}}
                  for entity_id in entity_ids]
        with patch.object(ha, "mqtt_service"), patch.object(ha, "publish_samples") as broker:
            run = lambda count, source=states: ha.publish_climate(
                self.root, "REM-0021", count, "climate", self.load, self.save, states=source)
            self.assertEqual(run(2), 2)
            first, second = self.ledger["climate"]["topics"]
            self.assertEqual(run(1, None), 1)
            self.assertEqual(broker.call_args.args[1], [(second, "", True)])
            broker.side_effect = RuntimeError("broker offline")
            with self.assertRaises(RuntimeError):
                run(0, None)
            self.assertEqual(self.ledger["climate"]["topics"], [first])
            broker.side_effect = None
            self.assertEqual(run(0, None), 0)
            self.assertEqual(broker.call_args.args[1], [(first, "", True)])
            self.assertEqual(run(2), 2)

    def test_broker_provisioning_uses_existing_service_first(self):
        with patch.object(hub_mqtt, "mqtt_service", return_value={"host": "broker"}), \
                patch("hub_netbird.supervisor") as supervisor:
            self.assertEqual(hub_mqtt.provision_mqtt(self.root), "ready")
            supervisor.assert_not_called()
        with patch.object(hub_mqtt, "mqtt_service", side_effect=RuntimeError), \
                patch("hub_netbird.supervisor", side_effect=[{"addons": []}, {}, {"state": "stopped"}, {}]) as supervisor:
            self.assertEqual(hub_mqtt.provision_mqtt(self.root), "starting")
            self.assertEqual([call.args[0] for call in supervisor.call_args_list], [
                "/addons", "/store/addons/core_mosquitto/install",
                "/addons/core_mosquitto/info", "/addons/core_mosquitto/start",
            ])
