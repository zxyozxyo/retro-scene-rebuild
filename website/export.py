"""Public export for the Retro Scene Rebuild website.

Reads the local store + index (never writes to them) and produces:
  <out>/rsr/<SYS>/<release>.rsr   public copy, only for "clean" releases
  <out>/catalog/<SYS>.jsonl       one line per release (page data)

A release is clean when its .rsr carries no file data beyond the rebuild
recipe: manifest, release.srr, deltas, zip/lha skeletons and preflate diffs,
and allow-listed sidecars/extras (nfo, sfv, images, text, disc sheets).
Anything else (carried zip members, LHA members, archives carried whole,
splice/tail/graft/av/sfx blobs, other extras) means the public site shows the
release page but does not host the .rsr.

The public manifest drops the local source folder path; nothing else changes.

usage: python export.py <out dir> [--workers N] [--only NAME ...]
"""
import argparse, hashlib, json, os, re, sqlite3, sys, zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "rsr_index.db"
SAFE_EXT = {".nfo", ".diz", ".sfv", ".jpg", ".jpeg", ".png", ".gif", ".bmp",
            ".txt", ".cue", ".ccd", ".mds", ".toc", ".m3u", ".gdi", ".md5",
            ".sha1", ".url", ".log", ".htm", ".html", ".srr", ".nzb"}
NFO_MAX = 96 * 1024
DATE_RE = re.compile(r"(?:^|[\\/])(?:\[[^\]]*\]\s*)?(\d{4}-\d{2}-\d{2})-")


def entry_class(n: str) -> str:
    """'ok' for recipe/diff data and safe sidecars, else the reason it is withheld."""
    top = n.split("/")[0]
    if n in ("manifest.json", "release.srr") or top == "deltas":
        return "ok"
    if top in ("sidecars", "extras"):
        return "ok" if Path(n).suffix.lower() in SAFE_EXT else f"{top} file {Path(n).name}"
    if top == "zips":
        if n.endswith(("skeleton.bin", "preflate.bin")) or ("/pf/" in n and "carry" not in n):
            return "ok"
        return "carried zip member data"
    if top == "lha":
        return "ok" if n.endswith("skeleton.bin") else "carried LHA member data"
    return f"carried archive data ({top})"


def nfo_text(raw: bytes) -> str:
    t = raw.decode("cp437")
    return t.replace("\r\n", "\n").replace("\r", "\n")


def one(row, out: Path):
    name, rsr_path, kind, fmt, verify, rver, level, mt = row
    p = Path(rsr_path)
    parts = p.parts
    sys_, year = parts[-4], parts[-3]
    z = zipfile.ZipFile(p)
    man = json.loads(z.read("manifest.json"))
    reasons = sorted({c for c in (entry_class(i.filename) for i in z.infolist()) if c != "ok"})
    whole = any((s.get("recipe") or {}).get("label") == "carried" for s in man.get("sets", []))
    if whole:
        reasons = sorted(set(reasons) | {"archive carried whole"})
    m = DATE_RE.search(man.get("source_folder") or "")
    rec = {
        "name": name, "sys": sys_, "year": year, "date": m.group(1) if m else None,
        "group": name.rsplit("-", 1)[1] if "-" in name else "",
        "kind": kind, "format": fmt, "verify": verify,
        "packer": rver or "", "level": level, "mt": mt,
        "sets": [{
            "stem": s.get("stem"), "format": s.get("format"),
            "files": [[f["name"], f.get("size"), f.get("crc32")] for f in s.get("files", [])],
            "volumes": [[v["name"], v.get("size")] for v in s.get("volumes", [])],
        } for s in man.get("sets", [])],
        "sidecars": [[c["name"], c.get("size"), c.get("crc32")] for c in man.get("sidecars", [])],
        "nfo": None, "nfo_name": None,
        "public": not reasons, "withheld": reasons,
    }
    for c in man.get("sidecars", []):
        if c["name"].lower().endswith(".nfo") and c.get("stored"):
            raw = z.read(c["stored"])[:NFO_MAX]
            rec["nfo"], rec["nfo_name"] = nfo_text(raw), c["name"]
            break
    if not reasons:
        dst = out / "rsr" / sys_ / f"{name}.rsr"
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
    rows, out = args
    res = []
    for r in rows:
        try:
            res.append(one(r, Path(out)))
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
    chunks = [(rows[i:i + 200], str(out)) for i in range(0, len(rows), 200)]
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
    print(f"releases {len(allr)}  public {sum(r['public'] for r in allr)}  "
          f"withheld {sum(not r['public'] for r in allr)}  errors {len(errs)}")
    for e in errs[:20]:
        print("  ERR", e["name"], e["error"])
    json.dump(errs, open(out / "export_errors.json", "w"), indent=1)


if __name__ == "__main__":
    main()
