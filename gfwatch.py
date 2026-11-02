#!/usr/bin/env python3
"""GeoForge Watch: does AI search name your business, and can AI read your site?

Stdlib only. No paid keys required.
  python3 gfwatch.py readiness https://example.com
  python3 gfwatch.py prompts --trade roofer --city "Birmingham, AL"
  python3 gfwatch.py answers --business "CB Built Homes" --trade "painter" --city "Birmingham, AL" [--answers-file pasted.json]
  python3 gfwatch.py report https://example.com --business NAME --trade T --city C [--answers-file F] [--out report.md]
  python3 gfwatch.py serve  (local demo UI on http://127.0.0.1:8787, PayPal sandbox or mock checkout)
Providers for live answers: set OPENAI_API_KEY, ANTHROPIC_API_KEY or PERPLEXITY_API_KEY.
Without keys, use --answers-file with answers pasted from the free chat apps (honest manual mode).
"""
import argparse, json, os, re, sys, base64, html, urllib.request, urllib.parse, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_ANSWERS = os.path.join(HERE, "sample-answers.json")

UA = "GeoForgeWatch/0.1 (+readiness check)"
AI_BOTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "PerplexityBot", "Google-Extended", "Applebot-Extended", "CCBot"]

def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)

def robots_rules(txt):
    """Return {bot: 'blocked'|'allowed'|'default'} using simple group parsing."""
    groups, cur, out = [], None, {}
    for line in txt.splitlines():
        line = line.split("#")[0].strip()
        if not line or ":" not in line: continue
        k, v = [s.strip() for s in line.split(":", 1)]
        k = k.lower()
        if k == "user-agent":
            if cur is None or cur["rules"]:
                cur = {"agents": [], "rules": []}; groups.append(cur)
            cur["agents"].append(v.lower())
        elif k in ("disallow", "allow") and cur is not None:
            cur["rules"].append((k, v))
    def blocked(g): return any(k == "disallow" and v == "/" for k, v in g["rules"])
    star = next((g for g in groups if "*" in g["agents"]), None)
    for b in AI_BOTS:
        g = next((g for g in groups if b.lower() in g["agents"]), None)
        if g: out[b] = "blocked" if blocked(g) else "allowed"
        elif star and blocked(star): out[b] = "blocked (via *)"
        else: out[b] = "allowed (default)"
    return out

def readiness(url):
    if not url.startswith("http"): url = "https://" + url
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
    st, page = fetch(url)
    rst, robots = fetch(base + "/robots.txt")
    lst, llms = fetch(base + "/llms.txt")
    checks = []
    def add(name, ok, detail, fix):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": fix})
    add("Homepage loads", st == 200, f"HTTP {st}", "Make sure the homepage returns 200 without a login wall.")
    rules = robots_rules(robots) if rst == 200 else {b: "no robots.txt (allowed)" for b in AI_BOTS}
    blocked = [b for b, v in rules.items() if v.startswith("blocked")]
    add("AI crawlers allowed in robots.txt", not blocked, "blocked: " + ", ".join(blocked) if blocked else "none blocked",
        "Remove Disallow: / for the AI search bots you want citing you (OAI-SearchBot, PerplexityBot, ClaudeBot).")
    add("llms.txt present", lst == 200 and len(llms.strip()) > 20, f"HTTP {lst}", "Publish /llms.txt: who you are, services, area, phone, key pages.")
    ld = re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I)
    types = []
    for blob in ld:
        types += re.findall(r'"@type"\s*:\s*"([^"]+)"', blob)
    local = [t for t in types if re.search(r"LocalBusiness|Contractor|Plumber|Roofing|HomeAndConstruction|Electrician|HVAC|RealEstate|Organization|ProfessionalService", t, re.I)]
    add("Business schema (JSON-LD)", bool(local), ", ".join(sorted(set(types)))[:120] or "none found",
        "Add LocalBusiness (or the trade subtype) JSON-LD with name, address, phone, areaServed, sameAs.")
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    add("Page title", bool(title and title.group(1).strip()), html.unescape(title.group(1).strip())[:90] if title else "missing",
        "Title should name the trade and the city.")
    desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']*)', page, re.I)
    add("Meta description", bool(desc and desc.group(1).strip()), (desc.group(1)[:90] if desc else "missing"),
        "Write one sentence: what you do, where, and how to reach you.")
    phone = re.search(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", re.sub(r"<[^>]+>", " ", page))
    add("Phone number in page text", bool(phone), phone.group(0) if phone else "none found", "Put the phone number in HTML text, not only in an image.")
    faq = re.search(r"FAQPage|<h[23][^>]*>[^<]*\?", page, re.I)
    add("Question-style content", bool(faq), "found" if faq else "none found", "Add an FAQ answering the questions customers ask AI (cost, timing, area).")
    score = round(100 * sum(c["ok"] for c in checks) / len(checks))
    return {"url": url, "score": score, "checks": checks, "robots": rules}

def prompts(trade, city):
    return [f"Who are the best {trade}s in {city}?",
            f"I need a {trade} in {city}. Who should I call?",
            f"Recommend a reliable {trade} near {city} with good reviews.",
            f"What {trade} companies in {city} do you recommend for a homeowner?",
            f"Top rated {trade} {city}"]

def ask_live(prompt):
    """Ask whichever provider has a key in env. Returns list of (provider, text)."""
    res = []
    def post(url, body, headers):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    try:
        if os.getenv("OPENAI_API_KEY"):
            j = post("https://api.openai.com/v1/chat/completions", {"model": os.getenv("GFW_OPENAI_MODEL", "gpt-4o-mini"), "messages": [{"role": "user", "content": prompt}]},
                     {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
            res.append(("openai", j["choices"][0]["message"]["content"]))
        if os.getenv("PERPLEXITY_API_KEY"):
            j = post("https://api.perplexity.ai/chat/completions", {"model": "sonar", "messages": [{"role": "user", "content": prompt}]},
                     {"Authorization": "Bearer " + os.environ["PERPLEXITY_API_KEY"]})
            res.append(("perplexity", j["choices"][0]["message"]["content"]))
        if os.getenv("ANTHROPIC_API_KEY"):
            j = post("https://api.anthropic.com/v1/messages", {"model": os.getenv("GFW_ANTHROPIC_MODEL", "claude-3-5-haiku-latest"), "max_tokens": 800, "messages": [{"role": "user", "content": prompt}]},
                     {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
            res.append(("anthropic", j["content"][0]["text"]))
    except Exception as e:
        res.append(("error", str(e)))
    return res

def norm(s): return re.sub(r"[^a-z0-9 ]", "", s.lower())

def score_answers(business, records):
    """records: [{provider, prompt, answer}] -> mention stats + competitors named."""
    b = norm(business); hits = 0; names = {}
    for r in records:
        a = r["answer"]; r["mentioned"] = b in norm(a); hits += r["mentioned"]; r["named"] = []
        for m in re.findall(r"\*\*([^*]{3,60})\*\*|^\s*\d+\.\s+([A-Z][^\n:–-]{2,60})", a, re.M):
            n = (m[0] or m[1]).strip()
            if n and norm(n) != b:
                names[n] = names.get(n, 0) + 1
                if n not in r["named"]: r["named"].append(n)
    rate = round(100 * hits / len(records)) if records else 0
    return {"mention_rate": rate, "answers": len(records), "mentions": hits,
            "competitors": sorted(names.items(), key=lambda x: -x[1])[:10], "records": records}

def gather(business, trade, city, answers_file=None):
    recs = []
    if answers_file:
        for r in json.load(open(answers_file)): recs.append(r)
    else:
        for p in prompts(trade, city):
            for prov, txt in ask_live(p):
                recs.append({"provider": prov, "prompt": p, "answer": txt})
    if not recs and os.getenv("GFW_DEMO_ANSWERS") == "1":
        for r in json.load(open(DEMO_ANSWERS)): recs.append(r)
        res = score_answers(business, recs); res["example"] = True; return res
    if not recs:
        return None
    return score_answers(business, recs)

def report_md(rd, ans, business, trade, city):
    L = [f"# GeoForge Watch report: {business}", f"{trade} · {city}", ""]
    if ans: L += ["## Does AI name you?"]
    if ans and ans.get("example"):
        L += ["> **EXAMPLE DATA, NOT A LIVE MEASUREMENT.** No AI provider key is set, so this section uses the invented demo fixture sample-answers.json. Competitor names below are made up.", ""]
    if ans:
        L += [f"Named in {ans['mentions']} of {ans['answers']} AI answers ({ans['mention_rate']}%).", ""]
        if ans["competitors"]:
            L += ["Named instead of you most often:"] + [f"- {n} ({c}x)" for n, c in ans["competitors"]] + [""]
    else:
        L += ["## Does AI name you?", "Not measured: no provider key set and no pasted answers. Run `prompts` and paste answers into a JSON file.", ""]
    L += [f"## Can AI read your site? {rd['score']}/100", "", "| Check | Result | Detail |", "|---|---|---|"]
    L += [f"| {c['check']} | {'pass' if c['ok'] else 'fix'} | {c['detail']} |" for c in rd["checks"]]
    L += ["", "## Fix list (in order)"] + [f"{i+1}. {c['fix']}" for i, c in enumerate([c for c in rd["checks"] if not c["ok"]])]
    L += ["", "AI answers change day to day. This is a snapshot, not a guarantee of ranking. Made by GeoForge Watch, an AI tool; a person at GeoForge reviews paid reports."]
    return "\n".join(L)

# ---------- PayPal (sandbox, Orders v2, stdlib only) ----------
PP = "https://api-m.sandbox.paypal.com"
PRICE, ITEM = "29.00", "GeoForge Watch full report"
FORCE_MOCK = os.getenv("GFW_MOCK") == "1"
def _creds():
    cid = os.getenv("PAYPAL_SANDBOX_CLIENT_ID") or os.getenv("PAYPAL_CLIENT_ID")
    sec = os.getenv("PAYPAL_SANDBOX_CLIENT_SECRET") or os.getenv("PAYPAL_CLIENT_SECRET")
    return (cid, sec) if (cid and sec and not FORCE_MOCK) else (None, None)
def pay_mode(): return "sandbox" if _creds()[0] else "mock"

def _pp(method, path, tok, body=None):
    """Call PayPal; returns (http_code, json). Never raises on HTTP errors."""
    req = urllib.request.Request(PP + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", "Prefer": "return=representation"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: j = json.loads(e.read() or b"{}")
        except Exception: j = {}
        return e.code, j

_TOK = {"v": None, "exp": 0}
def paypal_token():
    cid, sec = _creds()
    if not cid: return None
    if _TOK["v"] and time.time() < _TOK["exp"]: return _TOK["v"]
    req = urllib.request.Request(PP + "/v1/oauth2/token", data=b"grant_type=client_credentials",
        headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()})
    with urllib.request.urlopen(req, timeout=30) as r: j = json.loads(r.read())
    _TOK.update(v=j["access_token"], exp=time.time() + int(j.get("expires_in", 300)) - 60); return _TOK["v"]

def paypal_create_order(return_url="http://127.0.0.1:8787/paid", cancel_url="http://127.0.0.1:8787/"):
    tok = paypal_token()
    if not tok:
        return {"mode": "mock", "http": 201, "id": "MOCK-ORDER", "approve": "/paid?token=MOCK-ORDER"}
    body = {"intent": "CAPTURE", "purchase_units": [{
                "description": ITEM, "amount": {"currency_code": "USD", "value": PRICE,
                    "breakdown": {"item_total": {"currency_code": "USD", "value": PRICE}}},
                "items": [{"name": ITEM, "quantity": "1", "category": "DIGITAL_GOODS",
                           "unit_amount": {"currency_code": "USD", "value": PRICE}}]}],
            "payment_source": {"paypal": {"experience_context": {"brand_name": "GeoForge Watch",
                "shipping_preference": "NO_SHIPPING", "user_action": "PAY_NOW",
                "return_url": return_url, "cancel_url": cancel_url}}}}
    code, j = _pp("POST", "/v2/checkout/orders", tok, body)
    if code not in (200, 201): return {"mode": "sandbox", "http": code, "error": j.get("name", "ERROR"), "id": None, "approve": None}
    href = next((l["href"] for l in j.get("links", []) if l["rel"] in ("payer-action", "approve")), None)
    return {"mode": "sandbox", "http": code, "id": j["id"], "status": j.get("status"), "approve": href}

def paypal_capture(order_id):
    """Capture; report unlocks only if status == COMPLETED."""
    tok = paypal_token()
    if not tok: return {"mode": "mock", "http": 201, "status": "COMPLETED", "order_id": order_id, "capture_id": "MOCK-CAPTURE"}
    code, j = _pp("POST", f"/v2/checkout/orders/{urllib.parse.quote(order_id)}/capture", tok, {})
    if code in (200, 201):
        caps = [c for pu in j.get("purchase_units", []) for c in pu.get("payments", {}).get("captures", [])]
        return {"mode": "sandbox", "http": code, "status": j.get("status"), "order_id": j.get("id"), "capture_id": caps[0]["id"] if caps else None}
    det = (j.get("details") or [{}])[0].get("issue", "")
    return {"mode": "sandbox", "http": code, "status": "NOT_CAPTURED", "error": j.get("name", "ERROR"), "issue": det}


# ---------- AG Grid Community (MIT) results table ----------
AGG = "https://cdn.jsdelivr.net/npm/ag-grid-community@36.2.0/dist/ag-grid-community.min.js"
def grid_html(ans, full):
    """Sortable/filterable AI-answer table. Preview hides competitor names (they are the paid part)."""
    if not ans: return "<p><em>AI answers not measured: no provider key set.</em></p>"
    rows = [{"query": r["prompt"], "engine": r["provider"], "mentioned": "Yes" if r["mentioned"] else "No",
             "named": ", ".join(r.get("named", [])) if full else f"{len(r.get('named', []))} competitor(s), unlock to see",
             "answer": r["answer"][:300] if full else ""} for r in ans["records"]]
    cols = [{"field": "query", "headerName": "AI query", "flex": 2, "wrapText": True, "autoHeight": True},
            {"field": "engine", "headerName": "Engine", "width": 110},
            {"field": "mentioned", "headerName": "Named you?", "width": 125},
            {"field": "named", "headerName": "Named instead", "flex": 2, "wrapText": True, "autoHeight": True}]
    if full: cols.append({"field": "answer", "headerName": "Answer excerpt", "flex": 3, "wrapText": True, "autoHeight": True})
    banner = ("<p class=ex><strong>EXAMPLE DATA, NOT A LIVE MEASUREMENT.</strong> No AI provider key is set; rows come from the invented fixture sample-answers.json.</p>"
              if ans.get("example") else "")
    cfg = json.dumps({"rowData": rows, "columnDefs": cols,
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True}, "domLayout": "autoHeight"}).replace("</", "<\\/")
    return f"""<h2>Does AI name you? {ans['mentions']} of {ans['answers']} answers ({ans['mention_rate']}%)</h2>{banner}
<div id=grid style="width:100%"></div><p class=note>Table: AG Grid Community (MIT). Click a header to sort; use the menu to filter.</p>
<script src="{AGG}"></script><script>agGrid.createGrid(document.getElementById('grid'),{cfg});</script>"""

# ---------- local demo server ----------
PENDING = {}; ORDERS = {}; ANS = {}
CSS = """<style>body{font:16px/1.5 system-ui;max-width:960px;margin:40px auto;padding:0 16px}input{display:block;width:100%;padding:8px;margin:4px 0 12px}
button{padding:10px 16px}pre{white-space:pre-wrap;background:#f4f4f4;padding:12px}.ex{background:#fff4d6;border-left:4px solid #e0a400;padding:8px 12px}.note{color:#666;font-size:13px}</style>"""
FORM = """<!doctype html><meta charset=utf-8><title>GeoForge Watch</title>""" + CSS + """
<h1>When someone asks AI for a roofer in your town, are you the answer?</h1>
<p>Free: the site check. $29 (PayPal sandbox in this demo): the full report with which competitors AI names instead of you.</p>
<form method=get action=/check><label>Website<input name=url required></label><label>Business name<input name=business required></label>
<label>Trade<input name=trade value=roofer></label><label>City<input name=city value="Birmingham, AL"></label><button>Run free check</button></form>"""

def sdk_buttons(key):
    cid = _creds()[0]
    if not cid: return ""
    return f"""<div id=pp></div><script src="https://www.paypal.com/sdk/js?client-id={html.escape(cid)}&currency=USD&intent=capture"></script>
<script>paypal.Buttons({{createOrder:()=>fetch('/api/orders?k={key}',{{method:'POST'}}).then(r=>r.json()).then(o=>o.id),
onApprove:d=>{{document.getElementById('pp').style.display='none';document.getElementById('cf').style.display='block';location.href='/paid?k={key}&token='+d.orderID}}}}).render('#pp');</script>
<p id=cf style="display:none;font-weight:600">Confirming payment&hellip;</p>"""

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def send(self, body, code=200, ctype="text/html; charset=utf-8"):
        b = body.encode(); self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(b)
    def _order(self, k):
        o = paypal_create_order(return_url=f"http://127.0.0.1:8787/paid?k={k}")
        if o.get("id"): ORDERS[o["id"]] = k
        return o
    def do_POST(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/api/orders" and q.get("k") in PENDING:
            o = self._order(q["k"]); return self.send(json.dumps({"id": o.get("id")}), 201 if o.get("id") else 502, "application/json")
        self.send("not found", 404)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/": return self.send(FORM)
        if u.path == "/check":
            rd = readiness(q["url"]); key = str(len(PENDING) + 1); PENDING[key] = (rd, q)
            ans = gather(q["business"], q.get("trade", "roofer"), q.get("city", ""), os.getenv("GFW_ANSWERS_FILE")); ANS[key] = ans
            rows = "".join(f"<li>{'✔' if c['ok'] else '✖'} {html.escape(c['check'])}: {html.escape(c['detail'])}</li>" for c in rd["checks"])
            return self.send(f"<!doctype html><meta charset=utf-8>{CSS}<h1>Site check: {rd['score']}/100</h1><ul>{rows}</ul>{grid_html(ans, False)}"
                f"<p><a href='/buy?k={key}'><button>Get full report ($29, PayPal {pay_mode()})</button></a></p>{sdk_buttons(key)}<p><a href=/>Back</a></p>")
        if u.path == "/buy":
            k = q.get("k")
            if k not in PENDING: return self.send("unknown check", 404)
            o = self._order(k)
            if not o.get("approve"): return self.send(f"<p>PayPal order failed (HTTP {o.get('http')}, {html.escape(str(o.get('error')))}).</p>", 502)
            loc = o["approve"] + (f"&k={k}" if o["mode"] == "mock" else "")
            self.send_response(302); self.send_header("Location", loc); self.end_headers(); return
        if u.path == "/paid":
            oid = q.get("token", ""); k = ORDERS.get(oid) or (q.get("k") if pay_mode() == "mock" else None)
            if not oid or k not in PENDING: return self.send("<p>Unknown order.</p>", 400)
            t0 = time.time(); cap = paypal_capture(oid); dt = time.time() - t0
            print(f"capture {cap.get('status')} HTTP {cap.get('http')} in {dt:.2f}s", flush=True)
            if cap.get("status") != "COMPLETED":
                return self.send(f"<p>Payment not completed (HTTP {cap.get('http')}, {html.escape(str(cap.get('issue') or cap.get('error')))}). No charge was made and the report stays locked.</p><p><a href='/buy?k={k}'>Try again</a></p>", 402)
            ORDERS.pop(oid, None); rd, form = PENDING[k]
            ans = ANS.get(k) or gather(form["business"], form.get("trade", "roofer"), form.get("city", ""), os.getenv("GFW_ANSWERS_FILE"))
            md = report_md(rd, ans, form["business"], form.get("trade", ""), form.get("city", ""))
            return self.send(f"<!doctype html><meta charset=utf-8>{CSS}<p>Payment: {cap['status']} ({cap['mode']})<br>PayPal order ID: <code>{html.escape(str(cap.get('order_id')))}</code><br>Capture ID: <code>{html.escape(str(cap.get('capture_id')))}</code></p>{grid_html(ans, True)}<pre>{html.escape(md)}</pre>")
        self.send("not found", 404)
    def log_message(self, *a): pass

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("readiness"); a.add_argument("url"); a.add_argument("--json", action="store_true")
    b = sp.add_parser("prompts"); b.add_argument("--trade", required=True); b.add_argument("--city", required=True)
    c = sp.add_parser("answers"); [c.add_argument(x, required=True) for x in ("--business", "--trade", "--city")]; c.add_argument("--answers-file")
    d = sp.add_parser("report"); d.add_argument("url"); [d.add_argument(x, required=True) for x in ("--business", "--trade", "--city")]
    d.add_argument("--answers-file"); d.add_argument("--out")
    sv = sp.add_parser("serve"); sv.add_argument("--mock", action="store_true", help="force mock checkout")
    sv.add_argument("--demo-answers", action="store_true", help="with no AI key, use the EXAMPLE sample-answers.json fixture (labeled)")
    sp.add_parser("paypal-selftest", help="create a sandbox order and try an unapproved capture")
    o = ap.parse_args()
    if o.cmd == "readiness":
        r = readiness(o.url)
        if o.json: print(json.dumps(r, indent=2)); return
        print(f"{r['url']}  score {r['score']}/100")
        for ch in r["checks"]: print(f"  [{'ok' if ch['ok'] else 'FIX'}] {ch['check']}: {ch['detail']}")
    elif o.cmd == "prompts":
        print(json.dumps([{"provider": "chatgpt-free-app", "prompt": p, "answer": "PASTE ANSWER HERE"} for p in prompts(o.trade, o.city)], indent=2))
    elif o.cmd == "answers":
        r = gather(o.business, o.trade, o.city, o.answers_file)
        print(json.dumps(r, indent=2) if r else "No answers: set a provider key or pass --answers-file.")
    elif o.cmd == "report":
        md = report_md(readiness(o.url), gather(o.business, o.trade, o.city, o.answers_file), o.business, o.trade, o.city)
        open(o.out, "w").write(md) if o.out else print(md)
    elif o.cmd == "paypal-selftest":
        c = paypal_create_order(); print("mode", c["mode"], "create HTTP", c["http"], "status", c.get("status"), "approve link", "yes" if c.get("approve") else "no")
        if c.get("approve") and c["mode"] == "sandbox": print("approve host", urllib.parse.urlparse(c["approve"]).netloc)
        if c.get("id"):
            k = paypal_capture(c["id"]); print("unapproved capture HTTP", k["http"], "status", k["status"], k.get("issue", ""))
    elif o.cmd == "serve":
        global FORCE_MOCK
        if o.mock: FORCE_MOCK = True
        if o.demo_answers: os.environ["GFW_DEMO_ANSWERS"] = "1"
        print("GeoForge Watch demo on http://127.0.0.1:8787 (PayPal:", pay_mode(), ")")
        ThreadingHTTPServer(("127.0.0.1", 8787), H).serve_forever()

if __name__ == "__main__": main()
