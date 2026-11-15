"""Bundled SliceBatch SDK 1: shape checks, physical-coordinate reads."""
def check(buffer, offset, length):
    n = len(buffer["valid"])
    if any(len(buffer[k]) != n for k in ("station", "list_valid")):
        raise ValueError("shape")
    if len(buffer["offsets"]) != n + 1:
        raise ValueError("offset shape")
    if type(offset) is not int or type(length) is not int:
        raise ValueError("slice")
    if offset < 0 or length < 0 or offset + length > n:
        raise ValueError("slice")
    if any(type(v) is not bool for v in buffer["valid"] + buffer["list_valid"]):
        raise ValueError("validity")

def row(buffer, physical_row):
    if not buffer["valid"][physical_row]:
        return None
    station = buffer["station"][physical_row]
    if not buffer["list_valid"][physical_row]:
        return {"station": station, "samples": None}
    a, b = buffer["offsets"][physical_row:physical_row + 2]
    if type(a) is not int or type(b) is not int or not 0 <= a <= b <= len(buffer["values"]):
        raise ValueError("reachable offsets")
    return {"station": station, "samples": buffer["values"][a:b]}
