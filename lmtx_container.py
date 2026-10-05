"""The bounded LMTX ZIP profile, without extraction or permissive ZIP fallbacks."""

from dataclasses import dataclass
import binascii
import hashlib
import re
import stat
import struct
import zlib

MAX_MEMBER = 134_217_728
MAX_ARCHIVE = 268_435_456
MAX_TOTAL = 536_870_912
MAX_JSON = 1_048_576


class LmtxError(ValueError):
    """Stable contract error code, with an actionable English diagnostic."""
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')


def require(condition, message, code='invalid_container'):
    if not condition: raise LmtxError(code, message)


def safe_path(name):
    require(isinstance(name, str), 'Member name must be a string', 'unsafe_path')
    try: encoded = name.encode('ascii')
    except UnicodeError: raise LmtxError('unsafe_path', 'Member name must be lowercase ASCII')
    require(len(encoded) <= 240, 'Member path exceeds 240 bytes', 'resource_limit')
    parts = name.split('/')
    require(name == 'manifest.json' or len(parts) > 1 and parts[0] in ('data', 'preview', 'extensions'),
            'Member must be manifest.json or under data/preview/extensions', 'unsafe_path')
    for part in parts:
        require(re.fullmatch(r'[a-z0-9][a-z0-9._-]*', part) is not None and '..' not in part and not part.endswith('.'),
                'Unsafe path component', 'unsafe_path')
        base = part.split('.')[0]
        require(base not in ('con', 'prn', 'aux', 'nul') and not re.fullmatch(r'(com|lpt)[1-9]', base),
                'Windows reserved path component', 'unsafe_path')
    return name


def _extra(raw):
    at = 0
    while at < len(raw):
        require(at+4 <= len(raw), 'Truncated ZIP extra field')
        kind, size = struct.unpack_from('<HH', raw, at)
        at += 4
        require(at+size <= len(raw), 'Truncated ZIP extra field data')
        # Unknown metadata never becomes an alternate path authority.
        require(kind not in (1, 0x0017, 0x9901), 'Forbidden ZIP64/encryption extra field')
        at += size


@dataclass(frozen=True)
class Record:
    name: str
    method: int
    flags: int
    crc: int
    compressed: int
    size: int
    offset: int
    data_offset: int
    end_offset: int


class Container:
    """Single open file, validated layout, streaming decompression and CRC.

    Local records must exactly cover the region before the central directory.
    No alternate directory, hidden preamble, overlap or trailing content is read.
    """
    def __init__(self, stream, cancelled=lambda: False):
        self.cancelled = cancelled
        self.stream = stream
        stream.seek(0, 2)
        self.size = stream.tell()
        require(self.size <= MAX_ARCHIVE, 'Archive exceeds 256 MiB', 'resource_limit')
        require(self.size >= 22, 'Missing ZIP end record')
        tail_size = min(self.size, 65557)
        stream.seek(self.size-tail_size)
        tail = stream.read(tail_size)
        # An EOCD-looking token inside a comment is not a second authority.
        end = None
        for at in range(len(tail)-22, -1, -1):
            if tail[at:at+4] == b'PK\x05\x06' and at+22+struct.unpack_from('<H', tail, at+20)[0] == len(tail):
                end = at
                break
        require(end is not None, 'Incomplete or trailing ZIP end record')
        _, disk, central_disk, disk_count, count, central_size, central_offset, _ = struct.unpack_from('<4s4H2IH', tail, end)
        require(disk == central_disk == 0 and disk_count == count and count != 0xffff and
                central_size != 0xffffffff and central_offset != 0xffffffff, 'Multidisk or ZIP64 archive')
        require(1 <= count <= 256 and central_size <= 2_097_152, 'ZIP directory exceeds limits', 'resource_limit')
        end_offset = self.size-tail_size+end
        require(central_offset+central_size == end_offset, 'Central directory bounds disagree')
        central = self.read_at(central_offset, central_size)
        records = []
        names = set()
        at = 0
        total = 0
        for _ in range(count):
            if self.cancelled(): raise InterruptedError('Load cancelled')
            require(at+46 <= len(central), 'Truncated central entry')
            fields = struct.unpack_from('<4s6H3I5H2I', central, at)
            (signature, made, needed, flags, method, mt, md, crc, compressed, size,
             nlen, xlen, clen, disk, internal, external, offset) = fields
            require(signature == b'PK\x01\x02', 'Invalid central signature')
            require(needed <= 20 and disk == 0 and min(compressed, size, offset) >= 0 and
                    0xffffffff not in (compressed, size, offset), 'ZIP64 or unsupported ZIP version')
            require(method in (0, 8) and flags & ~0x080e == 0 and (method == 8 or not flags & 6),
                    'Encryption, unsupported flags or compression')
            require(not external & 0x18, 'Directory/volume attributes are forbidden')
            unix_mode = external >> 16
            require(not unix_mode & 0o111 and (stat.S_IFMT(unix_mode) in (0, stat.S_IFREG)),
                    'Link, special or executable ZIP member')
            length = 46+nlen+xlen+clen
            require(at+length <= len(central), 'Truncated central name/extra/comment')
            raw_name = central[at+46:at+46+nlen]
            try: name = raw_name.decode('ascii')
            except UnicodeError: raise LmtxError('unsafe_path', 'Non-ASCII ZIP path')
            safe_path(name)
            require(name not in names, 'Duplicate/colliding member path', 'unsafe_path')
            names.add(name)
            _extra(central[at+46+nlen:at+46+nlen+xlen])
            total += size
            require(size <= MAX_MEMBER and total <= MAX_TOTAL, 'Decompressed byte budget exceeded', 'resource_limit')
            require(name != 'manifest.json' or size <= MAX_JSON, 'Manifest exceeds 1 MiB', 'resource_limit')
            require(offset+30 <= central_offset, 'Local offset outside data region')
            local = self.read_at(offset, 30)
            sig, ln, lf, lm, lt, ld, lc, lcompressed, ls, nl, xl = struct.unpack('<4s5H3I2H', local)
            require(sig == b'PK\x03\x04' and (ln, lf, lm, lt, ld) == (needed, flags, method, mt, md),
                    'Local/central header contradiction')
            require(nl == nlen and self.read_at(offset+30, nl) == raw_name, 'Local/central path contradiction')
            _extra(self.read_at(offset+30+nl, xl))
            data_offset = offset+30+nl+xl
            finish = data_offset+compressed
            require(finish <= central_offset, 'Compressed member extends into directory')
            if flags & 8:
                require(lc in (0, crc) and lcompressed in (0, compressed) and ls in (0, size),
                        'Invalid placeholder local CRC/size')
                dd = self.read_at(finish, min(16, central_offset-finish))
                starts = [0, 4] if dd[:4] == b'PK\x07\x08' else [0]
                valid = [s for s in starts if len(dd) >= s+12 and struct.unpack_from('<3I', dd, s) == (crc, compressed, size)]
                require(valid, 'Contradictory/truncated data descriptor')
                finish += valid[0]+12
            else:
                require((lc, lcompressed, ls) == (crc, compressed, size), 'Local/central CRC/size contradiction')
            require(method != 0 or compressed == size, 'STORED sizes disagree')
            records.append(Record(name, method, flags, crc, compressed, size, offset, data_offset, finish))
            at += length
        require(at == len(central), 'Extra central-directory records')
        require('manifest.json' in names, 'Missing manifest', 'missing_payload')
        for name in names:
            parts = name.split('/')
            require(not any('/'.join(parts[:i]) in names for i in range(1, len(parts))),
                    'Parent/file path conflict', 'unsafe_path')
        covered = 0
        for record in sorted(records, key=lambda r: r.offset):
            require(record.offset == covered, 'Overlapping/gapped records or self-extracting preamble')
            covered = record.end_offset
        require(covered == central_offset, 'Hidden records before directory')
        self.records = {r.name: r for r in records}

    def read_at(self, offset, size):
        require(0 <= offset <= self.size and 0 <= size <= self.size-offset, 'Out-of-range ZIP read')
        self.stream.seek(offset)
        result = self.stream.read(size)
        require(len(result) == size, 'Truncated archive')
        return result

    def chunks(self, name):
        """Bounded output, including actual deflate length and eof validation."""
        record = self.records[name]
        self.stream.seek(record.data_offset)
        remaining = record.compressed
        count = crc = 0
        inflater = zlib.decompressobj(-15) if record.method == 8 else None
        try:
            while remaining:
                if self.cancelled(): raise InterruptedError('Load cancelled')
                compressed = self.stream.read(min(65536, remaining))
                require(compressed, 'Truncated member')
                remaining -= len(compressed)
                pending = compressed
                while pending:
                    output = inflater.decompress(pending, 65536) if inflater else pending
                    pending = inflater.unconsumed_tail if inflater else b''
                    count += len(output)
                    require(count <= record.size and count <= MAX_MEMBER, 'Actual member exceeds declared size', 'integrity_mismatch')
                    crc = binascii.crc32(output, crc)
                    if output: yield output
                    if inflater:
                        require(not inflater.unused_data, 'Trailing or concatenated DEFLATE data')
            require(inflater is None or inflater.eof, 'Incomplete DEFLATE stream')
        except zlib.error as exc:
            raise LmtxError('invalid_container', 'Invalid DEFLATE data') from exc
        require(count == record.size and crc == record.crc, 'Member byte length/CRC mismatch', 'integrity_mismatch')

    def read(self, name, expected_sha=None):
        digest = hashlib.sha256()
        parts = []
        for chunk in self.chunks(name):
            digest.update(chunk)
            parts.append(chunk)
        if expected_sha is not None:
            require(digest.hexdigest() == expected_sha, f'SHA-256 mismatch: {name}', 'integrity_mismatch')
        return b''.join(parts)
