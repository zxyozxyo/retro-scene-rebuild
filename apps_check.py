"""Which packer builds and helper programs the RSR tool needs, and whether
they are present.

apps_manifest.json lists every file by its place under apps/, its size and
SHA-256. It was made from a complete apps folder (`python apps_check.py
--make`), so a file counts as present only when its bytes match: a build
saved under the right name but from a different download is reported, not
trusted, because the wrong binary packs different bytes and every capture
with it would be a false wall.

    python apps_check.py              report what is missing or different
    python apps_check.py --import DIR file every known build found in DIR
                                      (any name, also inside installers)
                                      under its proper name
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "apps_manifest.json"

# What the tool runs. Paths are relative to apps/.
GROUPS = [
    ("Windows/console RAR builds", "winrar_pack-4.20", "*_rar*.exe", True),
    ("DOS RAR builds", "dosrar_pack", "*.exe", True),
    ("DOS32 helpers", "dosrar_pack/_dos32", "*", True),
    ("DOSBox-X (runs the DOS builds)", "dosbox-x", "dosbox-x.exe", True),
    ("precomp (ZIP re-creation)", "zip_pack/precomp/windows", "*", True),
    ("7-Zip", ".", "7z*.exe", False),
    ("UnRAR / Rar", ".", "[UR]*.exe", False),
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def make(apps: Path) -> dict:
    out = {"groups": []}
    for title, sub, pat, required in GROUPS:
        d = apps / sub
        files = sorted(p for p in d.glob(pat) if p.is_file()) if d.is_dir() else []
        out["groups"].append({
            "title": title, "dir": sub, "required": required,
            "files": [{"name": p.name, "size": p.stat().st_size, "sha256": sha256(p)} for p in files],
        })
    return out


def check(apps: Path, man: dict | None = None) -> dict:
    """{group title: {"ok": n, "missing": [...], "different": [...], "total": n}}"""
    man = man or json.loads(MANIFEST.read_text(encoding="utf-8"))
    res = {}
    for g in man["groups"]:
        r = {"ok": 0, "missing": [], "different": [], "total": len(g["files"]), "required": g["required"]}
        for f in g["files"]:
            p = apps / g["dir"] / f["name"]
            if not p.is_file():
                r["missing"].append(f["name"])
            elif p.stat().st_size != f["size"] or sha256(p) != f["sha256"]:
                r["different"].append(f["name"])
            else:
                r["ok"] += 1
        res[g["title"]] = r
    return res


def _index(man: dict) -> dict:
    return {f["sha256"]: (g["dir"], f["name"]) for g in man["groups"] for f in g["files"]}


def _seven_zip(apps: Path) -> Path | None:
    for n in ("7z.exe", "7za.exe"):
        if (apps / n).is_file():
            return apps / n
    return None


def import_dir(src: Path, apps: Path, log=print) -> dict:
    """File every known build found under `src` into apps/ under its proper
    name. Files are matched by SHA-256, so the download's own name does not
    matter. Installers and archives (WinRAR SFX installers, DOS rarNNN.exe
    self-extractors, .zip/.rar) are opened with 7-Zip and searched too.
    Nothing in `src` is changed or deleted."""
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    idx = _index(man)
    sz = _seven_zip(apps)
    sizes = {f["size"] for g in man["groups"] for f in g["files"]}
    added, already, seen = [], [], set()

    def consider(p: Path, origin: str):
        try:
            if p.stat().st_size not in sizes:
                return False
            h = sha256(p)
        except OSError:
            return False
        if h not in idx or h in seen:
            return h in idx
        seen.add(h)
        sub, name = idx[h]
        dst = apps / sub / name
        if dst.is_file() and sha256(dst) == h:
            already.append(name)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.rename(dst.with_name(dst.name + ".replaced"))
            shutil.copy2(p, dst)
            added.append(name)
            log(f"  + {sub}/{name}   (from {origin})")
        return True

    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        if consider(p, p.name):
            continue
        if sz and p.suffix.lower() in (".exe", ".zip", ".rar", ".7z", ".sfx"):
            with tempfile.TemporaryDirectory() as t:
                r = subprocess.run([str(sz), "x", "-y", f"-o{t}", str(p)],
                                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode in (0, 1):
                    for q in Path(t).rglob("*"):
                        if q.is_file():
                            consider(q, f"{p.name} -> {q.name}")
    return {"added": added, "already": already}


def report(res: dict) -> str:
    lines, bad = [], 0
    for title, r in res.items():
        miss = len(r["missing"]) + len(r["different"])
        mark = "OK " if not miss else ("!! " if r["required"] else "-  ")
        lines.append(f"{mark}{title}: {r['ok']} of {r['total']}")
        for n in r["missing"][:40]:
            lines.append(f"      missing    {n}")
        for n in r["different"][:40]:
            lines.append(f"      different  {n}")
        if r["required"]:
            bad += miss
    lines.append("All required files present." if not bad else
                 f"{bad} required file(s) missing or different — releases packed with those builds can't be captured or rebuilt.")
    return "\n".join(lines)


if __name__ == "__main__":
    apps = HERE / "apps"
    if "--make" in sys.argv:
        m = make(apps)
        MANIFEST.write_text(json.dumps(m, indent=1), encoding="utf-8")
        print("manifest:", sum(len(g["files"]) for g in m["groups"]), "files")
    elif "--import" in sys.argv:
        r = import_dir(Path(sys.argv[sys.argv.index("--import") + 1]), apps)
        print(f"added {len(r['added'])}, already present {len(r['already'])}")
    else:
        print(report(check(apps)))
