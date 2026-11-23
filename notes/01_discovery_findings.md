# Discovery findings (exploration pass, before building)

Source snapshots saved in `raw/` (website HTML, accounts.json, contacts.json, openapi.json).

## Inputs
- **Website:** 35 communities. Directory (`/communities`, 3 pages) lists 34; **Bellhaven Meadows of Findlay** is only linked from the home-page banner ("new this year"). Scraper must crawl home + directory, not just directory.
- **About page:** Bellhaven acquired **Harborview Care Group** (2025) and **select Cedar Trail** communities (2026). Explains most wrong-parent cases.
- **CRM:** 121 accounts, 67 contacts. API has no request-body schema for POST/PATCH; fields inferred from records. Parent IDs: Bellhaven `0015QAPLGS3FVYEEEM`, Harborview `001FJZYHR7MLFMNPLL`, Cedar Trail `001FWSQ30SFW6S7604`, Millstone `001YRHHXQ5HJ0TCL2U`.
- Website care offerings map: "Short-Term Rehabilitation & Nursing" → Skilled Nursing, "Memory Support" → Memory Care, "Assisted Living" → Assisted Living. Two sites list two offerings (Findlay, Erie); CRM `care_type` is single-valued.
- Phone numbers disagree between site and CRM on most records, including clear matches. Phone is weak evidence only, and not a field we were asked to sync.

## Case catalogue (preliminary, from rough address/phone cross-reference)

**Confident match, no change:** Altoona, Defiance, Goshen, Kalamazoo, Maplewood, Marion, Meadville, New Carlisle, Saline, Wooster, Akron, Toledo (address formatting differs only: Rd/Road, St/Street, NW/Northwest).

**Match, name outdated:** Sycamore Ridge ("of"→"at"), Ashland ("Health Care Center"→"Healthcare Centre"), Chagrin Falls (was "Riverbend Manor Care Center"), Chesterton (was "Chesterton Senior Commons"), Grove City ("Rehab and"→"Rehabilitation &"), Willow Creek/Portage (was "Sunny Acres Retirement Home"), Dayton ("Arbors at Bellhaven Dayton").

**Match, address wrong:** Ashtabula (CRM has PO Box), Portsmouth (CRM zip 45626 vs site 45662, transposed digits).

**Match, wrong/missing parent → SOP check:**
| Facility | CRM parent | Revenue | AR | SOP outcome |
|---|---|---|---|---|
| Crossings of Lima | Harborview | 47,000 | 0 | Re-parent directly |
| Meadows of Findlay | none | 22,000 | 0 | Set parent directly |
| Cedar Trail of Zanesville | Cedar Trail | 0 | 0 | Re-parent + rename |
| Bellhaven of Marietta | Cedar Trail | 51,250 | 3,800 | **CHOW**: create new under Bellhaven, link old |
| Bellhaven of Tiffin | Cedar Trail | 84,000 | 12,400 | **CHOW**: create new under Bellhaven, link old |

**Duplicates (same address, multiple accounts):**
- Monroe: Bellhaven Gardens of Monroe (Bellhaven, 1 contact) + Cedar Trail of Monroe + Monroe Gardens Care Center (Harborview)
- Kettering: Kettering Care Centre (Harborview) + Kettering Nursing & Rehab (no parent) + Kettering Senior Campus (Cedar Trail). None has revenue or contacts, and none is named "Bellhaven of Kettering".
- Port Clinton: Bellhaven of Port Clinton + Harborview Nursing & Rehab of Port Clinton
- Erie: Bellhaven Shores of Erie + Harborview Shores of Erie
- Owosso: two "Bellhaven of Owosso" under Bellhaven (1 contact each)

**On website, not in CRM → create:** Amberly Manor (Hudson OH; CRM has an unrelated "Amberly Manor" in Colorado Springs, a name-collision trap), Bellhaven of Batavia, Bellhaven of Carlisle PA (not the same as New Carlisle OH), Bellhaven at Union Square (CRM "Union Square Senior Living" is at a different street in New Albany under Juniper Point, so probably a different facility).

**Under Bellhaven in CRM, not on website:**
- Bellhaven Care Center of Alliance (rev 0)
- Bellhaven of Coldwater (rev 0)
- Bellhaven of Sandusky (rev 130,000, AR 5,200). **Millstone Care of Sandusky** exists at the same address under Millstone, so it looks like it was sold.

**Not Bellhaven, leave alone:** other "Aspen/Willowbrook/Heritage" style orphans, unless they share an address with a Bellhaven site.
