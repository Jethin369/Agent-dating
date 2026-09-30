# Agent dates

An agentic dating site. Every person is represented by an LLM agent that reads their public
LinkedIn and Instagram, builds a profile, and goes on first dates with other people's agents.
Each person then gets a ranked list of the people who fit them best.

## How it works

1. **Scrape.** Apify actors pull the public LinkedIn profile and the public Instagram profile.
2. **Profile.** One LLM call turns both into a structured profile (needs, hobbies, interests, values,
   personality, communication style, what they're likely looking for) with evidence from the source data.
3. **Quick score.** Every pair gets a cheap two-sided compatibility score, batched 10 candidates per call. 25 people means 300 pairs, so
   this runs in parallel and decides who is worth a full date.
4. **Dates.** Each person's top 5 candidates get a real multi-turn date. The two agents take turns,
   each seeing only its own person's profile and what the other one has said so far.
5. **Verdicts.** After the date, each agent writes its own score, what it liked, and concerns.
6. **Ranking.** Final score is 50% my agent's verdict, 30% their agent's verdict, 20% quick score.
   People who never got a date are ranked on the quick score alone (discounted) and marked as such.

Stack: FastAPI, SQLite, plain HTML/JS front end, Gemini (via its OpenAI-compatible API) for the agents, Apify for scraping.

## Run it locally

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env      # fill in GEMINI_API_KEY and APIFY_TOKEN
    set -a && source .env && set +a
    uvicorn app.main:app --reload

Open http://localhost:8000.

## Load the pool of people

Put one row per person in `people.csv` (`linkedin_url,instagram_url`), then:

    python scripts/seed.py people.csv

This scrapes everyone, builds profiles, scores every pair and runs the dates. It can be re-run and skips
finished work. The result lives in `data/app.db`, which is committed so the deployed site starts with
the full pool. `/demo` opens the rankings view of that finished run.

## Deploy

Any host that runs a long-lived Python process works (Render, Railway, Fly). Not serverless: a live
run takes a couple of minutes.

- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Env vars: `GEMINI_API_KEY`, `APIFY_TOKEN`

New people added on the live site are stored in SQLite on the server. On hosts with an ephemeral disk
they disappear on redeploy, the seeded pool doesn't.

## Limits

- Scraping depends on third-party Apify actors. If one changes, set `APIFY_LINKEDIN_ACTOR` or
  `APIFY_INSTAGRAM_ACTOR` to another actor and adjust `app/scrape.py`.
- Private Instagram accounts are rejected on purpose.
- The agents only know what is on the two profiles, so thin profiles make for generic dates.
