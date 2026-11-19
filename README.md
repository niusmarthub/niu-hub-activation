# Niu Hub Activation — pilote REM-0021

Ce paquet contient l'add-on Home Assistant, `hub-activation.php`, `key-update.php` et des tests Python.

## Modifications
- Activation : le Hub envoie **uniquement le code d'activation**. NiuSmart genere/attribue `hub_identifier`, retrouve le site, et renvoie la cle API, l'identite et les options. Le PHP rejette les codes expires, inconnus et deja `redeemed`.
- Synchronisation : lit `/homeassistant/niusmart/hub_config.json` et met à jour les options dans `site_config`. Les anciens fichiers restent lisibles.
- Archivage : avant remplacement, copie binaire verifiee dans `/homeassistant/niusmart/archive/hub_hub_config.json` (une seule archive tournante). **L'archive manuelle n'est jamais modifiee.**
- Rotation : `key-update.php` conserve les etapes check/commit/ack. La rotation n'est pas une synchronisation ordinaire.
- Le site 1 est refusé pendant le pilote.
- Une fois le hub activé, le Supervisor détecte et démarre NetBird. Pour une installation neuve, Niu Hub utilise la clé fournie à l’activation, celle de la section `netbird` ou l’option `netbird_setup_key` de l’add-on. L’option `netbird_repository` permet de choisir le dépôt. Le démarrage du client et sa connexion au réseau NetBird sont deux états distincts.
- Le tableau de bord affiche les options NiuSmart et les datapoints reçus ou lus localement. Le suivi de l’énergie publie les mesures de tous les modules dont la description indique un statut électrique, une unité et une échelle. Le profil PJ1103B fournit ces informations lorsqu’elles manquent pour ce produit. NiuSmart associe ensuite les statuts aux fonctions du site.

## 0.3.21 — mesures solaires validées sur REM-0021

- La carte locale affiche les mesures validées, avec leur unité.
- La ligne 9 de L01 fournit les puissances PV et LOAD affichées en watts. La ligne 6 donne la tension LOAD. L’état LOAD provient de la ligne 30 de L02.
- La température de la sonde est publiée comme capteur MQTT.

## 0.3.18 — tableaux solaire et climatisation REM-0021

- La carte solaire affiche l’état LOAD, la température de la sonde et trois mesures dans chacun des groupes Panneau solaire, Batterie et Sortie LOAD.
- Les lectures probables sont signalées sur la carte et en diagnostics MQTT. La production candidate CHG reste une valeur brute. La puissance LOAD confirmée reste disponible en MQTT.
- La carte climatisation affiche une section pour chaque zone Dometic activée. L’état du hub affiche la dernière synchronisation NiuSmart réussie dans le fuseau horaire du site.
- Les lectures locales solaire et climatisation reprennent cinq secondes après la fin du cycle précédent. L’intervalle de synchronisation NiuSmart reste configurable.
- Le test sur le matériel du REM-0021 reste à effectuer.

## 0.3.17 — carte solaire REM-0021

- La carte ECO-WORTHY affiche dix lectures. Tension PV, tension batterie, courant LOAD et puissance LOAD proviennent de L01. Les six autres lectures affichent « À valider ».
- La découverte MQTT publie ces quatre mesures et la température de la sonde comme diagnostic. Le paquet s’installe comme mise à jour de Niu Hub Activation dans son dépôt existant.
- Le test sur le matériel du REM-0021 reste à effectuer.

## 0.3.16 — options et diagnostic REM-0021

- Installez ou mettez à jour Niu Hub Activation. L’application réutilise le service MQTT fourni par le Supervisor et peut demander l’installation de Mosquitto si aucun service ni aucune configuration MQTT existante n’est détecté.
- L’option solaire NiuSmart active le lecteur Bluetooth intégré lorsqu’aucune intégration ECO-WORTHY n’est installée. Sur REM-0021, lorsqu’une intégration est déjà en place, Niu Hub reprend ses mesures et sauvegarde automatiquement le fichier d’origine reconnu avant d’ajouter le diagnostic. Cette modification prend effet au prochain redémarrage de Home Assistant. Un fichier personnalisé différent reste en place.
- Sous **Solaire ECO-WORTHY → Registres Modbus**, le tableau reçoit les 30 valeurs brutes de la dernière lecture valide. Les numéros 01–15 correspondent à L01 et 16–30 à L02. Il n’est pas nécessaire de créer une carte Home Assistant.
- Le nombre de climatiseurs NiuSmart prépare Niu CAN et définit les zones exportées en MQTT. Niu Hub tente de créer l’entrée Niu CAN dès que Home Assistant la reconnaît. Les commandes Dometic passent par les thermostats Niu CAN de Home Assistant.
- La désactivation retire les annonces MQTT créées par Niu Hub pour l’option. Une réduction de deux climatiseurs à un retire R2. La réactivation republie les entités correspondantes. Les états de ces sections sont publiés sans rétention.
- Ce paquet est prévu pour le pilote REM-0021. Le provisionnement du Supervisor, le redémarrage et les lectures matérielles restent à confirmer sur le hub.

## 0.3.14 — pilote solaire ECO-WORTHY

- L’option de site **Panneaux solaires** lance la lecture Bluetooth sur un Hub activé. L’application détecte un adaptateur MPPT-BT02. Si plusieurs sont présents, renseigner son adresse dans l’option `solar_bluetooth_address` de l’application.
- Le broker MQTT du Supervisor reçoit quatre capteurs en lecture seule, avec découverte Home Assistant. La tension de sortie LOAD est lue au registre `0x0005`.
- Sur un Hub équipé de l’ancienne intégration `custom_components/eco_worthy`, le lecteur de l’application reste en pause pour éviter deux connexions concurrentes. Planifier le retrait de l’ancienne intégration et de son entrée YAML lors de la migration.
- Le pilote requiert un adaptateur Bluetooth local et le service MQTT du Supervisor. Valider la détection et les quatre capteurs sur le Hub pilote avant le déploiement.

## 0.3.13 — validation sur REM-0021

- Au démarrage ou avec **Redécouvrir les modules**, le hub récupère l’inventaire NiuSmart et cherche les modules autorisés sur le réseau local. Les options NiuSmart sont aussi vérifiées une fois par heure par défaut.
- Le hub vise une lecture locale par seconde et publie les changements dans MQTT. Après une première synchronisation réussie, il peut utiliser l’inventaire enregistré pendant une coupure Internet.
- Sur le PJ1103B, Home Assistant reçoit une précision d’une décimale pour les puissances en watts. Le sens du courant indique « Inconnu » si la lecture du compteur est disponible sans la valeur de direction. L’ancienne entité de test du DP 101 est retirée du broker.
- Les états Tuya sont publiés en lecture seule. La commande des relais depuis NiuSmart et la migration des scripts Dometic CAN restent à valider.

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
