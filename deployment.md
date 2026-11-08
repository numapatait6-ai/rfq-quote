# deployment.md

## Run locally
1. Install Python 3.11+.
2. `git clone https://github.com/numapatait6-ai/rfq-quote.git && cd rfq-quote`
3. `python -m venv .venv && source .venv/bin/activate` (Windows: `.venv\Scripts\activate`)
4. `pip install -r requirements.txt`
5. Optional: `export ANTHROPIC_API_KEY=...` to turn on AI extraction. Without it the app uses the built-in rule-based parser.
6. `uvicorn app:app --reload` and open http://localhost:8000
7. First start creates `quotes.db` and loads the sample data (3 RFQs, rate masters, 5 past quotes).
8. Tests: `python -m pytest -q`

## How it is deployed
One web service (FastAPI serves the API and the static UI) with a SQLite file. Deployed on Render as a Python web service.
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Note: SQLite on a free Render instance is wiped on restart, and the app reseeds itself. For production use Postgres or a persistent disk (see TDD, "What I would do next").

## Environment variables (names only)
- `ANTHROPIC_API_KEY` (optional, enables AI extraction)
- `ANTHROPIC_MODEL` (optional)
- `DB_PATH` (optional, default `quotes.db`)

No secrets are stored in the repository.

## Demo logins (sample accounts, no real data)
| Role | Email | Password |
|---|---|---|
| Estimator | estimator@demo.test | Estimate#2026 |
| Sales head | saleshead@demo.test | Approve#2026 |

## Suggested reviewer walkthrough
1. Sign in as estimator. Open RFQ-2611-014, open the part, press Save & accept.
2. Submit for approval (value is above ₹25 lakh, so it goes to the sales head).
3. Sign in as sales head, approve, then open the PDF quote.
4. Rate masters: add an EN8 rate with a new date. The approved quote's PDF still shows the old rate.
5. Create a new RFQ and paste `Rear spindle | EN8 | 0.6 | 180000` into Extract parts.
