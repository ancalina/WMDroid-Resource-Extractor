#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create a partial WMDroid pack from verified user-owned media; never execute it."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from PIL import Image, ImageChops
from extract_imgfs import Imgfs
from extract_resources import PE, assemble_icon, wrap_bitmap, ce_2bpp_png
from restore_pe import restore
from pack_layout import canonical_resources

ROOT = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify_file(path, expected):
    actual = file_sha(path)
    if actual != expected:
        raise ValueError(f'Unsupported input SHA256: {path.name}: {actual}')


def select_rom_profile(rom, profile):
    actual = file_sha(rom)
    for recipe in (profile['rom'], profile['sdk_msi']['rom']):
        if actual == recipe['sha256']:
            return recipe
    raise ValueError(f'Unsupported input SHA256: {rom.name}: {actual}')


def png(image):
    result = io.BytesIO()
    image = image.copy()
    image.info.clear()
    image.save(result, 'PNG')
    return result.getvalue()


def icon(resources, number, language=None, size=None, selected_index=None):
    groups = [r for r in resources if r['type'] == 14 and r['id'] == number
              and (language is None or r['language'] == language)]
    if len(groups) != 1:
        raise ValueError(f'Expected one icon group: {number}, found {len(groups)}')
    data, _ = assemble_icon(groups[0], resources)
    count = struct.unpack_from('<H', data, 4)[0]
    frames = []
    for index in range(count):
        entry = struct.unpack_from('<4BHHII', data, 6 + index * 16)
        width, height = entry[0] or 256, entry[1] or 256
        if (size is None or (width, height) == tuple(size)) and (selected_index is None or index == selected_index):
            frames.append((width * height, entry[5], index, entry))
    if not frames:
        raise ValueError(f'Native icon size unavailable: {number}: {size}')
    _, _, _, entry = max(frames)
    frame = data[entry[7]:entry[7] + entry[6]]
    return decode_icon_frame(frame, entry)


def decode_icon_frame(raw, entry):
    width, height = entry[0] or 256, entry[1] or 256
    if not raw.startswith(b'\x89PNG'):
        header, = struct.unpack_from('<I', raw)
        dw, dh, planes, bits, compression = struct.unpack_from('<iiHHI', raw, 4)
        if bits == 16 and compression == 3:
            masks = struct.unpack_from('<3I', raw, header)
            if header != 40 or masks not in ((31,2016,63488),(63488,2016,31)) or planes != 1 or dw <= 0 or dh == 0:
                raise ValueError('Unsupported RGB565 icon geometry/masks')
            ph, stride = abs(dh)//2, ((dw*16+31)//32)*4
            start, mask_start = header+12, header+12+stride*ph
            mask_stride = ((dw+31)//32)*4
            if mask_start+mask_stride*ph > len(raw):
                raise ValueError('Truncated RGB565 icon')
            image = Image.new('RGBA',(dw,ph))
            pixels = image.load()
            for y in range(ph):
                sy = ph-1-y if dh > 0 else y
                for x in range(dw):
                    value, = struct.unpack_from('<H',raw,start+sy*stride+x*2)
                    r,b = (value&31),(value>>11)&31
                    if masks[0] == 63488:
                        r,b = b,r
                    g = (value>>5)&63
                    alpha = 0 if raw[mask_start+sy*mask_stride+x//8] & (128>>(x%8)) else 255
                    pixels[x,y] = (r<<3|r>>2,g<<2|g>>4,b<<3|b>>2,alpha)
            return image
        if bits == 32 and compression == 0:
            ph, stride = abs(dh) // 2, ((dw * 32 + 31) // 32) * 4
            image = Image.frombytes('RGBA', (dw, ph), raw[header:header + stride * ph],
                                    'raw', 'BGRA', stride, -1 if dh > 0 else 1)
            if image.getchannel('A').getextrema() == (0, 0):
                mask_stride = ((dw + 31) // 32) * 4
                mask = raw[header + stride * ph:]
                if len(mask) < mask_stride * ph:
                    raise ValueError('Truncated icon AND mask')
                pixels = image.load()
                for y in range(ph):
                    sy = ph - 1 - y if dh > 0 else y
                    for x in range(dw):
                        alpha = 0 if mask[sy * mask_stride + x // 8] & (0x80 >> (x % 8)) else 255
                        pixels[x, y] = pixels[x, y][:3] + (alpha,)
            return image
    single = struct.pack('<3H', 0, 1, 1) + struct.pack('<4BHHII', *entry[:7], 22) + raw
    with Image.open(io.BytesIO(single)) as image:
        return image.convert('RGBA')


def bitmap_image(data):
    bmp, meta = wrap_bitmap(data)
    width, signed_height, bits = meta['width'], meta['height'], meta['bits_per_pixel']
    height = abs(signed_height)
    if meta['windows_ce_2bpp']:
        return Image.open(io.BytesIO(ce_2bpp_png(data, meta))).convert('RGBA')
    if bits == 32 and meta['compression'] == 0:
        stride = width * 4
        image = Image.frombytes('RGBA', (width, height), bmp[meta['pixel_offset']:],
                                'raw', 'BGRA', stride, -1 if signed_height > 0 else 1)
        if image.getchannel('A').getextrema() == (0, 0):
            image.putalpha(255)
        return image
    if meta['compression'] in (1, 2):
        # Decode RLE palette indices directly; Pillow rejects some grayscale RLE8 strips.
        palette_start = meta['dib_header_size']
        palette = [tuple(data[i:i+3][::-1]) + (255,) for i in
                   range(palette_start, palette_start + meta['palette_count'] * 4, 4)]
        image = Image.new('RGBA', (width, height), palette[0])
        pixels, x, y, position = image.load(), 0, 0, meta['pixel_offset'] - 14
        def put(index):
            nonlocal x
            if not (0 <= x < width and 0 <= y < height and index < len(palette)):
                raise ValueError('RLE4 pixels outside bitmap/palette')
            pixels[x, height - 1 - y if signed_height > 0 else y] = palette[index]
            x += 1
        while position + 2 <= len(data):
            count, value = data[position:position+2]
            position += 2
            if count:
                for i in range(count):
                    put(value if bits == 8 else value >> 4 if i % 2 == 0 else value & 15)
            elif value == 0:
                x, y = 0, y + 1
            elif value == 1:
                return image
            elif value == 2:
                if position + 2 > len(data):
                    raise ValueError('Truncated RLE4 delta')
                dx, dy = data[position:position+2]
                x, y, position = x + dx, y + dy, position + 2
                if x > width or y >= height:
                    raise ValueError('RLE4 delta outside bitmap')
            else:
                needed = value if bits == 8 else (value + 1) // 2
                aligned = (needed + 1) & ~1
                if position + aligned > len(data):
                    raise ValueError('Truncated RLE4 literal')
                for i in range(value):
                    byte = data[position + (i if bits == 8 else i // 2)]
                    put(byte if bits == 8 else byte >> 4 if i % 2 == 0 else byte & 15)
                position += aligned
        raise ValueError('RLE4 end marker missing')
    with Image.open(io.BytesIO(bmp)) as image:
        return image.convert('RGBA')


def catalog_pack(resources, catalog):
    result = {}
    def checked_image(row, image):
        if image.size != (row['width'], row['height']) or sha(image.tobytes()) != row['rgba_sha256']:
            raise ValueError('Catalog pixels differ: ' + row['asset'])
        data = png(image)
        key = 'assets/' + row['asset']
        if key in result and result[key] != data:
            raise ValueError('Conflicting pixel alias: ' + key)
        result[key] = data
    for row in catalog['assets']:
        source = resources[row['provider']]
        record, = [r for r in source if (r['type'], r['id'], r['language']) ==
                   (row['resource_type'], row['resource_id'], row['language'])]
        data = record['payload']
        if sha(data) != row['source_hash']:
            raise ValueError('Catalog resource hash differs: ' + row['id'])
        kind = row['kind']
        if kind == 'icon':
            checked_image(row, icon(source, row['resource_id'], row['language'],
                                   selected_index=row['source_frame']['index']))
        elif kind == 'bitmap':
            checked_image(row, bitmap_image(data))
        elif kind in ('animation_avi', 'audio_wav'):
            result['assets/' + row['asset']] = data
        elif kind == 'cursor':
            frames, directory, payloads, offset = row['available_source_frames'], [], [], 6 + len(row['available_source_frames']) * 16
            for frame in frames:
                cursor, = [r for r in source if r['type'] == 1 and r['id'] == frame['icon_resource_id'] and r['language'] == frame['language']]
                native = cursor['payload']
                if sha(native) != frame['cursor_payload_sha256']:
                    raise ValueError('Cursor payload hash differs')
                hx, hy = struct.unpack_from('<HH', native)
                raw = native[4:]
                w, h = frame['width'], frame['height']
                entry = (w % 256, h % 256, 0, 0, 1, frame['bpp'], len(raw), 22)
                image = decode_icon_frame(raw, entry)
                checked_image(frame['png'], image)
                if frame['bpp'] == 1:
                    header, = struct.unpack_from('<I', raw)
                    dw, dh = struct.unpack_from('<ii', raw, 4)
                    stride = ((dw + 31) // 32) * 4
                    xor_raw = raw[header+8:header+8+stride*h]
                    and_raw = raw[header+8+stride*h:header+8+stride*h*2]
                    xor = Image.frombytes('1', (w, h), xor_raw, 'raw', '1', stride, -1 if dh > 0 else 1).convert('RGBA')
                    mask = Image.frombytes('1', (w, h), and_raw, 'raw', '1', stride, -1 if dh > 0 else 1).convert('RGBA')
                    checked_image(frame['xor_mask'], xor)
                    checked_image(frame['and_mask'], mask)
                    invert = ImageChops.multiply(xor.convert('RGB'), mask.convert('RGB')).convert('RGBA')
                    checked_image(frame['inversion_mask'], invert)
                directory.append(struct.pack('<4BHHII', w % 256, h % 256, 0, 0, hx, hy, len(raw), offset))
                payloads.append(raw)
                offset += len(raw)
            original = struct.pack('<3H', 0, 2, len(frames)) + b''.join(directory) + b''.join(payloads)
            if sha(original) != row['original_asset_sha256']:
                raise ValueError('Cursor container differs')
            result['assets/' + row['original_asset']] = original
        else:
            with Image.open(io.BytesIO(data)) as image:
                checked_image(row, image.convert('RGBA'))
                for frame in row.get('frames', []):
                    image.seek(frame['frame_index'])
                    checked_image(frame, image.convert('RGBA'))
            if row.get('original_asset'):
                result['assets/' + row['original_asset']] = data
    # PNG metadata is stripped during encoding; report the actual output hashes/sizes.
    catalog = json.loads(json.dumps(catalog))
    def refresh(value):
        if isinstance(value, dict):
            data = result.get('assets/' + value.get('asset', ''))
            if data is not None:
                for field in ('png_sha256', 'asset_sha256'):
                    if field in value:
                        value[field] = sha(data)
                if 'bytes' in value:
                    value['bytes'] = len(data)
            for child in value.values():
                refresh(child)
        elif isinstance(value, list):
            for child in value:
                refresh(child)
    refresh(catalog)
    result['assets/vista/catalog.json'] = json.dumps(catalog,ensure_ascii=False,separators=(',',':')).encode()
    return result


def vista_pack(iso, profile, work):
    verify_file(iso, profile['sha256'])
    for command in ['7z', 'wimlib-imagex']:
        if not shutil.which(command):
            raise ValueError(f'Required installed command missing: {command}')
    wim = work / 'install.wim'
    with wim.open('wb') as target:
        subprocess.run(['7z', 'x', '-so', str(iso.resolve()), 'sources/install.wim'],
                       stdout=target, check=True)
    modules = work / 'vista'
    modules.mkdir()
    paths = [r['path'] for r in profile['providers'].values()] + [r['path'] for r in profile.get('fonts',[])]
    subprocess.run(['wimlib-imagex', 'extract', str(wim), str(profile['wim_index']),
                    *paths, '--dest-dir=' + str(modules), '--no-acls', '--no-attributes',
                    '--preserve-dir-structure', '--no-globs'], stdout=subprocess.DEVNULL, check=True)
    resources = {}
    for name, recipe in profile['providers'].items():
        source = modules / recipe['path']
        verify_file(source, recipe['sha256'])
        resources[name] = PE(source.read_bytes()).resources()[0]
    result = {}
    for row in profile['folders']['icons']:
        image = icon(resources[row['provider']], row['group_resource_id'], row['language'],
                     (row['width'], row['height']))
        if sha(image.tobytes()) != row['rgba_sha256']:
            raise ValueError(f'Folder pixels differ: {row["id"]}')
        result['assets/' + row['asset']] = png(image)
    # Setup artwork lives directly in the ISO, outside install.wim.
    for index, row in enumerate(profile.get('iso_icons', [])):
        source = work / f'iso-icon-{index}.dll'
        with source.open('wb') as target:
            subprocess.run(['7z', 'x', '-so', str(iso.resolve()), row['path']],
                           stdout=target, check=True)
        verify_file(source, row['sha256'])
        image = icon(PE(source.read_bytes()).resources()[0], row['group_resource_id'],
                     row['language'], row['size'])
        if list(image.size) != row['size'] or sha(image.tobytes()) != row['rgba_sha256']:
            raise ValueError('ISO logo pixels differ')
        result['assets/' + row['asset']] = png(image)
    result.update(catalog_pack(resources, profile['catalog']))
    for row in profile['raster_aliases']:
        with Image.open(io.BytesIO(result['assets/' + row['source']])) as source:
            image = source.convert('RGBA')
        if list(image.size) != row['size'] or sha(image.tobytes()) != row['rgba_sha256']:
            raise ValueError('Vista launcher alias pixels differ')
        result['assets/' + row['asset']] = png(image)
    for row in profile.get('fonts',[]):
        source = modules / row['path']
        verify_file(source,row['sha256'])
        data = source.read_bytes()
        if len(data) != row['bytes'] or sha(data) != row['sha256']:
            raise ValueError('Vista font changed during read or size differs')
        result['assets/'+row['asset']] = data
    result['assets/vista-folders/catalog.json'] = json.dumps(profile['folders'],
                                                            ensure_ascii=False).encode()
    return result


def sdk_msi_pack(msi, profile, work):
    verify_file(msi, profile['sha256'])
    if not shutil.which('7z'):
        raise ValueError('Required installed command missing: 7z')
    cabinet = work / 'sdkfiles.cab'
    with cabinet.open('wb') as target:
        subprocess.run(['7z','x','-so',str(msi.resolve()),profile['cabinet']],
                       stdout=target,check=True)
    verify_file(cabinet, profile['cabinet_sha256'])
    rom = work / 'sdk-rom.bin'
    with rom.open('wb') as target:
        subprocess.run(['7z','x','-so',str(cabinet),profile['rom_entry']],
                       stdout=target,check=True)
    # The regular bounded IMGFS/resource pipeline verifies the inner ROM too.
    return rom_pack(rom, profile['rom'], work)


def rom_pack(rom, profile, work):
    verify_file(rom, profile['sha256'])
    fs = Imgfs(rom.read_bytes(), profile['base'])
    entries = {}
    for offset, kind in fs.entries():
        name = fs.name(offset).casefold()
        if name in entries:
            raise ValueError('Duplicate ROM filename')
        entries[name] = (offset, kind)
    def payload(offset):
        return fs.payload(fs.u32(offset + 24), fs.u32(offset + 44), fs.u32(offset + 48))[0]
    result = {}
    for row in profile['files']:
        offset, kind = entries[row['source'].casefold()]
        if kind != 0xfffff6fe:
            raise ValueError('File recipe points to a module')
        data = payload(offset)
        if sha(data) != row['sha256']:
            raise ValueError('ROM file content differs')
        result['assets/' + row['asset']] = data
    module_names = {'shellres.192.dll'} | {r['module'] for r in profile['rasters'] + profile['byte_resources'] + profile['images']}
    if 'gradient_table' in profile:
        module_names.add(profile['gradient_table']['module'])
    if 'lunar_calendar' in profile:
        module_names.update((profile['lunar_calendar']['module'], profile['lunar_calendar']['caption_module']))
    modules = {name: rom_resources(fs, entries, name, work) for name in sorted(module_names)}
    resources = modules['shellres.192.dll']
    for row in profile['connection_frames']:
        image = icon(resources, row['id'], size=(32, 32))
        if sha(image.tobytes()) != row['rgba_sha256']:
            raise ValueError('Connection frame pixels differ')
        result['assets/' + row['asset']] = png(image)
    for row in profile['rasters']:
        image = icon(modules[row['module']], row['id'], row['language'], row['size'])
        if sha(image.tobytes()) != row['rgba_sha256']:
            raise ValueError('Launcher raster pixels differ: ' + row['asset'])
        result['assets/' + row['asset']] = png(image)
    for row in profile['images']:
        records = modules[row['module']]
        if row['type'] == 14:
            image = icon(records,row['id'],row['language'],row['size'])
        else:
            record, = [r for r in records if (r['type'],r['id'],r['language']) ==
                       (row['type'],row['id'],row['language'])]
            image = bitmap_image(record['payload'])
        image = image_tile(image,row)
        result['assets/' + row['asset']] = png(image)
    for row in profile['byte_resources']:
        record, = [r for r in modules[row['module']] if
                   (r['type'],r['id'],r['language']) == (row['type'],row['id'],row['language'])]
        if sha(record['payload']) != row['sha256']:
            raise ValueError('ROM byte resource differs')
        result['assets/' + row['asset']] = record['payload']
    if 'gradient_table' in profile:
        row = profile['gradient_table']
        section = (work / row['module'] / row['section']).read_bytes()
        if sha(section) != row['section_sha256']:
            raise ValueError('Gradient source section differs')
        records = gradient_records(section, row['offset'], row['count'], row['runtime_base'])
        raw = section[row['offset']:row['offset']+row['count']*16]
        if sha(raw) != row['table_sha256'] or sha(json.dumps(records,sort_keys=True,separators=(',',':')).encode()) != row['records_sha256']:
            raise ValueError('Gradient records differ')
        result['assets/' + row['asset']] = json.dumps({'source':'aygshell.dll native73-color table',
            'source_sha256':row['section_sha256'],'records':records},indent=2).encode()
    if 'lunar_calendar' in profile:
        row = profile['lunar_calendar']
        section = (work / row['module'] / row['section']).read_bytes()
        calendar = lunar_calendar(section, modules[row['caption_module']], row)
        result['assets/' + row['asset']] = json.dumps(calendar, ensure_ascii=False, indent=2).encode('utf-8')
    return result


def lunar_calendar(section, resources, row):
    offset, count = row['offset'], row['count']
    if count != 150 or offset < 0 or offset + count * 4 > len(section):
        raise ValueError('Lunar table geometry invalid')
    raw = section[offset:offset + count * 4]
    if sha(section) != row['section_sha256'] or sha(raw) != row['table_sha256']:
        raise ValueError('Lunar source table differs')
    years = list(struct.unpack('<150I', raw))
    if any(word > 0x1ffff or (word & 15) > 12 for word in years):
        raise ValueError('Lunar year word invalid')
    strings = {}
    for caption in row['captions']:
        matches = [r for r in resources if (r['type'], r['id'], r['language']) ==
                   (6, caption['id'], caption['language'])]
        if len(matches) != 1:
            raise ValueError('Lunar caption group missing or duplicated')
        payload = matches[0]['payload']
        if sha(payload) != caption['sha256']:
            raise ValueError('Lunar caption group differs')
        position = 0
        for index in range(16):
            if position + 2 > len(payload):
                raise ValueError('Lunar caption length truncated')
            length, = struct.unpack_from('<H', payload, position)
            position += 2
            if length > 64 or position + length * 2 > len(payload):
                raise ValueError('Lunar caption text outside bounds')
            try:
                text = payload[position:position + length * 2].decode('utf-16le').rstrip('\0')
            except UnicodeDecodeError as error:
                raise ValueError('Lunar caption UTF16 invalid') from error
            position += length * 2
            identifier = (caption['id'] - 1) * 16 + index
            if '\0' in text or identifier in strings:
                raise ValueError('Lunar caption invalid or duplicated')
            strings[identifier] = text
        if position != len(payload):
            raise ValueError('Lunar caption trailing bytes')
    captions = {}
    for role, first, count in [('days', 2211, 29), ('months', 2241, 12), ('leap_months', 2261, 12)]:
        values = [strings.get(identifier, '') for identifier in range(first, first + count)]
        if any(not text for text in values):
            raise ValueError('Lunar caption missing')
        captions[role] = values
    return {'version': 1, 'epoch': '1900-01-31', 'first_year': 1900,
            'years': years, 'captions': captions}


def gradient_records(section, offset, count, runtime_base):
    if count != 73 or offset < 0 or offset+count*16 > len(section):
        raise ValueError('Gradient table geometry invalid')
    records, seen = [], set()
    for i in range(count):
        hsb,index,pointer,flags = struct.unpack_from('<4I',section,offset+i*16)
        if hsb > 0xffffff or index >= count or index in seen:
            raise ValueError('Gradient color/index invalid')
        seen.add(index)
        name = ''
        if pointer:
            start = pointer-runtime_base
            if start < 0 or start % 2 or start+2 > len(section):
                raise ValueError('Gradient string pointer invalid')
            end = start
            while end+2 <= min(len(section),start+256) and section[end:end+2] != b'\0\0':
                end += 2
            if section[end:end+2] != b'\0\0':
                raise ValueError('Gradient string unterminated')
            name = section[start:end].decode('utf-16le')
        records.append(dict(record=i,hsb=hsb,index=index,override=name,flags=flags))
    return records


def image_tile(image,row):
    if 'crop' in row:
        left,top,right,bottom = row['crop']
        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
            raise ValueError('Crop outside native strip')
        image = image.crop((left,top,right,bottom))
    if 'color_key' in row:
        key,pixels = tuple(row['color_key']),image.load()
        for y in range(image.height):
            for x in range(image.width):
                if pixels[x,y][:3] == key:
                    pixels[x,y] = pixels[x,y][:3]+(0,)
    if list(image.size) != row['size'] or sha(image.tobytes()) != row['rgba_sha256']:
        raise ValueError('Native tile pixels differ: ' + row['asset'])
    return image


def rom_resources(fs, entries, module_name, work):
    offset, kind = entries[module_name.casefold()]
    if kind != 0xfffffefe:
        raise ValueError('Expected ROM module')
    module = work / module_name
    module.mkdir()
    (module / 'imageinfo.bin').write_bytes(fs.payload(fs.u32(offset + 24), fs.u32(offset + 44), fs.u32(offset + 48))[0])
    stream, seen = fs.u32(offset + 8), set()
    while stream:
        if stream in seen or len(seen) >= 64 or fs.u32(stream) != 0xfffff6fd:
            raise ValueError('Invalid module stream chain')
        seen.add(stream)
        length = fs.u32(stream + 12)
        if not 1 <= length <= 4:
            raise ValueError('Invalid stream name length')
        name = fs.read(stream + 16, length * 2).decode('utf-16le')
        if not re.fullmatch(r'S[0-9]{3}', name):
            raise ValueError('Invalid stream name')
        (module / (name + '.bin')).write_bytes(
            fs.payload(fs.u32(stream + 24), fs.u32(stream + 28), fs.u32(stream + 32))[0])
        stream = fs.u32(stream + 8)
    pe = work / (module_name + '.pe')
    restore(module, pe)
    return PE(pe.read_bytes()).resources()[0]


def write_pack(files, output, profile=None):
    if not files or len(files) > 4095 or sum(map(len, files.values())) > 256 * 1024 * 1024:
        raise ValueError('Pack size/file count outside runtime bounds')
    if any(not re.fullmatch(r'assets/[A-Za-z0-9_.\-/]+', key)
           or any(part in ('', '.', '..') for part in key.split('/')) for key in files):
        raise ValueError('Unsafe resource ID')
    if profile is None:
        profile = json.loads((ROOT / 'profile.json').read_text())
    files = canonical_resources(files, profile)
    mapping = {key: key[len('assets/'):] for key in sorted(files)}
    payloads = {mapping[key]: data for key, data in files.items()}
    manifest = json.dumps({'version': 2, 'assets': mapping}, separators=(',', ':')).encode()
    if len(manifest) > 256 * 1024:
        raise ValueError('Manifest outside runtime bounds')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.zip', delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in [('pack.json', manifest), *sorted(payloads.items())]:
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('ZIP verification failed')
        os.link(temporary, output)  # Atomic publication, refuses existing output even in a race.
    finally:
        temporary.unlink()
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vista-iso', type=Path)
    rom_input = parser.add_mutually_exclusive_group()
    rom_input.add_argument('--rom', type=Path)
    rom_input.add_argument('--sdk-msi', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.vista_iso and not args.rom and not args.sdk_msi:
        parser.error('Provide --vista-iso and/or one of --rom / --sdk-msi')
    if args.output.exists():
        parser.error('Existing output preserved; choose a new filename')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    profile = json.loads((ROOT / 'profile.json').read_text())
    files = {}
    with tempfile.TemporaryDirectory(prefix='wmdroid-extract-', dir=args.output.resolve().parent) as directory:
        work = Path(directory)
        if args.vista_iso:
            files.update(vista_pack(args.vista_iso, profile['vista'], work))
        if args.rom:
            files.update(rom_pack(args.rom, select_rom_profile(args.rom, profile), work))
        if args.sdk_msi:
            files.update(sdk_msi_pack(args.sdk_msi, profile['sdk_msi'], work))
        resource_count = write_pack(files, args.output, profile)
    print(json.dumps({'output': str(args.output), 'resources': resource_count,
                      'sha256': sha(args.output.read_bytes()), 'coverage': 'partial',
                      'missing': ['remaining detailed vector replacements']}))


if __name__ == '__main__':
    main()
