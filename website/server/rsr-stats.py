#!/usr/bin/env python3
"""Daily visitor/download summary for retroscenerebuild.com, from Caddy's log.

Caddy keeps only ~a week of raw log (5 x 50 MiB rolls), so this writes one
small JSON per day to /var/lib/rsr-stats/ and those are kept for good. Counts
only -- no IP addresses are stored.

    rsr-stats.py              summarise yesterday (cron, 00:15 UTC)
    rsr-stats.py 2026-11-18   summarise that day
    rsr-stats.py all          every day still in the raw log
    rsr-stats.py today        today so far (printed, not saved)
"""
import collections, datetime, glob, gzip, json, re, sys
from pathlib import Path

LOGS = "/var/log/caddy/access*"
OUT = Path("/var/lib/rsr-stats")
BOT = re.compile(r"bot|crawl|spider|slurp|curl|wget|python|go-http|scrapy|httpx|"
                 r"java|libwww|headless|facebookexternalhit|preview|monitor|"
                 r"uptime|scan|fetch|http-client|okhttp|axios", re.I)


def day_of(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")


def new_day():
    return {"ips": set(), "dl_ips": set(), "page_views": 0, "downloads": 0,
            "bot_hits": 0, "leech_blocked": 0, "pages": collections.Counter(),
            "files": collections.Counter(), "systems": collections.Counter(),
            "referrers": collections.Counter(), "bots": collections.Counter()}


def scan(want=None):
    days = collections.defaultdict(new_day)
    for f in sorted(glob.glob(LOGS)):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", errors="replace") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                d = day_of(e.get("ts", 0))
                if want and d not in want:
                    continue
                D = days[d]
                r = e.get("request", {})
                h = r.get("headers", {})
                ua = (h.get("User-Agent") or [""])[0]
                uri = r.get("uri", "").split("?")[0]
                st = e.get("status", 0)
                ip = r.get("remote_ip")
                if not ua or BOT.search(ua):
                    D["bot_hits"] += 1
                    m = re.search(r"([A-Za-z-]*(?:bot|crawler|spider)[A-Za-z-]*)", ua, re.I)
                    D["bots"][m.group(1) if m else (ua[:30] or "(no agent)")] += 1
                    continue
                if uri.startswith("/files/"):
                    if st == 403:
                        D["leech_blocked"] += 1
                    elif st in (200, 206):
                        D["downloads"] += 1
                        D["dl_ips"].add(ip)
                        D["files"][uri.rsplit("/", 1)[-1]] += 1
                        parts = uri.split("/")
                        D["systems"][parts[2] if len(parts) > 3 else "?"] += 1
                    continue
                last = uri.rsplit("/", 1)[-1]
                if st == 200 and (uri.endswith(".html") or uri.endswith("/") or "." not in last):
                    D["page_views"] += 1
                    D["ips"].add(ip)
                    D["pages"][uri] += 1
                    ref = (h.get("Referer") or [""])[0]
                    if ref and "retroscenerebuild.com" not in ref:
                        D["referrers"][re.sub(r"^https?://([^/]+).*", r"\1", ref)] += 1
    return days


def summary(d, D):
    return {"day": d, "visitors": len(D["ips"]), "page_views": D["page_views"],
            "downloads": D["downloads"], "downloaders": len(D["dl_ips"]),
            "bot_hits": D["bot_hits"], "leech_blocked": D["leech_blocked"],
            "top_pages": D["pages"].most_common(20),
            "top_downloads": D["files"].most_common(20),
            "downloads_by_system": D["systems"].most_common(),
            "referrers": D["referrers"].most_common(20),
            "top_bots": D["bots"].most_common(10)}


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "yesterday"
    now = datetime.datetime.now(datetime.timezone.utc)
    if arg == "yesterday":
        want = {(now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")}
    elif arg == "today":
        want = {now.strftime("%Y-%m-%d")}
    elif arg == "all":
        want = None
    else:
        want = {arg}
    days = scan(want)
    today = now.strftime("%Y-%m-%d")
    OUT.mkdir(parents=True, exist_ok=True)
    for d in sorted(days):
        s = summary(d, days[d])
        if arg == "today" or d == today:
            print(json.dumps(s, indent=1))
            continue
        (OUT / f"{d}.json").write_text(json.dumps(s, indent=1))
        print(f"{d}: {s['visitors']} visitors, {s['page_views']} pages, "
              f"{s['downloads']} downloads by {s['downloaders']}, "
              f"{s['bot_hits']} bot hits, {s['leech_blocked']} blocked")


if __name__ == "__main__":
    main()
