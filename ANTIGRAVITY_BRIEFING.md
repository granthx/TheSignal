# THE SIGNAL: briefing for Antigravity

Paste this whole file as context at the start of a session. It says what exists, how it fits together, what has and
has not been verified, and what to build next. Read "Verified vs not verified" before trusting any single part.

## 1. What this is and who it's for

The owner is an investor who is tired of irrelevant news. They want a personal, no-nonsense newspaper that
only carries news about the industries and companies they hold or follow, centred on AI and superintelligence
(OpenAI, Anthropic, Meta, SpaceX, and everything around them), plus:

* **Brain Fuel:** a few "smart reads" that open up how a person thinks (ideas, not news).
* **Play:** a Sudoku page, like the puzzle section of an Indian daily.
* **Newsletter:** anyone can subscribe on the page; each day they automatically get an email with the day's
  relevant news that can be read in about five minutes.
* **Look:** a manga-panel UI (ink borders, screentone dots, speed lines, speech bubbles, offset shadows).
* **Constraints from the owner:** backend in Python; frontend stack is free choice; "great frontend welcome,
  use JS libraries". The owner understands multi-agent systems and has built some, so the pipeline is
  deliberately a small set of single-purpose agents with a clear contract between them.

## 2. Architecture

```
sources.yaml ──► ScoutAgent ──► dedupe.cluster ──► dedupe.drop_seen ──► ranker.rank ──► EditorAgent ──► compose_edition ──► SQLite
 (RSS/Atom,      fetch in        merge same         drop what was        score vs       optional LLM:        │                (editions table,
  Google News    parallel,       story from         in last 2 days       topics.yaml    rewrite, "why",     │                 one JSON per day)
  queries)       parse           several outlets                                        impact, lead        │
                                                                                                           ├─► GET /api/edition ──► frontend (website)
                 reads feeds ──► BrainAgent (smart reads: keyword+depth+recency, optional LLM hook) ──────┤
                                                                                                           └─► DigestAgent ──► Mailer ──► subscribers (SMTP, or .eml outbox)
```

* **Everything downstream reads one JSON "edition"** (schema in section 5). The website and the email are two
  renderings of the same object, so they can never disagree.
* The LLM is **optional everywhere**. Each LLM step has a deterministic fallback and is wrapped so that a model
  error, timeout, non-JSON reply or wrong-shaped JSON degrades to extractive text rather than failing the edition.
* No agent framework on purpose: plain classes, plain functions. If you want LangGraph / ADK, the seams are
  `Pipeline.run()` in `pipeline.py` and the four agent classes in `agents/`.

### Files (backend/app)

| File | Responsibility |
|---|---|
| `config.py` | `Settings` dataclass from env / `.env`. `Settings.from_env()` |
| `feeds.py` | stdlib RSS 2.0 / RSS 1.0 / Atom parser. Refuses XML that declares entities, repairs HTML entities (`&nbsp;`) and bare `&` |
| `agents/scout.py` | `ScoutAgent`: parallel fetch (thread pool), `build_specs()` expands the Google News query list |
| `agents/dedupe.py` | `cluster()` merges rewordings (token Jaccard + sequence ratio, number-aware so GPT-5 != GPT-6); `drop_seen()` |
| `agents/ranker.py` | `Matcher` + `rank()`: keyword scoring from `topics.yaml`. See "Scoring" below |
| `agents/editor.py` | `EditorAgent`: LLM rewrite/keep/impact/lead/ticker with extractive fallback |
| `agents/brain.py` | `BrainAgent`: picks smart reads, one per outlet, avoids repeats within 45 days |
| `edition.py` | `compose_edition()`: builds the edition JSON (lead, sections, reads, ticker, watchlist, stats) |
| `pipeline.py` | `Pipeline.run()` orchestrates the above and saves the edition |
| `demo.py` | A labelled SAMPLE edition shown before the first real run |
| `digest.py` + `templates/` | `build_digest()`: selects stories to a word budget, measures reading time, renders HTML + text. Jinja2 autoescape on |
| `newsletter.py` | `NewsletterService`: subscribe (double opt-in), confirm, unsubscribe, welcome digest, `send_daily()` |
| `mailer.py` | `Mailer`: SMTP (starttls / ssl / none) or `.eml` files in `data/outbox/` when `SMTP_HOST` is empty |
| `services.py` | `Services`: wires DB + pipeline + newsletter; `refresh()` (locked), `send_daily_job()`, `bootstrap()` |
| `scheduler.py` | asyncio loop: daily send at `SEND_TIME` (+ catch-up inside a grace window) and refresh every `REFRESH_HOURS` |
| `sudoku.py` | seeded generator, unique solution guaranteed, 180-degree symmetric; `daily(date, difficulty)` is stable per date |
| `llm.py` | `AnthropicLLM` over plain HTTPS (no SDK), `NullLLM`, tolerant `extract_json()` |
| `db.py` | SQLite (WAL); tables `editions`, `subscribers`, `send_log` |
| `main.py` | Starlette app: API, security-header middleware, in-memory rate limiter, admin guard, static frontend |
| `cli.py` | `serve run send preview check-sources sudoku` |

### Frontend (frontend/)

`index.html`, `css/style.css`, `js/app.js` (edition rendering, filters, nav, motion), `js/sudoku.js` (game),
`js/newsletter.js` (form + email preview dialog), `js/dom.js` (helpers). No build step.

* **All DOM is built with `textContent` / `setAttribute` via `h()`, never `innerHTML`**, and links go through
  `safeUrl()` (http/https only). Feed content is untrusted. Keep it that way.
* GSAP, ScrollTrigger and canvas-confetti load from jsDelivr with `defer`. If they fail, `<html>` gets class `no-gsap`
  and CSS animations take over; nothing is hidden waiting for JS animation. Fonts: Bangers, Inter, DM Mono from Google Fonts with system fallbacks.
* Themes: `data-theme="paper"|"ink"` on `<html>`, all colours are CSS variables. Preference stored in localStorage inside try/catch.
* Sudoku: notes, undo (300 deep), hint, check, conflict highlighting (rule-based, not solution-based, so it doesn't spoil),
  keyboard (1-9, arrows, Backspace, N, Ctrl+Z), live timer that pauses in hidden tabs, daily progress saved per date+difficulty. The solution is sent to the client (fine for a newspaper puzzle; not for anything with stakes).
* Security headers come from `main.py` (`CSP`): scripts only from self + `cdn.jsdelivr.net`, styles from self + Google Fonts. **If you add a CDN, add it there.**

## 3. Scoring (ranker.py), because this is the part most worth tuning

`score = (best_beat_score + 0.35 x other_beats + core + watchlist_bonus) x outlet_weight x freshness (+ coverage_bonus)`; halved by "demote" noise patterns; dropped by "drop" patterns.

* Keyword in the **title counts double** versus the summary. Matching is word-boundary with optional plural, so `ai` never matches inside "said".
* **Overlapping keywords are suppressed** (`deepmind` inside `google deepmind`, `claude` inside `claude code`): only the longest scores. Without this, multi-alias stories ran away with the top spot (an early version ranked a DeepMind blog post above the GPT-6 launch).
* A watchlist entry can have a `bare` word (e.g. `meta`, `claude`) that only counts if a `context` word is also present, to avoid "Claude Monet" and "the meta in chess".
* The coverage bonus (several outlets, same story) only applies if the story already scored >= 70% of `min_score`, so a widely-covered irrelevant story can't sneak in.
* Without an LLM, `impact` is a percentile of rank (+ a floor of 4 when >= 3 outlets cover it).
* **YAML gotcha:** quote keywords that YAML might coerce (`"on"`, `"no"`, `"3d"`). The loader assumes string keys.

## 4. Running, testing

```bash
./start.sh                                   # or: cd backend && python -m app.cli serve
cd backend && python -m unittest discover -s tests -t .      # 70 tests, ~6 s, no network needed
python -m app.cli check-sources              # DO THIS FIRST on a networked machine
```

Tests use `tests/fakefeeds.py` (invented offline feeds) and `tests/helpers.py` (an in-process ASGI client, because
`httpx` / `pytest` were not installable where this was built). They cover: feed parsing and hostile XML, dedupe,
ranking, Sudoku invariants, digest rendering and escaping, the LLM editor's failure modes, the full newsletter
lifecycle, idempotent daily sending, a **real SMTP conversation against a local fake server**, the scheduler's catch-up and
grace behaviour, rate limiting, honeypot, admin auth, and path traversal. Feel free to move them to pytest.

The browser behaviour was verified by driving headless Chromium (Playwright): layout at 1366px and 390px with no
horizontal overflow, company filter, theme toggle, a complete Sudoku solve to the win state, reload persistence, the
subscribe form, and the email preview dialog. Those scripts were scratch files and are **not** in the repo; recreating
them as `tests/e2e/` is on the backlog. Note for e2e: the page's CSP forbids `eval`, so Playwright's string-form
`wait_for_function` fails; use selector waits.

## 5. Contracts

**Edition JSON** (`GET /api/edition`, stored in `editions.payload`)

```jsonc
{
  "date": "2026-10-05", "edition_no": 1, "mode": "live",          // "demo" = labelled sample
  "generated_at": "2026-10-05T11:50:00+00:00", "timezone": "Asia/Kolkata",
  "beat_titles": {"labs": "Frontier Labs", "...": "..."},
  "lead": { /* story */ },
  "top_ids": ["..."],                                             // up to 5 story ids by impact
  "sections": [{"id": "labs", "title": "Frontier Labs", "kicker": "...", "stories": [ /* story */ ]}],
  "reads": [{"id","title","url","source","hook","read_minutes","published"}],
  "ticker": ["short headline", "..."],
  "watchlist": {"OpenAI": 2, "Anthropic": 3, "Meta": 2, "SpaceX": 2},
  "stats": {"sources_ok","sources_failed","failed_sources":[{"name","error"}],"articles_seen","unique_stories","cleared_the_bar","published","llm"},
  "today": "2026-10-05", "is_today": true                         // added by the API
}
// story: {id, title, headline, why, summary, beat, source, url, published, impact(1-5), score, entities[], also[]}
```

**Environment**: see `.env.example` (every variable `config.py` reads is listed there).

**HTTP API**: see README. Subscribe answers identically whether or not the address already exists (no account enumeration); unsubscribe via GET only shows a confirm page, POST performs it (mail-scanner safe) and also accepts the RFC 8058 one-click POST.

## 6. Verified vs not verified (be honest with the owner about this)

Verified by tests or a real run: everything in section 4's list.

**Not verified, because the build sandbox had no outbound internet except package registries:**

1. **No real feed was ever fetched.** The 44 URLs in `sources.yaml` are from memory. Several will be wrong or moved. Run `check-sources`, then fix/delete.
2. **Google News RSS was only tested against a hand-built imitation** of its format (title suffix `- Outlet`, `<source>`, HTML description with `&nbsp;`). Re-check against live output.
3. **The CDN scripts and Google Fonts never loaded** in testing, so GSAP animations, confetti and the Bangers typeface have not been seen. The no-GSAP fallback path is what was tested and screenshotted. Open the site once on a normal connection and look. No SRI hashes are set on the CDN tags; add them or vendor the files (`frontend/vendor/`, then relax the CSP).
4. **The Anthropic call (`llm.py`) was only tested with a fake LLM**, never the real API. Default model id is `claude-sonnet-5-5` via `ANTHROPIC_MODEL`; confirm it.
5. **SMTP was tested against a local fake server on plain TCP** (`SMTP_SECURITY=none`). STARTTLS/SSL with Gmail or another provider is untested.
6. **Nothing has been deployed.** No real subscribers, no real send.

Other honest limits: relevance is hand-tuned keywords (predictable, not semantic); the email is "about five minutes"
only when the day has enough stories, otherwise it reports its true shorter measured time; Sudoku difficulty is clue count only.

## 7. Suggested next work (in priority order)

1. Run `check-sources` on a real network; repair `sources.yaml`; commit the working list. Add a small per-source health history so a feed that fails for days is flagged in the footer.
2. Look at the live UI with fonts + GSAP loaded; tune spacing, tilt amounts and the chapter header style. Consider vendoring the JS libs and adding SRI.
3. Turn on the LLM editor with a real key, read one week of output, then tune the prompt in `agents/editor.py`. Consider a second pass that fact-checks `why` against the summary.
4. Add `tests/e2e/` (Playwright) from the scenarios listed in section 4.
5. Deploy (Dockerfile exists; needs a persistent volume for `backend/data`, HTTPS, `BASE_URL`, SMTP creds). Set SPF/DKIM/DMARC on the sending domain or the daily email will land in spam.
6. Product ideas the owner would likely want: per-subscriber topic preferences (e.g. only SpaceX + Anthropic), a weekly "what changed" digest, per-company pages, search across past editions, stock-price strip for the watchlist, an embeddings pass in the ranker to catch relevant stories keywords miss.

## 8. Conventions to keep

* Python 3.11+, type hints, no new heavy dependencies without a reason (the install is 6 small packages).
* Agents are pure-ish functions of their inputs; I/O (HTTP, DB, SMTP) stays at the edges (`scout`, `db`, `mailer`) so tests can fake it.
* Never trust feed or model text: escape on output (Jinja autoescape, `textContent`), validate model JSON shape, cap sizes.
* Any failure in one source / one subscriber / the LLM must degrade the output, not stop the edition or the send.
* Don't commit `.env`, `backend/data/*.db`, or the outbox.
