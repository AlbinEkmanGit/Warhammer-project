//! Datasheet viewer: shows every datasheet in the SQLite database as a collapsible bar.
//!
//! Configuration (environment variables, all optional):
//!   DATABASE_PATH  path to the SQLite file       (default: warhammer.db)
//!   BIND           address to listen on           (default: 127.0.0.1:3000)
//!
//! Open http://127.0.0.1:3000/ in a browser. Add ?run=<id> to view an older scrape.

use std::{env, path::Path, sync::Arc};

use axum::{
    extract::{Query, State},
    http::StatusCode,
    routing::get,
    Router,
};
use maud::{html, Markup, PreEscaped, DOCTYPE};
use rusqlite::{Connection, OpenFlags, OptionalExtension};
use serde::Deserialize;

const CSS: &str = include_str!("../assets/style.css");
const JS: &str = include_str!("../assets/app.js");

// ---------------------------------------------------------------------------
// Data model (mirrors the tables in schema.sql)
// ---------------------------------------------------------------------------

struct RunInfo {
    id: i64,
    faction: String,
    edition: String,
    scraped_at: String,
    source_url: String,
}

struct Model {
    name: String,
    base_size: Option<String>,
    movement: Option<String>,
    toughness: Option<String>,
    save: Option<String>,
    wounds: Option<String>,
    leadership: Option<String>,
    objective_control: Option<String>,
    invulnerable_save: Option<String>,
}

struct Weapon {
    name: String,
    weapon_type: String, // "ranged" | "melee"
    range: Option<String>,
    attacks: Option<String>,
    skill: Option<String>,
    strength: Option<String>,
    ap: Option<String>,
    damage: Option<String>,
    abilities: Vec<String>,
}

struct ArmyRule {
    name: String,
    description: Option<String>,
}

struct Ability {
    name: String,
    description: String,
}

struct Datasheet {
    id: i64,
    name: String,
    legends: bool,
    models: Vec<Model>,
    weapons: Vec<Weapon>,
    army_rules: Vec<ArmyRule>,
    core_abilities: Vec<String>,
    abilities: Vec<Ability>,
    wargear_abilities: Vec<Ability>,
    keywords: Vec<String>,
    faction_keywords: Vec<String>,
}

struct Page {
    run: RunInfo,
    factions: Vec<String>,
    datasheets: Vec<Datasheet>,
}

// ---------------------------------------------------------------------------
// Database access
// ---------------------------------------------------------------------------

fn load_page(db_path: &str, run: Option<i64>, faction: Option<&str>) -> rusqlite::Result<Option<Page>> {
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;

    let factions: Vec<String> = {
    let mut stmt = conn.prepare(
        "SELECT DISTINCT faction
         FROM scrape_runs
         ORDER BY faction COLLATE NOCASE"
    )?;

    let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
    rows.collect::<rusqlite::Result<_>>()?
    };

    let map_run = |r: &rusqlite::Row| -> rusqlite::Result<RunInfo> {
        Ok(RunInfo {
            id: r.get(0)?,
            faction: r.get(1)?,
            edition: r.get(2)?,
            scraped_at: r.get(3)?,
            source_url: r.get(4)?,
        })
    };
    let run_info = if let Some(id) = run {
    conn.query_row(
        "SELECT id, faction, edition, scraped_at, source_url
         FROM scrape_runs WHERE id = ?1",
        [id],
        map_run,
    ).optional()?
    } 
    else if let Some(faction) = faction {
        conn.query_row(
            "SELECT id, faction, edition, scraped_at, source_url
            FROM scrape_runs
            WHERE faction = ?1 COLLATE NOCASE
            ORDER BY scraped_at DESC, id DESC
            LIMIT 1",
            [faction],
            map_run,
        ).optional()?
    } 
    else {
        conn.query_row(
            "SELECT id, faction, edition, scraped_at, source_url
            FROM scrape_runs
            ORDER BY scraped_at DESC, id DESC
            LIMIT 1",
            [],
            map_run,
        ).optional()?
    };
    let Some(run_info) = run_info else {
        return Ok(None);
    };

    let sheets: Vec<(i64, String, bool)> = {
        let mut stmt = conn.prepare(
            "SELECT id, name, is_legends FROM datasheets WHERE run_id = ?1 ORDER BY name COLLATE NOCASE",
        )?;
        let rows = stmt.query_map([run_info.id], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get::<_, i64>(2)? != 0))
        })?;
        rows.collect::<rusqlite::Result<_>>()?
    };

    let mut datasheets = Vec::with_capacity(sheets.len());
    for (id, name, legends) in sheets {
        datasheets.push(Datasheet {
            id,
            name,
            legends,
            models: load_models(&conn, id)?,
            weapons: load_weapons(&conn, id)?,
            army_rules: load_army_rules(&conn, id)?,
            core_abilities: load_strings(
                &conn,
                "SELECT name FROM datasheet_core_abilities WHERE datasheet_id = ?1 ORDER BY sort_order",
                id,
            )?,
            abilities: load_abilities(&conn, id, "datasheet")?,
            wargear_abilities: load_abilities(&conn, id, "wargear")?,
            keywords: load_strings(
                &conn,
                "SELECT keyword FROM datasheet_keywords WHERE datasheet_id = ?1 AND is_faction = 0 ORDER BY sort_order",
                id,
            )?,
            faction_keywords: load_strings(
                &conn,
                "SELECT keyword FROM datasheet_keywords WHERE datasheet_id = ?1 AND is_faction = 1 ORDER BY sort_order",
                id,
            )?,
        });
    }

    Ok(Some(Page { run: run_info, factions, datasheets }))
}

fn load_strings(conn: &Connection, sql: &str, ds_id: i64) -> rusqlite::Result<Vec<String>> {
    let mut stmt = conn.prepare_cached(sql)?;
    let rows = stmt.query_map([ds_id], |r| r.get::<_, String>(0))?;
    rows.collect()
}

fn load_models(conn: &Connection, ds_id: i64) -> rusqlite::Result<Vec<Model>> {
    let mut stmt = conn.prepare_cached(
        "SELECT name, base_size, movement, toughness, save, wounds, leadership, \
                objective_control, invulnerable_save \
         FROM models WHERE datasheet_id = ?1 ORDER BY sort_order",
    )?;
    let rows = stmt.query_map([ds_id], |r| {
        Ok(Model {
            name: r.get(0)?,
            base_size: r.get(1)?,
            movement: r.get(2)?,
            toughness: r.get(3)?,
            save: r.get(4)?,
            wounds: r.get(5)?,
            leadership: r.get(6)?,
            objective_control: r.get(7)?,
            invulnerable_save: r.get(8)?,
        })
    })?;
    rows.collect()
}

fn load_weapons(conn: &Connection, ds_id: i64) -> rusqlite::Result<Vec<Weapon>> {
    let rows: Vec<(i64, Weapon)> = {
        let mut stmt = conn.prepare_cached(
            "SELECT id, name, weapon_type, weapon_range, attacks, skill, strength, ap, damage \
             FROM weapons WHERE datasheet_id = ?1 ORDER BY sort_order",
        )?;
        let it = stmt.query_map([ds_id], |r| {
            Ok((
                r.get(0)?,
                Weapon {
                    name: r.get(1)?,
                    weapon_type: r.get(2)?,
                    range: r.get(3)?,
                    attacks: r.get(4)?,
                    skill: r.get(5)?,
                    strength: r.get(6)?,
                    ap: r.get(7)?,
                    damage: r.get(8)?,
                    abilities: Vec::new(),
                },
            ))
        })?;
        it.collect::<rusqlite::Result<_>>()?
    };

    let mut weapons = Vec::with_capacity(rows.len());
    for (weapon_id, mut w) in rows {
        let mut stmt = conn.prepare_cached(
            "SELECT ability FROM weapon_abilities WHERE weapon_id = ?1 ORDER BY sort_order",
        )?;
        w.abilities = stmt
            .query_map([weapon_id], |r| r.get::<_, String>(0))?
            .collect::<rusqlite::Result<_>>()?;
        weapons.push(w);
    }
    Ok(weapons)
}

fn load_army_rules(conn: &Connection, ds_id: i64) -> rusqlite::Result<Vec<ArmyRule>> {
    // The rule text normally lives in faction_rules; older JSON files stored it per datasheet.
    let mut stmt = conn.prepare_cached(
        "SELECT ar.name, COALESCE(NULLIF(ar.description, ''), fr.description) \
         FROM datasheet_army_rules ar \
         LEFT JOIN faction_rules fr ON fr.id = ar.faction_rule_id \
         WHERE ar.datasheet_id = ?1 ORDER BY ar.sort_order",
    )?;
    let rows = stmt.query_map([ds_id], |r| {
        Ok(ArmyRule { name: r.get(0)?, description: r.get(1)? })
    })?;
    rows.collect()
}

fn load_abilities(conn: &Connection, ds_id: i64, kind: &str) -> rusqlite::Result<Vec<Ability>> {
    let mut stmt = conn.prepare_cached(
        "SELECT name, description FROM datasheet_abilities \
         WHERE datasheet_id = ?1 AND kind = ?2 ORDER BY sort_order",
    )?;
    let rows = stmt.query_map((ds_id, kind), |r| {
        Ok(Ability { name: r.get(0)?, description: r.get(1)? })
    })?;
    rows.collect()
}

// ---------------------------------------------------------------------------
// HTML rendering (maud escapes all text automatically)
// ---------------------------------------------------------------------------

/// Group headings, in the order they appear on the page.
/// A datasheet goes into the FIRST group whose keyword it has, so list the most
/// specific groups first (EPIC HERO before CHARACTER before INFANTRY).
const GROUPS: &[(&str, &str)] = &[
    ("EPIC HERO", "Epic Heroes"),
    ("CHARACTER", "Characters"),
    ("BATTLELINE", "Battleline"),
    ("DEDICATED TRANSPORT", "Dedicated Transports"),
    ("AIRCRAFT", "Aircraft"),
    ("TRANSPORT", "Transports"),
    ("WALKER", "Walkers"),
    ("MONSTER", "Monsters"),
    ("VEHICLE", "Vehicles"),
    ("MOUNTED", "Mounted"),
    ("INFANTRY", "Infantry"),
];
const OTHER_GROUP: &str = "Other";

fn group_datasheets(sheets: &[Datasheet]) -> Vec<(&'static str, Vec<&Datasheet>)> {
    let mut groups: Vec<(&'static str, Vec<&Datasheet>)> =
        GROUPS.iter().map(|(_, title)| (*title, Vec::new())).collect();
    groups.push((OTHER_GROUP, Vec::new()));

    for ds in sheets {
        let idx = GROUPS
            .iter()
            .position(|(kw, _)| ds.keywords.iter().any(|k| k.eq_ignore_ascii_case(kw)))
            .unwrap_or(GROUPS.len());
        groups[idx].1.push(ds);
    }
    groups.retain(|(_, list)| !list.is_empty()); // no empty headings
    groups
}

fn show(v: &Option<String>) -> &str {
    match v.as_deref() {
        Some(s) if !s.is_empty() => s,
        _ => "–",
    }
}

fn render_page(p: &Page) -> Markup {
    let total = p.datasheets.len();
    html! {
        (DOCTYPE)
        html lang="en" {
            head {
                meta charset="utf-8";
                meta name="viewport" content="width=device-width, initial-scale=1";
                title { (p.run.faction) " – datasheets" }
                style { (PreEscaped(CSS)) }
            }
            body {
                header.site {
                    h1 { (p.run.faction) }
                    p.meta {
                        (p.run.edition) " edition · " (total) " datasheets · scraped "
                        (p.run.scraped_at) " · run #" (p.run.id) " · "
                        a href=(p.run.source_url) { "source" }
                    }
                }
                div.toolbar {
                    select #faction-select {
                    @for faction in &p.factions {
                        option
                            value=(faction)
                            selected[faction == &p.run.faction]
                            {
                                (faction)
                            }
                        }
                    }

                    input #search type="search" placeholder="Search name or keyword…" autocomplete="off";
                    button #expand type="button" { "Expand all" }
                    button #collapse type="button" { "Collapse all" }
                    label { input #hide-legends type="checkbox"; " Hide Legends" }
                    span #count.count { (total) " shown" }
                }
                main #list {
                    @for (title, list) in group_datasheets(&p.datasheets) {
                        section.group {
                            h2.group-title { (title) span.group-count { " (" (list.len()) ")" } }
                            @for ds in list { (render_datasheet(ds)) }
                        }
                    }
                }
                script { (PreEscaped(JS)) }
            }
        }
    }
}

fn render_datasheet(ds: &Datasheet) -> Markup {
    let search = {
        let mut s = ds.name.to_lowercase();
        for k in ds.keywords.iter().chain(&ds.faction_keywords) {
            s.push(' ');
            s.push_str(&k.to_lowercase());
        }
        s
    };
    let first = ds.models.first();
    let ranged: Vec<&Weapon> = ds.weapons.iter().filter(|w| w.weapon_type == "ranged").collect();
    let melee: Vec<&Weapon> = ds.weapons.iter().filter(|w| w.weapon_type == "melee").collect();

    html! {
        details.datasheet.legends[ds.legends] id=(format!("ds-{}", ds.id))
                data-search=(search) data-legends=(if ds.legends { "1" } else { "0" }) {
            summary {
                span.chev { "▸" }
                span.title { (ds.name) }
                @if ds.legends { span.badge { "LEGENDS" } }
                @if let Some(m) = first {
                    span.mini {
                        span { "M " b { (show(&m.movement)) } }
                        span { "T " b { (show(&m.toughness)) } }
                        span { "Sv " b { (show(&m.save)) } }
                        span { "W " b { (show(&m.wounds)) } }
                    }
                }
            }
            div.ds-body {
                @for m in &ds.models { (render_model(m, ds.models.len() > 1)) }

                div.columns {
                    div.col-left {
                        @if !ranged.is_empty() { (weapon_table("RANGED WEAPONS", "BS", &ranged)) }
                        @if !melee.is_empty() { (weapon_table("MELEE WEAPONS", "WS", &melee)) }
                        @if ranged.is_empty() && melee.is_empty() {
                            p.empty { "No weapons." }
                        }
                    }
                    div.col-right {
                        @if !ds.core_abilities.is_empty() {
                            div.rule-line {
                                span.rl-label { "CORE ABILITIES" }
                                span { (ds.core_abilities.join(", ")) }
                            }
                        }
                        @for r in &ds.army_rules {
                            div.rule-line {
                                span.rl-label { "ARMY RULE" }
                                @match r.description.as_deref().filter(|d| !d.is_empty()) {
                                    Some(desc) => {
                                        details.rule {
                                            summary { (r.name) }
                                            p.rule-text { (desc) }
                                        }
                                    }
                                    None => { span { (r.name) } }
                                }
                            }
                        }
                        @if !ds.abilities.is_empty() {
                            h4 { "ABILITIES" }
                            @for a in &ds.abilities { (render_ability(a)) }
                        }
                        @if !ds.wargear_abilities.is_empty() {
                            h4 { "WARGEAR ABILITIES" }
                            @for a in &ds.wargear_abilities { (render_ability(a)) }
                        }
                    }
                }

                div.keywords {
                    @if !ds.keywords.is_empty() {
                        span { b { "KEYWORDS: " } (ds.keywords.join(", ")) }
                    }
                    @if !ds.faction_keywords.is_empty() {
                        span { b { "FACTION KEYWORDS: " } (ds.faction_keywords.join(", ")) }
                    }
                }
            }
        }
    }
}

fn render_model(m: &Model, show_name: bool) -> Markup {
    let stats = [
        ("M", &m.movement),
        ("T", &m.toughness),
        ("SV", &m.save),
        ("W", &m.wounds),
        ("LD", &m.leadership),
        ("OC", &m.objective_control),
    ];
    html! {
        div.model {
            @if show_name || m.base_size.is_some() {
                div.model-name {
                    (m.name)
                    @if let Some(b) = &m.base_size { span.base { " " (b) } }
                }
            }
            div.stats {
                @for (label, value) in stats {
                    div.stat { span.lbl { (label) } span.val { (show(value)) } }
                }
                @if let Some(inv) = &m.invulnerable_save {
                    div.stat.inv { span.lbl { "INV" } span.val { (inv) } }
                }
            }
        }
    }
}

fn weapon_table(title: &str, skill_label: &str, weapons: &[&Weapon]) -> Markup {
    html! {
        div.table-wrap {
            table.weapons {
                thead {
                    tr {
                        th.wname { (title) }
                        th { "RANGE" } th { "A" } th { (skill_label) }
                        th { "S" } th { "AP" } th { "D" }
                    }
                }
                tbody {
                    @for w in weapons {
                        tr {
                            td.wname {
                                b { (w.name) }
                                @for a in &w.abilities { span.tag { (a) } }
                            }
                            td { (show(&w.range)) }
                            td { (show(&w.attacks)) }
                            td { (show(&w.skill)) }
                            td { (show(&w.strength)) }
                            td { (show(&w.ap)) }
                            td { (show(&w.damage)) }
                        }
                    }
                }
            }
        }
    }
}

fn render_ability(a: &Ability) -> Markup {
    html! {
        p.ability { b { (a.name) ": " } (a.description) }
    }
}

// ---------------------------------------------------------------------------
// Web server
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct AppState {
    db_path: Arc<String>,
}

#[derive(Deserialize)]
struct PageQuery {
    run: Option<i64>,
    faction: Option<String>,
}

async fn index(
    State(state): State<AppState>,
    Query(q): Query<PageQuery>,
) -> Result<Markup, (StatusCode, String)> {
    let db_path = state.db_path.clone();
    let loaded = tokio::task::spawn_blocking(move || {load_page(&db_path, q.run, q.faction.as_deref())})
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("Task failed: {e}")))?;

    match loaded {
        Ok(Some(page)) => Ok(render_page(&page)),
        Ok(None) => Err((
            StatusCode::NOT_FOUND,
            "No scrape run found in the database. Did you run load_json_to_db.py?".into(),
        )),
        Err(e) => {
            eprintln!("Database error: {e}");
            Err((StatusCode::INTERNAL_SERVER_ERROR, format!("Database error: {e}")))
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let db_path = env::var("DATABASE_PATH").unwrap_or_else(|_| "warhammer.db".into());
    let bind = env::var("BIND").unwrap_or_else(|_| "127.0.0.1:3000".into());

    if !Path::new(&db_path).exists() {
        eprintln!(
            "Database file '{db_path}' not found.\n\
             Create it with:  python load_json_to_db.py <scraper.json> --db {db_path}\n\
             or point DATABASE_PATH at an existing file."
        );
        std::process::exit(1);
    }

    let state = AppState { db_path: Arc::new(db_path.clone()) };
    let app = Router::new().route("/", get(index)).with_state(state);

    let listener = tokio::net::TcpListener::bind(&bind).await?;
    println!("Serving '{db_path}' on http://{bind}/");
    axum::serve(listener, app).await?;
    Ok(())
}
