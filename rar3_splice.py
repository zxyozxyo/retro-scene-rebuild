"""RAR 3.x LZ streams read into their decisions and written back bit for bit.

A RAR 3 stream that differs from a replay in ONE match decision differs from
it in every later byte: a block is 16,384 decisions long, so one more or one
fewer moves every later block boundary, and each block carries Huffman tables
built from its own contents. A byte patch cannot follow that (measured:
Guitar_Rock_Tour_EUR_MULTi6_NDS-BAHAMUT is rar 3.60 -mt2 for all 94,433,093
decisions of its image but one, and the two streams never line up again).

So the difference is carried where it is small -- as DECISIONS -- and the
original's bytes are written again from the replay's decisions with those few
replaced:

    for tokens, keep, end in read_blocks(stream): ...     # decode
    Writer(sink).block(tokens, keep, end)                  # encode

    patch = diff(original_stream, replayed_stream, cap)    # capture
    apply(replayed_stream, patch, sink)                    # rebuild

What rar does, worked out from its output and checked on thousands of blocks
of rar 3.42 and 3.60 -mt2:
  * the four Huffman tables of a block come from that block's own symbol
    counts through LHA's make_tree, to 15 bits;
  * tables are sent as differences from the previous block's, zero runs and
    repeats run-length coded, under a 20-symbol code built the same way;
  * the form a match is written in (new, one of four remembered distances,
    repeat of the last, two-byte short) follows from its length and distance.
What is NOT derived: where rar ends a block early around multimedia data.
The original's block lengths are therefore part of the patch.

Text (PPM) blocks are not decoded either: each is carried as its own bytes
(see _ppm_end) -- a replay of the right build writes the same ones.
Solid continuation streams are not handled (the tables and distance history
of the previous file would be needed).
"""
from __future__ import annotations

import base64
import bisect
import json
import zlib

NC, DC, LDC, RC, BC = 299, 60, 17, 28, 20
LDEC = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56,
        64, 80, 96, 112, 128, 160, 192, 224]
LBITS = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4,
         4, 5, 5, 5, 5]
DDEC, DBITS = [], []
_d, _b = 0, 0
for _c in (4, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 14, 0, 12):
    for _ in range(_c):
        DDEC.append(_d)
        DBITS.append(_b)
        _d += 1 << _b
    _b += 1
SDDEC = [0, 4, 8, 16, 32, 64, 128, 192]
SDBITS = [2, 2, 3, 4, 5, 6, 6, 6]


N = NC + DC + LDC + RC


def codes(lengths):
    """Canonical codes: {sym: (code, length)}; and a 15-bit lookup."""
    out = {}
    look = [(0, 0)] * (1 << 15)
    code = 0
    for L in range(1, 16):
        for sym, ln in enumerate(lengths):
            if ln == L:
                out[sym] = (code, L)
                pre = code << (15 - L)
                for i in range(1 << (15 - L)):
                    look[pre | i] = (sym, L)
                code += 1
        code <<= 1
    return out, look



class Bits:
    def __init__(self):
        self.acc = 0
        self.n = 0
        self.out = bytearray()

    def put(self, v, nb):
        if not nb:
            return
        self.acc = (self.acc << nb) | (v & ((1 << nb) - 1))
        self.n += nb
        while self.n >= 8:
            self.n -= 8
            self.out.append((self.acc >> self.n) & 0xFF)
        self.acc &= (1 << self.n) - 1

    def align(self, v=0):
        if self.n:
            pad = 8 - self.n
            self.put(v, pad)

    def bytes(self):
        if self.n:
            return bytes(self.out) + bytes([(self.acc << (8 - self.n)) & 0xFF])
        return bytes(self.out)



def lha_tree(fr, maxlen=15):
    """Code lengths for the counts `fr`, exactly as LHA's make_tree gives
    them (heap order, the order symbols leave it, make_len) -- which is
    what rar 3.x writes. One used symbol gets a dummy partner."""
    n = len(fr); freq = list(fr) + [0] * (n + 2); L = [0] * n
    heap = [0] * (n + 2); hs = 0
    for i in range(n):
        if freq[i]:
            hs += 1; heap[hs] = i
    if hs < 2:
        # rar completes a one-symbol code with a dummy second symbol
        if hs == 1:
            L[heap[1]] = 1
            L[1 if heap[1] == 0 else 0] = 1
        return L
    def down(i):
        k = heap[i]
        while True:
            j = 2 * i
            if j > hs: break
            if j < hs and freq[heap[j]] > freq[heap[j + 1]]: j += 1
            if freq[k] <= freq[heap[j]]: break
            heap[i] = heap[j]; i = j
        heap[i] = k
    for i in range(hs // 2, 0, -1): down(i)
    order = []; left = {}; right = {}; avail = n
    while True:
        i = heap[1]
        if i < n: order.append(i)
        heap[1] = heap[hs]; hs -= 1; down(1)
        j = heap[1]
        if j < n: order.append(j)
        k = avail; avail += 1
        freq[k] = freq[i] + freq[j]
        heap[1] = k; down(1)
        left[k] = i; right[k] = j
        if hs <= 1: break
    cnt = [0] * (maxlen + 2)
    def count(node, d):
        stack = [(node, d)]
        while stack:
            x, dd = stack.pop()
            if x < n: cnt[min(dd, maxlen)] += 1
            else:
                stack.append((left[x], dd + 1)); stack.append((right[x], dd + 1))
    count(k, 0)
    cum = sum(cnt[i] << (maxlen - i) for i in range(maxlen, 0, -1))
    while cum != (1 << maxlen):
        cnt[maxlen] -= 1
        for i in range(maxlen - 1, 0, -1):
            if cnt[i]:
                cnt[i] -= 1; cnt[i + 1] += 2; break
        cum -= 1
    p = 0
    for i in range(maxlen, 0, -1):
        for _ in range(cnt[i]):
            L[order[p]] = i; p += 1
    return L

def code_table(t, old, keep):
    """[(symbol, extra value, extra bits)]: the 404 code lengths as rar sends them."""
    out = []; i = 0
    while i < N:
        if t[i] == 0:
            z = 1
            while i + z < N and t[i + z] == 0: z += 1
            if z >= 3:
                while z >= 3:
                    c = min(z, 138)
                    out.append((18, c - 3, 3) if c <= 10 else (19, c - 11, 7))
                    i += c; z -= c
                continue
        d = (t[i] - (old[i] if keep else 0)) & 15
        out.append((d, 0, 0)); i += 1
        r = 0
        while i + r < N and t[i + r] == t[i - 1] and t[i - 1] != 0: r += 1
        while r >= 3:
            c = min(r, 138)
            out.append((16, c - 3, 3) if c <= 10 else (17, c - 11, 7))
            i += c; r -= c
    return out
def code_bl(bl):
    out = []; i = 0
    while i < 20:
        if bl[i] == 0:
            z = 1
            while i + z < 20 and bl[i + z] == 0: z += 1
            if z >= 3:
                c = min(z, 17); out.append((15, c - 2)); i += c; continue
        out.append((15, 0) if bl[i] == 15 else (bl[i], None)); i += 1
    return out

def _complete(lengths) -> bool:
    """A code rar can have written: every prefix used (lha_tree always
    completes its code), or nothing at all."""
    k = sum(1 << (15 - L) for L in lengths if L)
    return k == 0 or k == 1 << 15


def _lz_tables_at(buf, q, old, nbits):
    """(tables, bit position after them) if an LZ table block that rar could
    have written starts at byte q, else None. `old` is the table a keep flag
    refers to."""
    pos = q * 8

    def get(n):
        nonlocal pos
        i = pos >> 3
        v = (int.from_bytes(buf[i:i + 4], "big") >> (32 - (pos & 7) - n)) & ((1 << n) - 1)
        pos += n
        return v

    if buf[q] & 0x80:
        return None
    keep = bool(buf[q] & 0x40)
    get(2)
    bl = [0] * BC
    i = 0
    while i < BC:
        ln = get(4)
        if ln == 15:
            z = get(4)
            if z == 0:
                bl[i] = 15
            else:
                i += z + 2
                continue
        else:
            bl[i] = ln
        i += 1
    if i != BC or not _complete(bl) or not any(bl):
        return None
    bd = codes(bl)[1]
    t = [0] * N
    i = 0
    while i < N:
        if pos >= nbits:
            return None
        s, ln = bd[(int.from_bytes(buf[pos >> 3:(pos >> 3) + 4], "big")
                    >> (17 - (pos & 7))) & 0x7FFF]
        if not ln:
            return None
        pos += ln
        if s < 16:
            t[i] = (s + (old[i] if keep else 0)) & 15
            i += 1
        elif s < 18:
            if i == 0:
                return None
            c = get(3) + 3 if s == 16 else get(7) + 11
            while c > 0 and i < N:
                t[i] = t[i - 1]; i += 1; c -= 1
        else:
            c = get(3) + 3 if s == 18 else get(7) + 11
            while c > 0 and i < N:
                t[i] = 0; i += 1; c -= 1
    parts = (t[:NC], t[NC:NC + DC], t[NC + DC:NC + DC + LDC], t[NC + DC + LDC:])
    if not all(_complete(p) for p in parts) or not t[256]:
        return None
    return t, pos


def _ppm_end(buf, s, old, nbits, first_block_ok):
    """The byte where LZ coding resumes after a text (PPM) block that starts
    at byte s -- or None when the stream ends inside it.

    The PPM model is not decoded. A PPM block ends on its escape code and the
    LZ tables that follow begin byte-aligned (the range coder reads whole
    bytes), so every later byte is a candidate, and a candidate is believed
    only when its tables are four complete codes rar's make_tree could have
    built AND a whole block decodes from them up to its end-of-block symbol.
    Whatever is wrong with that guess, the stream written back from the
    decisions is compared with the original byte for byte."""
    for q in range(s + 5, nbits >> 3):
        if buf[q] & 0x80:
            continue
        got = _lz_tables_at(buf, q, old, nbits)
        if got is None:
            continue
        if first_block_ok(q, got):
            return q
    return None


def parse_iter(data):
    """Yield each block dict as soon as it is complete (see rar3enc.parse).
    A text (PPM) block is yielded as {"ppm": its bytes} -- carried whole,
    never decoded."""
    buf = bytes(data) + b"\0" * 8
    nbits = len(data) * 8
    pos = 0

    def peek(n):
        i = pos >> 3
        return (int.from_bytes(buf[i:i + 4], "big") >> (32 - (pos & 7) - n)) & ((1 << n) - 1)

    def get(n):
        nonlocal pos
        v = peek(n)
        pos += n
        return v

    def dec(look):
        nonlocal pos
        s, ln = look[peek(15)]
        if not ln:
            raise ValueError(f"bad code at bit {pos}")
        pos += ln
        return s

    old = [0] * (NC + DC + LDC + RC)
    blocks = []
    st = {"prevlow": 0, "lowrep": 0}
    look = {}

    def block_ok(q, got):
        """A whole LZ block decodes from the tables found at byte q."""
        t, p = got
        LD = codes(t[:NC])[1]; DD = codes(t[NC:NC + DC])[1]
        LDD = codes(t[NC + DC:NC + DC + LDC])[1]; RD = codes(t[NC + DC + LDC:])[1]

        def pk(n, at):
            i = at >> 3
            return (int.from_bytes(buf[i:i + 4], "big") >> (32 - (at & 7) - n)) & ((1 << n) - 1)

        def dc(look, at):
            s, ln = look[pk(15, at)]
            if not ln:
                raise ValueError
            return s, at + ln

        lowrep = 0
        try:
            for _ in range(70000):
                if p >= nbits:
                    return False
                n, p = dc(LD, p)
                if n < 256:
                    continue
                if n >= 271:
                    k = n - 271
                    p += LBITS[k]
                    dn, p = dc(DD, p)
                    b = DBITS[dn]
                    if b:
                        if dn > 9:
                            if b > 4:
                                p += b - 4
                            if lowrep > 0:
                                lowrep -= 1
                            else:
                                low, p = dc(LDD, p)
                                if low == 16:
                                    lowrep = 15
                        else:
                            p += b
                    continue
                if n == 256:
                    return p <= nbits
                if n == 257:
                    fb = pk(8, p); p += 8
                    ln = (fb & 7) + 1
                    if ln == 7:
                        ln = pk(8, p) + 7; p += 8
                    elif ln == 8:
                        ln = pk(16, p); p += 16
                    p += 8 * ln
                    continue
                if n == 258:
                    continue
                if n < 263:
                    rn, p = dc(RD, p)
                    p += LBITS[rn]
                    continue
                p += SDBITS[n - 263]
        except (ValueError, IndexError):
            return False
        return False

    def tables():
        nonlocal pos
        pad = (8 - (pos & 7)) & 7
        padv = get(pad) if pad else 0
        at0 = pos >> 3
        bf = peek(16)
        if bf & 0x8000:
            s = pos >> 3
            q = _ppm_end(buf, s, old, nbits, block_ok)
            end = q if q is not None else nbits >> 3
            blocks.append({"ppm": bytes(buf[s:end]), "keep": True,
                           "pad": (padv, pad), "end": None, "syms": [], "at": s})
            pos = end * 8
            return q is not None
        keep = bool(bf & 0x4000)
        st["prevlow"] = 0
        st["lowrep"] = 0
        if not keep:
            old[:] = [0] * len(old)
        get(2)
        bl = [0] * BC
        blraw = []
        i = 0
        while i < BC:
            ln = get(4)
            if ln == 15:
                z = get(4)
                blraw.append((15, z))
                if z == 0:
                    bl[i] = 15
                else:
                    z += 2
                    while z > 0 and i < BC:
                        bl[i] = 0
                        i += 1
                        z -= 1
                    i -= 1
            else:
                blraw.append((ln, None))
                bl[i] = ln
            i += 1
        _c, bd = codes(bl)
        size = NC + DC + LDC + RC
        t = [0] * size
        ts = []
        i = 0
        while i < size:
            n = dec(bd)
            if n < 16:
                t[i] = (n + old[i]) & 15
                i += 1
                ts.append((n, 0, 0))
            elif n < 18:
                c, nb = (get(3) + 3, 3) if n == 16 else (get(7) + 11, 7)
                ts.append((n, c - (3 if n == 16 else 11), nb))
                while c > 0 and i < size:
                    t[i] = t[i - 1]
                    i += 1
                    c -= 1
            else:
                c, nb = (get(3) + 3, 3) if n == 18 else (get(7) + 11, 7)
                ts.append((n, c - (3 if n == 18 else 11), nb))
                while c > 0 and i < size:
                    t[i] = 0
                    i += 1
                    c -= 1
        look["LD"] = codes(t[:NC])[1]
        look["DD"] = codes(t[NC:NC + DC])[1]
        look["LDD"] = codes(t[NC + DC:NC + DC + LDC])[1]
        look["RD"] = codes(t[NC + DC + LDC:])[1]
        old[:] = t
        blocks.append({"keep": keep, "bl": bl, "blraw": blraw, "tab": list(t),
                       "tabsyms": ts, "syms": [], "end": None, "pad": (padv, pad), "at": at0})
        return True

    def lz_next():
        """Read tables; yield any text blocks met on the way. False at the
        end of the stream."""
        while True:
            more = tables()
            if "ppm" not in blocks[-1]:
                return True
            yield blocks.pop()
            if not more:
                return False

    if not (yield from lz_next()):
        return
    while pos < nbits:
        syms = blocks[-1]["syms"]
        n = dec(look["LD"])
        syms.append(("L", n))
        if n < 256:
            continue
        if n >= 271:
            k = n - 271
            if LBITS[k]:
                syms.append(("X", get(LBITS[k]), LBITS[k]))
            dn = dec(look["DD"])
            syms.append(("D", dn))
            b = DBITS[dn]
            if b:
                if dn > 9:
                    if b > 4:
                        syms.append(("X", get(b - 4), b - 4))
                    if st["lowrep"] > 0:
                        st["lowrep"] -= 1
                    else:
                        low = dec(look["LDD"])
                        syms.append(("W", low))
                        if low == 16:
                            st["lowrep"] = 15
                        else:
                            st["prevlow"] = low
                else:
                    syms.append(("X", get(b), b))
            continue
        if n == 256:
            bf = peek(16)
            if bf & 0x8000:
                blocks[-1]["end"] = (get(1), 1)
                yield blocks.pop()
                if not (yield from lz_next()):
                    return
                continue
            blocks[-1]["end"] = (get(2), 2)
            yield blocks.pop()
            return
        if n == 257:
            fb = get(8)
            syms.append(("X", fb, 8))
            ln = (fb & 7) + 1
            if ln == 7:
                v = get(8)
                syms.append(("X", v, 8))
                ln = v + 7
            elif ln == 8:
                v = get(16)
                syms.append(("X", v, 16))
                ln = v
            for _ in range(ln):
                syms.append(("X", get(8), 8))
            continue
        if n == 258:
            continue
        if n < 263:
            ln_n = dec(look["RD"])
            syms.append(("R", ln_n))
            if LBITS[ln_n]:
                syms.append(("X", get(LBITS[ln_n]), LBITS[ln_n]))
            continue
        k = n - 263
        if SDBITS[k]:
            syms.append(("X", get(SDBITS[k]), SDBITS[k]))
    if blocks:
        yield blocks.pop()





class TokDec:
    """Symbols of one block -> tokens ("L", byte) | ("M", len, dist) | ("F", raw)."""
    def __init__(self):
        self.olds = [0, 0, 0, 0]; self.last = 0

    def block(self, b):
        if "ppm" in b:
            return [("P", b["ppm"])]
        olds, last = self.olds, self.last
        toks = []; s = b["syms"]; i = 0; prevlow = 0; lowrep = 0
        while i < len(s):
            n = s[i][1]; i += 1
            if n < 256:
                toks.append(("L", n)); continue
            if n >= 271:
                k = n - 271; ln = LDEC[k] + 3
                if LBITS[k]:
                    ln += s[i][1]; i += 1
                dn = s[i][1]; i += 1
                dist = DDEC[dn] + 1; bb = DBITS[dn]
                if bb:
                    if dn > 9:
                        if bb > 4:
                            dist += s[i][1] << 4; i += 1
                        if lowrep > 0:
                            lowrep -= 1; dist += prevlow
                        else:
                            low = s[i][1]; i += 1
                            if low == 16:
                                lowrep = 15; dist += prevlow
                            else:
                                dist += low; prevlow = low
                    else:
                        dist += s[i][1]; i += 1
                if dist >= 0x2000:
                    ln += 1
                    if dist >= 0x40000:
                        ln += 1
                olds = [dist] + olds[:3]; last = ln; toks.append(("M", ln, dist)); continue
            if n == 256:
                continue
            if n == 257:
                j = i; fb = s[i][1]; i += 1; ln = (fb & 7) + 1
                if ln == 7:
                    ln = s[i][1] + 7; i += 1
                elif ln == 8:
                    ln = s[i][1]; i += 1
                i += ln; toks.append(("F", s[j:i])); continue
            if n == 258:
                toks.append(("M", last, olds[0])); continue
            if n < 263:
                k = n - 259; dist = olds[k]; olds = [dist] + olds[:k] + olds[k + 1:]
                rn = s[i][1]; i += 1; ln = LDEC[rn] + 2
                if LBITS[rn]:
                    ln += s[i][1]; i += 1
                last = ln; toks.append(("M", ln, dist)); continue
            k = n - 263; dist = SDDEC[k] + 1
            if SDBITS[k]:
                dist += s[i][1]; i += 1
            olds = [dist] + olds[:3]; last = 2; toks.append(("M", 2, dist))
        self.olds, self.last = olds, last
        return toks



def _len_sym(v, base):
    k = bisect.bisect_right(LDEC, v - base) - 1
    return k, v - base - LDEC[k], LBITS[k]


class SymEnc:
    """Tokens of one block -> the symbols rar writes for them."""
    def __init__(self):
        self.olds = [0, 0, 0, 0]; self.last = 0

    def block(self, toks):
        olds, last = self.olds, self.last
        s = []; far = []
        for t in toks:
            if t[0] == "L":
                s.append(("L", t[1])); continue
            if t[0] == "F":
                s.append(("L", 257)); s.extend(t[1]); continue
            ln, dist = t[1], t[2]
            if dist == olds[0] and ln == last and last:
                s.append(("L", 258)); continue
            if dist in olds:
                k = olds.index(dist)
                s.append(("L", 259 + k)); olds = [dist] + olds[:k] + olds[k + 1:]
                rn, ev, eb = _len_sym(ln, 2); s.append(("R", rn))
                if eb:
                    s.append(("X", ev, eb))
                last = ln; continue
            if ln == 2:
                k = bisect.bisect_right(SDDEC, dist - 1) - 1
                s.append(("L", 263 + k))
                if SDBITS[k]:
                    s.append(("X", dist - 1 - SDDEC[k], SDBITS[k]))
                olds = [dist] + olds[:3]; last = 2; continue
            base = ln - (1 if dist >= 0x2000 else 0) - (1 if dist >= 0x40000 else 0)
            k, ev, eb = _len_sym(base, 3); s.append(("L", 271 + k))
            if eb:
                s.append(("X", ev, eb))
            dn = bisect.bisect_right(DDEC, dist - 1) - 1; s.append(("D", dn))
            rem = dist - 1 - DDEC[dn]; bb = DBITS[dn]
            if bb:
                if dn > 9:
                    if bb > 4:
                        s.append(("X", rem >> 4, bb - 4))
                    far.append(len(s)); s.append(("W", rem & 15))
                else:
                    s.append(("X", rem, bb))
            olds = [dist] + olds[:3]; last = ln
        # repeat run: 16 = this low distance and the next 15 equal the last
        # one written out, and only directly after one written out in full
        drop = set(); p = 0; prevlow = 0; explicit = False
        while p < len(far):
            low = s[far[p]][1]
            if (explicit and low == prevlow and p + 16 < len(far)
                    and all(s[far[q]][1] == prevlow for q in range(p, p + 16))):
                s[far[p]] = ("W", 16)
                drop.update(far[p + 1:p + 16])
                p += 16; explicit = False; continue
            prevlow = low; explicit = True; p += 1
        self.olds, self.last = olds, last
        return [x for i, x in enumerate(s) if i not in drop] if drop else s




def read_blocks(stream):
    """Yield (tokens, keep, end) for every block of one file's stream.
    tokens: ("L", byte) | ("M", length, distance) | ("F", raw filter code)."""
    td = TokDec()
    for b in parse_iter(stream):
        yield td.block(b), b["keep"], b["end"]


class Writer:
    """Blocks of tokens -> rar's bytes, appended to `sink` (a bytearray, or
    anything with .write)."""

    def __init__(self, sink=None):
        self.w = Bits()
        self.se = SymEnc()
        self.old = [0] * N
        self.sink = sink
        self.total = 0

    def _flush(self, final=False):
        w = self.w
        if self.sink is None:
            return
        chunk = w.bytes() if final else bytes(w.out)
        if hasattr(self.sink, "write"):
            self.sink.write(chunk)
        else:
            self.sink.extend(chunk)
        self.total += len(chunk)
        w.out = bytearray()
        if final:
            w.acc = 0
            w.n = 0

    def block(self, toks, keep, end):
        if len(toks) == 1 and toks[0][0] == "P":
            # a text block: its bytes as they were, byte-aligned, and the
            # LZ state on either side of it untouched
            w = self.w
            w.align(0)
            w.out += toks[0][1]
            if len(w.out) >= 1 << 20:
                self._flush()
            return
        syms = self.se.block(toks)
        fr = {"L": [0] * NC, "D": [0] * DC, "W": [0] * LDC, "R": [0] * RC}
        for s in syms:
            if s[0] != "X":
                fr[s[0]][s[1]] += 1
        fr["L"][256] += 1                      # the end-of-block symbol
        t = (lha_tree(fr["L"]) + lha_tree(fr["D"]) + lha_tree(fr["W"])
             + lha_tree(fr["R"]))
        ts = code_table(t, self.old, keep)
        bfr = [0] * 20
        for s, _e, _n in ts:
            bfr[s] += 1
        bl = lha_tree(bfr)
        w = self.w
        w.align(0)
        w.put(0, 1)
        w.put(1 if keep else 0, 1)
        for ln, z in code_bl(bl):
            w.put(ln, 4)
            if ln == 15:
                w.put(z, 4)
        bc = codes(bl)[0]
        for n, extra, nb in ts:
            c, L = bc[n]
            w.put(c, L)
            if nb:
                w.put(extra, nb)
        C = {"L": codes(t[:NC])[0], "D": codes(t[NC:NC + DC])[0],
             "W": codes(t[NC + DC:NC + DC + LDC])[0],
             "R": codes(t[NC + DC + LDC:])[0]}
        for s in syms:
            if s[0] == "X":
                w.put(s[1], s[2])
            else:
                c, L = C[s[0]][s[1]]
                w.put(c, L)
        c, L = C["L"][256]
        w.put(c, L)
        w.put(end[0], end[1])
        self.old = t
        if len(w.out) >= 1 << 20:
            self._flush()

    def close(self):
        self._flush(final=True)

    def bytes(self):
        return self.w.bytes()


def _tok_len(t) -> int:
    return 1 if t[0] == "L" else t[1] if t[0] == "M" else 0


def _flat(stream):
    for toks, _keep, _end in read_blocks(stream):
        yield from toks


def _pack_tokens(toks) -> list:
    out = []
    for t in toks:
        if t[0] == "L":
            out.append(t[1])
        elif t[0] == "M":
            out.append([t[1], t[2]])
        elif t[0] == "P":
            out.append({"p": base64.b64encode(t[1]).decode("ascii")})
        else:
            out.append({"f": [[x[1], x[2]] for x in t[1]]})
    return out


def _unpack_tokens(raw) -> list:
    out = []
    for t in raw:
        if isinstance(t, int):
            out.append(("L", t))
        elif isinstance(t, list):
            out.append(("M", t[0], t[1]))
        elif "p" in t:
            out.append(("P", base64.b64decode(t["p"])))
        else:
            out.append(("F", [("X", v, nb) for v, nb in t["f"]]))
    return out


def diff(original: bytes, replayed: bytes, max_tokens: int = 200_000):
    """The patch (bytes) that turns `replayed`'s decisions into `original`'s,
    or None when more than `max_tokens` decisions would have to be carried.
    Raises ValueError for a stream that cannot be decoded.

    Both streams decode to the same file, so each is a way of cutting the
    same run of output bytes into tokens. They are walked side by side by
    OUTPUT POSITION; a span opens where the next tokens differ and closes
    where both sides stand at the same position with the same token next.
    Within a span the original's tokens are carried and the replay's dropped.
    Each span is [index of the replay's first dropped token, how many are
    dropped, the original's tokens]."""
    layout, keeps = [], []
    last_end = [None]

    def orig_iter():
        for toks, keep, e in read_blocks(original):
            layout.append(len(toks))
            keeps.append(bool(keep))
            last_end[0] = e
            yield from toks

    A, B = orig_iter(), _flat(replayed)
    spans = []
    carried = 0
    pa = pb = 0                        # output positions
    ib = 0                             # index of the replay's next token
    ta, tb = next(A, None), next(B, None)
    cur = None                         # [first replay index, dropped, tokens]
    while ta is not None or tb is not None:
        if cur is None and pa == pb and ta == tb:
            pa += _tok_len(ta)
            pb = pa
            ib += 1
            ta, tb = next(A, None), next(B, None)
            continue
        if cur is None:
            cur = [ib, 0, []]
        # inside a differing span: bring the side that is behind forward
        if tb is None or (ta is not None and pa <= pb):
            cur[2].append(ta)
            carried += 1
            if carried > max_tokens:
                return None
            pa += _tok_len(ta)
            ta = next(A, None)
        else:
            cur[1] += 1
            pb += _tok_len(tb)
            ib += 1
            tb = next(B, None)
        if pa == pb and ta == tb:
            spans.append(cur)
            cur = None
    if cur is not None:
        spans.append(cur)
    runs = []                          # block lengths, run-length coded
    for n in layout:
        if runs and runs[-1][0] == n:
            runs[-1][1] += 1
        else:
            runs.append([n, 1])
    end = last_end[0]
    doc = {"v": 1, "blocks": runs,
           "nokeep": [i for i, k in enumerate(keeps) if not k],
           "end": list(end) if end else [0, 2],
           "spans": [[s[0], s[1], _pack_tokens(s[2])] for s in spans],
           "carried": carried}
    return zlib.compress(json.dumps(doc, separators=(",", ":")).encode(), 9)


def info(patch: bytes) -> dict:
    doc = json.loads(zlib.decompress(patch))
    return {"spans": len(doc["spans"]), "carried": doc.get("carried", 0),
            "blocks": sum(n for _c, n in doc["blocks"])}


def apply(replayed: bytes, patch: bytes, sink) -> int:
    """Write the original stream to `sink`; returns its length."""
    doc = json.loads(zlib.decompress(patch))
    spans = [(s[0], s[1], _unpack_tokens(s[2])) for s in doc["spans"]]
    nokeep = set(doc["nokeep"])
    end = tuple(doc["end"])

    def patched():
        si = 0
        drop = 0
        ib = 0
        for t in _flat(replayed):
            while si < len(spans) and spans[si][0] == ib and not drop:
                yield from spans[si][2]
                drop = spans[si][1]
                si += 1
                if drop:
                    break
            ib += 1
            if drop:
                drop -= 1
                continue
            yield t
        while si < len(spans):         # spans that begin at the very end
            yield from spans[si][2]
            si += 1

    w = Writer(sink)
    it = patched()
    total_blocks = sum(n for _c, n in doc["blocks"])
    bi = 0
    for count, times in doc["blocks"]:
        for _ in range(times):
            blk = []
            for _i in range(count):
                blk.append(next(it))
            last = bi == total_blocks - 1
            w.block(blk, bi not in nokeep, end if last else (1, 1))
            bi += 1
    if next(it, None) is not None:
        raise ValueError("the replay has more decisions than the patch "
                         "accounts for")
    w.close()
    return w.total
