# 2-minute demo script

Setup: run `python app.py` and open http://127.0.0.1:5000 in one browser tab. Open the CRM browser in a second tab.
Start the screen recorder.

1. **(15s) Frame it.** "Daily pipeline scrapes Bellhaven's site, matches each community to the CRM by address, and queues every discrepancy. Nothing writes without approval." Point at the counts and the filter chips.
2. **(30s) Show evidence on two or three cards.**
   - *Change of ownership: Bellhaven of Marietta.* Point at the SOP box ($51,250 revenue, $3,800 AR, so the old account is preserved) and at the "new account to create" table.
   - *Duplicate: Kettering* (three accounts at one address). Explain why the survivor was chosen.
   - *New account: Amberly Manor.* Point at "considered and rejected: Amberly Manor, Colorado Springs, different state".
3. **(25s) Approve one.** Approve **Marietta** (the most interesting one). Click the green "view in CRM" link in the confirmation banner, then show the new account under Bellhaven. Open the old Marietta account: still under Cedar Trail, `chow_current_account` = the new ID.
4. **(20s) Reject one.** Reject an item you genuinely disagree with and type the reason in the comment box. Mention that rejected items are remembered and never re-proposed, and that Reopen exists for mistakes.
5. **(20s) Re-run safety.** Click "Run pipeline now". The banner shows "0 new … already decided (skipped)".
6. **(10s)** Briefly show the Audit log page.

After recording, finish approving the rest of the queue (the end state of the CRM is graded).
