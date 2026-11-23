"""Daily job: scrape -> read CRM -> match -> queue proposals. Never writes to the CRM.

    python -m bhsync.pipeline            # normal daily run
    python -m bhsync.pipeline --dry-run  # print what would be queued, change nothing
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from .crm import CRMClient
from .match import KIND_LABELS, build_proposals
from .scrape import save_snapshot, scrape
from .store import Store

# If the website suddenly shows far fewer communities than last time, the scraper is probably
# broken (layout change, outage). Stop rather than flag dozens of real facilities as "not on website".
MIN_LOCATION_RATIO = 0.7


def run(store: Store | None = None, crm=None, scrape_fn=scrape, dry_run: bool = False, today=None,
        snapshot: bool = True) -> dict:
    store = store or Store()
    crm = crm or CRMClient()
    previous = store.last_run()
    run_id = store.start_run()
    try:
        res = scrape_fn()
        n = len(res.locations)
        if n == 0:
            raise RuntimeError("Scraper found 0 locations - aborting so nothing gets flagged as stale")
        if previous and previous.get("locations") and n < MIN_LOCATION_RATIO * previous["locations"]:
            raise RuntimeError(f"Scraper found {n} locations vs {previous['locations']} last run - "
                               "looks like a scraping failure, aborting")
        if snapshot:
            save_snapshot(res)
        accounts, contacts = crm.list_accounts(), crm.list_contacts()
        report = build_proposals(res.locations, accounts, contacts, today=today)
        stats = {"new_proposals": 0, "refreshed": 0, "skipped_decided": 0, "superseded": 0}
        if not dry_run:
            stats = store.sync_proposals(run_id, report.proposals)
        store.finish_run(run_id, status="dry-run" if dry_run else "ok", locations=n, accounts=len(accounts),
                         warnings=res.warnings + report.warnings, coverage=report.coverage, **stats)
        return {"run_id": run_id, "locations": n, "accounts": len(accounts), "report": report,
                "warnings": res.warnings + report.warnings, **stats}
    except Exception as exc:
        store.finish_run(run_id, status=f"error: {exc}")
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="match and print, but don't queue anything")
    args = ap.parse_args(argv)
    out = run(dry_run=args.dry_run)
    report = out["report"]
    print(f"Run {out['run_id']}: {out['locations']} website locations, {out['accounts']} CRM accounts")
    for w in out["warnings"]:
        print(f"  WARN {w}")
    print("Proposals generated this run:")
    for kind, n in Counter(p.kind for p in report.proposals).most_common():
        print(f"  {n:3d}  {KIND_LABELS[kind]}")
    print(f"Queue: {out['new_proposals']} new, {out['refreshed']} refreshed (still pending), "
          f"{out['skipped_decided']} skipped (already approved/rejected), {out['superseded']} superseded")
    if args.dry_run:
        for p in report.proposals:
            print(f"  [{p.kind}] {p.title}  {p.changes or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
