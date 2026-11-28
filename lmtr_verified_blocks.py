"""Return the same block bytes used by the pinned R3 payload validator."""
import struct

from lmtr_reference import ContractError
from lmtr_reference import container as r3


def decode_verified_blocks(body, metadata, header):
    """Decode each physical block once, then apply all the R3 payload rules."""
    prefix = 4 + struct.unpack_from('<I', body)[0]
    blocks = {}
    for descriptor in metadata['blocks']:
        start = prefix + int(descriptor['offset'])
        stored = body[start:start + int(descriptor['stored'])]
        length = int(descriptor['decoded'])
        data = stored if descriptor['codec'] == 0 else r3.inflate(stored, length)
        r3.require(len(data) == length, 'STORED decoded length', 'invalid_payload')
        r3.require(r3.sha(data) == descriptor['hash'], 'decoded block hash', 'committed_payload_integrity')
        blocks[descriptor['id']] = data
    return blocks, r3.chunk(metadata, blocks, header)


def verify_ahead(body, metadata, header):
    # Keep stable error codes across multiprocessing (ContractError takes two
    # arguments, while ValueError serializes just its formatted message).
    try:
        return decode_verified_blocks(body, metadata, header), None
    except ContractError as error:
        return None, (error.code, error.detail)
