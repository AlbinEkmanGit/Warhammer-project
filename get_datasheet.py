#!/usr/bin/env python3
"""
Fetch one complete datasheet from the SQLite database and print it as JSON.

Usage
-----
    python get_datasheet.py "Ironstrider Ballistarii"
    python get_datasheet.py ironstrider                 # partial, case-insensitive
    python get_datasheet.py "Kastelan Robots" --db warhammer.db --run 2
    python get_datasheet.py --list                      # list all datasheet names

By default the most recent scrape run is used (--run picks a specific one).
The output has the same shape as the scraper's JSON, so it can be used the same way.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys


def latest_run(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM scrape_runs ORDER BY scraped_at DESC, id DESC LIMIT 1").fetchone()
    if not row:
        sys.exit("The database contains no scrape runs. Load a JSON file first.")
    return row["id"]


def find_datasheets(conn: sqlite3.Connection, run_id: int, name: str) -> list[sqlite3.Row]:
    exact = conn.execute(
        "SELECT * FROM datasheets WHERE run_id = ? AND name = ? COLLATE NOCASE", (run_id, name)
    ).fetchall()
    if exact:
        return exact
    return conn.execute(
        "SELECT * FROM datasheets WHERE run_id = ? AND name LIKE ? ORDER BY name",
        (run_id, f"%{name}%"),
    ).fetchall()


def build_datasheet(conn: sqlite3.Connection, ds: sqlite3.Row) -> dict:
    ds_id = ds["id"]

    models = [
        {k: v for k, v in dict(r).items() if v is not None}
        for r in conn.execute(
            "SELECT name, base_size, movement, toughness, save, wounds, leadership, "
            "objective_control, invulnerable_save FROM models WHERE datasheet_id = ? ORDER BY sort_order",
            (ds_id,),
        )
    ]

    weapons: dict[str, list] = {"ranged": [], "melee": []}
    for w in conn.execute("SELECT * FROM weapons WHERE datasheet_id = ? ORDER BY sort_order", (ds_id,)):
        abilities = [
            a["ability"]
            for a in conn.execute(
                "SELECT ability FROM weapon_abilities WHERE weapon_id = ? ORDER BY sort_order", (w["id"],)
            )
        ]
        weapons[w["weapon_type"]].append({
            "name": w["name"], "type": w["weapon_type"], "range": w["weapon_range"],
            "attacks": w["attacks"], "skill_type": w["skill_type"], "skill": w["skill"],
            "strength": w["strength"], "ap": w["ap"], "damage": w["damage"],
            "abilities": abilities,
        })

    army_rules = [
        {"name": r["name"], "description": r["description"] or r["rule_text"] or ""}
        for r in conn.execute(
            "SELECT ar.name, ar.description, fr.description AS rule_text "
            "FROM datasheet_army_rules ar LEFT JOIN faction_rules fr ON fr.id = ar.faction_rule_id "
            "WHERE ar.datasheet_id = ? ORDER BY ar.sort_order",
            (ds_id,),
        )
    ]

    def names(table: str) -> list[str]:
        return [r[0] for r in conn.execute(
            f"SELECT name FROM {table} WHERE datasheet_id = ? ORDER BY sort_order", (ds_id,))]

    def abilities(kind: str) -> list[dict]:
        return [dict(r) for r in conn.execute(
            "SELECT name, description FROM datasheet_abilities "
            "WHERE datasheet_id = ? AND kind = ? ORDER BY sort_order", (ds_id, kind))]

    def keywords(is_faction: int) -> list[str]:
        return [r[0] for r in conn.execute(
            "SELECT keyword FROM datasheet_keywords WHERE datasheet_id = ? AND is_faction = ? "
            "ORDER BY sort_order", (ds_id, is_faction))]

    return {
        "name": ds["name"],
        "url": ds["url"],
        "legends": bool(ds["is_legends"]),
        "models": models,
        "weapons": weapons,
        "army_rules": army_rules,
        "core_abilities": names("datasheet_core_abilities"),
        "abilities": abilities("datasheet"),
        "wargear_abilities": abilities("wargear"),
        "keywords": keywords(0),
        "faction_keywords": keywords(1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="datasheet name (exact, or part of it)")
    ap.add_argument("--db", default="warhammer.db")
    ap.add_argument("--run", type=int, help="scrape run id (default: latest)")
    ap.add_argument("--list", action="store_true", help="list all datasheet names and exit")
    ap.add_argument("--faction", help="Filter by faction name")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    if args.run:
        run_id = args.run
    elif args.faction:
        row = conn.execute(
            """
            SELECT id
            FROM scrape_runs
            WHERE faction = ? COLLATE NOCASE
            ORDER BY scraped_at DESC, id DESC
            LIMIT 1
            """,
            (args.faction,)
        ).fetchone()

        if not row:
            sys.exit(f"No scrape found for faction: {args.faction}")

        run_id = row["id"]
    else:
        run_id = latest_run(conn)
    
    if args.list:
        query = """
            SELECT
                sr.faction,
                sr.id AS run_id,
                d.name
            FROM datasheets d
            JOIN scrape_runs sr ON sr.id = d.run_id
            ORDER BY sr.faction, d.name
        """

        for r in conn.execute(query):
            print(f"[{r['faction']}] {r['name']}")

        conn.close()
        return 0

    run_id = args.run or latest_run(conn)
    if not args.name:
        ap.error("give a datasheet name, or use --list")

    matches = find_datasheets(conn, run_id, args.name)
    if not matches:
        print(f"No datasheet matching '{args.name}' in run {run_id}. Try --list.", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"'{args.name}' matches {len(matches)} datasheets, be more specific:", file=sys.stderr)
        for m in matches:
            print(f"  {m['name']}", file=sys.stderr)
        return 1

    print(json.dumps(build_datasheet(conn, matches[0]), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
