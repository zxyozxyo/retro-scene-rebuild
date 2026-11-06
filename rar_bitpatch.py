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
NEAR = 256 << 10      # dense resync window (bytes)


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


def _resync_far(a, b, i, d, near, far, win=256, step=128 << 10):
    """_resync for a stretch longer than `near`: one 256 B sample of `a` every
    128 KB from i + near to i + far, looked for in b at all eight bit phases.
    The first hit is walked back to where the two first agree again.

    F1_Career_Challenge_PAL_REPACK_XBOX-CSiSO: stock DOS RAR 2.00 -mm parts
    at byte 581,009,151 and agrees again 6.5 MB on, 115,485 bits behind --
    past the 4 MB the dense search looks through, and that search rescans
    its whole window per 32 B, so it cannot simply be made wider."""
    lo = max(0, 8 * i + d - 8 * (far // 2))
    hi = min(8 * len(b), 8 * (i + far + far // 2) + d)
    nb = (hi - lo) // 8
    phases = [bits_at(b, lo + p, nb) for p in range(8)]
    for j in range(i + near, min(len(a) - win, i + far), step):
        w = a[j:j + win]
        best = None
        for p, ph in enumerate(phases):
            k = ph.find(w)
            if k >= 0:
                at = lo + p + 8 * k
                if best is None or abs(at - 8 * j - d) < abs(best - 8 * j - d):
                    best = at
        if best is None:
            continue
        d2 = best - 8 * j
        # back to the first agreeing byte, a KB at a time and then byte-wise
        while j - 1024 > i and a[j - 1024:j] == bits_at(b, 8 * (j - 1024) + d2,
                                                         1024):
            j -= 1024
        while j > i + 1 and a[j - 1:j] == bits_at(b, 8 * (j - 1) + d2, 1):
            j -= 1
        return j, d2
    return None


def diff(original: bytes, ours: bytes, max_carry: int,
         reach: int = 4 << 20, notes: list | None = None,
         far: int = 16 << 20) -> bytes | None:
    """The patch that turns `ours` into `original`, or None when the streams
    do not agree again within `reach` bytes of a difference or the original's
    bytes it would carry pass `max_carry` -- and then, given `notes`, why."""
    def _no(msg):
        if notes is not None:
            notes.append(msg)
        return None
    edits, i, d, carried = [], 0, 0, 0
    while True:
        i = _first_diff(original, ours, i, d)
        if i is None:
            break
        # Dense (every 32 B) only close by: it searches its whole window per
        # step, so a 4 MB window cost minutes per spot and Frogger_Beyond's
        # 1,251 spots took an hour. Further out, sampled (_resync_far).
        near = min(reach, NEAR)
        r = _resync(original, ours, i, d, near)
        if r is None and max(reach, far) > near and len(original) - i > near:
            r = _resync_far(original, ours, i, d, near, max(reach, far))
        if r is None:
            # Different to the end: carry the rest if it is small.
            j, d2 = len(original), 8 * len(ours) - 8 * len(original)
        else:
            j, d2 = r
        carried += j - i
        if carried > max_carry:
            return _no(f"no agreement again within {max(reach, far):,} B of byte "
                       f"{i:,}" if r is None else
                       f"{len(edits) + 1} spots by byte {j:,} carry "
                       f"{carried:,} B, past the {max_carry:,} B allowed")
        edits.append((i, 8 * i + d, 8 * j + d2, original[i:j]))
        i, d = j, d2
        if r is None:
            break
    out = bytearray(MAGIC + struct.pack("<QI", len(original), len(edits)))
    for s, bs, be, data in edits:
        out += struct.pack("<QQQI", s, bs, be, len(data)) + data
    patch = bytes(out)
    if apply(ours, patch) != original:
        return _no("the patch does not write the original back")
    return patch


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
