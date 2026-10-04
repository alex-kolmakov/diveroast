"""Strip people from a donated dive log before it is stored.

What goes: buddy, divemaster and owner names, free-text notes, photo paths,
device serial numbers, body data (FIT user profile and Garmin's
undocumented messages). What stays: the dive profile, gases, dive site
names and GPS positions, which the dashboard needs.

Every function returns new bytes in the same format, so the stored file
still parses with the normal parsers.
"""

import io
import struct
import zipfile
from pathlib import PurePosixPath
from xml.etree.ElementTree import Element, tostring

from defusedxml.ElementTree import fromstring

from src.parsers.archive import MAX_MEMBER_BYTES, MAX_MEMBERS, MAX_TOTAL_BYTES

# --- XML (Subsurface, UDDF) -------------------------------------------------

# Subsurface: people and free text; <settings> holds the computer serials.
_SUBSURFACE_DROP = {"buddy", "divemaster", "notes", "picture", "settings"}
# UDDF: the owner and buddies (<diver>), manufacturers, dive centres and the
# people in them, media file paths, and the free-text fields.
_UDDF_DROP = {
    "diver",
    "maker",
    "business",
    "divebase",
    "mediadata",
    "notes",
    "observations",
    "anysymptoms",
}
_DROP_ATTRS = {"tags", "deviceid", "diveid", "serial", "serialnumber", "nickname"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _prune(root: Element, drop_tags: set[str]) -> None:
    for parent in root.iter():
        for child in list(parent):
            name = _local(child.tag)
            serial = name == "extradata" and "serial" in child.get("key", "").lower()
            if name in drop_tags or name == "serialnumber" or serial:
                parent.remove(child)
        for attr in [a for a in parent.attrib if a.lower() in _DROP_ATTRS]:
            del parent.attrib[attr]


def _drop_dangling_links(root: Element) -> None:
    """UDDF links dives to buddies by id; drop links whose target is gone."""
    ids = {el.get("id") for el in root.iter() if el.get("id")}
    for parent in root.iter():
        for child in list(parent):
            if _local(child.tag) == "link" and child.get("ref") not in ids:
                parent.remove(child)


def sanitize_xml(content: bytes) -> bytes:
    root = fromstring(content)
    kind = _local(root.tag)
    if kind == "uddf":
        _prune(root, _UDDF_DROP)
        _drop_dangling_links(root)
    elif kind == "divelog":
        _prune(root, _SUBSURFACE_DROP)
    else:
        raise ValueError(f"Unknown XML dive log root <{kind}>")
    return tostring(root, encoding="utf-8", xml_declaration=True)


# --- Garmin FIT ---------------------------------------------------------------

# Messages kept as they are: what the parser reads, plus the dive's own
# settings and events. Everything else (user_profile, user metrics, zones,
# Garmin's undocumented messages) has its values blanked.
_FIT_KEEP = {
    0,  # file_id (serial blanked below)
    12,  # sport
    18,  # session
    19,  # lap
    20,  # record
    21,  # event
    23,  # device_info (serial blanked below)
    34,  # activity
    49,  # file_creator
    206,  # field_description (developer field names, not values)
    207,  # developer_data_id
    258,  # dive_settings
    259,  # dive_gas
    262,  # dive_alarm
    268,  # dive_summary
    319,  # tank_update
    323,  # tank_summary
}
# Messages that identify a device keep only these documented fields; serial
# numbers, paired-sensor ANT ids and Garmin's undocumented fields are blanked.
_FIT_KEEP_FIELDS = {
    0: {0, 1, 2, 4, 5, 8},  # file_id: type, manufacturer, product, time, number, name
    23: {0, 1, 2, 4, 5, 6, 10, 11, 18, 25, 27, 32, 253},  # device_info
}

# Invalid value per FIT base type number, as (struct format, value).
_FIT_INVALID = {
    0: ("B", 0xFF),
    1: ("b", 0x7F),
    2: ("B", 0xFF),
    3: ("h", 0x7FFF),
    4: ("H", 0xFFFF),
    5: ("i", 0x7FFFFFFF),
    6: ("I", 0xFFFFFFFF),
    8: ("I", 0xFFFFFFFF),
    9: ("Q", 0xFFFFFFFFFFFFFFFF),
    13: ("B", 0xFF),
    14: ("q", 0x7FFFFFFFFFFFFFFF),
    15: ("Q", 0xFFFFFFFFFFFFFFFF),
}  # string (7) and the z types (10, 11, 12, 16) are invalid as zeros

_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)  # fmt: skip


def fit_crc(data: bytes | bytearray) -> int:
    crc = 0
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = _CRC_TABLE[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc ^= tmp ^ _CRC_TABLE[nibble]
    return crc


def _invalid(base_type: int, size: int, big_endian: bool) -> bytes:
    spec = _FIT_INVALID.get(base_type & 0x1F)
    if spec is None:
        return bytes(size)
    fmt, value = spec
    one = struct.pack((">" if big_endian else "<") + fmt, value)
    if size % len(one):
        return b"\xff" * size
    return one * (size // len(one))


def sanitize_fit(content: bytes) -> bytes:
    """Blank personal values in place; the file keeps its layout and CRCs."""
    out = bytearray(content)
    pos = 0
    while pos < len(out):  # FIT files can be chained
        header_size = out[pos]
        if header_size not in (12, 14) or out[pos + 8 : pos + 12] != b".FIT":
            raise ValueError("Not a FIT file")
        data_size = struct.unpack_from("<I", out, pos + 4)[0]
        start = pos
        pos += header_size
        end = pos + data_size
        definitions: dict[int, tuple[int, bool, list, list]] = {}
        while pos < end:
            header = out[pos]
            pos += 1
            if header & 0x80:  # compressed timestamp: a data message
                local = (header >> 5) & 0x3
            elif header & 0x40:  # definition message
                big_endian = out[pos + 1] == 1
                number = struct.unpack_from(">H" if big_endian else "<H", out, pos + 2)[
                    0
                ]
                n = out[pos + 4]
                pos += 5
                fields = [tuple(out[pos + 3 * i : pos + 3 * i + 3]) for i in range(n)]
                pos += 3 * n
                dev_fields = []
                if header & 0x20:
                    n_dev = out[pos]
                    pos += 1
                    dev_fields = [out[pos + 3 * i + 1] for i in range(n_dev)]
                    pos += 3 * n_dev
                definitions[header & 0x0F] = (number, big_endian, fields, dev_fields)
                continue
            else:
                local = header & 0x0F
            number, big_endian, fields, dev_fields = definitions[local]
            keep = number in _FIT_KEEP
            keep_fields = _FIT_KEEP_FIELDS.get(number)
            for field, size, base_type in fields:
                if not keep or (keep_fields is not None and field not in keep_fields):
                    out[pos : pos + size] = _invalid(base_type, size, big_endian)
                pos += size
            for size in dev_fields:  # third-party app data: cleared
                out[pos : pos + size] = bytes(size)
                pos += size
        struct.pack_into("<H", out, end, fit_crc(out[start:end]))
        pos = end + 2
    return bytes(out)


# --- Zip archives -------------------------------------------------------------


def sanitize_zip(content: bytes, depth: int = 0) -> bytes:
    """A new zip of the dive logs only, each sanitized and renamed.

    Everything that isn't a dive log is dropped (Garmin's account export is
    full of personal JSON), and members are renamed, since a filename can
    carry a name. Same limits as the archive parser.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) > MAX_MEMBERS:
            raise ValueError("Archive has too many files")
        if sum(m.file_size for m in members) > MAX_TOTAL_BYTES:
            raise ValueError("Archive is too large when uncompressed")
        out = io.BytesIO()
        kept = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as clean:
            for member in sorted(members, key=lambda m: m.filename):
                name = PurePosixPath(member.filename).name
                ext = PurePosixPath(name).suffix.lower()
                if name.startswith(".") or member.filename.startswith("__MACOSX/"):
                    continue
                if member.file_size > MAX_MEMBER_BYTES:
                    continue
                if ext == ".zip" and depth >= 1:
                    continue
                try:
                    data = sanitize(archive.read(member), name, depth + 1)
                except ValueError:
                    continue  # not a dive log
                kept += 1
                clean.writestr(f"log-{kept:04d}{ext}", data)
        if not kept:
            raise ValueError("No dive logs in the archive")
        return out.getvalue()


def sanitize(content: bytes, filename: str, depth: int = 0) -> bytes:
    """Sanitize a dive log by its extension. Raises ValueError if unsupported."""
    ext = PurePosixPath(filename).suffix.lower()
    if ext in (".ssrf", ".xml", ".uddf"):
        return sanitize_xml(content)
    if ext == ".fit":
        return sanitize_fit(content)
    if ext == ".zip":
        return sanitize_zip(content, depth)
    raise ValueError(f"Not a dive log: {filename}")
