"""Makes a fully INVENTED demo data set, so anyone can run the project without the real company data.

It writes the same two CSV files the real data comes in (a stock report and a one-month sales report) into data/raw/.
Every product, vendor, outlet, price and sale is made up with a fixed random seed (42), so every run gives the same files.

Run from the project folder, then build the database:
    python make_demo_data.py
    python build_database.py

It never overwrites existing files: if data/raw already holds files, it stops and tells you.
"""
import csv
import datetime
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
STOCK_FILE = RAW / "2025-08-28-15_11_58-Stock-Report.csv"
SALES_FILE = RAW / "Aug2025_AllStores_ProductSalesReport.csv.csv"
SEED = 42

# category -> (SKU prefix, sub categories, words used to build product names, price range, GST %)
CATEGORIES = {
    "Kitchen":          ("KIT", ["Utensils", "Storage", "Bottles"], ["Steel", "Glass", "Silicone", "Ceramic", "Bamboo"], ["Spatula", "Bottle", "Container", "Tumbler", "Cutter", "Rack", "Bowl"], (80, 600), 18),
    "Home and Living":  ("HNL", ["Decor", "Fragrance", "Candles"], ["Scented", "Handmade", "Vintage", "Mini", "Large"], ["Candle", "Incense Sticks", "Photo Frame", "Vase", "Wall Clock", "Diffuser"], (99, 900), 12),
    "Kids":             ("KID", ["Toys", "School", "Games"], ["Soft", "Wooden", "Colourful", "Magnetic", "Musical"], ["Puzzle", "Lunch Box", "Teddy", "Building Blocks", "Water Bottle", "Colouring Set"], (60, 500), 12),
    "Personal Care":    ("PER", ["Skin", "Hair", "Bath"], ["Herbal", "Gentle", "Fresh", "Aloe", "Rose"], ["Face Wash", "Body Lotion", "Soap", "Hair Oil", "Sanitizer", "Lip Balm"], (50, 450), 18),
    "Stationary":       ("STN", ["Writing", "Paper", "Office"], ["Gel", "Pastel", "Neon", "Pocket", "Jumbo"], ["Pen Set", "Notebook", "Sticky Notes", "Highlighter", "Sketchbook", "Planner"], (30, 350), 12),
    "Fashion":          ("FAC", ["Bags", "Jewellery", "Accessories"], ["Classic", "Trendy", "Pearl", "Canvas", "Printed"], ["Hand Bag", "Earrings", "Wallet", "Sling Bag", "Scarf", "Bracelet"], (149, 1499), 12),
    "Hair Accessories": ("HA", ["Clips", "Bands"], ["Floral", "Satin", "Glitter", "Pastel", "Boho"], ["Scrunchie", "Hair Clip", "Hair Band", "Claw Clip", "Bobby Pins"], (29, 249), 12),
    "Bed and Bath":     ("BNB", ["Towels", "Bedding"], ["Soft", "Cotton", "Quick Dry", "Premium"], ["Towel", "Pillow Cover", "Bath Mat", "Shower Cap", "Bedsheet"], (99, 999), 12),
    "Travel":           ("TAC", ["Organisers", "Bags"], ["Compact", "Foldable", "Waterproof", "Padded"], ["Passport Holder", "Pouch", "Luggage Tag", "Neck Pillow", "Organiser"], (79, 699), 12),
    "Packaging":        ("PCK", ["Gift"], ["Plain", "Printed", "Glossy"], ["Gift Bag", "Gift Box", "Wrapping Paper", "Ribbon"], (10, 99), 18),
    "Cosmetics":        ("CS", ["Face", "Lips", "Eyes"], ["Matte", "Glossy", "Natural", "Long Lasting"], ["Lipstick", "Kajal", "Compact", "Nail Polish", "Eyeliner"], (79, 599), 18),
}
COLOURS = ["Red", "Blue", "Green", "Black", "White", "Pink", "Yellow", "Purple", None, None, None, None]
VENDORS = ["Sunrise Traders", "Bluebird Supplies", "Maple Crafts", "Orchid Wholesale", "Evergreen Imports", "Silverline Goods",
           "Lotus Home Products", "Urban Nest Co", "Greenleaf Exports", "Pioneer Stationers", "Rainbow Kids Co", "Coastal Gift House"]
OUTLETS = ["Demo Outlet - Alpha", "Demo Outlet - Bravo", "Demo Outlet - Charlie", "Demo Outlet - Delta",
           "Demo Outlet - Echo", "Demo Outlet - Foxtrot", "Demo Outlet - Golf", "Demo Outlet - Hotel"]
OUTLET_SIZE = [1.0, 0.8, 1.3, 0.6, 1.2, 1.7, 0.5, 0.7]       # a bigger number = the outlet sells more
N_PRODUCTS = 420
SALES_DAYS = [datetime.date(2025, 8, 2) + datetime.timedelta(days=i) for i in range(30)]      # 2 to 31 Aug 2025


def initials(name: str) -> str:
    letters = "".join(w[0] for w in name.split() if w.isalpha())[:4].upper()
    return letters if len(letters) >= 2 else name[:3].upper()


def make_products(rng: random.Random) -> list:
    products, used, number = [], set(), 270
    categories = list(CATEGORIES)
    while len(products) < N_PRODUCTS:
        category = rng.choice(categories)
        prefix, subs, adjectives, nouns, (low, high), gst = CATEGORIES[category]
        base_name = f"{rng.choice(adjectives)} {rng.choice(nouns)}"
        number += 1
        for colour in rng.sample(COLOURS, rng.choice([1, 1, 2, 3])):           # variants share the product number
            name = base_name + (f" - {colour}" if colour else "")
            sku = f"{prefix}{number:06d}{'IN' if rng.random() < 0.3 else ''}{initials(base_name)}{colour[0] if colour else ''}"
            if sku in used or name in {p['name'] for p in products}:
                continue
            used.add(sku)
            mrp = round(rng.uniform(low, high) / 5) * 5
            products.append({"name": name, "sku": sku, "category": category, "sub": rng.choice(subs), "gst": gst,
                             "hsn": rng.choice(["3924", "392410", "48201010", "96081000", "33049990", "62171090", "63049239"]),
                             "mrp": mrp, "cost": round(mrp * rng.uniform(0.35, 0.6)),
                             "vendor": rng.choice(VENDORS) if rng.random() > 0.04 else "",     # a few products have no vendor
                             "popularity": rng.paretovariate(1.4),                               # a few products sell a lot
                             "stock": 0 if rng.random() < 0.25 else int(rng.choice([4, 8, 12, 20, 36, 60, 90, 150, 300]) * rng.uniform(0.7, 1.4))})
    return products[:N_PRODUCTS]


def main() -> None:
    if STOCK_FILE.exists() or SALES_FILE.exists():
        raise SystemExit("data/raw already has files, so nothing was written (your own data is safe).\n"
                         "To make the demo data anyway, move or rename the files in data/raw first.")
    RAW.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    products = make_products(rng)

    with open(STOCK_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Product Name", "SKU", "HSN", "Brand", "Category", "Sub Category", "MRP", "MOP", "Cost Price", "GST",
                    "WH Location", "Available Qty", "Total Stock Value"])
        for p in products[: int(N_PRODUCTS * 0.97)]:                           # a few sold items are missing from the stock report
            w.writerow([p["name"], p["sku"], p["hsn"], "DemoBrand", p["category"], p["sub"], p["mrp"], p["mrp"], p["cost"], p["gst"],
                        "Central DC", p["stock"], p["stock"] * p["cost"]])

    rows, invoice = [], 1000
    for day in SALES_DAYS:
        weekend = 1.5 if day.weekday() >= 5 else 1.0
        for outlet, size in zip(OUTLETS, OUTLET_SIZE):
            for _ in range(int(rng.uniform(8, 16) * size * weekend)):
                p = rng.choices(products, weights=[x["popularity"] for x in products])[0]
                qty = rng.choice([1, 1, 1, 2, 2, 3, 5]) if rng.random() > 0.01 else -1       # a few returns
                invoice += 1
                price = p["mrp"] * rng.choice([1, 1, 1, 0.9, 0.8])
                ex_gst = round(price * qty / (1 + p["gst"] / 100), 2)
                rows.append([day.strftime("%d/%m/%y"), outlet, f"INV{invoice}", p["name"], p["sku"], p["hsn"], p["vendor"], "DemoBrand",
                             p["category"], p["sub"], p["gst"], p["cost"], p["mrp"], p["mrp"] * qty, round(p["mrp"] * qty - price * qty, 2),
                             0, 0, 0, qty, ex_gst, round(price * qty - ex_gst, 2), round(price * qty, 2)])
    with open(SALES_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Sales Date", "POS Outlet", "Invoice No.", "Product Name", "SKU", "HSN", "Vendor Name", "Brand Name", "Category Name",
                    "Sub-Category Name", "GST %", "Vendor Price", "MRP", "Total Price b/Offer", "Discount (Rs.)", "Promotional Offer",
                    "Coupon Disc", "Product Offer", "Quantity", "Total Amt Ex GST (Rs.)", "GST Total Amt (Rs.)", "Net Total Amt (Rs.)"])
        w.writerows(rows)
    print(f"Demo data written to data/raw/: {int(N_PRODUCTS * 0.97)} products in the stock report, {len(rows)} sales rows, "
          f"{len(OUTLETS)} outlets, {len(VENDORS)} vendors. All invented.\nNext:  python build_database.py")


if __name__ == "__main__":
    main()
