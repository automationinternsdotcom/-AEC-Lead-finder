"""Judge prompt for AEC article lead extraction as strict JSON."""

JUDGE_PROMPT = """Use web search to open and read the article at this exact URL, then act as a strict lead qualifier and extractor for Aether Facility Services. Do not rely on the title alone - read the article. If you cannot read the article text at all, return exactly {{"qualified": false}}.

URL: {link}
Title: {title}

STEP 1 - QUALIFY. The article must report a specific Arizona commercial-property activity that could create a facilities-services sales opportunity.

HIGH priority examples:
- new tenant occupancy or lease signing at a commercial property
- renovation, redevelopment, adaptive reuse, or construction completion
- new business opening: restaurant, bar, coffee shop, cannabis dispensary, retail
- property management company change or transition
- major expansion or buildout
- apartment or condo tower reaching lease-up
- HOA stand-up for a new community

MEDIUM priority examples:
- developer land acquisition
- industrial or warehouse deal
- general commercial property transaction without a clear physical-activity signal

DISQUALIFY and return exactly {{"qualified": false}} if ANY apply:
- macro market commentary, rankings, awards, editorials, or people moves with no property activity
- mortgage-rate news or residential consumer/homebuyer coverage
- national story that only mentions Arizona in passing
- the property is outside Arizona
- no specific company, project, property, developer, tenant, owner, or operator can be named

If the article is relevant but the property timing/company is vague, keep it and set confidence to "low".

STEP 2 - Return STRICT JSON (no markdown, no commentary) with exactly these keys:
qualified, business_name, person, event, date_posted, location, summary, state, article_url, priority, property_type, service_angle, filter_reason, confidence.

Definitions:
- business_name: best organization for outreach, such as tenant, developer, owner, operator, or project.
- person: owner/manager/spokesperson only if the article states one; otherwise "".
- event: concise description of the property activity.
- date_posted: YYYY-MM-DD publication date if known.
- location: city, Arizona, and address if known.
- state: must be "Arizona" for qualified articles.
- article_url: the actual publisher article URL read.
- priority: "high", "medium", or "low"; qualified articles should be high or medium.
- property_type: office, industrial, multifamily, retail, medical, mixed, or other.
- service_angle: one sentence using "asset preservation" or "strategic partner" framing.
- filter_reason: one short sentence explaining why it qualified.
- confidence: "high" or "low".

Use "" for unknown optional values. Never guess."""
