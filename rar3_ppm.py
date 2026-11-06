"""RAR 3's text (PPM) blocks decoded -- PPMd variant H as UnRAR runs it.

The splice (rar3_splice) carries a text block as its own bytes, but it still
has to know where the block ENDS in the stream and how many bytes of the file
it stands for. Without decoding, both were guessed: the end by trying LZ
tables at every later byte, the length by matches voting on both sides of it.
Colin_McRae_Rally_2005_WORKING_UMDRIP_PSP-LIGHTFORCE and BreakQuest_Extra_
Evolution_PSN_PSP-ABSTRAKT stopped on that vote ("a text block's length could
not be found"). Decoded, both are exact.

A port of UnRAR 6.2.12's model.cpp / suballoc.cpp / coder.cpp (public-domain
PPMd by Dmitry Shkarin, with RAR's carryless range coder by Dmitry Subbotin).
The model's memory is emulated as the 32-bit original laid it out -- 12-byte
units, 4-byte pointers -- because WHEN it runs out (and the model restarts) is
part of the format: the packer restarted at that moment, so the decoder must.

The model lives across text blocks of one stream: a block without the reset
flag continues the previous one's statistics. Use one PpmStream per stream and
feed it the blocks in order:

    ps = PpmStream()
    end, produced = ps.block(buf, start)    # start: the block's flag byte
"""
from __future__ import annotations

UNIT = 12                       # the original's unit (FIXED_UNIT_SIZE)
N1, N2, N3 = 4, 4, 4
N4 = (128 + 3 - 1 * N1 - 2 * N2 - 3 * N3) // 4
N_INDEXES = N1 + N2 + N3 + N4
MAX_O = 64
TOP, BOT = 1 << 24, 1 << 15
INT_BITS, PERIOD_BITS = 7, 7
TOT_BITS = INT_BITS + PERIOD_BITS
INTERVAL, BIN_SCALE, MAX_FREQ = 1 << INT_BITS, 1 << TOT_BITS, 124
M32 = 0xFFFFFFFF
EXP_ESCAPE = (25, 14, 9, 7, 5, 5, 4, 4, 4, 3, 3, 3, 2, 2, 2, 2)
INIT_BIN_ESC = (0x3CDD, 0x1F3F, 0x59BF, 0x48F3, 0x64A1, 0x5ABC, 0x6632, 0x6051)
BASE = UNIT                     # HeapStart; offset 0 stays NULL

INDX2UNITS = []
_k = 1
for _n, _step in ((N1, 1), (N2, 2), (N3, 3), (N4, 4)):
    for _ in range(_n):
        INDX2UNITS.append(_k)
        _k += _step
    _k += 1                     # the `k++` that opens each next loop
UNITS2INDX = []
_i = 0
for _k in range(128):
    _i += INDX2UNITS[_i] < _k + 1
    UNITS2INDX.append(_i)

NS2BSINDX = [0, 2] + [4] * 9 + [6] * (256 - 11)
NS2INDX = [0, 1, 2]
_m, _k, _step = 3, 1, 1
for _i in range(3, 256):
    NS2INDX.append(_m)
    _k -= 1
    if not _k:
        _step += 1
        _k = _step
        _m += 1
HB2FLAG = [0] * 0x40 + [0x08] * (0x100 - 0x40)


class PpmError(ValueError):
    pass


class _See2:
    __slots__ = ("summ", "shift", "count")

    def __init__(self, init=0):
        self.shift = PERIOD_BITS - 4
        self.summ = (init << self.shift) & 0xFFFF
        self.count = 4

    def mean(self):
        r = self.summ >> self.shift
        self.summ = (self.summ - r) & 0xFFFF
        return r + (r == 0)

    def update(self):
        if self.shift < PERIOD_BITS:
            self.count = (self.count - 1) & 0xFF
            if self.count == 0:
                self.summ = (self.summ * 2) & 0xFFFF
                self.count = (3 << self.shift) & 0xFF
                self.shift += 1


class PpmStream:
    """One stream's PPM model, range coder and escape character."""

    def __init__(self):
        self.mem = None
        self.size = 0                 # SubAllocatorSize (bytes, as asked)
        self.esc = 2                  # PPMEscChar's start value (unpack30)
        self.min_ctx = 0
        # ModelPPM members StartModelRare does not reset
        self.dummy = _See2()
        self.num_masked = 0
        self.init_esc = 0
        self.hibits = 0

    # ── memory ──────────────────────────────────────────────────────────
    def _u16(self, a):
        m = self.mem
        return m[a] | m[a + 1] << 8

    def _w16(self, a, v):
        m = self.mem
        m[a] = v & 0xFF
        m[a + 1] = (v >> 8) & 0xFF

    def _u32(self, a):
        m = self.mem
        return m[a] | m[a + 1] << 8 | m[a + 2] << 16 | m[a + 3] << 24

    def _w32(self, a, v):
        self.mem[a:a + 4] = (v & M32).to_bytes(4, "little")

    # ── SubAllocator ────────────────────────────────────────────────────
    def _start_sub(self, mb):
        t = mb << 20
        if self.size == t:
            return
        alloc = t // UNIT * UNIT + 2 * UNIT
        # + room for the GlueFreeBlocks sentinel and reads past the end
        self.mem = bytearray(BASE + alloc + 4 * UNIT)
        self.heap_end = BASE + alloc - UNIT
        self.s0 = BASE + alloc + UNIT          # the sentinel MEM_BLK
        self.size = t

    def _init_sub(self):
        self.free = [0] * N_INDEXES
        self.ptext = BASE
        size2 = UNIT * (self.size // 8 // UNIT * 7)
        size1 = self.size - size2
        real1 = size1 // UNIT * UNIT + UNIT
        self.lo = self.units_start = BASE + real1
        self.fake_units_start = BASE + size1
        self.hi = self.lo + size2
        self.glue = 0

    def _insert(self, p, indx):
        self._w32(p, self.free[indx])
        self.free[indx] = p

    def _remove(self, indx):
        r = self.free[indx]
        self.free[indx] = self._u32(r)
        return r

    def _split(self, p, old, new):
        udiff = INDX2UNITS[old] - INDX2UNITS[new]
        p += UNIT * INDX2UNITS[new]
        i = UNITS2INDX[udiff - 1]
        if INDX2UNITS[i] != udiff:
            i -= 1
            self._insert(p, i)
            k = INDX2UNITS[i]
            p += UNIT * k
            udiff -= k
        self._insert(p, UNITS2INDX[udiff - 1])

    # MEM_BLK: Stamp u16 @0, NU u16 @2, next @4, prev @8
    def _glue_free(self):
        u16, w16, u32, w32 = self._u16, self._w16, self._u32, self._w32
        s0 = self.s0
        if self.lo != self.hi:
            self.mem[self.lo] = 0
        w32(s0 + 4, s0)
        w32(s0 + 8, s0)
        for i in range(N_INDEXES):
            while self.free[i]:
                p = self._remove(i)
                # p->insertAt(&s0): next=(prev=s0)->next; s0->next=next->prev=p
                w32(p + 8, s0)
                nxt = u32(s0 + 4)
                w32(p + 4, nxt)
                w32(nxt + 8, p)
                w32(s0 + 4, p)
                w16(p, 0xFFFF)
                w16(p + 2, INDX2UNITS[i])
        p = u32(s0 + 4)
        while p != s0:
            while True:
                p1 = p + UNIT * u16(p + 2)
                if not (u16(p1) == 0xFFFF and u16(p + 2) + u16(p1 + 2) < 0x10000):
                    break
                # p1->remove()
                pr, nx = u32(p1 + 8), u32(p1 + 4)
                w32(pr + 4, nx)
                w32(nx + 8, pr)
                w16(p + 2, u16(p + 2) + u16(p1 + 2))
            p = u32(p + 4)
        while True:
            p = u32(s0 + 4)
            if p == s0:
                break
            pr, nx = u32(p + 8), u32(p + 4)
            w32(pr + 4, nx)
            w32(nx + 8, pr)
            sz = u16(p + 2)
            while sz > 128:
                self._insert(p, N_INDEXES - 1)
                sz -= 128
                p += UNIT * 128
            i = UNITS2INDX[sz - 1]
            if INDX2UNITS[i] != sz:
                i -= 1
                k = sz - INDX2UNITS[i]
                self._insert(p + UNIT * (sz - k), k - 1)
            self._insert(p, i)

    def _alloc_rare(self, indx):
        if not self.glue:
            self.glue = 255
            self._glue_free()
            if self.free[indx]:
                return self._remove(indx)
        i = indx
        while True:
            i += 1
            if i == N_INDEXES:
                self.glue = (self.glue - 1) & 0xFF
                j = UNIT * INDX2UNITS[indx]
                if self.fake_units_start - self.ptext > j:
                    self.fake_units_start -= j
                    self.units_start -= j
                    return self.units_start
                return 0
            if self.free[i]:
                break
        r = self._remove(i)
        self._split(r, i, indx)
        return r

    def _alloc_units(self, nu):
        indx = UNITS2INDX[nu - 1]
        if self.free[indx]:
            return self._remove(indx)
        r = self.lo
        self.lo += UNIT * INDX2UNITS[indx]
        if self.lo <= self.hi:
            return r
        self.lo -= UNIT * INDX2UNITS[indx]
        return self._alloc_rare(indx)

    def _alloc_context(self):
        if self.hi != self.lo:
            self.hi -= UNIT
            return self.hi
        if self.free[0]:
            return self._remove(0)
        return self._alloc_rare(0)

    def _expand_units(self, old, old_nu):
        i0, i1 = UNITS2INDX[old_nu - 1], UNITS2INDX[old_nu]
        if i0 == i1:
            return old
        p = self._alloc_units(old_nu + 1)
        if p:
            n = UNIT * old_nu
            self.mem[p:p + n] = self.mem[old:old + n]
            self._insert(old, i0)
        return p

    def _shrink_units(self, old, old_nu, new_nu):
        i0, i1 = UNITS2INDX[old_nu - 1], UNITS2INDX[new_nu - 1]
        if i0 == i1:
            return old
        if self.free[i1]:
            p = self._remove(i1)
            n = UNIT * new_nu
            self.mem[p:p + n] = self.mem[old:old + n]
            self._insert(old, i0)
            return p
        self._split(old, i0, i1)
        return old

    # ── model ───────────────────────────────────────────────────────────
    # CONTEXT: NumStats u16 @0, SummFreq u16 @2 | OneState @2 (Symbol @2,
    # Freq @3, Successor @4), Stats @4, Suffix @8.  STATE: Symbol @0,
    # Freq @1, Successor @2 (6 bytes).
    def _restart(self):
        self.charmask = [0] * 256
        self._init_sub()
        self.init_rl = -(self.max_order if self.max_order < 12 else 12) - 1
        c = self._alloc_context()
        if not c:
            raise PpmError("no memory for the root context")
        self.min_ctx = self.max_ctx = c
        self._w32(c + 8, 0)
        self.order_fall = self.max_order
        self._w16(c, 256)
        self._w16(c + 2, 257)
        st = self._alloc_units(128)
        if not st:
            raise PpmError("no memory for the root statistics")
        self.found = st
        self._w32(c + 4, st)
        self.run_length = self.init_rl
        self.prev_success = 0
        m = self.mem
        for i in range(256):
            a = st + 6 * i
            m[a] = i
            m[a + 1] = 1
            m[a + 2:a + 6] = b"\0\0\0\0"
        self.binsumm = [[0] * 64 for _ in range(128)]
        for i in range(128):
            row = self.binsumm[i]
            for k in range(8):
                v = BIN_SCALE - INIT_BIN_ESC[k] // (i + 2)
                for mm in range(0, 64, 8):
                    row[k + mm] = v
        self.see2 = [[_See2(5 * i + 10) for _k in range(16)] for i in range(25)]

    def _start_model(self, max_order):
        self.esc_count = 1
        self.max_order = max_order
        self._restart()
        self.dummy.shift = PERIOD_BITS

    def _swap(self, a, b):
        m = self.mem
        m[a:a + 6], m[b:b + 6] = m[b:b + 6], m[a:a + 6]

    def _rescale(self, c):
        m = self.mem
        u16, w16 = self._u16, self._w16
        old_ns = u16(c)
        i = old_ns - 1
        stats = self._u32(c + 4)
        p = self.found
        while p != stats:
            self._swap(p, p - 6)
            p -= 6
        m[stats + 1] += 4
        summ = u16(c + 2) + 4
        esc = summ - m[p + 1]
        adder = 1 if self.order_fall != 0 else 0
        m[p + 1] = (m[p + 1] + adder) >> 1
        summ = m[p + 1]
        while True:
            p += 6
            esc -= m[p + 1]
            m[p + 1] = (m[p + 1] + adder) >> 1
            summ += m[p + 1]
            if m[p + 1] > m[p - 5]:
                tmp = bytes(m[p:p + 6])
                p1 = p
                while True:
                    m[p1:p1 + 6] = m[p1 - 6:p1]
                    p1 -= 6
                    if not (p1 != stats and tmp[1] > m[p1 - 5]):
                        break
                m[p1:p1 + 6] = tmp
            i -= 1
            if not i:
                break
        ns = old_ns
        if m[p + 1] == 0:
            while True:
                i += 1
                p -= 6
                if m[p + 1] != 0:
                    break
            esc += i
            ns -= i
            w16(c, ns)
            if ns == 1:
                tmp = bytearray(m[stats:stats + 6])
                while True:
                    tmp[1] -= tmp[1] >> 1
                    esc >>= 1
                    if not esc > 1:
                        break
                self._insert(stats, UNITS2INDX[((old_ns + 1) >> 1) - 1])
                m[c + 2:c + 8] = tmp
                self.found = c + 2
                return
        esc -= esc >> 1
        summ += esc
        w16(c + 2, summ)
        n0, n1 = (old_ns + 1) >> 1, (ns + 1) >> 1
        if n0 != n1:
            stats = self._shrink_units(stats, n0, n1)
            self._w32(c + 4, stats)
        self.found = stats

    def _create_child(self, ctx, pstats, first):
        pc = self._alloc_context()
        if pc:
            self._w16(pc, 1)
            self.mem[pc + 2:pc + 8] = first
            self._w32(pc + 8, ctx)
            self._w32(pstats + 2, pc)
        return pc

    def _create_successors(self, skip, p1):
        m, u16, u32 = self.mem, self._u16, self._u32
        pc = self.min_ctx
        fs = self.found
        up_branch = u32(fs + 2)
        fsym = m[fs]
        ps = []
        no_loop = False
        if not skip:
            ps.append(fs)
            if not u32(pc + 8):
                no_loop = True
        if not no_loop:
            entry = False
            if p1:
                p = p1
                pc = u32(pc + 8)
                entry = True
            while True:
                if not entry:
                    pc = u32(pc + 8)
                    if u16(pc) != 1:
                        p = u32(pc + 4)
                        while m[p] != fsym:
                            p += 6
                    else:
                        p = pc + 2
                entry = False
                if u32(p + 2) != up_branch:
                    pc = u32(p + 2)
                    break
                if len(ps) >= MAX_O:
                    return 0
                ps.append(p)
                if not u32(pc + 8):
                    break
        if not ps:
            return pc
        usym = m[up_branch]
        usucc = up_branch + 1
        if u16(pc) != 1:
            if pc <= self.ptext:
                return 0
            p = u32(pc + 4)
            while m[p] != usym:
                p += 6
            cf = m[p + 1] - 1
            s0 = (u16(pc + 2) - u16(pc) - cf) & M32
            if 2 * cf <= s0:
                ufreq = 1 + (5 * cf > s0)
            else:
                ufreq = 1 + (2 * cf + 3 * s0 - 1) // (2 * s0)
        else:
            ufreq = m[pc + 3]
        up = bytes((usym, ufreq & 0xFF)) + usucc.to_bytes(4, "little")
        while ps:
            pc = self._create_child(pc, ps.pop(), up)
            if not pc:
                return 0
        return pc

    def _update_model(self):
        m, u16, w16, u32, w32 = self.mem, self._u16, self._w16, self._u32, self._w32
        fsa = self.found
        fs_sym, fs_freq, fs_succ = m[fsa], m[fsa + 1], u32(fsa + 2)
        p = 0
        restart = False
        while True:                    # a block to break out of to RESTART
            pc = u32(self.min_ctx + 8)
            if fs_freq < MAX_FREQ // 4 and pc:
                if u16(pc) != 1:
                    p = u32(pc + 4)
                    if m[p] != fs_sym:
                        while True:
                            p += 6
                            if m[p] == fs_sym:
                                break
                        if m[p + 1] >= m[p - 5]:
                            self._swap(p, p - 6)
                            p -= 6
                    if m[p + 1] < MAX_FREQ - 9:
                        m[p + 1] += 2
                        w16(pc + 2, u16(pc + 2) + 2)
                else:
                    p = pc + 2
                    if m[p + 1] < 32:
                        m[p + 1] += 1
            if not self.order_fall:
                c = self._create_successors(True, p)
                w32(self.found + 2, c)
                self.min_ctx = self.max_ctx = c
                if not c:
                    restart = True
                    break
                return
            m[self.ptext] = fs_sym
            self.ptext += 1
            successor = self.ptext
            if self.ptext >= self.fake_units_start:
                restart = True
                break
            if fs_succ:
                if fs_succ <= self.ptext:
                    fs_succ = self._create_successors(False, p)
                    if not fs_succ:
                        restart = True
                        break
                self.order_fall -= 1
                if not self.order_fall:
                    successor = fs_succ
                    if self.max_ctx != self.min_ctx:
                        self.ptext -= 1
            else:
                w32(self.found + 2, successor)
                fs_succ = self.min_ctx
            mc = self.min_ctx
            ns = u16(mc)
            s0 = (u16(mc + 2) - ns - (fs_freq - 1)) & M32
            pc = self.max_ctx
            while pc != mc:
                ns1 = u16(pc)
                if ns1 != 1:
                    if (ns1 & 1) == 0:
                        st = self._expand_units(u32(pc + 4), ns1 >> 1)
                        w32(pc + 4, st)
                        if not st:
                            restart = True
                            break
                    sf_ = u16(pc + 2)
                    sf_ += (2 * ns1 < ns) + 2 * ((4 * ns1 <= ns) & (sf_ <= 8 * ns1))
                    w16(pc + 2, sf_)
                else:
                    p = self._alloc_units(1)
                    if not p:
                        restart = True
                        break
                    m[p:p + 6] = m[pc + 2:pc + 8]
                    w32(pc + 4, p)
                    if m[p + 1] < MAX_FREQ // 4 - 1:
                        m[p + 1] += m[p + 1]
                    else:
                        m[p + 1] = MAX_FREQ - 4
                    w16(pc + 2, m[p + 1] + self.init_esc + (ns > 3))
                summ = u16(pc + 2)
                cf = 2 * fs_freq * (summ + 6)
                sf = s0 + summ
                if cf < 6 * sf:
                    cf = 1 + (cf > sf) + (cf >= 4 * sf)
                    w16(pc + 2, summ + 3)
                else:
                    cf = 4 + (cf >= 9 * sf) + (cf >= 12 * sf) + (cf >= 15 * sf)
                    w16(pc + 2, summ + cf)
                p = u32(pc + 4) + 6 * ns1
                m[p] = fs_sym
                m[p + 1] = cf
                w32(p + 2, successor)
                w16(pc, ns1 + 1)
                pc = u32(pc + 8)
            if restart:
                break
            self.max_ctx = self.min_ctx = fs_succ
            return
        self._restart()
        self.esc_count = 0

    # ── range coder ─────────────────────────────────────────────────────
    def _getc(self):
        pos = self.pos
        self.pos = pos + 1
        return self.buf[pos] if pos < self.blen else 0

    def _normalize(self):
        low, rng, code = self.low, self.range, self.code
        while True:
            if (low ^ ((low + rng) & M32)) < TOP:
                pass
            elif rng < BOT:
                rng = (-low) & (BOT - 1)
            else:
                break
            code = ((code << 8) | self._getc()) & M32
            rng = (rng << 8) & M32
            low = (low << 8) & M32
        self.low, self.range, self.code = low, rng, code

    def _decode(self):
        self.low = (self.low + self.range * self.lowc) & M32
        self.range = (self.range * (self.highc - self.lowc)) & M32

    # ── symbols ─────────────────────────────────────────────────────────
    def _bin_symbol(self, c):
        m = self.mem
        rs = c + 2
        self.hibits = HB2FLAG[m[self.found]]
        suffix = self._u32(c + 8)
        row = self.binsumm[m[rs + 1] - 1]
        col = (self.prev_success + NS2BSINDX[self._u16(suffix) - 1] + self.hibits
               + 2 * HB2FLAG[m[rs]] + ((self.run_length >> 26) & 0x20))
        bs = row[col]
        self.range >>= TOT_BITS
        if ((self.code - self.low) & M32) // self.range < bs:
            self.found = rs
            if m[rs + 1] < 128:
                m[rs + 1] += 1
            self.lowc, self.highc = 0, bs
            row[col] = (bs + INTERVAL - ((bs + 32) >> 7)) & 0xFFFF
            self.prev_success = 1
            self.run_length += 1
        else:
            self.lowc = bs
            bs = (bs - ((bs + 32) >> 7)) & 0xFFFF
            row[col] = bs
            self.highc = BIN_SCALE
            self.init_esc = EXP_ESCAPE[bs >> 10]
            self.num_masked = 1
            self.charmask[m[rs]] = self.esc_count
            self.prev_success = 0
            self.found = 0

    def _symbol1(self, c):
        m, u16 = self.mem, self._u16
        scale = u16(c + 2)
        self.scale = scale
        p = self._u32(c + 4)
        self.range //= scale
        count = ((self.code - self.low) & M32) // self.range
        if count >= scale:
            raise PpmError("symbol count past the scale")
        hi = m[p + 1]
        if count < hi:
            self.highc = hi
            self.prev_success = 1 if 2 * hi > scale else 0
            self.run_length += self.prev_success
            hi += 4
            m[p + 1] = hi & 0xFF
            self._w16(c + 2, scale + 4)
            self.found = p
            if hi > MAX_FREQ:
                self._rescale(c)
            self.lowc = 0
            return
        if not self.found:
            raise PpmError("no previous state")
        self.prev_success = 0
        i = u16(c) - 1
        while True:
            p += 6
            hi += m[p + 1]
            if hi > count:
                break
            i -= 1
            if i == 0:
                self.hibits = HB2FLAG[m[self.found]]
                self.lowc = hi
                ec = self.esc_count
                cm = self.charmask
                cm[m[p]] = ec
                self.num_masked = u16(c)
                i = self.num_masked - 1
                self.found = 0
                while True:
                    p -= 6
                    cm[m[p]] = ec
                    i -= 1
                    if not i:
                        break
                self.highc = scale
                return
        self.highc = hi
        self.lowc = hi - m[p + 1]
        # update1
        self.found = p
        m[p + 1] += 4
        self._w16(c + 2, u16(c + 2) + 4)
        if m[p + 1] > m[p - 5]:
            self._swap(p, p - 6)
            p -= 6
            self.found = p
            if m[p + 1] > MAX_FREQ:
                self._rescale(c)

    def _symbol2(self, c):
        m, u16 = self.mem, self._u16
        ns = u16(c)
        diff = ns - self.num_masked
        # makeEscFreq2
        if ns != 256:
            suffix = self._u32(c + 8)
            see = self.see2[NS2INDX[diff - 1]][
                (diff < u16(suffix) - ns) + 2 * (u16(c + 2) < 11 * ns)
                + 4 * (self.num_masked > diff) + self.hibits]
            scale = see.mean()
        else:
            see = self.dummy
            scale = 1
        cm, ec = self.charmask, self.esc_count
        ps = []
        p = self._u32(c + 4) - 6
        hi = 0
        i = diff
        while True:
            while True:
                p += 6
                if cm[m[p]] != ec:
                    break
            hi += m[p + 1]
            ps.append(p)
            i -= 1
            if not i:
                break
        scale += hi
        self.range //= scale
        count = ((self.code - self.low) & M32) // self.range
        if count >= scale:
            raise PpmError("symbol count past the scale")
        if count < hi:
            hi = 0
            for p in ps:
                hi += m[p + 1]
                if hi > count:
                    break
            self.highc = hi
            self.lowc = hi - m[p + 1]
            see.update()
            # update2
            self.found = p
            m[p + 1] += 4
            self._w16(c + 2, u16(c + 2) + 4)
            if m[p + 1] > MAX_FREQ:
                self._rescale(c)
            self.esc_count = (self.esc_count + 1) & 0xFF
            self.run_length = self.init_rl
        else:
            self.lowc = hi
            self.highc = scale
            for p in ps:
                cm[m[p]] = ec
            see.summ = (see.summ + scale) & 0xFFFF
            self.num_masked = ns

    def char(self):
        """DecodeChar: the next symbol of the text block."""
        u16, u32 = self._u16, self._u32
        c = self.min_ctx
        if not (self.ptext < c <= self.heap_end):
            raise PpmError("context outside the model")
        if u16(c) != 1:
            st = u32(c + 4)
            if not (self.ptext < st <= self.heap_end):
                raise PpmError("statistics outside the model")
            self._symbol1(c)
        else:
            self._bin_symbol(c)
        self._decode()
        while not self.found:
            self._normalize()
            while True:
                self.order_fall += 1
                c = u32(c + 8)
                if not (self.ptext < c <= self.heap_end):
                    raise PpmError("suffix outside the model")
                if u16(c) != self.num_masked:
                    break
            self.min_ctx = c
            self._symbol2(c)
            self._decode()
        fs = self.found
        sym = self.mem[fs]
        succ = u32(fs + 2)
        if not self.order_fall and succ > self.ptext:
            self.min_ctx = self.max_ctx = succ
        else:
            self._update_model()
            if self.esc_count == 0:
                self.esc_count = 1
                self.charmask = [0] * 256
        self._normalize()
        return sym

    # ── a block ─────────────────────────────────────────────────────────
    def init(self, buf, start):
        """DecodeInit at byte `start` (the flag byte, high bit set)."""
        self.buf, self.blen, self.pos = buf, len(buf), start
        flags = self._getc()
        reset = flags & 0x20
        if reset:
            max_mb = self._getc()
        elif not self.size:
            raise PpmError("text block continues a model that was never started")
        if flags & 0x40:
            self.esc = self._getc()
        self.low, self.code, self.range = 0, 0, M32
        for _ in range(4):
            self.code = ((self.code << 8) | self._getc()) & M32
        if reset:
            order = (flags & 0x1F) + 1
            if order > 16:
                order = 16 + (order - 16) * 3
            if order == 1:
                self.size = 0
                raise PpmError("order 1 text block")
            self._start_sub(max_mb + 1)
            self._start_model(order)
        if not self.min_ctx:
            raise PpmError("no model")

    def block(self, buf, start, out=None):
        """Decode one text block from its flag byte at `start`.

        Returns (end, produced, last): `end` is the byte where the next
        block's tables begin, `produced` how many bytes of the file the
        block stands for, `last` True when it closed the stream (escape 2)
        instead of handing over to LZ (escape 0). With `out` (a bytearray),
        the block's literal bytes are appended and its matches copied, so a
        caller holding the window can check them."""
        self.init(buf, start)
        esc = self.esc
        produced = 0
        ch = self.char
        while True:
            c = ch()
            if c == esc:
                n = ch()
                if n == 0:
                    return self.pos, produced, False
                if n == 2:
                    return self.pos, produced, True
                if n == 3:              # filter (VM) code: no output of its own
                    first = ch()
                    ln = (first & 7) + 1
                    if ln == 7:
                        ln = ch() + 7
                    elif ln == 8:
                        ln = ch() * 256 + ch()
                    for _ in range(ln):
                        ch()
                    continue
                if n == 4:
                    d = (ch() << 16) | (ch() << 8)
                    d |= ch()
                    ln = ch() + 32
                    dist = d + 2
                    if out is not None:
                        for _ in range(ln):
                            out.append(out[-dist])
                    produced += ln
                    continue
                if n == 5:
                    ln = ch() + 4
                    if out is not None:
                        for _ in range(ln):
                            out.append(out[-1])
                    produced += ln
                    continue
                # n == 1 (or anything else): the escape byte itself
            if out is not None:
                out.append(c)
            produced += 1
