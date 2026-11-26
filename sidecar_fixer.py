"""
Sidecar fixer — make the .sfv / .nfo / .diz / .jpg files of a folder of scene
sets hash exactly what the DATs beside them say.

Point it at ONE work folder. It holds:
  * the scene sets, one folder each (CD1/CD2 subfolders are fine);
  * the DAT(s) for them, loose in the folder — the hashes every sidecar
    has to reach;
  * optionally, incorrect copies of the sidecars, inside the sets or loose
    in the folder. A loose copy is given to the set whose DAT lists its
    name, whose volumes its sfv lists, or whose archive stem it carries.

Optionally, an extras folder: loose files, zips, rars or 7z archives of
sidecars from anywhere (a dats.site extras pack, an old collection).

For every sidecar the DAT lists that is missing or hashes wrong, the
sources are tried in this order and the first to produce the DAT's exact
bytes wins:

  1. incorrect copies in the work folder   as they are, then repaired
  2. the set's own archives                 a file_id.diz inside the set's zip
  3. the extras folder                      as they are, then repaired
  4. the RSR store / srrdb extras store     captured sidecars, .srr stored files
  5. srrdb                                  the release's .srr and its "adds",
                                            as they are, then repaired
  6. a made .sfv                            the DAT's own volume CRCs in every
                                            layout sfv tools write, under the
                                            group's headers from the RSR store
                                            (sfv_forge), its time stamp
                                            searched and a zipscript site tag
                                            solved from the CRC

"Repaired" means: line endings (CRLF, LF, the CR CR LF and double-spaced
forms of some zipscripts), BOM, trailing ^Z / newline, cp437 <-> UTF-8,
per-line trailing blanks; for an .sfv also its header and trailer kept
with the entries rebuilt from the DAT, and a time in its header re-searched.

A hit means size, CRC32 and every MD5/SHA1 the DAT carries agree. Anything
whose bytes were SOLVED from the CRC also needs an MD5 or SHA1 to agree,
because solving can reach any CRC. Nothing is written in a dry run; a
wrong file that gets replaced is moved to the backup folder, never deleted.
An .nfo or .jpg cannot be made from nothing — when no source holds it, the
report says so.
"""

import csv
import hashlib
import io
import itertools
import json
import os
import re
import shutil
import sqlite3
import string
import subprocess
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    from dat_merger import parse_dat_file
except ImportError:
    parse_dat_file = None

HERE = Path(__file__).parent
SIDECAR_EXTS = ('.sfv', '.nfo', '.diz', '.jpg', '.jpeg', '.png', '.gif', '.txt')
TEXT_EXTS = ('.sfv', '.nfo', '.diz', '.txt')
ARCHIVE_EXTS = ('.zip', '.rar', '.7z')
BACKUP_DIR_NAME = '_sidecar_backup'
SEVEN_ZIP = HERE / 'apps' / '7z.exe'
MAX_SIDECAR = 4 << 20          # a sidecar inside an archive is never bigger

_DATE_PREFIX_RE = re.compile(r'^(?:\d{4}-\d{2}-\d{2}|\d{8}|\d{4}\.\d{2}\.\d{2})[-_.]')


_TAG_PREFIX_RE = re.compile(r'^(?:\[[^\]]{1,24}\]\s*)+')


def strip_date_prefix(name: str) -> str:
    """The scene release name srrdb knows: dats.site's status tag and date
    dropped — "[NUKED] 2005-04-01-Metal_Gear_Acid_PSP-KALISTO" ->
    "Metal_Gear_Acid_PSP-KALISTO"."""
    return _DATE_PREFIX_RE.sub('', _TAG_PREFIX_RE.sub('', name.strip()))


def name_keys(name: str) -> list:
    """Every spelling a release can go by: as-is, undated, dots<->underscores."""
    out = []
    for n in (name, strip_date_prefix(name)):
        for v in (n, n.replace('.', '_'), n.replace('_', '.')):
            if v.lower() not in out:
                out.append(v.lower())
    return out


def is_sidecar(name: str) -> bool:
    return name.lower().endswith(SIDECAR_EXTS)


def _rel(name: str) -> str:
    """A DAT rom name as a release-relative posix path."""
    return str(PurePosixPath(name.replace('\\', '/').strip('/')))


def _crc(data: bytes) -> str:
    return f'{zlib.crc32(data) & 0xFFFFFFFF:08x}'


# ═══════════════════════════════════════════════════════════════════════
# TARGETS AND MATCHING
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Target:
    name: str           # release-relative path, posix
    size: int
    crc: str            # 8 hex, lower
    md5: str = ''
    sha1: str = ''

    @property
    def base(self) -> str:
        return PurePosixPath(self.name).name

    @property
    def ext(self) -> str:
        return PurePosixPath(self.name).suffix.lower()

    def matches(self, data: bytes) -> bool:
        if len(data) != self.size:
            return False
        if _crc(data) != self.crc:
            return False
        if self.md5 and hashlib.md5(data).hexdigest() != self.md5:
            return False
        if self.sha1 and hashlib.sha1(data).hexdigest() != self.sha1:
            return False
        return True

    def describe(self) -> str:
        return f'{self.size:,} B, CRC {self.crc}' + (' + MD5' if self.md5 else '') + (
            ' + SHA1' if self.sha1 else '')


def _target_of(rom: dict):
    if rom.get('mia') == 'yes' or not rom.get('crc') or rom.get('size') in (None, ''):
        return None
    try:
        size = int(rom['size'])
    except (TypeError, ValueError):
        return None
    return Target(_rel(rom['name']), size, rom['crc'].lower().zfill(8),
                  (rom.get('md5') or '').lower(), (rom.get('sha1') or '').lower())


# ═══════════════════════════════════════════════════════════════════════
# REPAIRS OF A WRONG COPY
# ═══════════════════════════════════════════════════════════════════════

def text_variants(data: bytes):
    """The ways a text sidecar gets changed after release: line endings
    converted, a BOM added, the trailing ^Z or final newline dropped or added."""
    seen = {data}
    body = data[3:] if data.startswith(b'\xef\xbb\xbf') else data
    unix = body.replace(b'\r\n', b'\n')
    for eol_name, base in (('as-is', body), ('CRLF', unix.replace(b'\n', b'\r\n')),
                           ('LF', unix)):
        core = base.rstrip(b'\x1a \t\r\n')
        nl = b'\n' if eol_name == 'LF' else b'\r\n'
        tails = (base[len(core):], b'', nl, b'\x1a', nl + b'\x1a', nl + nl)
        for tail in tails:
            v = core + tail
            if v not in seen:
                seen.add(v)
                yield f'{eol_name} line endings, end {tail!r}', v


def _recodings(data: bytes):
    """(how, bytes): the text re-encoded the ways an nfo gets mangled —
    cp437 art saved as UTF-8 and back, blanks trimmed off every line."""
    yield 'as-is', data
    try:
        u = data.decode('utf-8')
        if any(ord(c) > 127 for c in u):
            yield 'UTF-8 back to cp437', u.encode('cp437')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    try:
        yield 'cp437 to UTF-8', data.decode('cp437').encode('utf-8')
    except UnicodeDecodeError:
        pass
    for eol in (b'\r\n', b'\n'):
        if eol in data:
            trimmed = eol.join(l.rstrip(b' \t') for l in data.split(eol))
            if trimmed != data:
                yield 'trailing blanks trimmed', trimmed
            break


def text_repairs(data: bytes):
    """Every repair of a text sidecar: each re-encoding in each line-ending form."""
    seen = set()
    for how, rec in _recodings(data):
        for fix, v in [('as-is', rec)] + list(text_variants(rec)):
            if v not in seen:
                seen.add(v)
                yield (how if fix == 'as-is' else fix if how == 'as-is' else f'{how}, {fix}'), v


# ═══════════════════════════════════════════════════════════════════════
# SFV BUILDING
# ═══════════════════════════════════════════════════════════════════════

_SFV_LINE_RE = re.compile(rb'^(.+?)\s+([0-9A-Fa-f]{8})\s*$')
# Every line ending an sfv tool or zipscript has been seen to write: some
# zipscripts turn each CRLF into CR CR LF, others double-space the file.
SFV_EOLS = ('\r\n', '\n', '\r\r\n', '\r\n\r\n')


def _parse_sfv(data: bytes):
    """(comment lines, [(name bytes, crc str)]) of an sfv, line endings dropped."""
    comments, entries = [], []
    for line in data.replace(b'\r\n', b'\n').split(b'\n'):
        if line.startswith(b';'):
            comments.append(line)
        elif line.strip():
            m = _SFV_LINE_RE.match(line)
            if m:
                entries.append((m.group(1), m.group(2).decode()))
    return comments, entries


def _split_sfv(data: bytes):
    """(head lines, entry names, tail lines) of an sfv as text, CRs dropped."""
    text = data.decode('latin-1').replace('\r', '')
    head, names, tail = [], [], []
    for ln in text.split('\n'):
        if not ln.strip() and not names:
            continue
        m = _SFV_LINE_RE.match(ln.encode('latin-1')) if not ln.startswith(';') else None
        if m and not tail:
            names.append(m.group(1).decode('latin-1'))
        elif ln.strip():
            (tail if names else head).append(ln)
    return head, names, tail


def _vol_order(name: str):
    """.rar before .r00, .r01 ...; .part1 before .part2; .001 before .002."""
    n = name.lower()
    m = re.search(r'\.part(\d+)\.rar$', n)
    if m:
        return (0, int(m.group(1)), n)
    if n.endswith('.rar'):
        return (0, -1, n)
    m = re.search(r'\.([r-z])(\d\d)$', n)
    if m:
        return (1, (ord(m.group(1)) - ord('r')) * 100 + int(m.group(2)), n)
    m = re.search(r'\.(\d{3})$', n)
    if m:
        return (1, int(m.group(1)), n)
    return (2, 0, n)


def sfv_covers(target: Target, game_roms: list) -> list:
    """The candidate file lists an sfv covers: the archives of its own stem
    when the folder holds more than one set, then every archive beside it."""
    folder = str(PurePosixPath(target.name).parent)
    folder = '' if folder == '.' else folder
    stem = PurePosixPath(target.name).stem.lower()
    listed = []
    for rom in game_roms:
        t = _target_of(rom)
        if not t or is_sidecar(t.name):
            continue
        p = PurePosixPath(t.name)
        if ('' if str(p.parent) == '.' else str(p.parent)) == folder:
            listed.append((p.name, t.crc))
    if not listed:
        return []
    same_stem = [e for e in listed if PurePosixPath(e[0]).stem.lower() == stem
                 or re.sub(r'\.part\d+$', '', PurePosixPath(e[0]).stem.lower()) == stem]
    return ([same_stem] if same_stem and len(same_stem) < len(listed) else []) + [listed]


def _orders(entries, known_order=None):
    """The entry orders sfv tools write: a known copy's order, volume order,
    the DAT's own order, alphabetical."""
    out = []
    if known_order:
        pos = {n.lower(): i for i, n in enumerate(known_order)}
        out.append(sorted(entries, key=lambda e: pos.get(e[0].lower(), 1e9)))
    out += [sorted(entries, key=lambda e: _vol_order(e[0])), list(entries),
            sorted(entries, key=lambda e: e[0].lower())]
    uniq = []
    for o in out:
        if o not in uniq:
            uniq.append(o)
    return uniq


def sfv_layouts(entries, head=(), tail=(), known_order=None):
    """(how, bytes) of every way an sfv of these entries can be laid out:
    entry order, CRC case, line ending (the header's may differ), a blank
    first line, a final line ending or not."""
    head = [h.rstrip('\r') for h in head]
    tail = [t.rstrip('\r') for t in tail]
    seen = set()
    for order in _orders(entries, known_order):
        for upper in (False, True):
            lines = [f'{n} {c.upper() if upper else c.lower()}' for n, c in order]
            for eol in SFV_EOLS:
                heols = [eol] + ([e for e in SFV_EOLS if e != eol] if head or tail else [])
                for heol in heols:
                    for lead in ('', eol):
                        parts = [h + heol for h in head] + [l + eol for l in lines] + \
                                [t + heol for t in tail]
                        full = lead + ''.join(parts)
                        last = heol if tail else eol
                        for final in (True, False):
                            text = full if final else full[:-len(last)]
                            if text in seen:
                                continue
                            seen.add(text)
                            yield (f'{len(order)} entries, {"upper" if upper else "lower"}case CRCs, '
                                   f'{eol!r} line ends' + (f', header {heol!r}' if heol != eol else '')
                                   + (', blank first line' if lead else '')
                                   + ('' if final else ', no final line end')), text.encode('latin-1')


# ── a short site tag solved from the CRC ──
# zipscript-c stamps the site's tag into its header (", sfv raped by
# zipscript-c on the mighty ,dS'"). The DAT's size fixes the tag's length;
# a tag of up to 3 characters is searched, a longer one up to 6 has its
# last 4 bytes SOLVED (CRC32 is affine in them) and is kept only when every
# character is one a tag uses and the MD5/SHA1 agrees.

_TAG_CHARS = string.ascii_letters + string.digits + '-_.!'
_TAG_SLOT_RE = re.compile(r",([^,']{1,8})'\s*$")
_TAG_MARK = '\x00'


def _solve_tail4(pre: bytes, post: bytes, want_crc: int):
    """The 4 bytes X with crc32(pre + X + post) == want_crc."""
    from scene_recreator import _crc_bit_effects, _solve_gf2
    n = len(pre) + 4 + len(post)
    zero = zlib.crc32(post, zlib.crc32(b'\0\0\0\0', zlib.crc32(pre)))
    x = _solve_gf2(_crc_bit_effects(n, len(pre)), zero ^ want_crc)
    return None if x is None else x.to_bytes(4, 'little')


def solve_tag(target: Target, template_bytes: bytes):
    """template_bytes holds one _TAG_MARK where the tag goes. The tag that
    gives the DAT's hashes, or None."""
    if not (target.md5 or target.sha1):
        return None
    k = target.size - (len(template_bytes) - 1)
    if not 1 <= k <= 6:
        return None
    i = template_bytes.index(_TAG_MARK.encode())
    pre, post = template_bytes[:i], template_bytes[i + 1:]
    want = int(target.crc, 16)
    chars = _TAG_CHARS.encode()
    if k <= 3:
        c0 = zlib.crc32(pre)
        for tag in itertools.product(chars, repeat=k):
            tg = bytes(tag)
            if zlib.crc32(post, zlib.crc32(tg, c0)) == want and target.matches(pre + tg + post):
                return tg
        return None
    for head in itertools.product(chars, repeat=k - 4):
        x = _solve_tail4(pre + bytes(head), post, want)
        if x and all(b in chars for b in x):
            tg = bytes(head) + x
            if target.matches(pre + tg + post):
                return tg
    return None


def _tag_templates(head: list, tail: list, entries, known_order=None):
    """(how, bytes with a _TAG_MARK) for each zipscript tag slot in head."""
    for i, line in enumerate(head):
        m = _TAG_SLOT_RE.search(line.rstrip('\r'))
        if not m:
            continue
        ln = line.rstrip('\r')
        marked = ln[:m.start(1)] + _TAG_MARK + ln[m.end(1):]
        for how, data in sfv_layouts(entries, head[:i] + [marked] + head[i + 1:], tail, known_order):
            yield f'site tag of "{ln}" solved, {how}', data


def make_sfv(target: Target, entry_lists: list, release: str, shapes: list, log,
             forge_cache=None) -> tuple:
    """(how, bytes) of an sfv matching target, or None.

    shapes: [(label, head lines, tail lines, entry order or None, forge
    template or None)] — the incorrect copies' own headers first, then the
    group's. Each is tried bare, then with its site tag solved, then with
    its time field searched (sfv_forge)."""
    import sfv_forge
    for entries in entry_lists:
        for label, head, tail, order, _tpl in shapes:
            for how, data in sfv_layouts(entries, head, tail, order):
                if len(data) == target.size and target.matches(data):
                    return f'{label}: {how}', data
        for label, head, tail, order, _tpl in shapes:
            for how, data in _tag_templates(list(head), list(tail), entries, order):
                tag = solve_tag(target, data)
                if tag is not None:
                    i = data.index(_TAG_MARK.encode())
                    return f'{label}: {how} -> {tag.decode()!r}', data[:i] + tag + data[i + 1:]
        for label, head, tail, order, tpl in shapes:
            if tpl is None or tpl.get('field') is None:
                continue
            group = sfv_forge._group(release)
            cache = {group: {'forgeable': [dict(tpl, example=label)]}, '*': {'forgeable': []}}
            hit = sfv_forge.forge(target, entries, release, cache=cache, layouts=SFV_EOLS)
            if hit:
                return hit
        hit = sfv_forge.forge(target, entries, release, cache=forge_cache, layouts=SFV_EOLS)
        if hit:
            return hit
    return None


def group_shapes(release: str, cache: dict) -> list:
    """The make_sfv shapes of the release's group (from the RSR store and
    the extras store, sfv_forge's cache), the bare sfv first."""
    import sfv_forge
    shapes = [('bare sfv from the DAT', [], [], None, None)]
    seen = set()
    for t in cache.get(sfv_forge._group(release), {}).get('forgeable', []):
        if t.get('field') is not None:
            continue
        key = (tuple(t['head']), tuple(t['tail']))
        if key in seen or key == ((), ()):
            continue
        seen.add(key)
        shapes.append((f"{sfv_forge._group(release)}'s header (from {t.get('example', '?')})",
                       list(t['head']), list(t['tail']), None, None))
    for t in cache.get('*', {}).get('forgeable', []):
        if t.get('field') is not None:
            continue
        key = (tuple(t['head']), tuple(t['tail']))
        if key in seen:
            continue
        seen.add(key)
        shapes.append((f"a common sfv tool's header (from {t.get('example', '?')})",
                       list(t['head']), list(t['tail']), None, None))
    return shapes


def copy_shape(label: str, data: bytes):
    """A make_sfv shape from an incorrect copy of the sfv: its header, its
    trailer and its entry order; its time field, if any, for sfv_forge."""
    import sfv_forge
    head, names, tail = _split_sfv(data)
    try:
        tpl = sfv_forge._detect(data)
    except Exception:
        tpl = None
    if tpl and tpl.get('per_file'):
        tpl = None
    return (label, head, tail, names or None, tpl)


# ═══════════════════════════════════════════════════════════════════════
# DATS AND SETS
# ═══════════════════════════════════════════════════════════════════════

def _looks_like_dat(p: Path) -> bool:
    try:
        if p.stat().st_size > 256 << 20:
            return False
        with open(p, 'rb') as f:
            head = f.read(512).lstrip(b'\xef\xbb\xbf \t\r\n').lower()
    except OSError:
        return False
    return head.startswith(b'<?xml') or head.startswith(b'<datafile') or head.startswith(b'clrmamepro')


def find_dats(root: Path) -> list:
    """The DAT files in the work folder: loose in it, or in a subfolder
    holding no archives (a 'DATs' folder). A set's own folder is skipped."""
    out = []
    for p in sorted(root.iterdir()):
        if p.is_file() and p.suffix.lower() in ('.dat', '.xml') and _looks_like_dat(p):
            out.append(p)
        elif p.is_dir() and p.name != BACKUP_DIR_NAME:
            kids = list(p.iterdir())
            if not any(k.suffix.lower() in ARCHIVE_EXTS or re.search(r'\.(r\d\d|\d{3})$', k.name.lower())
                       for k in kids if k.is_file()):
                out += [k for k in kids if k.is_file() and k.suffix.lower() in ('.dat', '.xml')
                        and _looks_like_dat(k)]
    return out


def load_games(dat_dir: Path) -> list:
    """[(game name, [rom dicts])] for every game with at least one sidecar.
    dat_dir is a DAT file, a list of them, or a folder searched for them."""
    games = []
    if isinstance(dat_dir, (list, tuple)):
        paths = list(dat_dir)
    else:
        paths = [dat_dir] if dat_dir.is_file() else (
            list(dat_dir.rglob('*.dat')) + list(dat_dir.rglob('*.xml')))
    for p in paths:
        try:
            _hdr, entries = parse_dat_file(p)
        except Exception:
            continue
        for e in entries:
            if any(is_sidecar(r.get('name') or '') for r in e.roms):
                games.append((e.name, e.roms))
    return games


class DatIndex:
    def __init__(self, games: list):
        self.by_name = {}
        self.by_file = {}       # lower archive basename -> {game idx}
        self.games = games
        for i, (name, roms) in enumerate(games):
            for k in name_keys(name):
                self.by_name.setdefault(k, i)
            for r in roms:
                n = (r.get('name') or '')
                if n and not is_sidecar(n) and r.get('mia') != 'yes':
                    self.by_file.setdefault(PurePosixPath(_rel(n)).name.lower(), set()).add(i)

    def by_folder_name(self, name: str):
        for k in name_keys(name):
            if k in self.by_name:
                return self.by_name[k]
        return None

    def by_contents(self, files: list):
        """The one game that at least 80% of a folder's archive files belong to."""
        names = [f.name.lower() for f in files if not is_sidecar(f.name)]
        if not names:
            return None
        hits = {}
        for n in names:
            for g in self.by_file.get(n, ()):
                hits[g] = hits.get(g, 0) + 1
        if not hits:
            return None
        best = max(hits.values())
        winners = [g for g, h in hits.items() if h == best]
        if len(winners) == 1 and best >= 0.8 * len(names):
            return winners[0]
        return None


def find_releases(root: Path, index: DatIndex, should_stop=lambda: False, skip=()):
    """[(folder, game idx, how)]. A folder named after a release claims its
    whole subtree. Any other folder holding archives is matched by their
    names; a release split over CD1/CD2 folders resolves to their parent.
    Folders in `skip` (a backup folder inside the root) are never entered."""
    found, by_contents = [], {}
    stack = [root]
    skip = {Path(x) for x in skip}
    while stack and not should_stop():
        d = stack.pop()
        if d.name == BACKUP_DIR_NAME or d in skip:
            continue
        g = index.by_folder_name(d.name)
        if g is not None:
            found.append((d, g, 'folder name'))
            continue
        try:
            kids = list(d.iterdir())
        except OSError:
            continue
        g = index.by_contents([p for p in kids if p.is_file()])
        if g is not None and d != root:
            by_contents.setdefault(g, []).append(d)
        stack.extend(sorted((p for p in kids if p.is_dir()), reverse=True))
    named = {g for _, g, _ in found}
    for g, dirs in by_contents.items():
        if g in named:
            continue
        top = Path(os.path.commonpath(dirs)) if len(dirs) > 1 else dirs[0]
        found.append((top, g, 'archive names'))
    return found


def _archive_stems(roms: list) -> set:
    """The volume-name stems of a release ("wrx-teng2" of wrx-teng2.part01.rar)."""
    out = set()
    for r in roms:
        n = PurePosixPath(_rel(r.get('name') or '')).name.lower()
        if not n or is_sidecar(n):
            continue
        out.add(re.sub(r'(\.part\d+)?\.(rar|zip|7z|r\d\d|[s-z]\d\d|\d{3})$', '', n))
    return out


def assign_loose(loose: list, releases: list, index: DatIndex, log) -> dict:
    """{release folder: [paths]} for sidecars lying loose in the work folder
    (or a non-set subfolder): by a sidecar name only one set's DAT lists,
    by the volumes an sfv lists, or by a set's archive stem in the name."""
    by_base, by_vol, stems = {}, {}, {}
    for folder, gi, _how in releases:
        roms = index.games[gi][1]
        for r in roms:
            t = _target_of(r)
            if not t:
                continue
            (by_base if is_sidecar(t.name) else by_vol).setdefault(t.base.lower(), set()).add(folder)
        for s in _archive_stems(roms):
            stems.setdefault(s, set()).add(folder)
    out = {}
    for p in loose:
        b = p.name.lower()
        owners = by_base.get(b, set())
        how = 'its name'
        if len(owners) != 1 and b.endswith('.sfv'):
            try:
                _c, entries = _parse_sfv(p.read_bytes())
            except OSError:
                entries = []
            hits = {}
            for n, _crc_ in entries:
                for f in by_vol.get(n.decode('latin-1').lower(), ()):
                    hits[f] = hits.get(f, 0) + 1
            if hits:
                best = max(hits.values())
                owners = {f for f, h in hits.items() if h == best}
                how = 'the volumes it lists'
        if len(owners) != 1:
            stem = re.sub(r'\.[^.]+$', '', b)
            stem = re.sub(r'([-_. ]?(wrong|bad|old|orig|copy|\(\d+\)))+$', '', stem)
            owners = stems.get(stem, set())
            how = 'its archive stem'
        if len(owners) == 1:
            f = next(iter(owners))
            out.setdefault(f, []).append(p)
            log(f'Loose {p.name} belongs to {f.name} (by {how})', 'dim')
        else:
            log(f'Loose {p.name}: no set claims it' + (f' ({len(owners)} could)' if owners else ''), 'warn')
    return out


# ═══════════════════════════════════════════════════════════════════════
# ARCHIVES (the set's own, and the extras folder's)
# ═══════════════════════════════════════════════════════════════════════

def _7z_list(path: Path) -> list:
    """[(member name, size, crc)] of an archive, by 7-Zip. [] if it can't."""
    if not SEVEN_ZIP.is_file():
        return []
    try:
        r = subprocess.run([str(SEVEN_ZIP), 'l', '-slt', '-p-', str(path)], capture_output=True,
                           timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        return []
    out, cur = [], {}
    for line in r.stdout.decode('utf-8', 'replace').splitlines() + ['']:
        if not line.strip():
            if cur.get('Path') and cur.get('Folder') != '+' and cur.get('Size', '').isdigit():
                out.append((cur['Path'].replace('\\', '/'), int(cur['Size']),
                            (cur.get('CRC') or '').lower().zfill(8)))
            cur = {}
        elif ' = ' in line:
            k, v = line.split(' = ', 1)
            cur[k.strip()] = v.strip()
    return out[1:] if out and out[0][0].replace('\\', '/') == str(path).replace('\\', '/') else out


def _7z_read(path: Path, member: str):
    try:
        r = subprocess.run([str(SEVEN_ZIP), 'e', '-so', '-p-', str(path), member],
                           capture_output=True, timeout=120,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _first_volume(p: Path) -> bool:
    n = p.name.lower()
    if n.endswith('.zip') or n.endswith('.7z'):
        return True
    if not n.endswith('.rar'):
        return False
    m = re.search(r'\.part(\d+)\.rar$', n)
    return not m or int(m.group(1)) == 1


class ArchiveMember:
    """A sidecar-sized file inside an archive, read when first wanted."""

    def __init__(self, archive: Path, name: str, size: int, crc: str, zf=False):
        self.archive, self.name, self.size, self.crc, self._zf = archive, name, size, crc, zf
        self._data = None

    @property
    def label(self) -> str:
        return f'{self.archive.name} > {self.name}'

    def read(self):
        if self._data is None:
            if self._zf:
                try:
                    with zipfile.ZipFile(self.archive) as z:
                        self._data = z.read(self.name)
                except Exception:
                    self._data = b''
            else:
                self._data = _7z_read(self.archive, self.name) or b''
        return self._data


def archive_members(path: Path) -> list:
    """The sidecar files inside one archive (zips natively, rar/7z by 7-Zip)."""
    out = []
    if path.suffix.lower() == '.zip':
        try:
            with zipfile.ZipFile(path) as z:
                for i in z.infolist():
                    if not i.is_dir() and i.file_size <= MAX_SIDECAR and is_sidecar(i.filename):
                        out.append(ArchiveMember(path, i.filename, i.file_size, f'{i.CRC:08x}', zf=True))
            return out
        except Exception:
            pass
    for name, size, crc in _7z_list(path):
        if size <= MAX_SIDECAR and is_sidecar(name):
            out.append(ArchiveMember(path, name, size, crc))
    return out


def extras_dirs(value) -> list:
    """The extras folders a config names: a list, or one string of them
    separated by ';'."""
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value or '').split(';')
    return [Path(v.strip()).expanduser() for v in items if v and str(v).strip()]


class ExtrasFolder:
    """Every sidecar in the extras folders — loose, or inside zips, rars and
    7z archives — indexed by (size, CRC) and by file name. Several folders
    can be given (packs from different DAT authors hold different copies);
    each hit is labelled with the folder it came from."""

    def __init__(self, roots, log, should_stop=lambda: False):
        self.by_hash, self.by_name = {}, {}
        self.files = 0
        for root in ([roots] if isinstance(roots, Path) else roots):
            if should_stop():
                break
            if not root.is_dir():
                log(f'Extras folder {root} does not exist — skipped', 'warn')
                continue
            log(f'Indexing the extras folder {root}', 'info')
            before, archives = self.files, 0
            for p in root.rglob('*'):
                if should_stop():
                    break
                if not p.is_file():
                    continue
                rel = f'{root.name}/{p.relative_to(root).as_posix()}'
                if is_sidecar(p.name) and p.stat().st_size <= MAX_SIDECAR:
                    try:
                        data = p.read_bytes()
                    except OSError:
                        continue
                    # Read again when wanted: a whole extras pack is too big to hold
                    self._add(rel, p.name, len(data), _crc(data), lambda p=p: p.read_bytes())
                elif p.suffix.lower() in ARCHIVE_EXTS and _first_volume(p):
                    archives += 1
                    for m in archive_members(p):
                        self._add(f'{rel} > {m.name}', PurePosixPath(m.name).name,
                                  m.size, m.crc, m.read)
            log(f'  {self.files - before:,} sidecar(s) in {root.name} '
                f'({archives:,} archive(s) opened)', 'dim')

    def _add(self, label, name, size, crc, read):
        self.files += 1
        item = (label, name, read)
        self.by_hash.setdefault((size, crc), []).append(item)
        self.by_name.setdefault(name.lower(), []).append(item)

    def exact(self, target: Target):
        for label, name, read in self.by_hash.get((target.size, target.crc), []):
            yield label, read(), name

    def named(self, target: Target, release: str):
        """Copies by the same file name, and anything in a folder or archive
        named after the release — candidates to repair."""
        seen = set()
        keys = name_keys(release)
        for label, name, read in self.by_name.get(target.base.lower(), []):
            seen.add(label)
            yield label, read(), name
        if target.ext:
            for items in self.by_name.values():
                for label, name, read in items:
                    if label in seen or not name.lower().endswith(target.ext):
                        continue
                    low = label.lower()
                    if any(k in low for k in keys):
                        seen.add(label)
                        yield label, read(), name


# ═══════════════════════════════════════════════════════════════════════
# STORES AND SRRDB
# ═══════════════════════════════════════════════════════════════════════

def _srr_stored_files(srr: Path, work: Path) -> list:
    """Files stored in an .srr, extracted under work. [] if rescene can't."""
    try:
        import rescene.main as rm  # type: ignore
        out = work / f'srr_{abs(hash(str(srr)))}'
        out.mkdir(parents=True, exist_ok=True)
        rm.extract_files(srr_file=str(srr), out_folder=str(out), extract_paths=True)
        return [p for p in out.rglob('*') if p.is_file()]
    except Exception:
        return []


class Sources:
    """Everything outside the release folder that may hold a release's
    sidecars, opened lazily and once per release."""

    def __init__(self, use_rsr: bool, use_extras: bool, use_srrdb: bool, log):
        self.use_rsr, self.use_extras, self.use_srrdb = use_rsr, use_extras, use_srrdb
        self.log = log
        self._rsr_db = HERE / 'rsr_index.db'
        self._extras_db = HERE / 'srrdb_extras.db'
        self._srrdb = None
        self.srrdb_calls = 0

    def _rsr_paths(self, release: str) -> list:
        if not self.use_rsr or not self._rsr_db.is_file():
            return []
        con = sqlite3.connect(f'file:{self._rsr_db}?mode=ro', uri=True)
        try:
            keys = name_keys(release)
            q = ('SELECT rsr_path FROM releases WHERE lower(name) IN (%s)'
                 % ','.join('?' * len(keys)))
            return [Path(r[0]) for r in con.execute(q, keys) if r[0]]
        finally:
            con.close()

    def rsr(self, release: str, work: Path):
        """(label, bytes, name) for every sidecar and .srr-stored file the RSR
        store captured for this release."""
        for rsr in self._rsr_paths(release):
            if not rsr.is_file():
                continue
            try:
                with zipfile.ZipFile(rsr) as z:
                    names = set(z.namelist())
                    man = json.loads(z.read('manifest.json'))
                    for f in man.get('sidecars', []):
                        if f.get('stored') in names:
                            yield f'RSR store ({f["name"]})', z.read(f['stored']), f['name']
                    srr_name = man.get('srr') if isinstance(man.get('srr'), str) else 'release.srr'
                    if srr_name not in names:
                        srr_name = 'release.srr' if 'release.srr' in names else None
                    if srr_name:
                        srr = work / f'rsr_{rsr.stem}.srr'
                        srr.write_bytes(z.read(srr_name))
                        for p in _srr_stored_files(srr, work):
                            yield f'RSR store .srr ({p.name})', p.read_bytes(), p.name
            except Exception as e:
                self.log(f'    RSR store: could not read {rsr.name}: {e}', 'dim')

    def extras(self, target: Target):
        if not self.use_extras or not self._extras_db.is_file():
            return
        con = sqlite3.connect(f'file:{self._extras_db}?mode=ro', uri=True)
        try:
            rows = con.execute('SELECT path FROM files WHERE crc = ? AND size = ?',
                               (target.crc, target.size)).fetchall()
        finally:
            con.close()
        for (path,) in rows:
            try:
                yield f'srrdb extras store ({Path(path).name})', Path(path).read_bytes(), Path(path).name
            except OSError:
                pass

    def _api(self):
        if self._srrdb is None:
            from srrdb_tool import SrrdbToolAPI
            self._srrdb = SrrdbToolAPI()
            self._srrdb._log = lambda m, c='info': None
        return self._srrdb

    def srr(self, release: str, work: Path):
        """The release's .srr from srr_cache, downloaded only when not cached."""
        cache = HERE / 'srr_cache'
        cached = None
        for n in (strip_date_prefix(release), release):
            for v in (n, n.replace('.', '_'), n.replace('_', '.')):
                p = cache / f'{v}.srr'
                if not cached and p.is_file() and p.stat().st_size:
                    cached = p
        if not cached and self.use_srrdb:
            self.srrdb_calls += 1
            self.log(f'    srrdb: asking for {strip_date_prefix(release)}', 'dim')
            res = self._api().download_srr(strip_date_prefix(release), str(work / 'dl'))
            if res.get('ok'):
                cached = Path(res['srr_path'])
            else:
                self.log(f'    srrdb: {res.get("error", "no .srr")}', 'dim')
        if not cached:
            return
        for p in _srr_stored_files(cached, work):
            yield f'srrdb .srr ({p.name})', p.read_bytes(), p.name

    def adds(self, release: str, target: Target, work: Path):
        """srrdb 'adds' whose CRC is the one the DAT wants (proof jpgs mostly)."""
        if not self.use_srrdb:
            return
        api = self._api()
        self.srrdb_calls += 1
        details = api._srrdb_details(strip_date_prefix(release))
        for a in (details or {}).get('adds', []) or []:
            if str(a.get('crc', '')).lower().zfill(8) != target.crc or not a.get('id'):
                continue
            dest = work / 'adds' / Path(a['name']).name
            res = api._download_add(details['_resolved_name'], a['id'], a['name'],
                                    dest, a.get('crc'))
            if res.get('ok'):
                yield f'srrdb add ({Path(a["name"]).name})', dest.read_bytes(), Path(a['name']).name
            else:
                self.log(f'    srrdb add {a["name"]}: {res.get("error")}', 'dim')


def release_texts(release: str, work: Path, srrdb: bool = False, log=lambda m, c='': None) -> dict:
    """lower basename -> [(label, bytes)] of every .nfo/.diz/.txt the RSR
    store or the release's .srr holds — candidate texts for a zip whose
    nfo/diz was edited (scene_recreator.technique_sidecar_revision)."""
    src = Sources(True, False, srrdb, log)
    out = {}
    for gen in (src.rsr(release, work), src.srr(release, work)):
        for label, data, name in gen:
            if name.lower().endswith(TEXT_EXTS):
                out.setdefault(Path(name).name.lower(), []).append((label, data))
    return out


# ═══════════════════════════════════════════════════════════════════════
# FINDING ONE SIDECAR
# ═══════════════════════════════════════════════════════════════════════

class _Cached:
    """Replays a source generator so a release's .srr is read once even when
    several of its sidecars need it."""

    def __init__(self, gen_fn):
        self._fn, self._items, self._done = gen_fn, [], False

    def __iter__(self):
        yield from self._items
        if self._done:
            return
        for item in self._fn():
            self._items.append(item)
            yield item
        self._done = True


def _repair(target: Target, data: bytes):
    """(how, bytes) of a wrong copy repaired to the DAT's hashes, or None.
    For an sfv, only the text repairs — its entries are rebuilt in make_sfv."""
    if target.ext in TEXT_EXTS:
        for how, v in text_repairs(data):
            if len(v) == target.size and target.matches(v):
                return how, v
    if target.ext == '.sfv':
        for how, v in _comment_cuts(data):
            for fix, w in text_repairs(v):
                if len(w) == target.size and target.matches(w):
                    return how + ('' if fix == 'as-is' else f', {fix}'), w
    return None


def _comment_cuts(data: bytes):
    """An sfv with comment lines taken off: a QuickPAR ';Q2-' trailer (srrdb
    keeps the copy a site's QuickPAR touched), the trailing comments, the
    header, or every comment."""
    eol = b'\r\n' if b'\r\n' in data else b'\n'
    lines = data.split(eol)
    final = lines[-1] == b''
    if final:
        lines = lines[:-1]
    first = next((i for i, l in enumerate(lines) if l and not l.startswith(b';')), len(lines))
    last = max((i for i, l in enumerate(lines) if l and not l.startswith(b';')), default=-1)
    cuts = [('QuickPAR ;Q2- trailer removed', [l for l in lines if not l.startswith(b';Q2-')]),
            ('trailing comment lines removed', lines[:last + 1]),
            ('header comment lines removed', lines[first:]),
            ('every comment line removed', [l for l in lines if not l.startswith(b';')])]
    seen = {data}
    for how, kept in cuts:
        v = eol.join(kept) + (eol if final and kept else b'')
        if v not in seen:
            seen.add(v)
            yield how, v


def _try_tier(target: Target, name: str, items, log, wrong_sfvs: list, relevant=None):
    """Try one tier's (label, bytes, file name) items: as they are, then
    repaired when the item is the same kind of file. Incorrect sfvs are
    kept for make_sfv. (how, bytes) or None."""
    n = tried = 0
    for label, data, fname in items:
        n += 1
        if not data:
            continue
        if len(data) == target.size and target.matches(data):
            log(f'    ✓ {label} is the DAT\'s file as it is', 'dim')
            return label, data
        same_kind = (fname or '').lower().endswith(target.ext) and (
            relevant is None or relevant(fname))
        if not same_kind:
            continue
        tried += 1
        if target.ext == '.sfv':
            wrong_sfvs.append((label, data))
        hit = _repair(target, data)
        if hit:
            return f'{label}, repaired: {hit[0]}', hit[1]
        log(f'    · {label} ({len(data):,} B, CRC {_crc(data)}): no repair reaches the DAT\'s hashes'
            + (' — kept to rebuild from' if target.ext == '.sfv' else ''), 'dim')
    if n == 0:
        log(f'    · {name}: nothing', 'dim')
    elif tried == 0:
        log(f'    · {name}: {n} file(s), none with the DAT\'s hashes', 'dim')
    return None


def find_sidecar(target: Target, ctx: dict, log):
    """(how, bytes) for target, or None, trying the sources in order and
    logging each."""
    folder, release, roms = ctx['folder'], ctx['release'], ctx['roms']
    wrong_sfvs = []
    same_base = lambda n: PurePosixPath(n.replace('\\', '/')).name.lower() == target.base.lower()

    # 1. incorrect copies in the work folder
    here = folder / target.name
    items = []
    if here.is_file():
        items.append((f'{target.name} on disk', here.read_bytes(), target.base))
    for rel, p in ctx['local'].items():
        if rel != target.name.lower() and rel.endswith(target.ext) and rel not in ctx['dat_names']:
            items.append((f'{rel} in the set', p.read_bytes(), p.name))
    for p in ctx['loose']:
        if p.name.lower().endswith(target.ext):
            items.append((f'loose {p.name}', p.read_bytes(), p.name))
    log(f'    1. incorrect copies in the folder: {len(items)}', 'dim')
    hit = _try_tier(target, 'incorrect copies', items, log, wrong_sfvs)
    if hit:
        return hit

    # 2. the set's own archives
    members = ctx['members']()
    log(f'    2. inside the set\'s archives: {len(members)} sidecar(s)', 'dim')
    hit = _try_tier(target, 'set archives',
                    ((m.label, m.read(), m.name) for m in members
                     if (m.size, m.crc) == (target.size, target.crc) or same_base(m.name)),
                    log, wrong_sfvs)
    if hit:
        return hit

    # 3. the extras folder
    extras = ctx['extras']
    if extras is not None and extras.files:
        log(f'    3. extras folders ({extras.files:,} sidecar(s))', 'dim')
        hit = _try_tier(target, 'extras folder',
                        itertools.chain(extras.exact(target), extras.named(target, release)),
                        log, wrong_sfvs)
        if hit:
            return hit
    else:
        log('    3. extras folders: none set', 'dim')

    # 4. the RSR store and the srrdb extras store
    src = ctx['src']
    log('    4. RSR store / srrdb extras store', 'dim')
    hit = _try_tier(target, 'stores', itertools.chain(ctx['rsr'], src.extras(target)),
                    log, wrong_sfvs, relevant=same_base)
    if hit:
        return hit

    # 5. srrdb
    if src.use_srrdb or ctx['srr_cached']:
        log('    5. srrdb', 'dim')
        hit = _try_tier(target, 'srrdb', itertools.chain(ctx['srr'], src.adds(release, target, ctx['work'])),
                        log, wrong_sfvs, relevant=same_base)
        if hit:
            return hit
    else:
        log('    5. srrdb: off', 'dim')

    # 6. make the sfv
    if target.ext == '.sfv' and ctx['make_sfv']:
        lists = sfv_covers(target, roms)
        if not lists:
            log('    6. make the sfv: the DAT lists no volumes beside it', 'dim')
            return None
        shapes = [copy_shape(f'rebuilt from {label}', data) for label, data in wrong_sfvs]
        shapes += ctx['group_shapes']
        log(f'    6. make the sfv: {len(lists[-1])} volume(s) from the DAT, '
            f'{len(wrong_sfvs)} incorrect cop{"y" if len(wrong_sfvs) == 1 else "ies"} '
            f'+ {len(ctx["group_shapes"]) - 1} group/tool header(s) to lay them out under', 'dim')
        hit = make_sfv(target, lists, release, shapes, log, ctx['forge_cache'])
        if hit:
            return hit
        import sfv_forge
        log(f'    · no layout reaches the DAT\'s hashes ({sfv_forge.describe(release, ctx["forge_cache"])})', 'dim')
    return None


# ═══════════════════════════════════════════════════════════════════════
# THE RSR STORE
# ═══════════════════════════════════════════════════════════════════════

def update_store(folder: Path, release: str, targets: list, backup: Path, dry: bool,
                 stats: dict, details: dict, log):
    """Give the release's .rsr in the RSR store the sidecars now proven right
    on disk: a stored one whose bytes differ is replaced, one it never had is
    added (rsr_tool.update_sidecars). No re-scan — the archive recipe never
    reads a sidecar. Only files matching the DAT go in."""
    rsrs = [p for p in Sources(True, False, False, log)._rsr_paths(release) if p.is_file()]
    if not rsrs:
        log('  RSR store: this release is not captured — nothing to update', 'dim')
        return
    fixes = []
    for t in targets:
        p = folder / t.name
        if p.is_file() and p.stat().st_size == t.size:
            data = p.read_bytes()
            if t.matches(data):
                fixes.append((t.name, data))
    if not fixes:
        return
    for rsr in rsrs:
        try:
            with zipfile.ZipFile(rsr) as z:
                man = json.loads(z.read('manifest.json'))
                names = set(z.namelist())
        except Exception as e:
            log(f'  RSR store: cannot read {rsr.name}: {e}', 'warn')
            continue
        have = {f['name'].lower(): f for f in man.get('sidecars', [])}
        plan = []
        for name, data in fixes:
            old = have.get(name.lower())
            if old and old.get('sha256') == hashlib.sha256(data).hexdigest() and old.get('stored') in names:
                continue
            plan.append((name, data, 'wrong' if old else 'missing'))
        if not plan:
            log(f'  RSR store: {rsr.name} already carries the right sidecars', 'dim')
            continue
        what = ', '.join(f'{n} ({w})' for n, _d, w in plan)
        if dry:
            log(f'  RSR store: would update {rsr.name} — {what}', 'info')
            stats['rsr_updated'] += 1
            details['rsr'].append({'release': folder.name, 'file': what, 'result': 'would update'})
            continue
        try:
            import rsr_tool
            api = rsr_tool.RsrToolAPI()
            api._app_dir = HERE
            api._log = lambda *a, **k: None
            api._emit = lambda *a, **k: None
            res = api.update_sidecars(rsr, [(n, d) for n, d, _w in plan], backup / '_rsr_store')
        except Exception as e:
            log(f'  RSR store: could not update {rsr.name}: {e}', 'err')
            continue
        if res.get('changed'):
            stats['rsr_updated'] += 1
            details['rsr'].append({'release': folder.name, 'file': what, 'result': 'updated'})
            log(f'  ✓ RSR store: {rsr.name} updated — {what} (old .rsr in '
                f'{backup / "_rsr_store"})', 'ok')


# ═══════════════════════════════════════════════════════════════════════
# THE RUN
# ═══════════════════════════════════════════════════════════════════════

def safe_dir_name(name: str) -> str:
    """A DAT game name as a Windows folder name (same rule as the zip fixer's
    output folders, so both write a release into ONE folder)."""
    return re.sub(r'[<>:"/\\|?*]', '_', name or 'Unknown').strip()


def _backup_slot(dst: Path) -> Path:
    """dst, or dst with ' (2)', ' (3)'... when an earlier run already backed
    up a file of that name: a backup is never overwritten."""
    if not dst.exists():
        return dst
    for i in range(2, 10000):
        q = dst.with_name(f'{dst.stem} ({i}){dst.suffix}')
        if not q.exists():
            return q
    return dst


def _file_crc(p: Path) -> str:
    c = 0
    with open(p, 'rb') as f:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            c = zlib.crc32(b, c)
    return f'{c & 0xFFFFFFFF:08x}'


def _copy_verified(src: Path, dst: Path) -> bool:
    """Copy src to dst and check the copy's size; False (and no dst left
    behind) if it went wrong."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
        if dst.stat().st_size == src.stat().st_size:
            return True
    except OSError:
        pass
    try:
        dst.unlink()
    except OSError:
        pass
    return False


def run(config: dict, log=print, progress=lambda pct, label='': None,
        should_stop=lambda: False) -> dict:
    """Fix every set in config['folder'] against the DATs in it.

    config: folder (sets + DATs + incorrect sidecars), extras_dirs (optional:
    a list, or folders separated by ';'),
    dat_dir (optional: DATs somewhere else), backup_dir (default
    <folder>/_sidecar_backup), dry_run, add_missing, use_rsr_store,
    use_extras_store, use_srrdb, make_sfv.

    Output mode (the combined "Fix releases" run): out_dir set means the sets
    are left as they are and every right or fixed sidecar is written to
    out_dir/<DAT game name>/; with copy_other_files the set's other files
    that match the DAT (size + CRC32) go there too (skip_zip_files leaves
    .zip to the zip fixer). out_mode 'move' adds each original that reached
    the output to the caller's moved_out list (the caller removes them once
    every pass is done) and moves a wrong original to the backup folder.
    only_dat_dir: read the DATs from dat_dir only, not from the folder;
    dat_files: an explicit list of DAT files (overrides both).
    Returns {'stats': {...}, 'details': {category: [rows]}, 'report': path}."""
    root = Path(config.get('folder') or config.get('releases') or '').expanduser()
    dry = bool(config.get('dry_run', False))
    add_missing = bool(config.get('add_missing', True))
    backup = Path(config.get('backup_dir') or (root / BACKUP_DIR_NAME))
    out_dir = Path(config['out_dir']).expanduser() if config.get('out_dir') else None
    out_move = out_dir is not None and config.get('out_mode') == 'move'
    moved_out = config['moved_out'] if isinstance(config.get('moved_out'), list) else []
    copy_others = out_dir is not None and config.get('copy_other_files', True)
    skip_zip_files = bool(config.get('skip_zip_files'))
    stats = dict(releases=0, ok=0, fixed=0, added=0, not_found=0, rsr_updated=0, copied=0)
    details = {k: [] for k in ('fixed', 'added', 'not_found', 'extra', 'rsr')}

    if parse_dat_file is None:
        log('ERROR: dat_merger is not available to read DATs.', 'err')
        return {'stats': stats, 'details': details}
    if not root.is_dir():
        log('ERROR: choose the folder holding the sets and their DATs.', 'err')
        return {'stats': stats, 'details': details}

    if config.get('dat_files'):
        dats = [Path(x) for x in config['dat_files']]
    else:
        dats = [] if config.get('dat_dir') and config.get('only_dat_dir') else find_dats(root)
    if config.get('dat_dir') and not config.get('dat_files'):
        d = Path(config['dat_dir']).expanduser()
        dats += [d] if d.is_file() else [p for p in d.rglob('*') if p.suffix.lower() in ('.dat', '.xml')]
    if not dats:
        log(f'ERROR: no DAT in {root} — put the sets\' DAT(s) in the folder beside them.', 'err')
        return {'stats': stats, 'details': details}
    log(f'Reading {len(dats)} DAT(s): ' + ', '.join(p.name for p in dats[:6])
        + (' ...' if len(dats) > 6 else ''), 'info')
    index = DatIndex(load_games(dats))
    log(f'  {len(index.games):,} DAT release(s) list sidecars', 'dim')
    try:
        backup_res = backup.resolve()
    except OSError:
        backup_res = backup

    def in_backup(p: Path) -> bool:
        if BACKUP_DIR_NAME in p.parts:
            return True
        try:
            p.resolve().relative_to(backup_res)
            return True
        except (ValueError, OSError):
            return False

    releases = find_releases(root, index, should_stop, skip=(backup_res,))
    log(f'{len(releases):,} set(s) found in {root}', 'info')
    if not releases:
        return {'stats': stats, 'details': details}

    claimed = {f.resolve() for f, _g, _h in releases}
    loose = []
    for p in root.rglob('*'):
        if p.is_file() and is_sidecar(p.name) and not in_backup(p) and \
                not any(a.resolve() in claimed for a in p.parents):
            loose.append(p)
    if loose:
        log(f'{len(loose)} loose sidecar(s) in the folder', 'info')
    loose_of = assign_loose(loose, releases, index, log)

    extras = None
    dirs = extras_dirs(config.get('extras_dirs') or config.get('extras_dir'))
    if dirs:
        extras = ExtrasFolder(dirs, log, should_stop)
    if dry:
        log('Dry run: nothing will be written.', 'warn')
    src = Sources(config.get('use_rsr_store', True), config.get('use_extras_store', True),
                  config.get('use_srrdb', True), log)
    make = config.get('make_sfv', True)
    forge_cache = None
    if make:
        import sfv_forge
        forge_cache = sfv_forge.load_cache()
        if not forge_cache and src.use_rsr:
            log('Learning sfv headers from the RSR store (once; cached in sfv_templates.json)', 'info')
            forge_cache = sfv_forge.build_cache(log=lambda m: log('  ' + m, 'dim'))
    work = Path(tempfile.mkdtemp(prefix='sidecar_fixer_'))
    rows = []
    try:
        for n, (folder, gi, how_matched) in enumerate(releases, 1):
            if should_stop():
                log('Stopped.', 'warn')
                break
            game, roms = index.games[gi]
            progress(int(100 * (n - 1) / max(1, len(releases))), folder.name)
            stats['releases'] += 1
            targets = [t for t in map(_target_of, roms) if t and is_sidecar(t.name)]
            dat_names = {t.name.lower() for t in map(_target_of, roms) if t} | {
                _rel(r.get('name') or '').lower() for r in roms}
            local = {p.relative_to(folder).as_posix().lower(): p
                     for p in folder.rglob('*') if p.is_file() and is_sidecar(p.name)
                     and BACKUP_DIR_NAME not in p.relative_to(folder).parts}
            for rel in sorted(set(local) - dat_names):
                details['extra'].append({'file': rel, 'game_name': game})

            gdir = out_dir / safe_dir_name(game) if out_dir else None
            todo, right = [], []
            for t in targets:
                p = folder / t.name
                if p.is_file() and p.stat().st_size == t.size and t.matches(p.read_bytes()):
                    stats['ok'] += 1
                    right.append(t.name)
                    if gdir and not dry:
                        if _copy_verified(p, gdir / t.name):
                            if out_move:
                                moved_out.append(p)
                        else:
                            log(f'  ERROR: could not copy {t.name} to {gdir}', 'err')
                elif p.is_file() or add_missing:
                    todo.append(t)
            via = '' if how_matched == 'folder name' else f' (matched by {how_matched} to {game})'
            log(f'[{n}/{len(releases)}] {folder.name}{via}', 'info')
            if right:
                log(f'  already right: {", ".join(right)}'
                    + (f' ({"moving" if out_move else "copied"} to the output)' if gdir else ''), 'dim')
            if copy_others:
                # The set's other DAT files (rar volumes, images...), checked
                # by size and CRC32, so the output holds the whole release
                for r in roms:
                    t = _target_of(r)
                    if not t or is_sidecar(t.name) or (skip_zip_files and t.ext == '.zip'):
                        continue
                    p = folder / t.name
                    if not p.is_file() or p.stat().st_size != t.size:
                        continue
                    if _file_crc(p) != t.crc:
                        log(f'  {t.name}: CRC differs from the DAT, not copied', 'warn')
                        continue
                    stats['copied'] += 1
                    if not dry:
                        if _copy_verified(p, gdir / t.name):
                            if out_move:
                                moved_out.append(p)
                        else:
                            log(f'  ERROR: could not copy {t.name} to {gdir}', 'err')
            store_dir = gdir if gdir else folder
            if not todo:
                if config.get('update_rsr_store'):
                    update_store(store_dir, game, targets, backup, dry, stats, details, log)
                continue
            rwork = work / f'r{n}'
            rwork.mkdir()
            members_cache = []

            def members(folder=folder, cache=members_cache):
                if not cache:
                    for p in sorted(folder.rglob('*')):
                        if p.is_file() and p.suffix.lower() in ARCHIVE_EXTS and _first_volume(p):
                            cache.extend(archive_members(p))
                    cache.append(None)
                return [m for m in cache if m is not None]

            srr_cached = any((HERE / 'srr_cache' / f'{v}.srr').is_file() for v in name_keys(game))
            ctx = dict(folder=folder, release=game, roms=roms, local=local, dat_names=dat_names,
                       loose=loose_of.get(folder, []), members=members, extras=extras, src=src,
                       work=rwork, make_sfv=make, forge_cache=forge_cache, srr_cached=srr_cached,
                       rsr=_Cached(lambda g=game, w=rwork: src.rsr(g, w)),
                       srr=_Cached(lambda g=game, w=rwork: src.srr(g, w)),
                       group_shapes=group_shapes(game, forge_cache or {}) if make else [])
            for t in todo:
                if should_stop():
                    break
                p = folder / t.name
                existed = p.is_file()
                log(f'  • {t.name} — {"WRONG on disk" if existed else "missing"}; '
                    f'the DAT wants {t.describe()}', 'info')
                t0 = time.time()
                hit = find_sidecar(t, ctx, log)
                row = {'release': folder.name, 'game_name': game, 'file': t.name,
                       'was': 'wrong' if existed else 'missing'}
                if not hit:
                    stats['not_found'] += 1
                    row['result'] = 'not found'
                    row['why'] = ('no source holds it, and an .nfo/.jpg cannot be made'
                                  if t.ext != '.sfv' else 'no source or made layout reaches the DAT\'s hashes')
                    details['not_found'].append(row)
                    log(f'  ✗ {t.name}: NOT FOUND — {row["why"]} ({time.time() - t0:.0f}s)', 'warn')
                    rows.append(row)
                    continue
                how, data = hit
                key = 'fixed' if existed else 'added'
                stats[key] += 1
                row.update(result=key, technique=how)
                details[key].append(row)
                rows.append(row)
                log(f'  ✓ {t.name}: {"FIXED" if existed else "ADDED"} — {how} '
                    f'(size, CRC32{", MD5" if t.md5 else ""}{", SHA1" if t.sha1 else ""} match)', 'ok')
                if dry:
                    continue
                if gdir:
                    q = gdir / t.name
                    q.parent.mkdir(parents=True, exist_ok=True)
                    q.write_bytes(data)
                    log(f'    written to {q}', 'dim')
                    if existed and out_move:
                        dst = _backup_slot(backup / folder.name / t.name)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(p), str(dst))
                        log(f'    the wrong copy moved to {dst}', 'dim')
                    continue
                if existed:
                    dst = _backup_slot(backup / folder.name / t.name)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(dst))
                    log(f'    the wrong copy moved to {dst}', 'dim')
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                local[t.name.lower()] = p
            if config.get('update_rsr_store'):
                update_store(store_dir, game, targets, backup, dry, stats, details, log)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    progress(100, 'done')
    report = None
    if rows:
        rep_dir = HERE / 'reports'
        rep_dir.mkdir(exist_ok=True)
        report = rep_dir / f'sidecar_fix_{time.strftime("%Y-%m-%d_%H%M%S")}.csv'
        with open(report, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=['release', 'game_name', 'file', 'was',
                                              'result', 'technique', 'why'])
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, '') for k in w.fieldnames})
    log(f'Done: {stats["releases"]} set(s), {stats["ok"]} sidecar(s) already right, '
        f'{stats["fixed"]} fixed, {stats["added"]} added, {stats["not_found"]} not found'
        + (f', {stats["copied"]} other file(s) {"would be " if dry else ""}copied to the output'
           if copy_others else '')
        + (f'; {stats["rsr_updated"]} .rsr {"would be " if dry else ""}updated in the RSR store'
           if config.get('update_rsr_store') else '')
        + (f'; {src.srrdb_calls} srrdb request(s)' if src.srrdb_calls else '')
        + (f'. Report: {report}' if report else ''), 'ok')
    if details['extra']:
        log(f'{len(details["extra"])} sidecar(s) in the sets are not in the DAT — left alone '
            '(listed under "Not in DAT").', 'dim')
    return {'stats': stats, 'details': details, 'report': str(report) if report else ''}
