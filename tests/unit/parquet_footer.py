"""A tiny Thrift compact writer, enough to build Parquet footers in tests."""
BINARY, LIST, STRUCT, I32, I64 = 8, 9, 12, 5, 6


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _zigzag(value):
    return _varint((value << 1) ^ (value >> 63))


def struct(fields):
    """fields: [(field id, kind, value)] in increasing id order. value of STRUCT is a field list."""
    out, last = bytearray(), 0
    for field, kind, value in fields:
        delta = field - last
        out += bytes([(delta << 4) | kind]) if 0 < delta <= 15 else bytes([kind]) + _zigzag(field)
        out += _value(kind, value)
        last = field
    return bytes(out + b"\x00")


def _value(kind, value):
    if kind in (I32, I64):
        return _zigzag(value)
    if kind == BINARY:
        data = value.encode("utf-8")
        return _varint(len(data)) + data
    if kind == STRUCT:
        return struct(value)
    if kind == LIST:
        element, items = value
        head = bytes([(len(items) << 4) | element]) if len(items) < 15 else bytes([0xF0 | element]) + _varint(len(items))
        return head + b"".join(_value(element, item) for item in items)
    raise ValueError(kind)


def parquet_file(schema, rows):
    """Bytes of a Parquet file with no data pages: magic, footer, length, magic."""
    footer = struct([(1, I32, 1), (2, LIST, (STRUCT, schema)), (3, I64, rows)])
    return b"PAR1" + footer + len(footer).to_bytes(4, "little") + b"PAR1"
