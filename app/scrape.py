import json
import asyncio
import os
import re

import httpx

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
LI_ACTOR = os.getenv("APIFY_LINKEDIN_ACTOR", "harvestapi~linkedin-profile-scraper")
IG_ACTOR = os.getenv("APIFY_INSTAGRAM_ACTOR", "apify~instagram-profile-scraper")

RESERVED = {"p", "reel", "reels", "explore", "stories", "accounts", "tv"}


class ScrapeError(Exception):
    pass


_gate = asyncio.Semaphore(3)  # apify free plan allows 5 concurrent runs


async def run_actor(actor, payload, timeout=240):
    if not APIFY_TOKEN:
        raise ScrapeError("APIFY_TOKEN is not set on the server")
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    async with _gate:
        for attempt in range(6):
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, params={"token": APIFY_TOKEN}, json=payload)
            if resp.status_code == 402 and "concurrent" in resp.text:
                await asyncio.sleep(10 * (attempt + 1))
                continue
            break
    if resp.status_code >= 400:
        raise ScrapeError(f"Apify error {resp.status_code} for {actor}: {resp.text[:200]}")
    return resp.json()


def strip_junk(x):
    # drop image/link urls and empty values, the model doesn't need them
    if isinstance(x, dict):
        out = {k: strip_junk(v) for k, v in x.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(x, list):
        return [v for v in (strip_junk(i) for i in x) if v not in (None, "", [], {})]
    if isinstance(x, str) and x.startswith("http"):
        return None
    return x


async def linkedin(url):
    payload = {"queries": [url]}
    if os.getenv("APIFY_LINKEDIN_MODE"):
        payload["profileScraperMode"] = os.environ["APIFY_LINKEDIN_MODE"]
    items = await run_actor(LI_ACTOR, payload)
    if not items:
        raise ScrapeError("LinkedIn returned nothing. Check the profile is public and the URL is right.")
    text = json.dumps(strip_junk(items[0]), ensure_ascii=False)
    return text[:7000]


def ig_username(url):
    m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", url)
    if not m or m.group(1).lower() in RESERVED:
        raise ScrapeError("That doesn't look like an Instagram profile link.")
    return m.group(1)


async def instagram(url):
    username = ig_username(url)
    items = await run_actor(IG_ACTOR, {"usernames": [username]})
    if not items:
        raise ScrapeError(f"Instagram returned nothing for @{username}.")
    p = items[0]
    if p.get("private"):
        raise ScrapeError(f"@{username} is a private account. Only public profiles work.")
    posts = []
    for post in (p.get("latestPosts") or [])[:12]:
        posts.append({
            "caption": (post.get("caption") or "")[:300],
            "hashtags": post.get("hashtags") or [],
            "location": post.get("locationName"),
            "type": post.get("type"),
        })
    slim = {
        "username": username,
        "fullName": p.get("fullName"),
        "biography": p.get("biography"),
        "followers": p.get("followersCount"),
        "following": p.get("followsCount"),
        "postCount": p.get("postsCount"),
        "category": p.get("businessCategoryName"),
        "posts": posts,
    }
    return json.dumps(strip_junk(slim), ensure_ascii=False)[:6000]
