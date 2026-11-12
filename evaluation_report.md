# Evaluation report

Run on 2026-11-11 on a COPY of the simulated database (seed 42). No real data, no e-mail, no AI call.

**155 of 155 checks passed.**

| Rule | Area | What the rule says | Checks | Passed |
|---|---|---|---:|---:|
| R01 | Store request | A store cannot request more than the warehouse holds, and zero or negative quantities are refused. | 13 | 13 |
| R02 | Store request | A store can request an item only once per day. | 2 | 2 |
| R03 | Agent 1 | Agent 1 never packs more than the warehouse can give (stock already promised to other lists is counted). | 12 | 12 |
| R04 | Agent 1 | Agent 1 never packs more than the store asked, and never a negative number. | 6 | 6 |
| R05 | Agent 1 | When stock is short it is shared by store tier (top 21 : average 14 : low 9) and nobody gets more than it needs. | 3 | 3 |
| R06 | Agent 1 | A store that already has plenty on the shelf gets nothing, unless the Note explains the demand. | 3 | 3 |
| R07 | Agent 1 | A person's edit cannot be negative or take more stock than is left, and a changed quantity needs a reason. | 8 | 8 |
| R08 | Agent 1 | The AI only writes the note: a note with a number that is not in the facts, a link or a very long text is refused. | 7 | 7 |
| R09 | Agent 1 | A packing list is checked against the LIVE warehouse stock again just before it is saved. | 3 | 3 |
| R10 | Warehouse packing | Packed + damaged can never be more than the quantity to pack, and numbers cannot be negative. | 21 | 21 |
| R11 | Agent 2 | A trip leaves only on a store's scheduled weekday (Mon packing only, Fri urgent only, Sat/Sun none). | 11 | 11 |
| R12 | Agent 2 | A store with a problem (or nothing packed, or not enough warehouse stock) is blocked and cannot go on a trip. | 3 | 3 |
| R13 | Agent 2 | A trip is saved all or nothing; the warehouse stock goes down by exactly what was packed + damaged. | 7 | 7 |
| R14 | Store receiving | Damaged cannot be more than received, numbers cannot be negative, and a store cannot confirm the same trip twice. | 9 | 9 |
| R15 | Store receiving | A problem on a trip raises a Notice / Warning / Serious alert for the warehouse (10% and 30% of lines). | 6 | 6 |
| R16 | Agent 3 | The manager summary is refused if it contains a number that is not in the data (a standard text is used instead). | 4 | 4 |
| R17 | Agent 4 | A new product with bad details is refused: HSN not 4/6/8 digits, GST not a real slab, prices not cost <= selling <= MRP, no category or vendor, empty or duplicate name. | 18 | 18 |
| R18 | Agent 4 | The SKU and product ID are made by code, never by the AI: unique, in the catalogue format, and never reused (also when two people add products at the same time). | 8 | 8 |
| R19 | Agent 4 | A product is saved only after a named person approves; cancel or a failure saves nothing. | 5 | 5 |
| R20 | Agent 4 | A new product gets its price and an empty warehouse slot, and cannot be requested by stores until stock arrives. | 6 | 6 |

## Every check

| Result | Rule | Check |
|---|---|---|
| PASS | R03 | 3000 random allocations: total handed out never above the stock |
| PASS | R04 | 3000 random allocations: nobody gets more than it needs, never negative, whole numbers |
| PASS | R05 | 3000 random allocations: same input gives the same answer, and no stock is left unused while a store still needs it |
| PASS | R05 | short stock 10 -> top+average 6:4, top+low 7:3, top+top 5:5 |
| PASS | R06 | store already holds plenty (8 on shelf, sold 3) -> need 0; with an explaining Note -> packs |
| PASS | R04 | asked 30 but sold 12, no Note -> 12; with Note -> 30; asked 10 sold 12 -> 10 |
| PASS | R01 | request catalogue lists only items the warehouse can give |
| PASS | R01 | request MORE than the warehouse holds is refused |
| PASS | R01 | request of 0 is refused |
| PASS | R01 | negative request is refused |
| PASS | R01 | fractional request (2.5) is refused |
| PASS | R01 | empty staff name is refused |
| PASS | R01 | a 500-character Note is refused |
| PASS | R01 | a 200-character staff name is refused |
| PASS | R01 | none of the refused requests was saved |
| PASS | R01 | a valid request is saved, with a date and time |
| PASS | R02 | the same item for the same store on the same day is refused |
| PASS | R02 | a list with one duplicate saves NOTHING (all or nothing) |
| PASS | R04 | Agent 1 produced a decision for every pending request (248) |
| PASS | R04 | no proposal is negative, above what the store asked, or not a whole number |
| PASS | R04 | status and quantity agree (approved only when quantity > 0) |
| PASS | R03 | in all 211 products the total handed out is within the warehouse stock |
| PASS | R03 | an item with zero warehouse stock is marked 'out of stock' and nothing is packed |
| PASS | R03 | contested item: 6 units, 8 stores each ask 20 -> exactly 6 units are handed out |
| PASS | R05 | contested item: a top store is never given less than a low store |
| PASS | R06 | store with 500 on the shelf and no Note: nothing is packed ('not needed') |
| PASS | R06 | same store, but the Note explains a big sale: Agent 1 packs it |
| PASS | R03 | a request equal to the whole stock never gets more than the stock |
| PASS | R04 | every proposal has a reason in plain words |
| PASS | R07 | edit to a negative number is refused |
| PASS | R07 | edit that takes more than the stock that is left is refused |
| PASS | R07 | edit with a word instead of a number is refused |
| PASS | R07 | edit of a request that does not exist is refused |
| PASS | R07 | a refused edit changed nothing |
| PASS | R07 | changing a quantity without a reason in Remark is refused |
| PASS | R07 | a negative Packaging qty on the screen is refused |
| PASS | R07 | nothing was saved by the refused edits |
| PASS | R09 | stock disappeared after the proposal -> Done is refused with a clear message |
| PASS | R09 | ...and nothing was saved (requests still pending, no packing list) |
| PASS | R08 | a note that uses only numbers from the facts is accepted |
| PASS | R08 | a note with an invented number (45) is refused |
| PASS | R08 | a note with a link is refused |
| PASS | R08 | a very long note is refused |
| PASS | R08 | an empty note is refused |
| PASS | R16 | the manager summary guard (Agent 3) uses the same number rule |
| PASS | R08 | inside Agent 1: an AI note with an invented number is replaced by the standard note |
| PASS | R08 | inside Agent 1: a correct AI note is kept |
| PASS | R09 | Done accepted for all 8 stores with a live stock check |
| PASS | R03 | packing lists hold exactly the units Agent 1 approved |
| PASS | R03 | one open barcode request per store, labels = units to pack |
| PASS | R03 | no request is left 'pending' after Done |
| PASS | R03 | running Agent 1 again finds nothing to do (no double packing) |
| PASS | R03 | after labels are printed every store shows Labels = printed |
| PASS | R10 | packed more than planned is refused |
| PASS | R10 | packed + damaged more than planned is refused |
| PASS | R10 | negative packed number is refused |
| PASS | R10 | empty staff name is refused |
| PASS | R10 | a line with no Packed number is refused |
| PASS | R10 | nothing was saved by the refused packing |
| PASS | R10 | Store A: valid packing (with damage while packing) is saved |
| PASS | R10 | Store B: valid packing (with damage while packing) is saved |
| PASS | R10 | Store C: valid packing (with damage while packing) is saved |
| PASS | R10 | Store D: valid packing (with damage while packing) is saved |
| PASS | R10 | Store E: valid packing (with damage while packing) is saved |
| PASS | R10 | Store F: valid packing (with damage while packing) is saved |
| PASS | R10 | Store G: valid packing (with damage while packing) is saved |
| PASS | R10 | Store H: valid packing (with damage while packing) is saved |
| PASS | R10 | every store shows 'Packed and ready to dispatch' |
| PASS | R10 | packed + damaged never above planned, for all saved lines |
| PASS | R11 | Monday: no trip is created |
| PASS | R11 | Saturday: no trip is created |
| PASS | R11 | Sunday: no trip is created |
| PASS | R11 | a wrong date ('not-a-date') gives a clear message, no crash |
| PASS | R11 | a wrong date ('2026-13-45') gives a clear message, no crash |
| PASS | R11 | a wrong date ('') gives a clear message, no crash |
| PASS | R11 | Tuesday: a trip is created |
| PASS | R11 | Tuesday: only the scheduled stores (C, D, E, G) are on it |
| PASS | R11 | stores of other days (A, B, F, H) are still waiting, not on the trip |
| PASS | R13 | Tuesday: warehouse stock fell by exactly packed + damaged, item by item |
| PASS | R13 | Tuesday: units in boxes = units in box lines = units packed |
| PASS | R13 | no warehouse stock below zero anywhere |
| PASS | R13 | running Tuesday again does not create a second trip or move stock twice |
| PASS | R12 | Wednesday: Store B (nothing packed) is blocked and not on the trip |
| PASS | R12 | Wednesday: Store F (warehouse no longer holds an item) is blocked and not on the trip |
| PASS | R11 | Wednesday: Store A, which is scheduled and fine, goes |
| PASS | R13 | Wednesday: stock fell by exactly what Store A took |
| PASS | R12 | blocked stores keep their lists (not lost, not shipped) |
| PASS | R13 | a trip that fails half-way changes NOTHING (all or nothing) |
| PASS | R11 | Thursday: Store H goes, after the failed attempt was cleared |
| PASS | R14 | negative received number is refused |
| PASS | R14 | damaged more than received is refused |
| PASS | R14 | a line that is not checked yet blocks Done |
| PASS | R14 | on the screen, damaged more than received blocks Done |
| PASS | R14 | empty checker name blocks Done |
| PASS | R15 | alert levels: 0 of 10 none, 1 of 20 NOTICE, 1 of 10 WARNING, 3 of 10 SERIOUS |
| PASS | R15 | Store C: many problem lines -> an alert is saved for the warehouse |
| PASS | R15 | Store C: the alert level matches the share of problem lines |
| PASS | R15 | Store D: a clean delivery raises no alert |
| PASS | R15 | Store E: a clean delivery raises no alert |
| PASS | R15 | Store G: a clean delivery raises no alert |
| PASS | R14 | the same store cannot confirm the same trip twice |
| PASS | R14 | when every store has checked, the trip becomes 'received' |
| PASS | R16 | Agent 3 makes the manager report for a finished trip (standard text, AI off) |
| PASS | R16 | the report numbers add up: totals in the text come from the database |
| PASS | R16 | if the data does not add up (1 unit off), Agent 3 stops and lists the problem instead of reporting |
| PASS | R03 | next week: stock that already left on a trip (1 units) is NOT taken off the warehouse quantity again |
| PASS | R03 | next week: the agent still never offers more than the warehouse holds |
| PASS | R17 | refused: HSN with 2 digits |
| PASS | R17 | refused: HSN with letters |
| PASS | R17 | refused: GST 7% |
| PASS | R17 | refused: selling below cost |
| PASS | R17 | refused: MRP below selling |
| PASS | R17 | refused: zero cost |
| PASS | R17 | refused: negative price |
| PASS | R17 | refused: no category |
| PASS | R17 | refused: category without SKU prefix |
| PASS | R17 | refused: empty name |
| PASS | R17 | refused: name of only numbers |
| PASS | R17 | refused: unknown vendor |
| PASS | R17 | refused: no vendor at all |
| PASS | R17 | refused: vendor chosen AND typed |
| PASS | R17 | refused: variant of a SKU that does not exist |
| PASS | R17 | refused: a name that already exists |
| PASS | R17 | nothing was saved by the refused products |
| PASS | R19 | cancel saves nothing |
| PASS | R19 | approval without a name saves nothing |
| PASS | R19 | approved by a named person: the product is saved |
| PASS | R18 | SKU has the catalogue format: category prefix + 6 digits + letters |
| PASS | R18 | the SKU matches the product number that was saved |
| PASS | R18 | the product ID is new and the SKU is unique |
| PASS | R20 | price saved with the product (cost, selling, MRP) |
| PASS | R20 | an empty warehouse slot (0 units, no rack) was created |
| PASS | R20 | stores cannot request it yet (no stock): it is not in the request list |
| PASS | R19 | the approval is written in the agent log |
| PASS | R17 | adding the same product again is refused |
| PASS | R18 | a variant reuses the product number but gets its own, different SKU |
| PASS | R18 | a new vendor is created with the product; a non-India vendor gets no 'IN' in the SKU |
| PASS | R18 | product numbers keep going up (never reused) |
| PASS | R18 | two proposals made at the same time: both save, with different product numbers |
| PASS | R18 | all SKUs in the catalogue are still unique |
| PASS | R19 | a proposal that became a duplicate before approval is refused and saves nothing |
| PASS | R20 | Agent 1 still runs normally with the new products in the catalogue |
| PASS | R01 | CSV upload: exactly the one good row passes |
| PASS | R01 | CSV upload: more than the warehouse, a repeated SKU, an unknown SKU, text, zero, negative and an empty SKU are all skipped |
| PASS | R01 | CSV upload: a file without the SKU / Request qty columns is explained |
| PASS | R20 | Listed on shows New for a new product that has not gone to a store |
| PASS | R20 | Listed on shows a date for a catalogue product |
| PASS | R10 | packing progress counts the lines typed so far (2 of N) |
| PASS | R10 | the pipeline shows the progress as 2/N |
| PASS | R10 | the tile shows the progress per store |
| PASS | R10 | a typed number above 'to pack' is not kept |
| PASS | R10 | typed numbers are not final: the lines are still 'to pack' until Done |
| PASS | R13 | Packing History: total packed equals the database (last 12 months) |
| PASS | R14 | Trip View (Store C): received total equals the database |
| PASS | R14 | Product View (Store C): one row per trip, totals add up |
