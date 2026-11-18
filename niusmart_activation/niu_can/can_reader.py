import socket
import struct
import threading
import time

from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import SIGNAL_UPDATE


MODE_MAP = {
    0x0: "OFF",
    0x1: "Climatisation",
    0x2: "Chauffage",
    0x3: "Auto",
    0x4: "Ventilation",
    0x6: "Deshumidification",
}

FAN_MAP = {
    0x42: "Vitesse 1",
    0x86: "Vitesse 2",
    0xC8: "Vitesse 3",
}


def raw_to_f(raw):
    if raw in (0x0000, 0xFFFF):
        return None

    return round(
        81 + ((raw - 0x2587) * 9 / 160),
        1
    )


class NiuCanReader:

    def __init__(self, hass):
        self.hass = hass
        self._stop = threading.Event()
        self._thread = None
        self._sock = None
        self._lock = threading.Lock()

        self.zones = {
            1: {},
            2: {},
        }

        # Diagnostics Dometic separes par climatiseur.
        # R1 est publie sur la source RV-C 0xCE, R2 sur 0xCF.
        self.power_fail = {
            1: None,
            2: None,
        }
        self.power_fail_raw = {
            1: None,
            2: None,
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()

        self._thread = threading.Thread(
            target=self._run,
            name="niu-can-reader",
            daemon=True,
        )

        self._thread.start()

    def stop(self):
        self._stop.set()

        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def snapshot_zone(self, zone):
        with self._lock:
            return dict(self.zones.get(zone, {}))

    def snapshot_power_fail(self, zone):
        """Retourne (etat, trame brute) du Power Fail pour R1 ou R2."""
        if zone not in (1, 2):
            raise ValueError(f"Zone Power Fail invalide: {zone}")

        with self._lock:
            return (
                self.power_fail.get(zone),
                self.power_fail_raw.get(zone),
            )

    def send_frame(self, can_id, data):
        """Envoie une trame CAN etendue sur can1."""
        payload = bytes(data)

        if len(payload) > 8:
            raise ValueError("CAN payload > 8 octets")

        tx_sock = socket.socket(
            socket.AF_CAN,
            socket.SOCK_RAW,
            socket.CAN_RAW,
        )

        try:
            tx_sock.bind(("can1",))

            frame = struct.pack(
                "=IB3x8s",
                can_id | socket.CAN_EFF_FLAG,
                len(payload),
                payload.ljust(8, b"\x00"),
            )

            return tx_sock.send(frame)

        finally:
            tx_sock.close()

    @staticmethod
    def temperature_f_to_raw(temp_f):
        """Convertit Fahrenheit vers valeur RV-C Dometic."""
        return round(
            0x2587
            + ((float(temp_f) - 81.0) * 160.0 / 9.0)
        )

    def set_r1_temperature(self, temp_f):
        """Modifie la consigne R1 selon le mode actif."""

        zone = self.snapshot_zone(1)

        if not zone:
            raise RuntimeError("R1 indisponible")

        mode = zone.get("mode")
        mode_raw = zone.get("mode_raw")
        fan_raw = zone.get("fan_raw")

        if mode_raw is None or fan_raw is None:
            raise RuntimeError(
                "Octets bruts R1 indisponibles"
            )

        raw = self.temperature_f_to_raw(temp_f)

        if not 0 <= raw <= 0xFFFF:
            raise ValueError("Temperature hors plage")

        lo = raw & 0xFF
        hi = (raw >> 8) & 0xFF

        if mode == "Chauffage":
            data = bytes([
                0x01,
                mode_raw,
                fan_raw,
                lo,
                hi,
                0xFF,
                0xFF,
                0xFF,
            ])

        elif mode == "Climatisation":
            data = bytes([
                0x01,
                mode_raw,
                fan_raw,
                0xFF,
                0xFF,
                lo,
                hi,
                0xFF,
            ])

        else:
            raise ValueError(
                "La consigne est modifiable seulement "
                "en Chauffage ou Climatisation"
            )

        return self.send_frame(
            0x19FEF99F,
            data
        )

    def set_r1_fan_mode(self, fan_mode):
        """Commande ventilateur R1."""

        zone = self.snapshot_zone(1)

        if not zone:
            raise RuntimeError("R1 indisponible")

        if zone.get("state") == "OFF":
            raise ValueError(
                "R1 doit etre ON pour modifier le ventilateur"
            )

        fan_values = {
            "AUTO": 0x00,
            "Vitesse 1": 0x42,
            "Vitesse 2": 0x84,
            "Vitesse 3": 0xC8,
        }

        if fan_mode not in fan_values:
            raise ValueError(
                f"Mode ventilateur non supporte: {fan_mode}"
            )

        mode_raw = zone.get("mode_raw")

        if mode_raw is None:
            raise RuntimeError("Mode brut R1 indisponible")

        # Observe sur le bus :
        # AUTO    : C2
        # MANUEL  : D2
        if fan_mode == "AUTO":
            tx_mode = mode_raw & 0xEF
        else:
            tx_mode = mode_raw | 0x10

        fan_raw = fan_values[fan_mode]

        mode = zone.get("mode")

        def raw_bytes(temp_f):
            raw = self.temperature_f_to_raw(temp_f)
            return raw & 0xFF, (raw >> 8) & 0xFF

        if mode == "Chauffage":
            lo, hi = raw_bytes(
                zone.get("heat_setpoint")
            )

            data = bytes([
                0x01,
                tx_mode,
                fan_raw,
                lo,
                hi,
                0xFF,
                0xFF,
                0xFF,
            ])

        elif mode == "Climatisation":
            lo, hi = raw_bytes(
                zone.get("cool_setpoint")
            )

            data = bytes([
                0x01,
                tx_mode,
                fan_raw,
                0xFF,
                0xFF,
                lo,
                hi,
                0xFF,
            ])

        else:
            data = bytes([
                0x01,
                tx_mode,
                fan_raw,
                0xFF,
                0xFF,
                0xFF,
                0xFF,
                0xFF,
            ])

        return self.send_frame(
            0x19FEF99F,
            data
        )

    def set_r1_mode(self, mode):
        """Commande de mode R1 validee physiquement."""

        frames = {
            "off": "01 F0 FF FF FF FF FF FF",
            "fan_only": "01 F4 FF FF FF 11 24 FF",
            "heat": "01 D2 C8 27 26 FF FF FF",
            "cool": "01 D1 C8 FF FF 11 24 FF",
            "dehumidify": "01 D6 42 FF FF 6A 24 FF",
        }

        if mode not in frames:
            raise ValueError(f"Mode R1 non supporte: {mode}")

        data = bytes.fromhex(frames[mode])

        return self.send_frame(
            0x19FEF99F,
            data
        )

    def set_r1_power(self, turn_on):
        """Commande R1 validee physiquement sur le Dometic."""

        if turn_on:
            # R1 ON / Ventilation
            data = bytes.fromhex(
                "01 F4 FF FF FF 11 24 FF"
            )
        else:
            # R1 OFF
            data = bytes.fromhex(
                "01 F0 FF FF FF FF FF FF"
            )

        return self.send_frame(
            0x19FEF99F,
            data
        )

    def set_r2_temperature(self, temp_f):
        """Modifie la consigne R2 selon le mode actif."""

        zone = self.snapshot_zone(2)

        if not zone:
            raise RuntimeError("R2 indisponible")

        mode = zone.get("mode")
        mode_raw = zone.get("mode_raw")
        fan_raw = zone.get("fan_raw")

        if mode_raw is None or fan_raw is None:
            raise RuntimeError("Octets bruts R2 indisponibles")

        raw = self.temperature_f_to_raw(temp_f)

        if not 0 <= raw <= 0xFFFF:
            raise ValueError("Temperature hors plage")

        lo = raw & 0xFF
        hi = (raw >> 8) & 0xFF

        if mode == "Chauffage":
            data = bytes([
                0x02,
                mode_raw,
                fan_raw,
                lo,
                hi,
                0xFF,
                0xFF,
                0xFF,
            ])

        elif mode == "Climatisation":
            data = bytes([
                0x02,
                mode_raw,
                fan_raw,
                0xFF,
                0xFF,
                lo,
                hi,
                0xFF,
            ])

        else:
            raise ValueError(
                "La consigne est modifiable seulement "
                "en Chauffage ou Climatisation"
            )

        return self.send_frame(0x19FEF99F, data)

    def set_r2_fan_mode(self, fan_mode):
        """Commande ventilateur R2."""

        zone = self.snapshot_zone(2)

        if not zone:
            raise RuntimeError("R2 indisponible")

        if zone.get("state") == "OFF":
            raise ValueError(
                "R2 doit etre ON pour modifier le ventilateur"
            )

        fan_values = {
            "AUTO": 0x00,
            "Vitesse 1": 0x42,
            "Vitesse 2": 0x84,
            "Vitesse 3": 0xC8,
        }

        if fan_mode not in fan_values:
            raise ValueError(
                f"Mode ventilateur non supporte: {fan_mode}"
            )

        mode_raw = zone.get("mode_raw")

        if mode_raw is None:
            raise RuntimeError("Mode brut R2 indisponible")

        if fan_mode == "AUTO":
            tx_mode = mode_raw & 0xEF
        else:
            tx_mode = mode_raw | 0x10

        fan_raw = fan_values[fan_mode]
        mode = zone.get("mode")

        def raw_bytes(temp_f):
            raw = self.temperature_f_to_raw(temp_f)
            return raw & 0xFF, (raw >> 8) & 0xFF

        if mode == "Chauffage":
            lo, hi = raw_bytes(zone.get("heat_setpoint"))
            data = bytes([
                0x02,
                tx_mode,
                fan_raw,
                lo,
                hi,
                0xFF,
                0xFF,
                0xFF,
            ])

        elif mode == "Climatisation":
            lo, hi = raw_bytes(zone.get("cool_setpoint"))
            data = bytes([
                0x02,
                tx_mode,
                fan_raw,
                0xFF,
                0xFF,
                lo,
                hi,
                0xFF,
            ])

        else:
            data = bytes([
                0x02,
                tx_mode,
                fan_raw,
                0xFF,
                0xFF,
                0xFF,
                0xFF,
                0xFF,
            ])

        return self.send_frame(0x19FEF99F, data)

    def set_r2_mode(self, mode):
        """Commande de mode R2, copie du comportement valide R1."""

        frames = {
            "off": "02 F0 FF FF FF FF FF FF",
            "fan_only": "02 F4 FF FF FF 11 24 FF",
            "heat": "02 D2 C8 27 26 FF FF FF",
            "cool": "02 D1 C8 FF FF 11 24 FF",
            "dehumidify": "02 D6 42 FF FF 6A 24 FF",
        }

        if mode not in frames:
            raise ValueError(f"Mode R2 non supporte: {mode}")

        return self.send_frame(
            0x19FEF99F,
            bytes.fromhex(frames[mode])
        )

    def set_r2_power(self, turn_on):
        """Commande ON/OFF R2."""

        if turn_on:
            data = bytes.fromhex("02 F4 FF FF FF 11 24 FF")
        else:
            data = bytes.fromhex("02 F0 FF FF FF FF FF FF")

        return self.send_frame(0x19FEF99F, data)

    def _notify(self):
        self.hass.loop.call_soon_threadsafe(
            async_dispatcher_send,
            self.hass,
            SIGNAL_UPDATE,
        )

    def _run(self):

        while not self._stop.is_set():

            try:
                sock = socket.socket(
                    socket.AF_CAN,
                    socket.SOCK_RAW,
                    socket.CAN_RAW
                )

                sock.bind(("can1",))
                sock.settimeout(1.0)

                self._sock = sock

                while not self._stop.is_set():

                    try:
                        frame = sock.recv(16)
                    except TimeoutError:
                        continue
                    except socket.timeout:
                        continue

                    if len(frame) < 16:
                        continue

                    can_id, dlc, raw = struct.unpack(
                        "=IB3x8s",
                        frame
                    )

                    can_id &= 0x1FFFFFFF
                    data = list(raw[:dlc])

                    changed = False

                    # ------------------------------------
                    # THERMOSTAT_STATUS_1 = 1FFE2
                    # ------------------------------------
                    if (
                        (can_id & 0x1FFFFF00) == 0x19FFE200
                        and len(data) >= 7
                    ):
                        instance = data[0]

                        if instance in (1, 2):
                            mode_raw = data[1]
                            mode_code = mode_raw & 0x0F

                            if (mode_raw & 0x10) == 0:
                                fan = "AUTO"
                            else:
                                fan = FAN_MAP.get(
                                    data[2],
                                    f"0x{data[2]:02X}"
                                )

                            heat_raw = (
                                data[3]
                                | (data[4] << 8)
                            )

                            cool_raw = (
                                data[5]
                                | (data[6] << 8)
                            )

                            zone_data = {
                                "state":
                                    "OFF"
                                    if mode_code == 0
                                    else "ON",

                                "mode":
                                    MODE_MAP.get(
                                        mode_code,
                                        f"0x{mode_code:X}"
                                    ),

                                "fan": fan,
                                "mode_raw": mode_raw,
                                "fan_raw": data[2],

                                "heat_setpoint":
                                    raw_to_f(heat_raw),

                                "cool_setpoint":
                                    raw_to_f(cool_raw),

                                "can_id":
                                    f"0x{can_id:08X}",

                                "raw":
                                    " ".join(
                                        f"{x:02X}"
                                        for x in data
                                    ),

                                "updated":
                                    time.time(),
                            }

                            with self._lock:
                                self.zones[instance] = zone_data

                            changed = True

                    # ------------------------------------
                    # DM-RV = 1FECA
                    # Diagnostics Power Fail Dometic
                    #   source CE = R1
                    #   source CF = R2
                    # Signatures confirmees sur le bus :
                    #   R1 : 1F 67 01 01 DF 96 FF FF
                    #   R2 : 1F 67 01 02 DF 94 FF FF
                    # Retour normal sur la source correspondante :
                    #   0F 58 FF FF FF FF FF FF
                    # ------------------------------------
                    elif (
                        (can_id & 0x1FFFFF00) == 0x19FECA00
                        and len(data) >= 6
                    ):

                        source = can_id & 0xFF
                        zone = {
                            0xCE: 1,
                            0xCF: 2,
                        }.get(source)

                        if zone is not None:
                            raw_text = " ".join(
                                f"{x:02X}"
                                for x in data
                            )

                            # Power Fail de la zone correspondante.
                            # data[3] contient l'instance 01 / 02.
                            if (
                                data[1] == 0x67
                                and data[2] == 0x01
                                and data[3] == zone
                                and data[4] == 0xDF
                            ):
                                with self._lock:
                                    self.power_fail[zone] = True
                                    self.power_fail_raw[zone] = raw_text

                                changed = True

                            # Diagnostic efface / retabli.
                            # La source CE/CF permet de savoir quelle zone
                            # revient a NORMAL meme si l'instance disparait.
                            elif data[2:5] == [
                                0xFF,
                                0xFF,
                                0xFF
                            ]:
                                with self._lock:
                                    self.power_fail[zone] = False
                                    self.power_fail_raw[zone] = raw_text

                                changed = True

                    if changed:
                        self._notify()

            except Exception:
                try:
                    if self._sock:
                        self._sock.close()
                except Exception:
                    pass

                self._sock = None

                if not self._stop.is_set():
                    time.sleep(3)

