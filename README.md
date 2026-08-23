# <img src="static/favicon.svg" width="24" height="24" alt="project icon"> shelf-mg

A self-hosted, single-user, fanfic-aware EPUB reader for Calibre libraries.

shelf-mg reads directly from a Calibre `metadata.db` (read-only, no Calibre install required) and serves your library as a plain server-rendered web app.

This is a highly-opinionated, precise project - I built this specifically because I was not satisfied with calibre-web/Kavita/BookLore/KOReader/Anx Reader/Moon+ Reader/plain-webDAV setups. Please make suggestions via issues/pull requests if you have any ideas to add to the project.

Please note: This is a ***reader*** only. Any library management should be done by Calibre.

## Features

- **Reads your Calibre library directly** - Does not require calibre/calibredb to be installed; Reads a Calibre-formed library, wherever it is located.
- **Fanfic aware, but fanfic agnostic** - Library views are tailored for both fics and regular ebooks alike.
- **Customizable** - Pick fonts, font size, themes, and other things.
- **Reading position and settings sync across devices** via server-side SQLite (no localStorage) - pick up on your phone where you left off on desktop (or vice versa), with conflict detection.
- **Filters/Sorting** (tags, author, series, publisher, and Calibre custom columns) - Read from your Calibre library automatically.
- **Simple** - Serves server rendered HTML. Each request is ~50kb.

## Usage

Right now, shelf-mg is distributed as build-from-source only. You must clone/download the repo to get started.

There is no authentication included in this app. You must either put this behind a reverse-proxy or use a VPN when accessing this over the internet. I personally use Tailscale to access all of my Docker services.

There are 2 options:

1. Using `docker compose` (Preferred)
2. Running locally (Needs Python)

Instead of exporting stuff to your PATH, you can use an .env file instead (see `app.py`'s dotenv loading)

### Run with Docker

A `Dockerfile` and `docker-compose.yml` are included. Edit the volume paths in `docker-compose.yml` to point at your Calibre library, then:

```bash
docker compose build && docker compose up -d
```

This mounts your Calibre library read-only and persists app data (`shelf-mg.db`, settings, reading positions) in `./shelf-mg-data`.

### Run locally

You'll need Python 3.12+ to be installed.

```bash
git clone https://github.com/<your-fork-or-org>/shelf-mg.git
cd shelf-mg
pip install -r requirements.txt

export SHELF_MG_LIBRARY_PATH=/path/to/your/Calibre Library
# optional, defaults to ./shelf-mg.db
export SHELF_MG_DB_PATH=/path/to/shelf-mg.db

python app.py
```

The app listens on `http://0.0.0.0:5000`.

### Configuration

| Environment variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SHELF_MG_LIBRARY_PATH` | Yes | - | Path to the folder containing your Calibre `metadata.db` |
| `SHELF_MG_DB_PATH` | No | `shelf-mg.db` | Path to shelf-mg's own SQLite database (settings, reading positions) |

## Disclaimer

Yes, Claude was used in the development of this app. However, any mistakes are my own: I read every line of code that Claude spits out.

Yes, the project is a bit of a mess. I kinda gave up on organizing about halfway through.

The icon was designed by me, in about 10 minutes in Inkscape.
