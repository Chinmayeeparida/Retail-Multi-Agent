"""Checks that the allocation rules behave as the business described.   Run:  python test_allocation.py"""
from allocation import store_tier, need_quantity, allocate_new_item, allocate_repeat_item, split_equally, split_to_top_stores

# --- Repeat item: need = sold, shared by tier weights (top 21 : average 14 : low 9) when stock is short ---
assert [store_tier(r, 8)[0] for r in range(1, 9)] == ["top", "top", "average", "average", "average", "low", "low", "low"]
T, A, L = 21, 14, 9
assert need_quantity(sold=12, asked=10) == 10, "good demand: pack what the store asked"
assert need_quantity(sold=39, asked=26) == 26
assert need_quantity(sold=12, asked=30) == 12, "asked much more than sold, no note: only what it sold"
assert need_quantity(sold=12, asked=30, has_note=True) == 30, "a note explaining a high demand: pack what was asked"
assert need_quantity(sold=3, asked=10, store_qty=8) == 0, "the store already has plenty"
assert need_quantity(sold=3, asked=10, store_qty=8, has_note=True) == 10
assert need_quantity(sold=12, asked=10, store_qty=4) == 10, "store qty below 14 days of sales: still pack"
assert need_quantity(sold=0, asked=7) == 7, "no sales yet: pack the request"

# plenty of stock: everyone gets what they sold
assert allocate_repeat_item(500, [(1, 5, 10, T), (2, 39, 26, L)]) == {1: 5, 2: 26}

# short stock, two stores that both need 10 and the warehouse holds 10  ->  the business splits
assert allocate_repeat_item(10, [(1, 10, 10, T), (2, 10, 10, A)]) == {1: 6, 2: 4}     # top + average: 60 / 40
assert allocate_repeat_item(10, [(1, 10, 10, T), (2, 10, 10, T)]) == {1: 5, 2: 5}     # top + top: 50 / 50
assert allocate_repeat_item(10, [(1, 10, 10, T), (2, 10, 10, L)]) == {1: 7, 2: 3}     # top + low: 70 / 30
assert allocate_repeat_item(10, [(1, 10, 10, T), (2, 10, 10, T), (3, 10, 10, A)]) == {1: 4, 2: 4, 3: 2}   # two top + one average

# priority: with almost no stock, the single unit goes to the top store, then average, then low
assert allocate_repeat_item(1, [(1, 10, 10, T), (2, 10, 10, A), (3, 10, 10, L)]) == {1: 1, 2: 0, 3: 0}
assert allocate_repeat_item(2, [(1, 10, 10, A), (2, 10, 10, L), (3, 10, 10, T)]) == {3: 1, 1: 1, 2: 0}

# nobody gets more than it needs; the spare goes to the others
assert allocate_repeat_item(50, [(1, 10, 10, T), (2, 80, 80, A)]) == {1: 10, 2: 40}
got = allocate_repeat_item(37, [(1, 12, 12, T), (2, 30, 30, A), (3, 25, 25, L), (4, 9, 9, T)])
assert sum(got.values()) == 37 and all(got[r] <= n for r, n in {1: 12, 2: 30, 3: 25, 4: 9}.items()), got
assert allocate_repeat_item(0, [(1, 5, 10, T)]) == {1: 0}

# --- New item ---
stores = [("A", 7332), ("B", 3681), ("C", 6815), ("D", 3234), ("E", 6657), ("F", 11453), ("G", 2737), ("H", 3558)]
plenty = allocate_new_item(500, stores)
assert sum(plenty.values()) == 500 and set(plenty.values()) <= {62, 63}, plenty      # equal split (spare to biggest)
assert plenty["F"] == 63 and plenty["G"] == 62

scarce = allocate_new_item(35, stores)                                                # 3 packs of 10 + 5 spare
assert sum(scarce.values()) == 35 and set(scarce) == {"F", "A", "C"}, scarce          # only the 3 biggest stores
assert allocate_new_item(7, stores) == {"F": 7}                                       # less than one pack: biggest store
assert allocate_new_item(0, stores) == {}
# --- Choices a human can make when stock arrives ---
assert split_equally(500, stores) == {"F": 63, "A": 63, "C": 63, "E": 63, "B": 62, "D": 62, "G": 62, "H": 62}
assert split_equally(5, stores) == {"F": 1, "A": 1, "C": 1, "E": 1, "B": 1}          # fewer units than stores: biggest first
assert split_to_top_stores(100, stores, 3) == {"F": 34, "A": 33, "C": 33}
assert split_to_top_stores(100, stores, 0) == {}
print("All allocation checks passed.")
print("  500 new units (plenty):", dict(sorted(plenty.items())))
print("   35 new units (scarce):", dict(sorted(scarce.items(), key=lambda kv: -kv[1])))
print("  stock 10: two top + one average:", allocate_repeat_item(10, [(1, 10, 10, T), (2, 10, 10, T), (3, 10, 10, A)]))
