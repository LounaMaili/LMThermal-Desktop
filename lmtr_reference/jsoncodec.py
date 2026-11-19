"""Exact bounded JSON for the accepted LMTR semantic hashing domain.

This implementation intentionally does not import the earlier draft checker.
Decimal tuples avoid dependence on the caller's decimal precision/context.
"""
from decimal import Decimal
import datetime
import json
import re
from pathlib import Path

MAX63 = 2**63 - 1
MAX_JSON_BYTES = 1048576

class ContractError(ValueError):
    # Initialize exact error/schema state without loading any remote dependency.
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(code + ': ' + detail)

# Emit a stable contract error; invalid data must never become a benign frame gap.
def require(condition, detail, code='invalid_metadata'):
    if not condition:
        raise ContractError(code, detail)

# Render exact semantic Decimal values independently of caller precision or lexical spelling.
def decimal_text(value):
    require(type(value) in (int, Decimal), 'binary floats are not exact JSON authority')
    number = Decimal(value) if type(value) is int else value
    require(number.is_finite(), 'nonfinite JSON number')
    if not number:
        return '0'
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient = ''.join(str(d) for d in digits)
    adjusted = len(digits) + exponent - 1
    require(-4096 <= adjusted <= 4096, 'adjusted decimal exponent', 'resource_limit')
    if -6 <= adjusted <= 20:
        position = len(coefficient) + exponent
        if position <= 0:
            output = '0.' + '0' * -position + coefficient
        elif position >= len(coefficient):
            output = coefficient + '0' * (position - len(coefficient))
        else:
            output = coefficient[:position] + '.' + coefficient[position:]
    else:
        output = coefficient[0] + ('.' + coefficient[1:] if len(coefficient) > 1 else '') + 'e' + str(adjusted)
    output = ('-' if sign else '') + output
    require(len(output) <= 1024, 'canonical decimal token', 'resource_limit')
    return output

# Reject lexical bounds before Decimal expansion and retain strict integer token semantics.
def _number(token, integer=False):
    require(len(token) <= 1024, 'numeric token', 'resource_limit')
    parts = re.fullmatch(r'(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?', token)
    require(parts is not None, 'numeric spelling')
    require(len(parts[2]) + len(parts[3] or '') <= 1024, 'coefficient digits', 'resource_limit')
    exponent = (parts[4] or '0').lstrip('+-').lstrip('0') or '0'
    require(len(exponent) <= 4 and int(exponent) <= 5120, 'explicit exponent', 'resource_limit')
    number = Decimal(token)
    decimal_text(number)
    return int(token) if integer and token != '-0' else number

# Charge aggregate elements, scalar UTF8 strings and depth before canonical serialization.
def bounded(value, depth=0, counter=None):
    counter = [0] if counter is None else counter
    if isinstance(value, (dict, list)):
        depth += 1
        require(depth <= 32, 'JSON depth', 'resource_limit')
        counter[0] += len(value)
        require(counter[0] <= 65536, 'JSON aggregate elements', 'resource_limit')
        values = value.items() if isinstance(value, dict) else enumerate(value)
        for key, child in values:
            if isinstance(value, dict):
                bounded(key, depth, counter)
            bounded(child, depth, counter)
    elif isinstance(value, str):
        require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), 'lone surrogate')
        require(len(value.encode('utf-8')) <= 16384, 'UTF8 string', 'resource_limit')
    elif type(value) in (Decimal, int):
        decimal_text(value)
    else:
        require(value is None or type(value) is bool, 'not an exact JSON value')

# Parse exact UTF8 JSON, rejecting duplicate decoded keys and nonfinite numbers.
def loads(data, root_object=True):
    # Duplicate key detection uses decoded strings, including escaped aliases.
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate decoded JSON key')
            result[key] = value
        return result
    require(len(data if isinstance(data,bytes) else data.encode('utf-8',errors='surrogatepass'))<=MAX_JSON_BYTES,'metadata JSON bytes','resource_limit')
    try:
        if isinstance(data, bytes):
            data = data.decode('utf-8', errors='strict')
        require(not data.startswith('\ufeff'), 'JSON BOM')
        result = json.loads(data, object_pairs_hook=pairs,
                            parse_int=lambda t: _number(t, True), parse_float=_number,
                            parse_constant=lambda t: require(False, 'nonfinite JSON ' + t))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ContractError('invalid_metadata', 'JSON syntax/UTF8') from error
    require(not root_object or isinstance(result, dict), 'JSON root object')
    bounded(result)
    return result

# Emit accepted scalar-key ordering and exact decimal spelling without binary float conversion.
def dumps(value):
    bounded(value)
    # Recursively emit already bounded JSON values without Unicode normalization.
    def emit(v):
        if v is None: return 'null'
        if type(v) is bool: return 'true' if v else 'false'
        if type(v) in (int, Decimal): return decimal_text(v)
        if isinstance(v, str):
            return '"' + ''.join('\\"' if c == '"' else '\\\\' if c == '\\' else '\\u%04x' % ord(c) if ord(c) < 32 else c for c in v) + '"'
        if isinstance(v, list): return '[' + ','.join(emit(x) for x in v) + ']'
        return '{' + ','.join(emit(k) + ':' + emit(v[k]) for k in sorted(v)) + '}'
    output=emit(value).encode('utf-8')
    require(len(output)<=MAX_JSON_BYTES,'canonical JSON bytes','resource_limit')
    return output

class Shapes:
    """Local official shape interpreter plus lexical primitives; no remote resolution."""
    # Initialize exact error/schema state without loading any remote dependency.
    def __init__(self, schema_path=None):
        path = schema_path or Path(__file__).with_name('schema.json')
        # The package lives tools/lmtr_v1: parents[2] is repository root.
        self.defs = json.loads(Path(path).read_text())['$defs']
    # Keep opening, selected access, full integrity and prefix recovery as distinct scopes.
    def check(self, name, value):
        self._check({'$ref': '#/$defs/' + name}, value, name)
    # Apply frozen local schema shapes; semantic and byte integrity require separate validation.
    def _check(self, shape, value, path):
        if '$ref' in shape:
            name = shape['$ref'].split('/')[-1]
            self._check(self.defs[name], value, path)
            if name in ('D', 'PositiveD'):
                require(int(value) <= MAX63, path + ': D overflow', 'resource_limit')
            if name == 'UUID': require(value != '00000000-0000-0000-0000-000000000000', path + ': nil UUID')
            if name in ('Id', 'Namespace'): require('..' not in value, path + ': dot traversal')
            if name == 'Utc':
                try: datetime.datetime.fromisoformat(value[:-1] + '+00:00')
                except ValueError: require(False, path + ': invalid UTC date')
            self._check({k:v for k,v in shape.items() if k != '$ref'}, value, path)
        for kind in ('oneOf', 'anyOf'):
            if kind in shape:
                hits = 0
                for variant in shape[kind]:
                    try: self._check(variant, value, path); hits += 1
                    except ContractError: pass
                require(hits == 1 if kind == 'oneOf' else hits > 0, path + ': union')
        for part in shape.get('allOf', []): self._check(part, value, path)
        if 'not' in shape:
            try: self._check(shape['not'], value, path)
            except ContractError: pass
            else: require(False, path + ': forbidden union field')
        if 'const' in shape: require(type(value) is type(shape['const']) and value == shape['const'], path + ': const')
        if 'enum' in shape: require(any(type(value) is type(x) and value == x for x in shape['enum']), path + ': enum')
        types = {'integer': type(value) is int, 'number': type(value) in (int, Decimal), 'object': isinstance(value, dict), 'array': isinstance(value, list), 'string': isinstance(value, str), 'boolean': type(value) is bool, 'null': value is None}
        if 'type' in shape: require(types[shape['type']], path + ': type/lexical token')
        if isinstance(value, dict):
            require(all(k in value for k in shape.get('required', [])), path + ': required')
            require(len(value) <= shape.get('maxProperties', MAX63), path + ': properties', 'resource_limit')
            for key, item in value.items():
                if 'propertyNames' in shape: self._check(shape['propertyNames'], key, path)
                rule = shape.get('properties', {}).get(key, shape.get('additionalProperties', True))
                require(rule is not False, path + ': unexpected property')
                if isinstance(rule, dict): self._check(rule, item, path + '.' + key)
        if isinstance(value, list):
            require(shape.get('minItems', 0) <= len(value) <= shape.get('maxItems', MAX63), path + ': list size')
            if shape.get('uniqueItems'): require(len({dumps(x) for x in value}) == len(value), path + ': unique')
            for item in value:
                if 'items' in shape: self._check(shape['items'], item, path + '[]')
        if isinstance(value, str):
            require(shape.get('minLength', 0) <= len(value) <= shape.get('maxLength', MAX63), path + ': string length')
            if 'pattern' in shape: require(re.fullmatch(shape['pattern'], value) is not None, path + ': pattern')
        if type(value) in (int, Decimal):
            require(shape.get('minimum', -MAX63) <= value <= shape.get('maximum', Decimal('1e4096')), path + ': numeric bound')
