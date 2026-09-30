import json

from . import db


def pair_view(pid):
    """Quick scores from pid's point of view: {other_id: {mine, theirs, note}}"""
    out = {}
    for r in db.all("select * from pairs where a=? or b=?", (pid, pid)):
        if r["a"] == pid:
            out[r["b"]] = {"mine": r["score_ab"], "theirs": r["score_ba"], "note": r["note"]}
        else:
            out[r["a"]] = {"mine": r["score_ba"], "theirs": r["score_ab"], "note": r["note"]}
    return out


def rankings(pid):
    view = pair_view(pid)
    people = {p["id"]: p for p in db.all("select id, name, profile from people where status='ready'")}

    dated = {}
    for d in db.all("select * from dates where a=? or b=?", (pid, pid)):
        i_am_a = d["a"] == pid
        other = d["b"] if i_am_a else d["a"]
        mine = json.loads(d["verdict_a"] if i_am_a else d["verdict_b"])
        theirs = json.loads(d["verdict_b"] if i_am_a else d["verdict_a"])
        dated[other] = (d["id"], mine, theirs)

    rows = []
    for oid, q in view.items():
        if oid not in people:
            continue
        quick = 0.6 * q["mine"] + 0.4 * q["theirs"]
        if oid in dated:
            did, mine, theirs = dated[oid]
            # my verdict counts most, theirs matters too, quick score is a small tiebreaker
            score = 0.5 * mine["score"] + 0.3 * theirs["score"] + 0.2 * quick
            reason = mine.get("summary", "")
            extra = {"liked": mine.get("liked", []), "concerns": mine.get("concerns", []),
                     "my_score": mine["score"], "their_score": theirs["score"],
                     "meet_again": mine.get("would_meet_again")}
        else:
            did = None
            score = 0.85 * quick
            reason = q["note"]
            extra = {}
        headline = json.loads(people[oid]["profile"]).get("headline", "")
        rows.append({"person_id": oid, "name": people[oid]["name"], "headline": headline,
                     "score": round(score), "quick": round(quick), "date_id": did,
                     "reason": reason, **extra})

    rows.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
