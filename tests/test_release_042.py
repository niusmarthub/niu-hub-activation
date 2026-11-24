import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from hub_event_queue import EventQueue
import hub_modules
from test_activation import CONFIG, HUB_ID, SITE, hub


class BatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'events.sqlite3'
        self.queue = EventQueue(self.path)
        self.key = 'binary_sensor.n4dih32_di01'
        self.other = {**CONFIG, 'site_id': 37, 'device_code': 'REM-0037'}

    def append(self, index, config=CONFIG):
        return self.queue.append(config, self.key, 'n4dih32', ['on', 'off'][index % 2],
                                 1760000000 + index)

    def test_700_events_survive_restart_disconnect_and_partial_ack(self):
        expected = [self.append(i) for i in range(700)]
        self.append(0, self.other)
        queue = EventQueue(self.path)
        self.assertEqual(queue.status(CONFIG)['pending_events'], 700)
        self.assertEqual(queue.status(CONFIG)['oldest_event_at'], 1760000000)
        self.assertEqual(len(queue.pending(self.other)), 1)
        origin = CONFIG['api_base_url']
        reply = {'success': True, 'site_id': 2, 'device_code': 'REM-0021'}
        with patch('hub_event_queue.post_json', side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(urllib.error.URLError):
                queue.flush_once(CONFIG, origin)
        with patch('hub_event_queue.post_json', return_value={**reply, 'accepted': expected[:499]}):
            with self.assertRaises(RuntimeError):
                queue.flush_once(CONFIG, origin)
        self.assertEqual(queue.status(CONFIG)['pending_events'], 700)
        for part in (expected[:500], expected[500:]):
            with patch('hub_event_queue.post_json', return_value={**reply, 'accepted': part}):
                self.assertEqual(queue.flush_once({**CONFIG, 'api_key': 'rotated'}, origin), len(part))
        self.assertIsNotNone(queue.status(CONFIG)['last_success'])
        self.assertEqual(queue.status(CONFIG)['pending_events'], 0)
        self.assertEqual(queue.status(self.other)['pending_events'], 1)

    def test_disk_pressure_does_not_remove_pending_and_reports_order(self):
        self.append(0)
        with patch('hub_event_queue.MIN_FREE_BYTES', 10**20):
            self.assertIsNone(self.append(1))
            status = self.queue.status(CONFIG)
            self.assertEqual((status['disk_status'], status['pending_events'], status['rejected_events']),
                             ('blocked', 1, 1))
        self.assertIsNotNone(self.append(1))
        seen = []

        def acknowledge(_base, _config, _endpoint, payload):
            seen.extend(row['active'] for row in payload['disk_transitions'])
            return {'success': True, 'site_id': 2, 'device_code': 'REM-0021',
                    'accepted': [row['id'] for row in payload['disk_transitions']]}

        with patch('hub_event_queue.post_json', side_effect=acknowledge):
            self.queue.report(CONFIG, CONFIG['api_base_url'], 'eco')
            self.queue.report(CONFIG, CONFIG['api_base_url'], 'normal')
        self.assertEqual(seen, [True, False])

    def test_control_mode_is_scoped_to_hub(self):
        with patch.object(hub, 'ECO_CONTROL', (self.queue.identity(CONFIG), 'eco')):
            self.assertEqual(hub.requested_sync_mode(CONFIG), 'eco')
            self.assertEqual(hub.requested_sync_mode(self.other), 'normal')

    def test_activation_and_update_accept_site_other_than_pilot(self):
        with tempfile.TemporaryDirectory() as temp:
            shared = Path(temp) / 'homeassistant' / 'hub_config.json'
            with patch.object(hub, 'SHARED_FILE', shared):
                config = {**self.other, 'site_config': {**SITE, 'id': 37}}
                hub.save_active_config(config, activation=True)
                self.assertEqual(hub.load_active_config(), config)
                self.assertTrue(hub.validate_identity_for_sync(config))
                self.assertFalse(hub.validate_identity_for_sync({**config, 'site_id': 2}))
                self.assertEqual(hub.validate_backup(hub.make_backup(config))['hub_identifier'], HUB_ID)


class ModuleFallbackTests(unittest.TestCase):
    def test_local_then_cloud_then_last_value(self):
        feed = {'modules': [{'tuya_device_id': 'sensor', 'datapoints': []}]}
        endpoints = {'sensor': {'discovered': True, 'ip': '192.0.2.1', 'version': '3.3'}}
        cache = {}
        cloud_calls = []

        def cloud():
            cloud_calls.append(True)
            return {'sensor': {'1': 22}}, {}

        def snapshot(fallback=None):
            return hub_modules.snapshot_modules(feed, endpoints, self.fail, cloud_reader=fallback,
                                                last_values=cache)[0]

        with patch.object(hub_modules, 'read_datapoints', return_value={'1': 21}):
            self.assertEqual(snapshot(cloud)['source'], 'local')
            self.assertEqual(cloud_calls, [])
        with patch.object(hub_modules, 'read_datapoints', return_value=None):
            self.assertEqual(snapshot(cloud)['source'], 'tuya_cloud')
            cached = snapshot()
            self.assertEqual(cached['source'], 'cache')
            self.assertFalse(cached['read'])


class CloudSondeTests(unittest.TestCase):
    def test_reports_unchanged_temperature_and_keeps_old_data_as_cache_after_restart(self):
        from datetime import datetime, timedelta, timezone
        from hub_tuya_cloud import read_cloud_states

        now = datetime.now(timezone.utc)
        entity_id = 'sensor.rem_0021_tank_temp_temperature'
        device_id = 'tuya-th06'
        module = {'tuya_device_id': device_id, 'datapoints': [
            {'dp_id': 1, 'status_code': 'va_temperature', 'scale': 1},
        ]}
        feed = {'modules': [module]}
        endpoints = {device_id: {'discovered': False, 'ip': None, 'version': None}}
        devices = [{'id': 'ha-device', 'identifiers': [['tuya', device_id]]}]
        entities = [{'entity_id': entity_id, 'platform': 'tuya', 'device_id': 'ha-device',
                     'unique_id': 'tuya-th06-temperature'}]
        hour = (now - timedelta(hours=6)).isoformat()
        state = {'entity_id': entity_id, 'state': '19.2', 'last_updated': hour}
        with patch('hub_tuya_cloud._ws_request', return_value=(devices, entities, [state])):
            snapshots = hub_modules.snapshot_modules(
                feed, endpoints, self.fail, cloud_reader=lambda: read_cloud_states(feed),
            )
            self.assertEqual(snapshots[0]['source'], 'cache')
            self.assertFalse(snapshots[0]['read'])
            self.assertEqual(snapshots[0]['datapoints'][0]['raw'], 192)
            state['last_reported'] = now.isoformat()
            self.assertEqual(read_cloud_states(feed), ({device_id: {'1': 192}}, {}))
            state['last_reported'] = (now - timedelta(days=2)).isoformat()
            self.assertEqual(read_cloud_states(feed), ({}, {}))
