"""Accepted v1 manifest types, references and cross-field invariants.

Normative authority is LMThermal/docs/LMTX_FORMAT_V1.md at 1cbdd776.
Unknown optional JSON remains data; there is no plugin/code/path execution.
"""

from datetime import datetime
from decimal import Decimal
import math
import re
from uuid import UUID

from lmtx_container import LmtxError, MAX_MEMBER, require as container_require, safe_path
from offline_measurement import Geometry, Rectangle

FEATURES = {'core.still', 'core.native-coordinates', 'core.temperature-f32le',
            'core.validity-u8', 'core.analysis-v1'}
ROLES = {'preview': 'preview', 'temperature': 'temperature', 'native_samples': 'native_samples',
         'acquisition_payload': 'acquisition', 'visible_image': 'visible_image', 'calibration_settings': None}
DTYPES = {'u8': ('u1', 1), 'u16': ('<u2', 2), 'u32': ('<u4', 4), 'i16': ('<i2', 2),
          'i32': ('<i4', 4), 'f32': ('<f4', 4), 'f64': ('<f8', 8)}
WARNING = 'Native-equivalent temperatures; absolute physical accuracy not yet independently validated.'


def demand(condition, message, code='invalid_manifest'):
    container_require(condition, message, code)


def obj(value):
    demand(type(value) is dict, 'Expected JSON object')
    return value


def array(value):
    demand(type(value) is list, 'Expected JSON array')
    return value


def text(value, *, empty=False):
    demand(type(value) is str and (empty or bool(value)), 'Expected nonempty string')
    return value


def integer(value, minimum=0, maximum=2147483647):
    demand(type(value) is int and minimum <= value <= maximum, 'Invalid integer/type/bounds')
    return value


def number(value):
    demand(type(value) in (int, float, Decimal), 'Expected finite JSON number')
    try: result = float(value)
    except (OverflowError, ValueError): result = math.inf
    demand(math.isfinite(result), 'Known numeric field overflows binary64')
    return result


def identifier(value):
    result = text(value)
    demand(len(result) <= 128 and re.fullmatch(r'[a-z0-9][a-z0-9._-]*', result) is not None and '..' not in result,
           'Invalid stable identifier')
    return result


def uuid(value):
    result = text(value)
    demand(re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', result) is not None,
           'Invalid canonical UUID')
    demand(UUID(result).int != 0, 'Nil UUID')
    return result


def sha(value):
    demand(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None, 'Invalid SHA-256')
    return value


def optional(value, *fields):
    for key in fields: demand(key not in value or value[key] is not None, f'Known optional {key} must be omitted, not null')


def version(value, minimum=0):
    value = obj(value)
    return integer(value.get('major'), minimum), integer(value.get('minor'))


def decimal_string(value):
    s = text(value)
    demand(len(s) <= 20 and re.fullmatch(r'0|[1-9][0-9]*', s) is not None and int(s) <= 18446744073709551615,
           'Invalid unsigned-64 decimal string')


def producer(value):
    value = obj(value)
    identifier(value.get('application_id'))
    text(value.get('version'))
    optional(value, 'build_id')
    if 'build_id' in value: text(value['build_id'])


def clock(value):
    value = obj(value)
    identifier(value.get('clock_source'))
    status = value.get('status')
    demand(status in ('known', 'unreliable', 'unknown'), 'Invalid clock status')
    optional(value, 'utc', 'reason', 'uncertainty_ns')
    if status != 'known': identifier(value.get('reason'))
    elif 'reason' in value: identifier(value['reason'])
    demand(status != 'known' or 'utc' in value, 'Known clock lacks UTC')
    demand(status != 'unknown' or 'utc' not in value, 'Unknown clock cannot supply UTC')
    if 'utc' in value:
        utc = text(value['utc'])
        demand(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-5][0-9](?:\.[0-9]{1,9})?Z', utc) is not None,
               'Noncanonical UTC')
        try: datetime.fromisoformat(utc[:-1]+'+00:00')
        except ValueError: raise LmtxError('invalid_manifest', 'Invalid UTC calendar')
    if 'uncertainty_ns' in value: decimal_string(value['uncertainty_ns'])


def rectangle(value, geometry):
    b = obj(value.get('bounds'))
    roi = Rectangle(*(integer(b.get(k)) for k in ('x1_px', 'y1_px', 'x2_px', 'y2_px')))
    try: return roi.validate(geometry)
    except ValueError as exc: raise LmtxError('invalid_manifest', str(exc)) from exc


class Schema:
    def __init__(self, manifest):
        self.manifest = m = obj(manifest)
        optional(m, 'extensions', 'analysis', 'presentation', 'lineage')
        demand(m.get('format') == 'lmthermal-exchange', 'Not LMThermal Exchange Format', 'unsupported_format')
        major, minor = version(m.get('schema_version'))
        demand(major == 1, f'Unsupported major {major}', 'unsupported_major')
        demand(m.get('kind') == 'still', 'Only still captures are supported', 'unsupported_kind')
        demand(m.get('complete') is True, 'Capture is incomplete')
        uuid(m.get('capture_id'))
        producer(m.get('producer'))
        clock(m.get('creation_time'))
        source = obj(m.get('source'))
        module = identifier(source.get('module_id'))
        demand(re.fullmatch(r'[a-z0-9][a-z0-9.-]*', module) is not None, 'Invalid module ID')
        text(source.get('model_id'))
        optional(source, 'module_version')
        if 'module_version' in source: text(source['module_version'])
        demand(source.get('origin') in ('device', 'simulated', 'imported_legacy', 'unknown'), 'Invalid source origin')
        g = obj(m.get('geometry'))
        self.geometry = Geometry(integer(g.get('width_px'), 1), integer(g.get('height_px'), 1))
        demand(self.geometry.pixel_count <= 33554432, 'Primary grid exceeds pixel limit', 'resource_limit')
        for k, v in {'coordinate_space': 'native', 'origin': 'top_left', 'x_direction': 'right',
                     'y_direction': 'down', 'matrix_order': 'row_major', 'orientation': 'source_native'}.items():
            demand(g.get(k) == v, f'Invalid native geometry {k}')
        features = [identifier(v) for v in array(m.get('required_features'))]
        demand(len(features) <= 64 and len(set(features)) == len(features), 'Invalid required-feature inventory')
        demand(set(features) <= FEATURES, 'Unknown required feature: '+', '.join(sorted(set(features)-FEATURES)),
               'unsupported_required_feature')
        demand({'core.still', 'core.native-coordinates'} <= set(features), 'Missing mandatory core features')
        self.features = set(features)
        acquisition = obj(m.get('acquisition'))
        clock(acquisition.get('time'))
        optional(acquisition, 'producer', 'sequence', 'receipt', 'settings_extension_ids')
        if 'producer' in acquisition: producer(acquisition['producer'])
        if 'sequence' in acquisition: decimal_string(acquisition['sequence'])
        if 'receipt' in acquisition:
            receipt = obj(acquisition['receipt'])
            uuid(receipt.get('clock_domain_id'))
            decimal_string(receipt.get('monotonic_ns'))
            optional(receipt, 'utc')
            if 'utc' in receipt: clock(receipt['utc'])
        payloads = [obj(v) for v in array(m.get('payloads'))]
        demand(1 <= len(payloads) <= 255, 'Payload count exceeds limits', 'resource_limit')
        self.payloads = {}
        names = set()
        self.spaces = {'native': self.geometry}
        for p in payloads:
            pid = identifier(p.get('id'))
            demand(pid not in self.payloads, 'Duplicate payload ID')
            self.payloads[pid] = p
            name = safe_path(p.get('member'))
            demand(name != 'manifest.json' and name not in names, 'Duplicate manifest inventory member', 'unsafe_path')
            names.add(name)
            identifier(p.get('role'))
            text(p.get('media_type'))
            length = integer(p.get('byte_length'), 1)
            demand(length <= MAX_MEMBER, 'Payload exceeds 128 MiB', 'resource_limit')
            sha(p.get('sha256'))
            self._descriptor(p)
        self.measurement = measurement = obj(m.get('measurement'))
        available = measurement.get('status') == 'available'
        demand(measurement.get('status') in ('available', 'unavailable'), 'Invalid measurement status')
        temperatures, masks = self.role('temperature'), self.role('temperature_validity')
        demand(len(temperatures) <= 1 and len(masks) <= 1, 'Duplicate temperature/mask role')
        demand(available == bool(temperatures), 'Measurement status contradicts payloads')
        self.temperature = temperatures[0] if temperatures else None
        self.mask = masks[0] if masks else None
        optional(measurement, 'mask_payload_id', 'extrema')
        if not available:
            identifier(measurement.get('reason'))
            demand(not masks and not any(k in measurement for k in ('temperature_payload_id', 'mask_payload_id', 'extrema', 'provenance', 'validity')),
                   'Unavailable measurement contains readings or temperature references')
        else:
            demand(measurement.get('temperature_payload_id') == self.temperature['id'] and 'core.temperature-f32le' in features,
                   'Temperature reference/feature mismatch')
            demand(measurement.get('validity') in ('all_valid', 'partially_valid', 'no_valid_pixels'), 'Invalid validity class')
            demand((not masks and 'mask_payload_id' not in measurement and measurement['validity'] == 'all_valid') or
                   (bool(masks) and measurement.get('mask_payload_id') == self.mask['id'] and 'core.validity-u8' in features),
                   'Mask reference/feature mismatch')
            self._provenance(obj(measurement.get('provenance')), module)
        self.extensions = [obj(e) for e in array(m.get('extensions', []))]
        self._extensions()
        settings = [identifier(v) for v in array(acquisition.get('settings_extension_ids', []))]
        demand(len(set(settings)) == len(settings) and set(settings) <= {e['id'] for e in self.extensions}, 'Settings reference mismatch')
        caps, availability = obj(m.get('capabilities')), obj(m.get('availability'))
        for role, payload_role in ROLES.items():
            demand(caps.get(role) in ('supported', 'unsupported', 'unknown'), 'Invalid capability')
            a = obj(availability.get(role))
            optional(a, 'reason')
            exists = bool(settings) if payload_role is None else bool(self.role(payload_role))
            demand(a.get('status') in ('present', 'absent') and (a['status'] == 'present') == exists and
                   (not exists or caps[role] != 'unsupported'), 'Availability/capability/inventory contradiction')
            if not exists or 'reason' in a: identifier(a.get('reason'))
        classification = ('radiometric' if available else 'native_samples' if self.role('native_samples') else
                          'visual' if self.role('preview') or self.role('visible_image') else
                          'opaque_evidence' if self.role('acquisition') else None)
        demand(classification is not None, 'No recognized source payload', 'invalid_payload')
        demand(m.get('content_class') == classification, 'Invalid content class')
        self.rectangles = []
        self._analysis(m.get('analysis'))
        self._presentation(m.get('presentation'))
        if 'lineage' in m: self._lineage(obj(m['lineage']))

    def role(self, role): return [p for p in self.payloads.values() if p['role'] == role]

    def _descriptor(self, p):
        role, name = p['role'], p['member']
        if role in ('temperature', 'temperature_validity', 'native_samples'):
            shape = [integer(v, 1) for v in array(p.get('shape'))]
            demand(len(shape) == 2 or role == 'native_samples' and len(shape) == 3, 'Invalid typed shape')
            demand(shape[:2] == list(self.geometry.shape) and (len(shape) == 2 or shape[2] <= 16), 'Wrong plane geometry/channels')
            demand(p.get('order') == 'row_major' and p.get('coordinate_space') == 'native', 'Wrong array order/space')
            demand(p.get('dtype') in DTYPES, 'Unsupported typed-plane dtype')
            dtype, size = DTYPES[p['dtype']]
            demand(math.prod(shape)*size == p['byte_length'], 'Typed byte length mismatch', 'invalid_payload')
            demand(p.get('byte_order') == ('not_applicable' if size == 1 else 'little'), 'Wrong byte order')
            demand(name.startswith('data/'), 'Typed plane must be under data/')
            if role == 'temperature':
                demand(p.get('encoding') == 'ieee754' and p['dtype'] == 'f32' and p.get('unit') == 'Cel' and
                       p['media_type'] == 'application/octet-stream', 'Invalid Celsius encoding')
            elif role == 'temperature_validity':
                demand(p.get('encoding') == 'validity.u8' and p['dtype'] == 'u8', 'Invalid mask encoding')
            else:
                demand('.' in identifier(p.get('encoding')), 'Native encoding must be namespaced')
        elif role == 'acquisition':
            demand(name.startswith('data/'), 'Acquisition must be under data/')
            identifier(p.get('encoding'))
        elif role in ('preview', 'visible_image'):
            demand(name.startswith('preview/') and p['media_type'] in ('image/png', 'image/jpeg'), 'Invalid image path/media type')
            image = obj(p.get('image'))
            geometry = Geometry(integer(image.get('width_px'), 1), integer(image.get('height_px'), 1))
            demand(geometry.pixel_count <= 33554432, 'Image exceeds pixel limit', 'resource_limit')
            demand(image.get('orientation') == 'stored_pixels', 'Invalid stored image orientation')
            space = identifier(image.get('coordinate_space'))
            demand((space == 'native' and geometry == self.geometry) if role == 'preview' else space != 'native', 'Invalid image coordinate space')
            demand(space not in self.spaces or self.spaces[space] == geometry, 'Conflicting visible-image space geometry')
            self.spaces[space] = geometry
        elif role in ('extension_json', 'extension_blob'):
            demand(name.startswith('extensions/'), 'Extension must be under extensions/')
            if role == 'extension_json': demand(p['media_type'] == 'application/json', 'Invalid extension JSON media type')

    def _provenance(self, p, module):
        demand(p.get('kind') in ('native_equivalent', 'device_reported', 'simulated', 'derived', 'unknown'), 'Invalid provenance kind')
        demand(p.get('physical_accuracy') in ('not_independently_validated', 'independently_validated', 'unknown', 'not_applicable'), 'Invalid physical accuracy')
        optional(p, 'algorithm', 'warning')
        if 'algorithm' in p:
            algorithm = obj(p['algorithm'])
            identifier(algorithm.get('id'))
            text(algorithm.get('version'))
        if 'warning' in p: text(p['warning'])
        if module == 'ht301' and p['kind'] == 'native_equivalent':
            demand(p['physical_accuracy'] == 'not_independently_validated' and p.get('warning') == WARNING,
                   'HT-301 native-equivalent accuracy warning must remain intact')
        if p['physical_accuracy'] == 'independently_validated':
            demand(any(v for k, v in p.items() if k not in ('kind', 'physical_accuracy', 'algorithm', 'warning')),
                   'Independent-accuracy claim lacks evidence metadata/reference')

    def _extensions(self):
        namespaces, assigned = set(), set()
        for extension in self.extensions:
            namespace = identifier(extension.get('id'))
            demand(re.fullmatch(r'[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+', namespace) is not None,
                   'Invalid extension namespace')
            demand(namespace not in namespaces, 'Duplicate namespace')
            namespaces.add(namespace)
            version(extension.get('schema_version'), 1)
            optional(extension, 'metadata_payload_id', 'blob_payload_ids')
            refs = []
            if 'metadata_payload_id' in extension: refs.append((identifier(extension['metadata_payload_id']), 'extension_json'))
            refs += [(identifier(v), 'extension_blob') for v in array(extension.get('blob_payload_ids', []))]
            demand(refs, 'Empty extension evidence')
            for ref, role in refs:
                payload = self.payloads.get(ref)
                demand(ref not in assigned and payload is not None and payload['role'] == role and
                       payload['member'].startswith('extensions/'+namespace+'/'), 'Invalid extension ownership/reference')
                assigned.add(ref)
        demand(all(p['id'] in assigned for p in self.payloads.values() if p['role'] in ('extension_json', 'extension_blob')),
               'Unassigned extension payload')

    def _analysis(self, value):
        if value is None: return
        a = obj(value)
        producer(a.get('producer'))
        clock(a.get('creation_time'))
        optional(a, 'points', 'shapes', 'annotations')
        groups = {k: [obj(v) for v in array(a.get(k, []))] for k in ('points', 'shapes', 'annotations')}
        items = sum(groups.values(), [])
        ids = [identifier(item.get('id')) for item in items]
        demand(len(ids) == len(set(ids)), 'Duplicate analysis ID')
        targets = {v['id'] for v in groups['points']+groups['shapes']}
        for group, members in groups.items():
            for item in members:
                space = identifier(item.get('coordinate_space'))
                demand(space in self.spaces, 'Unknown analysis coordinate space')
                geometry = self.spaces[space]
                optional(item, 'label', 'temperature_c', 'statistics', 'anchor', 'target_id')
                if 'label' in item: text(item['label'], empty=True)
                if group == 'points': self._point(item, geometry)
                elif group == 'shapes':
                    identifier(item.get('type'))
                    if item['type'] == 'rectangle':
                        demand(space == 'native' and item.get('interval') == 'half_open', 'Invalid rectangle semantics')
                        self.rectangles.append((item, rectangle(item, geometry)))
                    else: demand('geometry' in item and item['geometry'] is not None, 'Future shape lacks declarative geometry')
                else: text(item.get('text'), empty=True)
                if 'temperature_c' in item or 'statistics' in item:
                    demand(space == 'native' and self.temperature is not None, 'Non-native/unavailable numerical analysis')
                if 'anchor' in item: self._point(obj(item['anchor']), geometry)
                if 'target_id' in item: demand(identifier(item['target_id']) in targets, 'Invalid annotation target')

    @staticmethod
    def _point(item, geometry):
        demand(integer(item.get('x_px')) < geometry.width and integer(item.get('y_px')) < geometry.height,
               'Point outside declared coordinate grid')

    def _presentation(self, value):
        if value is None: return
        p = obj(value)
        optional(p, 'palette_id', 'range_mode', 'effective_bounds', 'transform')
        if 'palette_id' in p: identifier(p['palette_id'])
        if 'range_mode' in p: demand(p['range_mode'] in ('auto', 'manual'), 'Invalid range mode')
        if 'effective_bounds' in p:
            demand('range_mode' in p and self.temperature is not None,
                   'Celsius range without a temperature plane/range mode')
            b = obj(p['effective_bounds'])
            demand(b.get('unit') == 'Cel' and number(b.get('min')) < number(b.get('max')), 'Invalid Celsius bounds')
        if self.temperature is not None and self.measurement['validity'] != 'no_valid_pixels' and 'range_mode' in p:
            demand('effective_bounds' in p, 'Range mode without effective Celsius bounds')
        if 'transform' in p:
            t = obj(p['transform'])
            demand(integer(t.get('rotation_degrees')) in (0, 90, 180, 270) and type(t.get('mirror_x')) is bool and
                   type(t.get('mirror_y')) is bool, 'Invalid presentation transform')

    def _lineage(self, value):
        demand(value.get('operation') == 'analysis', 'Unsupported v1 lineage operation')
        parents = array(value.get('parents'))
        demand(len(parents) == 1, 'v1 lineage requires one direct parent')
        parent = obj(parents[0])
        demand(uuid(parent.get('capture_id')) != self.manifest['capture_id'], 'Derivative reused parent capture UUID')
        sha(parent.get('archive_sha256'))
        mapping = [obj(v) for v in array(value.get('preserved_payloads'))]
        parent_ids = [identifier(v.get('parent_payload_id')) for v in mapping]
        child_ids = [identifier(v.get('payload_id')) for v in mapping]
        demand(mapping and len(set(parent_ids)) == len(parent_ids) and len(set(child_ids)) == len(child_ids) and
               set(child_ids) <= set(self.payloads), 'Invalid preserved-payload lineage mapping')
        # Full parent-byte/semantic preservation is only provable with that parent.
        # Missing parent is explicitly not corruption; no parent path is followed.
