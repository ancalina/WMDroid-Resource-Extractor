#!/usr/bin/env python3
"""Build analysis-only PE32 containers from extracted IMGFS ROM modules.

Stored section bytes are copied exactly. Synthetic PE headers do not restore
the original executable, CE runtime aliases, or ROM relocation encoding.
"""

import argparse
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path


E32_PREFIX = struct.Struct('<HHIIHH5I')
O32 = struct.Struct('<6I')
FILE_ALIGNMENT = 0x200
SECTION_ALIGNMENT = 0x1000
DIRECTORY_NAMES = (
    'export', 'import', 'resource', 'exception', 'security', 'base_relocation',
    'debug', 'architecture', 'global_pointer',
)


def align(value, alignment):
    return (value + alignment - 1) & ~(alignment - 1)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def inferred_section_name(section, directories, used):
    rva = section['rva']
    extent = max(section['virtual_size'], section['stored_size'])
    contained = {
        d['index'] for d in directories if d['size']
        and rva <= d['rva'] and d['rva'] + d['size'] <= rva + extent
    }
    flags = section['characteristics']
    if flags & 0x20:
        name = '.text'
    elif 2 in contained:
        name = '.rsrc'
    elif 3 in contained:
        name = '.pdata'
    elif 5 in contained:
        name = '.reloc'
    elif flags & 0x80000000:
        name = '.data'
    else:
        name = '.rdata'
    if name in used:
        name = f'.s{section["index"]:03d}'
    used.add(name)
    return name


def restore(source, output):
    raw = (source / 'imageinfo.bin').read_bytes()
    if len(raw) < 112:
        raise ValueError('metadata is shorter than e32_rom')
    values = E32_PREFIX.unpack_from(raw)
    fields = dict(zip((
        'section_count', 'image_flags', 'entry_rva', 'image_base',
        'subsystem_major', 'subsystem_minor', 'stack_reserve',
        'original_virtual_size', 'section14_rva', 'section14_size', 'timestamp',
    ), values))
    count = fields['section_count']
    if not 0 < count <= 96 or len(raw) != 112 + count * O32.size:
        raise ValueError('metadata size does not match section count')
    fields['subsystem'] = struct.unpack_from('<H', raw, 108)[0]
    directories = [
        {'index': index, 'name': name, 'rva': rva, 'size': size}
        for index, name in enumerate(DIRECTORY_NAMES)
        for rva, size in [struct.unpack_from('<II', raw, 36 + index * 8)]
    ]
    directories.append({
        'index': 14, 'name': 'clr_runtime',
        'rva': fields['section14_rva'], 'size': fields['section14_size'],
    })
    sections = []
    payloads = []
    warnings = [
        'Analysis-only synthetic PE container; the original runnable executable '
        'has not been reconstructed'
    ]
    used_names = set()
    for index in range(count):
        values = O32.unpack_from(raw, 112 + index * O32.size)
        section = dict(zip((
            'virtual_size', 'rva', 'stored_size', 'rom_data_address',
            'runtime_address', 'characteristics',
        ), values))
        section['index'] = index
        section['source_path'] = str(source / f'S{index:03d}.bin')
        payload = Path(section['source_path']).read_bytes()
        if len(payload) != section['stored_size']:
            raise ValueError(f'S{index:03d} size differs from o32 stored_size')
        if section['rva'] % SECTION_ALIGNMENT:
            raise ValueError(f'S{index:03d} RVA is not page aligned')
        section['nominal_address'] = fields['image_base'] + section['rva']
        section['runtime_alias_required'] = bool(
            section['runtime_address']
            and section['runtime_address'] != section['nominal_address']
        )
        section['runtime_address_missing'] = section['runtime_address'] == 0
        if section['runtime_alias_required']:
            warnings.append(
                f'S{index:03d}: nominal VA {section["nominal_address"]:#x} '
                f'differs from ROM runtime VA {section["runtime_address"]:#x}; '
                'runtime alias is required for absolute-reference analysis'
            )
        elif section['runtime_address_missing']:
            warnings.append(f'S{index:03d}: ROM runtime VA is zero (no runtime alias specified)')
        section['executable'] = bool(section['characteristics'] & 0x20000000)
        section['name'] = inferred_section_name(section, directories, used_names)
        section['sha256'] = digest(payload)
        sections.append(section)
        payloads.append(payload)
    for index, a in enumerate(sections):
        for b in sections[index + 1:]:
            if max(a['rva'], b['rva']) < min(
                a['rva'] + max(a['virtual_size'], a['stored_size']),
                b['rva'] + max(b['virtual_size'], b['stored_size']),
            ):
                raise ValueError('overlapping ROM section RVAs')
    if directories[5]['size']:
        warnings.append(
            'Base relocation directory and section bytes retain Windows CE ROM '
            'encoding in the source manifest; synthetic PE directory 5 is zero '
            'so standard parsers do not treat those bytes as PE relocations'
        )
    if directories[6]['size']:
        warnings.append(
            'Debug directory section bytes retain original PointerToRawData '
            'values; synthetic PE file offsets differ'
        )
    headers_size = align(0x80 + 4 + 20 + 224 + 40 * count, FILE_ALIGNMENT)
    file_size = headers_size
    for section in sections:
        section['raw_offset'] = file_size
        section['raw_size'] = align(section['stored_size'], FILE_ALIGNMENT)
        file_size += section['raw_size']
    image_size = max(fields['original_virtual_size'], align(max(
        s['rva'] + max(s['virtual_size'], s['stored_size']) for s in sections
    ), SECTION_ALIGNMENT))
    image_size = align(image_size, SECTION_ALIGNMENT)
    if image_size != fields['original_virtual_size']:
        warnings.append(
            f'Synthetic SizeOfImage {image_size:#x} includes all stored sections; '
            f'ROM e32_vsize was {fields["original_virtual_size"]:#x}'
        )
    data = bytearray(file_size)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', data, 0x84,
                     0x1c2, count, fields['timestamp'], 0, 0, 224, fields['image_flags'])
    opt = 0x98
    struct.pack_into('<HBB', data, opt, 0x10b, 0, 0)
    code_size = sum(s['raw_size'] for s in sections if s['characteristics'] & 0x20)
    initialized_size = sum(s['raw_size'] for s in sections if s['characteristics'] & 0x40)
    uninitialized_size = sum(
        max(0, s['virtual_size'] - s['stored_size'])
        for s in sections if s['characteristics'] & 0x80
    )
    code_rvas = [s['rva'] for s in sections if s['characteristics'] & 0x20]
    data_rvas = [s['rva'] for s in sections if not s['characteristics'] & 0x20]
    struct.pack_into('<6I', data, opt + 4, code_size, initialized_size,
                     uninitialized_size, fields['entry_rva'],
                     min(code_rvas, default=0), min(data_rvas, default=0))
    struct.pack_into('<3I', data, opt + 28, fields['image_base'],
                     SECTION_ALIGNMENT, FILE_ALIGNMENT)
    # Missing original PE-only fields remain zero; only ROM-known values copied.
    struct.pack_into('<HH', data, opt + 48,
                     fields['subsystem_major'], fields['subsystem_minor'])
    struct.pack_into('<II', data, opt + 56, image_size, headers_size)
    struct.pack_into('<H', data, opt + 68, fields['subsystem'])
    struct.pack_into('<6I', data, opt + 72, fields['stack_reserve'],
                     min(fields['stack_reserve'], 0x1000), 0, 0, 0, 16)
    for directory in directories:
        if directory['index'] == 5:
            continue
        struct.pack_into('<II', data, opt + 96 + directory['index'] * 8,
                         directory['rva'], directory['size'])
    for section, payload in zip(sections, payloads):
        off = opt + 224 + section['index'] * 40
        name = section['name'].encode('ascii')
        struct.pack_into('<8s8I', data, off, name, section['virtual_size'],
                         section['rva'], section['raw_size'], section['raw_offset'],
                         0, 0, 0, section['characteristics'])
        data[section['raw_offset']:section['raw_offset'] + len(payload)] = payload
        if data[section['raw_offset']:section['raw_offset'] + len(payload)] != payload:
            raise ValueError('section bytes changed during reconstruction')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    result = {
        'name': source.name, 'path': str(output), 'source_metadata_path': str(source / 'imageinfo.bin'),
        **fields, 'sections': sections, 'directories': directories, 'warnings': warnings,
        'synthetic_pe_disabled_directory_indices': [5],
        'synthetic_image_size': image_size, 'synthetic_headers_size': headers_size,
        'section_names_inferred': True, 'section_bytes_preserved': True,
        'analysis_only': True, 'sha256': digest(data), 'size': len(data),
    }
    output.with_name(output.name + '.json').write_bytes(json_bytes(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, nargs='?', default=Path('analysis/imgfs/modules'))
    parser.add_argument('--output', type=Path, default=Path('analysis/pe'))
    args = parser.parse_args()
    manifest = {
        'source': str(args.source), 'output': str(args.output), 'format': 'ARM-Thumb PE32 analysis containers',
        'caveats': [
            'Synthetic DOS/COFF/optional/section headers; original section names and PE-only values are unavailable.',
            'All source section bytes preserved, with only trailing alignment padding added.',
            'Original nominal section RVAs preserved; nonzero runtime VA mismatches are explicit in each section.',
            'ROM import thunks, debug file offsets and CE relocation encoding are retained; loader validity is not claimed.',
            'Synthetic PE directory 5 is zero; its original ROM RVA/size and all relocation section bytes remain preserved.',
            'ROM timestamps are opaque original values, not interpreted as reliable build dates.',
        ],
        'modules': [], 'excluded': [], 'errors': [],
    }
    for source in sorted(args.source.iterdir(), key=lambda p: p.name.casefold()):
        if not source.is_dir():
            continue
        if source.name in ('.ROM', '.VM'):
            manifest['excluded'].append({'name': source.name, 'reason': 'ROM bookkeeping record, not an e32 module'})
            continue
        try:
            manifest['modules'].append(restore(source, args.output / 'modules' / source.name))
        except (OSError, ValueError, struct.error) as exc:
            manifest['errors'].append({'name': source.name, 'error': str(exc)})
    stats = Counter()
    for module in manifest['modules']:
        for section in module['sections']:
            stats['sections'] += 1
            stats['runtime_alias_required'] += section['runtime_alias_required']
            stats['runtime_address_zero'] += section['runtime_address_missing']
            stats['executable_sections'] += section['executable']
            stats['executable_runtime_mismatches'] += bool(section['executable'] and (
                section['runtime_address'] != section['nominal_address']
            ))
    manifest['verification'] = {
        'section_stream_sizes_match_o32': True,
        'all_output_section_payloads_equal_source': True,
        'overlapping_section_rvas': 0,
        **dict(stats),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'manifest.json').write_bytes(json_bytes(manifest))
    print(json.dumps({'modules': len(manifest['modules']), 'excluded': len(manifest['excluded']),
                      'errors': len(manifest['errors']), **dict(stats)}))
    if manifest['errors']:
        print(json.dumps(manifest['errors'][:10], ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
