# Niu Hub Activation 0.3.0 — préparation pour REM-0021

**Statut : code préparé et testé localement, NON installé sur Home Assistant.**

## Fichiers du dépôt GitHub

Mettre dans `niusmart_activation/` :
- `activation_server.py` — service d'activation, commentaires détaillés des cinq modes
- `Dockerfile` — copie aussi les deux images dans le conteneur
- `config.yaml` — version 0.3.0
- `logo.png` — logo NiuSmart dans la page Web sur le port 8099
- `icon.png` — icône de l'add-on Home Assistant + favicon de la page

Le dossier `tests/` reste optionnel dans le dépôt; il sert aux essais hors ligne.

## Les cinq modes et leur état

| Mode | 0.3.0 | Ecritures autorisées |
| --- | --- | --- |
| Activation | Opérationnel si le serveur renvoie une config complète | `hub_config.json`, état de confirmation, puis clé de secours après confirmation |
| Mise à jour | Opérationnel | Exclusivement les champs de `site_config` |
| Update API | Rotation `check`/`commit`/`ack` conservée; nécessite `key-update.php` compatible | Clé dans `hub_config.json` après commit; clé de secours après ACK |
| Changement de serveur | Prévu/documenté, **pas encore activé** | Aucune — validation interserveurs à développer |
| Récupération | Client prêt mais **serveur incomplet** | Réécriture de `hub_config.json` uniquement après réponse `action=recover` complète/authentifiée |

### Protection des clés

- La sauvegarde se nomme `api_key_backup.json` dans le **/data persistant** de l'add-on (permissions 0600).
- Elle est créée après confirmation d'une activation. Sur un Hub ancien, elle est initialisée seulement après une réponse `update` authentifiée.
- Elle **n'est jamais modifiée par une simple mise à jour**. En rotation, elle est remplacée uniquement après `ack` final.
- La configuration active est écrite de manière atomique. Une activation ne remplace jamais un fichier existant, même corrompu.
- Aucune clé n'est affichée dans `/status` ni dans les journaux du service.
- `secrets.yaml` et les autres fonctions Home Assistant ne sont ni lus ni modifiés.

### Limites actuelles importantes

1. Le PHP actuel `hub-activation_REM0021_update.php` ne supporte que les actions `activate`, `confirm`, `update` et la mise à jour du **site 2**. Le client Python reste volontairement limité au **site 2** jusqu'à adaptation du PHP. Les sites existants ne sont pas concernés par cette livraison.
2. Le **mode récupération automatique** exige un nouvel endpoint serveur `action=recover` dans `hub-activation.php` avec authentification du Hub, vérification de la clé non révoquée, et retour de `{'success':true,'config':{...}}` complet. Tant que l'endpoint n'est pas disponible, une perte de `hub_config.json` ne sera **pas** réparée automatiquement; une restauration manuelle reste nécessaire.
3. Le **changement de serveur** n'est pas implémenté. Il faudra le protocole d'acceptation authentifiée du serveur destinataire et l'interface root dans `dashboard.php`.
4. La rotation de clé exige le serveur `key-update.php` correspondant à son protocole actuel; aucun changement serveur n'est fourni ici.
5. Il faut que le serveur renvoie tous les champs de `site_config` pour une nouvelle activation. La liste se trouve dans le commentaire de `SITE_FIELDS`, conforme au PHP REM-0021 fourni.
6. **Aucun déploiement et aucune rotation de clé n'ont été effectués.** L'installation doit être testée uniquement sur REM-0021, après sauvegarde des fichiers locaux /data.

## Essais déjà exécutés

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile niusmart_activation/activation_server.py
```

Les tests isolés simulent les réponses PHP et vérifient les protections; **ils ne prouvent pas que le serveur de production actuel répond correctement**. Effectuer ensuite un test bout en bout sur REM-0021 uniquement, sans refaire une activation.

## Mise à jour du dépôt

Copier les cinq fichiers sous `niusmart_activation/` dans le dépôt `niusmarthub/niu-hub-activation` branche `main`, puis créer un commit. Après vérification du dépôt miroir et **après autorisation explicite**, l'add-on peut être mis à jour sur REM-0021. Ne jamais utiliser cette archive comme sauvegarde des clés API : la sauvegarde est propre à chaque Hub.
