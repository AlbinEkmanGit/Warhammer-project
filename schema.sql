-- Schema for the Wahapedia datasheet scraper output (SQLite).
-- Every field in the scraper's JSON has a home here.
--
-- Stat values are stored as TEXT on purpose: they are not always plain numbers
-- ('10"', '4+', 'D6+1', '-', 'Melee'). Cast in queries when you need numbers.
--
-- Each JSON file loaded becomes one row in scrape_runs, so you can keep several
-- scrapes (e.g. after a Faction Pack update) side by side and compare them.

PRAGMA foreign_keys = ON;

-- One row per JSON file / scrape ---------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_runs (
    id              INTEGER PRIMARY KEY,
    faction         TEXT    NOT NULL,
    edition         TEXT    NOT NULL,
    source_url      TEXT    NOT NULL,
    scraped_at      TEXT    NOT NULL,                -- from the JSON (ISO 8601, UTC)
    datasheet_count INTEGER NOT NULL DEFAULT 0,
    loaded_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (faction, edition, scraped_at)
);

-- Faction-wide army rules, e.g. "Doctrina Imperatives" -------------------------
CREATE TABLE IF NOT EXISTS faction_rules (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES scrape_runs(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    UNIQUE (run_id, name)
);

-- One row per datasheet ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS datasheets (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES scrape_runs(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    url        TEXT    NOT NULL,
    is_legends INTEGER NOT NULL DEFAULT 0 CHECK (is_legends IN (0, 1)),
    UNIQUE (run_id, url)
);

-- Statlines: a datasheet can have several model profiles --------------------------
CREATE TABLE IF NOT EXISTS models (
    id                INTEGER PRIMARY KEY,
    datasheet_id      INTEGER NOT NULL REFERENCES datasheets(id) ON DELETE CASCADE,
    sort_order        INTEGER NOT NULL,
    name              TEXT    NOT NULL,
    base_size         TEXT,
    movement          TEXT,
    toughness         TEXT,
    save              TEXT,
    wounds            TEXT,
    leadership        TEXT,
    objective_control TEXT,
    invulnerable_save TEXT
);

-- Weapon profiles (ranged and melee) -----------------------------------------------
CREATE TABLE IF NOT EXISTS weapons (
    id           INTEGER PRIMARY KEY,
    datasheet_id INTEGER NOT NULL REFERENCES datasheets(id) ON DELETE CASCADE,
    sort_order   INTEGER NOT NULL,
    weapon_type  TEXT    NOT NULL CHECK (weapon_type IN ('ranged', 'melee')),
    name         TEXT    NOT NULL,
    weapon_range TEXT,
    attacks      TEXT,
    skill_type   TEXT    CHECK (skill_type IN ('BS', 'WS')),
    skill        TEXT,
    strength     TEXT,
    ap           TEXT,
    damage       TEXT
);

-- Weapon abilities, e.g. SUSTAINED HITS 1, TWIN-LINKED -------------------------------
CREATE TABLE IF NOT EXISTS weapon_abilities (
    id         INTEGER PRIMARY KEY,
    weapon_id  INTEGER NOT NULL REFERENCES weapons(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL,
    ability    TEXT    NOT NULL
);

-- Army rules a datasheet uses (name always; text when the JSON carried it) -----------
CREATE TABLE IF NOT EXISTS datasheet_army_rules (
    id              INTEGER PRIMARY KEY,
    datasheet_id    INTEGER NOT NULL REFERENCES datasheets(id) ON DELETE CASCADE,
    sort_order      INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    faction_rule_id INTEGER REFERENCES faction_rules(id) ON DELETE SET NULL,
    description     TEXT                                -- only set by older JSON files
);

-- Core abilities, e.g. "Deadly Demise 1" ------------------------------------------------
CREATE TABLE IF NOT EXISTS datasheet_core_abilities (
    id           INTEGER PRIMARY KEY,
    datasheet_id INTEGER NOT NULL REFERENCES datasheets(id) ON DELETE CASCADE,
    sort_order   INTEGER NOT NULL,
    name         TEXT    NOT NULL
);

-- Datasheet abilities and wargear abilities with their text ------------------------------
CREATE TABLE IF NOT EXISTS datasheet_abilities (
    id           INTEGER PRIMARY KEY,
    datasheet_id INTEGER NOT NULL REFERENCES datasheets(id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL CHECK (kind IN ('datasheet', 'wargear')),
    sort_order   INTEGER NOT NULL,
    name         TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT ''
);

-- Keywords and faction keywords ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS datasheet_keywords (
    id           INTEGER PRIMARY KEY,
    datasheet_id INTEGER NOT NULL REFERENCES datasheets(id) ON DELETE CASCADE,
    sort_order   INTEGER NOT NULL,
    keyword      TEXT    NOT NULL,
    is_faction   INTEGER NOT NULL DEFAULT 0 CHECK (is_faction IN (0, 1))
);

-- Indexes on the columns you will filter and join on ---------------------------------------
CREATE INDEX IF NOT EXISTS idx_datasheets_run     ON datasheets(run_id);
CREATE INDEX IF NOT EXISTS idx_datasheets_name    ON datasheets(name);
CREATE INDEX IF NOT EXISTS idx_models_ds          ON models(datasheet_id);
CREATE INDEX IF NOT EXISTS idx_weapons_ds         ON weapons(datasheet_id);
CREATE INDEX IF NOT EXISTS idx_weapon_abil_weapon ON weapon_abilities(weapon_id);
CREATE INDEX IF NOT EXISTS idx_weapon_abil_name   ON weapon_abilities(ability);
CREATE INDEX IF NOT EXISTS idx_army_rules_ds      ON datasheet_army_rules(datasheet_id);
CREATE INDEX IF NOT EXISTS idx_core_abil_ds       ON datasheet_core_abilities(datasheet_id);
CREATE INDEX IF NOT EXISTS idx_abilities_ds       ON datasheet_abilities(datasheet_id);
CREATE INDEX IF NOT EXISTS idx_keywords_ds        ON datasheet_keywords(datasheet_id);
CREATE INDEX IF NOT EXISTS idx_keywords_kw        ON datasheet_keywords(keyword);

-- Convenience views --------------------------------------------------------------------------
-- Weapons with their abilities on one line.
CREATE VIEW IF NOT EXISTS v_weapon_profiles AS
SELECT d.run_id,
       d.name  AS datasheet,
       w.weapon_type,
       w.name  AS weapon,
       w.weapon_range, w.attacks, w.skill_type, w.skill, w.strength, w.ap, w.damage,
       (SELECT group_concat(wa.ability, ', ')
          FROM (SELECT ability FROM weapon_abilities
                 WHERE weapon_id = w.id ORDER BY sort_order) wa) AS abilities
FROM weapons w
JOIN datasheets d ON d.id = w.datasheet_id;

-- Datasheets with their keywords on one line.
CREATE VIEW IF NOT EXISTS v_datasheet_keywords AS
SELECT d.run_id,
       d.id   AS datasheet_id,
       d.name AS datasheet,
       d.is_legends,
       (SELECT group_concat(k.keyword, '; ')
          FROM (SELECT keyword FROM datasheet_keywords
                 WHERE datasheet_id = d.id AND is_faction = 0 ORDER BY sort_order) k) AS keywords,
       (SELECT group_concat(k.keyword, '; ')
          FROM (SELECT keyword FROM datasheet_keywords
                 WHERE datasheet_id = d.id AND is_faction = 1 ORDER BY sort_order) k) AS faction_keywords
FROM datasheets d;
