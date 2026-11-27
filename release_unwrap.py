"""Unpack downloads: releases that arrive double-packed.

A download often wraps a whole scene release (its .zip or .rar set plus the
.nfo/.sfv/.diz) in one more .7z, .rar or .zip. This module finds those
wrappers in a folder, by what is inside them compared with the DATs, and
unpacks them so the Scene Recreator's normal passes can fix the release.

A wrapper is an archive that
  - is not itself a file the DATs list (a release's own .zip or .rar is
    never unpacked; the zip pass reads those), and
  - holds at least one of the DATs' non-sidecar files (same size and CRC,
    or same name and size), or a folder named after a DAT release, or only
    other archives that turn out to be wrappers (a zip inside a rar).

Nothing here deletes a file it did not make. What happens to a wrapper
once its release is complete is the caller's choice (release_status +
dispose)."""
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import sidecar_fixer as sc

WRAPPER_EXTS = ('.7z', '.rar', '.zip')
UNPACK_PREFIX = '_recreator_unpack'
MAX_DEPTH = 3
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class DatLookup:
    """Every DAT release with all its files (not only the ones with sidecars)."""

    def __init__(self, dats):
        self.games = []                 # [(name, [Target])]
        self.by_base = {}               # lower basename -> [(gi, Target)]
        self.by_sc = {}                 # (size, crc) -> [(gi, Target)]
        self.by_key = {}                # name key -> gi
        for p in dats:
            try:
                _hdr, entries = sc.parse_dat_file(Path(p))
            except Exception:
                continue
            for e in entries:
                ts = [t for t in (sc._target_of(r) for r in e.roms) if t]
                if not ts:
                    continue
                gi = len(self.games)
                self.games.append((e.name, ts))
                for k in sc.name_keys(e.name):
                    self.by_key.setdefault(k, gi)
                for t in ts:
                    self.by_base.setdefault(t.base.lower(), []).append((gi, t))
                    if not sc.is_sidecar(t.name):
                        self.by_sc.setdefault((t.size, t.crc), []).append((gi, t))

    @property
    def sizes(self) -> set:
        if not hasattr(self, '_sizes'):
            self._sizes = {s for s, _c in self.by_sc}
        return self._sizes

    def is_dat_file(self, name: str) -> bool:
        return PurePosixPath(name.replace('\\', '/')).name.lower() in self.by_base

    def game_of_folder(self, name: str):
        for k in sc.name_keys(name):
            if k in self.by_key:
                return self.by_key[k]
        return None


def volumes_of(first: Path) -> list:
    """The archive and every other volume of the same set beside it."""
    n = first.name
    out = [first]
    m = re.match(r'(?i)^(.*)\.part0*1\.rar$', n)
    try:
        sibs = list(first.parent.iterdir())
    except OSError:
        return out
    if m:
        pat = re.compile(r'(?i)^' + re.escape(m.group(1)) + r'\.part\d+\.rar$')
    elif n.lower().endswith('.rar'):
        pat = re.compile(r'(?i)^' + re.escape(n[:-4]) + r'\.[rs]\d\d$')
    else:
        return out
    out += sorted(p for p in sibs if p != first and p.is_file() and pat.match(p.name))
    return out


def probe(archive: Path, look: DatLookup):
    """What the archive holds against the DATs.
    Returns (verdict, game idx or None, entries): verdict 'wrapper', 'maybe'
    (holds only archives, so it has to be unpacked to tell), or None."""
    entries = sc._7z_list(archive)
    if not entries:
        return None, None, entries
    hits = {}
    tops = {e[0].split('/', 1)[0] for e in entries if '/' in e[0]}
    for name, size, crc in entries:
        base = PurePosixPath(name).name.lower()
        found = {gi for gi, _t in look.by_sc.get((size, crc), ())}
        found |= {gi for gi, t in look.by_base.get(base, ())
                  if not sc.is_sidecar(t.name) and t.size == size}
        for gi in found:
            hits[gi] = hits.get(gi, 0) + 1
    for t in tops:
        gi = look.game_of_folder(t)
        if gi is not None:
            hits[gi] = hits.get(gi, 0) + 1
    if hits:
        return 'wrapper', max(hits, key=hits.get), entries
    payload = [e for e in entries if not sc.is_sidecar(e[0])
               and not e[0].lower().endswith(('.url', '.lnk', '.htm', '.html'))]
    if payload and all(e[0].lower().endswith(WRAPPER_EXTS) for e in payload):
        return 'maybe', None, entries
    return None, None, entries


def find_wrappers(root: Path, look: DatLookup, skip=(), should_stop=lambda: False):
    """[(archive, verdict, game idx, entries)] under root, first volumes only."""
    skip = [Path(s) for s in skip]
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        if should_stop():
            break
        if any(_inside(d, s) for s in skip) or d.name.startswith(UNPACK_PREFIX) \
                or d.name == sc.BACKUP_DIR_NAME:
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            p = d / fn
            if not fn.lower().endswith(WRAPPER_EXTS) or not sc._first_volume(p):
                continue
            if look.is_dat_file(fn):
                continue
            verdict, gi, entries = probe(p, look)
            if verdict:
                out.append((p, verdict, gi, entries))
    return out


def _inside(a: Path, b: Path) -> bool:
    try:
        a.resolve().relative_to(b.resolve())
        return True
    except (ValueError, OSError):
        return False


def extract(archive: Path, dest: Path, log) -> bool:
    """Unpack the whole archive (all volumes) into dest with 7-Zip. Existing
    files are never overwritten."""
    if not sc.SEVEN_ZIP.is_file():
        log('  7-Zip (apps/7z.exe) is missing: cannot unpack', 'err')
        return False
    dest.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([str(sc.SEVEN_ZIP), 'x', '-p-', '-y', '-aos', f'-o{dest}', str(archive)],
                           capture_output=True, creationflags=_NO_WINDOW)
    except OSError as e:
        log(f'  could not run 7-Zip: {e}', 'err')
        return False
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode('utf-8', 'replace').strip().splitlines()
        tail = msg[-1] if msg else f'exit code {r.returncode}'
        if 'password' in ' '.join(msg).lower():
            tail = 'it is password protected'
        log(f'  could not unpack {archive.name}: {tail}', 'warn')
        return False
    return True


def unpack_nested(folder: Path, look: DatLookup, log, depth: int = 1) -> int:
    """Unpack wrappers found inside an unpacked folder (our own copy), in
    place, removing each inner wrapper once unpacked. Returns how many."""
    if depth > MAX_DEPTH:
        return 0
    n = 0
    for p in sorted(folder.rglob('*')):
        if not p.is_file() or not p.name.lower().endswith(WRAPPER_EXTS) \
                or not sc._first_volume(p) or look.is_dat_file(p.name):
            continue
        verdict, _gi, _e = probe(p, look)
        if not verdict:
            continue
        vols = volumes_of(p)
        if extract(p, p.parent, log):
            log(f'  unpacked the inner {p.name}', 'dim')
            for v in vols:
                try:
                    v.unlink()
                except OSError:
                    pass
            n += 1
    if n:
        n += unpack_nested(folder, look, log, depth + 1)
    return n


def content_root(tmp: Path) -> Path:
    """The folder holding the release: tmp, or its one subfolder."""
    kids = [k for k in tmp.iterdir()]
    if len(kids) == 1 and kids[0].is_dir():
        return kids[0]
    return tmp


def best_game(folder: Path, look: DatLookup):
    """The DAT release most of the unpacked files belong to (by size+CRC,
    then name+size, then folder name)."""
    hits = {}
    gi = look.game_of_folder(folder.name)
    if gi is not None:
        hits[gi] = 1
    for p in folder.rglob('*'):
        if not p.is_file():
            continue
        size = p.stat().st_size
        cands = {g for g, t in look.by_base.get(p.name.lower(), ())
                 if not sc.is_sidecar(t.name) and t.size == size}
        if not cands and size in look.sizes:
            cands = {g for g, _t in look.by_sc.get((size, sc._file_crc(p)), ())}
        for g in cands:
            hits[g] = hits.get(g, 0) + 1
    return max(hits, key=hits.get) if hits else None


def release_status(where: Path, look: DatLookup, gi: int):
    """(right, total, missing names) of DAT release gi's files in `where`."""
    _name, targets = look.games[gi]
    right, missing = 0, []
    for t in targets:
        p = where / t.name
        try:
            if p.is_file() and p.stat().st_size == t.size and sc._file_crc(p) == t.crc:
                right += 1
                continue
        except OSError:
            pass
        missing.append(t.name)
    return right, len(targets), missing


def move_tree_into(src: Path, dst: Path):
    """Move src's children into dst (same drive: renames)."""
    dst.mkdir(parents=True, exist_ok=True)
    for k in list(src.iterdir()):
        shutil.move(str(k), str(dst / k.name))


def dispose(archive: Path, how: str, backup: Path, root: Path, log, dry: bool) -> int:
    """Move every volume of a wrapper to the backup folder, delete it, or
    leave it. Returns the number of files handled."""
    vols = volumes_of(archive)
    if how == 'keep':
        log(f'  the download {archive.name} is left where it is', 'dim')
        return 0
    n = 0
    for v in vols:
        if dry:
            log(f'  would {"delete" if how == "delete" else "move to the backup folder"}: {v.name}', 'dim')
            continue
        try:
            if how == 'delete':
                v.unlink()
                log(f'  deleted the download {v.name}', 'dim')
            else:
                try:
                    rel = v.resolve().relative_to(root.resolve())
                except ValueError:
                    rel = Path(v.name)
                bak = sc._backup_slot(backup / rel)
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(v), str(bak))
                log(f'  the download {v.name} moved to {bak}', 'dim')
            n += 1
        except OSError as e:
            log(f'  could not {how} {v}: {e}', 'warn')
    return n

