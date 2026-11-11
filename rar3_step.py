"""RAR 3.x (Win32) packs that stepped DOWN a level part-way through a file.

WHY. Three XBOX 2005 JEDi releases (Ski_Racing_2006_Ft_Hermann_Maier_PAL_
MULTi5_XBOX_DVDRiP, Kingdom_Under_Fire_Heroes_XBOX_DVDRIP, Heroes_Of_The_
Pacific_PAL_XBOX_DVDRIP) never rebuilt: rar 3.42 -m3 writes the first
87-200 MB of the stream exactly and then every decision differs, the
original some 6% LARGER. Decoded, Ski Racing's original stops using filters
for good after filter 1,453 (stock goes on to 14,036) and from there packs
exactly as -m2 would, then from 291 MB exactly as -m1:

  analysed byte 104,366,080   filters off, level 3 -> 2
  analysed byte 291,209,216   level 2 -> 1
  -> the 361,734,118 B stream byte-identical (stock 341,448,919).

What made the packing PC do that is not known; this build simply does it at
the same places. Positions are in the filter ANALYSER's byte count, which
runs one 32 KB chunk ahead of the compressor, so a step lands on a chunk
boundary (multiples of 32,768 from the file's start).

HOW. The dispatcher's one call to the analyser is sent to a code cave
(CODE's raw data grown into its virtual slack, as rar2_mmv does). Before
the analyser each pending step whose position the analysed-byte counter
has reached writes the level fields (+0x30 level, +0x34 / +0x38 the match
search depth, the values rar sets for -m1..-m5) and switches filters off;
after it, with filters off, every filter region is dropped from the
analyser's list so that data is packed plain.

A recipe carries `@R3STEP<pos>:<level>` per step (level as in -m<level>).
"""
from __future__ import annotations

import struct

# what rar 3.42 sets per -m level (dumped from the compressor object)
LEVEL_FIELDS = {1: (0, 2, 0), 2: (1, 6, 1), 3: (2, 12, 3), 4: (3, 30, 7),
                5: (4, 70, 17)}
CHUNK = 32768
_CAVE = 0x400
# lea eax,[ebx+0DA4]; mov edx,[edx]; call ANALYSER   (in the dispatcher)
_SITE = bytes.fromhex("8D83A40D00008B12E8")
# the dispatcher's own frame: 16 regions at ebp-138, their count at ebp-0C
_FRAME = bytes.fromhex("8D95C8FEFFFF" "8945F4" "52" "8D4DF4" "51" "6A10")
_COUNTER = 0x14A4           # analysed bytes (low dword), compressor object


# Found by hand (B:/rtmp/jedi/stepfind.py), keyed by the SHA-1 of the
# original stream's first 64 KB: (build, steps).
KNOWN_STEPS = {
    # Ski_Racing_2006_Ft_Hermann_Maier_PAL_MULTi5_XBOX_DVDRiP-JEDi
    "c323555d25bb2fc38d3254c399ebba387ee02aa0": ("2005-02-22_rar342.exe", [(104366080, 2), (291209216, 1)]),
}


def known_steps(head: bytes):
    """(build, steps) on record for an original stream starting with `head`
    (its first 64 KB), or None."""
    import hashlib
    return KNOWN_STEPS.get(hashlib.sha1(head[:65536]).hexdigest())


def _sections(d):
    from rar2_mmv import _sections as s
    return s(d)


def _site(d, code):
    lo, hi = code['ro'], code['ro'] + code['rs']
    at = d.find(_SITE, lo, hi)
    if at < 0 or d.find(_SITE, at + 1, hi) >= 0:
        return None
    if d.rfind(_FRAME, max(lo, at - 0x40), at) < 0:
        return None
    return at + len(_SITE) - 1          # the E8


def supported(exe: bytes) -> bool:
    try:
        d = bytearray(exe)
        _pe, secs, _ib = _sections(d)
        code = secs[0]
        return (_site(d, code) is not None
                and code['vs'] - code['rs'] >= _CAVE)
    except Exception:
        return False


def parse(switches) -> list[tuple[int, int]]:
    out = []
    for s in switches:
        s = str(s)
        if s.startswith("@R3STEP"):
            p, lv = s[len("@R3STEP"):].split(":")
            out.append((int(p), int(lv)))
    return sorted(out)


def switch(steps) -> list[str]:
    return [f"@R3STEP{int(p)}:{int(lv)}" for p, lv in sorted(steps)]


def build(exe: bytes, steps) -> bytes:
    """The build with each (analysed-byte position, -m level) step done."""
    d = bytearray(exe)
    _pe, secs, ib = _sections(d)
    code = secs[0]
    at = _site(d, code)
    if at is None:
        raise ValueError("filter analyser call site not found")
    if code['vs'] - code['rs'] < _CAVE:
        raise ValueError("no slack in CODE for the patch")
    steps = sorted((int(p), int(lv)) for p, lv in steps)
    if not steps or any(lv not in LEVEL_FIELDS for _p, lv in steps):
        raise ValueError(f"bad steps {steps}")
    site_va = ib + code['va'] + (at - code['ro'])
    analyser = site_va + 5 + struct.unpack_from('<i', d, at + 1)[0]
    ins = code['ro'] + code['rs']
    cave = ib + code['va'] + code['rs']
    d[ins:ins] = bytes(_CAVE)
    struct.pack_into('<I', d, code['off'] + 16, code['rs'] + _CAVE)
    for s in secs[1:]:
        if s['ro'] >= ins:
            struct.pack_into('<I', d, s['off'] + 20, s['ro'] + _CAVE)
    ch = struct.unpack_from('<I', d, code['off'] + 36)[0]
    struct.pack_into('<I', d, code['off'] + 36, ch | 0x80000000)  # writable
    data = cave + 0x300
    RET, NOF, DONE = data, data + 4, data + 0x10
    if len(steps) > 0xE0:
        raise ValueError("too many steps")
    c = bytearray()
    c += b"\x8F\x05" + struct.pack('<I', RET)                  # pop [RET]
    for i, (p, lv) in enumerate(steps):
        c += b"\x80\x3D" + struct.pack('<I', DONE + i) + b"\x00\x75\x00"
        j1 = len(c) - 1                                        # done? skip
        c += (b"\x81\xBB" + struct.pack('<I', _COUNTER)
              + struct.pack('<I', p) + b"\x72\x00")
        j2 = len(c) - 1                                        # not yet? skip
        c += b"\xC6\x05" + struct.pack('<I', DONE + i) + b"\x01"
        c += b"\xC6\x05" + struct.pack('<I', NOF) + b"\x01"    # filters off
        for k, v in zip((0x30, 0x34, 0x38), LEVEL_FIELDS[lv]):
            c += b"\xC7\x83" + struct.pack('<iI', k, v)        # level fields
        c[j1] = len(c) - (j1 + 1)
        c[j2] = len(c) - (j2 + 1)
    c += b"\xE8" + struct.pack('<i', analyser - (cave + len(c) + 5))
    c += b"\x80\x3D" + struct.pack('<I', NOF) + b"\x00\x74\x00"
    jz = len(c) - 1                                            # filters on
    c += b"\x60"                                               # pushad
    c += b"\x8D\xB5\xC8\xFE\xFF\xFF\x8B\xFE"                   # esi=edi=regions
    c += b"\x8B\x4D\xF4\x33\xD2"                               # ecx=n; edx=0
    c += b"\xE3\x00"; j0 = len(c) - 1
    lp = len(c)
    c += b"\x83\x3E\x02\x73\x00"; jae = len(c) - 1             # a filter? drop
    c += b"\x51\xB9\x04\x00\x00\x00\xF3\xA5\x59\x42"           # keep: copy; n++
    c += b"\xEB\x00"; jk = len(c) - 1
    drop = len(c)
    c += b"\x83\xC6\x10"                                       # skip it
    nx = len(c)
    c += b"\xE2" + bytes([(lp - (len(c) + 2)) & 0xFF])         # loop
    end = len(c)
    c += b"\x89\x55\xF4\x61"                                   # count; popad
    out = len(c)
    c += b"\xFF\x35" + struct.pack('<I', RET) + b"\xC3"        # back
    c[j0] = end - (j0 + 1); c[jae] = drop - (jae + 1)
    c[jk] = nx - (jk + 1); c[jz] = out - (jz + 1)
    if len(c) > 0x300:
        raise ValueError("too many steps for the cave")
    d[ins:ins + len(c)] = c
    struct.pack_into('<i', d, at + 1, cave - (site_va + 5))
    return bytes(d)


if __name__ == "__main__":
    import sys
    src, dst = sys.argv[1], sys.argv[2]
    st = parse(sys.argv[3:])
    open(dst, "wb").write(build(open(src, "rb").read(), st))
    print("ok", st)
