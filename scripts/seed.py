"""Load people.csv, scrape + profile everyone, then run the dates.

    python scripts/seed.py people.csv

Safe to re-run: finished people, pairs and dates are skipped.
Commit data/app.db afterwards so the deployed site starts with the full pool.
"""
import asyncio
import csv
import sys

sys.path.insert(0, ".")
from app import db, pipeline  # noqa: E402


async def main(path):
    db.init()
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    ids = [pipeline.upsert_person(r["linkedin_url"], r["instagram_url"]) for r in rows]
    print(f"{len(ids)} people in the csv")

    gate = asyncio.Semaphore(4)

    async def ingest(pid):
        async with gate:
            if pipeline.get(pid)["status"] == "ready":
                return
            try:
                await pipeline.scrape_person(pid)
                await pipeline.analyze_person(pid)
                print("ready:", pipeline.get(pid)["name"])
            except Exception as e:
                db.run("update people set status='error', error=? where id=?", (str(e), pid))
                print("FAILED:", pipeline.get(pid)["linkedin_url"], "->", e)

    await asyncio.gather(*(ingest(i) for i in ids))

    ready = [r["id"] for r in db.all("select id from people where status='ready'")]
    print(f"{len(ready)} profiles ready, scoring pairs")
    for pid in ready:
        await pipeline.score_pairs(pid)

    print("running dates")
    for pid in ready:
        await pipeline.run_dates(pid)
        print("dates done for", pipeline.get(pid)["name"])

    total = db.one("select count(*) as n from dates")["n"]
    print(f"done: {len(ready)} people, {total} dates")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "people.csv"))
