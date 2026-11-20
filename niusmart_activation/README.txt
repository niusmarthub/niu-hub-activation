Niu Hub Activation - module complementaire Home Assistant OS (version de test)

Contenu: niu_activation/config.yaml, Dockerfile, activation_server.py

IMPORTANT : ne pas installer sur une unite en production.
Ce module est concu pour le Hub de test REM-0021.

1. Decompresser l'archive sur votre PC.
2. Copier le dossier niu_activation entier vers /addons/niu_activation sur Home Assistant OS,
   par exemple au moyen de Samba/SSH si cet acces est deja disponible.
3. Home Assistant > Parametres > Modules complementaires > Boutique > menu > Recharger.
4. Installer "Niu Hub Activation", puis demarrer le module.
5. Ouvrir http://ADRESSE_LOCALE_DU_HUB:8099 depuis le meme reseau local.
6. Entrer le code d'activation genere dans NiuSmart.

La configuration est sauvegardee dans le stockage prive persistant de l'add-on:
/data/hub_config.json (a l'interieur du conteneur de l'add-on).
Elle n'est pas automatiquement partagee avec les autres integrations HA.

PREALABLE: le fichier https://niusmart.com/hub-activation.php doit etre installe
sur le serveur et correspondre au format JSON de activation_server.py.

LIMITES DE CETTE PREMIERE VERSION:
- Ne remplace PAS encore le tableau de bord HA ni la page Fully Kiosk.
- Ne configure PAS encore les autres integrations Home Assistant.
- L'interface HTTP locale sur le port 8099 n'a pas d'authentification; n'exposer
  ce port ni a Internet ni a un reseau non fiable. Restreindre l'acces au LAN de test.
- La cle API du site existante est envoyee au Hub apres validation du code.
- Ne pas utiliser un code d'activation d'un site de production pour ce test.

0.3.25 — surveillance de can1
- Avec la climatisation active, contrôle can1 au démarrage puis toutes les 2 h.
- Réactive un lien arrêté à 250 kbit/s et active la récupération automatique
  après bus-off à 100 ms. Un lien déjà actif garde ses paramètres.
- Vérifie à nouveau 30 s après le premier succès pour couvrir le démarrage de
  l’intégration Niu CAN. Réessaie après 60 s si le contrôle échoue.

0.3.15 — essai REM-0021
- Ajoute l'appareil MQTT commun aux 32 entrées NDIH-432 existantes, puis recharge
  le YAML MQTT. La sauvegarde est configuration.yaml.niu-before-ndih432.
- Les interrupteurs Tuya sont numérotés en continu dans l'ordre initial du flux
  NiuSmart, conservé au redémarrage. Les nouveaux modules s'ajoutent à la fin.
  Le retrait d'un module peut décaler les numéros suivants. Les identifiants,
  les sujets de commande et les relais physiques associés restent stables.
- Pour une option climatisation active, le paquet inclut Niu CAN et copie
  l'intégration si elle manque. Une nouvelle installation nécessite le
  redémarrage de Home Assistant et l'ajout de Niu CAN dans Intégrations.
- Pour une option solaire active, l'intégration Bluetooth ECO-WORTHY déjà
  présente dans Home Assistant fournit les mesures. Les états qu'elle expose
  sont relayés vers MQTT en lecture seule. La vue solaire regroupe les champs
  reconnus en Panneau solaire, Batterie et Sortie LOAD. Les champs non exposés,
  non identifiés ou ambigus apparaissent comme non disponibles.

0.3.16 — mise à jour REM-0021
- Les options NiuSmart pilotent les annonces MQTT solaires et Niu CAN. La
  désactivation retire les annonces du hub pour cette option.
- Le tableau Modbus à 30 lignes est intégré à Niu Hub Activation.
- Le lecteur Bluetooth intégré est utilisé en l’absence d’une intégration
  ECO-WORTHY existante. Sur REM-0021, la version connue de l’intégration est
  sauvegardée et enrichie avec le diagnostic au prochain redémarrage Core.
