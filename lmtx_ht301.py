"""Declarative HT-301 evidence checks, separate from generic Celsius analysis."""

import numpy as np
from lmtx_container import require
from lmtx_schema import number, integer, obj, optional, identifier, decimal_string


def validate_native(descriptor, content):
    if descriptor['encoding'] != 'ht301.raw14': return
    require(descriptor['shape'] == [288, 384] and descriptor['dtype'] == 'u16',
            'ht301.raw14 must contain original uint16 [288,384] words', 'invalid_payload')
    require(np.frombuffer(content, dtype='<u2').max() < 0x4000,
            'ht301.raw14 contains display/mixed high bits', 'invalid_payload')


def validate_evidence(schema, evidence, payloads):
    """Interpret only known schema-1 facts; future metadata is preserved opaque."""
    namespace = 'org.lmthermal.camera.ht301'
    extension = next((e for e in schema.extensions if e['id'] == namespace), None)
    if extension is None or extension['schema_version']['major'] != 1: return
    data = evidence.get(namespace)
    if data is None: return
    # No defaults, arithmetic reconstruction, center-region inference or controls.
    optional(data, 'transport', 'frame_relationship', 'settings', 'host_configuration', 'observations', 'calibration')
    if 'transport' in data:
        t = obj(data['transport'])
        descriptor = schema.payloads.get(t.get('payload_id'))
        require(descriptor is not None and descriptor['role'] == 'acquisition', 'HT transport reference mismatch', 'invalid_payload')
        content = payloads[descriptor['id']]
        image_bytes, trailer_bytes, rows = (integer(t.get(k)) for k in ('image_bytes', 'trailer_bytes', 'rows'))
        require((image_bytes, trailer_bytes, rows) == (221184, 3072, 292) and len(content) == image_bytes+trailer_bytes,
                'HT transport layout mismatch', 'invalid_payload')
        native_id = obj(data.get('frame_relationship', {})).get('native_payload_id')
        native = schema.payloads.get(native_id)
        if native is not None:
            require(native['role'] == 'native_samples' and native['encoding'] == 'ht301.raw14' and
                    payloads[native_id] == content[:image_bytes], 'HT native/transport evidence differs', 'invalid_payload')
    if 'frame_relationship' in data:
        relationship = obj(data['frame_relationship'])
        optional(relationship, 'native_payload_id', 'sequence')
        if 'native_payload_id' in relationship:
            native = schema.payloads.get(identifier(relationship['native_payload_id']))
            require(native is not None and native['role'] == 'native_samples',
                    'HT native relationship reference mismatch', 'invalid_payload')
        if 'sequence' in relationship:
            decimal_string(relationship['sequence'])
    fields = {'settings': ('correction_c', 'reflected_c', 'ambient_c', 'humidity', 'emissivity', 'distance'),
              'host_configuration': ('range', 'lens', 'shutter_fix')}
    for group, known_fields in fields.items():
        if group in data:
            values = obj(data[group])
            optional(values, *known_fields)
            for key in known_fields:
                if key in values:
                    number(values[key])
    if 'calibration' in data:
        require(isinstance(data['calibration'], list), 'Invalid HT calibration', 'invalid_payload')
        for v in data['calibration']: number(v)
    if 'observations' in data:
        observations = obj(data['observations'])
        for key in ('trailer_center', 'literal_center', 'trailer_high', 'trailer_low'):
            optional(observations, key)
            observation = observations.get(key)
            if observation is None: continue
            observation = obj(observation)
            integer(observation.get('raw_index'), 0, 0x3fff)
            number(observation.get('native_equivalent_c'))
            if 'xy' in observation:
                xy = observation['xy']
                require(isinstance(xy, list) and len(xy) == 2 and
                        0 <= integer(xy[0]) < schema.geometry.width and 0 <= integer(xy[1]) < schema.geometry.height,
                        'HT observation outside native image', 'invalid_payload')
