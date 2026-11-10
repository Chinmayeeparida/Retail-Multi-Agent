"""Rebuild the whole database from scratch with one command:   python build_database.py

It deletes data/retail.db (the raw CSVs in data/raw are never touched), then runs every step in order
and stops at the first error. Add --yes to skip the confirmation question.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "data" / "retail.db"

STEPS = [
    "create_db.py",         # Step 2:  empty tables from schema.sql
    "load_catalog.py",      # Step 3:  clean the CSVs, load products / vendors / prices / stock
    "load_stores_sales.py", # Step 4:  stores and August sales
    "simulate_data.py",     # Step 5a: simulated vendor details and rack names
    "simulate_restock.py",  # Step 5b: simulated restock requests
    "simulate_store_stock.py",  # Step 5c: simulated stock on each store's shelves
    "seed_schedule.py",     # Step 11: weekly delivery schedule (editable)
    "check_db.py",          # final health check
]


def main() -> None:
    if DB_FILE.exists():
        if "--yes" not in sys.argv and input(f"Delete {DB_FILE.name} and rebuild? Type yes: ").strip().lower() != "yes":
            raise SystemExit("Cancelled. Nothing was changed.")
        DB_FILE.unlink()
        (ROOT / "data" / "pipeline_checkpoints.db").unlink(missing_ok=True)   # saved pipeline progress belongs to the old data

    for step in STEPS:
        print(f"\n=== {step} " + "=" * (50 - len(step)))
        # sys.executable = the same Python (the .venv one) that is running this file
        if subprocess.run([sys.executable, str(ROOT / step)], cwd=ROOT).returncode != 0:
            raise SystemExit(f"\nStopped: {step} failed. Fix the error above, then run this again.")

    print("\nDatabase rebuilt successfully.")


if __name__ == "__main__":
    main()
