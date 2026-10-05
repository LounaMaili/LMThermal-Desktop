"""Bounded PNG/JPEG stored-pixel validation, with no automatic EXIF rotation."""

import binascii
from io import BytesIO
import struct
import warnings
import zlib

from PIL import Image
from lmtx_container import LmtxError, MAX_MEMBER, require


def _png(content, width, height):
    """Validate CRC/chunks and actual inflated scanlines, including Adam7.

    Pillow verifies image interpretation afterwards; this pass prevents an IDAT
    stream from expanding past the declared scanline layout before decoding.
    """
    require(content[:8] == b'\x89PNG\r\n\x1a\n', 'Not a PNG payload', 'invalid_payload')
    at = 8
    seen_header = seen_data = ended = after_data = False
    inflater = zlib.decompressobj()
    spans = []
    inflated = span_index = span_offset = 0
    expected = 0
    while at < len(content):
        require(at+12 <= len(content), 'Truncated PNG chunk', 'invalid_payload')
        size, = struct.unpack_from('>I', content, at)
        kind = content[at+4:at+8]
        require(size <= len(content)-at-12, 'PNG chunk exceeds member', 'invalid_payload')
        data = memoryview(content)[at+8:at+8+size]
        crc, = struct.unpack_from('>I', content, at+8+size)
        require(binascii.crc32(data, binascii.crc32(kind)) == crc, 'PNG CRC mismatch', 'integrity_mismatch')
        require(not ended and (seen_header or kind == b'IHDR'), 'PNG ordering/extra data', 'invalid_payload')
        require(len(kind) == 4 and all(65 <= c <= 90 or 97 <= c <= 122 for c in kind) and not kind[2] & 32,
                'Invalid PNG chunk type', 'invalid_payload')
        if kind == b'IHDR':
            require(not seen_header and size == 13, 'Invalid PNG header', 'invalid_payload')
            seen_header = True
            w, h, depth, color, compression, filtering, interlace = struct.unpack('>2I5B', data)
            require((w, h) == (width, height), 'Decoded PNG dimensions disagree', 'invalid_payload')
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
            require(channels is not None and depth in ({1, 2, 4, 8} if color == 3 else {1, 2, 4, 8, 16} if color == 0 else {8, 16})
                    and compression == filtering == 0 and interlace in (0, 1), 'Invalid PNG encoding', 'invalid_payload')
            require(w*h*channels*(2 if depth == 16 else 1) <= MAX_MEMBER, 'Decoded PNG exceeds 128 MiB', 'resource_limit')
            passes = [(0, 0, 1, 1)] if not interlace else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
            for x, y, dx, dy in passes:
                pw, ph = max(0, (w-x+dx-1)//dx), max(0, (h-y+dy-1)//dy)
                if pw and ph:
                    row_bytes = (pw*channels*depth+7)//8+1
                    spans.append((row_bytes, row_bytes*ph))
                    expected += row_bytes*ph
        elif kind == b'IDAT':
            require(not after_data, 'Noncontiguous PNG IDAT', 'invalid_payload')
            seen_data = True
            pending = data
            try:
                while pending:
                    output = inflater.decompress(pending, 65536)
                    pending = inflater.unconsumed_tail
                    require(not inflater.unused_data, 'Extra PNG zlib data', 'invalid_payload')
                    require(inflated+len(output) <= expected, 'PNG inflated size exceeds dimensions', 'invalid_payload')
                    offset = 0
                    while offset < len(output):
                        row_bytes, span_size = spans[span_index]
                        length = min(len(output)-offset, span_size-span_offset)
                        first = (-span_offset) % row_bytes
                        require(all(v <= 4 for v in output[offset+first:offset+length:row_bytes]), 'Invalid PNG filter', 'invalid_payload')
                        offset += length
                        span_offset += length
                        if span_offset == span_size:
                            span_index += 1
                            span_offset = 0
                    inflated += len(output)
            except zlib.error as exc: raise LmtxError('invalid_payload', 'Invalid PNG zlib') from exc
        elif kind == b'IEND':
            require(size == 0 and seen_data and inflater.eof and inflated == expected, 'Incomplete PNG', 'invalid_payload')
            ended = True
        else:
            if seen_data: after_data = True
            require(kind[0] & 32 or kind == b'PLTE', 'Unsupported critical PNG chunk', 'invalid_payload')
        at += size+12
    require(ended, 'PNG lacks IEND', 'invalid_payload')


def decode_image(content, descriptor):
    image = descriptor['image']
    width, height = image['width_px'], image['height_px']
    require(width*height*3 <= MAX_MEMBER, 'Decoded RGB exceeds 128 MiB', 'resource_limit')
    expected = 'PNG' if descriptor['media_type'] == 'image/png' else 'JPEG'
    if expected == 'PNG': _png(content, width, height)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content), formats=[expected]) as source:
                require(source.format == expected and source.size == (width, height), 'Image format/dimensions disagree', 'invalid_payload')
                source.verify()
            with Image.open(BytesIO(content), formats=[expected]) as source:
                require(getattr(source, 'n_frames', 1) == 1, 'Still image contains animation', 'invalid_payload')
                require(source.getexif().get(274, 1) == 1, 'Nonidentity EXIF orientation must be normalized by producer', 'invalid_payload')
                require(width*height*len(source.getbands())*(2 if source.mode.startswith('I;16') else 4 if source.mode in ('I', 'F') else 1)
                        <= MAX_MEMBER, 'Decoded image exceeds 128 MiB', 'resource_limit')
                source.load()
                return source.convert('RGB').tobytes()
    except LmtxError: raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, MemoryError) as exc:
        raise LmtxError('resource_limit', 'Image decoder allocation refused') from exc
    except Exception as exc:
        raise LmtxError('invalid_payload', 'Cannot decode complete image') from exc
