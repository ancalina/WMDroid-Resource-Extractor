#!/usr/bin/env python3
"""Extract this ROM's IMGFS as data; never execute ROM contents."""
import argparse
import hashlib
import json
import struct
from pathlib import Path

GUID = bytes.fromhex('f8ac2c9de3d42b4dbd30916ed84f31dc')


def xpress(data, expected):
    """Microsoft XPRESS LZ77 (non-Huffman), bounded by the chunk size."""
    out = bytearray()
    p = 0
    nibble = None
    while len(out) < expected:
        if p + 4 > len(data):
            raise ValueError('truncated XPRESS flags')
        flags = struct.unpack_from('<I', data, p)[0]
        p += 4
        for bit in range(31, -1, -1):
            if len(out) == expected:
                break
            if not (flags >> bit) & 1:
                if p >= len(data):
                    raise ValueError('truncated XPRESS literal')
                out.append(data[p])
                p += 1
                continue
            if p + 2 > len(data):
                raise ValueError('truncated XPRESS token')
            token = struct.unpack_from('<H', data, p)[0]
            p += 2
            distance = (token >> 3) + 1
            length = token & 7
            if length == 7:
                if nibble is None:
                    if p >= len(data):
                        raise ValueError('truncated XPRESS nibble')
                    nibble = p
                    length = data[p] & 15
                    p += 1
                else:
                    length = data[nibble] >> 4
                    nibble = None
                if length == 15:
                    if p >= len(data):
                        raise ValueError('truncated XPRESS length')
                    length = data[p]
                    p += 1
                    if length == 255:
                        if p + 2 > len(data):
                            raise ValueError('truncated XPRESS long length')
                        length = struct.unpack_from('<H', data, p)[0]
                        p += 2
                        if length == 0:
                            if p + 4 > len(data):
                                raise ValueError('truncated XPRESS extended length')
                            length = struct.unpack_from('<I', data, p)[0]
                            p += 4
                        if length < 22:
                            raise ValueError('invalid XPRESS long length')
                        length -= 22
                    length += 15
                length += 7
            length += 3
            if distance > len(out) or len(out) + length > expected:
                raise ValueError(f'invalid XPRESS match {distance=} {length=} output={len(out)} expected={expected}')
            for _ in range(length):
                out.append(out[-distance])
    return bytes(out)


class Imgfs:
    def __init__(self, raw, base=0x2b0000):
        self.raw = raw
        self.base = base
        # Each 64 KiB physical block contains 60 KiB IMGFS and 4 KiB spare.
        self.data = b''.join(raw[p:p + 0xf000] for p in range(base, len(raw), 0x10000))
        if self.data[:16] != GUID or self.data[0x2c:0x30] != b'XPR\0':
            raise ValueError('unexpected IMGFS signature/compression')
        self.header_size = self.u32(0x1c)
        self.first_header = self.u32(0x24)
        self.chunk_size = self.u32(0x28)
        if self.header_size != 52 or self.chunk_size != 4096:
            raise ValueError('unexpected IMGFS geometry')

    def raw_offset(self, offset):
        return self.base + offset + (offset // 0xf000) * 0x1000

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > len(self.data):
            raise ValueError(f'IMGFS range outside image: {offset:#x}+{length:#x}')
        return self.data[offset:offset + length]

    def u32(self, offset):
        return struct.unpack('<I', self.read(offset, 4))[0]

    def name(self, offset):
        n, flags = struct.unpack('<HH', self.read(offset + 12, 4))
        if not 0 < n <= 255:
            raise ValueError('invalid IMGFS filename length')
        if n <= 4:
            name_data = self.read(offset + 16, n * 2)
        else:
            p = self.u32(offset + 20)
            if flags == 2:
                if self.u32(p) != 0xfffffefb:
                    raise ValueError('invalid name header')
                p += 4
            elif flags != 0:
                raise ValueError(f'unknown name flags {flags}')
            name_data = self.read(p, n * 2)
        name = name_data.decode('utf-16le')
        if '/' in name or '\\' in name or name in ('.', '..') or '\0' in name:
            raise ValueError('unsafe output filename')
        return name

    def entries(self):
        seen = set()
        at = self.first_header
        while at:
            if at in seen or len(seen) > 1024:
                raise ValueError('cyclic IMGFS header chain')
            seen.add(at)
            if self.u32(at) != 0x2f5314ce:
                raise ValueError(f'invalid header block at {at:#x}')
            for off in range(at + 8, at + 4096 - 51, 52):
                typ = self.u32(off)
                if typ in (0xfffffefe, 0xfffff6fe):
                    yield off, typ
            at = self.u32(at + 4)

    def payload(self, size, table, table_size):
        if table_size % 8 or size > 128 * 1024 * 1024:
            raise ValueError('invalid allocation table')
        desc = self.read(table, table_size)
        output = bytearray()
        chunks = []
        for pos in range(0, len(desc), 8):
            compressed, original, source = struct.unpack_from('<HHI', desc, pos)
            if not compressed and not original and not source:
                continue
            if not 0 < compressed <= original <= self.chunk_size:
                raise ValueError('invalid chunk size')
            block = self.read(source, compressed)
            output.extend(block if compressed == original else xpress(block, original))
            chunks.append({'logical_offset': source, 'raw_offset': self.raw_offset(source),
                           'stored_size': compressed, 'size': original})
        if len(output) != size:
            raise ValueError(f'file size mismatch: actual={len(output)} expected={size}')
        return bytes(output), chunks


def save_payload(fs, out, path, size, table, table_size):
    data, chunks = fs.payload(size, table, table_size)
    target = out / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {'path': str(path), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'allocation_table': table, 'allocation_table_size': table_size, 'chunks': chunks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rom', type=Path)
    parser.add_argument('--output', type=Path, default=Path('analysis/imgfs'))
    parser.add_argument('--base', type=lambda value: int(value, 0), default=0x2b0000)
    args = parser.parse_args()
    raw = args.rom.read_bytes()
    fs = Imgfs(raw, base=args.base)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {'input': str(args.rom), 'sha256': hashlib.sha256(raw).hexdigest(),
                'imgfs_base': fs.base, 'compression': 'XPRESS-LZ77',
                'physical_block_size': 0x10000, 'logical_block_size': 0xf000,
                'modules': [], 'files': [], 'errors': []}
    names = set()
    for off, typ in fs.entries():
        name = '<unreadable>'
        try:
            name = fs.name(off)
            if name.casefold() in names:
                raise ValueError('duplicate filename')
            names.add(name.casefold())
            item = {'name': name, 'logical_header': off, 'raw_header': fs.raw_offset(off),
                    'attributes': fs.u32(off + 28), 'filetime': struct.unpack('<Q', fs.read(off + 32, 8))[0]}
            kind = 'modules' if typ == 0xfffffefe else 'files'
            path = Path(kind) / name / 'imageinfo.bin' if kind == 'modules' else Path(kind) / name
            item.update(save_payload(fs, args.output, path, fs.u32(off + 24), fs.u32(off + 44), fs.u32(off + 48)))
            if kind == 'modules':
                streams = []
                s = fs.u32(off + 8)
                seen = set()
                while s:
                    if s in seen or len(seen) > 64 or fs.u32(s) != 0xfffff6fd:
                        raise ValueError('invalid module stream chain')
                    seen.add(s)
                    length = fs.u32(s + 12)
                    if not 0 < length <= 4:
                        raise ValueError('unexpected stream name length')
                    sn = fs.read(s + 16, length * 2).decode('utf-16le')
                    if not sn.startswith('S') or not sn[1:].isdigit():
                        raise ValueError('unexpected stream name')
                    st = save_payload(fs, args.output, Path(kind) / name / (sn + '.bin'),
                                      fs.u32(s + 24), fs.u32(s + 28), fs.u32(s + 32))
                    st.update({'name': sn, 'logical_header': s})
                    streams.append(st)
                    s = fs.u32(s + 8)
                item['streams'] = streams
            manifest[kind].append(item)
        except (ValueError, UnicodeError, struct.error) as exc:
            manifest['errors'].append({'name': name, 'header': off, 'error': str(exc)})
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'modules': len(manifest['modules']), 'files': len(manifest['files']),
                      'errors': len(manifest['errors']), 'output': str(args.output)}))
    if manifest['errors']:
        print(json.dumps(manifest['errors'][:10], ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
