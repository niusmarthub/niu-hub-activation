#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NiuSmart | Niu Hub Activation -- version 0.3.1.

Architecture : cinq modes separes. Ne JAMAIS transformer une synchronisation
ordinaire en activation, rotation de cle ou changement de serveur.

MODE 1 - ACTIVATION (operationnel, seulement pour le site pilote REM-0021)
    POST /activate -> NiuSmart action=activate, puis action=confirm.
    Exige TOUS les champs obligatoires de la configuration du site avant
    d'ecrire hub_config.json. Aucune activation n'ecrase un fichier existant.
    La cle de secours est enregistree APRES confirmation de NiuSmart.

MODE 2 - MISE A JOUR (operationnel)
    NiuSmart action=update. Seuls les champs connus de site_config peuvent
    changer. api_key, hub_identifier, site_id, device_code, api_base_url
    restent intouchables. Une reponse invalide ne remplace aucun fichier.
    Une configuration de site anciennement incomplete peut etre completee
    avec les champs autorises provenant du serveur authentifie.

MODE 3 - UPDATE API / ROTATION DE CLE (operationnel sous reserve du serveur)
    NiuSmart key-update.php: check -> commit -> ack. La rotation exige que
    TOUS les champs obligatoires de hub_config.json soient presents/valides.
    Le journal prive key_rotation_pending.json permet la reprise apres coupure.
    api_key_backup.json n'est modifie qu'APRES l'ACK final du serveur.
    Ne jamais changer la cle lors d'une simple action=update.

MODE 4 - CHANGEMENT DE SERVEUR (reserve pour une version future)
    Une adresse differente ne suffit PAS a declencher un transfert.
    Le futur protocole devra demander l'approbation root, recevoir une preuve
    authentifiee du nouveau serveur et tester l'authentification du Hub AVANT
    toute modification de api_base_url. La sauvegarde ne change qu'apres ACK.
    AUCUN changement automatique de serveur n'est active dans cette version.

MODE 5 - RECUPERATION (client pret; endpoint serveur restant a implementer)
    Quand hub_config.json est absent/invalide, la cle de secours permet de
    demander au serveur d'origine une configuration COMPLETE par action=recover.
    La reponse doit etre authentifiee et correspondre au meme Hub/site/cle.
    Le serveur PHP actuel ne prend pas encore en charge action=recover :
    en cas d'echec, aucun fichier local n'est ecrase, et l'intervention manuelle
    reste possible. Ce mode NE cree PAS une nouvelle activation.

Fichiers persistants de l'add-on (dans /data, jamais dans le code installe) :
  hub_config.json               ancien format prive (migration uniquement)
  api_key_backup.json           identite + derniere cle et serveur CONFIRMES
  hub_identifier                identifiant stable, cree une seule fois
  activation_pending.json       confirmation initiale non terminee
  key_rotation_pending.json     reprise de rotation, cles sensibles (mode 0600)

Aucune dependance Python tierce. Executer dans le conteneur de l'add-on HA OS,
jamais directement dans le shell de l'hote Home Assistant.
"""

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

DATA_DIR = Path(os.environ.get('NIU_DATA_DIR', '/data'))
CONFIG_FILE = DATA_DIR / 'hub_config.json'
SHARED_FILE = Path(os.environ.get('NIU_SHARED_CONFIG', '/homeassistant/niusmart/hub_config.json'))
SHARED_ARCHIVE = SHARED_FILE.parent / 'archive' / 'hub_hub_config.json'
BACKUP_FILE = DATA_DIR / 'api_key_backup.json'
PENDING_FILE = DATA_DIR / 'activation_pending.json'
ROTATION_FILE = DATA_DIR / 'key_rotation_pending.json'
IDENTIFIER_FILE = DATA_DIR / 'hub_identifier'
LOGO_FILE = Path(__file__).with_name('logo.png')
ICON_FILE = Path(__file__).with_name('icon.png')

SYNC_INTERVAL = max(60, int(os.environ.get('NIU_SYNC_INTERVAL', '60')))
DEFAULT_BASE_URL = 'https://niusmart.com'
ACTIVATION_URL = os.environ.get('NIU_ACTIVATION_URL', DEFAULT_BASE_URL + '/hub-activation.php')
KEY_UPDATE_URL = os.environ.get('NIU_KEY_UPDATE_URL', DEFAULT_BASE_URL + '/key-update.php')
BIND = os.environ.get('NIU_BIND', '0.0.0.0')
PORT = int(os.environ.get('NIU_PORT', '8099'))
# Restriction TEMPORAIRE : le PHP actuel autorise la synchronisation du site 2.
PILOT_SITE_ID = 2
STOP = Event()
LOCK = Lock()
CODE_PATTERN = re.compile(r'^[A-F0-9]{4}(?:-[A-F0-9]{4}){3}$')
IDENTIFIER_PATTERN = re.compile(r'^niu-[a-f0-9]{32}$')

# Schema EXACT de hubSiteConfig() dans hub-activation_REM0021_update.php.
# Tout nouveau champ doit etre ajoute ici ET cote serveur avant publication.
SITE_TEXT_FIELDS = frozenset({
    'name', 'nickname', 'address', 'city', 'province', 'postal_code', 'timezone',
})
SITE_INTEGER_FIELDS = frozenset({'id', 'client_id', 'toilet_count', 'door_count', 'climate_count'})
SITE_FLAG_FIELDS = frozenset({
    'has_fresh_water', 'has_waste_water', 'has_water_control',
    'has_exterior_lighting', 'has_power_monitoring', 'has_solar_panels', 'active',
})
SITE_FIELDS = SITE_TEXT_FIELDS | SITE_INTEGER_FIELDS | SITE_FLAG_FIELDS
CONFIG_FIELDS = frozenset({
    'site_id', 'device_code', 'api_key', 'api_base_url', 'hub_identifier', 'site_config',
})

HTML = '''<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#071522"><title>NiuSmart | Activation Niu Hub</title>
<link rel="icon" href="/icon.png" type="image/png">
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{font-family:Arial,Helvetica,sans-serif;background:linear-gradient(145deg,#071522,#102b42);color:#e9f4fc;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}
.card{background:#12263b;border:1px solid #28516b;border-radius:18px;padding:30px;max-width:450px;width:100%;box-shadow:0 14px 45px #0005}
.logo{display:block;max-width:240px;width:78%;height:auto;margin:0 auto 24px}
h1{margin:0 0 10px;font-size:25px;color:white}p{line-height:1.5;color:#c6d9e7}
input,button{width:100%;font-size:18px;padding:14px;border-radius:9px;margin:8px 0}
input{background:#ecf4f9;border:1px solid #93afc1;color:#14233c;text-align:center;text-transform:uppercase;letter-spacing:1px}
button{background:#009bc9;color:white;border:0;cursor:pointer;font-weight:bold}button:disabled{opacity:.5;cursor:wait}
#msg{min-height:38px;white-space:pre-wrap;font-size:15px}
</style></head><body><main class="card">
<img src="/logo.png" alt="NiuSmart" class="logo"><h1>Activation Niu Hub</h1>
<p>Entrez le code d'activation affiché dans NiuSmart.</p>
<form id="form"><input id="code" maxlength="19" placeholder="ABCD-1234-ABCD-1234" autocomplete="off" required aria-label="Code d'activation">
<button id="go">Activer</button></form><p id="msg" role="status" aria-live="polite"></p>
</main><script>
const f=document.getElementById('form'),b=document.getElementById('go'),m=document.getElementById('msg');
f.addEventListener('submit',async e=>{e.preventDefault();b.disabled=true;m.textContent='Activation en cours…';
try{const r=await fetch('/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:document.getElementById('code').value.trim().toUpperCase()})});
const x=await r.json();m.textContent=x.message||'Réponse inconnue';if(x.success){f.hidden=true;}}
catch(e){m.textContent='Connexion au service local impossible.'}finally{b.disabled=false}});
fetch('/status').then(r=>r.json()).then(x=>{if(x.activated){f.hidden=true;m.textContent='Ce Niu Hub est déjà activé.'}
else if(x.pending){f.hidden=true;m.textContent='Configuration reçue; confirmation en attente.'}
else if(x.recovery_needed){f.hidden=true;m.textContent='Configuration incomplète. Récupération ou intervention requise.'}}).catch(()=>{});
</script></body></html>'''


def write_json_atomic(path, data):
    """Ecriture atomique et privee; ne jamais ecrire hors de /data."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if path.parent != DATA_DIR:
        raise RuntimeError('Chemin de configuration interdit')
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


def shared_to_internal(shared):
    """Adapter l'ancien protocole API au fichier HA sans perdre ses sections."""
    if not isinstance(shared, dict):
        return None
    hub = shared.get('hub')
    niu = shared.get('niusmart')
    if not isinstance(hub, dict) or not isinstance(niu, dict):
        return None
    return {
        'site_id': hub.get('site_id'),
        'device_code': hub.get('device_code'),
        'api_key': niu.get('api_key'),
        'api_base_url': niu.get('server_url'),
        'hub_identifier': shared.get('hub', {}).get('hub_identifier') or existing_identifier(),
        'site_config': shared.get('site_config', {}),
    }


def load_active_config():
    """Le fichier Home Assistant est prioritaire, jamais le JSON prive."""
    if SHARED_FILE.exists():
        return shared_to_internal(load_json(SHARED_FILE))
    # Ne jamais synchroniser un fichier prive a la place du fichier Home Assistant.
    return None


def save_active_config(config, activation=False):
    """Sauvegarde tournante avant remplacement, restauration sur echec.

    L'archive manuelle n'est jamais lue ni modifiee par cette fonction.
    """
    validate_config(config, require_full_site=False)
    if not SHARED_FILE.exists():
        # 0.3.1 ne doit jamais creer un fichier HA incomplet.
        raise RuntimeError('Fichier HA absent; activation reportee jusqu au modele complet')
    shared = load_json(SHARED_FILE)
    if not isinstance(shared, dict) or not isinstance(shared.get('hub'), dict) or not isinstance(shared.get('niusmart'), dict):
        raise RuntimeError('Fichier HA invalide; aucune modification')
    if shared['hub'].get('site_id') != PILOT_SITE_ID or shared['hub'].get('site_id') != config['site_id']:
        raise RuntimeError('Site HA different du site pilote')
    if shared['hub'].get('device_code') != config['device_code']:
        raise RuntimeError('Identite appareil differente')
    # Pas de changement de serveur ou cle lors d'une simple mise a jour.
    # Les changements sont effectues uniquement par le mode qui fournit config.
    if shared['niusmart'].get('server_url') != config['api_base_url']:
        raise RuntimeError('Changement serveur non autorise')
    if activation:
        if is_nonblank(shared['niusmart'].get('api_key')):
            raise RuntimeError('Cle deja presente: reactivation interdite')
        if is_nonblank(shared['hub'].get('hub_identifier')):
            raise RuntimeError('Identifiant deja present: reactivation interdite')
        shared['hub']['hub_identifier'] = config['hub_identifier']
    elif not is_nonblank(shared['niusmart'].get('api_key')):
        raise RuntimeError('Cle existante absente')
    shared['niusmart']['api_key'] = config['api_key']
    if 'hub_identifier' in shared['hub'] and shared['hub']['hub_identifier'] != config['hub_identifier']:
        raise RuntimeError('Identifiant Hub different')
    shared['site_config'] = config['site_config']
    # S'assurer que la section site est valide AVANT tout remplacement.
    if not isinstance(shared['site_config'], dict):
        raise RuntimeError('Options site invalides')
    validate_site_config(shared['site_config'], config['site_id'], complete=False)
    SHARED_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SHARED_FILE.exists():
        SHARED_ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
        # Sauvegarder les octets EXACTS, ne jamais toucher aux archives manuelles.
        raw = SHARED_FILE.read_bytes()
        if not raw:
            raise RuntimeError('Fichier HA vide; aucune modification')
        fd, tmp = tempfile.mkstemp(prefix='.hub-archive-', dir=SHARED_ARCHIVE.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                os.fchmod(out.fileno(), 0o600)
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            if Path(tmp).read_bytes() != raw:
                raise RuntimeError('Verification archive echouee')
            os.replace(tmp, SHARED_ARCHIVE)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    else:
        raw = None
    fd, tmp = tempfile.mkstemp(prefix='.hub-new-', dir=SHARED_FILE.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump(shared, out, ensure_ascii=False, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        if load_json(Path(tmp)) != shared:
            raise RuntimeError('Verification configuration echouee')
        os.replace(tmp, SHARED_FILE)
        if load_json(SHARED_FILE) != shared:
            if raw is not None:
                fd_restore, restore_tmp = tempfile.mkstemp(prefix='.hub-restore-', dir=SHARED_FILE.parent)
                try:
                    with os.fdopen(fd_restore, 'wb') as restore_out:
                        os.fchmod(restore_out.fileno(), 0o600)
                        restore_out.write(raw)
                        restore_out.flush()
                        os.fsync(restore_out.fileno())
                    os.replace(restore_tmp, SHARED_FILE)
                finally:
                    if os.path.exists(restore_tmp):
                        os.unlink(restore_tmp)
            raise RuntimeError('Verification apres ecriture echouee')
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_json(path):
    try:
        with path.open(encoding='utf-8') as stream:
            return json.load(stream)
    except (FileNotFoundError, ValueError, OSError):
        return None


def is_nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def validate_base_url(value):
    """Accepte uniquement une URL HTTPS sans identifiants ni query/fragment."""
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('Adresse de serveur manquante')
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ('', '/') or value.rstrip('/') != value):
        raise ValueError('Adresse de serveur non autorisee')
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError('Port serveur invalide') from exc
    return value


def endpoint(config, filename):
    base = validate_base_url(config['api_base_url'])
    # Compatibilite des URLs deja parametrables par les variables d'env.
    if base == DEFAULT_BASE_URL:
        return ACTIVATION_URL if filename == 'hub-activation.php' else KEY_UPDATE_URL
    return base + '/' + filename


def validate_site_config(site_config, site_id, complete=True):
    """Schema ferme: les mises a jour ne peuvent modifier que ces champs."""
    if not isinstance(site_config, dict):
        raise ValueError('site_config doit etre un objet')
    if set(site_config) - SITE_FIELDS:
        raise ValueError('Champ de site_config non autorise')
    if complete and set(site_config) != SITE_FIELDS:
        raise ValueError('Champs obligatoires de site_config manquants')
    for key, value in site_config.items():
        if key in SITE_TEXT_FIELDS:
            if value is not None and not isinstance(value, str):
                raise ValueError('Texte de site invalide')
            if key == 'name' and not is_nonblank(value):
                raise ValueError('Nom de site invalide')
        elif key in SITE_FLAG_FIELDS:
            if type(value) is not int or value not in (0, 1):
                raise ValueError('Option de site invalide')
        elif key in SITE_INTEGER_FIELDS:
            if key == 'client_id' and value is None:
                continue  # La colonne peut etre NULL avant association complete.
            if type(value) is not int or value < 0:
                raise ValueError('Nombre de site invalide')
    if 'id' in site_config and site_config['id'] != site_id:
        raise ValueError('Identifiant de site incoherent')
    if 'active' in site_config and site_config['active'] != 1:
        raise ValueError('Site inactif')
    if 'toilet_count' in site_config and not 1 <= site_config['toilet_count'] <= 10:
        raise ValueError('Nombre de toilettes invalide')
    if 'door_count' in site_config and not 0 <= site_config['door_count'] <= 10:
        raise ValueError('Nombre de portes invalide')
    if 'climate_count' in site_config and not 0 <= site_config['climate_count'] <= 2:
        raise ValueError('Nombre de climatiseurs invalide')
    return dict(site_config)


def validate_config(config, require_full_site=True):
    """Verifie TOUS les champs avant activation ou remplacement de cle."""
    if not isinstance(config, dict) or not CONFIG_FIELDS.issubset(config):
        raise ValueError('Champs obligatoires de hub_config manquants')
    if type(config['site_id']) is not int or config['site_id'] < 1:
        raise ValueError('site_id invalide')
    if not isinstance(config['hub_identifier'], str) or not IDENTIFIER_PATTERN.fullmatch(config['hub_identifier']):
        raise ValueError('hub_identifier invalide')
    if not is_nonblank(config['api_key']) or not is_nonblank(config['device_code']):
        raise ValueError('Cle API ou code appareil manquant')
    validate_base_url(config['api_base_url'])
    validate_site_config(config['site_config'], config['site_id'], complete=require_full_site)
    return config


def validate_identity_for_sync(config):
    """Tolere les anciens site_config incomplets pour les completer en update."""
    if not isinstance(config, dict):
        return False
    try:
        # Ancien fichier 0.2 : site_config parfois absent. Autoriser seulement
        # la lecture des options, jamais l'ecrasement de l'identite / cle.
        candidate = dict(config)
        if candidate.get('site_config') is None:
            candidate['site_config'] = {}
        validate_config(candidate, require_full_site=False)
        return candidate['site_id'] == PILOT_SITE_ID
    except ValueError:
        return False


def existing_identifier():
    """Read an existing local identifier without creating a new identity."""
    if not IDENTIFIER_FILE.is_file():
        return None
    value = IDENTIFIER_FILE.read_text(encoding='ascii').strip()
    return value if IDENTIFIER_PATTERN.fullmatch(value) else None


def hub_identity():
    """Prefer the identity assigned by NiuSmart, fall back to legacy local ID."""
    shared = load_json(SHARED_FILE)
    if isinstance(shared, dict) and isinstance(shared.get('hub'), dict):
        value = shared['hub'].get('hub_identifier')
        if isinstance(value, str) and IDENTIFIER_PATTERN.fullmatch(value):
            return value
    return existing_identifier()


def identifier():
    if IDENTIFIER_FILE.exists():
        value = IDENTIFIER_FILE.read_text(encoding='ascii').strip()
        if IDENTIFIER_PATTERN.fullmatch(value):
            return value
        raise RuntimeError('Identifiant local invalide')
    value = 'niu-' + secrets.token_hex(16)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(IDENTIFIER_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='ascii') as stream:
        stream.write(value + '\n')
    return value


def api(payload, url=ACTIVATION_URL):
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        url, data=data,
        headers={'Content-Type':'application/json', 'Accept':'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get('success') is not True:
        raise RuntimeError('Reponse serveur invalide')
    return result


def make_backup(config):
    """Sauvegarde exclusivement la derniere identite/cle/serveur CONFIRMES."""
    validate_config(config)
    return {
        'hub_identifier': config['hub_identifier'],
        'site_id': config['site_id'],
        'device_code': config['device_code'],
        'api_base_url': config['api_base_url'],
        'api_key': config['api_key'],
    }


def validate_backup(backup):
    if not isinstance(backup, dict) or set(backup) != (CONFIG_FIELDS - {'site_config'}):
        raise ValueError('Sauvegarde incomplete')
    if type(backup['site_id']) is not int or backup['site_id'] != PILOT_SITE_ID:
        raise ValueError('Site de secours invalide')
    if not isinstance(backup['hub_identifier'], str) or not IDENTIFIER_PATTERN.fullmatch(backup['hub_identifier']):
        raise ValueError('Identifiant de secours invalide')
    if backup['hub_identifier'] != hub_identity():
        raise ValueError('Sauvegarde associee a un autre Hub')
    if not is_nonblank(backup['api_key']) or not is_nonblank(backup['device_code']):
        raise ValueError('Cle de secours invalide')
    validate_base_url(backup['api_base_url'])
    return backup


def ensure_backup_after_authenticated_update(config):
    """Migration depuis v0.2 : creer backup seulement apres reponse API valide."""
    if BACKUP_FILE.exists():
        return  # Une synchronisation ne remplace JAMAIS le backup existant.
    if PENDING_FILE.exists() or ROTATION_FILE.exists():
        return
    try:
        backup = make_backup(config)
        validate_backup(backup)
    except ValueError:
        return
    write_json_atomic(BACKUP_FILE, backup)


def rotation_request(action, config, key, **extra):
    payload = {
        'action': action,
        'site_id': config['site_id'],
        'hub_identifier': config['hub_identifier'],
        'api_key': key,
    }
    payload.update(extra)
    return api(payload, endpoint(config, 'key-update.php'))


def resume_rotation(config):
    """Protocole confirme; backup mis a jour UNIQUEMENT apres ACK final."""
    state = load_json(ROTATION_FILE)
    if state is None:
        if ROTATION_FILE.exists():
            raise RuntimeError('Journal de rotation illisible; aucune modification')
        return False
    validate_config(config)  # TOUTES les proprietes sont obligatoires.
    if (config['site_id'] != PILOT_SITE_ID or state.get('site_id') != config['site_id']
            or state.get('hub_identifier') != config['hub_identifier']):
        raise RuntimeError('Rotation associee a un autre Hub')
    old_key = state.get('old_api_key')
    new_key = state.get('new_api_key')
    if not is_nonblank(old_key) or not is_nonblank(new_key) or old_key == new_key or len(new_key) < 16:
        raise RuntimeError('Journal de rotation invalide')
    # Ne jamais faire la rotation si une sauvegarde connue est incoherente.
    backup = load_json(BACKUP_FILE)
    if backup is not None:
        validate_backup(backup)
        if backup['api_key'] not in (old_key, new_key):
            raise RuntimeError('Cle de secours incoherente; rotation arretee')
    if config['api_key'] == old_key:
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
        validate_config(config)
        save_active_config(config)
    elif config['api_key'] != new_key:
        raise RuntimeError('Cle locale differente de la rotation')
    # S'il y a eu une coupure, on refait l'ACK avant de toucher au backup.
    result = rotation_request('ack', config, new_key)
    if result.get('status') != 'done':
        raise RuntimeError('ACK final non confirme')
    write_json_atomic(BACKUP_FILE, make_backup(config))
    ROTATION_FILE.unlink(missing_ok=True)
    return True


def apply_site_changes(config, changes):
    """Le mode update ne peut modifier AUCUN champ hors de site_config."""
    if not isinstance(changes, dict):
        raise ValueError('Liste des changements invalide')
    if not changes:
        return config, False
    if not set(changes).issubset(SITE_FIELDS):
        raise ValueError('Update interdit: champ hors site_config')
    current = config.get('site_config')
    if current is None:
        current = {}  # Compatibilite avec un ancien hub_config incomplet.
    if not isinstance(current, dict):
        raise ValueError('Configuration locale du site illisible')
    merged = dict(current)
    merged.update(changes)
    validate_site_config(merged, config['site_id'], complete=False)
    if merged == current:
        return config, False
    updated = dict(config)
    updated['site_config'] = merged
    validate_config(updated, require_full_site=False)
    return updated, True


def synchronize():
    """Mise a jour des seuls champs du site; aucune rotation implicite."""
    with LOCK:
        config = load_active_config()
        if not validate_identity_for_sync(config) or PENDING_FILE.exists():
            return
        if ROTATION_FILE.exists():
            resume_rotation(config)
            config = load_active_config()
            if not validate_identity_for_sync(config):
                raise RuntimeError('Configuration invalide apres rotation')
        response = api({
            'action':'update', 'site_id':config['site_id'],
            'hub_identifier':config['hub_identifier'],
            'api_key':config['api_key'],
            'site_config':config.get('site_config') or {},
        }, endpoint(config, 'hub-activation.php'))
        # Refuser les champs inattendus: une reponse update ne dicte jamais une cle/serveur.
        if set(response) - {'success', 'status', 'changes', 'key_update_pending'}:
            raise RuntimeError('Reponse update contient un champ non autorise')
        changes = response.get('changes', {})
        updated, changed = apply_site_changes(config, changes)
        if changed:
            if updated['api_key'] != config['api_key'] or updated['api_base_url'] != config['api_base_url']:
                raise RuntimeError('Modification de cle ou serveur interdite')
            save_active_config(updated)
            config = updated
        # Au premier update authentifie, initialiser le backup des anciens Hubs.
        ensure_backup_after_authenticated_update(config)
        if response.get('key_update_pending') is True:
            # Rotation distincte: exige une config COMPLETE, refuse sinon.
            validate_config(config)
            result = rotation_request('check', config, config['api_key'])
            if result.get('status') == 'pending':
                new_key = result.get('new_api_key')
                if not isinstance(new_key, str) or len(new_key) < 16 or new_key == config['api_key']:
                    raise RuntimeError('Nouvelle cle invalide')
                write_json_atomic(ROTATION_FILE, {
                    'site_id': config['site_id'],
                    'hub_identifier': config['hub_identifier'],
                    'old_api_key': config['api_key'],
                    'new_api_key': new_key,
                })
                resume_rotation(config)


def confirm_pending():
    pending = load_json(PENDING_FILE)
    config = load_active_config()
    if not isinstance(pending, dict) or not isinstance(config, dict):
        return False
    validate_config(config)
    if config['hub_identifier'] != hub_identity() or config['site_id'] != PILOT_SITE_ID:
        raise RuntimeError('Activation associee a un autre Hub')
    aid = pending.get('activation_id')
    if type(aid) is not int or aid < 1:
        raise RuntimeError('Etat activation invalide')
    result = api({
        'action': 'confirm', 'activation_id':aid,
        'hub_identifier':config['hub_identifier'], 'api_key':config['api_key'],
    }, endpoint(config, 'hub-activation.php'))
    if result.get('status') != 'redeemed' or result.get('site_id') != config['site_id']:
        raise RuntimeError('Confirmation activation incoherente')
    # Ne jamais remplacer un backup d'un autre Hub/site pendant l'activation.
    existing = load_json(BACKUP_FILE)
    if existing is not None:
        validate_backup(existing)
        if existing != make_backup(config):
            raise RuntimeError('Backup existant different; intervention requise')
    write_json_atomic(BACKUP_FILE, make_backup(config))
    PENDING_FILE.unlink(missing_ok=True)
    return True


def recover_if_needed():
    """Recuperation candidate; inactive cote PHP tant que 'recover' n'existe pas.

    La cle backup ne suffit PAS pour reautoriser un Hub revoque. NiuSmart doit
    verifier identifiant, site, cle et statut active avant de renvoyer le JSON.
    """
    with LOCK:
        current = load_active_config()
        if SHARED_FILE.exists() or validate_identity_for_sync(current) or PENDING_FILE.exists() or ROTATION_FILE.exists():
            return False
        if not BACKUP_FILE.exists():
            return False
        backup = validate_backup(load_json(BACKUP_FILE))
        result = api({
            'action': 'recover',
            'site_id':backup['site_id'],
            'hub_identifier':backup['hub_identifier'],
            'api_key':backup['api_key'],
        }, endpoint(backup, 'hub-activation.php'))
        recovered = result.get('config')
        validate_config(recovered)
        for key, value in backup.items():
            if recovered.get(key) != value:
                raise RuntimeError('Recuperation refusee: identite/cle/serveur differents')
        if recovered['hub_identifier'] != hub_identity():
            raise RuntimeError('Identite locale differente')
        if recovered['site_id'] != PILOT_SITE_ID:
            raise RuntimeError('Recuperation non autorisee sur ce site')
        # Une reponse complete et authentifiee seulement peut remplacer le fichier.
        save_active_config(recovered)
        return True


def sync_loop():
    while not STOP.wait(SYNC_INTERVAL):
        try:
            config = load_active_config()
            if not validate_identity_for_sync(config):
                recover_if_needed()
            else:
                synchronize()
        except urllib.error.HTTPError as exc:
            # Log utile mais sans cle API, token, ni reponse potentiellement sensible.
            print('Niu Hub sync: HTTP ' + str(exc.code), flush=True)
        except Exception as exc:
            print('Niu Hub sync: ' + type(exc).__name__ + ': operation ignoree', flush=True)


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
        if self.path in ('/', '/index.html'):
            raw = HTML.encode('utf-8')
            content_type = 'text/html; charset=utf-8'
        elif self.path == '/logo.png' and LOGO_FILE.is_file():
            raw = LOGO_FILE.read_bytes()
            content_type = 'image/png'
        elif self.path == '/icon.png' and ICON_FILE.is_file():
            raw = ICON_FILE.read_bytes()
            content_type = 'image/png'
        elif self.path == '/status':
            config = load_active_config()
            activated = validate_identity_for_sync(config) and not PENDING_FILE.exists()
            return self.send_json(200, {
                'activated': activated, 'pending': PENDING_FILE.exists(),
                'recovery_needed': (SHARED_FILE.exists() or CONFIG_FILE.exists() or BACKUP_FILE.exists()) and not activated and not PENDING_FILE.exists(),
            })
        else:
            return self.send_json(404, {'success':False,'message':'Page introuvable'})
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path != '/activate':
            return self.send_json(404, {'success':False,'message':'Page introuvable'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= 1024:
                return self.send_json(400, {'success':False,'message':'Requete invalide'})
            body = json.loads(self.rfile.read(length))
            code = body.get('code', '') if isinstance(body, dict) else ''
            if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
                return self.send_json(400, {'success':False,'message':'Code invalide. Format XXXX-XXXX-XXXX-XXXX (hexadecimal).'})
            with LOCK:
                if PENDING_FILE.exists():
                    try:
                        confirm_pending()
                        return self.send_json(200, {'success':True,'message':'Activation confirmee.'})
                    except Exception:
                        return self.send_json(503, {'success':False,'message':'Confirmation en attente; reessayez plus tard.'})
                # Refus aussi si fichier corrompu : ne jamais en autoriser l'ecrasement.
                if CONFIG_FILE.exists() or BACKUP_FILE.exists() or ROTATION_FILE.exists() or existing_identifier() is not None:
                    return self.send_json(409, {'success':False,'message':'Hub deja configure ou en recuperation; intervention manuelle requise.'})
                template = load_json(SHARED_FILE)
                required = {'hub', 'niusmart', 'home_assistant', 'netbird', 'localtuya', 'tuya_developer', 'ssh'}
                if (not isinstance(template, dict) or not required.issubset(template)
                        or not isinstance(template.get('hub'), dict) or not isinstance(template.get('niusmart'), dict)
                        or template['hub'].get('site_id') != PILOT_SITE_ID
                        or is_nonblank(template['niusmart'].get('api_key'))
                        or is_nonblank(template['hub'].get('hub_identifier'))):
                    return self.send_json(409, {'success':False,'message':'Modele HA absent, incomplet ou Hub deja active.'})
                result = api({'action':'activate','code':code})
                aid = result.get('activation_id')
                config = {
                    'site_id': result.get('site_id'),
                    'device_code': result.get('device_code'),
                    'api_key': result.get('api_key'),
                    'api_base_url': DEFAULT_BASE_URL,
                    'hub_identifier': result.get('hub_identifier'),
                    'site_config': result.get('site_config'),
                }
                if type(aid) is not int or aid < 1 or config['site_id'] != PILOT_SITE_ID:
                    raise RuntimeError('Activation non autorisee sur ce site pilote')
                validate_config(config)  # TOUS les champs AVANT ecriture.
                if existing_identifier() is not None:
                    raise RuntimeError('Identifiant deja attribue; activation interdite')
                save_active_config(config, activation=True)
                write_json_atomic(PENDING_FILE, {'activation_id':aid})
                try:
                    confirm_pending()
                except Exception:
                    return self.send_json(202, {'success':True,'message':'Configuration enregistree. Confirmation serveur en attente.'})
                return self.send_json(200, {'success':True,'message':'Activation reussie et confirmee.'})
        except urllib.error.HTTPError as exc:
            return self.send_json(403 if exc.code in (400,403) else 502, {
                'success':False,
                'message':'Code refuse ou expire.' if exc.code in (400,403) else 'Erreur du serveur NiuSmart.',
            })
        except (urllib.error.URLError, TimeoutError):
            return self.send_json(503, {'success':False,'message':'Serveur NiuSmart inaccessible. Reessayez.'})
        except Exception:
            return self.send_json(500, {'success':False,'message':'Activation impossible ou configuration incomplete. Aucun ecrasement autorise.'})


if __name__ == '__main__':
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if PENDING_FILE.exists():
        try:
            confirm_pending()
        except Exception:
            print('Niu Hub: confirmation initiale en attente', flush=True)
    if not validate_identity_for_sync(load_active_config()):
        try:
            recover_if_needed()
        except urllib.error.HTTPError as exc:
            print('Niu Hub recovery: serveur HTTP ' + str(exc.code), flush=True)
        except Exception:
            print('Niu Hub recovery: configuration non restauree', flush=True)
    Thread(target=sync_loop, daemon=True).start()
    print(f'Niu Hub activation listening on {BIND}:{PORT}', flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
