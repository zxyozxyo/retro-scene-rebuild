"""RAR 2.x (Win32) multimedia-analyser verdicts, forced per call.

WHY. With -mm, RAR 2.x asks an analyser about every ~1 KB chunk whether it is
audio. Its final test reads the match finder's hash heads, so the answer can
depend on the state the packing PC left in memory, not on the data alone --
the Windows twin of the DOS leftover-memory case (dosrar_fill). Measured on
two HOOLiGANS PSX walls whose .bin never reproduced at any build:

  Oni_Zero_Fukkatsu   582,175 analyser calls, ONE disagrees with the original
                      (call 344,061: original audio, ours LZ). Forced: the
                      281,460,921 B stream is byte-identical.
  FURAIKI             692,951 calls, ONE disagrees (call 279,549: original
                      LZ, ours audio). Forced: 518,112,167 B identical.

So a recipe can carry the verdicts that went the other way, `@MMV<call>:<v>`
(call numbers are 1-based across the whole command), and the replay runs the
same build patched to answer those calls that way. Every other call is
untouched and the build is otherwise byte-for-byte the stock one.

HOW THE PATCH WORKS. Every call to the analyser is redirected into a code cave
(CODE's raw data grown into its virtual slack): count the call, run the real
analyser, and if the count is in the table replace AL. With a trace path it
also appends a record per call -- ring position, call number, chunk length,
verdict, channels, window mask, the mode before the call and the chunk's
first 16 bytes -- which is how the capture finds the calls to force.

FINDING THEM (capture). The ring position is NOT the output position (it
drifts from it), so each call is placed in its file by its 16 data bytes;
the original's block starts come from decoding its stream
(dosrar_fill.rar20_tokens(blocks=...)). A call whose verdict differs from the
original's block type at that position is the one to force. One at a time,
repacking between: a flip changes what follows it.

CHANNELS. An audio verdict comes with a channel count (1-4), which the
analyser leaves in the compressor object, and that can be the part that went
the other way: `@MMV<call>:1:4` forces "audio, 4 channels". Measured on two
DC walls where forcing "audio" alone left the pack 2-channel against the
original's 4:

  Jet_Coaster_Dream_2 (STONEARTS)   call 619,520 -> audio/4: 17,402,684 B
                                    stream identical
  F1_World_Grand_Prix (UTOPIA)      call 141,310 -> audio/4: 132,013,265 B
                                    identical

THE LAZY DECISION (`@LZV<call>:<v>`). The same class without -mm. After
finding a match the compressor asks a second routine whether the NEXT
position has a better one; if so it emits a literal and takes that instead.
That answer, too, can differ from the scene's PC's in one place:

  Virtua_Cop_2 (ULFC)   750 MB image, 43,680,498 tokens, ONE differs: at
                        434,734,197 the original deferred (literal, then
                        19 @ 176,400) and every 2.70-2.90 build takes
                        17 @ 557,424. Forced: 53,179,842 B identical.

The stock build skips the question there because the match's distance equals
the last deferred one; why the original did not is not known. A recipe
carries the call number, as with @MMV. The capture finds it by packing with
a 32-byte context (16 bytes each side of the position) to watch for: the
patch forces the answer on the first call made there and logs that call's
number.
"""
from __future__ import annotations

import bisect
import re
import struct

REC = 44                                  # bytes per trace record
_SITE = bytes.fromhex("5653E8")           # push esi; push ebx; call ANALYSER
_AFTER = bytes.fromhex("0FBED08993B4010000")  # movsx edx, al; mov [ebx+1b4], edx
_CAVE = 0xC00
_TAB, _NAME, _DATA, _CNT, _OVR, _CHN = 0x400, 0x900, 0xB00, 0xB80, 0xB84, 0xB85
# the lazy decision's own table, context to watch for, counters and trace
_TAB2, _FP, _FPK, _FPV, _HITS, _LCNT = 0x600, 0x700, 0x720, 0x724, 0x728, 0x72C
_DATA2, _NAME2 = 0x730, 0x740
_NTAB = (_TAB2 - _TAB - 4) // 8
_NTAB2 = (_FP - _TAB2 - 4) // 8
_NAME_MAX = _DATA - _NAME - 0x40          # DATA-0x40 is where the code looks
_NAME2_MAX = _NAME - _NAME2
LZ_REC = 8                                # bytes per lazy trace record
FP_SIDE = 16                              # context bytes each side
# mov eax,[ebx+30]; push eax; push ebx; call LAZY; test eax,eax
_LZ_SITE = bytes.fromhex("8B43305053E8")
_LZ_AFTER = bytes.fromhex("85C0")


def _sections(d):
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    if d[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE image")
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    opt = struct.unpack_from('<H', d, pe + 20)[0]
    so = pe + 24 + opt
    secs = []
    for i in range(nsec):
        name, vs, va, rs, ro = struct.unpack_from('<8sIIII', d, so + 40 * i)
        secs.append(dict(off=so + 40 * i, name=name.rstrip(b'\0'), vs=vs,
                         va=va, rs=rs, ro=ro))
    ib = struct.unpack_from('<I', d, pe + 24 + 28)[0]
    return pe, secs, ib


def _rva2off(secs, rva):
    for s in secs:
        if s['va'] <= rva < s['va'] + max(s['vs'], s['rs']):
            return s['ro'] + rva - s['va']
    raise ValueError(f"rva {rva:#x} outside every section")


def _iat(d, secs, ib, pe, want):
    imp_rva = struct.unpack_from('<I', d, pe + 24 + 104)[0]
    out = {}
    p = _rva2off(secs, imp_rva)
    while True:
        oft, _, _, name_rva, ft = struct.unpack_from('<5I', d, p)
        if not name_rva:
            break
        look = oft or ft
        i = 0
        while True:
            ent = struct.unpack_from('<I', d, _rva2off(secs, look + 4 * i))[0]
            if not ent:
                break
            if not ent & 0x80000000:
                n = _rva2off(secs, ent) + 2
                nm = d[n:d.index(b'\0', n)].decode('latin1')
                if nm in want:
                    out[nm] = ib + ft + 4 * i
            i += 1
        p += 20
    return out


def _target(d, secs, ib):
    code = secs[0]
    at = d.find(_SITE, code['ro'], code['ro'] + code['rs'])
    while at >= 0 and d[at + 7:at + 7 + len(_AFTER)] != _AFTER:
        at = d.find(_SITE, at + 1, code['ro'] + code['rs'])
    if at < 0:
        return None
    call_va = ib + code['va'] + (at + 2 - code['ro'])
    return call_va + 5 + struct.unpack_from('<i', d, at + 3)[0]


def _lz_site(d, secs):
    """File offset of the one `call LAZY` (its E8), or None."""
    code = secs[0]
    end = code['ro'] + code['rs']
    found = []
    at = d.find(_LZ_SITE, code['ro'], end)
    while at >= 0:
        if d[at + 10:at + 12] == _LZ_AFTER:
            found.append(at + 5)
        at = d.find(_LZ_SITE, at + 1, end)
    return found[0] if len(found) == 1 else None


def supported(exe: bytes) -> bool:
    """Whether this build has the analyser this patch knows and room for it."""
    try:
        pe, secs, ib = _sections(exe)
        code = secs[0]
        return (_target(exe, secs, ib) is not None
                and _lz_site(exe, secs) is not None
                and code['vs'] - code['rs'] >= _CAVE
                and len(_iat(exe, secs, ib, pe,
                             {"CreateFileA", "WriteFile"})) == 2)
    except (ValueError, struct.error, IndexError):
        return False


# -- a hash table directly behind the window (`@ADJ`) ------------------------
#
# The analyser samples up to 1,024 bytes from the window position WITHOUT
# wrapping, and may start as late as 258 bytes before the window's end -- so
# on the last call of every lap it reads up to ~760 bytes of whatever the
# heap holds behind the 1 MB window. On a current Windows that is a free
# block's header and zeros (the header even differs run to run). On the
# packers' PCs it was one of the compressor's own 4-byte-per-entry position
# tables, which the analyser reads as four-channel audio: that is where the
# "one verdict went the other way on their PC" walls come from, and why the
# forced verdicts were nearly always "audio, 4 channels" on the last call of
# a lap (call k*1024-1) and the one after it.
#
# Measured on Virtua_Fighter_3tb_PAL_DC-REPLICANT (2.70 -m5 -md1024 -s -mm,
# 814 MB, 776 laps): stock parts from the original at 40 MB and needs six
# forced verdicts; with the table at [obj+0xc] behind the window the whole
# 577,946,824 B stream is identical with none. The table at [obj+0x10] with
# no heap header between does the same; the LINK table ([obj+0x14], the one
# allocated straight after the window) gets five of the six and fails where
# its first entries are a run's ramp. Which table it truly was is not known
# -- this is the one that reproduces.
_ADJ_WIN = re.compile(rb"\x68\x00\x00\x10\x00(?=\xE8....\x59\x8B\xF0\x89\x33"
                      rb"\x8B\x43\x18\x50\x6A\x00\x56\xE8)", re.S)
_ADJ_TAB = re.compile(rb"\x68\x00\x00\x10\x00\xE8....\x59(?=\x89\x43\x0C)",
                      re.S)
_ADJ_FREE = re.compile(rb"\x8B\x53\x0C\x52\xE8....\x59", re.S)
_ADJ_HDR = 4                              # the heap's own header between them


def adjacent_ok(exe: bytes) -> bool:
    return all(len(r.findall(exe)) == 1
               for r in (_ADJ_WIN, _ADJ_TAB, _ADJ_FREE))


def adjacent(exe: bytes) -> bytes:
    """The build with the table at [obj+0xc] allocated directly behind its
    window, as one block: window (1 MB), a 4-byte heap header, the table."""
    if not adjacent_ok(exe):
        raise ValueError("window / table allocation not found")
    d = bytearray(exe)
    m = _ADJ_WIN.search(d)                 # push 0x100000 -> room for both
    d[m.start():m.end()] = b"\x68" + struct.pack('<I', 0x200010)
    m = _ADJ_TAB.search(d)                 # table = window + 1 MB + header
    new = (b"\x8B\x03"                                    # mov eax, [ebx]
           + b"\x05" + struct.pack('<I', 0x100000 + _ADJ_HDR)
           + b"\x90" * 4)
    assert len(new) == m.end() - m.start()
    d[m.start():m.end()] = new
    m = _ADJ_FREE.search(d)                # the table is freed with the window
    d[m.start():m.end()] = b"\x90" * (m.end() - m.start())
    return bytes(d)


def parse(switches, tag: str = "@MMV") -> list[tuple]:
    """[(call, verdict[, channels])] from recipe switches like
    '@MMV344061:1,500000:0,619520:1:4' -- or, with tag='@LZV', the lazy
    decisions '@LZV1234567:1'."""
    out = []
    for x in switches:
        x = str(x)
        if not x.startswith(tag):
            continue
        for part in filter(None, x[len(tag):].split(",")):
            out.append(tuple(int(p) for p in part.split(":")))
    return out


def switch(entries, tag: str = "@MMV") -> str:
    return tag + ",".join(":".join(str(int(p)) for p in e)
                          for e in sorted(entries))


def _lz_code(at: int, lazy: int, cave: int, imp: dict, watch: bool) -> bytes:
    """The stand-in for `call LAZY`, assembled to sit at virtual address
    `at`: count the call, run the real routine, replace its answer if the
    count is in the table -- and, with `watch`, also on the [FPK]-th call
    whose 32 bytes of context equal [FP], logging (call, ring position)."""
    A = lambda off: struct.pack('<I', cave + off)
    c = bytearray()
    c += b"\xFF\x05" + A(_LCNT)                                      # inc [LCNT]
    c += bytes.fromhex("FF742408FF742408")                           # push depth; push obj
    c += b"\xE8" + struct.pack('<i', lazy - (at + len(c) + 5))       # the routine
    c += b"\x60"                                                     # pushad
    c += b"\xA1" + A(_LCNT)                                          # eax = [LCNT]
    c += b"\xBE" + A(_TAB2)                                          # esi = TAB2
    c += b"\x8B\x0E\x83\xC6\x04"                                     # ecx = n
    c += b"\xE3\x00"; j0 = len(c) - 1                                # jecxz notab
    lp = len(c)
    c += b"\x3B\x06" + b"\x75\x00"; jn = len(c) - 1                  # cmp eax,[esi]
    c += b"\x0F\xB6\x5E\x04" + b"\x89\x5C\x24\x1C"                   # saved eax = v
    c += b"\xEB\x00"; jd = len(c) - 1
    nx = len(c)
    c += b"\x83\xC6\x08"                                             # next entry
    c += b"\xE2" + bytes([(lp - (len(c) + 2)) & 0xFF])               # loop
    notab = len(c)
    c[j0] = notab - (j0 + 1); c[jn] = nx - (jn + 1); c[jd] = notab - (jd + 1)
    far = []                                                         # jcc -> done
    if watch:
        c += b"\x8B\x74\x24\x24"                                     # esi = obj
        c += bytes.fromhex("8B96681A0000")                           # edx = ring pos
        c += bytes([0x83, 0xFA, FP_SIDE])                            # too near 0?
        c += b"\x0F\x82" + bytes(4); far.append(len(c))
        c += b"\x8B\x4E\x1C" + bytes([0x83, 0xE9, FP_SIDE]) + b"\x39\xCA"
        c += b"\x0F\x87" + bytes(4); far.append(len(c))              # or the end
        c += b"\x8B\x06" + bytes([0x8D, 0x44, 0x10, 256 - FP_SIDE])  # eax = context
        c += b"\xBF" + A(_FP)                                        # edi = FP
        c += b"\xB9" + struct.pack('<I', FP_SIDE // 2)               # 8 dwords
        cl = len(c)
        c += b"\x8B\x18\x3B\x1F"                                     # same dword?
        c += b"\x0F\x85" + bytes(4); far.append(len(c))
        c += b"\x83\xC0\x04\x83\xC7\x04"
        c += b"\xE2" + bytes([(cl - (len(c) + 2)) & 0xFF])
        c += b"\xA1" + A(_HITS) + b"\xFF\x05" + A(_HITS)             # eax = hit#
        c += b"\x3B\x05" + A(_FPK) + b"\x75\x0B"                     # the one?
        c += b"\x0F\xB6\x1D" + A(_FPV) + b"\x89\x5C\x24\x1C"         # force it
        c += b"\xBF" + A(_DATA2)                                     # edi = DATA2
        c += b"\xA1" + A(_LCNT) + b"\x89\x47\x08" + b"\x89\x57\x0C"  # call, ring
        c += b"\x83\x3F\x00" + b"\x75\x00"; jo = len(c) - 1          # file open?
        c += bytes.fromhex("6A006880000000" "6A026A006A01" "6800000040")
        c += b"\x68" + A(_NAME2)
        c += b"\xFF\x15" + struct.pack('<I', imp["CreateFileA"]) + b"\x89\x07"
        c[jo] = len(c) - (jo + 1)
        c += bytes.fromhex("6A008D4704506A") + bytes([LZ_REC])
        c += bytes.fromhex("8D470850FF37")
        c += b"\xFF\x15" + struct.pack('<I', imp["WriteFile"])
    done = len(c)
    for f in far:
        struct.pack_into('<i', c, f - 4, done - f)
    c += b"\x61" + b"\xC2\x08\x00"                                   # popad; ret 8
    return bytes(c)


def build(exe: bytes, entries, trace_path: str | None = None,
          lz=(), lz_watch=None) -> bytes:
    """The build with each (call number, verdict[, channels]) in `entries`
    forced, and each lazy decision (call number, answer) in `lz`.

    `lz_watch` is (trace path, 32 context bytes, k, answer): force that
    answer on the k-th lazy call made where the window holds those bytes
    around the position, and log every such call."""
    d = bytearray(exe)
    pe, secs, ib = _sections(d)
    code = secs[0]
    target = _target(d, secs, ib)
    if target is None:
        raise ValueError("analyser call site not found")
    if code['vs'] - code['rs'] < _CAVE:
        raise ValueError("no slack in CODE for the patch")
    if len(entries) > _NTAB or len(lz) > _NTAB2:
        raise ValueError("too many forced verdicts")
    lz_at = _lz_site(d, secs)
    if lz_at is None:
        raise ValueError("lazy call site not found")
    lz_va = ib + code['va'] + (lz_at - code['ro'])
    lazy = lz_va + 5 + struct.unpack_from('<i', d, lz_at + 1)[0]
    ins_at = code['ro'] + code['rs']
    cave = ib + code['va'] + code['rs']
    d[ins_at:ins_at] = bytes(_CAVE)
    struct.pack_into('<I', d, code['off'] + 16, code['rs'] + _CAVE)
    for s in secs[1:]:
        if s['ro'] >= ins_at:
            struct.pack_into('<I', d, s['off'] + 20, s['ro'] + _CAVE)
    ch = struct.unpack_from('<I', d, code['off'] + 36)[0]
    struct.pack_into('<I', d, code['off'] + 36, ch | 0x80000000)   # writable
    secs = _sections(d)[1]
    TAB, CNT, OVR, DATA = cave + _TAB, cave + _CNT, cave + _OVR, cave + _DATA
    CHN = cave + _CHN
    c = bytearray()
    c += b"\xFF\x05" + struct.pack('<I', CNT)                        # inc [CNT]
    c += b"\xC6\x05" + struct.pack('<I', OVR) + b"\xFF"              # [OVR] = none
    c += b"\xC6\x05" + struct.pack('<I', CHN) + b"\x00"              # [CHN] = as analysed
    c += b"\x60"                                                     # pushad
    c += b"\xA1" + struct.pack('<I', CNT)                            # eax = [CNT]
    c += b"\xBE" + struct.pack('<I', TAB)                            # esi = TAB
    c += b"\x8B\x0E\x83\xC6\x04"                                     # ecx = n
    c += b"\xE3\x00"; j0 = len(c) - 1                                # jecxz done
    lp = len(c)
    c += b"\x3B\x06" + b"\x75\x00"; jn = len(c) - 1                  # cmp eax,[esi]
    c += b"\x8A\x5E\x04\x88\x1D" + struct.pack('<I', OVR)            # [OVR] = verdict
    c += b"\x8A\x5E\x05\x88\x1D" + struct.pack('<I', CHN)            # [CHN] = channels
    c += b"\xEB\x00"; jd = len(c) - 1
    nx = len(c)
    c += b"\x83\xC6\x08"                                             # next entry
    c += b"\xE2" + bytes([(lp - (len(c) + 2)) & 0xFF])               # loop
    done = len(c)
    c[j0] = done - (j0 + 1); c[jn] = nx - (jn + 1); c[jd] = done - (jd + 1)
    c += b"\x61"                                                     # popad
    c += bytes.fromhex("FF742408FF742408")                           # push len; push obj
    c += b"\xE8" + struct.pack('<i', target - (cave + len(c) + 5))   # the analyser
    c += b"\x8A\x0D" + struct.pack('<I', OVR)                        # cl = [OVR]
    c += b"\x80\xF9\xFF\x74\x1A\x88\xC8"                             # forced? al = cl
    c += b"\x8A\x0D" + struct.pack('<I', CHN)                        # cl = [CHN]
    c += b"\x84\xC9\x74\x0E"                                         # none? keep
    c += b"\x0F\xB6\xC9"                                             # ecx = channels
    c += b"\x8B\x54\x24\x04"                                         # edx = obj
    c += b"\x89\x8A\xB0\x01\x00\x00"                                 # obj.channels
    c += b"\x90"
    if trace_path:
        name = trace_path.encode("mbcs") + b"\0"
        if len(name) > _NAME_MAX:
            raise ValueError("trace path too long")
        imp = _iat(d, secs, ib, pe, {"CreateFileA", "WriteFile"})
        if len(imp) != 2:
            raise ValueError("CreateFileA/WriteFile not imported")
        c += b"\x60"                                                 # pushad
        c += b"\x8B\x74\x24\x24"                                     # esi = obj
        c += b"\xBF" + struct.pack('<I', DATA)                       # edi = DATA
        c += bytes.fromhex("8B8E681A0000894F10")                     # ring pos
        c += b"\xA1" + struct.pack('<I', CNT) + b"\x89\x47\x14"      # call number
        c += bytes.fromhex("8B4C2428894F18")                         # chunk length
        c += bytes.fromhex("0FB64C241C894F1C")                       # verdict (AL)
        c += bytes.fromhex("8B8EB0010000894F20")                     # channels
        c += bytes.fromhex("8B4E1C894F24")                           # window mask
        c += bytes.fromhex("8B8EB4010000894F28")                     # mode before
        c += bytes.fromhex("8B06" "0386681A0000")                    # eax = window+pos
        for k in range(4):
            c += bytes([0x8B, 0x48, 4 * k, 0x89, 0x4F, 0x2C + 4 * k])  # 16 data bytes
        c += b"\x83\x3F\x00" + b"\x75\x00"; jo = len(c) - 1          # file open?
        c += bytes.fromhex("6A006880000000" "6A026A006A01" "6800000040")
        c += b"\x8D\x87" + struct.pack('<i', -(_DATA - _NAME)) + b"\x50"  # lea eax, name
        c += b"\xFF\x15" + struct.pack('<I', imp["CreateFileA"]) + b"\x89\x07"
        c[jo] = len(c) - (jo + 1)
        c += bytes.fromhex("6A008D4708506A") + bytes([REC]) + bytes.fromhex("8D471050FF37")
        c += b"\xFF\x15" + struct.pack('<I', imp["WriteFile"])
        c += b"\x61"                                                 # popad
    c += b"\xC2\x08\x00"                                             # ret 8
    # the lazy decision's stand-in, behind the analyser's
    lz_code_at = len(c)
    if lz_watch:
        lz_name = str(lz_watch[0]).encode("mbcs") + b"\0"
        if len(lz_name) > _NAME2_MAX or len(lz_watch[1]) != 2 * FP_SIDE:
            raise ValueError("lazy watch: path too long or context not "
                             f"{2 * FP_SIDE} bytes")
        limp = _iat(d, secs, ib, pe, {"CreateFileA", "WriteFile"})
        if len(limp) != 2:
            raise ValueError("CreateFileA/WriteFile not imported")
    else:
        limp = {}
    c += _lz_code(cave + lz_code_at, lazy, cave, limp, bool(lz_watch))
    if len(c) > _TAB:
        raise AssertionError("patch code overflows its slot")
    d[ins_at:ins_at + len(c)] = c
    tab2 = struct.pack('<I', len(lz)) + b''.join(
        struct.pack('<II', int(n), int(v) & 0xFF) for n, v in sorted(lz))
    d[ins_at + _TAB2:ins_at + _TAB2 + len(tab2)] = tab2
    if lz_watch:
        d[ins_at + _FP:ins_at + _FP + 2 * FP_SIDE] = bytes(lz_watch[1])
        struct.pack_into('<I', d, ins_at + _FPK, int(lz_watch[2]))
        d[ins_at + _FPV] = int(lz_watch[3]) & 0xFF
        d[ins_at + _NAME2:ins_at + _NAME2 + len(lz_name)] = lz_name
    # the moved code section shifted lz_at only if it lay past the cave,
    # which it cannot: the cave is appended to CODE's raw data
    struct.pack_into('<i', d, lz_at + 1, cave + lz_code_at - (lz_va + 5))
    tab = struct.pack('<I', len(entries)) + b''.join(
        struct.pack('<II', int(e[0]), (int(e[1]) & 0xFF)
                    | (((int(e[2]) & 0xFF) << 8) if len(e) > 2 else 0))
        for e in sorted(entries))
    d[ins_at + _TAB:ins_at + _TAB + len(tab)] = tab
    if trace_path:
        d[ins_at + _NAME:ins_at + _NAME + len(name)] = name
    hooked = 0
    i, end = code['ro'], code['ro'] + code['rs']
    while True:
        i = d.find(b"\xE8", i, end)
        if i < 0:
            break
        va = ib + code['va'] + (i - code['ro'])
        if (va + 5 + struct.unpack_from('<i', d, i + 1)[0] == target
                and 0x50 <= d[i - 1] <= 0x57 and 0x50 <= d[i - 2] <= 0x57):
            struct.pack_into('<i', d, i + 1, cave - (va + 5))
            hooked += 1
        i += 1
    if not hooked:
        raise ValueError("no analyser call hooked")
    return bytes(d)


def read_trace(raw: bytes):
    """[(call, unwrapped ring position, len, verdict, channels, mode, fp16)]."""
    out = []
    base = prev = 0
    for i in range(len(raw) // REC):
        pos, cnt, ln, v, ch, mask, mode = struct.unpack_from('<7I', raw, i * REC)
        if pos < prev:
            base += mask + 1
        prev = pos
        out.append((cnt, base + pos, ln, v & 0xFF, ch, mode,
                    raw[i * REC + 28:i * REC + 44]))
    return out


def place(calls, src: bytes, win: int = 1 << 17, samples: int = 48):
    """Yield (call, file position, verdict) for the calls whose 16 data bytes
    are FOUND in `src` -- one file's content, or a solid run's. Calibrated on
    data bytes that occur exactly once in the file, then followed call to call
    (the ring position drifts from the file position, slowly, so a narrow
    window almost always finds it and the wide one is only the fallback)."""
    good = [c for c in calls if len(set(c[6])) > 8]
    if not good:
        return
    votes = {}
    step = max(1, len(good) // samples)
    for c in good[::step][:samples * 2]:
        j = src.find(c[6])
        if j >= 0 and src.find(c[6], j + 1) < 0:
            votes[j - c[1]] = votes.get(j - c[1], 0) + 1
    if not votes:
        return
    off = max(votes, key=votes.get)
    for cnt, u, ln, v, ch, mode, fp in calls:
        est = u + off
        if not (0 <= est < len(src)) or len(set(fp)) <= 2:
            continue
        best = None
        for w in (4096, win):
            j = src.find(fp, max(0, est - w), est + w + 16)
            while j >= 0:
                if best is None or abs(j - est) < abs(best - est):
                    best = j
                j = src.find(fp, j + 1, est + w + 16)
            if best is not None:
                break
        if best is None:
            continue
        off = best - u
        yield cnt, best, v, ch


def first_disagreement(placed, blocks):
    """(call, verdict the original implies) for the first call whose verdict
    differs from the original's block type at its position, or None.
    `blocks` is [(output position, audio?, channels)] of the ORIGINAL."""
    starts = [b[0] for b in blocks]
    for cnt, at, v, ch in placed:
        i = bisect.bisect_right(starts, at) - 1
        if i < 0:
            continue
        want = 1 if blocks[i][1] else 0
        if v != want:
            return (cnt, want, blocks[i][2]) if want else (cnt, 0)
        if want and ch != blocks[i][2]:
            return cnt, 1, blocks[i][2]
    return None
