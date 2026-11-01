# GeoForge Watch

When a homeowner asks ChatGPT for a roofer in Birmingham, three names come back. This tool tells a local business whether it is one of them, who is named instead, and the specific fixes that help AI read its site.

Built for the PayPal AI Hackathon (submission closes Thu Nov 12, 2026, 12:00 pm PT). Built with my AI agent under my direction (Gaige Roberson, with Monty, a Grok Bot agent, writing most of the code).

## What it does
1. Free site check (no key needed): homepage status, AI crawler rules in robots.txt (GPTBot, OAI-SearchBot, ClaudeBot, PerplexityBot, Google-Extended and others), /llms.txt, LocalBusiness JSON-LD, title, meta description, phone in text, question-style content. Scored out of 100 with a fix list.
2. AI answer check: asks five buyer-style questions ("Who are the best painters in Birmingham, AL?") to the AI providers you have keys for, then counts how often the business is named and which competitors are named instead. With no key, it prints the questions so you can paste answers from the free chat apps (manual mode, labeled as such in the report).
3. Paid report through PayPal: the free check ends with a $29 checkout. PayPal Orders v2 REST (sandbox, Python stdlib only) creates an order for "GeoForge Watch full report" ($29.00 USD), sends the buyer to PayPal to approve, captures on return, and unlocks the full report **only when the capture status is COMPLETED**. An unapproved or forged order returns an error and the report stays locked. The page also shows PayPal JS SDK Smart Buttons (client ID only in the browser; the secret stays on the server).

## Run it
```
python3 gfwatch.py readiness https://buildwithcbhomes.com
python3 gfwatch.py prompts --trade painter --city "Birmingham, AL" > answers.json   # paste answers in
python3 gfwatch.py report https://buildwithcbhomes.com --business "CB Built Homes" --trade painter --city "Birmingham, AL" --answers-file answers.json
cp .env.example .env    # fill in your sandbox app keys
set -a; . ./.env; set +a
python3 gfwatch.py paypal-selftest   # creates a sandbox order, checks an unapproved capture is refused
python3 gfwatch.py serve             # http://127.0.0.1:8787  (add --mock to force mock checkout)
```
Python 3.10+, standard library only. Sandbox is the default when `PAYPAL_SANDBOX_CLIENT_ID` and `PAYPAL_SANDBOX_CLIENT_SECRET` are set; with no keys (or `--mock` / `GFW_MOCK=1`) checkout runs in mock mode and says so on screen.

Demo mode for the AI-answer section: `python3 gfwatch.py serve --demo-answers` (or `GFW_DEMO_ANSWERS=1`). With no AI key set, it loads the invented `sample-answers.json` fixture and the report shows a banner saying it is EXAMPLE DATA, not a live measurement. Any AI key set means live answers are used instead.

The paid page shows the PayPal order ID and capture ID.

## PayPal sandbox setup
1. At developer.paypal.com, create a Sandbox REST app (Merchant).
2. Copy its Client ID and Secret into `.env` (see `.env.example`). `.env` is git-ignored; never commit keys.
3. Pay with a sandbox Personal (buyer) account from the Sandbox Accounts page.
Optional AI keys: OPENAI_API_KEY, PERPLEXITY_API_KEY, ANTHROPIC_API_KEY.

## Judges: test credentials
Use any PayPal sandbox Personal account, or run with your own sandbox app keys. A demo buyer login will be listed in the Devpost submission.

## Honest limits
- AI answers vary run to run; the report is a snapshot.
- Competitor names are pulled with simple patterns (numbered or bold names) and can miss some.
- No ranking is promised.

## Roadmap inside the hackathon window
- PayPal Subscriptions for weekly monitoring ($29/mo draft price), with a change alert when the mention rate drops.
- Venmo button on mobile checkout.
- Report PDF and one-click llms.txt generator from the site check.

## Example data
`sample-answers.json` and `sample-report.md` are invented test fixtures. The competitor names in them are made up.

License: MIT (see LICENSE).
