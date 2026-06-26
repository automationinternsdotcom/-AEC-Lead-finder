# Generic B2B Lead Qualification Rubric

Use this rubric for every campaign unless a campaign explicitly overrides it.
The CampaignSpec supplies the company-specific details: client context, target
industry, ICP, geography, trigger signals, buyer personas, negative keywords,
qualification notes, and outreach angle.

## Core Decision

Qualify each article/page as:

```text
HIGH = strong buying moment + clear target fit + actionable entity
MEDIUM = plausible buying moment + weaker timing/detail/fit
LOW = no buying moment, wrong audience, wrong geography, or not actionable
```

## HIGH

Assign `high` when the page contains a clear, concrete buying moment that
matches the campaign's ICP and geography.

A HIGH lead usually has:

- a named company, property, project, organization, or account
- a specific event or trigger signal from the CampaignSpec
- clear timing or active movement, such as opening, launch, expansion, funding,
  hiring, lease-up, construction, relocation, vendor change, compliance
  deadline, procurement need, or operational change
- enough detail to identify who the likely buyer or account is
- a plausible reason the client should reach out soon

HIGH means: continue to enrichment and delivery with priority emphasis.

## MEDIUM

Assign `medium` when the page contains a plausible lead, but the timing, fit, or
actionability is weaker.

A MEDIUM lead usually has:

- a real company, project, account, or organization
- some connection to the campaign's target market
- a possible trigger signal, but one that is earlier-stage, indirect, lower
  urgency, or missing key details
- longer sales timeline or uncertain buying need
- enough information to track or enrich later

MEDIUM means: continue to enrichment and delivery as a routine lead unless
deterministic gates reject it.

## LOW

Assign `low` when the page does not contain an actionable lead for this
campaign.

A LOW item usually has:

- no specific company, project, account, or organization to pursue
- no campaign-relevant buying moment
- wrong geography or service area
- wrong customer type or audience
- consumer, residential, or non-B2B content when the campaign is B2B
- macro commentary, trend pieces, rankings, awards, opinion pieces, generic
  market reports, or people moves without a buying signal
- duplicate, stale, non-public, login-required, or paywalled content
- content that only mentions the target geography or industry in passing

LOW means: do not enrich or deliver.

## Confidence

Return a confidence score from `0.0` to `1.0`.

Use higher confidence when:

- the relevant entity is named
- the event is explicit
- geography/service area is clear
- the page directly supports the trigger signal
- extracted fields are grounded in the text

Use lower confidence when:

- the entity is ambiguous
- the event is implied but not explicit
- geography is unclear
- the article is thin, partial, or mostly commentary
- the buying need is plausible but inferred

## Signal Type

Choose the closest campaign-relevant event category.

Use the campaign's configured trigger signals first. If no configured category
fits, use `other`.

Common generic signal categories:

```text
opening
development
acquisition
expansion
lease
construction
funding
hiring
compliance
procurement
partnership
other
```

If the output schema only allows a smaller fixed enum, choose the closest
allowed value and use `other` when none fit.

## Filter Reason

Always write one short sentence explaining the decision. This is the audit trail
for why the record was rated HIGH, MEDIUM, or LOW.

## Service Angle

For HIGH and MEDIUM only, write one campaign-voice sentence explaining why the
client should reach out.

Use the campaign's outreach angle and value framing. Do not write a service
angle for LOW records.

## Campaign-Specific Inputs

Each campaign should provide:

- industry / ICP
- service area / geography
- trigger signals
- buyer personas
- negative keywords
- HIGH examples
- MEDIUM examples
- LOW examples
- outreach angle

The rubric stays generic. The campaign provides the details.
