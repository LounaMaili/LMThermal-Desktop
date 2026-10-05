"""Bounded strict UTF-8 JSON; unknown numbers retain their exact decimal value."""

from decimal import Decimal, InvalidOperation
import json
from lmtx_container import LmtxError, MAX_JSON, require


def decode(content):
    require(len(content) <= MAX_JSON, 'JSON exceeds 1 MiB', 'resource_limit')
    try:
        text = content.decode('utf-8')
        require(not text.startswith('\ufeff'), 'JSON BOM is forbidden', 'invalid_manifest')
        # Bound nesting before the recursive standard-library decoder is called.
        depth = 0
        quoted = escaped = False
        for c in text:
            if quoted:
                if escaped: escaped = False
                elif c == '\\': escaped = True
                elif c == '"': quoted = False
            elif c == '"': quoted = True
            elif c in '{[':
                depth += 1
                require(depth <= 32, 'JSON nesting exceeds 32 containers', 'resource_limit')
            elif c in '}]': depth -= 1
        def pairs(items):
            value = {}
            for k, v in items:
                require(k not in value, 'Duplicate JSON key', 'invalid_manifest')
                value[k] = v
            return value
        def number(token, integer=False):
            require(len(token) <= 16384, 'Local 16 KiB numeric-token limit', 'resource_limit')
            value = int(token) if integer and len(token) <= 4000 else Decimal(token)
            require(not isinstance(value, Decimal) or value.is_finite(), 'Numeric overflow', 'invalid_manifest')
            return value
        def constant(token): raise LmtxError('invalid_manifest', 'Non-finite JSON number')
        result = json.loads(text, object_pairs_hook=pairs, parse_float=number,
                            parse_int=lambda t: number(t, True), parse_constant=constant)
        require(isinstance(result, dict), 'JSON document must be an object', 'invalid_manifest')
        pending = [result]
        items = 0
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                items += len(value)
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                items += len(value)
                pending.extend(value)
            elif isinstance(value, str):
                require(len(value.encode('utf-8')) <= 16384, 'JSON string exceeds 16 KiB', 'resource_limit')
            require(items <= 65536, 'JSON exceeds 65536 properties/elements', 'resource_limit')
        return result
    except LmtxError: raise
    except (ValueError, UnicodeError, InvalidOperation, RecursionError) as exc:
        raise LmtxError('invalid_manifest', 'Malformed UTF-8 JSON or numeric overflow') from exc


def dumps(value, indent=2):
    """Display preserved metadata without rounding optional Decimal values."""
    def encode(v, level):
        if isinstance(v, dict):
            entries = [json.dumps(k, ensure_ascii=False)+': '+encode(x, level+1) for k, x in v.items()]
            opening, closing = '{', '}'
        elif isinstance(v, (list, tuple)):
            entries = [encode(x, level+1) for x in v]
            opening, closing = '[', ']'
        elif isinstance(v, Decimal): return str(v)
        else: return json.dumps(v, ensure_ascii=False, allow_nan=False)
        if not entries: return opening+closing
        pad = ' '*(indent*(level+1))
        return opening+'\n'+pad+(',\n'+pad).join(entries)+'\n'+' '*(indent*level)+closing
    return encode(value, 0)
