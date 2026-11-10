# Datasheet viewer

A small Rust web server (Axum + SQLite + Maud) that shows every datasheet in the
database as a collapsible bar, laid out like a Wahapedia datasheet.

## Requirements
* Rust (stable, 1.80 or newer): https://rustup.rs
* A C compiler, because SQLite is compiled into the program
  * Windows: "Build Tools for Visual Studio" (the Rust installer offers to set this up)
  * macOS: `xcode-select --install`
  * Linux: `sudo apt install build-essential`
* The database produced by `load_json_to_db.py`

## Run
```bash
cd datasheet-viewer
DATABASE_PATH=../warhammer.db cargo run --release
```
Windows PowerShell:
```powershell
$env:DATABASE_PATH = "..\warhammer.db"
cargo run --release
```
Then open http://127.0.0.1:3000/

| Variable        | Default          | Meaning                              |
|-----------------|------------------|--------------------------------------|
| `DATABASE_PATH` | `warhammer.db`   | SQLite file to read                  |
| `BIND`          | `127.0.0.1:3000` | address and port to listen on        |

`/?run=<id>` shows an older scrape (ids are in the `scrape_runs` table).

## Updating the data
The database is opened on every page load, so after re-running
`load_json_to_db.py` just refresh the browser. No restart is needed.
Changes to the Rust code, `assets/style.css` or `assets/app.js` need a rebuild
(`cargo run` does that automatically).
