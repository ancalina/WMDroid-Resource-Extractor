#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Own-data and official format-vector checks; no vendor asset or binary execution."""
import json
import struct
import subprocess
import sys
from PIL import Image
from pathlib import Path
import tempfile
import zipfile
from extract_pack import sha, verify_file, write_pack, icon, png, bitmap_image, decode_icon_frame, image_tile, gradient_records, sdk_msi_pack, lunar_calendar, select_rom_profile
from extract_imgfs import xpress
from pack_layout import physical_paths, canonical_resources


def rejected(operation, exception=ValueError):
    try:
        operation()
    except exception:
        return
    raise AssertionError('Expected rejection')


# MS-XCA section 3.1 format vectors, not extracted ROM/resource bytes.
# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-xca/72da4f8d-2ba3-437d-b772-2e4173713a0b
assert xpress(bytes.fromhex('3f000000') + b'abcdefghijklmnopqrstuvwxyz', 26) == b'abcdefghijklmnopqrstuvwxyz'
assert xpress(bytes.fromhex('ffffff1f61626317000fff2601'), 300) == b'abc' * 100

# Authored boundaries exercise flag refill, overlapping copies and both nibbles.
assert xpress(bytes(4) + b'A' * 32 + bytes(4) + b'B', 33) == b'A' * 32 + b'B'
assert xpress(struct.pack('<I', 0x40000000) + b'A' + struct.pack('<H', 2), 6) == b'A' * 6
assert xpress(struct.pack('<I', 0x60000000) + b'A' + bytes.fromhex('0700100700'), 22) == b'A' * 22

# Authored lengths cover byte, uint16 and uint32 extension paths.
prefix = struct.pack('<I', 0x40000000) + b'A' + struct.pack('<H', 7)
for extension in (bytes.fromhex('0f00'), bytes.fromhex('0fff1600'), bytes.fromhex('0fff000016000000')):
    assert xpress(prefix + extension, 26) == b'A' * 26
for extension in (b'', bytes.fromhex('0f'), bytes.fromhex('0fff'), bytes.fromhex('0fff16'),
                  bytes.fromhex('0fff0000'), bytes.fromhex('0fff0000160000'), bytes.fromhex('0fff1500')):
    rejected(lambda extension=extension: xpress(prefix + extension, 26))
rejected(lambda: xpress(bytes(3), 1))
rejected(lambda: xpress(bytes(4), 1))
rejected(lambda: xpress(struct.pack('<I', 0x80000000) + bytes(1), 3))
rejected(lambda: xpress(struct.pack('<I', 0x80000000) + bytes(2), 3))
rejected(lambda: xpress(prefix + bytes.fromhex('0f00'), 25))


# Same dimensions at two bit depths: the 32-bit authored blue image must win.
red, blue = png(Image.new('RGBA', (16, 16), 'red')), png(Image.new('RGBA', (16, 16), 'blue'))
group = struct.pack('<3H', 0, 1, 2)
for bits, number, data in [(8, 1, red), (32, 2, blue)]:
    group += struct.pack('<4BHHIH', 16, 16, 0, 0, 1, bits, len(data), number)
resources = [{'type': 14, 'id': 10, 'language': 0, 'payload': group}]
for number, data in [(1, red), (2, blue)]:
    resources.append({'type': 3, 'id': number, 'language': 0, 'payload': data,
                      'sha256': sha(data), 'size': len(data)})
assert icon(resources, 10, 0, (16, 16)).getpixel((8, 8)) == (0, 0, 255, 255)
rejected(lambda: icon(resources, 10, 0, (32, 32)))

# Authored two-color DIBs exercise both RLE paths, literal padding, and bounds.
def dib(bits, compression, encoded):
    return struct.pack('<IiiHHIIiiII', 40, 4, 1, 1, bits, compression, len(encoded), 0, 0, 2, 0) + bytes.fromhex('00000000ffffff00') + encoded
assert bitmap_image(dib(4, 2, bytes([4, 0x10, 0, 1]))).tobytes() == bytes.fromhex('ffffffff000000ff') * 2
assert bitmap_image(dib(4, 2, bytes([0, 4, 0x10, 0x10, 0, 1]))).tobytes() == bytes.fromhex('ffffffff000000ff') * 2
assert bitmap_image(dib(8, 1, bytes([4, 1, 0, 1]))).getpixel((3,0)) == (255,255,255,255)
rejected(lambda: bitmap_image(dib(4, 2, bytes([0, 2, 5, 0, 0, 1]))))

# WinCE's reversed RGB565 masks and AND transparency must survive decoding.
raw = struct.pack('<IiiHHIIiiII',40,2,2,1,16,3,4,0,0,0,0) + struct.pack('<3I',31,2016,63488) + struct.pack('<2H',31,63488) + bytes([64,0,0,0])
entry = (2,1,0,0,1,16,len(raw),22)
frame = decode_icon_frame(raw,entry)
assert frame.getpixel((0,0)) == (255,0,0,255) and frame.getpixel((1,0)) == (0,0,255,0)
rejected(lambda: decode_icon_frame(raw[:-1],entry))
strip = Image.new('RGBA',(2,1),(192,192,192,255))
row = {'asset':'own/tile.png','crop':[0,0,1,1],'color_key':[192,192,192],'size':[1,1], 'rgba_sha256':sha(bytes([192,192,192,0]))}
assert image_tile(strip,row).getpixel((0,0)) == (192,192,192,0)
rejected(lambda: image_tile(strip,dict(row,crop=[0,0,3,1])))

# Authored table checks null/UTF16 pointers, index uniqueness and bounded strings.
table = b''.join(struct.pack('<4I',0x123456,i,0,1) for i in range(73))
assert len(gradient_records(table,0,73,0x1000)) == 73
named = bytearray(table + 'Own'.encode('utf-16le') + b'\0\0')
struct.pack_into('<I',named,8,0x1000+len(table))
assert gradient_records(named,0,73,0x1000)[0]['override'] == 'Own'
rejected(lambda: gradient_records(table[:-1],0,73,0x1000))
bad = bytearray(table);struct.pack_into('<I',bad,20,0)
rejected(lambda: gradient_records(bad,0,73,0x1000))
struct.pack_into('<I',bad,20,1);struct.pack_into('<I',bad,8,1)
rejected(lambda: gradient_records(bad,0,73,0x1000))
rejected(lambda: gradient_records(named[:-2],0,73,0x1000))

# Authored lunar words and English captions; no original table or text payload.
def own_string_group(fields):
    return b''.join(struct.pack('<H', len(text.encode('utf-16le')) // 2)
                    + text.encode('utf-16le') for text in fields)


lunar_words = [((i % 2) << 16) | ((i * 17 % 4096) << 4) | (i % 13)
               for i in range(150)]
lunar_raw = struct.pack('<150I', *lunar_words)
lunar_section = b'own prefix' + lunar_raw + b'own suffix'
lunar_offset = len(b'own prefix')
lunar_strings = {**{2210 + day - 1: 'Day %02d' % day for day in range(2, 31)},
                 **{2240 + month: 'Month %02d' % month for month in range(1, 13)},
                 **{2260 + month: 'Leap %02d' % month for month in range(1, 13)}}
lunar_resources = []
for group in range(139, 144):
    fields = [lunar_strings.get((group - 1) * 16 + i, '') for i in range(16)]
    # Original captions have a trailing terminator inside the length field.
    fields = [text + '\0' if text else '' for text in fields]
    lunar_resources.append({'type': 6, 'id': group, 'language': 1042,
                            'payload': own_string_group(fields)})
lunar_row = {'section_sha256': sha(lunar_section), 'offset': lunar_offset,
             'count': 150, 'table_sha256': sha(lunar_raw),
             'captions': [{'id': rec['id'], 'language': rec['language'],
                           'sha256': sha(rec['payload'])} for rec in lunar_resources]}
lunar_expected = {'version': 1, 'epoch': '1900-01-31', 'first_year': 1900,
                  'years': lunar_words,
                  'captions': {'days': ['Day %02d' % day for day in range(2, 31)],
                               'months': ['Month %02d' % month for month in range(1, 13)],
                               'leap_months': ['Leap %02d' % month for month in range(1, 13)]}}
assert lunar_calendar(lunar_section, lunar_resources, lunar_row) == lunar_expected
assert lunar_calendar(lunar_section, list(reversed(lunar_resources)), lunar_row) == lunar_expected
rejected(lambda: lunar_calendar(lunar_section + b'changed', lunar_resources, lunar_row))
for changes in ({'section_sha256': '0' * 64}, {'table_sha256': '0' * 64},
                {'offset': -1}, {'offset': len(lunar_section)}, {'count': 149}, {'count': 151}):
    rejected(lambda changes=changes: lunar_calendar(lunar_section, lunar_resources,
                                                    dict(lunar_row, **changes)))
short = lunar_section[:lunar_offset + len(lunar_raw) - 1]
rejected(lambda: lunar_calendar(short, lunar_resources, dict(lunar_row, section_sha256=sha(short))))
for word in (0x20000, 13, 14, 15):
    raw = struct.pack('<I', word) + lunar_raw[4:]
    section = b'own prefix' + raw + b'own suffix'
    rejected(lambda section=section, raw=raw: lunar_calendar(section, lunar_resources,
        dict(lunar_row, section_sha256=sha(section), table_sha256=sha(raw))))
rejected(lambda: lunar_calendar(lunar_section, lunar_resources[:-1], lunar_row))
rejected(lambda: lunar_calendar(lunar_section, lunar_resources,
    dict(lunar_row, captions=lunar_row['captions'][:-1])))
for change in ({'type': 10}, {'language': 1033}):
    records = [dict(rec) for rec in lunar_resources]
    records[0].update(change)
    rejected(lambda records=records: lunar_calendar(lunar_section, records, lunar_row))
rejected(lambda: lunar_calendar(lunar_section, lunar_resources + [dict(lunar_resources[0])], lunar_row))
rejected(lambda: lunar_calendar(lunar_section, lunar_resources,
    dict(lunar_row, captions=lunar_row['captions'] + [dict(lunar_row['captions'][0])])))
bad_recipe = json.loads(json.dumps(lunar_row))
bad_recipe['captions'][0]['sha256'] = '0' * 64
rejected(lambda: lunar_calendar(lunar_section, lunar_resources, bad_recipe))


def reject_own_caption(payload):
    records = [dict(rec) for rec in lunar_resources]
    records[0]['payload'] = payload
    row = json.loads(json.dumps(lunar_row))
    row['captions'][0]['sha256'] = sha(payload)
    rejected(lambda: lunar_calendar(lunar_section, records, row))


first_fields = [lunar_strings.get(2208 + i, '') for i in range(16)]
# ID2211 is requested day2, while ID2208 is unrelated and empty is valid there.
for text in ('', 'Bad\0Interior', '\ud800', 'X' * 65):
    fields = list(first_fields)
    fields[3] = text
    if text == '\ud800':
        payload = b''.join(struct.pack('<H', len(v.encode('utf-16le', 'surrogatepass')) // 2)
                           + v.encode('utf-16le', 'surrogatepass') for v in fields)
    else:
        payload = own_string_group(fields)
    reject_own_caption(payload)
reject_own_caption(lunar_resources[0]['payload'][:-1])
reject_own_caption(own_string_group(first_fields[:-1]))
reject_own_caption(lunar_resources[0]['payload'] + b'\0\0')

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / 'own.txt'
    source.write_bytes(b'own fixture')
    verify_file(source, sha(b'own fixture'))
    rejected(lambda: verify_file(source, '0' * 64))
    extractor = Path(__file__).with_name('extract_pack.py')
    help_result = subprocess.run([sys.executable, str(extractor), '--help'],
                                 capture_output=True, text=True, check=True)
    for option in ('--vista-iso', '--rom', '--sdk-msi'):
        assert option in help_result.stdout
    output = root / 'rejected-output.zip'
    for option in ('--samsung-rom', '--malgun-font-dir'):
        assert option not in help_result.stdout
        result = subprocess.run([sys.executable, str(extractor), '--output', str(output),
                                 option, str(root / 'absent-input')],
                                capture_output=True, text=True)
        assert result.returncode == 2 and 'unrecognized arguments: ' + option in result.stderr
        assert not output.exists()
    authored_roms = {'rom': {'sha256':sha(b'own fixture')},
                     'sdk_msi': {'rom': {'sha256':sha(b'own USA fixture')}}}
    assert select_rom_profile(source, authored_roms) is authored_roms['rom']
    usa = root / 'renamed.bin'
    usa.write_bytes(b'own USA fixture')
    assert select_rom_profile(usa, authored_roms) is authored_roms['sdk_msi']['rom']
    usa.write_bytes(b'unsupported fixture')
    rejected(lambda: select_rom_profile(usa, authored_roms))
    rejected(lambda: sdk_msi_pack(source, {'sha256':'0'*64}, root))
    assert not (root/'sdkfiles.cab').exists()
    profile = json.loads((Path(__file__).parent / 'profile.json').read_text())
    assert profile['rom']['lunar_calendar']['asset'] == 'calendar/lunar/korean.json'
    assert 'lunar_calendar' not in profile['sdk_msi']['rom']
    # All four templates must be extractable from either supported ROM recipe.
    templates = {name + '.pwi' for name in ('memo', 'meeting', 'phone', 'todo')}
    for recipe in (profile['rom'], profile['sdk_msi']['rom']):
        rows = [row for row in recipe['files'] if row['asset'].startswith('pwi/')]
        assert {row['source'] for row in rows} == templates and len(rows) == 4
        assert all(row['asset'] == 'pwi/' + row['source'] for row in rows)
        assert len({row['asset'] for row in recipe['files']}) == len(recipe['files'])
    own_pwi = {'assets/pwi/' + name: b'own template' for name in templates}
    assert canonical_resources(own_pwi, profile) == own_pwi
    write_pack(own_pwi, root / 'pwi.zip', profile)
    with zipfile.ZipFile(root / 'pwi.zip') as archive:
        mapping = json.loads(archive.read('pack.json'))['assets']
        assert set(mapping) == set(own_pwi)
        for role, path in mapping.items():
            assert path == role[len('assets/'):]
            assert archive.read(path) == own_pwi[role]
    lunar_asset = 'assets/calendar/lunar/korean.json'
    lunar_bytes = json.dumps(lunar_expected, ensure_ascii=False, separators=(',', ':')).encode()
    lunar_files = {lunar_asset: lunar_bytes}
    assert physical_paths(lunar_files, profile) == {lunar_asset: 'calendar/lunar/korean.json'}
    assert canonical_resources(lunar_files, profile) == lunar_files
    write_pack(lunar_files, root / 'lunar-one.zip', profile)
    write_pack(lunar_files, root / 'lunar-two.zip', profile)
    assert (root / 'lunar-one.zip').read_bytes() == (root / 'lunar-two.zip').read_bytes()
    with zipfile.ZipFile(root / 'lunar-one.zip') as archive:
        assert set(archive.namelist()) == {'pack.json', 'calendar/lunar/korean.json'}
        assert json.loads(archive.read('pack.json')) == {'version': 2, 'assets': {
            lunar_asset: 'calendar/lunar/korean.json'}}
        assert archive.read('calendar/lunar/korean.json') == lunar_bytes
    year_files = {'assets/rom-calendar/year_grid_12000.png': b'own compact atlas',
                  'assets/rom-calendar/year_grid_12001.png': b'own tall atlas'}
    assert canonical_resources(year_files, profile) == {
        'assets/calendar/year-grid-compact.png': b'own compact atlas',
        'assets/calendar/year-grid-tall.png': b'own tall atlas'}
    today_files = {'assets/drawables/wm_wireless.png': b'own wireless reference',
                   'assets/drawables/wm_lock.png': b'own lock reference'}
    today_canonical = {'assets/library/today/wireless.png': b'own wireless reference',
                       'assets/library/today/lock.png': b'own lock reference'}
    assert canonical_resources(today_files, profile) == today_canonical
    write_pack(today_files, root / 'today-references.zip', profile)
    with zipfile.ZipFile(root / 'today-references.zip') as archive:
        mapping = json.loads(archive.read('pack.json'))['assets']
        assert mapping == {key: key[len('assets/'):] for key in today_canonical}
        assert not {'assets/launcher/icons/wireless.png',
                    'assets/launcher/icons/lock.png'} & set(mapping)
        for key, data in today_canonical.items():
            assert archive.read(mapping[key]) == data
    files = {'assets/fonts/tahoma.ttf': b'not a vendor font', 'assets/drawables/wm_clock.png': b'own fixture'}
    first, second = root / 'one.zip', root / 'nested/two.zip'
    write_pack(files, first)
    write_pack(dict(reversed(list(files.items()))), second)
    assert first.read_bytes() == second.read_bytes()
    before = first.read_bytes()
    rejected(lambda: write_pack(files, first), FileExistsError)
    assert first.read_bytes() == before
    assert not list(root.glob('tmp*.zip'))
    with zipfile.ZipFile(first) as archive:
        assert {entry.date_time for entry in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert not archive.comment and all(not entry.comment and not entry.extra for entry in archive.infolist())
        assert set(json.loads(archive.read('pack.json'))) == {'version', 'assets'}
        mapping = json.loads(archive.read('pack.json'))['assets']
        assert set(mapping) == set(canonical_resources(files, profile))
        assert mapping['assets/fonts/tahoma/regular.ttf'] == 'fonts/tahoma/regular.ttf'
        assert mapping['assets/launcher/icons/clock.png'] == 'launcher/icons/clock.png'
        for key, path in mapping.items():
            assert archive.read(path) == canonical_resources(files, profile)[key]
    for key in ['assets/../escape', 'assets//empty', 'assets/./dot', '/assets/demo',
                'assets/back\\slash', 'assets/a\x00b']:
        rejected(lambda: write_pack({key: b'x'}, root / 'bad.zip'))
    rejected(lambda: write_pack({'assets/unmapped.png': b'x'}, root / 'unmapped.zip'))
    roles = {'rom': {'rasters': [{'asset': 'drawables/wm_cpl_sound.png'},
                                {'asset': 'drawables/wm_settings_sound.png'}]}}
    shared = {'assets/drawables/wm_cpl_sound.png': b'own image',
              'assets/drawables/wm_settings_sound.png': b'own image'}
    write_pack(shared, root / 'shared.zip', roles)
    with zipfile.ZipFile(root / 'shared.zip') as archive:
        assert archive.namelist() == ['pack.json', 'settings/sound.png']
    shared['assets/drawables/wm_settings_sound.png'] = b'different own image'
    rejected(lambda: write_pack(shared, root / 'collision.zip', roles))
    profile = json.loads((Path(__file__).parent / 'profile.json').read_text())
    assert physical_paths({'assets/drawables/wm_battery_charge.png': b'edited by user'}, profile) == {
        'assets/drawables/wm_battery_charge.png': 'status/battery/charging.png'}
    assert physical_paths({'assets/rom-status/connection_progress_4688.png': b'own frame'}, profile) == {
        'assets/rom-status/connection_progress_4688.png': 'status/connection/connecting-12.png'}
    # Named roles change; unclassified numeric/native identities stay metadata.
    library = profile['vista']['catalog']
    target_rows = [row for row in library['assets'] if row['provider'] == 'netshell.dll'
                   and str(row['resource_id']).startswith(('IDI_CFI_TRAY_WIRED_', 'IDI_CFI_TRAY_WIRELESS_'))]
    assert len(target_rows) == 8
    media_names = {'CHECKBOXES.PNG': 'checkboxes', 'LISTVIEWHEADER.PNG': 'list-view-header',
                   'PROGRESS.PNG': 'progress', 'WMP_PUSHBUTTON.PNG': 'push-button',
                   'WMP_TOOLBARBUTTON.PNG': 'toolbar-button'}
    media_rows = [row for row in library['assets'] if row['provider'] == 'wmploc.DLL'
                  and row['kind'] == 'png' and row['resource_id'] in media_names]
    assert len(media_rows) == 5
    target_rows += media_rows
    target_assets = {row['asset'] for row in target_rows}
    own_library = {'assets/' + row['asset']: b'own ' + row['asset'].encode()
                   for row in library['assets']}
    layout = physical_paths(own_library, profile)
    assert len({layout['assets/' + asset] for asset in target_assets}) == 13
    for row in library['assets']:
        asset = row['asset']
        if row in media_rows:
            assert layout['assets/' + asset] == 'library/media-player/' + media_names[row['resource_id']] + '.png'
        elif asset in target_assets:
            link = 'wireless' if '_WIRELESS_' in row['resource_id'] else 'wired'
            state = 'warning' if row['resource_id'].endswith('_WARNING') else 'acquiring-0' + row['resource_id'][-1]
            assert layout['assets/' + asset] == f'library/status/network/{link}/{state}.png'
        else:
            assert layout['assets/' + asset] == 'library/' + asset[len('vista/'):]
    small_catalog = {'assets': json.loads(json.dumps(target_rows))}
    # A different native alias shares the rewritten path and keeps its own ID.
    alias = dict(small_catalog['assets'][0], id='own_native_alias', resource_id=99999,
                 provider='own.dll')
    small_catalog['assets'].append(alias)
    own_targets = {'assets/' + asset: b'own ' + asset.encode() for asset in target_assets}
    own_targets['assets/vista/catalog.json'] = json.dumps(small_catalog).encode()
    canonical = canonical_resources(own_targets, profile)
    changed_rows = json.loads(canonical['assets/metadata/library.json'])['assets']
    assert len({row['id'] for row in changed_rows}) == 14
    assert len({row['id'] for row in changed_rows[:13]}) == 13
    for old, new in zip(small_catalog['assets'], changed_rows):
        expected_path = layout['assets/' + old['asset']]
        assert new['asset'] == expected_path
        expected_id = expected_path[len('library/'):].removesuffix('.png').replace('/', '-')
        assert new['id'] == (old['id'] if old is alias else expected_id)
        assert {k: v for k, v in new.items() if k not in ('id', 'asset')} == {
            k: v for k, v in old.items() if k not in ('id', 'asset')}
        assert canonical['assets/' + expected_path] == own_targets['assets/' + old['asset']]
    conflicting = json.loads(json.dumps(profile))
    # A raw library path matching a semantic path cannot silently replace bytes.
    collision = 'vista/status/network/wired/warning.png'
    conflicting['vista']['catalog']['assets'].append(dict(target_rows[0], resource_id=99997, asset=collision))
    warning = next(row['asset'] for row in target_rows if row['resource_id'] == 'IDI_CFI_TRAY_WIRED_WARNING')
    rejected(lambda: physical_paths({'assets/' + warning: b'first', 'assets/' + collision: b'second'}, conflicting))
    duplicate_catalog = json.loads(json.dumps(small_catalog))
    duplicate_catalog['assets'][-1]['id'] = changed_rows[0]['id']
    duplicate_files = dict(own_targets, **{'assets/vista/catalog.json': json.dumps(duplicate_catalog).encode()})
    rejected(lambda: canonical_resources(duplicate_files, profile))
    rejected(lambda: write_pack({}, root / 'empty.zip'))
    rejected(lambda: write_pack({f'assets/{i}': b'x' for i in range(4096)}, root / 'too-many.zip'))
    assert xpress(b'\x00\x00\x00\x00ABC', 3) == b'ABC'
    rejected(lambda: xpress(b'\x00\x00\x00\x80\x00\x00', 3))
    assert not (root / 'bad.zip').exists()
print('PASS: input hash, deterministic ZIP, atomic no-overwrite, IDs/count, netshell8/media5 roles/aliases/provenance/bytes/collisions, XPRESS/icon/RLE bounds, gradient pointers/indices, lunar table/caption validation/path/determinism, supported CLI and obsolete option rejection')
