#!/usr/bin/env python3
"""Niu Hub Activation 0.3.0 — configuration protégée, bibliothèque standard.

Les cinq modes et leurs droits (aucune écriture dans secrets.yaml) :
1. ACTIVATION : crée une configuration COMPLETE, seulement sur un Hub vierge.
   Identité, site, unité, serveur HTTPS, clé et toutes les options sont requis.
   La clé de secours est créée après confirmation serveur, jamais avant.
2. MISE À JOUR : modifie UNIQUEMENT les champs autorisés de site_config.
   Aucun changement de clé, site, unité, identifiant ou serveur n'est accepté.
3. UPDATE API : journal persistant avant écriture; commit puis ACK serveur.
   Après commit la clé active change; la sauvegarde change après ACK confirmé.
   Les reprises utilisent le journal; une config incomplète bloque la rotation.
4. CHANGEMENT DE SERVEUR : réservé, DESACTIVE dans cette version. Toute
   différence est refusée sans modifier l'adresse. Le futur protocole devra
   valider le serveur de destination et la demande authentifiée de transfert.
5. RECUPERATION : si la config est absente, incomplète, corrompue, ou refusée
   pour authentification (401/403), demande une config complète avec le secours.
   Identité, site, unité, serveur et clé doivent rester ceux du secours confirmé.
   Une panne réseau conserve les fichiers et sera retentée sans réactivation.

Les modes 1/2/3 utilisent activate/confirm/update et key-update check/commit/ack.
Le mode 5 nécessite l'action recover côté NiuSmart (voir README).
Stockage persistant /data : hub_config.json, api_key_backup.json, identifiant,
ainsi que les journaux de transactions. Ne pas exécuter dans le shell hôte HA.
"""
import base64
import copy
import json
import os
import re
import secrets
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread

VERSION = '0.3.0'
DATA_DIR = Path(os.environ.get('NIU_DATA_DIR', '/data'))
CONFIG_FILE = DATA_DIR / 'hub_config.json'
BACKUP_FILE = DATA_DIR / 'api_key_backup.json'
PENDING_FILE = DATA_DIR / 'activation_pending.json'
ROTATION_FILE = DATA_DIR / 'key_rotation_pending.json'
SYNC_INTERVAL = max(60, int(os.environ.get('NIU_SYNC_INTERVAL', '60')))
ACTIVATION_URL = os.environ.get('NIU_ACTIVATION_URL', 'https://niusmart.com/hub-activation.php')
# Ancien override accepté uniquement sur le même serveur HTTPS que la config.
KEY_UPDATE_URL = os.environ.get('NIU_KEY_UPDATE_URL')
BIND = os.environ.get('NIU_BIND', '0.0.0.0')
PORT = int(os.environ.get('NIU_PORT', '8099'))
STOP = Event()
LOCK = Lock()
CODE_PATTERN = re.compile(r'^[A-F0-9]{4}(?:-[A-F0-9]{4}){3}$')
ID_PATTERN = re.compile(r'^niu-[a-f0-9]{32}$')
IDENTITY_FIELDS = ('hub_identifier', 'site_id', 'device_code', 'api_base_url')
# Contrat explicite : aucune option n'est devinée ni remplacée par un défaut.
COUNTS = {'toilet_count': (1, 10), 'door_count': (0, 10), 'climate_count': (0, 2)}
FLAGS = ('has_solar_panels', 'has_power_monitoring', 'has_outdoor_lighting',
         'has_water_supply', 'has_clean_water', 'has_waste_water')
SITE_FIELDS = frozenset((*COUNTS, *FLAGS))
MAX_RESPONSE = 1024 * 1024


def write_json_atomic(path, data):
    """Remplacement complet : l'appelant doit valider ET fusionner avant appel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.niu-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump(data, out, ensure_ascii=False, separators=(',', ':'))
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        # Persistance du renommage sur Linux après coupure d'alimentation.
        folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_json(path):
    try:
        with path.open(encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


def normalize_url(value):
    if not isinstance(value, str):
        raise ValueError('Adresse serveur absente')
    p = urllib.parse.urlsplit(value)
    if (p.scheme != 'https' or not p.hostname or p.username or p.password
            or p.query or p.fragment or any(c.isspace() for c in value)):
        raise ValueError('Adresse serveur HTTPS invalide')
    # Déclenche aussi la validation du port.
    p.port
    return value.rstrip('/')


def validate_site_config(value, complete=True):
    if not isinstance(value, dict) or set(value) - SITE_FIELDS:
        raise ValueError('Options inconnues ou invalides')
    if complete and set(value) != SITE_FIELDS:
        raise ValueError('Options du site incomplètes')
    for field, v in value.items():
        if field in COUNTS:
            lo, hi = COUNTS[field]
            if type(v) is not int or not lo <= v <= hi:
                raise ValueError('Nombre matériel invalide : ' + field)
        elif type(v) is not bool:
            raise ValueError('Option booléenne invalide : ' + field)
    return copy.deepcopy(value)


def validate_identity(value):
    if not isinstance(value, dict):
        raise ValueError('Identité absente')
    if not isinstance(value.get('hub_identifier'), str) or not ID_PATTERN.fullmatch(value['hub_identifier']):
        raise ValueError('Identifiant Hub invalide')
    if type(value.get('site_id')) is not int or value['site_id'] < 1:
        raise ValueError('Site invalide')
    if not isinstance(value.get('device_code'), str) or not value['device_code'].strip():
        raise ValueError('Nom unité absent')
    normalize_url(value.get('api_base_url'))
    if not isinstance(value.get('api_key'), str) or len(value['api_key']) < 16:
        raise ValueError('Clé API absente ou invalide')


def validate_config(value):
    validate_identity(value)
    validate_site_config(value.get('site_config'))
    return copy.deepcopy(value)


def is_valid(value):
    try:
        validate_config(value)
        return True
    except (ValueError, TypeError):
        return False


def same_identity(a, b):
    return all(a.get(f) == b.get(f) for f in IDENTITY_FIELDS)


def save_backup(config):
    """Uniquement après confirmation d'activation ou ACK de rotation."""
    validate_config(config)
    write_json_atomic(BACKUP_FILE, {f: config[f] for f in (*IDENTITY_FIELDS, 'api_key')})


def identifier():
    path = DATA_DIR / 'hub_identifier'
    if path.exists():
        value = path.read_text(encoding='ascii').strip()
        if ID_PATTERN.fullmatch(value):
            return value
        raise ValueError('Identifiant local invalide')
    # Réutiliser une identité persistante, jamais en inventer une pour récupérer.
    candidates = [load_json(CONFIG_FILE), load_json(BACKUP_FILE), load_json(PENDING_FILE)]
    value = None
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get('config', candidate)
            found = candidate.get('hub_identifier')
            if isinstance(found, str) and ID_PATTERN.fullmatch(found):
                value = found
                break
    if value is None:
        if any(p.exists() for p in (CONFIG_FILE, BACKUP_FILE, PENDING_FILE, ROTATION_FILE)):
            raise ValueError('Identité introuvable; intervention manuelle nécessaire')
        value = 'niu-' + secrets.token_hex(16)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='ascii') as f:
        f.write(value + '\n')
        f.flush()
        os.fsync(f.fileno())
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Ne jamais transmettre une clé vers une destination redirigée.
        raise urllib.error.HTTPError(req.full_url, code, 'Redirection refusée', headers, fp)


def api(payload, url=None):
    url = url or ACTIVATION_URL
    normalize_url(url)
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(req, timeout=15) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError('Réponse trop volumineuse')
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get('success') is not True:
        raise ValueError('Réponse serveur invalide')
    return result


def endpoint(config, name='hub-activation.php'):
    base = normalize_url(config['api_base_url'])
    if name == 'key-update.php' and KEY_UPDATE_URL:
        override = normalize_url(KEY_UPDATE_URL)
        if urllib.parse.urlsplit(override).netloc != urllib.parse.urlsplit(base).netloc:
            raise ValueError('Serveur de rotation différent')
        return override
    return base + '/' + name


def request(action, config, key=None, **extra):
    payload = {'action': action, 'site_id': config['site_id'],
               'hub_identifier': config['hub_identifier'], 'api_key': key or config['api_key']}
    payload.update(extra)
    return api(payload, endpoint(config))


def rotation_request(action, config, key, **extra):
    payload = {'action': action, 'site_id': config['site_id'],
               'hub_identifier': config['hub_identifier'], 'api_key': key}
    payload.update(extra)
    return api(payload, endpoint(config, 'key-update.php'))


def check_response_identity(response, config):
    for field in IDENTITY_FIELDS:
        if field in response and response[field] != config[field]:
            raise ValueError('Modification protégée refusée : ' + field)


def resume_rotation(config):
    config = validate_config(config)
    state = load_json(ROTATION_FILE)
    if not isinstance(state, dict):
        raise ValueError('Journal de rotation invalide')
    # Compatibilité avec le journal 0.2.0 : site et identifiant étaient seuls stockés.
    if state.get('site_id') != config['site_id'] or state.get('hub_identifier') != config['hub_identifier']:
        raise ValueError('Rotation associée à un autre Hub')
    for f in ('device_code', 'api_base_url'):
        if f in state and state[f] != config[f]:
            raise ValueError('Identité de rotation incohérente')
    old_key, new_key = state.get('old_api_key'), state.get('new_api_key')
    if any(not isinstance(k, str) or len(k) < 16 for k in (old_key, new_key)) or old_key == new_key:
        raise ValueError('Clés de rotation invalides')
    if config['api_key'] not in (old_key, new_key):
        raise ValueError('Clé locale différente de la rotation')
    backup = load_json(BACKUP_FILE)
    if backup is not None:
        validate_identity(backup)
        if not same_identity(backup, config) or backup['api_key'] not in (old_key, new_key):
            raise ValueError('Secours différent de la rotation')
    # Commit idempotent : réessayer la nouvelle clé si le serveur avait déjà validé.
    try:
        result = rotation_request('commit', config, old_key, new_api_key=new_key)
    except urllib.error.HTTPError as error:
        if error.code not in (401, 403, 409):
            raise
        result = rotation_request('commit', config, new_key, new_api_key=new_key)
    check_response_identity(result, config)
    if result.get('status') != 'done':
        raise ValueError('Commit non confirmé')
    if config['api_key'] != new_key:
        config['api_key'] = new_key
        validate_config(config)
        write_json_atomic(CONFIG_FILE, config)
    # Aucun remplacement du secours sur simple commit.
    result = rotation_request('ack', config, new_key)
    check_response_identity(result, config)
    if result.get('status') != 'done':
        raise ValueError('ACK non confirmé')
    save_backup(config)
    ROTATION_FILE.unlink(missing_ok=True)
    return config


def recover_config():
    if ROTATION_FILE.exists() or PENDING_FILE.exists():
        raise ValueError('Transaction en attente; récupération ordinaire bloquée')
    backup = load_json(BACKUP_FILE)
    validate_identity(backup)
    if backup['hub_identifier'] != identifier():
        raise ValueError('Secours associé à un autre Hub')
    existing = load_json(CONFIG_FILE)
    if isinstance(existing, dict):
        for f in IDENTITY_FIELDS:
            if f in existing and existing[f] != backup[f]:
                raise ValueError('Identité locale différente du secours')
        if existing.get('api_key') and existing['api_key'] != backup['api_key']:
            raise ValueError('Clé locale différente; aucune restauration automatique')
    response = request('recover', backup)
    restored = validate_config(response.get('config'))
    if not same_identity(restored, backup) or restored['api_key'] != backup['api_key']:
        raise ValueError('Récupération modifiant identité ou clé refusée')
    write_json_atomic(CONFIG_FILE, restored)
    return restored


def confirm_pending():
    pending = load_json(PENDING_FILE)
    if not isinstance(pending, dict) or type(pending.get('activation_id')) is not int:
        raise ValueError('Journal activation invalide')
    # 0.3.0 écrit le journal AVANT la config; reprise même après arrêt entre les deux.
    config = validate_config(pending.get('config', load_json(CONFIG_FILE)))
    if config['hub_identifier'] != identifier():
        raise ValueError('Activation associée à un autre Hub')
    current = load_json(CONFIG_FILE)
    if current is not None and current != config:
        raise ValueError('Configuration modifiée pendant activation')
    result = request('confirm', config, activation_id=pending['activation_id'])
    check_response_identity(result, config)
    if result.get('status') != 'redeemed' or result.get('site_id') != config['site_id']:
        raise ValueError('Confirmation incohérente')
    write_json_atomic(CONFIG_FILE, config)
    save_backup(config)
    PENDING_FILE.unlink(missing_ok=True)
    return config


def synchronize():
    with LOCK:
        if PENDING_FILE.exists():
            confirm_pending()
        config = load_json(CONFIG_FILE)
        if not is_valid(config):
            if BACKUP_FILE.exists():
                recover_config()
            elif CONFIG_FILE.exists():
                raise ValueError('Configuration incomplète; remise manuelle nécessaire sans secours')
            return
        if config['hub_identifier'] != identifier():
            raise ValueError('Identité locale incohérente')
        if ROTATION_FILE.exists():
            config = resume_rotation(config)
        try:
            response = request('update', config, site_config=config['site_config'])
        except urllib.error.HTTPError as error:
            if error.code in (401, 403) and BACKUP_FILE.exists():
                recover_config()
                return
            raise
        check_response_identity(response, config)
        if 'api_key' in response and response['api_key'] != config['api_key']:
            raise ValueError('Changement de clé hors rotation refusé')
        # Valider TOUT le message avant écriture pour éviter une mise à jour partielle.
        changes = validate_site_config(response.get('changes', {}), complete=False)
        if 'key_update_pending' in response and type(response['key_update_pending']) is not bool:
            raise ValueError('Indicateur rotation invalide')
        if changes:
            updated = copy.deepcopy(config)
            updated['site_config'].update(changes)
            validate_config(updated)
            if updated != config:
                write_json_atomic(CONFIG_FILE, updated)
                config = updated
        if response.get('key_update_pending'):
            result = rotation_request('check', config, config['api_key'])
            check_response_identity(result, config)
            if result.get('status') == 'pending':
                new_key = result.get('new_api_key')
                if not isinstance(new_key, str) or len(new_key) < 16 or new_key == config['api_key']:
                    raise ValueError('Nouvelle clé invalide')
                validate_config(config)
                state = {f: config[f] for f in IDENTITY_FIELDS}
                state.update(old_api_key=config['api_key'], new_api_key=new_key)
                write_json_atomic(ROTATION_FILE, state)
                resume_rotation(config)


def safe_log(exc):
    # Ni corps de réponse, ni URL, ni clé dans les journaux.
    detail = ' HTTP ' + str(exc.code) if isinstance(exc, urllib.error.HTTPError) else ''
    print('Niu Hub sync: ' + type(exc).__name__ + detail, flush=True)


def sync_loop():
    while not STOP.is_set():
        try:
            synchronize()
        except Exception as exc:
            safe_log(exc)
        STOP.wait(SYNC_INTERVAL)


def activation_config(result, hub_id):
    if type(result.get('activation_id')) is not int or result['activation_id'] < 1:
        raise ValueError('Activation ID absent')
    # Serveur choisi par le point d'activation connu, pas par une réponse arbitraire.
    p = urllib.parse.urlsplit(normalize_url(ACTIVATION_URL))
    base = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path.rsplit('/', 1)[0], '', '')).rstrip('/')
    if 'api_base_url' in result and normalize_url(result['api_base_url']) != base:
        raise ValueError('Serveur activation incohérent')
    if 'hub_identifier' in result and result['hub_identifier'] != hub_id:
        raise ValueError('Identité activation incohérente')
    return validate_config({'site_id': result.get('site_id'), 'device_code': result.get('device_code'),
                            'hub_identifier': hub_id, 'api_base_url': base,
                            'api_key': result.get('api_key'), 'site_config': result.get('site_config')})


HTML = '<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Activation Niu Hub</title><style>body{font-family:Arial,sans-serif;background:#f1f5f9;color:#14233c;display:grid;place-items:center;min-height:100vh;margin:0}.card{background:white;border-radius:16px;padding:32px;max-width:440px;width:calc(100% - 60px);box-shadow:0 6px 28px #0001}.logo{display:block;width:270px;max-width:100%;margin:0 auto 28px}h1{margin-top:0;font-size:26px}input,button{box-sizing:border-box;width:100%;font-size:20px;padding:14px;border-radius:9px;margin:8px 0}input{border:1px solid #bac7d8;text-align:center;text-transform:uppercase;letter-spacing:1px}button{background:#123b6d;color:white;border:0;cursor:pointer}button:disabled{opacity:.5}#msg{min-height:40px;white-space:pre-wrap}</style></head><body><main class="card"><img class="logo" src="data:image/png;base64,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" alt="NiuSmart"><h1>Activation Niu Hub</h1><p>Entrez le code d’activation affiché dans NiuSmart.</p><form id="form"><input id="code" maxlength="19" placeholder="ABCD-1234-ABCD-1234" autocomplete="off" required><button id="go">Activer</button></form><p id="msg" role="status"></p></main><script>const f=document.getElementById(\'form\'),b=document.getElementById(\'go\'),m=document.getElementById(\'msg\');f.addEventListener(\'submit\',async e=>{e.preventDefault();b.disabled=true;m.textContent=\'Activation en cours…\';try{const r=await fetch(\'activate\',{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify({code:document.getElementById(\'code\').value.trim().toUpperCase()})});const x=await r.json();m.textContent=x.message||\'Réponse inconnue\';if(x.success){f.hidden=true;}}catch(e){m.textContent=\'Connexion au service local impossible.\'}finally{b.disabled=false}});fetch(\'status\').then(r=>r.json()).then(x=>{if(x.activated){f.hidden=true;m.textContent=\'Ce Niu Hub est déjà activé.\'}else if(x.recovery_needed){f.hidden=true;m.textContent=\'Configuration à récupérer. Consultez les journaux du Hub.\'}else if(x.pending){m.textContent=\'Configuration reçue; confirmation en attente. Réessayez après rétablissement de la connexion.\'}}).catch(()=>{});</script></body></html>'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Eviter les URL arbitraires dans les journaux HTTP.
        pass

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
        path = urllib.parse.urlsplit(self.path).path
        if path in ('/', '/activation', '/activation/'):
            if path == '/activation':
                self.send_response(302)
                self.send_header('Location', '/activation/')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            raw = HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif path in ('/status', '/activation/status'):
            with LOCK:
                valid = is_valid(load_json(CONFIG_FILE))
                pending = PENDING_FILE.exists()
                damaged = CONFIG_FILE.exists() and not valid
                recovery = BACKUP_FILE.exists() and not valid
            self.send_json(200, {'activated': valid and not pending, 'pending': pending,
                                 'recovery_needed': damaged or recovery, 'version': VERSION})
        else:
            self.send_json(404, {'success': False, 'message': 'Page introuvable'})

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path not in ('/activate', '/activation/activate'):
            return self.send_json(404, {'success': False, 'message': 'Page introuvable'})
        try:
            # Pas de CORS : les formulaires externes ne peuvent pas lancer une activation.
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return self.send_json(415, {'success': False, 'message': 'Format JSON requis'})
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > 1024 or self.headers.get('Transfer-Encoding'):
                return self.send_json(400, {'success': False, 'message': 'Requête invalide'})
            self.connection.settimeout(15)
            body = json.loads(self.rfile.read(length))
            code = body.get('code', '') if isinstance(body, dict) else ''
            if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
                return self.send_json(400, {'success': False, 'message': 'Code invalide : XXXX-XXXX-XXXX-XXXX.'})
            with LOCK:
                if PENDING_FILE.exists():
                    try:
                        confirm_pending()
                        return self.send_json(200, {'success': True, 'message': 'Activation confirmée.'})
                    except Exception as exc:
                        safe_log(exc)
                        return self.send_json(503, {'success': False, 'message': 'Confirmation en attente.'})
                if any(p.exists() for p in (CONFIG_FILE, BACKUP_FILE, ROTATION_FILE)):
                    return self.send_json(409, {'success': False, 'message': 'Configuration existante. Aucune nouvelle activation autorisée.'})
                hub_id = identifier()
                result = api({'action': 'activate', 'code': code, 'hub_identifier': hub_id})
                config = activation_config(result, hub_id)
                # Journal d'abord : conserve la configuration reçue si l'écriture suivante échoue.
                write_json_atomic(PENDING_FILE, {'activation_id': result['activation_id'], 'config': config})
                write_json_atomic(CONFIG_FILE, config)
                try:
                    confirm_pending()
                except Exception as exc:
                    safe_log(exc)
                    return self.send_json(202, {'success': True, 'message': 'Configuration enregistrée. Confirmation retentée automatiquement.'})
                return self.send_json(200, {'success': True, 'message': 'Activation réussie.'})
        except urllib.error.HTTPError as exc:
            safe_log(exc)
            return self.send_json(403 if exc.code in (400, 401, 403) else 502,
                                  {'success': False, 'message': 'Code refusé ou expiré.' if exc.code in (400, 401, 403) else 'Erreur serveur NiuSmart.'})
        except (urllib.error.URLError, TimeoutError):
            return self.send_json(503, {'success': False, 'message': 'Serveur inaccessible. Réessayez.'})
        except Exception as exc:
            safe_log(exc)
            return self.send_json(500, {'success': False, 'message': 'Configuration refusée ou activation impossible. Consultez les journaux.'})


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    identifier()
    Thread(target=sync_loop, daemon=True).start()
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f'Niu Hub Activation {VERSION} — {BIND}:{PORT}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.server_close()


if __name__ == '__main__':
    main()
