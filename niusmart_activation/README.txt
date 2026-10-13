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
