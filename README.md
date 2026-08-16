# Analyst Desk

**A research team of AI agents that argue with each other, then check their own work.**

You give it a company. Two analysts research it independently and argue opposite sides, a referee decides which arguments survived, and an isolated fact-checker re-verifies every number against its source before you see it.

Every claim carries a source id. Anything the checker cannot support is labelled as unverified rather than quietly removed.

> Research to help you think, not advice telling you what to do. It never says buy, sell or hold, and never predicts a price.

---

## Why this exists

Every article about a stock is either hype or doom, and a chatbot asked "is Nvidia a good investment?" will write you a confident essay shaped by how you phrased the question. Neither tells you which parts are actually true.

Real institutional research works differently: separate analysts are assigned to argue for and against, a risk desk stress-tests both, and a senior analyst weighs the evidence. That adversarial structure is what makes the output trustworthy.

This reproduces that, and adds the thing a real desk does not have: **an automated verifier that catches the analysts making things up.**

## The proof

From a real run on Coca-Cola. The memo claimed the company's annual report warns about consumers switching to cheaper own-brand drinks. It reads exactly like something a 10-K would say.

```
[MISREPRESENTED]  "Coca-Cola's own 10-K risk factors name the exact mechanism
                   that would hurt it, consumers trading down to cheaper
                   private-label drinks"                            cites [S21]

  The retrieved excerpt cites "global inflationary pressures", "a recession or
  economic slowdown", and says unfavourable conditions "could negatively affect
  the affordability of, and consumer demand for, our beverages". It contains no
  reference to private-label or own-brand alternatives. The inflation half is
  supported. The private-label mechanism is not in the source.
```

A plausible, well-written, entirely invented detail, stopped before it reached the reader.

On a separate Nvidia run it caught something subtler: the memo said the stock rallied 18% "in a handful of sessions". The 18% was real, but the cited source was a one-month window containing no dates for the low, so the *speed* was never established by the evidence. The number was right; the implied timeframe was invented.

---

## How it works

```
   you type "Apple"
         │
         ▼
   ┌──────────┐   name to ticker, with candidates to confirm
   │ RESOLVE  │   so a wrong "Delta" cannot poison the run
   └────┬─────┘
        ▼
   ┌──────────┐   prices, audited SEC financials, the annual report,
   │  GATHER  │   news and a peer group, fetched in parallel.
   └────┬─────┘   every figure is stamped with a citation id
        ▼
   ┌──────────────────────────────┐
   │ RESEARCH                     │   Bull and Bear work from the SAME evidence
   │   ┌────────┐   ┌────────┐    │   in separate contexts. Neither can see the
   │   │  BULL  │   │  BEAR  │    │   other, so neither reacts to the other.
   │   └────────┘   └────────┘    │   The Bear also produces a direction-agnostic
   └────┬─────────────────────────┘   risk map as a non-advocacy section.
        ▼
   ┌──────────┐   each reads the other's case and attacks its weakest claims:
   │  DEBATE  │   unsupported, cherry-picked, misread, wrong horizon, overstated.
   └────┬─────┘   both answer the same pre-debate positions, so neither gets
        │         the last word. Conceding a point is explicitly allowed.
        ▼
   ┌───────────┐  weighs evidence quality, decides what survived,
   │ MODERATOR │  writes one page a non-expert can finish in 30 seconds
   └────┬──────┘
        ▼
   ┌───────────┐  sees ONLY the memo and the raw source records.
   │  VERIFY   │  never the debate, so it cannot be persuaded by the
   └────┬──────┘  argument it is checking. Flags what fails.
        ▼
    the memo, plus a chat that answers follow-ups from the same sources
```

### The horizon changes what counts as evidence

Not how far ahead it predicts. Over a few weeks, business quality is irrelevant and momentum decides the price. Over five years, momentum is noise and the moat decides it. The agents reweight accordingly, and pull different data: a short-horizon run fetches one and three month charts, a long-horizon run pulls five years.

---

## What makes it more than an API wrapper

**Citations are a schema constraint, not a request.** `evidence_ref` is a required field on every claim, so an agent physically cannot record a point without attaching the source it came from. Prompting for citations gets you mostly-compliance; making it structural gets you compliance.

**The verifier is context-isolated.** It receives the memo and the raw data, and nothing else. A verifier that reads the argument can be talked into it.

**Agents are context-isolated during research.** Separate message histories and tool budgets. They meet only at the debate stage.

**The orchestrator is hand-written.** An explicit state machine rather than a framework, so the control flow is something you can read and defend. Every transition emits a typed event, and those events are simultaneously the live UI feed and the permanent audit log, so there is no privileged internal view.

**Failure degrades instead of dying.** One agent falling over is recorded and the run continues. If verification cannot run, the memo says *"claims unchecked"* rather than presenting itself as verified. This happened for real during development and behaved correctly before there was a test for it.

**Financials come from the filings, not a scraper.** Fundamentals are read from SEC XBRL, the structured data companies file with the regulator. Market data is used for prices, where it is reliable.

---

## Data sources

| Source | Used for |
|---|---|
| SEC XBRL | Audited revenue, margins, cash flow and balance sheet, exactly as filed |
| SEC EDGAR | 10-K, 10-Q, 8-K and 20-F filings, with the Risk Factors section extracted |
| Market data | Prices, returns, volatility, drawdown, moving averages |
| Peer group | Comparable companies, so a valuation multiple means something |
| News | Recent headlines, treated as evidence of what is being said, not what is true |

All free, all cached locally so a repeat run is instant.

---

## Running it

Requires Python 3.11+, Node 18+, and an [Anthropic API key](https://console.anthropic.com).

```bash
git clone https://github.com/krishnanhamsavi/analyst-desk.git
cd analyst-desk

# backend
python -m venv .venv && .venv/Scripts/activate     # macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env                                # then add your key and contact email

# frontend
cd frontend && npm install && cd ..
```

`.env` needs two things:

```
ANTHROPIC_API_KEY=sk-ant-...
SEC_USER_AGENT=YourApp/0.1 (your.email@example.com)   # EDGAR rejects requests without it
```

Then run both halves:

```bash
cd backend && python serve.py --reload     # API on :8000
cd frontend && npm run dev                 # UI on :5173
```

Or skip the browser entirely:

```bash
cd backend
python run.py "Coca-Cola" --horizon medium
python run.py NVDA --horizon short --view "AI chip demand feels like a bubble to me"
python run.py --history
```

Set a spend cap in the Anthropic console before you start. A run costs roughly **$0.66** and takes about **five minutes**.

---

## Layout

```
backend/
  tools/          the grounded data layer. every function returns the same
                  envelope with source refs, and never raises
  agents/         one file per role, plus the hand-written tool-calling loop
  prompts/        every agent's system prompt as an editable .md file
  core/           config, caching, events, persistence, token accounting
  orchestrator.py the state machine
  api/            FastAPI and the WebSocket bridge
frontend/src/
  components/     the live trading floor, the memo, the chat, the glossary
```

Prompts live in files rather than string literals, so a prompt change shows up as a readable diff.

**79 tests**, covering the state machine, degradation paths, verification, citation integrity, and the data-parsing edge cases that caused real bugs.

---

## Things that went wrong, and what they taught me

**A gross margin of 405%.** XBRL's `fy` field is the year of the *filing* that reported a fact, not the period the fact describes, so a 10-K restating three prior years tags them all with its own year. Grouping on it divided this year's profit by three-year-old revenue. Facts are now aligned on period end date.

**Nvidia frozen at FY2022.** It switched revenue tags partway through its history, and the code took the first tag that returned data. All tags for a concept are now merged.

**A dividend yield reported as 45% instead of 0.45%.** Found by an agent, not by me. It flagged the figure as internally impossible against the payout ratio and refused to reason from it, which is the verification premise working before the verifier existed.

**Prompt caching that cached nothing.** The shared evidence pack sat in the user turn, behind each agent's own role prompt. Caching is a prefix match, so all five agents wrote their own cache and none read. Moving the pack ahead of the role prompt made it shared.

**A repair that was worse than the bug.** A helper written to fix cosmetic escaping decoded every `\uXXXX` sequence, including ``, putting a real form-feed character inside a sentence. Invisible in logs, corrupting on screen. It now decodes only to printable characters.

---

## Honest limitations

**Two instances of one model share blind spots.** Forcing them into opposite roles produces structurally different arguments, but does not make their judgement independent. They can be confidently wrong together. `BULL_MODEL` and `BEAR_MODEL` allow running the two sides on different models, which reduces the problem without solving it.

**There is no valuation model.** It compares multiples against history and peers. It does not build a DCF, so it cannot tell you what growth rate today's price implies.

**Nothing is backtested.** The confidence ratings are not calibrated against outcomes, because that has not been measured.

**Missing data a professional would want:** earnings call transcripts, analyst estimate revisions, insider transactions.

**Most people should not pick stocks.** This makes you better informed about one company. It does not make stock picking a good idea, and the evidence strongly favours low-cost index funds for most individuals.

---

## Built with

Python, FastAPI, WebSockets, Pydantic, SQLAlchemy, the Anthropic API with tool use and structured outputs, React, TypeScript, Tailwind and Recharts.
