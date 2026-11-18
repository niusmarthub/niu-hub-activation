"""Install and read the ECO-WORTHY Modbus diagnostics for REM-0021."""

import hashlib
import os
import stat
import tempfile

from pathlib import Path

PAYLOAD = Path("/usr/local/share/eco_worthy_sensor.py")
BASE_HASH = Path("/usr/local/share/eco_worthy_original.sha256")
ENTITY = "sensor.eco_worthy_registres_modbus"


def normalized(data):
    return data.replace(b"\r\n", b"\n")


def staged_file(directory, data, owner):
    fd, name = tempfile.mkstemp(prefix=".niu-eco-worthy-", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchown(handle.fileno(), owner.st_uid, owner.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(owner.st_mode))
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def install_eco_diagnostics(root, *, payload=PAYLOAD, base_hash=BASE_HASH):
    target = root / "custom_components" / "eco_worthy" / "sensor.py"
    if target.is_symlink():
        return "different_version"
    try:
        original = target.read_bytes()
    except FileNotFoundError:
        return "missing"
    replacement = payload.read_bytes()
    if normalized(original) == normalized(replacement):
        return "installed"
    if hashlib.sha256(normalized(original)).hexdigest() != base_hash.read_text().strip():
        return "different_version"

    metadata = target.stat()
    backup = target.with_name("sensor.py.niu-before-registers")
    if not backup.exists():
        saved = staged_file(target.parent, original, metadata)
        try:
            os.link(saved, backup)
        except FileExistsError:
            pass
        finally:
            saved.unlink(missing_ok=True)
    if backup.is_symlink() or normalized(backup.read_bytes()) != normalized(original):
        return "backup_conflict"

    candidate = staged_file(target.parent, replacement, metadata)
    try:
        if target.is_symlink() or target.read_bytes() != original:
            return "different_version"
        candidate.replace(target)
    finally:
        candidate.unlink(missing_ok=True)
    return "installed"



def diagnostic_rows(states):
    """Return only the two complete, numbered blocks from the diagnostic sensor."""
    for state in states:
        entity_id = state.get("entity_id", "")
        if entity_id != ENTITY and not (
            entity_id.startswith(f"{ENTITY}_") and entity_id[len(ENTITY) + 1:].isdigit()
        ):
            continue
        registers = state.get("attributes", {}).get("registres")
        if state.get("state") in ("unavailable", "unknown") or not isinstance(registers, list):
            continue
        if len(registers) != 30:
            continue

        rows = []
        for idx, row in enumerate(registers):
            block = "L01" if idx < 15 else "L02"
            address = f"0x{idx if idx < 15 else 0x0401 + idx - 15:04X}"
            value = row.get("decimale") if isinstance(row, dict) else None
            if (type(value) is not int or not 0 <= value <= 0xffff
                    or row.get("numero") != idx + 1 or row.get("adresse") != address):
                break
            note = row.get("repere")
            rows.append({
                "numero": idx + 1,
                "bloc": block,
                "adresse": address,
                "decimale": value,
                "hexadecimale": f"0x{value:04X}",
                "repere": note[:100] if isinstance(note, str) else "Non identifié",
            })
        if len(rows) == 30:
            return rows
    return []
