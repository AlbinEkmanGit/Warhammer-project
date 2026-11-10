#!/usr/bin/env python3
"""
Load the JSON written by wahapedia_scraper.py into a SQLite database.

Usage
-----
    python load_json_to_db.py                                  # defaults below
    python load_json_to_db.py my_data.json --db warhammer.db
    python load_json_to_db.py my_data.json --replace           # overwrite earlier scrapes

Behaviour
---------
* The database and its tables are created from schema.sql if they don't exist.
* Every JSON file becomes one "scrape run". Loading a newer scrape adds a new run
  and keeps the old one, so you can compare versions over time.
* Loading the exact same scrape twice is detected and skipped.
* --replace first deletes all earlier runs of the same faction + edition.
* Everything is loaded in a single transaction: if anything fails, nothing is
  written.

Uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_JSON = "adeptus_mechanicus_datasheets.json"
DEFAULT_DB = "warhammer.db"


def as_text(value) -> str | None:
    """Keep stat values exactly as scraped; None stays NULL."""
    return None if value is None else str(value)


def load(conn: sqlite3.Connection, data: dict, replace: bool) -> int | None:
    """Insert one scrape. Returns the new run id, or None if it already existed."""
    faction = data.get("faction", "Unknown")
    edition = str(data.get("edition", ""))
    scraped_at = data.get("scraped_at", "")
    datasheets = data.get("datasheets", [])

    cur = conn.cursor()

    if replace:
        cur.execute("DELETE FROM scrape_runs WHERE faction = ? AND edition = ?", (faction, edition))
    else:
        cur.execute(
            "SELECT id FROM scrape_runs WHERE faction = ? AND edition = ? AND scraped_at = ?",
            (faction, edition, scraped_at),
        )
        if cur.fetchone():
            return None

    cur.execute(
        "INSERT INTO scrape_runs (faction, edition, source_url, scraped_at, datasheet_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (faction, edition, data.get("source", ""), scraped_at,
         data.get("datasheet_count", len(datasheets))),
    )
    run_id = cur.lastrowid

    # Faction-wide army rules (name -> id, case-insensitive, for linking below)
    rule_ids: dict[str, int] = {}
    for rule in data.get("faction_rules", []):
        cur.execute(
            "INSERT OR IGNORE INTO faction_rules (run_id, name, description) VALUES (?, ?, ?)",
            (run_id, rule["name"], rule.get("description", "")),
        )
        row = cur.execute(
            "SELECT id FROM faction_rules WHERE run_id = ? AND name = ?", (run_id, rule["name"])
        ).fetchone()
        rule_ids[rule["name"].lower()] = row[0]

    for ds in datasheets:
        cur.execute(
            "INSERT INTO datasheets (run_id, name, url, is_legends) VALUES (?, ?, ?, ?)",
            (run_id, ds["name"], ds["url"], int(bool(ds.get("legends")))),
        )
        ds_id = cur.lastrowid

        # Statlines
        for i, m in enumerate(ds.get("models", [])):
            cur.execute(
                "INSERT INTO models (datasheet_id, sort_order, name, base_size, movement, toughness, "
                "save, wounds, leadership, objective_control, invulnerable_save) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ds_id, i, m.get("name") or ds["name"], as_text(m.get("base_size")),
                 as_text(m.get("movement")), as_text(m.get("toughness")), as_text(m.get("save")),
                 as_text(m.get("wounds")), as_text(m.get("leadership")),
                 as_text(m.get("objective_control")), as_text(m.get("invulnerable_save"))),
            )

        # Weapons (ranged first, then melee) and their abilities
        order = 0
        for wtype in ("ranged", "melee"):
            for w in ds.get("weapons", {}).get(wtype, []):
                cur.execute(
                    "INSERT INTO weapons (datasheet_id, sort_order, weapon_type, name, weapon_range, "
                    "attacks, skill_type, skill, strength, ap, damage) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ds_id, order, w.get("type", wtype), w["name"], as_text(w.get("range")),
                     as_text(w.get("attacks")),
                     w.get("skill_type") or ("BS" if wtype == "ranged" else "WS"),
                     as_text(w.get("skill")), as_text(w.get("strength")), as_text(w.get("ap")),
                     as_text(w.get("damage"))),
                )
                weapon_id = cur.lastrowid
                order += 1
                cur.executemany(
                    "INSERT INTO weapon_abilities (weapon_id, sort_order, ability) VALUES (?, ?, ?)",
                    [(weapon_id, j, a) for j, a in enumerate(w.get("abilities", []))],
                )

        # Army rules: accept plain names (current scraper) or {name, description} (older output)
        for i, rule in enumerate(ds.get("army_rules", [])):
            name, desc = (rule, None) if isinstance(rule, str) else (rule["name"], rule.get("description"))
            cur.execute(
                "INSERT INTO datasheet_army_rules (datasheet_id, sort_order, name, faction_rule_id, description) "
                "VALUES (?, ?, ?, ?, ?)",
                (ds_id, i, name, rule_ids.get(name.lower()), desc or None),
            )

        cur.executemany(
            "INSERT INTO datasheet_core_abilities (datasheet_id, sort_order, name) VALUES (?, ?, ?)",
            [(ds_id, i, n) for i, n in enumerate(ds.get("core_abilities", []))],
        )

        for kind, key in (("datasheet", "abilities"), ("wargear", "wargear_abilities")):
            cur.executemany(
                "INSERT INTO datasheet_abilities (datasheet_id, kind, sort_order, name, description) "
                "VALUES (?, ?, ?, ?, ?)",
                [(ds_id, kind, i, a["name"], a.get("description", ""))
                 for i, a in enumerate(ds.get(key, []))],
            )

        for is_faction, key in ((0, "keywords"), (1, "faction_keywords")):
            cur.executemany(
                "INSERT INTO datasheet_keywords (datasheet_id, sort_order, keyword, is_faction) "
                "VALUES (?, ?, ?, ?)",
                [(ds_id, i, k, is_faction) for i, k in enumerate(ds.get(key, []))],
            )

    return run_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_file", nargs="?", default=DEFAULT_JSON)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--schema", default=str(HERE / "schema.sql"))
    ap.add_argument("--replace", action="store_true",
                    help="delete earlier scrapes of the same faction/edition first")
    args = ap.parse_args()

    json_path = Path(args.json_file)
    if not json_path.exists():
        print(f"JSON file not found: {json_path}", file=sys.stderr)
        return 1
    data = json.loads(json_path.read_text(encoding="utf-8"))

    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(Path(args.schema).read_text(encoding="utf-8"))
        with conn:                                   # one transaction: commit or roll back
            run_id = load(conn, data, args.replace)
    except Exception:
        conn.close()
        raise

    if run_id is None:
        print("This scrape is already in the database – nothing to do (use --replace to reload).")
    else:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("datasheets", "models", "weapons", "weapon_abilities",
                      "datasheet_abilities", "datasheet_keywords")
        }
        print(f"Loaded scrape run {run_id} into {args.db}")
        print("Totals in database:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
