import asyncio
import json
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, pipeline, rank

app = FastAPI(title="Agent dates")
db.init()

STATIC = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

jobs = {}
background = set()

LI_RE = re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[^/?#\s]+", re.I)
IG_RE = re.compile(r"^https?://(www\.)?instagram\.com/[A-Za-z0-9._]+/?(\?.*)?$", re.I)


class NewPerson(BaseModel):
    linkedin_url: str
    instagram_url: str


@app.get("/")
@app.get("/demo")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/people")
def people():
    rows = db.all("select id, name, profile, status from people where status='ready' order by name")
    return [{"id": r["id"], "name": r["name"],
             "headline": json.loads(r["profile"]).get("headline", "")} for r in rows]


@app.get("/api/people/{pid}")
def person(pid: int):
    row = pipeline.get(pid)
    if not row or row["status"] != "ready":
        raise HTTPException(404, "Person not found")
    p = json.loads(row["profile"])
    p["id"] = pid
    p["linkedin_url"] = row["linkedin_url"]
    p["instagram_url"] = row["instagram_url"]
    return p


@app.post("/api/people")
async def add_person(body: NewPerson):
    li, ig = body.linkedin_url.strip(), body.instagram_url.strip()
    if not LI_RE.match(li):
        raise HTTPException(400, "The LinkedIn link should look like https://www.linkedin.com/in/username")
    if not IG_RE.match(ig):
        raise HTTPException(400, "The Instagram link should look like https://www.instagram.com/username")

    pid = pipeline.upsert_person(li, ig)
    if pipeline.get(pid)["status"] == "ready":
        return {"person_id": pid, "job_id": None}

    db.run("update people set status='working', error=null, instagram_url=? where id=?",
           (pipeline.clean_url(ig), pid))
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {
        "state": "running", "error": None, "person_id": pid,
        "steps": [{"label": s, "state": "waiting"} for s in pipeline.LIVE_STEPS],
    }
    task = asyncio.create_task(pipeline.run_live(pid, jobs[job_id]))
    background.add(task)
    task.add_done_callback(background.discard)
    return {"person_id": pid, "job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Unknown job")
    return jobs[job_id]


@app.get("/api/dates")
def dates():
    names = {p["id"]: p["name"] for p in db.all("select id, name from people")}
    out = []
    for d in db.all("select * from dates order by id"):
        out.append({"id": d["id"], "a": names.get(d["a"]), "b": names.get(d["b"]),
                    "score_a": json.loads(d["verdict_a"])["score"],
                    "score_b": json.loads(d["verdict_b"])["score"]})
    return out


@app.get("/api/dates/{did}")
def date(did: int):
    d = db.one("select * from dates where id=?", (did,))
    if not d:
        raise HTTPException(404, "Date not found")
    a, b = pipeline.get(d["a"]), pipeline.get(d["b"])
    return {"a": {"id": a["id"], "name": a["name"]}, "b": {"id": b["id"], "name": b["name"]},
            "transcript": json.loads(d["transcript"]),
            "verdict_a": json.loads(d["verdict_a"]), "verdict_b": json.loads(d["verdict_b"])}


@app.get("/api/rankings/{pid}")
def rankings(pid: int):
    if not pipeline.get(pid):
        raise HTTPException(404, "Person not found")
    return rank.rankings(pid)
