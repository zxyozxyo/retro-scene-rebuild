"""Public export for the Retro Scene Rebuild website.

Reads the local store + index (never writes to them) and produces:
  <out>/rsr/<SYS>/<release>.rsr   public copy, only for "clean" releases
  <out>/catalog/<SYS>.jsonl       one line per release (page data)
  <out>/extras/<SYS>/<release>/   the release's nfo/sfv/proof files (allow-listed)

A release is public only when EVERY entry of its .rsr passes an allow-list:
  * recipe data: manifest, release.srr, deltas (4 KB in total at most: header-sized fixes only),
    zip/lha skeletons and preflate.bin reconstruction data (one over 64 KB may be at most
    half the packed size of the files they rebuild);
  * files that sat BESIDE the archives (sidecars): nfo/diz/sfv/text that
    really is text, and proof images that really are images (5 MB at most);
  * files from INSIDE the archives: only nfo/diz/sfv and disc sheets
    (cue/ccd/toc/gdi/m3u) that really are text.
Anything else (images or documents from inside an archive, carried zip/LHA
members, archives carried whole, splice/tail/graft/av/sfx blobs, large diffs)
means the release page is shown but the .rsr is not hosted.

The public manifest drops the local source folder path; nothing else changes.

usage: python export.py <out dir> [--workers N] [--only NAME ...]
"""
import argparse, hashlib, json, os, re, sqlite3, sys, zipfile, zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "rsr_index.db"
SIDECAR_TEXT = {".nfo", ".diz", ".sfv", ".txt", ".md5", ".sha1", ".url", ".log",
                ".htm", ".html", ".nzb", ".m3u", ".cue", ".srr"}
SIDECAR_IMG = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
INNER_OK = {".nfo", ".diz", ".sfv", ".cue", ".ccd", ".toc", ".gdi", ".m3u"}
IMG_MAX = 5_000_000
DELTA_MAX = 4 * 1024      # a diff holds original archive bytes; header-sized only
BIN_MAGIC = (b"\xd0\xcf\x11\xe0", b"MZ", b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf", b"\x1f\x8b", b"CD001")
RECON_MIN, RECON_RATIO = 64 * 1024, 0.5   # see withheld_reasons
AV_MAX = 4 * 1024         # one RAR authenticity-verification block
IMG_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM")
NFO_MAX = 96 * 1024
DATE_RE = re.compile(r"(?:^|[\\/])(?:\[[^\]]*\]\s*)?(\d{4}-\d{2}-\d{2})-")


def is_meta(n: str) -> bool:
    top = n.split("/")[0]
    return (n in ("manifest.json", "release.srr") or top == "deltas"
            or (top == "zips" and n.endswith(("/skeleton.bin", "/preflate.bin")))
            or (top == "lha" and n.endswith("/skeleton.bin")))


def is_text(b: bytes) -> bool:
    if b.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True                     # UTF-16 / UTF-8 with BOM
    if b.startswith(BIN_MAGIC):
        return False                    # a Word document, program or archive
    return b.count(0) <= len(b) // 100  # cp437 art uses 0x01-0x1F, never NULs


def is_image(b: bytes) -> bool:
    return b.startswith(IMG_MAGIC) and b.strip(b"\x00") != b""


def _load_delta_clear() -> dict:
    """{release: .rsr SHA-256} whose large diffs were mapped onto the original
    volumes and carry no compressed file data (deltacheck.py). A different
    .rsr than the one checked gets no pass."""
    p = Path(__file__).with_name("delta_clear.json")
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("clear", {})
    except (OSError, ValueError):
        return {}


DELTA_CLEAR = _load_delta_clear()


def withheld_reasons(z: zipfile.ZipFile, man: dict, rsr_sha256: str | None = None) -> list:
    """Why this .rsr can't be published; [] when every entry passes."""
    names = {i.filename: i for i in z.infolist()}
    refs, methods = {}, {}
    for c in man.get("sidecars", []):
        if c.get("stored"):
            refs[c["stored"]] = ("sidecar", c["name"], c.get("size") or 0)
    for s in man.get("sets", []):
        for f in s.get("files", []):
            if f.get("stored"):
                refs[f["stored"]] = ("inner", f["name"], f.get("size") or 0)
                if f.get("method") is not None:
                    methods[f["stored"]] = f["method"]
    why = set()
    if any((s.get("recipe") or {}).get("label") == "carried" for s in man.get("sets", [])):
        why.add("archive carried whole")
    delta = sum(i.file_size for n, i in names.items() if n.startswith("deltas/"))
    cleared = rsr_sha256 is not None and DELTA_CLEAR.get(man.get("release")) == rsr_sha256
    if delta > DELTA_MAX and not cleared:
        why.add(f"large diff ({delta // 1024:,} KB)")
    # Reconstruction data is a small description of how the zipper packed the
    # files. One as big as the packed data itself means the packer could not be
    # modelled and the compressed files were kept instead (a damaged zip).
    for k, st in enumerate(man.get("sets", [])):
        top = "lha" if st.get("format") == "LHA" else "zips"
        packed = sum(f.get("packed_size") or 0 for f in st.get("files", []) if f.get("source") == "content")
        for blob in (f"{top}/{k}/preflate.bin", f"{top}/{k}/skeleton.bin"):
            if blob in names and names[blob].file_size > RECON_MIN and names[blob].file_size > packed * RECON_RATIO:
                why.add("zip reconstruction data as large as the packed files")
    for n in names:
        if is_meta(n):
            continue
        # RAR authenticity verification: the group's registered-RAR signature
        # block (~220 B per volume) and its end marker. No file data; allowed
        # by the site owner 2026-11-16.
        if n.startswith("av/") and names[n].file_size <= AV_MAX:
            continue
        if n not in refs:
            top = n.split("/")[0]
            why.add({"zips": "carried zip member data", "lha": "carried LHA member data"}.get(
                top, f"carried archive data ({top})"))
            continue
        where, fname, size = refs[n]
        # An LHA member name can carry an Amiga file note after a NUL
        # ("FiLE_iD.DiZ\0PSYCHO TERMiNAL ..."): the type is the name's.
        ext = Path(fname.split("\x00")[0]).suffix.lower()
        head = z.open(n).read(1 << 20)
        if n.endswith(".def") and methods.get(n, 8) == 8:
            # a zip member carried as its original deflate stream: judge what it
            # unpacks to (an nfo kept compressed is still an nfo). A member the
            # zip only STORED (method 0) is carried as-is and is judged raw.
            try:
                head = zlib.decompressobj(-15).decompress(z.read(n), 1 << 20)
            except zlib.error:
                head = b"\x00" * 64
        if where == "sidecar":
            if ext in SIDECAR_TEXT and is_text(head):
                continue
            if ext in SIDECAR_IMG and is_image(head) and size <= IMG_MAX:
                continue
            why.add("release file that is not plain text or a small proof image")
        else:
            if ext in INNER_OK and is_text(head):
                continue
            why.add("files from inside the archive")
    return sorted(why)


def sidecar_ok(name: str, data: bytes) -> bool:
    """A release file that may be offered on its own: the allow-list above."""
    ext = Path(name.split("\x00")[0]).suffix.lower()
    if ext in SIDECAR_TEXT and is_text(data[:1 << 20]):
        return True
    return ext in SIDECAR_IMG and is_image(data) and len(data) <= IMG_MAX


def safe_rel(name: str) -> str | None:
    """The sidecar's path below the release folder, or None if it would escape."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p or "\x00" in p for p in parts):
        return None
    return "/".join(parts)


def nfo_text(raw: bytes) -> str:
    t = raw.decode("cp437")
    return t.replace("\r\n", "\n").replace("\r", "\n")


def one(row, out: Path, clash=frozenset()):
    name, rsr_path, kind, fmt, verify, rver, level, mt = row
    p = Path(rsr_path)
    parts = p.parts
    sys_, year = parts[-4], parts[-3]
    z = zipfile.ZipFile(p)
    man = json.loads(z.read("manifest.json"))
    sha = None
    if man.get("release") in DELTA_CLEAR:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        sha = h.hexdigest()
    reasons = withheld_reasons(z, man, sha)
    # Windows (and many web hosts) treat names that differ only in case as the
    # same file: such releases get their year added to the FILE name only.
    fname = f"{name}_{year}" if (sys_, name.lower()) in clash else name
    m = DATE_RE.search(man.get("source_folder") or "")
    rec = {
        "name": name, "file": fname, "sys": sys_, "year": year, "date": m.group(1) if m else None,
        "group": name.rsplit("-", 1)[1] if "-" in name else "",
        "kind": kind, "format": fmt, "verify": verify,
        "packer": rver or "", "level": level, "mt": mt,
        "sets": [{
            "stem": s.get("stem"), "format": s.get("format"),
            "files": [[f["name"], f.get("size"), f.get("crc32")] for f in s.get("files", [])],
            "volumes": [[v["name"], v.get("size")] for v in s.get("volumes", [])],
        } for s in man.get("sets", [])],
        "sidecars": [[c["name"], c.get("size"), c.get("crc32"), 0] for c in man.get("sidecars", [])],
        # The files a user supplies (size, CRC32): what the app looks up on
        # the site to find this release from the files alone.
        "content": sorted({(f.get("size"), f.get("crc32")) for s in man.get("sets", [])
                           for f in s.get("files", []) if f.get("source") == "content"
                           and f.get("size") is not None and f.get("crc32") is not None}),
        "nfo": None, "nfo_name": None,
        "public": not reasons, "withheld": reasons,
    }
    # The release's own nfo/sfv/proof files, downloadable one by one (public
    # or withheld alike: they hold no archive data). 4th field = its path under extras/, or 0.
    for k, c in enumerate(man.get("sidecars", [])):
        rel = safe_rel(c["name"])
        if not c.get("stored") or rel is None or c["stored"] not in z.namelist():
            continue
        data = z.read(c["stored"])
        if not sidecar_ok(c["name"], data):
            continue
        dst = out / "extras" / sys_ / fname / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        rec["sidecars"][k][3] = rel
    for c in man.get("sidecars", []):
        if c["name"].lower().endswith(".nfo") and c.get("stored"):
            raw = z.read(c["stored"])[:NFO_MAX]
            if not is_text(raw):
                continue
            rec["nfo"], rec["nfo_name"] = nfo_text(raw), c["name"]
            break
    if not reasons:
        dst = out / "rsr" / sys_ / f"{fname}.rsr"
        dst.parent.mkdir(parents=True, exist_ok=True)
        pub = dict(man)
        if pub.get("source_folder"):
            pub["source_folder"] = Path(pub["source_folder"].replace("\\", "/")).name
        tmp = dst.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w") as w:
            for i in z.infolist():
                if i.filename == "manifest.json":
                    w.writestr(i, json.dumps(pub, indent=1, ensure_ascii=False).encode("utf-8"))
                else:
                    with z.open(i) as src, w.open(i, "w") as d:
                        while chunk := src.read(1 << 20):
                            d.write(chunk)
        os.replace(tmp, dst)
        h = hashlib.sha256()
        with open(dst, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        rec["rsr_size"], rec["rsr_sha256"] = dst.stat().st_size, h.hexdigest()
    return rec


def _work(args):
    rows, out, clash = args
    res = []
    for r in rows:
        try:
            res.append(one(r, Path(out), clash))
        except Exception as e:
            res.append({"name": r[0], "error": f"{type(e).__name__}: {e}"})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "catalog").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("select name, rsr_path, kind, format, verify, recipe_version, level, mt "
                       "from releases where rsr_path is not null order by name").fetchall()
    if a.only:
        rows = [r for r in rows if r[0] in set(a.only)]
    seen = {}
    for r in rows:
        k = (Path(r[1]).parts[-4], r[0].lower())
        seen[k] = seen.get(k, 0) + 1
    clash = frozenset(k for k, v in seen.items() if v > 1)
    chunks = [(rows[i:i + 200], str(out), clash) for i in range(0, len(rows), 200)]
    cats, errs, done = {}, [], 0
    with ProcessPoolExecutor(a.workers) as ex:
        for res in ex.map(_work, chunks):
            for rec in res:
                if "error" in rec:
                    errs.append(rec)
                else:
                    cats.setdefault(rec["sys"], []).append(rec)
            done += len(res)
            print(f"\r{done}/{len(rows)}", end="", flush=True)
    print()
    for s, recs in cats.items():
        with open(out / "catalog" / f"{s}.jsonl", "w", encoding="utf-8") as f:
            for rec in sorted(recs, key=lambda r: r["name"].lower()):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    allr = [r for v in cats.values() for r in v]
    files = {}
    for r in allr:
        files.setdefault((r["sys"], r["file"].lower()), []).append(r["name"])
    dup = [v for v in files.values() if len(v) > 1]
    if dup:
        raise SystemExit(f"file names still clash (same name, case and year): {dup[:5]}")
    print(f"releases {len(allr)}  public {sum(r['public'] for r in allr)}  "
          f"withheld {sum(not r['public'] for r in allr)}  errors {len(errs)}")
    for e in errs[:20]:
        print("  ERR", e["name"], e["error"])
    json.dump(errs, open(out / "export_errors.json", "w"), indent=1)


if __name__ == "__main__":
    main()
