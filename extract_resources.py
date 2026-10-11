#!/usr/bin/env python3
"""Statically extract PE resource payloads, strings, templates, BMPs and ICOs.

Inputs are read only. PE resource offsets must map unambiguously to stored
bytes; malformed trees are rejected before writing a module's resources.
Unknown template/image formats retain their raw payload and a parse error.
"""

import argparse
import hashlib
import json
import struct
import zlib
from collections import Counter
from pathlib import Path


TYPE_NAMES = {1: "CURSOR", 2: "BITMAP", 3: "ICON", 4: "MENU", 5: "DIALOG",
              6: "STRING", 7: "FONTDIR", 8: "FONT", 9: "ACCELERATOR",
              10: "RCDATA", 11: "MESSAGETABLE", 12: "GROUP_CURSOR",
              14: "GROUP_ICON", 16: "VERSION", 17: "DLGINCLUDE", 19: "PLUGPLAY",
              20: "VXD", 21: "ANICURSOR", 22: "ANIICON", 23: "HTML", 24: "MANIFEST"}
PRIMARY = ("shell32.exe", "aygshell.dll", "netui.dll", "voipphonecanvas.dll",
           "tmailutl.dll", "taskdll.dll", "shutil.dll", "WLMTodayScreen.dll")
PRIMARY_FAMILIES = ("shellres", "tshres")


class ResourceError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def is_today(name):
    value = name.lower()
    return (any(value == p.lower() or value == p.lower() + ".0412.mui" for p in PRIMARY)
            or any(value.startswith(p) and value.endswith((".dll", ".mui")) for p in PRIMARY_FAMILIES))


class Cursor:
    def __init__(self, data, offset=0):
        self.data = data
        self.offset = offset

    def take(self, size):
        if size < 0 or self.offset + size > len(self.data):
            raise ResourceError(f"template truncated at {self.offset:#x}, need {size} bytes")
        value = self.data[self.offset:self.offset + size]
        self.offset += size
        return value

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))

    def u16(self):
        return self.unpack("<H")[0]

    def u32(self):
        return self.unpack("<I")[0]

    def align(self, size=4):
        self.take((-self.offset) % size)

    def text(self, first=None):
        units = [] if first is None else [first]
        while not units or units[-1]:
            units.append(self.u16())
        return struct.pack("<" + "H" * (len(units) - 1), *units[:-1]).decode("utf-16le")

    def atom(self):
        value = self.u16()
        if value == 0:
            return None
        if value == 0xFFFF:
            return {"ordinal": self.u16()}
        return {"text": self.text(value)}


class PE:
    def __init__(self, data):
        self.data = data
        if self.read(0, 2) != b"MZ":
            raise ResourceError("not an MZ PE input")
        self.pe_offset = self.unpack("<I", 0x3C)[0]
        if self.read(self.pe_offset, 4) != b"PE\0\0":
            raise ResourceError("PE signature missing")
        self.machine, count = self.unpack("<HH", self.pe_offset + 4)
        opt_size = self.unpack("<H", self.pe_offset + 20)[0]
        opt = self.pe_offset + 24
        self.read(opt, opt_size)
        magic = self.unpack("<H", opt)[0]
        if magic == 0x10B:
            count_offset, directory_offset = 92, 96
        elif magic == 0x20B:
            count_offset, directory_offset = 108, 112
        else:
            raise ResourceError(f"unsupported PE optional header {magic:#x}")
        self.size_of_headers = self.unpack("<I", opt + 60)[0]
        if opt_size < count_offset + 4:
            raise ResourceError("optional header has no directory count")
        directory_count = self.unpack("<I", opt + count_offset)[0]
        self.resource_rva = self.resource_size = 0
        if directory_count > 2:
            if opt_size < directory_offset + 24:
                raise ResourceError("resource directory entry outside optional header")
            self.resource_rva, self.resource_size = self.unpack("<II", opt + directory_offset + 16)
        self.sections = []
        for i in range(count):
            offset = opt + opt_size + i * 40
            record = self.read(offset, 40)
            virtual_size, rva, raw_size, raw_offset = struct.unpack_from("<4I", record, 8)
            if raw_size:
                self.read(raw_offset, raw_size)
            self.sections.append({"index": i, "name_hex": record[:8].hex(),
                                  "virtual_size": virtual_size, "rva": rva,
                                  "raw_size": raw_size, "raw_offset": raw_offset})

    def read(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ResourceError(f"PE file range invalid: [{offset:#x}, {offset + size:#x})")
        return self.data[offset:offset + size]

    def unpack(self, fmt, offset):
        return struct.unpack(fmt, self.read(offset, struct.calcsize(fmt)))

    def map_rva(self, rva, size):
        if rva < 0 or size < 0 or rva + size > 0x100000000:
            raise ResourceError("invalid resource RVA/size")
        candidates = [(s["raw_offset"] + rva - s["rva"], s["index"])
                      for s in self.sections
                      if s["rva"] <= rva and rva + size <= s["rva"] + s["raw_size"]
                      and (size or rva < s["rva"] + s["raw_size"])]
        if rva + size <= self.size_of_headers and rva + size <= len(self.data):
            candidates.append((rva, None))
        if len(candidates) != 1:
            raise ResourceError(f"RVA {rva:#x}+{size:#x} has {len(candidates)} stored mappings")
        offset, index = candidates[0]
        self.read(offset, size)
        return offset, index

    def resources(self):
        if not self.resource_rva and not self.resource_size:
            return [], [], None
        if not self.resource_rva or not self.resource_size:
            raise ResourceError("incomplete resource directory RVA/size")
        root, section = self.map_rva(self.resource_rva, self.resource_size)
        resources, nodes, paths = [], [], set()
        visits = 0

        def relative(offset, size):
            if offset < 0 or size < 0 or offset + size > self.resource_size:
                raise ResourceError(f"resource tree offset {offset:#x}+{size:#x} exceeds directory")
            return self.read(root + offset, size)

        def walk(offset, path, ancestors):
            nonlocal visits
            visits += 1
            if len(path) > 8 or offset in ancestors or visits > 100000:
                raise ResourceError("resource directory cycle/depth/count limit exceeded")
            characteristics, timestamp, major, minor, named, ids = struct.unpack("<II4H", relative(offset, 16))
            entries = relative(offset + 16, (named + ids) * 8)
            nodes.append({"path": path, "relative_offset": offset,
                          "characteristics": characteristics, "timestamp": timestamp,
                          "major": major, "minor": minor, "named_count": named, "id_count": ids})
            for i, (name, target) in enumerate(struct.iter_unpack("<II", entries)):
                named_flag = bool(name & 0x80000000)
                if named_flag != (i < named):
                    raise ResourceError("resource named/ID ordering disagrees with directory counts")
                if named_flag:
                    name_offset = name & 0x7FFFFFFF
                    length = struct.unpack("<H", relative(name_offset, 2))[0]
                    key = relative(name_offset + 2, length * 2).decode("utf-16le")
                else:
                    key = name
                item_path = path + [key]
                if target & 0x80000000:
                    walk(target & 0x7FFFFFFF, item_path, ancestors | {offset})
                    continue
                if tuple(item_path) in paths:
                    raise ResourceError(f"duplicate resource path {item_path}")
                paths.add(tuple(item_path))
                rva, size, codepage, reserved = struct.unpack("<4I", relative(target, 16))
                payload_offset, payload_section = self.map_rva(rva, size)
                payload = self.read(payload_offset, size)
                resources.append({"path": item_path, "type": item_path[0],
                                  "type_name": TYPE_NAMES.get(item_path[0], str(item_path[0])),
                                  "id": item_path[1] if len(item_path) > 1 else None,
                                  "language": item_path[2] if len(item_path) > 2 else None,
                                  "data_entry_relative_offset": target,
                                  "rva": rva, "source_offset": payload_offset,
                                  "section_index": payload_section, "size": size,
                                  "codepage": codepage, "reserved": reserved,
                                  "sha256": digest(payload), "payload": payload})

        walk(0, [], set())
        return resources, nodes, {"rva": self.resource_rva, "size": self.resource_size,
                                  "source_offset": root, "section_index": section}


def parse_strings(payload, block):
    if not isinstance(block, int) or block < 1:
        raise ResourceError("RT_STRING block ID is not a positive integer")
    cursor = Cursor(payload)
    entries = []
    for i in range(16):
        count = cursor.u16()
        text_offset = cursor.offset
        text = cursor.take(count * 2).decode("utf-16le")
        entries.append({"id": (block - 1) * 16 + i, "text": text,
                        "utf16_code_units": count, "payload_text_offset": text_offset})
    return {"format": "RT_STRING UTF-16LE", "block_id": block, "strings": entries,
            "trailing_bytes_hex": payload[cursor.offset:].hex()}


def parse_menu(payload):
    cursor = Cursor(payload)
    version, offset = cursor.unpack("<HH")
    if version not in (0, 1):
        raise ResourceError(f"unsupported menu version {version}")
    result = {"format": "MENUEX" if version else "MENU", "version": version, "header_offset": offset}
    if version:
        result["help_id"] = cursor.u32()
    first_item = 4 + offset
    if first_item < cursor.offset:
        raise ResourceError("menu item offset overlaps header")
    cursor.take(first_item - cursor.offset)
    count = 0

    def level(depth):
        nonlocal count
        if depth > 64:
            raise ResourceError("menu nesting limit exceeded")
        items = []
        while True:
            count += 1
            if count > 10000:
                raise ResourceError("menu item limit exceeded")
            start = cursor.offset
            if version:
                cursor.align()
                type_flags, state, command, info = cursor.unpack("<IIIH")
                popup, end = bool(info & 1), bool(info & 0x80)
                item = {"type_flags": type_flags, "state": state,
                        "command_id": command, "resource_info": info}
            else:
                options = cursor.u16()
                popup, end = bool(options & 0x10), bool(options & 0x80)
                item = {"options": options, "command_id": None if popup else cursor.u16()}
            item.update({"payload_offset": start, "popup": popup, "end": end, "text": cursor.text()})
            if version:
                cursor.align()
            if popup:
                if version:
                    item["help_id"] = cursor.u32()
                item["children"] = level(depth + 1)
            items.append(item)
            if end:
                return items

    result["items"] = level(0)
    result["trailing_bytes_hex"] = payload[cursor.offset:].hex()
    return result


def parse_dialog(payload):
    cursor = Cursor(payload)
    extended = payload[:4] == b"\x01\x00\xff\xff"
    if extended:
        version, signature, help_id, ex_style, style, count, x, y, cx, cy = cursor.unpack("<HHIIIH4h")
        result = {"format": "DIALOGEX", "version": version, "signature": signature, "help_id": help_id}
    else:
        style, ex_style, count, x, y, cx, cy = cursor.unpack("<IIH4h")
        result = {"format": "DIALOG"}
    result.update({"style": style, "extended_style": ex_style, "control_count": count,
                   "x": x, "y": y, "cx": cx, "cy": cy,
                   "menu": cursor.atom(), "window_class": cursor.atom(), "title": cursor.atom()})
    if style & 0x40:
        font = {"point_size": cursor.u16()}
        if extended:
            font["weight"], font["italic"], font["charset"] = cursor.unpack("<HBB")
        font["typeface"] = cursor.text()
        result["font"] = font
    if count > 10000:
        raise ResourceError("dialog control count limit exceeded")
    controls = []
    for _ in range(count):
        cursor.align()
        start = cursor.offset
        if extended:
            help_id, ex_style, style, x, y, cx, cy, control_id = cursor.unpack("<III4hI")
            control = {"help_id": help_id}
        else:
            style, ex_style, x, y, cx, cy, control_id = cursor.unpack("<II4hH")
            control = {}
        control.update({"payload_offset": start, "style": style, "extended_style": ex_style,
                        "x": x, "y": y, "cx": cx, "cy": cy, "id": control_id,
                        "window_class": cursor.atom(), "title": cursor.atom()})
        extra = cursor.u16()
        if extra:
            raise ResourceError(f"nonzero control creation-data size {extra} is retained raw, not interpreted")
        control["creation_data_size"] = extra
        controls.append(control)
    result["controls"] = controls
    result["coordinate_units"] = "dialog template units; no pixel-size conversion assumed"
    result["trailing_bytes_hex"] = payload[cursor.offset:].hex()
    return result


def wrap_bitmap(payload):
    cursor = Cursor(payload)
    header_size = cursor.u32()
    if header_size == 12:
        width, height, planes, bits = cursor.unpack("<4H")
        compression, image_size = 0, 0
        colors, palette_entry = (1 << bits) if bits <= 8 else 0, 3
        mask_size = 0
    elif header_size in (40, 52, 56, 108, 124):
        if len(payload) < header_size:
            raise ResourceError("truncated bitmap header")
        width, height, planes, bits, compression, image_size, _, _, colors, _ = cursor.unpack("<iiHHIIiiII")
        if bits <= 8 and not colors:
            colors = 1 << bits
        palette_entry = 4
        mask_size = (16 if compression == 6 else 12) if header_size == 40 and compression in (3, 6) else 0
        if header_size == 124 and struct.unpack_from("<I", payload, 116)[0]:
            raise ResourceError("V5 bitmap profile placement requires interpretation; raw DIB retained")
    else:
        raise ResourceError(f"unsupported DIB header size {header_size}")
    ce_2bpp = bits == 2 and header_size == 40 and compression == 0
    if planes != 1 or width <= 0 or height == 0 or bits not in (1, 2, 4, 8, 16, 24, 32):
        raise ResourceError("unsupported bitmap geometry/bit depth")
    if bits == 2 and not ce_2bpp:
        raise ResourceError("unsupported 2-bpp DIB variant")
    if compression not in (0, 1, 2, 3, 6):
        raise ResourceError(f"unsupported bitmap compression {compression}")
    if (compression == 1 and bits != 8) or (compression == 2 and bits != 4) or (compression in (3, 6) and bits not in (16, 32)):
        raise ResourceError("bitmap compression/bit depth mismatch")
    if colors > 65536 or (bits <= 8 and colors > (1 << bits)):
        raise ResourceError("invalid bitmap palette size")
    pixel_offset = header_size + mask_size + colors * palette_entry
    computed_pixel_size = ((width * bits + 31) // 32) * 4 * abs(height)
    # CE has 2-bpp DIBs; several retain a stale biSizeImage from a different
    # bit depth. Use their explicit geometry only when every row is stored.
    needed = 0 if ce_2bpp else image_size
    if compression in (0, 3, 6):
        needed = max(needed, computed_pixel_size)
    if pixel_offset > len(payload) or pixel_offset + needed > len(payload):
        raise ResourceError("bitmap pixels/palette exceed resource payload")
    bitmap = struct.pack("<2sIHHI", b"BM", 14 + len(payload), 0, 0, 14 + pixel_offset) + payload
    return bitmap, {"width": width, "height": height, "bits_per_pixel": bits,
                    "compression": compression, "dib_header_size": header_size,
                    "palette_count": colors, "palette_entry_size": palette_entry,
                    "pixel_offset": 14 + pixel_offset, "payload_unchanged": True,
                    "windows_ce_2bpp": ce_2bpp, "standard_desktop_bmp": not ce_2bpp,
                    "declared_image_size": image_size, "computed_pixel_size": computed_pixel_size,
                    "declared_image_size_mismatch": image_size not in (0, computed_pixel_size) if ce_2bpp else None}


def ce_2bpp_png(payload, metadata):
    """Expand CE's palette indexes to RGB; keep dimensions and pixels exact."""
    width, height = metadata["width"], metadata["height"]
    palette_offset = metadata["dib_header_size"]
    palette = [tuple(reversed(payload[palette_offset + i * 4:palette_offset + i * 4 + 3]))
               for i in range(metadata["palette_count"])]
    stride = ((width * 2 + 31) // 32) * 4
    pixel_offset = metadata["pixel_offset"] - 14
    rows = bytearray()
    for y in range(abs(height)):
        stored_y = abs(height) - 1 - y if height > 0 else y
        rows.append(0)  # PNG filter None.
        for x in range(width):
            packed = payload[pixel_offset + stored_y * stride + x // 4]
            index = (packed >> (6 - (x % 4) * 2)) & 3
            if index >= len(palette):
                raise ResourceError("CE 2-bpp pixel refers outside palette")
            rows.extend(palette[index])

    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))

    header = struct.pack(">2I5B", width, abs(height), 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def assemble_icon(group, resources):
    cursor = Cursor(group["payload"])
    reserved, kind, count = cursor.unpack("<3H")
    if reserved or kind != 1 or not 0 < count <= 1024:
        raise ResourceError("invalid RT_GROUP_ICON header")
    entries, images, dependencies = [], [], []
    offset = 6 + count * 16
    for _ in range(count):
        width, height, colors, reserved, planes, bits, size, icon_id = cursor.unpack("<4BHHIH")
        if reserved:
            raise ResourceError("group icon entry reserved byte is nonzero")
        candidates = [r for r in resources if r["type"] == 3 and r["id"] == icon_id]
        same = [r for r in candidates if r["language"] == group["language"]]
        neutral = [r for r in candidates if r["language"] == 0]
        selected = same or neutral or candidates
        if len(selected) != 1:
            raise ResourceError(f"RT_ICON {icon_id} has {len(selected)} language candidates")
        icon = selected[0]
        if len(icon["payload"]) != size:
            raise ResourceError(f"RT_ICON {icon_id} size disagrees with group entry")
        entries.append(struct.pack("<4BHHII", width, height, colors, 0, planes, bits, size, offset))
        images.append(icon["payload"])
        dependencies.append({"icon_id": icon_id, "language": icon["language"],
                             "sha256": icon["sha256"], "size": size,
                             "language_selection": "exact" if same else "neutral" if neutral else "sole_candidate"})
        offset += size
    tail = group["payload"][cursor.offset:]
    if any(tail):
        raise ResourceError("nonzero bytes follow RT_GROUP_ICON entries")
    return struct.pack("<3H", 0, 1, count) + b"".join(entries) + b"".join(images), dependencies


def resource_label(value):
    if isinstance(value, int):
        return str(value)
    return "s_" + digest(value.encode("utf-8"))[:16]


def write_output(output, relative, data):
    target = output / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"path": str(relative), "size": len(data), "sha256": digest(data)}


def load_origin(source, imgfs_manifest):
    metadata_path = source.with_name(source.name + ".json")
    if metadata_path.is_file():
        raw = metadata_path.read_bytes()
        metadata = json.loads(raw)
        return {"kind": "reassembled_IMGFS_PE_analysis_container", "metadata_path": str(metadata_path),
                "metadata_sha256": digest(raw), "rom_metadata_path": metadata.get("source_metadata_path"),
                "analysis_only": metadata.get("analysis_only")}, metadata
    matches = [f for f in imgfs_manifest.get("files", []) if f.get("name") == source.name]
    record = matches[0] if len(matches) == 1 else {}
    return {"kind": "IMGFS_original_file", "imgfs_path": record.get("path"),
            "imgfs_sha256": record.get("sha256"), "rom_raw_header_offset": record.get("raw_header")}, None


def extract_module(source, source_kind, output, imgfs_manifest):
    data = source.read_bytes()
    summary = {"name": source.name, "source_path": str(source), "source_kind": source_kind,
               "source_size": len(data), "source_sha256": digest(data), "today_primary": is_today(source.name)}
    pe = PE(data)
    resources, nodes, directory = pe.resources()
    origin, origin_metadata = load_origin(source, imgfs_manifest)
    summary["origin"] = origin
    if origin.get("imgfs_sha256") and origin["imgfs_sha256"] != summary["source_sha256"]:
        raise ResourceError("original IMGFS file hash disagrees with IMGFS manifest")
    base = Path(source_kind) / source.name
    string_records, dialog_records, menu_records = [], [], []
    parsed_count, parse_errors, asset_count = Counter(), [], Counter()
    for index, resource in enumerate(resources):
        label = f"{index:05d}_T{resource_label(resource['type'])}_N{resource_label(resource['id'])}_L{resource_label(resource['language'])}"
        payload = resource["payload"]
        resource["raw_file"] = write_output(output, base / "raw" / (label + ".bin"), payload)
        if origin_metadata is not None and resource["section_index"] is not None:
            original_sections = origin_metadata.get("sections", [])
            matching = [s for s in original_sections if s.get("index") == resource["section_index"]]
            if len(matching) == 1:
                s = matching[0]
                resource["rom_section_source_path"] = s.get("source_path")
                resource["rom_section_source_offset"] = resource["source_offset"] - s["raw_offset"]
        context = {"source_path": str(source), "source_sha256": summary["source_sha256"],
                   "source_kind": source_kind, "module": source.name, "resource_id": resource["id"],
                   "language": resource["language"], "resource_sha256": resource["sha256"],
                   "raw_file": resource["raw_file"]["path"], "today_primary": summary["today_primary"]}
        try:
            parsed = None
            if resource["type"] == 6:
                parsed = parse_strings(payload, resource["id"])
                string_records.extend({**context, **s} for s in parsed["strings"] if s["text"])
            elif resource["type"] == 4:
                parsed = parse_menu(payload)
                menu_records.append({**context, **parsed})
            elif resource["type"] == 5:
                parsed = parse_dialog(payload)
                dialog_records.append({**context, **parsed})
            elif resource["type"] == 2:
                bmp, metadata = wrap_bitmap(payload)
                resource["bitmap"] = metadata
                resource["bitmap_file"] = write_output(output, base / "assets" / (label + ".bmp"), bmp)
                asset_count["bmp"] += 1
                if metadata["windows_ce_2bpp"]:
                    png = ce_2bpp_png(payload, metadata)
                    resource["converted_png_file"] = write_output(output, base / "assets" / (label + ".png"), png)
                    resource["png_conversion"] = "Exact 2-bpp palette-index expansion to RGB; no resizing or other pixel edits."
                    asset_count["converted_ce_2bpp_png"] += 1
            elif resource["type"] == 14:
                ico, dependencies = assemble_icon(resource, resources)
                resource["icon_dependencies"] = dependencies
                resource["icon_file"] = write_output(output, base / "assets" / (label + ".ico"), ico)
                asset_count["ico"] += 1
            if parsed is not None:
                resource["parsed_file"] = write_output(output, base / "parsed" / (label + ".json"), json_bytes(parsed))
                parsed_count[resource["type_name"]] += 1
            native = None
            if payload.startswith(b"\x89PNG\r\n\x1a\n"):
                native = ".png"
            elif payload.startswith((b"GIF87a", b"GIF89a")):
                native = ".gif"
            elif payload.startswith(b"\xff\xd8\xff"):
                native = ".jpg"
            if native:
                resource["native_image_file"] = write_output(output, base / "assets" / (label + native), payload)
                asset_count[native[1:]] += 1
        except (ResourceError, UnicodeDecodeError, struct.error) as exc:
            resource["parse_error"] = str(exc)
            parse_errors.append({"resource_path": resource["path"], "error": str(exc)})
    summary.update({"status": "extracted" if resources else "no_resource_directory",
                    "resource_count": len(resources), "resource_types": dict(Counter(r["type_name"] for r in resources)),
                    "nonempty_string_count": len(string_records), "parsed_count": dict(parsed_count),
                    "asset_count": dict(asset_count), "parse_errors": parse_errors})
    metadata = {**summary, "machine": pe.machine, "resource_directory": directory,
                "directory_nodes": nodes, "resources": [{k: v for k, v in r.items() if k != "payload"} for r in resources]}
    if directory:
        raw = pe.read(directory["source_offset"], directory["size"])
        metadata["resource_directory_raw_file"] = write_output(output, base / "resource_directory.bin", raw)
    summary["metadata"] = write_output(output, base / "metadata.json", json_bytes(metadata))
    if source.read_bytes() != data:
        raise ResourceError("input changed during extraction; output provenance is not final")
    return summary, string_records, dialog_records, menu_records


def extract(pe_dir, imgfs_dir, output, today_only, update=False):
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        if not update or not output.is_dir():
            raise ResourceError(f"output must be absent or empty (or use --update for this extractor's output): {output}")
        previous = json.loads((output / "manifest.json").read_text())
        if (previous.get("format") != "Static PE resource extraction"
                or previous.get("source_directories") != [str(pe_dir), str(imgfs_dir)]
                or previous.get("today_only") != today_only):
            raise ResourceError("--update requires a matching previous extraction manifest")
    imgfs_manifest_path = imgfs_dir.parent / "manifest.json"
    imgfs_manifest = json.loads(imgfs_manifest_path.read_text()) if imgfs_manifest_path.is_file() else {}
    candidates = [(p, kind) for directory, kind in ((pe_dir, "pe_modules"), (imgfs_dir, "imgfs_files"))
                  for p in sorted(directory.iterdir(), key=lambda p: p.name.casefold())
                  if p.is_file() and p.suffix.lower() in (".dll", ".exe", ".mui", ".cpl", ".acm")
                  and (not today_only or is_today(p.name))]
    summaries, strings, dialogs, menus = [], [], [], []
    for source, kind in candidates:
        try:
            summary, new_strings, new_dialogs, new_menus = extract_module(source, kind, output, imgfs_manifest)
            strings.extend(new_strings)
            dialogs.extend(new_dialogs)
            menus.extend(new_menus)
            summaries.append(summary)
        except (ResourceError, UnicodeDecodeError, struct.error, OSError, json.JSONDecodeError) as exc:
            raw = source.read_bytes()
            summaries.append({"name": source.name, "source_path": str(source), "source_kind": kind,
                              "source_size": len(raw), "source_sha256": digest(raw),
                              "today_primary": is_today(source.name), "status": "error", "error": str(exc)})
    aggregate = {}
    for name, records in (("strings", strings), ("dialogs", dialogs), ("menus", menus)):
        encoded = b"".join((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8") for r in records)
        aggregate[name] = write_output(output, Path(name + ".jsonl"), encoded)
        selected = [r for r in records if r["today_primary"]]
        encoded = b"".join((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8") for r in selected)
        aggregate["today_" + name] = write_output(output, Path("today_" + name + ".jsonl"), encoded)
    manifest = {
        "format": "Static PE resource extraction", "binary_code_executed": False,
        "source_directories": [str(pe_dir), str(imgfs_dir)], "today_only": today_only,
        "rom_source_sha256": imgfs_manifest.get("sha256"),
        "imgfs_manifest_path": str(imgfs_manifest_path),
        "source_count": len(summaries), "status_counts": dict(Counter(s["status"] for s in summaries)),
        "resource_count": sum(s.get("resource_count", 0) for s in summaries),
        "nonempty_string_count": len(strings), "dialog_count": len(dialogs), "menu_count": len(menus),
        "asset_count": dict(sum((Counter(s.get("asset_count", {})) for s in summaries), Counter())),
        "primary_exact_coverage": [
            {"requested": name, "matches": [s["source_path"] for s in summaries if s["name"].lower() == name.lower()],
             "korean_mui_matches": [s["source_path"] for s in summaries if s["name"].lower() == name.lower() + ".0412.mui"]}
            for name in PRIMARY
        ],
        "primary_family_coverage": {family: [s["source_path"] for s in summaries if s["name"].lower().startswith(family)] for family in PRIMARY_FAMILIES},
        "today_sources": [s["source_path"] for s in summaries if s["today_primary"]],
        "aggregate_files": aggregate, "sources": summaries,
        "notes": [
            "All resource payloads and directory bytes are preserved unchanged with source offsets and SHA-256.",
            "Reassembled PE inputs are analysis containers; original IMGFS source section paths are retained where available.",
            "RT_STRING uses exact length-prefixed UTF-16LE decoding; empty strings remain in each parsed block.",
            "Dialog coordinates remain template units. No pixel dimensions, layout behavior or rendering is invented.",
            "BMP adds only a BITMAPFILEHEADER to the original DIB. ICO combines original group metadata and RT_ICON bytes.",
            "CE 2-bpp DIBs retain their native BMP bytes and also receive exact RGB PNG conversion for desktop display; stale biSizeImage values are explicit.",
            "Unrecognized formats retain raw bytes and parse_error. No image generation, binary execution or source modification occurs.",
        ],
    }
    write_output(output, Path("manifest.json"), json_bytes(manifest))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pe-dir", type=Path, default=Path("analysis/pe/modules"))
    parser.add_argument("--imgfs-dir", type=Path, default=Path("analysis/imgfs/files"))
    parser.add_argument("--output", type=Path, default=Path("analysis/resources"))
    parser.add_argument("--today-only", action="store_true")
    parser.add_argument("--update", action="store_true", help="refresh files from a matching previous extraction; never delete files")
    args = parser.parse_args()
    try:
        manifest = extract(args.pe_dir, args.imgfs_dir, args.output, args.today_only, args.update)
    except (OSError, ResourceError, json.JSONDecodeError) as exc:
        parser.exit(1, f"extract_resources: {exc}\n")
    print(json.dumps({k: manifest[k] for k in ("source_count", "status_counts", "resource_count", "nonempty_string_count", "dialog_count", "menu_count", "asset_count")}))


if __name__ == "__main__":
    main()
