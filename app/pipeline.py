import asyncio
import json
import os

from . import agents, db, rank, scrape

TOP_K = int(os.getenv("TOP_K", "5"))

LIVE_STEPS = [
    "Reading LinkedIn and Instagram",
    "Writing the profile",
    "Scoring against everyone in the pool",
    "Agents are out on dates",
]


def clean_url(u):
    return u.strip().split("?")[0].rstrip("/")


def upsert_person(linkedin_url, instagram_url):
    li, ig = clean_url(linkedin_url), clean_url(instagram_url)
    row = db.one("select id from people where linkedin_url=?", (li,))
    if row:
        return row["id"]
    return db.run(
        "insert into people (linkedin_url, instagram_url, status) values (?, ?, 'working')", (li, ig)
    )


def get(pid):
    return db.one("select * from people where id=?", (pid,))


def profile_of(row):
    p = json.loads(row["profile"])
    p["name"] = row["name"]
    return p


async def scrape_person(pid):
    p = get(pid)
    if p["raw"]:
        return  # already scraped, don't spend apify credits again
    li, ig = await asyncio.gather(scrape.linkedin(p["linkedin_url"]), scrape.instagram(p["instagram_url"]))
    db.run("update people set raw=? where id=?", (json.dumps({"linkedin": li, "instagram": ig}), pid))


async def analyze_person(pid):
    raw = json.loads(get(pid)["raw"])
    prof = await agents.build_profile(raw["linkedin"], raw["instagram"])
    db.run(
        "update people set name=?, profile=?, status='ready', error=null where id=?",
        (prof.get("name") or "Unknown", json.dumps(prof, ensure_ascii=False), pid),
    )


async def score_pairs(pid):
    me = get(pid)
    others = db.all("select * from people where status='ready' and id != ?", (pid,))
    todo = []
    for o in others:
        a, b = sorted((me["id"], o["id"]))
        if not db.one("select 1 from pairs where a=? and b=?", (a, b)):
            todo.append(o)

    async def score(chunk):
        res = await agents.quick_compat_batch(profile_of(me), [(o["id"], profile_of(o)) for o in chunk])
        for o in chunk:
            r = res[o["id"]]
            if me["id"] < o["id"]:
                a, b, ab, ba = me["id"], o["id"], r["me_to_them"], r["them_to_me"]
            else:
                a, b, ab, ba = o["id"], me["id"], r["them_to_me"], r["me_to_them"]
            db.run("insert or replace into pairs values (?,?,?,?,?)", (a, b, ab, ba, r["note"]))

    chunks = [todo[i:i + 10] for i in range(0, len(todo), 10)]
    await asyncio.gather(*(score(c) for c in chunks))


async def run_dates(pid, k=TOP_K):
    view = rank.pair_view(pid)
    best = sorted(view, key=lambda o: view[o]["mine"] + view[o]["theirs"], reverse=True)[:k]

    async def date(oid):
        a, b = sorted((pid, oid))
        if db.one("select 1 from dates where a=? and b=?", (a, b)):
            return
        pa, pb = profile_of(get(a)), profile_of(get(b))
        transcript, va, vb = await agents.run_date(pa, pb)
        db.run("insert or replace into dates (a, b, transcript, verdict_a, verdict_b) values (?,?,?,?,?)",
               (a, b, json.dumps(transcript, ensure_ascii=False), json.dumps(va), json.dumps(vb)))

    results = await asyncio.gather(*(date(o) for o in best), return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            print("date failed:", r)


async def run_live(pid, job):
    def mark(i, state):
        job["steps"][i]["state"] = state

    try:
        mark(0, "active")
        await scrape_person(pid)
        mark(0, "done")
        mark(1, "active")
        await analyze_person(pid)
        mark(1, "done")
        mark(2, "active")
        await score_pairs(pid)
        mark(2, "done")
        mark(3, "active")
        await run_dates(pid)
        mark(3, "done")
        job["state"] = "done"
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        db.run("update people set status='error', error=? where id=?", (msg, pid))
        job["state"] = "error"
        job["error"] = msg
