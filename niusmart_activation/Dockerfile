FROM python:3.12-alpine
RUN apk add --no-cache python3 ca-certificates \
    && pip install --no-cache-dir tinytuya==1.21.1 paho-mqtt==2.1.0
COPY activation_server.py /usr/local/bin/activation_server.py
COPY tuya_discovery.py /usr/local/bin/tuya_discovery.py
COPY tuya_inventory.py /usr/local/bin/tuya_inventory.py
COPY energy_mqtt_test.py /usr/local/bin/energy_mqtt_test.py
COPY logo.png /usr/local/bin/logo.png
COPY icon.png /usr/local/bin/icon.png
CMD ["python3", "/usr/local/bin/activation_server.py"]
