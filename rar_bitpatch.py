"""Bit-level patches between two compressed streams that differ in a few places.

A RAR 2.0 stream is a run of Huffman blocks read MSB first. When the original's
packer made a different decision somewhere -- one match it found that the replay
did not -- the block around it codes differently, and every bit after it moves
by however many bits that block grew or shrank. Nothing else changes: the next
blocks are the same bits again, only no longer at the same offset, so no byte
delta carries it (the shift is rarely a whole byte) and the volumes after it
split in other places.

The patch here needs no knowledge of the format at all. It walks both streams,
and wherever they part it finds the nearest point where they agree again at
some new bit offset, and carries the original's bytes in between. Measured on
Ghost_Recon_XBOX-ProjectX (DOS RAR 2.50 -m1, one 735 MB image): stock 2.50
writes all 578 MB of the stream but six spots in one 75 KB stretch, and 17,469
bytes of the original turn the replay's stream into it.

    patch = b"BIT0" + u64 total + u32 count + count x (u64 orig start,
            u64 our bit start, u64 our bit end, u32 n, n bytes)

An edit puts the original's bytes [start, start + n) where our bits
[bit start, bit end) were; between edits the two agree at the offset the last
edit left."""
import struct

MAGIC = b"BIT0"
_STEP = 1 << 20


def bits_at(buf: bytes, bit: int, n: int) -> bytes:
    """n bytes of buf read from bit offset `bit` (MSB first), or fewer at the
    end -- a last partial byte is padded with zero bits."""
    q, r = divmod(bit, 8)
    if r == 0:
        return bytes(buf[q:q + n])
    chunk = buf[q:q + n + 1]
    have = 8 * len(chunk) - r
    if have <= 0:
        return b""
    keep = min(8 * n, have)
    v = int.from_bytes(chunk, "big") >> (8 * len(chunk) - r - keep)
    v &= (1 << keep) - 1
    pad = -keep % 8
    return (v << pad).to_bytes((keep + pad) // 8, "big")


def _copy(buf, bit, n, out):
    """Append n bytes of buf from bit offset `bit` to out, a MB at a time."""
    while n > 0:
        k = min(n, _STEP)
        piece = bits_at(buf, bit, k)
        if len(piece) != k:
            raise ValueError("the replay's stream ends before the patch does")
        out += piece
        bit += 8 * k
        n -= k


def _first_diff(a, b, i, d):
    """First byte of `a` from i on that differs from b read at bit 8*i + d."""
    while i < len(a):
        x = a[i:i + _STEP]
        y = bits_at(b, 8 * i + d, len(x))
        if x != y:
            k = 0
            while k < len(x) and k < len(y) and x[k] == y[k]:
                k += 1
            return i + k
        i += len(x)
    return None


def _resync(a, b, i, d, reach, win=256):
    """(j, d2): the earliest byte j > i of `a` where it agrees with b again at
    bit 8*j + d2, the offset nearest the old one winning; None within reach."""
    lo = max(0, 8 * i + d - (1 << 20))
    hi = min(8 * len(b), 8 * (i + reach) + d + (1 << 20))
    nb = (hi - lo) // 8
    phases = [bits_at(b, lo + p, nb) for p in range(8)]
    for j in range(i + 1, min(len(a) - win, i + reach), 32):
        w = a[j:j + win]
        hit = None
        for p, ph in enumerate(phases):
            k = ph.find(w)
            if k >= 0:
                at = lo + p + 8 * k
                if hit is None or abs(at - 8 * j - d) < abs(hit - 8 * j - d):
                    hit = at
        if hit is not None:
            d2 = hit - 8 * j
            while j > i + 1 and a[j - 1:j] == bits_at(b, 8 * (j - 1) + d2, 1):
                j -= 1
            return j, d2
    return None


def diff(original: bytes, ours: bytes, max_carry: int,
         reach: int = 4 << 20) -> bytes | None:
    """The patch that turns `ours` into `original`, or None when the streams
    do not agree again within `reach` bytes of a difference or the original's
    bytes it would carry pass `max_carry`."""
    edits, i, d, carried = [], 0, 0, 0
    while True:
        i = _first_diff(original, ours, i, d)
        if i is None:
            break
        r = _resync(original, ours, i, d, reach)
        if r is None:
            # Different to the end: carry the rest if it is small.
            j, d2 = len(original), 8 * len(ours) - 8 * len(original)
        else:
            j, d2 = r
        carried += j - i
        if carried > max_carry:
            return None
        edits.append((i, 8 * i + d, 8 * j + d2, original[i:j]))
        i, d = j, d2
        if r is None:
            break
    out = bytearray(MAGIC + struct.pack("<QI", len(original), len(edits)))
    for s, bs, be, data in edits:
        out += struct.pack("<QQQI", s, bs, be, len(data)) + data
    patch = bytes(out)
    return patch if apply(ours, patch) == original else None


def carried(patch: bytes) -> int:
    """How many of the original's bytes a patch carries."""
    _total, n = struct.unpack_from("<QI", patch, 4)
    pos, got = 16, 0
    for _ in range(n):
        k = struct.unpack_from("<QQQI", patch, pos)[3]
        got += k
        pos += 28 + k
    return got


def apply(ours: bytes, patch: bytes) -> bytes:
    """The original's stream, from the replay's and a patch."""
    if not patch.startswith(MAGIC):
        raise ValueError("not a bit patch")
    total, n = struct.unpack_from("<QI", patch, 4)
    pos, out, at_bit, at = 16, bytearray(), 0, 0
    for _ in range(n):
        s, bs, be, k = struct.unpack_from("<QQQI", patch, pos)
        pos += 28
        _copy(ours, at_bit, s - at, out)
        out += patch[pos:pos + k]
        pos += k
        at_bit, at = be, s + k
    _copy(ours, at_bit, total - at, out)
    return bytes(out)
