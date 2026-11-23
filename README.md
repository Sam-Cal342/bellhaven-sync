# Bellhaven → CRM ownership sync

Keeps the CRM's facility → parent-company links correct for **Bellhaven Senior Living** by comparing the
operator's public website to the CRM every day, and turning every discrepancy into a proposal a human
approves or rejects. **Nothing is written to the CRM without an approval.**

```
website ──scrape──▶ locations ─┐
                               ├─▶ match + classify ─▶ proposals ─▶ review app ─(approve)─▶ CRM API
CRM API ──read─────▶ accounts ─┘        (pure)          (SQLite)        │
                                                                         └─(reject)─▶ remembered, never re-proposed
```

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows   (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
cp .env.example .env              # then put your CRM token in .env
python -m pytest -q               # 13 tests, use an in-memory fake CRM (no network)
python -m bhsync.pipeline --dry-run   # see what would be proposed, change nothing
python app.py                     # review app on http://127.0.0.1:5000 -> "Run pipeline now"
```

## What's in here

| Path | What it does |
|---|---|
| `bhsync/scrape.py` | Crawls the home page **and** every directory page (following "Next"), then each community detail page. Cross-checks counts against "N communities listed" / "serve N communities". |
| `bhsync/normalize.py` | Makes formatting noise disappear: `Rd`=`Road`, `Pk`=`Pike`, `NW`=`Northwest`, `&`=`and`, `Centre`=`Center`… |
| `bhsync/match.py` | Scores every (location, account) pair, assigns accounts to locations, classifies, and builds proposals with evidence. Pure function, no I/O. |
| `bhsync/store.py` | SQLite: proposals keyed by fingerprint, decisions, run history, audit log. |
| `bhsync/apply.py` | Executes **one** approved proposal: conflict check, live SOP re-check, write, read-back verification. |
| `bhsync/pipeline.py` | The daily job: scrape → read CRM → match → queue. Never writes to the CRM. |
| `app.py`, `templates/` | Flask review app: queue, decided history (with reopen), coverage, audit log. |
| `bhsync/config.py` | All tunable rules in one place: parent names, care-type mapping, thresholds, the SOP rule. |
| `.github/workflows/daily-sync.yml`, `deploy/crontab.txt` | Daily schedule (GitHub Actions; cron/Task Scheduler alternative). |
| `tests/` | Fake CRM built from the real snapshot; classification, SOP, end-to-end and app tests. |

## Matching

1. **Address first.** Normalised street + ZIP match = 60 pts; same street/city but different ZIP = 45 (catches the
   Portsmouth ZIP typo); street number + ZIP = 35; PO Box in the same ZIP = 10.
2. **Name** (brand word "Bellhaven" ignored, because every community shares it): ≥0.90 similarity = 30, ≥0.75 = 15.
3. **Phone** exact = 20. **Same city** = 5. **Different state = never a match.**
4. ≥ 50 points = same facility; ≥ 80 = "high confidence". Each account goes to its single best location.

Name alone can never produce a match (max 30 + city 5), which is what defeats the traps in the data:
"Amberly Manor" in Colorado vs. Ohio, "Union Square Senior Living" in the same town but on a different street,
"Carlisle, PA" vs. "New Carlisle, OH". Rejected look-alikes are shown to the reviewer as evidence.

## Classification → proposal

| Situation | Proposal | CRM write on approval |
|---|---|---|
| Matched, everything agrees | none (shown on Coverage page) | — |
| Matched, name / address / care type differ | **Fix existing account** | PATCH changed fields + dated note line |
| Matched, wrong or missing parent, **no** revenue or **no** AR | **Fix existing account** (re-parent) | PATCH `parent_id` (+ other fixes) |
| Matched, wrong parent, revenue **and** AR > 0 | **Change of ownership (SOP)** | POST new account under Bellhaven, then PATCH old account's `chow_current_account` **only** |
| Several accounts at the same facility | **Mark duplicate** for each loser | PATCH `duplicate_of_account`, `status=Inactive`, note |
| On website, no account | **New account** | POST |
| Under Bellhaven in CRM, not on website, another owner's account at the same address, revenue + AR | **Sold, billing preserved (SOP)** | PATCH `chow_current_account` → new owner's account only |
| …same, but no outstanding billing | **Mark duplicate** of the new owner's account | PATCH as above |
| Under Bellhaven in CRM, not on website, no buyer evidence | **Not on website** | PATCH `status=Needs Review` + note |

**Duplicate survivor:** billing history › already under Bellhaven › predecessor parent (Harborview, acquired outright,
over Cedar Trail, partly acquired) › more contacts › stronger website match.

**Not synced on purpose:** phone numbers (they disagree on nearly every record, including clear matches, so
the website is not obviously the better source); contacts.

## Safety and re-run guarantees

* **Fingerprints.** A proposal's fingerprint is a hash of *what it changes* (kind, account, location, target
  values), not of its wording or date. Re-running produces the same fingerprint, so:
  approved/rejected → skipped; still pending → refreshed in place (no duplicates); no longer proposed → `superseded`.
* **Approved changes disappear on their own**: the next run sees the CRM already agrees with the website.
  CHOW'd originals and inactivated duplicates are recognised as resolved and excluded from matching.
* **Atomic claim** (`pending → applying`) stops a double click from applying twice; creates also check for an
  account we already created before POSTing.
* **Conflict check.** At approval time the account is re-read; if a field we would change was edited since the
  proposal was made, nothing is written and the item is marked `conflict`.
* **SOP re-checked live.** Revenue/AR are re-read at approval; if the SOP outcome flipped, nothing is written.
* **Read-back verification** after every write, and an audit log of every request/response.
* **Scraper circuit breaker.** If the site suddenly yields < 70% of last run's locations, the run aborts instead
  of flagging real facilities as "not on website".
* **Client-side validation** of `care_type` and `status` (the API does not validate `care_type`).

## Schedule

`.github/workflows/daily-sync.yml` runs at 11:00 UTC daily (plus manual trigger), runs the tests first, then the
pipeline, and commits the updated decision store. The CRM token is a repository secret. In production, the
decision store would be a shared database used by both the job and the review app (`BHSYNC_DB`).
`deploy/crontab.txt` has the cron / Windows Task Scheduler equivalent.
