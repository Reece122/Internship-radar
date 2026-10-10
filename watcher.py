"""Polls company job boards directly and writes data/feed.json (open engineering internships).

Run by .github/workflows/watch.yml on a schedule. Standard library only.
Prints one status line per board so failures are visible in the Actions log.
"""
import concurrent.futures as cf, datetime as dt, json, os, re, sys, time, urllib.request

C = json.load(open("config.json"))
NOW = int(time.time())
R = {k: re.compile(v, re.I) for k, v in C["rx"].items()}
CATS = [(n, re.compile(r, re.I)) for n, r in C["cats"]]
UA = {"User-Agent": "Mozilla/5.0 (internship-radar; personal job alert)", "Accept": "application/json"}


def http(url, body=None, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(UA, **({"Content-Type": "application/json"} if body is not None else {}))
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def classify(t):
    if not R["intern"].search(t) or R["drop"].search(t): return None
    if R["grad"].search(t) and not R["undergrad"].search(t): return None
    if R["offseason"].search(t) and not re.search("summer", t, re.I): return None
    if "2026" in t and "2027" not in t: return None
    if R["soft"].search(t) and not R["soft_ok"].search(t): return None
    for n, rx in CATS:
        if rx.search(t): return n
    return None


def epoch(s):
    try: return int(dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except Exception: return 0


def greenhouse(slug):
    j = http(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    return [(str(x["id"]), x["title"], (x.get("location") or {}).get("name", ""), x.get("absolute_url", ""),
             epoch(x.get("first_published"))) for x in j.get("jobs", [])]


def ashby(slug):
    j = http(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return [(x["id"], x["title"], x.get("location", "") or "", x.get("jobUrl", f"https://jobs.ashbyhq.com/{slug}/{x['id']}"),
             epoch(x.get("publishedAt"))) for x in j.get("jobs", [])]


def lever(slug):
    j = http(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return [(x["id"], x["text"], (x.get("categories") or {}).get("location", "") or "", x.get("hostedUrl", ""),
             int(x.get("createdAt", 0) / 1000)) for x in j]


def workday(w):
    base = f"https://{w['host']}.myworkdayjobs.com"
    out, off = [], 0
    while off < 300:
        j = http(f"{base}/wday/cxs/{w['tenant']}/{w['site']}/jobs",
                 {"appliedFacets": {}, "limit": 20, "offset": off, "searchText": "intern"})
        posts = j.get("jobPostings", [])
        for x in posts:
            path = x.get("externalPath", "")
            if path:
                out.append((path.rsplit("/", 1)[-1], x.get("title", ""), x.get("locationsText", ""),
                            f"{base}/en-US/{w['site']}{path}", 0))
        if len(posts) < 20: break
        off += 20
    return out


ATS = {"g": greenhouse, "a": ashby, "l": lever}


def board_name(ats, slug):
    if ats == "g": return http(f"https://boards-api.greenhouse.io/v1/boards/{slug}").get("name", "")
    return slug


def run_board(b):
    try:
        if "host" in b: rows, key = workday(b), "w:" + b["tenant"]
        else: rows, key = ATS[b["ats"]](b["slug"]), b["ats"] + ":" + b["slug"]
        return b["co"], key, rows, None
    except Exception as e:  # noqa: BLE001
        return b["co"], b.get("slug") or b.get("tenant"), [], f"{type(e).__name__}: {str(e)[:90]}"


def discover():
    found = []
    for c in C["candidates"]:
        for slug in c["slugs"]:
            for ats in "gal":
                try:
                    rows = ATS[ats](slug)
                    name = board_name(ats, slug) if ats == "g" else slug
                    if rows and c["match"].lower() in (name + " " + slug).lower().replace("-", ""):
                        found.append({"co": c["co"], "ats": ats, "slug": slug, "jobs": len(rows),
                                      "sample": [r[1] for r in rows[:8]], "name": name})
                        print(f"discovered {c['co']}: {ats}:{slug} ({len(rows)} jobs)")
                        break
                except Exception:  # noqa: BLE001
                    continue
            else:
                continue
            break
        else:
            print(f"not found  {c['co']}: tried {', '.join(c['slugs'])} on Greenhouse, Ashby and Lever")
    return found


def main():
    try: old = {x["id"]: x for x in json.load(open("data/feed.json"))}
    except Exception: old = {}
    try: status_old = json.load(open("data/status.json"))
    except Exception: status_old = {}
    known_sources = set(status_old.get("ok_sources", []))
    found = discover()
    boards = C["boards"] + C["workday"] + found
    feed, ok, failed, new = {}, [], [], []
    b_any = {b["co"]: b["any_role"] for b in C["boards"] if b.get("any_role")}
    with cf.ThreadPoolExecutor(12) as ex:
        results = list(ex.map(run_board, boards))
    for co, key, rows, err in results:
        if err:
            failed.append(f"{co} ({key}): {err}")
            print(f"FAIL {co:28s} {err}")
            for x in old.values():  # keep what we knew when a board is unreachable
                if x["source"] == co: feed[x["id"]] = x
            continue
        ok.append(co)
        kept = 0
        anyrx = re.compile(b_any[co], re.I) if co in b_any else None
        for jid, title, loc, url, posted in rows:
            cat = classify(title)
            if not cat and anyrx and anyrx.search(title): cat = "Full-time req (no internship posted)"
            if not cat: continue
            kept += 1
            fid = f"{key}:{jid}"
            prev = old.get(fid)
            e = {"id": fid, "source": co, "company_name": co, "title": title.strip(), "locations": [loc] if loc else [],
                 "url": url, "date_posted": (prev or {}).get("date_posted") or posted or NOW, "date_updated": NOW,
                 "active": True, "is_visible": True, "terms": ["Summer 2027"], "category": "Hardware", "radar_cat": cat}
            feed[fid] = e
            if prev is None and co in known_sources and not any(co.lower().startswith(h.lower()) for h in C["hide"]):
                new.append(e)
        print(f"ok   {co:28s} {len(rows):5d} jobs, {kept:3d} relevant internships")
    for fid, x in old.items():  # roles that vanished from a board we could read: mark closed, drop after 14 days
        if fid not in feed and x["source"] in ok:
            if x.get("active"): x = dict(x, active=False, date_updated=NOW)
            if NOW - x["date_updated"] < 14 * 86400: feed[fid] = x
    out = sorted(feed.values(), key=lambda x: (-x["date_posted"], x["id"]))
    os.makedirs("data", exist_ok=True)
    stable = lambda rows: [{k: v for k, v in x.items() if k != "date_updated" or not x["active"]} for x in rows]
    changed = stable(out) != stable(sorted(old.values(), key=lambda x: (-x["date_posted"], x["id"])))
    if changed or not old:
        json.dump(out, open("data/feed.json", "w"), indent=0, ensure_ascii=False)
    json.dump({"ok_sources": sorted(set(ok) | known_sources), "failed": failed, "discovered": found,
               "open": sum(1 for x in out if x["active"])}, open("data/status.json", "w"), indent=1)
    lines = [f"- **{x['company_name']}**: [{x['title']}]({x['url']}) ({', '.join(x['locations']) or 'location n/a'}; {x['radar_cat']})"
             for x in new]
    open("new.md", "w").write("\n".join(lines))
    title = f"{len(new)} new internship posting(s)" if len(new) != 1 else f"New: {new[0]['company_name']} - {new[0]['title']}"
    open("new_title.txt", "w").write(title[:120])
    print(f"\n{len(ok)} boards read, {len(failed)} failed, {sum(1 for x in out if x['active'])} open relevant internships, {len(new)} new")


if __name__ == "__main__":
    main()
