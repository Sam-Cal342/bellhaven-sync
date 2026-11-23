# Build log (how the work was directed and checked)

Running log of decisions, AI mistakes caught, and verification. Feeds the writeup.

## Decisions approved by me before building
1. **Sandusky** (off website, Millstone account at same address, $130K revenue / $5.2K AR): leave the account as is and set `chow_current_account` to the existing Millstone account. Don't create a third record.
2. **Stale accounts without a sale signal** (Alliance, Coldwater): set them to `Needs Review` with a note, not `Inactive`. Missing from a website isn't proof of closure.
3. **Duplicate survivor ranking:** billing history first, then current parent (Bellhaven > Harborview, fully acquired > Cedar Trail, partly acquired > other), then contacts, then match strength.
4. **Phones:** not synced on existing accounts (they disagree almost everywhere, even on clear matches). Used only as evidence. New accounts take the website phone.

## API behaviour learned by probing
- PATCH mutable fields: name, parent_id, status, note, care_type, phone, billing_street, billing_city, billing_state, billing_zip, chow_current_account, duplicate_of_account.
- Server validates `status` and `parent_id`, and POST requires `name`.
- **Server does NOT validate `care_type`.** Found it the hard way: a probe meant to fail actually wrote `care_type="Bogus"` to Cedar Trail of Zanesville. I reverted it immediately and verified it against the snapshot (only `updated_at` differs). Fix: the pipeline checks care_type against an allowed list before any write.
- No request-body schema in OpenAPI, so fields were inferred from GET responses.

## Verification checkpoints
- **Scraper:** 35 locations, which ties to the home page ("serve 35 communities") and the directory ("34 communities listed" plus Findlay, found only via the home page).
- **Matcher output vs my manual discovery pass:** identical case-for-case (notes/01).
- **Live dry run vs snapshot:** the same 29 proposals.
- **Tests:** 13 passing (normalization, per-case classification, SOP, approve-all-then-rerun = zero proposals, rejected not re-proposed, double apply refused, conflict detection, live SOP re-check, scraper circuit breaker, app routes).
- **Mutation check:** made the SOP rule "OR" instead of "AND", and separately "never CHOW". The tests caught both.
- **Live re-run in the app:** run 2 showed 0 new, 29 still pending, 0 superseded.
- **Live CRM vs original snapshot after all of the above:** 121 accounts, 0 field differences. Only Zanesville's `updated_at` moved, from the reverted probe.

## Bugs caught in the AI's own code
- The shared brand word "Bellhaven" inflated name similarity, so near-miss evidence was noisy. Now ignored in name comparison.
- Operator-precedence bug in the before/after display code (`x or y if c else z`). Rewritten explicitly.
- The app test's monkeypatch recursed into itself, which made the queue look empty. Fixed by keeping a reference to the original.
- Duplicate losers with contacts would leave those contacts on an inactive account. The proposal now warns the reviewer.
