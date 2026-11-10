"""The allocation rules: how warehouse stock is shared between stores.

Plain Python, no AI, no database. That makes the rules easy to read and to test.

TWO RULES (from the business):
  1. REPEAT item (stores already sell it), per store request:
       * What a store NEEDS is what it SOLD (even if it asked for less or more).
         If it has no sales of the item yet, we fall back to what it asked.
       * If the warehouse has enough for every store, every store gets what it needs.
       * If the warehouse is SHORT, the stock is shared by store tier, with these weights:
             top 21  :  average 14  :  low 9
         which gives exactly the business splits:
             top + average  -> 60 / 40       top + top  -> 50 / 50       top + low  -> 70 / 30
         and works for any number of stores (e.g. two top + one average share 10 units as 4 / 4 / 2).
         Nobody gets more than it needs; spare units go to the next stores. Odd units go top first, then average, then low.
  2. NEW item (no sales history yet), when stock arrives from a vendor:
        enough stock  -> split EQUALLY between all stores
        too little    -> give a minimum pack to the BIGGEST stores only, in order of sales, until it runs out.

The numbers you may want to change are just below.
"""

# Store tiers by performance (rank by total units sold).
# (name, last rank that belongs to the tier as a share of all stores, weight when stock is short)
TIERS = [("top", 0.25, 21), ("average", 0.625, 14), ("low", 1.0, 9)]
STORE_PLENTY_DAYS = 14     # default: a store holding this many days of its sales on the shelf does not need more
GOOD_SALES_UNITS = 5      # top and average stores selling at least this many units in 30 days are always sent stock
PLENTY_DAYS_BY_TIER = {"top": 28, "average": 14, "low": 10}   # top stores sell fast and must not run out, so they keep a bigger buffer
MIN_UNITS_PER_STORE = 10   # new items: the smallest pack worth sending to a store ("good enough" test)


def store_tier(rank: int, n_stores: int) -> tuple:
    """rank 1 = best-selling store.  Returns (tier name, weight).  With 8 stores: ranks 1-2 top, 3-5 average, 6-8 low."""
    for name, upto, weight in TIERS:
        if rank <= max(1, int(n_stores * upto + 1e-9)):
            return name, weight
    return TIERS[-1][0], TIERS[-1][2]


def need_quantity(sold: int, asked: int, store_qty: int = 0, has_note: bool = False, plenty_days: int | None = STORE_PLENTY_DAYS) -> int:
    """What Agent 1 packs for one repeat item, before the warehouse stock is shared.
      * No sales yet (new item)                                    -> what the store asked.
      * Store already has plenty (more than the buffer days of sales (top 28, average 14, low 10), and at least what we would pack) -> 0 (unless the Note explains a big demand).
      * Store asked for no more than it sold in 30 days            -> what it asked (good demand).
      * Store asked for MORE than it sold                          -> only what it sold, unless the Note explains a high demand."""
    if sold <= 0:
        return max(asked, 0)
    if plenty_days is None:                               # a good seller in a top/average store: never 'nothing to pack'
        return max(asked, 0) if asked <= sold or has_note else sold
    cover = -(-sold * plenty_days // 30)                  # units needed for that many days of sales, rounded UP
    base = asked if asked <= sold else sold                 # what we would pack otherwise
    if store_qty > cover and store_qty >= base and not has_note:   # plenty = more than the buffer days of sales (top 28, average 14, low 10), and at least what we would pack
        return 0
    if asked <= sold or has_note:
        return max(asked, 0)
    return sold


def allocate_repeat_item(stock: int, requests: list[tuple]) -> dict:
    """requests = [(request_id, sold, asked, weight[, store_qty, has_note, plenty_days]), ...]  ->  {request_id: units granted}"""
    need = {r[0]: need_quantity(r[1], r[2], r[4] if len(r) > 4 else 0, bool(r[5]) if len(r) > 5 else False, r[6] if len(r) > 6 else STORE_PLENTY_DAYS) for r in requests}
    weight = {r[0]: r[3] for r in requests}
    sold_of = {r[0]: r[1] for r in requests}
    stock = max(stock, 0)
    granted = {rid: 0 for rid in need}
    active = [rid for rid in need if need[rid] > 0]
    remaining = stock

    while remaining > 0 and active:
        total_w = sum(weight[r] for r in active)
        share = {r: remaining * weight[r] / total_w for r in active}
        capped = [r for r in active if need[r] - granted[r] <= share[r]]
        if capped:                                    # these stores are fully served; the rest share what is left
            for r in capped:
                give = need[r] - granted[r]
                granted[r] += give
                remaining -= give
                active.remove(r)
            continue
        whole = {r: int(share[r]) for r in active}    # nobody is fully served: split by weight, whole units first
        for r in active:
            granted[r] += whole[r]
        leftover = remaining - sum(whole.values())
        order = sorted(active, key=lambda r: (-(share[r] - whole[r]), -weight[r], -sold_of[r], r))
        for r in order[:leftover]:                    # odd units: biggest remainder first; ties go top > average > low
            granted[r] += 1
        break
    return granted


def split_equally(units: int, stores_by_sales: list[tuple]) -> dict:
    """Same quantity for every store; any spare units go one by one to the best-selling stores."""
    ranked = [store for store, _ in sorted(stores_by_sales, key=lambda x: (-x[1], str(x[0])))]
    if not ranked or units <= 0:
        return {}
    base, spare = divmod(units, len(ranked))
    result = {store: base + (1 if i < spare else 0) for i, store in enumerate(ranked)}
    return {store: qty for store, qty in result.items() if qty > 0}


def split_to_top_stores(units: int, stores_by_sales: list[tuple], how_many: int) -> dict:
    """Only the top-selling `how_many` stores get the stock, split equally between them."""
    ranked = sorted(stores_by_sales, key=lambda x: (-x[1], str(x[0])))
    return split_equally(units, ranked[:max(how_many, 0)])


def allocate_new_item(units: int, stores_by_sales: list[tuple], min_units: int = MIN_UNITS_PER_STORE) -> dict:
    """stores_by_sales = [(store, total_units_sold), ...]  ->  {store: units granted}  (stores with 0 are left out)"""
    ranked = [store for store, _ in sorted(stores_by_sales, key=lambda x: (-x[1], str(x[0])))]
    n = len(ranked)
    if n == 0 or units <= 0:
        return {}

    if units >= n * min_units:                                  # plenty: equal split, spare units to the biggest stores
        base, spare = divmod(units, n)
        return {store: base + (1 if i < spare else 0) for i, store in enumerate(ranked)}

    served = max(units // min_units, 1)                         # scarce: only the biggest stores, a minimum pack each
    served = min(served, n)
    result = {store: min_units for store in ranked[:served]}
    leftover = units - min_units * served
    if units < min_units:
        return {ranked[0]: units}                               # less than one pack: it all goes to the biggest store
    i = 0
    while leftover > 0:                                         # spare units, one at a time, biggest stores first
        result[ranked[i % served]] += 1
        leftover -= 1
        i += 1
    return result
