"""Tests sans reseau ni modification Home Assistant, bibliotheque standard."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

SCRIPT = Path(__file__).resolve().parents[1] / 'niusmart_activation' / 'activation_server.py'
spec = importlib.util.spec_from_file_location('niu_activation_test', SCRIPT)
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)

HUB_ID = 'niu-' + 'a' * 32
OLD_KEY = 'TEST_OLD_' + 'x' * 40
NEW_KEY = 'TEST_NEW_' + 'y' * 40
SITE = {
    'id': 2, 'name': 'REM-0021', 'nickname': None, 'client_id': 5,
    'address': None, 'city': None, 'province': None, 'postal_code': None,
    'timezone': 'America/Toronto', 'toilet_count': 2, 'door_count': 1,
    'has_fresh_water': 0, 'has_waste_water': 1, 'has_water_control': 0,
    'has_exterior_lighting': 0, 'has_power_monitoring': 1,
    'has_solar_panels': 1, 'active': 1, 'climate_count': 1,
}
CONFIG = {
    'site_id': 2, 'device_code': 'REM-0021', 'api_key': OLD_KEY,
    'api_base_url': 'https://niusmart.com',
    'hub_identifier': HUB_ID, 'site_config': SITE,
}


class ActivationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.orig = {}
        for name in ('DATA_DIR', 'CONFIG_FILE', 'BACKUP_FILE', 'PENDING_FILE', 'ROTATION_FILE', 'IDENTIFIER_FILE', 'api'):
            self.orig[name] = getattr(hub, name)
        self.addCleanup(self.restore)
        path = Path(self.tmp.name)
        hub.DATA_DIR = path
        hub.CONFIG_FILE = path / 'hub_config.json'
        hub.BACKUP_FILE = path / 'api_key_backup.json'
        hub.PENDING_FILE = path / 'activation_pending.json'
        hub.ROTATION_FILE = path / 'key_rotation_pending.json'
        hub.IDENTIFIER_FILE = path / 'hub_identifier'
        hub.IDENTIFIER_FILE.write_text(HUB_ID + '\n')

    def restore(self):
        for name, value in self.orig.items():
            setattr(hub, name, value)

    def put_config(self, obj=CONFIG):
        hub.write_json_atomic(hub.CONFIG_FILE, json.loads(json.dumps(obj)))

    def get_config(self):
        return hub.load_json(hub.CONFIG_FILE)

    def test_activation_requires_every_site_field(self):
        conf = dict(CONFIG)
        conf['site_config'] = dict(SITE)
        del conf['site_config']['climate_count']
        with self.assertRaises(ValueError):
            hub.validate_config(conf)
        with self.assertRaises(ValueError):
            hub.validate_config(dict(CONFIG, site_config=None))

    def test_update_cannot_change_key_or_server(self):
        self.put_config()
        for changes in ({'api_key': NEW_KEY}, {'api_base_url': 'https://other.example'}, {'secret': 's'}):
            with self.assertRaises(ValueError):
                hub.apply_site_changes(self.get_config(), changes)
            self.assertEqual(self.get_config()['api_key'], OLD_KEY)
            self.assertEqual(self.get_config()['api_base_url'], 'https://niusmart.com')

    def test_update_changes_only_site_options(self):
        self.put_config()
        config, changed = hub.apply_site_changes(self.get_config(), {'climate_count': 2, 'has_solar_panels': 0})
        self.assertTrue(changed)
        self.assertEqual(config['site_config']['climate_count'], 2)
        self.assertEqual(config['api_key'], OLD_KEY)
        self.assertEqual(config['hub_identifier'], HUB_ID)

    def test_legacy_site_config_missing_is_refilled_and_key_preserved(self):
        conf = dict(CONFIG)
        del conf['site_config']
        self.put_config(conf)
        self.assertTrue(hub.validate_identity_for_sync(conf))
        def fake_api(payload, url=None):
            self.assertEqual(payload['action'], 'update')
            self.assertEqual(payload['site_config'], {})
            return {'success': True, 'status': 'update', 'changes': dict(SITE), 'key_update_pending': False}
        hub.api = fake_api
        hub.synchronize()
        self.assertEqual(self.get_config()['site_config'], SITE)
        self.assertEqual(self.get_config()['api_key'], OLD_KEY)
        self.assertEqual(hub.load_json(hub.BACKUP_FILE)['api_key'], OLD_KEY)

    def test_backup_only_after_authenticated_update(self):
        self.put_config()
        self.assertFalse(hub.BACKUP_FILE.exists())
        hub.api = lambda payload, url=None: {'success': True, 'status': 'ok', 'changes': {}, 'key_update_pending': False}
        hub.synchronize()
        self.assertEqual(hub.load_json(hub.BACKUP_FILE)['api_key'], OLD_KEY)
        self.assertEqual(os.stat(hub.BACKUP_FILE).st_mode & 0o777, 0o600)

    def test_rotation_backup_waits_for_ack_and_recovers(self):
        self.put_config()
        hub.write_json_atomic(hub.BACKUP_FILE, hub.make_backup(CONFIG))
        stage = {'ack': False}
        def fake_api(payload, url=None):
            action = payload['action']
            if action == 'update':
                return {'success':True, 'status':'ok', 'changes':{}, 'key_update_pending':not stage['ack']}
            if action == 'check':
                return {'success':True, 'status':'pending', 'new_api_key':NEW_KEY}
            if action == 'commit':
                return {'success':True, 'status':'done'}
            if action == 'ack':
                if not stage['ack']:
                    raise RuntimeError('Coupure simulee avant ACK')
                return {'success':True, 'status':'done'}
            self.fail('Action inattendue: ' + action)
        hub.api = fake_api
        with self.assertRaises(RuntimeError):
            hub.synchronize()
        self.assertEqual(self.get_config()['api_key'], NEW_KEY)
        self.assertEqual(hub.load_json(hub.BACKUP_FILE)['api_key'], OLD_KEY)
        self.assertTrue(hub.ROTATION_FILE.exists())
        stage['ack'] = True
        hub.synchronize()
        self.assertEqual(hub.load_json(hub.BACKUP_FILE)['api_key'], NEW_KEY)
        self.assertFalse(hub.ROTATION_FILE.exists())

    def test_rotation_refuses_incomplete_site(self):
        conf = dict(CONFIG, site_config={'id': 2})
        self.put_config(conf)
        hub.write_json_atomic(hub.ROTATION_FILE, {
            'site_id':2,'hub_identifier':HUB_ID,'old_api_key':OLD_KEY,'new_api_key':NEW_KEY,
        })
        with self.assertRaises(ValueError):
            hub.resume_rotation(conf)
        self.assertEqual(self.get_config()['api_key'], OLD_KEY)

    def test_recovery_requires_full_matching_config(self):
        hub.write_json_atomic(hub.BACKUP_FILE, hub.make_backup(CONFIG))
        def fake_api(payload, url=None):
            self.assertEqual(payload['action'], 'recover')
            return {'success':True,'config':dict(CONFIG)}
        hub.api = fake_api
        self.assertTrue(hub.recover_if_needed())
        self.assertEqual(self.get_config(), CONFIG)

    def test_recovery_rejects_changed_key(self):
        hub.write_json_atomic(hub.BACKUP_FILE, hub.make_backup(CONFIG))
        hub.api = lambda payload, url=None: {'success':True,'config':dict(CONFIG, api_key=NEW_KEY)}
        with self.assertRaises(RuntimeError):
            hub.recover_if_needed()
        self.assertFalse(hub.CONFIG_FILE.exists())

    def test_web_page_logo_and_activation_missing_fields(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), hub.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        root = 'http://127.0.0.1:' + str(server.server_port)
        with urllib.request.urlopen(root + '/') as response:
            page = response.read().decode('utf-8')
            self.assertIn('src="/logo.png"', page)
        with urllib.request.urlopen(root + '/logo.png') as response:
            self.assertEqual(response.headers.get_content_type(), 'image/png')
            self.assertTrue(response.read().startswith(b'\x89PNG'))
        with urllib.request.urlopen(root + '/icon.png') as response:
            self.assertEqual(response.headers.get_content_type(), 'image/png')
            self.assertTrue(response.read().startswith(b'\x89PNG'))
        hub.api = lambda payload, url=None: {
            'success':True,'activation_id':41,'site_id':2,
            'device_code':'REM-0021','api_key':OLD_KEY,
            # site_config absent -> refus AVANT ecriture
        }
        payload = json.dumps({'code':'ABCD-1234-ABCD-1234'}).encode('utf-8')
        req = urllib.request.Request(root + '/activate', data=payload,
            headers={'Content-Type':'application/json'}, method='POST')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 500)
        self.assertFalse(hub.CONFIG_FILE.exists())
        self.assertFalse(hub.BACKUP_FILE.exists())


if __name__ == '__main__':
    unittest.main()
