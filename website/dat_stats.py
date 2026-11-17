"""Count every scene release in the dats.site DATs against what the store holds.

usage: python dat_stats.py <export dir> --dats <DatRoot> [--bad <!BAD-ARCHIVES>] [-o stats_data.json]

One <game> in a DAT is one release, however many archive volumes it has, so
the numbers are releases, not files. Each release lands in exactly one bucket:

  public    captured; the .rsr is downloadable on the site
  withheld  captured; listed on the site but its .rsr is not hosted
  bad       not captured; the release itself is damaged (in !BAD-ARCHIVES)
  toget     not captured yet

bad_captured counts releases from !BAD-ARCHIVES that were still captured
as they are (they are in public/withheld too).

The output holds counts only (no names), so it can be committed; build_site.py
turns it into stats.html.
"""
import argparse, html, json, re, time
from collections import defaultdict
from pathlib import Path

GAME = re.compile(r'<game name="([^"]*)"')
HEAD = re.compile(r"<header>.*?<name>([^<]*)</name>", re.S)
SYSYEAR = re.compile(r"(.+?) (Scene|nonScene) (\d{4})")
PREFIX = re.compile(r"^(\[nuked\]\s*)?(\d{4}-\d{2}-\d{2}-)?", re.I)


def key(name: str) -> str:
    """Release names compare case-blind; a "[NUKED] " tag or a date in front
    (how some folders are named) is not part of the name."""
    return PREFIX.sub("", name.strip()).lower()


def store_names(exp: Path) -> dict:
    out = {}
    for f in sorted((exp / "catalog").glob("*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                out[key(r["name"])] = bool(r.get("public"))
    return out


def bad_names(root: Path | None) -> set:
    if not root or not root.is_dir():
        return set()
    out = set()
    for top in root.iterdir():
        if not top.is_dir():
            continue
        dats = top.iterdir() if top.name.startswith("!") else [top]   # !PASSWORD-PROTECTED/<dat>/<release>
        for d in dats:
            if d.is_dir():
                out.update(key(r.name) for r in d.iterdir() if r.is_dir())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("--dats", required=True)
    ap.add_argument("--bad")
    ap.add_argument("-o", "--out", default=str(Path(__file__).resolve().parent / "stats_data.json"))
    a = ap.parse_args()
    store = store_names(Path(a.export))
    bad = bad_names(Path(a.bad) if a.bad else None)
    counts = defaultdict(lambda: defaultdict(lambda: dict(public=0, withheld=0, bad=0, toget=0, bad_captured=0)))
    seen = set()
    for f in sorted(Path(a.dats).glob("*.dat")):
        text = f.read_text(encoding="utf-8", errors="replace")
        m = HEAD.search(text)
        title = html.unescape(m.group(1)) if m else f.stem
        sy = SYSYEAR.match(title)
        if sy:
            system = sy.group(1) + (" nonScene" if sy.group(2) == "nonScene" else "")
            year = sy.group(3)
        else:
            system, year = title, "?"
        c = counts[system][year]
        for g in GAME.findall(text):
            k = key(html.unescape(g))
            if k in seen:          # the same release in two DATs counts once
                continue
            seen.add(k)
            if k in store:
                c["public" if store[k] else "withheld"] += 1
                c["bad_captured"] += k in bad
            elif k in bad:
                c["bad"] += 1
            else:
                c["toget"] += 1
    data = {"generated": time.strftime("%Y-%m-%d"),
            "dat_files": len(list(Path(a.dats).glob("*.dat"))),
            "systems": {s: dict(sorted(y.items())) for s, y in sorted(counts.items())}}
    Path(a.out).write_text(json.dumps(data, indent=1), encoding="utf-8")
    tot = defaultdict(int)
    for y in counts.values():
        for c in y.values():
            for k2, v in c.items():
                tot[k2] += v
    print(f"releases {len(seen):,}  " + "  ".join(f"{k2} {v:,}" for k2, v in tot.items()))
    print("store releases in no DAT:", sum(1 for k in store if k not in seen))


if __name__ == "__main__":
    main()
