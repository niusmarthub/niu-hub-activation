#!/usr/bin/env python3
"""Niu Hub first-run activation UI. Intended to run inside a future HA OS add-on.
No third-party Python dependencies. Do not run directly in HA OS host shell.
"""
import json
import os
import re
import secrets
import ssl
import tempfile
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread, Event

DATA_DIR = Path(os.environ.get('NIU_DATA_DIR', '/data'))
CONFIG_FILE = DATA_DIR / 'hub_config.json'
PENDING_FILE = DATA_DIR / 'activation_pending.json'
ROTATION_FILE = DATA_DIR / 'key_rotation_pending.json'
SYNC_INTERVAL = max(60, int(os.environ.get('NIU_SYNC_INTERVAL', '60')))
STOP = Event()
ACTIVATION_URL = os.environ.get('NIU_ACTIVATION_URL', 'https://niusmart.com/hub-activation.php')
KEY_UPDATE_URL = os.environ.get('NIU_KEY_UPDATE_URL', 'https://niusmart.com/key-update.php')
BIND = os.environ.get('NIU_BIND', '0.0.0.0')
PORT = int(os.environ.get('NIU_PORT', '8099'))
LOCK = Lock()
CODE_PATTERN = re.compile(r'^[A-F0-9]{4}(?:-[A-F0-9]{4}){3}$')

HTML = '''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Activation Niu Hub</title><style>body{font-family:Arial,sans-serif;background:#f1f5f9;color:#14233c;display:grid;place-items:center;min-height:100vh;margin:0}.card{background:white;border-radius:16px;padding:32px;max-width:440px;width:calc(100% - 60px);box-shadow:0 6px 28px #0001}h1{margin-top:0}input,button{box-sizing:border-box;width:100%;font-size:20px;padding:14px;border-radius:9px;margin:8px 0}input{border:1px solid #bac7d8;text-align:center;text-transform:uppercase;letter-spacing:1px}button{background:#123b6d;color:white;border:0;cursor:pointer}button:disabled{opacity:.5}#msg{min-height:40px;white-space:pre-wrap}</style></head><body><main class="card"><h1>Niu Hub</h1><p>Entrez le code d’activation affiché dans NiuSmart.</p><form id="form"><input id="code" maxlength="19" placeholder="ABCD-1234-ABCD-1234" autocomplete="off" required><button id="go">Activer</button></form><p id="msg" role="status"></p></main><script>const f=document.getElementById('form'),b=document.getElementById('go'),m=document.getElementById('msg');f.addEventListener('submit',async e=>{e.preventDefault();b.disabled=true;m.textContent='Activation en cours…';try{const r=await fetch('/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:document.getElementById('code').value.trim().toUpperCase()})});const x=await r.json();m.textContent=x.message||'Réponse inconnue';if(x.success){f.hidden=true;}}catch(e){m.textContent='Connexion au service local impossible.'}finally{b.disabled=false}});fetch('/status').then(r=>r.json()).then(x=>{if(x.activated){f.hidden=true;m.textContent='Ce Niu Hub est déjà activé.'}else if(x.pending){m.textContent='Configuration reçue; confirmation en attente. Réessayez après rétablissement de la connexion.'}}).catch(()=>{});</script></body></html>'''


def write_json_atomic(path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.niu-', dir=DATA_DIR)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump(data, out, ensure_ascii=False, separators=(',', ':'))
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_json(path):
    try:
        with path.open(encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


def identifier():
    path = DATA_DIR / 'hub_identifier'
    if path.exists():
        value = path.read_text(encoding='ascii').strip()
        if re.fullmatch(r'niu-[a-f0-9]{32}', value):
            return value
        raise RuntimeError('Identifiant local invalide')
    value = 'niu-' + secrets.token_hex(16)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='ascii') as f:
        f.write(value + '\n')
    return value


def api(payload, url=ACTIVATION_URL):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type':'application/json','Accept':'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as response:
        result = json.load(response)
    if not isinstance(result, dict) or not result.get('success'):
        raise RuntimeError('Réponse serveur invalide')
    return result


def rotation_request(action, config, key, **extra):
    payload = {'action': action, 'site_id': config['site_id'],
               'hub_identifier': config['hub_identifier'], 'api_key': key}
    payload.update(extra)
    return api(payload, KEY_UPDATE_URL)


def resume_rotation(config):
    state = load_json(ROTATION_FILE)
    if not state:
        return False
    if config['site_id'] != 2 or state.get('site_id') != config['site_id'] or state.get('hub_identifier') != config['hub_identifier']:
        raise RuntimeError('Rotation associee a un autre Hub')
    old_key = state.get('old_api_key')
    new_key = state.get('new_api_key')
    if not isinstance(old_key, str) or not isinstance(new_key, str) or not old_key or not new_key:
        raise RuntimeError('Etat de rotation invalide')
    if config['api_key'] == old_key:
        # The server might have committed before the response was lost.
        # First attempt with the old key; if it fails, retry with the new key.
        try:
            result = rotation_request('commit', config, old_key, new_api_key=new_key)
        except urllib.error.HTTPError as error:
            if error.code not in (403, 409):
                raise
            result = rotation_request('commit', config, new_key, new_api_key=new_key)
        if result.get('status') != 'done':
            raise RuntimeError('Commit non confirme')
        config = dict(config)
        config['api_key'] = new_key
        write_json_atomic(CONFIG_FILE, config)
    elif config['api_key'] != new_key:
        raise RuntimeError('Cle locale differente de la rotation')
    # Keep recovery state until the acknowledgement is received.
    result = rotation_request('ack', config, new_key)
    if result.get('status') != 'done':
        raise RuntimeError('ACK non confirme')
    ROTATION_FILE.unlink(missing_ok=True)
    return True


def synchronize():
    with LOCK:
        config = load_json(CONFIG_FILE)
        if not isinstance(config, dict) or config.get('site_id') != 2 or PENDING_FILE.exists():
            return
        if ROTATION_FILE.exists():
            resume_rotation(config)
            config = load_json(CONFIG_FILE)
        if not isinstance(config.get('site_config'), dict):
            config['site_config'] = {}
        response = api({'action':'update', 'site_id':2,
                        'hub_identifier':config['hub_identifier'],
                        'api_key':config['api_key'],
                        'site_config':config['site_config']})
        changes = response.get('changes', {})
        if not isinstance(changes, dict):
            raise RuntimeError('Changements invalides')
        if changes:
            config = dict(config)
            config['site_config'] = dict(config['site_config'])
            config['site_config'].update(changes)
            write_json_atomic(CONFIG_FILE, config)
        if response.get('key_update_pending'):
            result = rotation_request('check', config, config['api_key'])
            if result.get('status') == 'pending':
                new_key = result.get('new_api_key')
                if not isinstance(new_key, str) or len(new_key) < 16:
                    raise RuntimeError('Nouvelle cle invalide')
                write_json_atomic(ROTATION_FILE, {
                    'site_id':2, 'hub_identifier':config['hub_identifier'],
                    'old_api_key':config['api_key'], 'new_api_key':new_key})
                resume_rotation(config)


def sync_loop():
    while not STOP.wait(SYNC_INTERVAL):
        try:
            synchronize()
        except Exception as exc:
            print('Niu Hub sync: ' + type(exc).__name__, flush=True)


def confirm_pending():
    pending = load_json(PENDING_FILE)
    config = load_json(CONFIG_FILE)
    if not pending or not config:
        return False
    result = api({'action':'confirm','activation_id':pending['activation_id'], 'hub_identifier':identifier(), 'api_key':config['api_key']})
    if result.get('status') != 'redeemed' or int(result.get('site_id', -1)) != config['site_id']:
        raise RuntimeError('Confirmation incohérente')
    PENDING_FILE.unlink(missing_ok=True)
    return True


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/':
            raw = HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path == '/status':
            self.send_json(200, {'activated':CONFIG_FILE.exists() and not PENDING_FILE.exists(), 'pending':PENDING_FILE.exists()})
        else:
            self.send_json(404, {'success':False,'message':'Page introuvable'})

    def do_POST(self):
        if self.path != '/activate':
            return self.send_json(404, {'success':False,'message':'Page introuvable'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > 1024:
                return self.send_json(400, {'success':False,'message':'Requête invalide'})
            body = json.loads(self.rfile.read(length))
            code = body.get('code', '') if isinstance(body, dict) else ''
            if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
                return self.send_json(400, {'success':False,'message':'Code invalide. Format attendu : XXXX-XXXX-XXXX-XXXX (hexadécimal).'})
            with LOCK:
                if PENDING_FILE.exists():
                    try:
                        confirm_pending()
                        return self.send_json(200, {'success':True,'message':'Activation confirmée.'})
                    except Exception:
                        return self.send_json(503, {'success':False,'message':'Confirmation en attente. Réessayez lorsque le serveur sera accessible.'})
                if CONFIG_FILE.exists():
                    return self.send_json(409, {'success':False,'message':'Hub déjà activé. Réinitialisation manuelle requise.'})
                result = api({'action':'activate','code':code,'hub_identifier':identifier()})
                key = result.get('api_key')
                device = result.get('device_code')
                site = result.get('site_id')
                aid = result.get('activation_id')
                if not isinstance(key,str) or not key or not isinstance(device,str) or not device or type(site) is not int or type(aid) is not int:
                    raise RuntimeError('Configuration incomplète')
                write_json_atomic(CONFIG_FILE, {'site_id':site,'device_code':device,'api_key':key,'api_base_url':'https://niusmart.com','hub_identifier':identifier(), 'site_config':result.get('site_config', {}) if site == 2 else {}})
                write_json_atomic(PENDING_FILE, {'activation_id':aid})
                try:
                    confirm_pending()
                except Exception:
                    return self.send_json(202, {'success':True,'message':'Configuration enregistrée. Confirmation serveur en attente; elle sera retentée au prochain démarrage.'})
                return self.send_json(200, {'success':True,'message':'Activation réussie. Configuration enregistrée.'})
        except urllib.error.HTTPError as e:
            return self.send_json(403 if e.code in (400,403) else 502, {'success':False,'message':'Code refusé ou expiré.' if e.code in (400,403) else 'Erreur du serveur NiuSmart.'})
        except (urllib.error.URLError, TimeoutError):
            return self.send_json(503, {'success':False,'message':'Serveur NiuSmart inaccessible. Réessayez.'})
        except Exception:
            return self.send_json(500, {'success':False,'message':'Activation impossible. Vérifiez les journaux locaux.'})


if __name__ == '__main__':
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    identifier()
    if PENDING_FILE.exists():
        try:
            confirm_pending()
        except Exception:
            pass
    Thread(target=sync_loop, daemon=True).start()
    print(f'Niu Hub activation listening on {BIND}:{PORT}', flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
