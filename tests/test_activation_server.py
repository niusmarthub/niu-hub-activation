import copy
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'niusmart_activation/activation_server.py'
spec = importlib.util.spec_from_file_location('activation', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        m.DATA_DIR = Path(self.temp.name)
        for attr, name in [('CONFIG_FILE','hub_config.json'), ('BACKUP_FILE','api_key_backup.json'),
                           ('PENDING_FILE','activation_pending.json'), ('ROTATION_FILE','key_rotation_pending.json')]:
            setattr(m, attr, m.DATA_DIR / name)
        m.ACTIVATION_URL = 'https://niusmart.com/hub-activation.php'
        m.KEY_UPDATE_URL = None
        self.c = {'hub_identifier': 'niu-' + 'a'*32, 'site_id': 37, 'device_code': 'REM-0037',
                  'api_base_url': 'https://niusmart.com', 'api_key': 'old-test-key-' + 'x'*32,
                  'site_config': dict(toilet_count=2, door_count=0, climate_count=1,
                                      **{k:False for k in m.FLAGS})}
        (m.DATA_DIR/'hub_identifier').write_text(self.c['hub_identifier'])
        self.store()

    def store(self):
        m.write_json_atomic(m.CONFIG_FILE, self.c)

    def test_incomplete_activation_no_write(self):
        r = {**self.c, 'activation_id': 12}
        del r['site_config']
        before=m.CONFIG_FILE.read_bytes()
        with self.assertRaises(ValueError): m.activation_config(r,self.c['hub_identifier'])
        self.assertEqual(before,m.CONFIG_FILE.read_bytes())

    def test_all_required_fields(self):
        for f in list(self.c):
            c=copy.deepcopy(self.c); del c[f]
            with self.subTest(field=f), self.assertRaises(ValueError): m.validate_config(c)
        for f in m.SITE_FIELDS:
            c=copy.deepcopy(self.c); del c['site_config'][f]
            with self.subTest(field=f), self.assertRaises(ValueError): m.validate_config(c)

    def test_types_ranges_and_unknown_options(self):
        for field,value in [('toilet_count',True),('climate_count',3),('door_count',-1),
                            ('has_solar_panels',1),('api_key','attack')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.validate_site_config({field:value},complete=False)

    def test_dynamic_site_update_preserves_key_and_extra_metadata(self):
        self.c['custom_metadata']={'keep':True}; self.store()
        def call(payload,url=None):
            self.assertEqual(payload['site_id'],37)
            self.assertEqual(url,'https://niusmart.com/hub-activation.php')
            return {'success':True,'changes':{'climate_count':2}}
        with patch.object(m,'api',side_effect=call): m.synchronize()
        after=m.load_json(m.CONFIG_FILE)
        self.assertEqual(after['site_config']['climate_count'],2)
        self.assertEqual(after['api_key'],self.c['api_key'])
        self.assertEqual(after['custom_metadata'],self.c['custom_metadata'])

    def test_protected_fields_reject_entire_update(self):
        before=m.CONFIG_FILE.read_bytes()
        for r in [{'success':True,'changes':{'api_key':'bad'}},
                  {'success':True,'changes':{'climate_count':2},'site_id':2},
                  {'success':True,'api_base_url':'https://other.example','changes':{'climate_count':2}},
                  {'success':True,'api_key':'other-key','changes':{'climate_count':2}}]:
            with patch.object(m,'api',return_value=r), self.assertRaises(ValueError): m.synchronize()
            self.assertEqual(before,m.CONFIG_FILE.read_bytes())

    def test_network_failure_leaves_both_files(self):
        m.save_backup(self.c)
        before=[p.read_bytes() for p in (m.CONFIG_FILE,m.BACKUP_FILE)]
        with patch.object(m,'api',side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(urllib.error.URLError): m.synchronize()
        self.assertEqual(before,[p.read_bytes() for p in (m.CONFIG_FILE,m.BACKUP_FILE)])

    def test_confirm_before_backup(self):
        m.write_json_atomic(m.PENDING_FILE,{'activation_id':7,'config':self.c})
        with patch.object(m,'api',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError): m.confirm_pending()
        self.assertFalse(m.BACKUP_FILE.exists())
        with patch.object(m,'api',return_value={'success':True,'status':'redeemed','site_id':37}):
            m.confirm_pending()
        self.assertEqual(m.load_json(m.BACKUP_FILE)['api_key'],self.c['api_key'])
        self.assertFalse(m.PENDING_FILE.exists())

    def test_activation_resume_journal_only(self):
        m.CONFIG_FILE.unlink()
        m.write_json_atomic(m.PENDING_FILE,{'activation_id':7,'config':self.c})
        with patch.object(m,'api',return_value={'success':True,'status':'redeemed','site_id':37}):
            m.confirm_pending()
        self.assertEqual(m.load_json(m.CONFIG_FILE),self.c)

    def rotation(self):
        m.save_backup(self.c)
        state={f:self.c[f] for f in m.IDENTITY_FIELDS}
        state.update(old_api_key=self.c['api_key'],new_api_key='new-test-key-'+'y'*32)
        m.write_json_atomic(m.ROTATION_FILE,state)
        return state

    def test_rotation_ack_lost_keeps_backup_and_resumes(self):
        state=self.rotation()
        def call(action,*args,**kwargs):
            if action=='ack': raise TimeoutError()
            return {'status':'done'}
        with patch.object(m,'rotation_request',side_effect=call), self.assertRaises(TimeoutError):
            m.resume_rotation(self.c)
        self.assertEqual(m.load_json(m.CONFIG_FILE)['api_key'],state['new_api_key'])
        self.assertEqual(m.load_json(m.BACKUP_FILE)['api_key'],state['old_api_key'])
        self.assertTrue(m.ROTATION_FILE.exists())
        with patch.object(m,'rotation_request',return_value={'status':'done'}):
            m.resume_rotation(m.load_json(m.CONFIG_FILE))
        self.assertEqual(m.load_json(m.BACKUP_FILE)['api_key'],state['new_api_key'])
        self.assertFalse(m.ROTATION_FILE.exists())

    def test_rotation_commit_lost_retry_new_key(self):
        state=self.rotation(); keys=[]
        def call(action,config,key,**kwargs):
            keys.append(key)
            if key==state['old_api_key']:
                raise urllib.error.HTTPError('https://niusmart.com',403,'',{},None)
            return {'status':'done'}
        with patch.object(m,'rotation_request',side_effect=call): m.resume_rotation(self.c)
        self.assertEqual(keys,[state['old_api_key'],state['new_api_key'],state['new_api_key']])

    def test_rotation_incomplete_config_never_contacts_server(self):
        self.rotation(); del self.c['site_config']['climate_count']
        with patch.object(m,'rotation_request') as remote, self.assertRaises(ValueError):
            m.resume_rotation(self.c)
        remote.assert_not_called()

    def test_recovery_missing_config_and_missing_key(self):
        m.save_backup(self.c)
        for damaged in [None, {k:v for k,v in self.c.items() if k!='api_key'}]:
            if damaged is None: m.CONFIG_FILE.unlink(missing_ok=True)
            else: m.write_json_atomic(m.CONFIG_FILE,damaged)
            with patch.object(m,'api',return_value={'success':True,'config':self.c}): m.synchronize()
            self.assertEqual(m.load_json(m.CONFIG_FILE),self.c)

    def test_corrupted_json_recovery(self):
        m.save_backup(self.c); m.CONFIG_FILE.write_text('{broken')
        with patch.object(m,'api',return_value={'success':True,'config':self.c}): m.synchronize()
        self.assertEqual(m.load_json(m.CONFIG_FILE),self.c)

    def test_recovery_never_changes_identity_or_key(self):
        m.save_backup(self.c); m.CONFIG_FILE.unlink()
        for field,val in [('site_id',38),('api_base_url','https://other.example'),('api_key','z'*32)]:
            c=copy.deepcopy(self.c); c[field]=val
            with patch.object(m,'api',return_value={'success':True,'config':c}), self.assertRaises(ValueError):
                m.recover_config()
            self.assertFalse(m.CONFIG_FILE.exists())

    def test_atomic_failed_rename_preserves_config(self):
        before=m.CONFIG_FILE.read_bytes()
        with patch.object(m.os,'replace',side_effect=OSError('disk')), self.assertRaises(OSError):
            m.write_json_atomic(m.CONFIG_FILE,{'bad':True})
        self.assertEqual(before,m.CONFIG_FILE.read_bytes())
        self.assertEqual(list(m.DATA_DIR.glob('.niu-*')),[])

    def test_https_and_redirect_guard(self):
        for url in ['http://example.com','https://user:pass@example.com','https://example.com?a=b']:
            with self.assertRaises(ValueError): m.normalize_url(url)
        with self.assertRaises(urllib.error.HTTPError):
            m.NoRedirect().redirect_request(type('R',(),{'full_url':'https://niusmart.com'})(),None,302,'',{},'https://evil.example')



# Vérification HTTP locale : aucune connexion au vrai serveur NiuSmart.
class HTTPTests(unittest.TestCase):
    setUp = ConfigurationTests.setUp
    store = ConfigurationTests.store
    def test_http_page_logo_status_and_activation_guard(self):
        from threading import Thread
        import urllib.request
        server=m.ThreadingHTTPServer(('127.0.0.1',0),m.Handler)
        thread=Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_port)
            with urllib.request.urlopen(base+'/activation/') as r:
                html=r.read().decode()
                self.assertIn('data:image/png;base64,',html)
                self.assertIn('alt="NiuSmart"',html)
            with urllib.request.urlopen(base+'/status') as r:
                status=json.load(r)
                self.assertTrue(status['activated'])
                self.assertNotIn('api_key',status)
            req=urllib.request.Request(base+'/activate',data=b'{"code":"ABCD-1234-ABCD-1234"}',
                                       headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
            self.assertEqual(error.exception.code,409)
            req=urllib.request.Request(base+'/activate',data=b'code=ABCD')
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
            self.assertEqual(error.exception.code,415)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__": unittest.main()
