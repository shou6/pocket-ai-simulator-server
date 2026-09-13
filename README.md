# Pocket AI Simulator — engine and server

This repository contains the simulation engine and the API server of Pocket AI Simulator.
Pocket AI Simulator is an unofficial deck analysis tool for Pokémon TCG Pocket.
It is published to meet the source code offer of the GNU AGPL v3.0.

The contents are a snapshot of the version running in production.
Development happens in a separate repository, so history here is one commit per release.

## Contents

| Path | Description |
| --- | --- |
| `engine/` | Rust wrapper around [deckgym-core](https://github.com/shou6/deckgym-core), and its Python binding |
| `backend/` | FastAPI server, job worker, meta ingestion and deck optimization (Python 3.13) |
| `data/cards/` | Card data read by the server (Japanese names, deck code IDs) |

The web front end is a separate program that talks to the server over HTTP. It is not included.

## Build and run

Requirements: Rust (stable), [uv](https://docs.astral.sh/uv/) and PostgreSQL.

```bash
# Build the engine binding into the backend virtual environment
cd backend
uv sync
uv run maturin develop --release --manifest-path ../engine/crates/pocket-engine-py/Cargo.toml

# Create the database schema
export DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/pocket
uv run alembic upgrade head

# Fetch a metagame snapshot into data/meta/ and load it
uv run python -m pocket_api.ingest --top 10 --decklists 5
uv run python -m pocket_api.ingest.load_cli

# Start the API and the worker
uv run uvicorn pocket_api.main:app --port 8000
uv run python -m pocket_api.jobs.worker
```

The ingestion step downloads data from Limitless TCG. Check their terms before running it.
Some modules expect a snapshot in `data/meta/`, so fetch one before starting the server.

## License

The source code is licensed under the GNU Affero General Public License v3.0 only (`LICENSE`).
Some data files are under other licenses. See `REUSE.toml` for per-file information
and `THIRD_PARTY_NOTICES.md` for attributions.

This project is an unofficial fan tool. It is not affiliated with The Pokémon Company or its partners.
