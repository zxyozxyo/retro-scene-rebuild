"""Build the Retro Scene Rebuild static site from a public export.

usage: python build_site.py <export dir> <site dir> [--link]

<export dir> is what export.py wrote (catalog/*.jsonl + rsr/). The site gets
plain HTML pages plus the public .rsr files under files/. --link hardlinks the
.rsr files instead of copying them (local preview on the same drive).
"""
import argparse, html, json, os, re, shutil, sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

SITE_NAME = "Retro Scene Rebuild"
HERE = Path(__file__).resolve().parent
LIST_CAP = 5000

SYSTEM_NAMES = {
    "3DO": "3DO", "3DS": "Nintendo 3DS", "AMIGA": "Amiga", "CD32": "Amiga CD32",
    "ATARI2600": "Atari 2600", "ATARI7800": "Atari 7800", "ATARIST": "Atari ST",
    "C64": "Commodore 64", "C264": "Commodore 264", "DC": "Dreamcast",
    "GB": "Game Boy", "GBC": "Game Boy Color", "GBA": "Game Boy Advance",
    "GG": "Game Gear", "GIZ": "Gizmondo", "GP32": "GP32", "GUIDES": "Guides",
    "INTV": "Intellivision", "IQP": "iQue Player", "JAG": "Atari Jaguar",
    "LINUX": "Linux", "LYNX": "Atari Lynx", "MAC": "Macintosh", "MAME": "MAME",
    "MCD": "Mega CD", "MISC": "Miscellaneous", "MSX": "MSX", "N64": "Nintendo 64",
    "NDS": "Nintendo DS", "NES": "NES", "NEOGEO": "Neo Geo", "NGCD": "Neo Geo CD",
    "NGP": "Neo Geo Pocket", "NGPC": "Neo Geo Pocket Color", "NGAGE": "N-Gage",
    "NGC": "GameCube", "OTHER": "Other", "PC": "PC", "PCECD": "PC Engine CD",
    "POCKET": "Analogue Pocket", "PSX": "PlayStation",
    "PS2": "PlayStation 2", "PS3": "PlayStation 3", "PS4": "PlayStation 4",
    "PSP": "PlayStation Portable", "PSV": "PlayStation Vita", "ROMSETS": "ROM sets",
    "SATURN": "Saturn", "SMD": "Mega Drive", "SMS": "Master System", "SNES": "Super Nintendo",
    "SWITCH": "Nintendo Switch", "VVD": "VVD", "WII": "Wii", "WIIU": "Wii U",
    "WS": "WonderSwan", "WSC": "WonderSwan Color", "XBOX": "Xbox", "XBOX360": "Xbox 360",
    "ZXS": "ZX Spectrum",
}
# The store files homebrew it could not place under "Unknown"; the tag before
# the group name says which system it is for.
TAG_SYS = {"NES": "NES", "NESMINI": "NES", "ZXS": "ZXS", "SMD": "SMD", "POCKET": "POCKET",
           "PCECD": "PCECD", "PCED": "PCECD", "NGPC": "NGPC", "NGP": "NGP", "SNES": "SNES",
           "SMS": "SMS", "MSX": "MSX", "VVD": "VVD", "SATURN": "SATURN", "NEOGEO": "NEOGEO",
           "NGCD": "NGCD", "WSC": "WSC", "WS": "WS", "ST": "ATARIST", "ATARIST": "ATARIST"}


# Store folders that are one system on the site.
MERGE_SYS = {"PS1": "PSX"}


def place_unknown(name):
    stem = name.rsplit("-", 1)[0]
    for tok in reversed(re.split(r"[._\-]", stem)):
        if tok.upper() in TAG_SYS:
            return TAG_SYS[tok.upper()]
    return "OTHER"


KIND_LABEL = {"archive": "RAR set", "zip": "ZIP set", "zip>rar": "ZIP of RAR set",
              "lha": "LHA archive", "metadata": "NFO / dir fix", "loose": "Loose files"}
VERIFY_LABEL = {"exact": "byte-exact", "delta": "byte-exact (with diff)"}

e = html.escape


def sys_name(s):
    return SYSTEM_NAMES.get(s.upper(), s)


def human(n):
    if n is None:
        return ""
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:,.0f} {u}" if u == "B" else f"{n:,.1f} {u}"
        n /= 1024


def crc(c):
    return "" if c is None else f"{c & 0xFFFFFFFF:08X}"


def rel_url(rec):
    return f"r/{quote(rec['sys'])}/{quote(rec['slug'])}.html"


def page(title, body, depth, desc=""):
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc or SITE_NAME)}">
<link rel="stylesheet" href="{up}assets/style.css">
<link rel="icon" href="{up}assets/icon.svg" type="image/svg+xml">
</head>
<body>
<header class="top"><div class="wrap">
<a class="brand" href="{up}index.html"><span class="mark">▚</span> {SITE_NAME}</a>
<nav><a href="{up}search.html">Search</a><a href="{up}tools.html">Tools</a><a href="{up}about.html">About</a><a href="{up}disclaimer.html">Disclaimer</a></nav>
</div></header>
<main class="wrap">
{body}
</main>
<footer class="wrap foot">
<p><strong>No copyrighted material is hosted on this site.</strong> {SITE_NAME} holds rebuild recipes only: packing instructions, checksums, small diffs and each release's own NFO/SFV/proof files. There are no games, ROMs, disc images, BIOS files or other copyrighted program data here, and none are linked to.</p>
<p><strong>Please do not ask for files.</strong> Requests for games, ROMs or releases will not be answered. You must supply your own legally obtained files to rebuild anything.</p>
<p>All game titles, trademarks and release names belong to their respective owners and are used only to identify releases. <a href="{up}disclaimer.html">Full disclaimer</a></p>
</footer>
</body>
</html>
"""


def release_page(rec):
    crumbs = (f'<nav class="crumbs"><a href="../../index.html">Home</a> / '
              f'<a href="../../s/{quote(rec["sys"])}/index.html">{e(sys_name(rec["sys"]))}</a> / '
              f'<a href="../../s/{quote(rec["sys"])}/{quote(rec["year"])}.html">{e(rec["year"])}</a></nav>')
    packer = rec.get("packer") or ""
    m = re.match(r"(\d{4}-\d{2}-\d{2}) (.+)", packer)
    if m:
        packer = f"RAR {m.group(2)} ({m.group(1)})"
    if packer and rec.get("level", -1) not in (-1, None):
        packer += f" · -m{rec['level']}"
    if packer and rec.get("mt", -1) not in (-1, 0, None):
        packer += f" · -mt{rec['mt']}"
    facts = [("System", sys_name(rec["sys"])), ("Group", rec.get("group")),
             ("Date", rec.get("date")), ("Type", KIND_LABEL.get(rec.get("kind"), rec.get("kind"))),
             ("Packer", packer or None),
             ("Rebuild", VERIFY_LABEL.get(rec.get("verify"), rec.get("verify")))]
    fact_html = "".join(f"<dt>{k}</dt><dd>{e(str(v))}</dd>" for k, v in facts if v)
    if rec.get("public"):
        dl = (f'<div class="dl ok"><a class="btn" href="../../files/{quote(rec["sys"])}/{quote(rec["slug"])}.rsr" download>'
              f'Download .rsr</a><span>{human(rec["rsr_size"])}</span>'
              f'<code class="sha" title="SHA-256">{rec["rsr_sha256"]}</code></div>')
    else:
        why = ", ".join(rec.get("withheld") or [])
        dl = ('<div class="dl no"><strong>Rebuild data not hosted.</strong> '
              'This release\'s .rsr holds pieces of the original files, so it is listed for reference only.'
              f'<span class="why">{e(why)}</span></div>')
    sets_html = []
    for s in rec["sets"]:
        files = s["files"]
        rows = "".join(f"<tr><td>{e(n)}</td><td class=num>{human(sz)}</td><td class=mono>{crc(c)}</td></tr>"
                       for n, sz, c in files[:LIST_CAP])
        more = f'<p class="note">… and {len(files) - LIST_CAP:,} more files.</p>' if len(files) > LIST_CAP else ""
        vols = s["volumes"]
        vtot = sum(v[1] or 0 for v in vols)
        vol_html = ""
        if vols:
            vrows = "".join(f"<tr><td>{e(n)}</td><td class=num>{human(sz)}</td></tr>" for n, sz in vols[:LIST_CAP])
            vol_html = (f'<details><summary>{len(vols):,} archive volume{"s" if len(vols) != 1 else ""} · {human(vtot)}</summary>'
                        f'<table class="t"><tbody>{vrows}</tbody></table></details>')
        head = f'<h3>{e(s.get("stem") or "")} <span class="tag">{e(s.get("format") or "")}</span></h3>' if len(rec["sets"]) > 1 or s.get("stem") else ""
        sets_html.append(f'<section class="set">{head}'
                         f'<table class="t"><thead><tr><th>File</th><th class=num>Size</th><th>CRC32</th></tr></thead>'
                         f'<tbody>{rows}</tbody></table>{more}{vol_html}</section>')
    side = ""
    if rec["sidecars"]:
        srows = "".join(f"<tr><td>{e(n)}</td><td class=num>{human(sz)}</td><td class=mono>{crc(c)}</td></tr>"
                        for n, sz, c in rec["sidecars"])
        side = f'<h2>Release files</h2><table class="t"><tbody>{srows}</tbody></table>'
    nfo = ""
    if rec.get("nfo"):
        nfo = (f'<h2>{e(rec["nfo_name"])}</h2>'
               f'<pre class="nfo">{e(rec["nfo"])}</pre>')
    body = (f'{crumbs}<h1 class="rel">{e(rec["name"])}</h1>'
            f'<div class="relhead"><dl class="facts">{fact_html}</dl>{dl}</div>'
            f'<h2>Contents</h2>{"".join(sets_html)}{side}{nfo}')
    return page(rec["name"], body, 2, f'{rec["name"]} — {sys_name(rec["sys"])} scene release, rebuild data and file list.')


def list_table(recs, depth):
    up = "../" * depth
    rows = []
    for r in recs:
        mark = "" if r.get("public") else ' <span class="tag dim" title="rebuild data not hosted">listed</span>'
        rows.append(f'<tr><td><a href="{up}{rel_url(r)}">{e(r["name"])}</a>{mark}</td>'
                    f'<td>{e(r.get("date") or "")}</td><td>{e(r.get("group") or "")}</td></tr>')
    return ('<table class="t list"><thead><tr><th>Release</th><th>Date</th><th>Group</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("site")
    ap.add_argument("--link", action="store_true")
    a = ap.parse_args()
    exp, site = Path(a.export), Path(a.site)
    recs = []
    for f in sorted((exp / "catalog").glob("*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            recs += [json.loads(l) for l in fh]
    for r in recs:
        r["store_sys"] = r["sys"]
        if r["sys"] == "Unknown":
            r["sys"] = place_unknown(r["name"])
        elif r["sys"] in MERGE_SYS:
            r["sys"] = MERGE_SYS[r["sys"]]
    print("releases", len(recs))
    (site / "assets").mkdir(parents=True, exist_ok=True)
    for f in (HERE / "static").iterdir():
        shutil.copy2(f, site / "assets" / f.name)

    by_sys = defaultdict(list)
    for r in recs:
        by_sys[r["sys"]].append(r)
    # one page + one file per release, even where names differ only in case
    # (or two merged systems hold the same name)
    taken = defaultdict(int)
    for r in recs:
        taken[(r["sys"], r.get("file", r["name"]).lower())] += 1
    for r in recs:
        base = r.get("file", r["name"])
        r["slug"] = f"{base}_{r['store_sys']}" if taken[(r["sys"], base.lower())] > 1 else base
    slugs = defaultdict(list)
    for r in recs:
        slugs[(r["sys"], r["slug"].lower())].append(r["name"])
    clash = [v for v in slugs.values() if len(v) > 1]
    if clash:
        raise SystemExit(f"page names still clash: {clash[:5]}")

    # release pages + files
    nfiles = 0
    for r in recs:
        p = site / "r" / r["sys"] / f"{r['slug']}.html"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(release_page(r), encoding="utf-8")
        if r.get("public"):
            src = exp / "rsr" / r["store_sys"] / f"{r.get('file', r['name'])}.rsr"
            dst = site / "files" / r["sys"] / f"{r['slug']}.rsr"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            if a.link:
                os.link(src, dst)
            else:
                shutil.copy2(src, dst)
            nfiles += 1

    # system + year pages
    for s, rs in by_sys.items():
        years = defaultdict(list)
        for r in rs:
            years[r["year"]].append(r)
        d = site / "s" / s
        d.mkdir(parents=True, exist_ok=True)
        ylinks = "".join(f'<a class="card" href="{quote(y)}.html"><b>{e(y)}</b><span>{len(v):,}</span></a>'
                         for y, v in sorted(years.items()))
        pub = sum(1 for r in rs if r.get("public"))
        body = (f'<nav class="crumbs"><a href="../../index.html">Home</a></nav>'
                f'<h1>{e(sys_name(s))}</h1><p class="lede">{len(rs):,} releases · {pub:,} with rebuild data</p>'
                f'<div class="grid">{ylinks}</div>')
        (d / "index.html").write_text(page(f"{sys_name(s)} — {SITE_NAME}", body, 2), encoding="utf-8")
        for y, v in years.items():
            v.sort(key=lambda r: (r.get("date") or "9999", r["name"].lower()))
            body = (f'<nav class="crumbs"><a href="../../index.html">Home</a> / <a href="index.html">{e(sys_name(s))}</a></nav>'
                    f'<h1>{e(sys_name(s))} · {e(y)}</h1><p class="lede">{len(v):,} releases</p>{list_table(v, 2)}')
            (d / f"{y}.html").write_text(page(f"{sys_name(s)} {y} — {SITE_NAME}", body, 2), encoding="utf-8")

    # search index: [name, sys, year, public(, page name when it isn't the release name)]
    idx = [[r["name"], r["sys"], r["year"], 1 if r.get("public") else 0] + ([r["slug"]] if r["slug"] != r["name"] else [])
           for r in recs]
    (site / "assets" / "index.json").write_text(json.dumps(idx, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    # tools list
    man = json.loads((HERE.parent / "apps_manifest.json").read_text(encoding="utf-8"))
    counts = {g["title"]: len(g["files"]) for g in man["groups"]}
    n_win = counts.get("Windows/console RAR builds", 0)
    n_dos = counts.get("DOS RAR builds", 0)
    groups_html = []
    for g in man["groups"]:
        rows = "".join(f"<tr><td class=mono>{e(f['name'])}</td><td class=num>{human(f['size'])}</td>"
                       f"<td class=sha>{f['sha256']}</td></tr>" for f in g["files"])
        req = "required" if g["required"] else "helper"
        groups_html.append(f'<details{" open" if len(g["files"]) < 10 else ""}><summary><b>{e(g["title"])}</b> · '
                           f'{len(g["files"])} file{"s" if len(g["files"]) != 1 else ""} · {req} · <code>apps/{e(g["dir"])}</code></summary>'
                           f'<table class="t tools"><thead><tr><th>File</th><th class=num>Size</th><th>SHA-256</th></tr></thead>'
                           f'<tbody>{rows}</tbody></table></details>')
    tools = (HERE / "tools.html").read_text(encoding="utf-8")
    tools = tools.replace("__N_WIN__", str(n_win)).replace("__N_DOS__", str(n_dos)).replace("__GROUPS__", "".join(groups_html))
    (site / "tools.html").write_text(page(f"Tools — {SITE_NAME}", tools, 0), encoding="utf-8")

    # home
    cards = "".join(
        f'<a class="card" href="s/{quote(s)}/index.html"><b>{e(sys_name(s))}</b><span>{len(rs):,}</span></a>'
        for s, rs in sorted(by_sys.items(), key=lambda kv: sys_name(kv[0]).lower()))
    pub = sum(1 for r in recs if r.get("public"))
    body = f"""<section class="hero">
<h1>{SITE_NAME}</h1>
<p class="lede">Byte-exact rebuild recipes for retro console scene releases. Bring your own files, get the original release back — archives, volumes, NFO and all.</p>
<form class="search" action="search.html" method="get"><input name="q" type="search" placeholder="Search {len(recs):,} releases…" autofocus><button>Search</button></form>
<p class="stats"><b>{len(recs):,}</b> releases · <b>{len(by_sys)}</b> systems · <b>{pub:,}</b> with downloadable rebuild data</p>
</section>
<section class="features">
<div class="feat big"><div class="num">100%</div><h3>Byte-exact, every time</h3>
<p>Every release on this site was rebuilt and checked against the original archive hashes before it was added. Supply the original files unpacked (matching the listed CRC32), and you get back the <strong>exact original scene archives</strong>: every volume, header, comment and timestamp, identical to the day they were released.</p></div>
<div class="feat"><h3>RAR and ZIP</h3>
<p>RAR from the 1994 DOS 1.5x builds to RAR 7: multi-volume, solid, recovery records, comments, old and new volume naming. ZIP releases are re-created too, along with LHA archives and NFO/dir fixes.</p></div>
<div class="feat"><h3>Uses the original packers</h3>
<p>A rebuild replays the archiver the group actually used. Full compatibility needs <strong>{n_win} Windows/console RAR builds</strong> (1996–2026) and <strong>{n_dos} DOS RAR builds</strong> run under DOSBox-X. They aren't hosted here; the app checks which ones you have and files them under the right names. <a href="tools.html">See the list</a></p></div>
</section>
<h2>Systems</h2>
<div class="grid">{cards}</div>"""
    (site / "index.html").write_text(page(SITE_NAME, body, 0), encoding="utf-8")

    body = """<h1>Search</h1>
<form class="search" onsubmit="return false"><input id="q" type="search" placeholder="Release name, group, system…" autofocus></form>
<details class="help"><summary>Search tips</summary>
<table class="t"><tbody>
<tr><td><code>zelda usa</code></td><td>all words must appear, in any order</td></tr>
<tr><td><code>"game and watch"</code></td><td>exact phrase (underscores, dots and dashes count as spaces)</td></tr>
<tr><td><code>mario*kart</code></td><td><code>*</code> matches any run of characters</td></tr>
<tr><td><code>fifa?0?</code></td><td><code>?</code> matches exactly one character</td></tr>
<tr><td><code>hitman -nds</code></td><td><code>-</code> leaves out names containing a word</td></tr>
<tr><td><code>sys:xbox</code></td><td>only one system (code or name, e.g. <code>sys:psp</code>, <code>sys:dreamcast</code>)</td></tr>
<tr><td><code>year:2004</code></td><td>only one year; wildcards work, e.g. <code>year:199?</code></td></tr>
<tr><td><code>-CLEAR</code></td><td>the group is part of the name, so <code>-group</code> or <code>group</code> filters by group</td></tr>
</tbody></table></details>
<p id="count" class="lede"></p>
<table class="t list"><thead><tr><th>Release</th><th>System</th><th>Year</th></tr></thead><tbody id="res"></tbody></table>
<script>window.RSR_SYSTEMS = __SYSNAMES__;</script>
<script src="assets/search.js"></script>"""
    sysnames = {k: sys_name(k) for k in by_sys}
    body = body.replace("__SYSNAMES__", json.dumps(sysnames))
    (site / "search.html").write_text(page(f"Search — {SITE_NAME}", body, 0), encoding="utf-8")

    about = (HERE / "about.html").read_text(encoding="utf-8")
    (site / "about.html").write_text(page(f"About — {SITE_NAME}", about, 0), encoding="utf-8")
    disc = (HERE / "disclaimer.html").read_text(encoding="utf-8")
    (site / "disclaimer.html").write_text(page(f"Disclaimer — {SITE_NAME}", disc, 0), encoding="utf-8")
    print(f"pages {len(recs)}  systems {len(by_sys)}  .rsr files {nfiles}")


if __name__ == "__main__":
    main()
