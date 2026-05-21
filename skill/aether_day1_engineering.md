# Aether — Day 1 Overview

**Date:** Wed, May 20, 2026  ·  **Time spent:** ~4–6 hours

## What is Aether?

Aether is an automated daily pipeline that finds commercial real estate sales leads in Arizona. Each morning it will:

1. **Scan the news** — pull articles from real estate news sources (RSS feeds + Google News).
2. **Read & extract** — use AI to pull out the useful details (company, property type, deal size, location).
3. **Enrich** — look up contact and company info via Apollo.
4. **Push to CRM** — create a deal in Pipedrive so the sales team can act on it.

The whole thing runs automatically every day on GitHub, with no one needing to press a button.

---

## The goal for Day 1

Day 1 is about building the **foundation**, not the features. Think of it like framing a house before adding plumbing and electrical.

By end of day, the project should have:
- A clean, organized code structure that everything else can build on.
- A working database with all the tables the pipeline will need.
- Configuration, logging, and basic utilities in place and tested.
- A verified list of news sources to pull from.
- Automated checks (testing + quality tools) all passing.

**Nothing talks to the outside world yet** — no news fetching, no AI calls, no CRM writes. Those are the "rooms" we build in the coming days.

---

## What gets done today

| Task | What it means |
|---|---|
| Project cleanup | Reorganize files into one tidy package so the project is easy to navigate. |
| Core building blocks | Set up shared tools, settings, and logging that every other part relies on. |
| Database setup | Create the database and all its tables so data has a place to live. |
| Source list | Verify and fill in ~12–15 news sources to monitor. |
| Safety checks | Add automated tests and quality checks; confirm everything passes. |
| Secure keys | Store the 4 needed access keys (for AI, Apollo, Pipedrive) safely in GitHub. |
| Documentation | Write a short README so anyone can understand and run the project. |

---

## Explicitly NOT for today

These are intentionally saved for later days so we don't get ahead of ourselves:
- Fetching live news articles → **Day 2**
- Using AI to read articles → **Day 2**
- Connecting to Apollo or Pipedrive → **Day 4**
- Turning on the daily automatic schedule → **Day 6**

---

## How we know Day 1 is "done"

A short checklist of green lights:
- All automated tests pass and quality checks are clean.
- The project runs start-to-finish without errors (even though it's mostly empty so far).
- The database exists with all its tables.
- The 4 secure keys are saved in GitHub.
- The source list is filled in and the README is drafted.
- Everything is committed and the automated checks pass on GitHub.

---

## What's next (Day 2)

With the foundation solid, Day 2 starts building the parts that actually do the work — fetching real news articles and using AI to read and extract leads from them. Because Day 1 set things up carefully, the rest of the build should be faster and safer.

---

*This is a plain-language summary. The full technical plan with all code and commands lives in the engineering documentation.*