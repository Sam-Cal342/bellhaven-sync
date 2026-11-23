# Writeup — Bellhaven ownership sync

> Draft. Edit freely; the time spent figure and any review decisions still need to be filled in.

## Result
- **35** Bellhaven communities on the website (34 in the directory plus Findlay, which is linked only from a home-page announcement).
- Every one of them maps to exactly one active CRM account under Bellhaven after review, and every CRM account that claimed to be Bellhaven but isn't on the site was dealt with.
- The pipeline proposed **29 changes**: 13 field fixes or direct re-parents, 2 changes of ownership under the billing SOP, 1 sale with billing preserved, 7 duplicates, 4 new accounts, and 2 accounts flagged as no longer on the website.
- Review decisions: _fill in, e.g. "approved 28, rejected 1 (reason)"_.

## Matching approach
**Address is the identity and the name is just a label.** Facilities get renamed on every acquisition ("Sunny Acres Retirement Home" → "Bellhaven Willow Creek", "Cedar Trail of Zanesville" → "Bellhaven of Zanesville"), but they don't move. So:
- Each (website location, CRM account) pair is scored. A normalized street + ZIP match is worth 60 points. Name similarity (ignoring the shared brand word) adds up to 30, phone 20, same city 5. A different state rules out a match.
- 50 points is a match. Name alone can never get there, which is what defeats the look-alikes in the data: an "Amberly Manor" in Colorado, a "Union Square Senior Living" in the same town on a different street, and Carlisle, PA vs New Carlisle, OH. The reviewer sees those rejected candidates as evidence on the "new account" proposals.
- Normalization keeps formatting from showing up as differences. "Rd/Road", "Pk/Pike", "NW/Northwest" and "&/and" all compare equal, so I don't churn addresses that are only formatted differently.
- When several accounts match one facility (Monroe and Kettering had three each), one survivor is chosen in this order: billing history, then already under Bellhaven, then a predecessor parent (Harborview, acquired outright, ranks above Cedar Trail, only partly acquired), then more contacts, then a stronger match. The rest are marked Inactive with `duplicate_of_account`.

## Judgment calls
- **SOP.** The re-parent decision checks `lifetime_revenue > 0 AND outstanding_ar > 0`. Marietta and Tiffin meet it. For each, a new account is created under Bellhaven and the old one gets **only** `chow_current_account`; parent, status and note are left exactly as they were. Lima ($47K revenue, $0 AR) and Findlay ($22K revenue, $0 AR) are re-parented directly. The SOP is re-checked against live values at approval time, in case AR changes between the run and the review.
- **Sandusky.** It's under Bellhaven in the CRM but not on the website, and a Millstone account exists at the same address, so the facility was sold. It has $130K revenue and $5.2K AR, so per the SOP I didn't touch it except to point `chow_current_account` at the existing Millstone account. Creating a third account for a facility that already has a correctly parented record would just create a new duplicate.
- **Alliance and Coldwater.** They're under Bellhaven and not on the website, with no sign of a buyer. I set them to `Needs Review` with a note rather than `Inactive`. Missing from a marketing website isn't proof of closure, and inactivating a live account hides it from reps.
- **Phones aren't synced.** They disagree on nearly every record, including unambiguous matches, so the website isn't clearly the better source. Phones are used only as matching evidence, and new accounts take the website phone.
- **Care type.** Website offerings map to CRM values ("Short-Term Rehabilitation & Nursing" → Skilled Nursing, "Memory Support" → Memory Care). Where the website lists two offerings, an existing CRM value that's one of them is kept.

## Re-run safety
- Each proposal has a fingerprint built from *what it changes*, not from its wording or date. A re-run therefore skips anything approved or rejected, refreshes pending items in place, and supersedes items that no longer apply.
- Approved changes don't come back because the CRM now agrees with the website.
- Before writing, the review app re-reads the account and refuses to write if someone edited it since the proposal was made.
- A circuit breaker stops a broken scrape from flagging the whole portfolio as "not on website".
- Tested end to end: approving all 29 proposals against a fake CRM leaves the next run with **zero** proposals. Against the real CRM, a second run reported 0 new, 29 still pending, and no duplicates.

## How I used AI
I used Claude Code as the builder and acted as the reviewer and decision-maker:
- **I set the process.** First I gave it full context. Then it explored and reported, I approved a plan and four business judgment calls (Sandusky, stale accounts, duplicate survivor ranking, phones), and only then did it build, piece by piece, with checks at each step.
- **I made it prove its work.** It wrote tests against a fake CRM built from the real data, and I had it **deliberately break the SOP rule** to confirm the tests would catch it (they did). Before anything was approved, I had it diff the live CRM against the original snapshot to confirm the tooling had written nothing.
- **Mistakes caught along the way:**
  - A probe meant to see API validation errors **actually wrote** `care_type="Bogus"` to a real account, because the API doesn't validate that field. It was reverted immediately and verified against the snapshot. That's why the client now validates `care_type` itself.
  - The first version of the scraper logic would have missed Findlay, because the directory lists 34 of 35 communities.
  - Name similarity was inflated by the shared word "Bellhaven", which surfaced irrelevant candidates. Fixed by ignoring the brand word.
  - An operator-precedence bug in the display code, and a test that recursed into itself, were both caught before they mattered.
- **I did the review myself in the app.** The AI built the queue, and every write to the CRM was my decision.

## What I'd build next
1. **Shared decision store.** Postgres instead of a SQLite file committed by CI, so the scheduled job and the review app share state properly.
2. **Contact re-homing.** When a duplicate is inactivated, offer to move its contacts to the survivor (Owosso has one), and sync website administrators to contacts.
3. **More evidence sources.** CMS/NPI ownership data (the NPI registry lists the legal business name and authorized official), state licensing lookups, and press releases, to confirm sales like Sandusky instead of inferring them from a shared address.
4. **Parent-level changes.** Harborview now has no active facilities, so propose linking or retiring the predecessor parent accounts.
5. **Generalize to every parent.** The matcher is operator-agnostic. Add a per-operator config (site URL plus parser) and run it across the roughly 60% of LTC accounts that have a parent.
6. **Notifications.** A daily Slack or email digest with the count of new proposals and a link to the queue.

## Time spent
_Fill in honestly: e.g. "~X hours, including Y for review and the demo recording."_
