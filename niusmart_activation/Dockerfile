FROM python:3.12-alpine
RUN apk add --no-cache python3 ca-certificates iproute2 \
    && pip install --no-cache-dir tinytuya==1.21.1 paho-mqtt==2.1.0 PyYAML==6.0.2 bleak==1.1.1
COPY activation_server.py /usr/local/bin/activation_server.py
COPY tuya_discovery.py /usr/local/bin/tuya_discovery.py
COPY tuya_inventory.py /usr/local/bin/tuya_inventory.py
COPY hub_netbird.py hub_modules.py hub_mqtt.py hub_solar.py hub_dashboard.html /usr/local/bin/
COPY hub_homeassistant.py hub_eco_diagnostic.py hub_state_sync.py hub_can_watchdog.py hub_router_traffic.py /usr/local/bin/
COPY eco_worthy_sensor.py eco_worthy_original.sha256 /usr/local/share/
COPY niu_can/ /usr/local/share/niu_can/
COPY logo.png /usr/local/bin/logo.png
COPY icon.png /usr/local/bin/icon.png
CMD ["python3", "/usr/local/bin/activation_server.py"]
