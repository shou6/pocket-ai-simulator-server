# Third-party notices

This project includes or uses the following third-party works.
Per-file license information is recorded in `REUSE.toml`, and the full license texts are in `LICENSES/`.

## deckgym-core

- Source: <https://github.com/bcollazo/deckgym-core>
- Modified version used by this project: <https://github.com/shou6/deckgym-core> (branch `pocket-cards`)
- License: GNU Affero General Public License v3.0 only (`LICENSES/AGPL-3.0-only.txt`)
- Use: the rules engine. It is compiled into `engine/` (pocket-engine) and loaded by `backend/`.
  The exact revision is pinned by `rev` in `engine/crates/pocket-engine/Cargo.toml`.

## pokemon-tcg-pocket-database

- Source: <https://github.com/flibustier/pokemon-tcg-pocket-database>
- License: MIT (`LICENSES/MIT.txt`)
- Use: card metadata used to generate `data/cards/deck_builder_ids.json`.
  The card images shown by the web service are taken from its release archive.
  The images themselves belong to their respective rights holders (see below).

```text
MIT License

Copyright (c) 2025 Jon (flibustier)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Bulbapedia

- Source: card pages on Bulbapedia, <https://bulbapedia.bulbagarden.net/>
- Author: Bulbapedia contributors
- License: CC BY-NC-SA 2.5 (`LICENSES/CC-BY-NC-SA-2.5.txt`)
- License deed: <https://creativecommons.org/licenses/by-nc-sa/2.5/>
- Use: Japanese names of cards, attacks and abilities.
- Files: `data/cards/names_ja.json` and `data/cards/name_overrides.json`
- Changes: the names were extracted from the card pages and keyed by card ID.
- These files are distributed under the same license.
  It applies to these data files only, not to the source code.

## Limitless TCG

- Source: <https://play.limitlesstcg.com/>
- Use: metagame statistics, matchup records and decklists, collected by `pocket_api.ingest`.
- No license is granted for this data. It is subject to the terms of Limitless TCG.

## Pokémon

Card names and card images belong to their rights holders.
They include Nintendo, Creatures Inc., GAME FREAK inc. and The Pokémon Company.
This project is an unofficial fan tool. It is not affiliated with or endorsed by them.

## Other dependencies

Rust and Python packages are listed in `engine/Cargo.lock` and `backend/uv.lock`.
Their licenses are recorded in each package's metadata.
