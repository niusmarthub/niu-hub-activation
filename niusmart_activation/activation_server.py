#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NiuSmart | Niu Hub Activation -- version 0.3.2.

Architecture : cinq modes separes. Ne JAMAIS transformer une synchronisation
ordinaire en activation, rotation de cle ou changement de serveur.

MODE 1 - ACTIVATION (operationnel, seulement pour le site pilote REM-0021)
    POST /activate -> NiuSmart action=activate, puis action=confirm.
    Exige TOUS les champs obligatoires de la configuration du site avant
    d'ecrire hub_config.json. Une activation ne remplace pas un hub configure.
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

import asyncio
import json
import traceback
from datetime import datetime, timezone
import os
import re
import secrets
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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
DASHBOARD_FILE = Path(__file__).with_name('hub_dashboard.html')
NETBIRD_PENDING_FILE = DATA_DIR / 'netbird_pending.json'
ADDON_OPTIONS = Path('/data/options.json')

SYNC_INTERVAL = max(60, int(os.environ.get('NIU_SYNC_INTERVAL', '3600')))
TUYA_READ_INTERVAL = 1
MQTT_STATE_REFRESH = 30
FEATURE_STATE_REFRESH = 5
SOLAR_WEB_INTERVAL = 30
TUYA_DISCOVERY_ENABLED = os.environ.get('NIU_TUYA_DISCOVERY', '1') == '1'
TUYA_SCAN_INTERVAL = max(120, int(os.environ.get('NIU_TUYA_SCAN_INTERVAL', '300')))
TUYA_INVENTORY_FILE = DATA_DIR / 'tuya_inventory.json'
TUYA_MANUAL_IP_FILE = DATA_DIR / 'tuya_manual_ips.json'
TUYA_SWITCHES_FILE = DATA_DIR / 'tuya_mqtt_switches.json'
TUYA_MODULE_ORDER_FILE = DATA_DIR / 'tuya_module_order.json'
TUYA_ACTIVE_ATTEMPT_FILE = DATA_DIR / 'tuya_active_attempt.json'
TUYA_ACTIVE_INTERVAL = 3600
DEFAULT_BASE_URL = 'https://niusmart.com'
ACTIVATION_URL = os.environ.get('NIU_ACTIVATION_URL', DEFAULT_BASE_URL + '/hub-activation.php')
KEY_UPDATE_URL = os.environ.get('NIU_KEY_UPDATE_URL', DEFAULT_BASE_URL + '/key-update.php')
BIND = os.environ.get('NIU_BIND', '0.0.0.0')
PORT = int(os.environ.get('NIU_PORT', '8099'))
# Restriction TEMPORAIRE : le PHP actuel autorise la synchronisation du site 2.
PILOT_SITE_ID = 2
STOP = Event()
LOCK = Lock()
DISCOVERY_LOCK = Lock()
DISCOVERY_REQUEST = Event()
SYNC_REQUEST = Event()
DISCOVERY_STATUS = 'En attente'
TUYA_TARGETS = None
MODULE_STATUS = {'scanned_at': None, 'modules': [], 'error': None, 'energy_mqtt': 'En attente de l’inventaire',
                 'tuya_mqtt': 'En attente de l’inventaire'}
SOLAR_STATUS = {}
SOLAR_WEB_SAMPLE = None
SOLAR_DIAGNOSTICS = {"installation": "pending", "registres": []}
NETBIRD_STATUS = 'Vérification en attente'
MQTT_BROKER_STATUS = 'pending'
CLIMATE_STATUS = {'installation': 'pending', 'mqtt_entities': None}
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

# Diagnostic v0.3.2 : pas de secrets dans les journaux. Les messages internes
# (ValueError/RuntimeError leves par ce module) sont des constantes du code.
# Les messages des bibliotheques et du reseau ne sont jamais imprimes.
def diagnostic(event, **fields):
    safe = {key: value for key, value in fields.items()
            if key in {'site_id', 'phase', 'result', 'http', 'changed_fields',
                       'status', 'error_type', 'file', 'function', 'line', 'message'}}
    record = {'utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'event': event, **safe}
    print('Niu Hub diagnostic: ' + json.dumps(record, ensure_ascii=False), flush=True)


def diagnostic_error(event, exc):
    frames = traceback.extract_tb(exc.__traceback__)
    last = frames[-1] if frames else None
    # Afficher uniquement les messages codes dans ce fichier; ne jamais
    # exposer les messages externes (URL, corps HTTP, tokens, chemins sensibles).
    local_error = bool(last and Path(last.filename).resolve() == Path(__file__).resolve())
    safe_message = str(exc) if local_error and type(exc) in (ValueError, RuntimeError) else 'message externe masque'
    diagnostic(event, error_type=type(exc).__name__,
               message=safe_message,
               file=Path(last.filename).name if last else 'inconnu',
               function=last.name if last else 'inconnue',
               line=last.lineno if last else 0,
               **({'http': exc.code} if isinstance(exc, urllib.error.HTTPError) else {}))


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


def save_active_config(config, activation=False, netbird_settings=None):
    """Sauvegarde tournante avant remplacement, restauration sur echec.

    L'archive manuelle n'est jamais lue ni modifiee par cette fonction.
    """
    validate_config(config, require_full_site=False)
    if activation and not SHARED_FILE.exists():
        shared = {
            'hub': {'site_id': config['site_id'], 'device_code': config['device_code']},
            'niusmart': {'server_url': config['api_base_url']},
            'netbird': {},
        }
    else:
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
        if netbird_settings:
            previous = shared.get('netbird')
            shared['netbird'] = {**(previous if isinstance(previous, dict) else {}), **netbird_settings}
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
        diagnostic('sync_request', site_id=config['site_id'], phase='update')
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
        diagnostic('sync_response', site_id=config['site_id'], status=response.get('status') if response.get('status') in ('ok', 'update') else 'unexpected',
                   changed_fields=sorted(changes) if isinstance(changes, dict) else 'invalides')
        updated, changed = apply_site_changes(config, changes)
        if changed:
            if updated['api_key'] != config['api_key'] or updated['api_base_url'] != config['api_base_url']:
                raise RuntimeError('Modification de cle ou serveur interdite')
            diagnostic('sync_apply', site_id=config['site_id'], phase='before_save')
            save_active_config(updated)
            config = updated
            diagnostic('sync_apply', site_id=config['site_id'], result='saved')
        else:
            diagnostic('sync_apply', site_id=config['site_id'], result='no_change')
        # Au premier update authentifie, initialiser le backup des anciens Hubs.
        ensure_backup_after_authenticated_update(config)
        if response.get('status') in ('ok', 'update'):
            try:
                write_json_atomic(DATA_DIR / 'last_niusmart_sync.json', {
                    'hub_identifier': config['hub_identifier'],
                    'site_id': config['site_id'],
                    'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                })
            except OSError as exc:
                diagnostic_error('sync_timestamp_error', exc)
        diagnostic('sync_complete', site_id=config['site_id'], result='ok')
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
    if TUYA_DISCOVERY_ENABLED:
        request_discovery()
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
    while not STOP.is_set():
        SYNC_REQUEST.clear()
        retry_activation = True
        try:
            config = load_active_config()
            if PENDING_FILE.exists():
                with LOCK:
                    if PENDING_FILE.exists():
                        confirm_pending()
            elif not validate_identity_for_sync(config):
                if recover_if_needed() and TUYA_DISCOVERY_ENABLED:
                    request_discovery()
            else:
                retry_activation = False
                synchronize()
        except Exception as exc:
            diagnostic_error('sync_error', exc)
        SYNC_REQUEST.wait(60 if retry_activation else SYNC_INTERVAL)


def fetch_tuya_inventory():
    config = load_active_config()
    if PENDING_FILE.exists() or not validate_identity_for_sync(config):
        return None

    url = validate_base_url(config['api_base_url']) + '/hub_tuya_api.php'
    request = urllib.request.Request(url, headers={'Accept': 'application/json'})
    request.add_unredirected_header('X-API-Key', config['api_key'])
    with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
        if response.geturl() != url:
            raise RuntimeError('Redirection du flux Tuya refusee')
        feed = json.load(response)
    if (not isinstance(feed, dict) or feed.get('site_id') != config['site_id']
            or feed.get('hub_id') != config['device_code']):
        raise RuntimeError('Identite du flux Tuya invalide')
    return feed


def update_tuya_inventory(feed, scan):
    from tuya_discovery import active_scan_targets, discover_missing
    from tuya_inventory import reconcile_modules

    discovered = scan['devices']
    previous = load_json(TUYA_INVENTORY_FILE)
    known = previous.get('modules') if isinstance(previous, dict) and previous.get('hub_id') == feed['hub_id'] else None
    manual_record = load_json(TUYA_MANUAL_IP_FILE) or {}
    manual = manual_record.get('ips', {}) if manual_record.get('hub_id') == feed['hub_id'] else {}
    modules = reconcile_modules(feed, discovered, known, manual)
    key_counts = Counter(row['local_key'] for row in feed['modules'] if is_nonblank(row.get('local_key')))
    missing = [
        {'id': row['tuya_device_id'], 'key': row['local_key'], 'ip': modules[row['tuya_device_id']]['ip']}
        for row in feed['modules']
        if not modules[row['tuya_device_id']]['discovered']
        and is_nonblank(row.get('local_key')) and key_counts[row['local_key']] == 1
    ]
    note = ''
    if missing:
        try:
            attempt = load_json(TUYA_ACTIVE_ATTEMPT_FILE)
            now = time.time()
            if isinstance(attempt, dict) and now - attempt.get('at', 0) < TUYA_ACTIVE_INTERVAL:
                note = 'Le balayage actif a déjà été tenté dans la dernière heure.'
            else:
                anchor_ips = [item['ip'] for item in modules.values() if item['discovered']]
                targets = active_scan_targets({entry['ip'] for entry in discovered.values()}, anchor_ips)
                # Prefer the router's actual DHCP leases over a full-subnet scan.
                # Do not probe the router itself or already discovered endpoints.
                try:
                    from hub_router_traffic import read_dhcp_leases
                    from ipaddress import IPv4Address, IPv4Network
                    router_ips = read_dhcp_leases()
                    network = IPv4Network('192.168.1.0/24')
                    seen_ips = {entry['ip'] for entry in discovered.values()}
                    dhcp_targets = [
                        f'{ip}/32' for ip in router_ips
                        if IPv4Address(ip) in network and ip not in seen_ips
                    ]
                    if dhcp_targets:
                        targets = dhcp_targets
                except Exception as exc:
                    diagnostic_error('tuya_dhcp_discovery_error', exc)
                if targets:
                    write_json_atomic(TUYA_ACTIVE_ATTEMPT_FILE, {'at': now})
                    discovered.update(discover_missing(missing, targets))
                    modules = reconcile_modules(feed, discovered, known, manual)
                else:
                    note = 'Aucune adresse locale admissible pour la découverte active.'
        except Exception as exc:
            diagnostic_error('tuya_active_discovery_error', exc)
            note = 'Le balayage actif a rencontré une erreur. Consultez le journal de l’application.'
    write_json_atomic(TUYA_INVENTORY_FILE, {
        'hub_id': feed['hub_id'], 'scanned_at': scan.get('scanned_at'), 'modules': modules,
    })
    detected = sum(item['discovered'] for item in modules.values())
    diagnostic('tuya_inventory', result='ok', status=f'{detected}/{len(modules)}')
    return modules, f'{detected} module(s) détecté(s) sur {len(modules)} attendu(s). {note}'.strip()


def update_module_status(feed, endpoints, scanned_at, executor, sent, listener, tracked_switches):
    from hub_modules import apply_relay_commands, publish_module_states, snapshot_modules

    global MODULE_STATUS
    config = load_active_config()
    activated = (validate_identity_for_sync(config) and not PENDING_FILE.exists()
                 and (feed['site_id'], feed['hub_id']) == (config['site_id'], config['device_code']))
    if not activated:
        return
    modules = snapshot_modules(feed, endpoints, lambda exc: diagnostic_error('tuya_dp_read_error', exc),
                               executor=executor)
    if TUYA_TARGETS is None or TUYA_TARGETS[0] is not feed:
        return
    if listener:
        apply_relay_commands(
            feed, endpoints, modules, listener.drain(), executor,
            lambda: TUYA_TARGETS is not None and TUYA_TARGETS[0] is feed and not PENDING_FILE.exists(),
            lambda exc: diagnostic_error('tuya_relay_command_error', exc),
        )
    if TUYA_TARGETS is None or TUYA_TARGETS[0] is not feed:
        return
    stored = load_json(TUYA_MODULE_ORDER_FILE) or {}
    order = stored.get('modules', []) if isinstance(stored, dict) and stored.get('hub_id') == feed['hub_id'] else []
    if not isinstance(order, list):
        order = []
    current = {module['id']: module for module in modules}
    ordered_ids = list(dict.fromkeys(device_id for device_id in order if isinstance(device_id, str) and device_id in current))
    ordered_ids.extend(device_id for device_id in current if device_id not in ordered_ids)
    if ordered_ids != order:
        write_json_atomic(TUYA_MODULE_ORDER_FILE, {'hub_id': feed['hub_id'], 'modules': ordered_ids})
    modules = [current[device_id] for device_id in ordered_ids]
    options = config.get('site_config') or {}
    monitoring = options.get('has_power_monitoring')
    energy_status = 'Option non reçue' if monitoring is None else 'Mesures MQTT désactivées'
    module_status = 'En attente d’états utilisables'

    def save_switches(switches):
        write_json_atomic(TUYA_SWITCHES_FILE, {'hub_id': feed['hub_id'], 'switches': sorted(switches)})

    try:
        counts = publish_module_states(
            feed['hub_id'], modules, expire_after=MQTT_STATE_REFRESH * 3,
            energy_enabled=monitoring == 1, sent=sent, refresh_after=MQTT_STATE_REFRESH,
            tracked_switches=tracked_switches, save_switches=save_switches,
        )
        if counts['other']:
            module_status = f"{counts['other']} état(s) publié(s)"
        if monitoring == 1:
            energy_status = (f"{counts['energy']} mesure(s) publiée(s)" if counts['energy']
                             else 'En attente de mesures utilisables')
    except Exception as exc:
        diagnostic_error('tuya_mqtt_error', exc)
        module_status = 'Publication MQTT indisponible'
        if monitoring == 1:
            energy_status = module_status
    if TUYA_TARGETS is not None and TUYA_TARGETS[0] is feed:
        MODULE_STATUS = {**MODULE_STATUS, 'modules': modules, 'scanned_at': scanned_at,
                         'read_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'error': None,
                         'energy_mqtt': energy_status, 'tuya_mqtt': module_status}


def scan_tuya():
    from tuya_discovery import discover_once
    from tuya_inventory import reconcile_modules

    global TUYA_TARGETS, MODULE_STATUS
    config = load_active_config()
    if not validate_identity_for_sync(config) or PENDING_FILE.exists():
        return None
    cache_file = DATA_DIR / 'tuya_feed.json'
    cached = load_json(cache_file)
    offline = False
    try:
        feed = fetch_tuya_inventory()
        if feed is None:
            return None
        reconcile_modules(feed, {})
        write_json_atomic(cache_file, {'identity': config['hub_identifier'], 'feed': feed})
    except (urllib.error.URLError, TimeoutError) as exc:
        if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
            if TUYA_TARGETS and TUYA_TARGETS[0].get('hub_id') == config['device_code']:
                TUYA_TARGETS = None
                MODULE_STATUS = {'scanned_at': None, 'modules': [], 'error': None,
                                 'energy_mqtt': 'Indisponible', 'tuya_mqtt': 'Indisponible'}
            cache_file.unlink(missing_ok=True)
            raise
        feed = cached.get('feed') if isinstance(cached, dict) and cached.get(
            'identity') == config['hub_identifier'] else None
        if not isinstance(feed, dict) or (feed.get('site_id'), feed.get('hub_id')) != (
                config['site_id'], config['device_code']):
            raise
        offline = True

    discover_once(str(DATA_DIR))
    scan = load_json(DATA_DIR / 'tuya_discovery.json')
    if not isinstance(scan, dict) or not isinstance(scan.get('devices'), dict):
        raise RuntimeError('Resultat de balayage Tuya invalide')
    modules, message = update_tuya_inventory(feed, scan)
    TUYA_TARGETS = (feed, modules, scan.get('scanned_at'))
    return ('NiuSmart inaccessible · inventaire local utilisé. ' if offline else '') + message


def request_discovery():
    global DISCOVERY_STATUS
    with DISCOVERY_LOCK:
        if DISCOVERY_REQUEST.is_set() or DISCOVERY_STATUS == 'Redécouverte en cours…':
            return False
        DISCOVERY_STATUS = 'Redécouverte en attente…'
        DISCOVERY_REQUEST.set()
        return True


def tuya_discovery_loop():
    global DISCOVERY_STATUS
    # An already activated hub requests its inventory on startup.
    request_discovery()
    while not STOP.is_set():
        if not DISCOVERY_REQUEST.wait(1):
            continue
        with DISCOVERY_LOCK:
            DISCOVERY_REQUEST.clear()
            DISCOVERY_STATUS = 'Redécouverte en cours…'
        try:
            message = scan_tuya() or 'En attente de l’activation'
        except Exception as exc:
            diagnostic_error('tuya_discovery_error', exc)
            message = 'Redécouverte indisponible · consultez le journal'
        with DISCOVERY_LOCK:
            DISCOVERY_STATUS = message


def tuya_state_loop():
    from hub_modules import DP_ID, TOPIC_ID
    from hub_mqtt import RelayCommandListener, mqtt_service

    sent = {}
    tracked_switches = set()
    hub_id = listener = None
    next_mqtt_attempt = 0
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            while not STOP.is_set():
                started = time.monotonic()
                targets = TUYA_TARGETS
                target_hub = targets[0]['hub_id'] if targets else None
                if target_hub != hub_id:
                    if listener:
                        listener.close()
                        listener = None
                    hub_id = target_hub
                    next_mqtt_attempt = 0
                    sent.clear()
                    saved = load_json(TUYA_SWITCHES_FILE) or {}
                    tracked_switches = {
                        (item[0], item[1]) for item in saved.get('switches', [])
                        if isinstance(item, list) and len(item) == 2
                        and all(isinstance(value, str) for value in item)
                        and TOPIC_ID.fullmatch(item[0]) and DP_ID.fullmatch(item[1])
                    } if isinstance(saved, dict) and saved.get('hub_id') == hub_id else set()
                if targets:
                    if listener is None and started >= next_mqtt_attempt and TOPIC_ID.fullmatch(hub_id):
                        next_mqtt_attempt = started + 30
                        try:
                            listener = RelayCommandListener(mqtt_service(), hub_id)
                        except Exception as exc:
                            diagnostic_error('tuya_mqtt_command_listener_error', exc)
                    try:
                        update_module_status(*targets, executor, sent, listener, tracked_switches)
                    except Exception as exc:
                        diagnostic_error('tuya_state_error', exc)
                STOP.wait(max(0.1, TUYA_READ_INTERVAL - (time.monotonic() - started)))
    finally:
        if listener:
            listener.close()


def mqtt_dependency_loop():
    from hub_mqtt import provision_mqtt

    global MQTT_BROKER_STATUS
    root = SHARED_FILE.parent.parent
    while not STOP.is_set():
        config = load_active_config()
        if validate_identity_for_sync(config) and not PENDING_FILE.exists():
            try:
                MQTT_BROKER_STATUS = provision_mqtt(root)
            except Exception as exc:
                MQTT_BROKER_STATUS = 'error'
                diagnostic_error('mqtt_dependency_error', exc)
        STOP.wait(60)


def send_solar_web_sample(config, sample):
    request = urllib.request.Request(
        validate_base_url(config['api_base_url']) + '/solar_data_api.php',
        data=json.dumps({**sample, 'site_id': config['site_id']}, allow_nan=False).encode('utf-8'),
        headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
        method='POST',
    )
    request.add_unredirected_header('X-API-Key', config['api_key'])
    with urllib.request.urlopen(request, timeout=8, context=ssl.create_default_context()) as response:
        reply = json.load(response)
    if (not isinstance(reply, dict) or reply.get('success') is not True
            or type(reply.get('site_id')) is not int or reply['site_id'] != config['site_id']):
        raise RuntimeError('Synchronisation solaire refusée')


def solar_web_sync_loop():
    sent = None
    interval = SOLAR_WEB_INTERVAL
    while not STOP.is_set():
        try:
            config = load_active_config()
            sample = SOLAR_WEB_SAMPLE
            if (sample and sample is not sent and validate_identity_for_sync(config)
                    and not PENDING_FILE.exists()
                    and sample[0] == (config['site_id'], config['hub_identifier'])
                    and (config.get('site_config') or {}).get('has_solar_panels') == 1
                    and 0 <= time.time() - sample[1]['sampled_at'] <= 60):
                send_solar_web_sample(config, sample[1])
                sent = sample
                interval = SOLAR_WEB_INTERVAL
        except Exception as exc:
            diagnostic_error('solar_web_sync_error', exc)
            interval = min(interval * 2, 300)
        STOP.wait(interval)


def entity_state_sync_loop():
    from hub_state_sync import RECONNECT_INTERVAL, sync_states

    sent = {}
    previous_identity = None
    interval = RECONNECT_INTERVAL
    while not STOP.is_set():
        try:
            config = load_active_config()
            if not validate_identity_for_sync(config) or PENDING_FILE.exists():
                sent.clear()
                previous_identity = None
                interval = RECONNECT_INTERVAL
            else:
                identity_fields = ('site_id', 'hub_identifier', 'device_code', 'api_base_url', 'api_key')
                identity = tuple(config[field] for field in identity_fields)
                if identity != previous_identity:
                    sent.clear()
                    previous_identity = identity

                def still_active():
                    if STOP.is_set() or PENDING_FILE.exists():
                        return False
                    current = load_active_config() or {}
                    return tuple(current.get(field) for field in identity_fields) == identity

                sync_states(config, validate_base_url(config['api_base_url']), sent, still_active)
                interval = RECONNECT_INTERVAL
        except Exception as exc:
            diagnostic_error('entity_state_sync_error', exc)
            interval = min(interval * 2, 30)
        STOP.wait(interval)


def can_watchdog_loop():
    from hub_can_watchdog import check_can1

    next_check = 0
    startup_recheck = True
    while not STOP.is_set():
        config = load_active_config()
        options = (config or {}).get('site_config') or {}
        climate_count = options.get('climate_count')
        if PENDING_FILE.exists() or type(climate_count) is not int or climate_count < 1:
            next_check = 0
            startup_recheck = True
        elif time.monotonic() >= next_check:
            try:
                result = check_can1()
            except Exception as exc:
                diagnostic_error('can1_watchdog_error', exc)
                next_check = time.monotonic() + 60
            else:
                diagnostic('can1_watchdog', result=result)
                # The older Niu CAN integration can cycle the link during startup.
                next_check = time.monotonic() + (30 if startup_recheck else 2 * 60 * 60)
                startup_recheck = False
        STOP.wait(FEATURE_STATE_REFRESH)



def tablet_loop():
    """Sync tablet presentation only; activation flow remains unchanged."""
    from hub_tablet import CARD_DIRECTORY, fetch_mappings, install_cards, publish_tablet, tablet_attributes

    root = SHARED_FILE.parent.parent
    while not STOP.is_set():
        try:
            config = load_active_config()
            enabled = (load_json(ADDON_OPTIONS) or {}).get('local_tablet') is True
            if not enabled or not validate_identity_for_sync(config) or PENDING_FILE.exists():
                if (root / CARD_DIRECTORY / 'niu-tablet.js').is_file():
                    publish_tablet('disabled')
            else:
                install_cards(root)
                mappings = fetch_mappings(config, validate_base_url(config['api_base_url']))
                current = load_active_config()
                if current == config and not PENDING_FILE.exists():
                    publish_tablet('ready', tablet_attributes(root, config, mappings))
                else:
                    publish_tablet('unavailable')
        except Exception as exc:
            diagnostic_error('tablet_sync_error', exc)
            try:
                publish_tablet('unavailable')
            except Exception as state_error:
                diagnostic_error('tablet_state_error', state_error)
        STOP.wait(60)


def homeassistant_features_loop():
    from hub_eco_diagnostic import diagnostic_rows, install_eco_diagnostics
    from hub_solar import (direct_diagnostic_rows, legacy_measures, read_solar,
                           recently_reported, web_solar_readings)
    from hub_homeassistant import (climate_readings, climate_sources, configure_climate, configure_inputs,
                                  core_api, install_climate, publish_climate, publish_solar, solar_payloads)

    global SOLAR_STATUS, SOLAR_DIAGNOSTICS, CLIMATE_STATUS, SOLAR_WEB_SAMPLE
    root = SHARED_FILE.parent.parent
    reload_flag = DATA_DIR / 'mqtt_inputs_reload.json'
    solar_file = DATA_DIR / 'solar_mqtt_topics.json'
    climate_file = DATA_DIR / 'climate_mqtt_topics.json'
    while not STOP.is_set():
        config = load_active_config()
        if validate_identity_for_sync(config) and not PENDING_FILE.exists():
            hub_id = config['device_code']
            options = config.get('site_config') or {}
            solar_enabled = options.get('has_solar_panels') == 1
            climate_count = options.get('climate_count')
            pilot = hub_id.upper() == 'REM-0021'
            if pilot:
                try:
                    configure_inputs(root, reload_flag, write_json_atomic)
                except Exception as exc:
                    diagnostic_error('mqtt_inputs_error', exc)

            climate_installation = 'disabled'
            if climate_count:
                try:
                    install_climate(root)
                    climate_installation = configure_climate(root)
                except Exception as exc:
                    climate_installation = 'error'
                    diagnostic_error('niu_can_install_error', exc)

            solar_installation = 'disabled'
            legacy_eco = root / 'custom_components' / 'eco_worthy'
            use_direct_solar = solar_enabled and not legacy_eco.exists() and not legacy_eco.is_symlink()
            if solar_enabled and pilot and not use_direct_solar:
                try:
                    solar_installation = install_eco_diagnostics(root)
                except Exception as exc:
                    solar_installation = 'error'
                    diagnostic_error('eco_worthy_diagnostic_install_error', exc)

            states = None
            if (solar_enabled and not use_direct_solar) or climate_count:
                try:
                    received = core_api("states")
                    if not isinstance(received, list):
                        raise ValueError("Réponse d’états Home Assistant invalide")
                    states = received
                except Exception as exc:
                    diagnostic_error('optional_module_states_error', exc)

            if climate_count is not None:
                active_climate = None
                try:
                    active_climate = publish_climate(root, hub_id, climate_count, climate_file,
                                                     load_json, write_json_atomic, states=states)
                except Exception as exc:
                    diagnostic_error('niu_can_mqtt_error', exc)
                zones = climate_readings(climate_count, [], {})
                if climate_count and states is not None:
                    try:
                        zones = climate_readings(climate_count, states, climate_sources(root))
                    except Exception as exc:
                        diagnostic_error('niu_can_dashboard_error', exc)
                CLIMATE_STATUS = {'installation': climate_installation, 'mqtt_entities': active_climate,
                                  'zones': zones}

            if 'has_solar_panels' in options:
                direct_measures = direct_load = None
                rows = diagnostic_rows(states or []) if solar_enabled else []
                if use_direct_solar:
                    solar_installation = 'direct_wait'
                    try:
                        addon = load_json(ADDON_OPTIONS) or {}
                        address = (addon.get('solar_bluetooth_address') or '').strip()
                        measures, load = asyncio.run(read_solar(address))
                        current = load_active_config()
                        if (not validate_identity_for_sync(current) or PENDING_FILE.exists()
                                or current['device_code'] != hub_id or legacy_eco.exists()):
                            continue
                        solar_enabled = (current.get('site_config') or {}).get('has_solar_panels') == 1
                        if solar_enabled:
                            rows = direct_diagnostic_rows(measures, load)
                            direct_measures, direct_load = measures, load
                            solar_installation = 'direct'
                    except Exception as exc:
                        diagnostic_error('eco_worthy_direct_read_error', exc)
                SOLAR_DIAGNOSTICS = {"installation": solar_installation, "registres": rows}
                SOLAR_WEB_SAMPLE = None
                if solar_enabled:
                    try:
                        sampled_at = int(time.time())
                        if use_direct_solar:
                            measures, load = direct_measures, direct_load
                        else:
                            fresh_states = list(recently_reported(states or [], sampled_at))
                            measures = legacy_measures(root, fresh_states)
                            load = legacy_measures(root, fresh_states, "l02")
                        readings = web_solar_readings(solar_payloads(hub_id, measures, load)[1])
                        if readings:
                            SOLAR_WEB_SAMPLE = ((config['site_id'], config['hub_identifier']),
                                                {'sampled_at': sampled_at, 'readings': readings})
                    except Exception as exc:
                        diagnostic_error('solar_web_sample_error', exc)
                try:
                    SOLAR_STATUS = publish_solar(root, hub_id, solar_enabled, solar_file,
                                                 load_json, write_json_atomic,
                                                 states=[] if use_direct_solar else states or [],
                                                 measures=direct_measures, load=direct_load)
                except Exception as exc:
                    SOLAR_STATUS = solar_payloads(hub_id, None)[1] if solar_enabled else {}
                    diagnostic_error('eco_worthy_mqtt_error', exc)
        STOP.wait(FEATURE_STATE_REFRESH)


def netbird_enrollment(source):
    if not isinstance(source, dict):
        return {}
    return {key: source[key].strip() for key in ('setup_key', 'management_url', 'admin_url')
            if is_nonblank(source.get(key))}


def netbird_loop():
    from hub_netbird import provision_netbird

    global NETBIRD_STATUS
    while not STOP.is_set():
        try:
            config = load_active_config()
            if validate_identity_for_sync(config) and not PENDING_FILE.exists():
                shared = load_json(SHARED_FILE) or {}
                legacy = shared.get('netbird')
                options = load_json(ADDON_OPTIONS) or {}
                settings = netbird_enrollment(legacy)
                if is_nonblank(options.get('netbird_setup_key')):
                    settings['setup_key'] = options['netbird_setup_key']
                if is_nonblank(options.get('netbird_repository')):
                    settings['repository'] = options['netbird_repository'].strip()
                elif isinstance(legacy, dict) and is_nonblank(legacy.get('repository')):
                    settings['repository'] = legacy['repository'].strip()

                def save_pending(slug):
                    if slug is None:
                        NETBIRD_PENDING_FILE.unlink(missing_ok=True)
                    else:
                        write_json_atomic(NETBIRD_PENDING_FILE, {'slug': slug})

                pending = load_json(NETBIRD_PENDING_FILE) or {}
                NETBIRD_STATUS = provision_netbird(
                    settings, config['device_code'], pending.get('slug'), save_pending,
                )
        except Exception as exc:
            NETBIRD_STATUS = 'Vérification ou installation indisponible'
            diagnostic_error('netbird_error', exc)
        if STOP.wait(TUYA_SCAN_INTERVAL):
            break


def is_activation_template(shared):
    if not isinstance(shared, dict):
        return False
    hub, niusmart = shared.get('hub'), shared.get('niusmart')
    return (
        isinstance(hub, dict) and isinstance(niusmart, dict)
        and hub.get('site_id') == PILOT_SITE_ID and is_nonblank(hub.get('device_code'))
        and niusmart.get('server_url') == DEFAULT_BASE_URL
        and not is_nonblank(niusmart.get('api_key'))
        and not is_nonblank(hub.get('hub_identifier'))
    )


def dashboard_status():
    config = load_active_config()
    pending = PENDING_FILE.exists()
    activated = validate_identity_for_sync(config) and not pending
    status = {
        'activated': activated,
        'pending': pending,
        'recovery_needed': not activated and not pending and (
            (SHARED_FILE.exists() and not is_activation_template(load_json(SHARED_FILE)))
            or CONFIG_FILE.exists() or BACKUP_FILE.exists() or ROTATION_FILE.exists()
            or IDENTIFIER_FILE.exists()
        ),
    }
    if activated:
        site = config.get('site_config') or {}
        sync = load_json(DATA_DIR / 'last_niusmart_sync.json')
        last_sync = None
        if isinstance(sync, dict) and (sync.get('hub_identifier'), sync.get('site_id')) == (
            config['hub_identifier'], config['site_id']
        ):
            last_sync = sync.get('at')
        status.update({
            'hub': config['device_code'],
            'last_niusmart_sync': last_sync,
            'site_timezone': site.get('timezone'),
            'options': {key: site[key] for key in (
                SITE_FLAG_FIELDS | {'toilet_count', 'door_count', 'climate_count'}
            ) if key in site},
            'netbird': NETBIRD_STATUS,
            'internet_traffic': __import__('hub_router_traffic').current_status(),
            'solar': SOLAR_STATUS,
            'solar_diagnostics': SOLAR_DIAGNOSTICS,
            'climate': CLIMATE_STATUS,
            'mqtt_broker': MQTT_BROKER_STATUS,
            'discovery': DISCOVERY_STATUS,
            'discovery_enabled': TUYA_DISCOVERY_ENABLED,
            **MODULE_STATUS,
        })
    return status


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
            raw = DASHBOARD_FILE.read_bytes()
            content_type = 'text/html; charset=utf-8'
        elif self.path == '/logo.png' and LOGO_FILE.is_file():
            raw = LOGO_FILE.read_bytes()
            content_type = 'image/png'
        elif self.path == '/icon.png' and ICON_FILE.is_file():
            raw = ICON_FILE.read_bytes()
            content_type = 'image/png'
        elif self.path == '/status':
            return self.send_json(200, dashboard_status())
        elif self.path == '/tuya/dhcp-leases':
            if not validate_identity_for_sync(load_active_config()) or PENDING_FILE.exists():
                return self.send_json(409, {'success': False, 'message': 'Hub non activé.'})
            try:
                from hub_router_traffic import read_dhcp_leases
                return self.send_json(200, {'success': True, 'ips': read_dhcp_leases()})
            except Exception as exc:
                diagnostic_error('tuya_dhcp_list_error', exc)
                return self.send_json(503, {'success': False, 'message': 'Lecture DHCP indisponible.'})
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
        if self.path == '/tuya/manual-ip':
            if self.headers.get('X-Niu-Hub-Request') != 'manual-ip':
                return self.send_json(403, {'success': False, 'message': 'Requête locale invalide.'})
            config = load_active_config()
            if not validate_identity_for_sync(config) or PENDING_FILE.exists():
                return self.send_json(409, {'success': False, 'message': 'Hub non activé.'})
            try:
                from ipaddress import IPv4Address
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 512:
                    raise ValueError('Taille invalide')
                body = json.loads(self.rfile.read(length))
                device_id, ip = body['device_id'], body['ip'].strip()
                feed = fetch_tuya_inventory()
                if not any(m.get('tuya_device_id') == device_id for m in feed['modules']):
                    raise ValueError('Module non autorisé')
                if ip:
                    address = IPv4Address(ip)
                    if not address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
                        raise ValueError('Adresse IP locale invalide')
                    ip = str(address)
                    from tuya_discovery import verify_manual_ip
                    verdict = verify_manual_ip(device_id, ip, feed['modules'])
                    if verdict == 'incorrect':
                        return self.send_json(409, {'success': False, 'result': 'incorrect', 'message': 'Module incorrect : identité différente.'})
                    if verdict != 'confirmed':
                        return self.send_json(409, {'success': False, 'result': 'no_response', 'message': 'Module ne répond pas ou identité non vérifiable.'})
                with LOCK:
                    stored = load_json(TUYA_MANUAL_IP_FILE) or {}
                    ips = stored.get('ips', {}) if stored.get('hub_id') == feed['hub_id'] else {}
                    ips = dict(ips)
                    if ip:
                        ips[device_id] = ip
                    else:
                        ips.pop(device_id, None)
                    write_json_atomic(TUYA_MANUAL_IP_FILE, {'hub_id': feed['hub_id'], 'ips': ips})
                request_discovery()
                return self.send_json(200, {'success': True, 'message': 'Adresse de secours enregistrée. Redécouverte demandée.'})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                return self.send_json(400, {'success': False, 'message': str(exc)})
            except Exception as exc:
                diagnostic_error('tuya_manual_ip_error', exc)
                return self.send_json(503, {'success': False, 'message': 'Impossible de sauvegarder cette adresse.'})
        if self.path == '/rediscover':
            if self.headers.get('X-Niu-Hub-Request') != 'rediscover':
                return self.send_json(403, {'success': False, 'message': 'Requête locale invalide.'})
            if not TUYA_DISCOVERY_ENABLED or not dashboard_status()['activated']:
                return self.send_json(409, {'success': False, 'message': 'Redécouverte indisponible.'})
            if request_discovery():
                SYNC_REQUEST.set()
            return self.send_json(202, {'success': True, 'message': DISCOVERY_STATUS})
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
                if SHARED_FILE.exists() and not is_activation_template(load_json(SHARED_FILE)):
                    return self.send_json(409, {'success':False,'message':'Configuration HA incomplète ou hub déjà activé.'})
                config_dir = SHARED_FILE.parent if SHARED_FILE.parent.is_dir() else SHARED_FILE.parent.parent
                if not config_dir.is_dir() or not os.access(config_dir, os.W_OK):
                    return self.send_json(503, {'success':False,'message':'Dossier de configuration Home Assistant inaccessible.'})
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
                save_active_config(config, activation=True, netbird_settings=netbird_enrollment(result.get('netbird')))
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
            diagnostic('confirmation_pending', result='retry_later')
    if not validate_identity_for_sync(load_active_config()):
        try:
            recover_if_needed()
        except urllib.error.HTTPError as exc:
            diagnostic_error('recovery_error', exc)
        except Exception as exc:
            diagnostic_error('recovery_error', exc)
    Thread(target=sync_loop, daemon=True).start()
    Thread(target=tablet_loop, daemon=True).start()
    Thread(target=netbird_loop, daemon=True).start()
    from hub_router_traffic import polling_loop as router_traffic_loop
    Thread(target=router_traffic_loop, args=(STOP,), daemon=True).start()
    Thread(target=homeassistant_features_loop, daemon=True).start()
    Thread(target=can_watchdog_loop, daemon=True).start()
    Thread(target=solar_web_sync_loop, daemon=True).start()
    Thread(target=entity_state_sync_loop, daemon=True).start()
    Thread(target=mqtt_dependency_loop, daemon=True).start()
    if TUYA_DISCOVERY_ENABLED:
        Thread(target=tuya_discovery_loop, daemon=True).start()
        Thread(target=tuya_state_loop, daemon=True).start()
    print(f'Niu Hub activation listening on {BIND}:{PORT}', flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
