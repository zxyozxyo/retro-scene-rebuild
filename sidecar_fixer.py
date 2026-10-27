"""
Sidecar fixer — make a release folder's .sfv / .nfo / .diz / .jpg files match
what the DAT says, in batch.

Point it at a DAT folder and a folder of releases. Each release folder is
matched to its DAT <game> by name (with or without the dats.site date prefix)
or, failing that, by the archive names inside it. For every sidecar the DAT
lists that is missing or does not hash right, the first source that produces
the DAT's exact bytes wins:

  1. the file on disk, repaired   line endings, trailing ^Z / newline, BOM
  2. another file in the release  a renamed or misplaced copy
  3. the RSR store                the release's captured sidecars + its .srr
  4. the srrdb extras store       srrdb_extras.db, by CRC + size
  5. srrdb                        the release's .srr (cached in srr_cache)
                                  and its "adds" (proof jpgs etc.)
  6. a rebuilt .sfv               from the DAT's own archive CRCs

Nothing is written unless size, CRC32 and every MD5/SHA1 the DAT carries all
match. A wrong file that gets replaced is moved to the backup folder, never
deleted. An .nfo or .jpg cannot be made from nothing; when no source has it,
the report says so.
"""

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
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
BACKUP_DIR_NAME = '_sidecar_backup'

_DATE_PREFIX_RE = re.compile(r'^(?:\d{4}-\d{2}-\d{2}|\d{8}|\d{4}\.\d{2}\.\d{2})[-_.]')


def strip_date_prefix(name: str) -> str:
    return _DATE_PREFIX_RE.sub('', name)


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

    def matches(self, data: bytes) -> bool:
        if len(data) != self.size:
            return False
        if f'{zlib.crc32(data) & 0xFFFFFFFF:08x}' != self.crc:
            return False
        if self.md5 and hashlib.md5(data).hexdigest() != self.md5:
            return False
        if self.sha1 and hashlib.sha1(data).hexdigest() != self.sha1:
            return False
        return True


def _target_of(rom: dict):
    if rom.get('mia') == 'yes' or not rom.get('crc') or rom.get('size') in (None, ''):
        return None
    try:
        size = int(rom['size'])
    except (TypeError, ValueError):
        return None
    return Target(_rel(rom['name']), size, rom['crc'].lower().zfill(8),
                  (rom.get('md5') or '').lower(), (rom.get('sha1') or '').lower())


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


# ═══════════════════════════════════════════════════════════════════════
# SFV REBUILD
# ═══════════════════════════════════════════════════════════════════════

_SFV_LINE_RE = re.compile(rb'^(.+?)\s+([0-9A-Fa-f]{8})\s*$')


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


def _vol_order(name: str):
    """.rar before .r00, .r01 ...; .part1 before .part2."""
    n = name.lower()
    m = re.search(r'\.part(\d+)\.rar$', n)
    if m:
        return (0, int(m.group(1)), n)
    if n.endswith('.rar'):
        return (0, -1, n)
    m = re.search(r'\.([r-z])(\d\d)$', n)
    if m:
        return (1, (ord(m.group(1)) - ord('r')) * 100 + int(m.group(2)), n)
    return (2, 0, n)


def sfv_variants(target: Target, game_roms: list, local_sfv: bytes = None):
    """Rebuilt sfv files for target, from the DAT's own CRCs for the files the
    sfv covers. The header comment (tool name, date) cannot be invented: it is
    kept from a local copy when there is one, otherwise left out."""
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
        return
    same_stem = [e for e in listed if PurePosixPath(e[0]).stem.lower() == stem
                 or re.sub(r'\.part\d+$', '', PurePosixPath(e[0]).stem.lower()) == stem]
    covers = [same_stem] if same_stem and len(same_stem) < len(listed) else []
    covers.append(listed)

    headers = [[]]
    local_order = None
    if local_sfv:
        comments, entries = _parse_sfv(local_sfv)
        if comments:
            headers.insert(0, comments)
        local_order = [n.decode('latin-1').lower() for n, _ in entries]

    for files in covers:
        orders = [sorted(files, key=lambda e: _vol_order(e[0])), list(files)]
        if local_order:
            pos = {n: i for i, n in enumerate(local_order)}
            orders.insert(0, sorted(files, key=lambda e: pos.get(e[0].lower(), 1e9)))
        tried = set()
        for header in headers:
            for order in orders:
                for upper in (True, False):
                    lines = [n.encode('latin-1') + b' ' + (c.upper() if upper else c).encode()
                             for n, c in order]
                    for eol in (b'\r\n', b'\n'):
                        body = eol.join(list(header) + lines)
                        for tail in (eol, b''):
                            v = body + tail
                            if len(v) == target.size and v not in tried:
                                tried.add(v)
                                yield (f'rebuilt sfv ({len(order)} files, '
                                       f'{"kept" if header else "no"} header)'), v


# ═══════════════════════════════════════════════════════════════════════
# DAT INDEX AND RELEASE MATCHING
# ═══════════════════════════════════════════════════════════════════════

def load_games(dat_dir: Path) -> list:
    """[(game name, [rom dicts])] for every game with at least one sidecar."""
    games = []
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


def find_releases(root: Path, index: DatIndex, should_stop=lambda: False):
    """[(folder, game idx, how)]. A folder named after a release claims its
    whole subtree. Any other folder holding archives is matched by their
    names; a release split over CD1/CD2 folders resolves to their parent."""
    found, by_contents = [], {}
    stack = [root]
    while stack and not should_stop():
        d = stack.pop()
        if d.name == BACKUP_DIR_NAME:
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
        if g is not None:
            by_contents.setdefault(g, []).append(d)
        stack.extend(sorted((p for p in kids if p.is_dir()), reverse=True))
    named = {g for _, g, _ in found}
    for g, dirs in by_contents.items():
        if g in named:
            continue
        top = Path(os.path.commonpath(dirs)) if len(dirs) > 1 else dirs[0]
        found.append((top, g, 'archive names'))
    return found


# ═══════════════════════════════════════════════════════════════════════
# SOURCES
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
        """(label, bytes) for every sidecar and .srr-stored file the RSR store
        captured for this release."""
        for rsr in self._rsr_paths(release):
            if not rsr.is_file():
                continue
            try:
                with zipfile.ZipFile(rsr) as z:
                    names = set(z.namelist())
                    man = json.loads(z.read('manifest.json'))
                    for f in man.get('sidecars', []):
                        if f.get('stored') in names:
                            yield f'RSR store ({f["name"]})', z.read(f['stored'])
                    srr_name = man.get('srr') if isinstance(man.get('srr'), str) else 'release.srr'
                    if srr_name not in names:
                        srr_name = 'release.srr' if 'release.srr' in names else None
                    if srr_name:
                        srr = work / f'rsr_{rsr.stem}.srr'
                        srr.write_bytes(z.read(srr_name))
                        for p in _srr_stored_files(srr, work):
                            yield f'RSR store .srr ({p.name})', p.read_bytes()
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
                yield f'srrdb extras store ({Path(path).name})', Path(path).read_bytes()
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
            res = self._api().download_srr(strip_date_prefix(release), str(work / 'dl'))
            if res.get('ok'):
                cached = Path(res['srr_path'])
            else:
                self.log(f'    srrdb: {res.get("error", "no .srr")}', 'dim')
        if not cached:
            return
        for p in _srr_stored_files(cached, work):
            yield f'srrdb .srr ({p.name})', p.read_bytes()

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
                yield f'srrdb add ({Path(a["name"]).name})', dest.read_bytes()
            else:
                self.log(f'    srrdb add {a["name"]}: {res.get("error")}', 'dim')


# ═══════════════════════════════════════════════════════════════════════
# THE RUN
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


def _find(target: Target, folder: Path, game_roms: list, local: dict,
          dat_names: set, release_src: list, src: Sources, release: str, work: Path):
    """(how, bytes, consumed local path or None) for target, or None."""
    is_text = target.name.lower().endswith(TEXT_EXTS)
    here = folder / target.name
    on_disk = here.read_bytes() if here.is_file() else None

    if on_disk is not None and is_text:
        for how, v in text_variants(on_disk):
            if target.matches(v):
                return f'repaired: {how}', v, None

    ext = PurePosixPath(target.name).suffix.lower()
    for rel, p in local.items():
        if rel == target.name.lower() or not rel.endswith(ext):
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        cands = [('as-is', data)] + (list(text_variants(data)) if is_text else [])
        for how, v in cands:
            if target.matches(v):
                spare = None if rel in dat_names else p
                return f'copied from {rel}' + ('' if how == 'as-is' else f', {how}'), v, spare

    for gen in release_src:
        for how, data in gen:
            if target.matches(data):
                return how, data, None
            if is_text:
                for fix, v in text_variants(data):
                    if target.matches(v):
                        return f'{how}, {fix}', v, None

    for how, data in src.extras(target):
        if target.matches(data):
            return how, data, None

    for how, data in src.adds(release, target, work):
        if target.matches(data):
            return how, data, None

    if ext == '.sfv':
        for how, v in sfv_variants(target, game_roms, on_disk):
            if target.matches(v):
                return how, v, None
    return None


def run(config: dict, log=print, progress=lambda pct, label='': None,
        should_stop=lambda: False) -> dict:
    """Fix every release under config['releases'] against config['dat_dir'].

    config: releases, dat_dir, dry_run, add_missing, use_rsr_store,
    use_extras_store, use_srrdb, backup_dir (default <releases>/_sidecar_backup).
    Returns {'stats': {...}, 'details': {category: [rows]}, 'report': path}."""
    root = Path(config.get('releases', '')).expanduser()
    dat_dir = Path(config.get('dat_dir', '')).expanduser()
    dry = bool(config.get('dry_run', False))
    add_missing = bool(config.get('add_missing', True))
    backup = Path(config.get('backup_dir') or (root / BACKUP_DIR_NAME))
    stats = dict(releases=0, ok=0, fixed=0, added=0, not_found=0)
    details = {k: [] for k in ('fixed', 'added', 'not_found', 'extra')}

    if parse_dat_file is None:
        log('ERROR: dat_merger is not available to read DATs.', 'err')
        return {'stats': stats, 'details': details}
    if not root.is_dir() or not dat_dir.exists():
        log('ERROR: choose a releases folder and a DAT folder or file.', 'err')
        return {'stats': stats, 'details': details}

    log(f'Reading DATs in {dat_dir}', 'info')
    index = DatIndex(load_games(dat_dir))
    log(f'{len(index.games):,} DAT release(s) list sidecars', 'dim')
    releases = find_releases(root, index, should_stop)
    log(f'{len(releases):,} release folder(s) matched under {root}', 'info')
    if dry:
        log('Dry run: nothing will be written.', 'warn')
    src = Sources(config.get('use_rsr_store', True), config.get('use_extras_store', True),
                  config.get('use_srrdb', True), log)
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

            todo = []
            for t in targets:
                p = folder / t.name
                if p.is_file() and p.stat().st_size == t.size and t.matches(p.read_bytes()):
                    stats['ok'] += 1
                elif p.is_file() or add_missing:
                    todo.append(t)
            if not todo:
                continue
            via = '' if how_matched == 'folder name' else f' (matched by {how_matched} to {game})'
            log(f'{folder.name}{via}', 'info')
            rwork = work / f'r{n}'
            rwork.mkdir()
            release_src = [_Cached(lambda g=game, w=rwork: src.rsr(g, w)),
                           _Cached(lambda g=game, w=rwork: src.srr(g, w))]
            for t in todo:
                p = folder / t.name
                existed = p.is_file()
                hit = _find(t, folder, roms, local, dat_names, release_src, src, game, rwork)
                row = {'release': folder.name, 'game_name': game, 'file': t.name,
                       'was': 'wrong' if existed else 'missing'}
                if not hit:
                    stats['not_found'] += 1
                    row['result'] = 'not found'
                    details['not_found'].append(row)
                    log(f'  ✗ {t.name}: {row["was"]}, no source has the DAT\'s bytes', 'warn')
                    rows.append(row)
                    continue
                how, data, spare = hit
                key = 'fixed' if existed else 'added'
                stats[key] += 1
                row.update(result=key, technique=how)
                details[key].append(row)
                rows.append(row)
                log(f'  ✓ {t.name}: {"FIXED" if existed else "ADDED"} — {how}', 'ok')
                if dry:
                    continue
                bdir = backup / folder.name
                if existed:
                    dst = bdir / t.name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(dst))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                if spare is not None and spare.is_file():
                    dst = bdir / spare.relative_to(folder)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(spare), str(dst))
                    local.pop(spare.relative_to(folder).as_posix().lower(), None)
                local[t.name.lower()] = p
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
                                              'result', 'technique'])
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, '') for k in w.fieldnames})
    log(f'Done: {stats["releases"]} release(s), {stats["ok"]} sidecar(s) already right, '
        f'{stats["fixed"]} fixed, {stats["added"]} added, {stats["not_found"]} not found'
        + (f'; {src.srrdb_calls} srrdb request(s)' if src.srrdb_calls else '')
        + (f'. Report: {report}' if report else ''), 'ok')
    if details['extra']:
        log(f'{len(details["extra"])} sidecar(s) on disk are not in the DAT — left alone '
            '(listed under "Not in DAT").', 'dim')
    return {'stats': stats, 'details': details, 'report': str(report) if report else ''}
