# THE SIGNAL

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Repository](https://img.shields.io/badge/GitHub-granthx%2FTheSignal-black?logo=github)](https://github.com/granthx/TheSignal)

A no-nonsense, manga-styled daily newspaper that only covers what you invest in and care about: frontier AI
labs (OpenAI, Anthropic, Meta, ...), chips and compute, SpaceX and space, money and policy. It also has a
"Brain Fuel" section of smart reads, a daily Sudoku, and a newsletter anyone can subscribe to that emails
a five-minute version of the day's paper.

* **Backend:** Python (Starlette + uvicorn, SQLite, Jinja2, requests). No heavy framework, no ML dependencies.
* **Frontend:** plain HTML/CSS/ES modules, GSAP + ScrollTrigger + canvas-confetti from a CDN (optional polish).
* **Agents:** scout -> dedupe -> ranker -> editor (optional LLM) -> brain (smart reads) -> digest.

## Run it

```bash
./start.sh                      # creates .venv, installs deps, serves http://localhost:8000
```

or by hand (Windows included):

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
copy .env.example .env   # (cp on mac/linux) and edit it
cd backend
python -m app.cli serve
```

On first start it builds today's edition in the background (the page shows clearly-labelled SAMPLE content
until that finishes). Then open <http://localhost:8000>.

**Do this once:** `python -m app.cli check-sources`. The feed URLs in `backend/config/sources.yaml` were
written from memory and have never been fetched; this command tells you which work. Fix or delete the rest.

## Make it yours

| What | Where |
|---|---|
| Companies you track, topic keywords, how picky the paper is | `backend/config/topics.yaml` |
| Where news and essays come from | `backend/config/sources.yaml` |
| Send time, timezone, email settings, LLM key | `.env` (see `.env.example`) |

Watchlist companies get a score boost and a clickable chip in the UI. Raise `selection.min_score` for a thinner
paper. Everything is re-read on every run, so no restart is needed.

## Email newsletter

* Visitors subscribe on the page (double opt-in: they click a link in a confirmation email).
* Every day at `SEND_TIME` (in `TIMEZONE`) the app refreshes the edition and mails each confirmed subscriber a
  condensed briefing: **The Big Three** (headline + why it matters), **Quick Hits**, **Brain Fuel**, and a link to the Sudoku.
  The reading time in the header is measured from the actual text (225 words/min), not hard-coded.
* Every email has a one-click unsubscribe (`List-Unsubscribe` header + a link). Nobody is ever sent the same
  edition twice, and a failed address is retried, not skipped.
* **With no SMTP configured, emails are written as `.eml` files to `backend/data/outbox/`.** Open one in
  any mail client to see exactly what a subscriber would get. This is the default and needs no setup.
* To send for real, set `SMTP_*` in `.env`. For Gmail use an App Password and `SMTP_HOST=smtp.gmail.com`, port 587.
* Preview without sending: `python -m app.cli preview`, or click "See what the email looks like" on the page.

## Optional: an AI editor

Set `ANTHROPIC_API_KEY` and a model rewrites headlines plainly, writes the one-line "why it matters", rates
impact 1-5, picks the lead, and writes the hook for each smart read. Without a key the paper still works
using extractive summaries. If the model call fails or returns garbage, the paper falls back automatically.

## Commands

```
python -m app.cli serve           website + API + scheduler
python -m app.cli run             build today's edition once
python -m app.cli send            refresh and email subscribers who haven't had today's edition
python -m app.cli preview         write the newsletter HTML to a file
python -m app.cli check-sources   test every feed
python -m app.cli sudoku          print a puzzle
python -m unittest discover -s tests -t .      (from backend/) 70 tests
```

If you would rather trigger sending from cron / a hosted scheduler than keep the server running all day,
set `SCHEDULER_ENABLED=false` and run `python -m app.cli send` on your schedule.

## HTTP API

| Endpoint | |
|---|---|
| `GET /api/edition[?date=YYYY-MM-DD]` | the edition JSON (latest, or a past date; SAMPLE if none built yet) |
| `GET /api/editions` | list of past editions |
| `GET /api/sudoku?difficulty=easy\|medium\|hard[&mode=random]` | today's puzzle (same for everyone) or a random one |
| `POST /api/subscribe` `{email, name?}` | start a subscription |
| `GET /api/confirm?token=` / `GET,POST /api/unsubscribe?token=` | double opt-in / opt-out |
| `GET /api/email-preview` | the newsletter as HTML |
| `POST /api/admin/run`, `POST /api/admin/send`, `GET /api/admin/status` | `Authorization: Bearer $ADMIN_TOKEN`; disabled if the token is unset |

## Deploying

Needs one always-on Python process (the scheduler lives inside it) and a persistent disk for
`backend/data/` (the SQLite file holds your subscribers). A `Dockerfile` is included. Set `BASE_URL` to your public URL (it is
used in confirm / unsubscribe links), put it behind HTTPS, and set `TRUST_PROXY=true` only if your own reverse proxy sets `X-Forwarded-For`.

## Contributing

Contributions, feed suggestions, and improvements are welcome!
1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
