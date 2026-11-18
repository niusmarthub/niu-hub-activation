# Niu Hub Activation — pilote REM-0021

Ce paquet contient l'add-on Home Assistant, `hub-activation.php`, `key-update.php` et des tests Python.

## Modifications
- Activation : le Hub envoie **uniquement le code d'activation**. NiuSmart genere/attribue `hub_identifier`, retrouve le site, et renvoie la cle API, l'identite et les options. Le PHP rejette les codes expires, inconnus et deja `redeemed`.
- Synchronisation : lit `/homeassistant/niusmart/hub_config.json` et met à jour les options dans `site_config`. Les anciens fichiers restent lisibles.
- Archivage : avant remplacement, copie binaire verifiee dans `/homeassistant/niusmart/archive/hub_hub_config.json` (une seule archive tournante). **L'archive manuelle n'est jamais modifiee.**
- Rotation : `key-update.php` conserve les etapes check/commit/ack. La rotation n'est pas une synchronisation ordinaire.
- Le site 1 est refusé pendant le pilote.
- Une fois le hub activé, le Supervisor détecte et démarre NetBird. Pour une installation neuve, Niu Hub utilise la clé fournie à l’activation, celle de la section `netbird` ou l’option `netbird_setup_key` de l’add-on. L’option `netbird_repository` permet de choisir le dépôt. Le démarrage du client et sa connexion au réseau NetBird sont deux états distincts.
- Le tableau de bord est accessible depuis **Ouvrir l’interface Web** dans Home Assistant. Il affiche les options NiuSmart et les datapoints reçus ou lus localement. L’option de suivi de l’énergie active la publication MQTT des mesures d’un PJ1103B reconnu par son identifiant produit.

## IMPORTANT avant installation
1. Conserver la sauvegarde manuelle originale `/config/niusmart/archive/hub_config_avant_0.3.1_20261013.json` hors de portee de l'add-on.
2. Sur un Hub deja active, ne **pas** relancer l'activation : utiliser la synchronisation avec l'identite deja enregistree.
3. Sur un hub neuf, saisir le code d’activation. Si le fichier est absent, l’add-on le crée à partir de la réponse validée du serveur. Un ancien modèle compatible peut aussi être utilisé.
4. Verifier que le montage HA `/homeassistant` est reellement disponible en lecture/ecriture pour l'add-on; si absent, NE PAS installer pour ecriture.
5. Deployer `hub-activation.php` et l'add-on comme un ensemble coherent, seulement apres sauvegarde et verification de la version PHP. Ne pas deployer `key-update.php` si la version deja installee est identique.
6. Les tests fournis sont locaux et simules. Ils ne valident pas la base MySQL reelle, le reseau, le montage HA OS ou les integrations tierces.

## Limites connues
- Changement de serveur : non implemente, doit rester interdit.
- Recuperation automatique : non operationnelle sans endpoint PHP `recover`.
- L’activation reste limitée au site pilote. Les datapoints candidats au contrôle de relais sont signalés pour validation.
- Un Hub ancien sans identifiant `hub.hub_identifier` utilise son ancien identifiant persistant dans `/data/hub_identifier`.
- Pas de modification des secrets des integrations tierces par la synchronisation.

## Tests hors Hub
`python -m unittest discover -s . -p 'test_*.py' -v`
`php -l hub-activation.php`
`php -l key-update.php`
