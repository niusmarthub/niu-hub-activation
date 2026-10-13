# Niu Hub Activation 0.3.1 - livraison de validation REM-0021

Ce paquet contient l'add-on Home Assistant, `hub-activation.php`, `key-update.php` et des tests Python.

## Modifications
- Activation : le Hub envoie **uniquement le code d'activation**. NiuSmart genere/attribue `hub_identifier`, retrouve le site, et renvoie la cle API, l'identite et les options. Le PHP rejette les codes expires, inconnus et deja `redeemed`.
- Synchronisation : lit `/homeassistant/niusmart/hub_config.json` (monte par l'add-on), ajoute `site_config` et conserve les sept sections existantes. Un `site_config` objet vide est accepte cote PHP.
- Archivage : avant remplacement, copie binaire verifiee dans `/homeassistant/niusmart/archive/hub_hub_config.json` (une seule archive tournante). **L'archive manuelle n'est jamais modifiee.**
- Rotation : `key-update.php` conserve les etapes check/commit/ack. La rotation n'est pas une synchronisation ordinaire.
- Le site 1 est refuse par les deux programmes pendant le pretest. Aucun code d'installation n'est execute sur le Hub par la livraison.

## IMPORTANT avant installation
1. Conserver la sauvegarde manuelle originale `/config/niusmart/archive/hub_config_avant_0.3.1_20261013.json` hors de portee de l'add-on.
2. Sur un Hub deja active, ne **pas** relancer l'activation : utiliser la synchronisation avec l'identite deja enregistree.
3. Sur un Hub neuf, preparer un `hub_config.json` modele avec les sept sections existantes, `hub.site_id=2`, `hub.device_code` identique a la valeur renvoyee par NiuSmart, et `niusmart.api_key` vide. L'activation refusera un fichier deja configure ou un modele absent. Ce garde-fou empeche de creer un JSON incomplet.
4. Verifier que le montage HA `/homeassistant` est reellement disponible en lecture/ecriture pour l'add-on; si absent, NE PAS installer pour ecriture.
5. Deployer `hub-activation.php` et l'add-on comme un ensemble coherent, seulement apres sauvegarde et verification de la version PHP. Ne pas deployer `key-update.php` si la version deja installee est identique.
6. Les tests fournis sont locaux et simules. Ils ne valident pas la base MySQL reelle, le reseau, le montage HA OS ou les integrations tierces.

## Limites connues
- Changement de serveur : non implemente, doit rester interdit.
- Recuperation automatique : non operationnelle sans endpoint PHP `recover`.
- L'activation d'un Hub neuf exige un modele complet de configuration Home Assistant. L'activation d'un Hub deja configure est bloquee.
- Un Hub ancien sans identifiant `hub.hub_identifier` utilise son ancien identifiant persistant dans `/data/hub_identifier`.
- Pas de modification des secrets des integrations tierces par la synchronisation.

## Tests hors Hub
`python -m unittest discover -s . -p 'test_*.py' -v`
`php -l hub-activation.php`
`php -l key-update.php`
