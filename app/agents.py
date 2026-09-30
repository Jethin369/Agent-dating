import asyncio
import json
import os
import re
import time

from openai import AsyncOpenAI

# Gemini through its OpenAI-compatible endpoint, so the openai client works as is
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
TURNS = int(os.getenv("DATE_TURNS", "6"))
# gemini 2.5 counts its hidden thinking tokens against max_tokens, so leave room
THINK_PAD = int(os.getenv("THINK_PAD", "2000"))
RPM = float(os.getenv("LLM_RPM", "0"))  # set this if you're on the free tier
_sem = asyncio.Semaphore(int(os.getenv("LLM_CONCURRENCY", "6")))
_pace_lock = asyncio.Lock()
_last_call = 0.0
_client = None


def client():
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["GEMINI_API_KEY"], base_url=BASE_URL)
    return _client


async def pace():
    global _last_call
    if RPM <= 0:
        return
    async with _pace_lock:
        wait = _last_call + 60.0 / RPM - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


async def chat(system, user, as_json=False, temperature=0.8, max_tokens=700):
    extra = {"response_format": {"type": "json_object"}} if as_json else {}
    async with _sem:
        for attempt in range(5):
            try:
                await pace()
                r = await client().chat.completions.create(
                    model=MODEL,
                    temperature=temperature,
                    max_tokens=max_tokens + THINK_PAD,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    **extra,
                )
                text = r.choices[0].message.content or ""
                print(".", end="", flush=True)
                return parse_json(text) if as_json else text.strip()
            except Exception as e:
                print(f"\n[gemini retry {attempt + 1}/5] {repr(e)[:200]}")
                if attempt == 4:
                    raise
                await asyncio.sleep(4 * (attempt + 1))


# ---------- reading a person ----------

PROFILE_SYSTEM = """You read a person's public LinkedIn and public Instagram data and write a profile
that a dating agent can use to represent them.

Rules:
- Only use what is in the data. If something isn't supported, leave it out. Shorter lists are fine.
- Do not guess sensitive things: religion, sexual orientation, health, politics, ethnicity, relationship history.
- "looking_for" is a tentative read of what they'd probably value in a partner, based on their values and lifestyle.
- Every item in "evidence" points at something concrete from the data.

Return JSON with exactly these keys:
name, headline, location, summary (3-4 sentences), needs (list of short strings, what they need
day to day and in a relationship), hobbies (list), interests (list), values (list), personality (list),
communication_style (string), lifestyle (string), looking_for (string),
evidence (list of {claim, source: "linkedin" or "instagram", detail})"""


async def build_profile(linkedin_text, instagram_text):
    user = f"LINKEDIN DATA:\n{linkedin_text}\n\nINSTAGRAM DATA:\n{instagram_text}"
    prof = await chat(PROFILE_SYSTEM, user, as_json=True, temperature=0.3, max_tokens=1400)
    prof.setdefault("name", "Unknown")
    return prof


def brief(p):
    keep = ["name", "headline", "summary", "needs", "hobbies", "interests", "values",
            "personality", "communication_style", "lifestyle", "looking_for"]
    return json.dumps({k: p.get(k) for k in keep}, ensure_ascii=False)


# ---------- quick compatibility ----------

BATCH_SYSTEM = """You judge how well people might get along as a couple, from each person's own point of view.
You get one PERSON and a list of CANDIDATES, each with an id. Use the whole 0-100 range: 50 is an average
stranger, 80+ should be rare and needs real overlap in values, lifestyle and interests.
For every candidate return {"id": id, "me_to_them": int, "them_to_me": int, "note": one sentence}.
me_to_them is how good the candidate is for PERSON, from PERSON's point of view. them_to_me is the reverse.
Return JSON: {"results": [ ... ]} with one entry per candidate."""


async def quick_compat_batch(me, others):
    """others is a list of (id, profile). Returns {id: {me_to_them, them_to_me, note}}"""
    cands = "\n".join(f"ID {oid}: {brief(p)}" for oid, p in others)
    r = await chat(BATCH_SYSTEM, f"PERSON:\n{brief(me)}\n\nCANDIDATES:\n{cands}",
                   as_json=True, temperature=0.2, max_tokens=120 * len(others) + 200)
    got = {}
    for item in r.get("results", []):
        try:
            got[int(item["id"])] = item
        except (KeyError, TypeError, ValueError):
            continue
    out = {}
    for oid, _ in others:
        item = got.get(oid, {})
        out[oid] = {"me_to_them": clamp(item.get("me_to_them")),
                    "them_to_me": clamp(item.get("them_to_me")),
                    "note": str(item.get("note", ""))[:300]}
    return out


def clamp(x):
    try:
        return max(0, min(100, int(x)))
    except (TypeError, ValueError):
        return 50


# ---------- the date ----------

def agent_system(me, other_name):
    return f"""You are the dating agent for {me['name']}. You are on a first date with {other_name}, and you speak
as {me['name']} in first person.

Here is everything you know about {me['name']}:
{brief(me)}

How to behave:
- Stay true to this profile: their interests, values and way of talking.
- Never invent big facts (jobs, trips, family) that aren't in the profile. If unsure, stay general.
- You know nothing about {other_name} except what they say in the conversation.
- React to what they actually said, ask real questions, and let some of your own personality show.
- If something clashes with your values, you can show it politely. Don't fake chemistry.
- 1 to 3 sentences per message. Plain speech, no lists, no stage directions."""


async def run_date(pa, pb, turns=TURNS):
    people = (pa, pb)
    transcript = []
    for i in range(turns):
        me, other = people[i % 2], people[(i + 1) % 2]
        history = "\n".join(f"{t['speaker']}: {t['text']}" for t in transcript) or "(nothing said yet)"
        opener = "You're meeting at a coffee place. Start the conversation." if i == 0 else "Reply."
        text = await chat(
            agent_system(me, other["name"]),
            f"Conversation so far:\n{history}\n\n{opener}",
            temperature=0.9, max_tokens=160,
        )
        prefix = f"{me['name']}:"
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
        transcript.append({"speaker": me["name"], "text": text})
    va, vb = await asyncio.gather(verdict(pa, pb["name"], transcript), verdict(pb, pa["name"], transcript))
    return transcript, va, vb


VERDICT_SYSTEM = """You are a dating agent deciding, honestly and for your person, how the date went.
Use the full range: 50 is fine but forgettable, 80+ means real chemistry and shared values, below 35 means a poor fit.
Return JSON: {"score": int 0-100, "would_meet_again": bool, "liked": [short strings],
"concerns": [short strings], "summary": two sentences written about the other person from your person's view}."""


async def verdict(me, other_name, transcript):
    log = "\n".join(f"{t['speaker']}: {t['text']}" for t in transcript)
    user = f"Your person:\n{brief(me)}\n\nThey just had a date with {other_name}. Transcript:\n{log}"
    r = await chat(VERDICT_SYSTEM, user, as_json=True, temperature=0.3, max_tokens=350)
    r["score"] = clamp(r.get("score"))
    r["would_meet_again"] = bool(r.get("would_meet_again"))
    r.setdefault("liked", [])
    r.setdefault("concerns", [])
    r.setdefault("summary", "")
    return r
