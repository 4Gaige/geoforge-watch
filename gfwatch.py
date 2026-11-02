#!/usr/bin/env python3
"""GeoForge Watch: does AI search name your business, and can AI read your site?

Stdlib only. No paid keys required.
  python3 gfwatch.py readiness https://example.com
  python3 gfwatch.py prompts --trade roofer --city "Birmingham, AL"
  python3 gfwatch.py answers --business "CB Built Homes" --trade "painter" --city "Birmingham, AL" [--answers-file pasted.json]
  python3 gfwatch.py report https://example.com --business NAME --trade T --city C [--answers-file F] [--out report.md]
  python3 gfwatch.py serve  (local demo UI on http://127.0.0.1:8787, PayPal sandbox or mock checkout)
Providers for live answers (any one is enough; Gemini has a free tier): GEMINI_API_KEY, OPENAI_API_KEY,
ANTHROPIC_API_KEY or PERPLEXITY_API_KEY. Each engine returns structured JSON naming the businesses it recommends.
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
    facts = {"title": html.unescape(title.group(1).strip()) if title else "", "description": html.unescape(desc.group(1).strip()) if desc else "",
             "phone": phone.group(0) if phone else "", "base": base}
    return {"url": url, "score": score, "checks": checks, "robots": rules, "facts": facts}

def prompts(trade, city):
    return [f"Who are the best {trade}s in {city}?",
            f"I need a {trade} in {city}. Who should I call?",
            f"Recommend a reliable {trade} near {city} with good reviews.",
            f"What {trade} companies in {city} do you recommend for a homeowner?",
            f"Top rated {trade} {city}"]

# ---------- AI engines (provider abstraction, structured JSON output) ----------
ENGINES = [  # (name, env key, default model)
    ("gemini", "GEMINI_API_KEY", "gemini-2.5-flash"),
    ("openai", "OPENAI_API_KEY", "gpt-4o-mini"),
    ("perplexity", "PERPLEXITY_API_KEY", "sonar"),
    ("anthropic", "ANTHROPIC_API_KEY", "claude-3-5-haiku-latest"),
]
def engines_on(): return [e for e in ENGINES if os.getenv(e[1])]
def _model(name, default): return os.getenv(f"GFW_{name.upper()}_MODEL", default)

def _post(url, body, headers, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def llm(engine, prompt, want_json=True):
    """One call to one engine. Returns raw text. Keys come from env and are never logged."""
    name, env, default = engine; key = os.environ[env]; m = _model(name, default)
    if name == "gemini":
        cfg = {"responseMimeType": "application/json"} if want_json else {}
        j = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                  {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": cfg}, {"x-goog-api-key": key})
        return "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
    if name in ("openai", "perplexity"):
        url = "https://api.openai.com/v1/chat/completions" if name == "openai" else "https://api.perplexity.ai/chat/completions"
        body = {"model": m, "messages": [{"role": "user", "content": prompt}]}
        if want_json and name == "openai": body["response_format"] = {"type": "json_object"}
        return _post(url, body, {"Authorization": "Bearer " + key})["choices"][0]["message"]["content"]
    if name == "anthropic":
        j = _post("https://api.anthropic.com/v1/messages", {"model": m, "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]},
                  {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return j["content"][0]["text"]
    raise ValueError(name)

def parse_json(txt):
    txt = (txt or "").strip()
    try: return json.loads(txt)
    except Exception: pass
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0)) if m else None

ASK = """You are answering a homeowner's question the way you normally would as an AI assistant.
Question: {q}
Reply ONLY with JSON: {{"answer": "<your normal answer, plain text>", "businesses": [{{"name": "<business name exactly as you named it>", "reason": "<why, under 15 words>"}}]}}
List every specific business you named in the answer, in the order you named them. Use [] if you named none."""

def ask_live(prompt):
    """Ask every engine that has a key. Returns [{provider, prompt, answer, businesses, extraction}]."""
    out = []
    for e in engines_on():
        try:
            j = parse_json(llm(e, ASK.format(q=prompt))) or {}
            biz = [b.get("name", "").strip() if isinstance(b, dict) else str(b).strip() for b in j.get("businesses", [])]
            out.append({"provider": e[0], "prompt": prompt, "answer": str(j.get("answer", "")),
                        "businesses": [b for b in biz if b], "extraction": "model-json"})
        except Exception as ex:
            out.append({"provider": e[0], "prompt": prompt, "answer": "", "businesses": [], "error": type(ex).__name__})
    return out

def norm(s): return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

def _regex_names(a):
    return [(m[0] or m[1]).strip() for m in re.findall(r"\*\*([^*]{3,60})\*\*|^\s*\d+\.\s+([A-Z][^\n:–-]{2,60})", a, re.M)]

def score_answers(business, records):
    """records: [{provider, prompt, answer, businesses?}] -> mention stats, competitors, per-engine split."""
    b = norm(business); hits = 0; names = {}; eng = {}
    recs = [r for r in records if not r.get("error")]
    for r in recs:
        if "businesses" not in r: r["businesses"] = _regex_names(r["answer"]); r["extraction"] = "regex (pasted answer)"
        r["mentioned"] = b in norm(r["answer"]) or any(b and b in norm(n) for n in r["businesses"]); hits += r["mentioned"]
        r["named"] = [n for n in dict.fromkeys(r["businesses"]) if norm(n) and b not in norm(n)]
        e = eng.setdefault(r["provider"], {"answers": 0, "mentions": 0}); e["answers"] += 1; e["mentions"] += r["mentioned"]
        for n in r["named"]:
            d = names.setdefault(norm(n), {"name": n, "count": 0, "engines": set()}); d["count"] += 1; d["engines"].add(r["provider"])
    total = len(recs); rate = round(100 * hits / total) if total else 0
    comps = sorted(names.values(), key=lambda x: -x["count"])
    comps = [{"name": c["name"], "count": c["count"], "rate": round(100 * c["count"] / total) if total else 0, "engines": sorted(c["engines"])} for c in comps]
    return {"mention_rate": rate, "answers": total, "mentions": hits, "competitors": comps[:15], "engines": eng,
            "errors": [r for r in records if r.get("error")], "records": recs}

def gather(business, trade, city, answers_file=None):
    recs = []
    if answers_file:
        recs = json.load(open(answers_file))
    else:
        for p in prompts(trade, city): recs += ask_live(p)
    if not [r for r in recs if not r.get("error")] and os.getenv("GFW_DEMO_ANSWERS") == "1":
        res = score_answers(business, json.load(open(DEMO_ANSWERS))); res["example"] = True; return res
    if not recs: return None
    return score_answers(business, recs)

# ---------- post-payment agent: ready-to-paste fixes ----------
FIX_ASK = """You write website fixes that help AI assistants understand a local business. Use ONLY the facts below; never invent
addresses, reviews, prices, licenses or awards. Where a fact is unknown, leave a clearly marked placeholder like [ADD STREET ADDRESS].
Business: {business}
Trade: {trade}
City/area: {city}
Website: {url}
Page title: {title}
Meta description: {description}
Phone found on site: {phone}
Competitors AI named instead (context only, do not mention them): {comps}
Reply ONLY with JSON: {{"llms_txt": "<full /llms.txt in the llmstxt.org markdown format>",
"jsonld": <a schema.org LocalBusiness-subtype JSON-LD object>, "faq": [{{"q": "...", "a": "..."}}]}} with 5 FAQ items homeowners ask AI."""

SUBTYPE = {"roofer": "RoofingContractor", "plumber": "Plumber", "electrician": "Electrician", "painter": "HousePainter",
           "hvac": "HVACBusiness", "contractor": "GeneralContractor", "landscaper": "LandscapingBusiness"}

def template_fixes(rd, business, trade, city):
    f = rd.get("facts", {}); phone = f.get("phone") or "[ADD PHONE]"; base = f.get("base") or rd["url"]
    desc = f.get("description") or f"{business} is a {trade} serving {city}."
    llms = f"""# {business}

> {desc}

- Trade: {trade}
- Service area: {city} and nearby communities [EDIT TO YOUR REAL AREA]
- Phone: {phone}
- Website: {base}

## Key pages
- [Home]({base}/): who we are and what we do
- [Services]({base}/services): [EDIT: list your services]
- [Contact]({base}/contact): call {phone} or request a quote
"""
    ld = {"@context": "https://schema.org", "@type": SUBTYPE.get(trade.lower().rstrip("s"), "HomeAndConstructionBusiness"),
          "name": business, "url": base, "telephone": phone, "description": desc,
          "address": {"@type": "PostalAddress", "streetAddress": "[ADD STREET ADDRESS]", "addressLocality": city.split(",")[0].strip(),
                      "addressRegion": city.split(",")[-1].strip() if "," in city else "[ADD STATE]", "addressCountry": "US"},
          "areaServed": city, "sameAs": ["[ADD GOOGLE BUSINESS PROFILE URL]", "[ADD FACEBOOK URL]"]}
    faq = [{"q": f"Who is a good {trade} in {city}?", "a": f"{business} is a {trade} serving {city}. Call {phone} for a quote."},
           {"q": f"How much does a {trade} cost in {city}?", "a": "[EDIT: give a typical price range and what changes it.]"},
           {"q": f"How fast can {business} start?", "a": "[EDIT: typical lead time, e.g. estimates within 2 business days.]"},
           {"q": f"What areas does {business} serve?", "a": f"{city} and nearby communities. [EDIT: list towns.]"},
           {"q": f"Is {business} licensed and insured?", "a": "[EDIT: state license number and insurance, only if true.]"}]
    return {"llms_txt": llms, "jsonld": ld, "faq": faq, "source": "template"}

def make_fixes(rd, ans, business, trade, city):
    """AI-written fixes from the first engine with a key; deterministic template otherwise (labeled)."""
    eng = engines_on()
    if eng:
        f = rd.get("facts", {}); comps = ", ".join(c["name"] for c in (ans or {}).get("competitors", [])[:5]) or "none"
        try:
            j = parse_json(llm(eng[0], FIX_ASK.format(business=business, trade=trade, city=city, url=rd["url"], comps=comps,
                                                      title=f.get("title") or "unknown", description=f.get("description") or "unknown", phone=f.get("phone") or "unknown")))
            if j and j.get("llms_txt") and j.get("jsonld"):
                return {"llms_txt": j["llms_txt"], "jsonld": j["jsonld"], "faq": j.get("faq", []), "source": "ai:" + eng[0][0]}
        except Exception as ex:
            print("fix generation failed:", type(ex).__name__, flush=True)
    return template_fixes(rd, business, trade, city)

def report_md(rd, ans, business, trade, city):
    L = [f"# GeoForge Watch report: {business}", f"{trade} · {city}", ""]
    if ans:
        L += ["## Does AI name you?"]
        if ans.get("example"):
            L += ["> **EXAMPLE DATA, NOT A LIVE MEASUREMENT.** No AI provider key is set, so this section uses the invented demo fixture sample-answers.json. Competitor names below are made up.", ""]
        L += [f"Named in {ans['mentions']} of {ans['answers']} AI answers ({ans['mention_rate']}%)."]
        L += [f"- {e}: {v['mentions']}/{v['answers']}" for e, v in ans["engines"].items()] + [""]
        if ans["competitors"]:
            L += ["Named instead of you most often:"] + [f"- {c['name']} ({c['count']}x, {', '.join(c['engines'])})" for c in ans["competitors"]] + [""]
    else:
        L += ["## Does AI name you?", "Not measured: no provider key set and no pasted answers. Run `prompts` and paste answers into a JSON file.", ""]
    L += [f"## Can AI read your site? {rd['score']}/100", "", "| Check | Result | Detail |", "|---|---|---|"]
    L += [f"| {c['check']} | {'pass' if c['ok'] else 'fix'} | {c['detail']} |" for c in rd["checks"]]
    L += ["", "## Fix list (in order)"] + [f"{i+1}. {c['fix']}" for i, c in enumerate([c for c in rd["checks"] if not c["ok"]])]
    L += ["", "AI answers change day to day. This is a snapshot, not a guarantee of ranking. Made by GeoForge Watch, an AI tool."]
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


# ---------- PayPal Subscriptions (sandbox): weekly watch, $29/month ----------
DATA = os.path.join(HERE, "data"); os.makedirs(os.path.join(DATA, "reports"), exist_ok=True)
WATCH_PRICE = "29.00"
def _jload(p, d):
    try: return json.load(open(p))
    except Exception: return d
def _jsave(p, v):
    tmp = p + ".tmp"; json.dump(v, open(tmp, "w"), indent=1, default=list); os.replace(tmp, p)

def paypal_plan_id():
    """Create (once) a sandbox catalog product + monthly billing plan; cache the plan ID in data/paypal-plan.json."""
    tok = paypal_token()
    if not tok: return "MOCK-PLAN"
    p = os.path.join(DATA, "paypal-plan.json"); c = _jload(p, {})
    if c.get("plan_id"): return c["plan_id"]
    code, prod = _pp("POST", "/v1/catalogs/products", tok, {"name": "GeoForge Watch weekly watch", "type": "SERVICE", "category": "SOFTWARE",
        "description": "Re-asks AI engines the same homeowner questions every week and reports changes."})
    if code not in (200, 201): return None
    code, plan = _pp("POST", "/v1/billing/plans", tok, {"product_id": prod["id"], "name": "Weekly watch, monthly billing",
        "billing_cycles": [{"frequency": {"interval_unit": "MONTH", "interval_count": 1}, "tenure_type": "REGULAR", "sequence": 1, "total_cycles": 0,
                            "pricing_scheme": {"fixed_price": {"value": WATCH_PRICE, "currency_code": "USD"}}}],
        "payment_preferences": {"auto_bill_outstanding": True, "payment_failure_threshold": 1}})
    if code not in (200, 201): return None
    _jsave(p, {"product_id": prod["id"], "plan_id": plan["id"]}); return plan["id"]

def paypal_subscription(sid):
    """Server-side check of a subscription the buyer approved. Watch starts only on ACTIVE."""
    tok = paypal_token()
    if not tok: return {"mode": "mock", "status": "ACTIVE", "id": sid}
    code, j = _pp("GET", f"/v1/billing/subscriptions/{urllib.parse.quote(sid)}", tok)
    return {"mode": "sandbox", "http": code, "status": j.get("status", "UNKNOWN"), "id": j.get("id"), "plan_id": j.get("plan_id"),
            "next": (j.get("billing_info") or {}).get("next_billing_time")}

# ---------- history (re-check tracking over time) ----------
def host_of(url): return urllib.parse.urlparse(url if url.startswith("http") else "https://" + url).netloc.lower().removeprefix("www.")
def record_history(rd, ans):
    p = os.path.join(DATA, "history.json"); h = _jload(p, {}); k = host_of(rd["url"])
    h.setdefault(k, []).append({"ts": time.strftime("%Y-%m-%d %H:%M"), "score": rd["score"], "rate": ans["mention_rate"] if ans else None,
                                "example": bool(ans and ans.get("example"))})
    h[k] = h[k][-26:]; _jsave(p, h); return h[k]
def history_for(url): return _jload(os.path.join(DATA, "history.json"), {}).get(host_of(url), [])

# ---------- AG Grid Community (MIT) tables ----------
AGG = "https://cdn.jsdelivr.net/npm/ag-grid-community@36.2.0/dist/ag-grid-community.min.js"
GRID_JS = """<script src="%s"></script><script>
const gfwTheme=()=>agGrid.themeQuartz.withParams({fontFamily:'"IBM Plex Sans",system-ui,sans-serif',fontSize:14,headerFontWeight:600,
  headerBackgroundColor:'#f3f0e8',borderColor:'#e2ddd2',accentColor:'#e8730c',foregroundColor:'#14181f',rowBorder:true,wrapperBorderRadius:10,spacing:7});
function gfwGrid(id, cfg){ const mx=cfg.heatMax||1;
  (cfg.columnDefs||[]).forEach(c=>{ if(c.bar) c.cellRenderer=p=>'<span class=bar><i style="width:'+(p.value||0)+'%%"></i></span> '+(p.value||0)+'%%';
  if(c.yes) c.cellRenderer=p=>'<span class="chip '+(p.value==='Yes'?'ok':'no')+'">'+p.value+'</span>';
  if(c.heat){ c.cellStyle=p=>{const a=(p.value||0)/mx;return {background:'rgba(19,35,63,'+(0.06+0.86*a).toFixed(2)+')',color:a>.45?'#fff':'#14181f',fontVariantNumeric:'tabular-nums',textAlign:'center'}};
    c.valueFormatter=p=>(p.value||0)+'%%'; }
  if(c.you) c.cellRenderer=p=>p.data.you?'<b>'+p.value+'</b> <span class="chip you">You</span>':p.value; });
  delete cfg.heatMax; cfg.theme=gfwTheme(); cfg.defaultColDef={sortable:true,filter:true,resizable:true}; cfg.domLayout='autoHeight';
  window['api_'+id]=agGrid.createGrid(document.getElementById(id),cfg); }
function gfwCsv(id,name){ window['api_'+id].exportDataAsCsv({fileName:name}); }
function gfwCopy(id,btn){ navigator.clipboard.writeText(document.getElementById(id).innerText).then(()=>{btn.textContent='Copied';setTimeout(()=>btn.textContent='Copy',1500)}); }
function gfwShare(btn){ navigator.clipboard.writeText(btn.dataset.url).then(()=>{btn.textContent='Link copied';setTimeout(()=>btn.textContent='Copy share link',1800)}); }
</script>""" % AGG

def _cfg(rows, cols, **extra): return json.dumps({"rowData": rows, "columnDefs": cols, **extra}).replace("</", "<\\/")
E = html.escape

def example_banner(ans):
    return ("<div class=ex><b>Example data, not a live measurement.</b> No AI key is set yet, so the answers below come from the invented "
            "fixture <code>sample-answers.json</code>. Engine and business names are made up. Add a free <code>GEMINI_API_KEY</code> for live answers.</div>") if ans and ans.get("example") else ""

# ---------- design v3: family H (one metaphor: "the answer"), refs in docs/design-v3.md ----------
CSS = """<link rel=preconnect href=https://fonts.googleapis.com><link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,650&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap"><style>
:root{--navy:#13233f;--orange:#e8730c;--ink:#14181f;--mute:#5d6470;--line:#e2ddd2;--paper:#f7f5f0;--card:#fff;--ok:#1d7148;--no:#b0401c;--soft:#f3f0e8}
*{box-sizing:border-box}html{-webkit-font-smoothing:antialiased}body{margin:0;font:16px/1.6 "IBM Plex Sans",system-ui,sans-serif;color:var(--ink);background:var(--paper)}
a{color:var(--navy)}code,.mono{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.86em}
.top{display:flex;justify-content:space-between;align-items:center;padding:14px 32px;border-bottom:1px solid var(--line);background:var(--paper);position:sticky;top:0;z-index:5}
.brand{display:flex;gap:10px;align-items:center;font-weight:600;text-decoration:none;color:var(--ink)}.brand i{width:14px;height:14px;background:var(--orange);display:inline-block;border-radius:3px;box-shadow:5px 5px 0 var(--navy)}
.pills{display:flex;gap:8px}.pill{font:500 12px/1 "IBM Plex Mono",monospace;padding:6px 9px;border:1px solid var(--line);border-radius:99px;color:var(--mute);background:#fff}.pill.warn{border-color:#f0c48f;color:#8a4a0a;background:#fff6eb}
.wrap{max-width:1160px;margin:0 auto;padding:0 32px}
h1,h2,.display{font-family:Fraunces,Georgia,serif;font-weight:650;letter-spacing:-.015em;color:var(--navy)}
h1{font-size:clamp(38px,4.4vw,60px);line-height:1.04;margin:0 0 20px;max-width:12ch}h2{font-size:30px;line-height:1.15;margin:0 0 6px}h3{font-size:16px;margin:0 0 6px}
.eyebrow{font:500 12px/1 "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--orange);margin-bottom:14px}
.lede{font-size:19px;color:#353b45;max-width:36em}.mute{color:var(--mute)}.small{font-size:14px}
.hero{display:grid;grid-template-columns:1.1fr .9fr;gap:64px;align-items:start;padding:64px 0 56px}.hero .answer{margin-top:44px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px}
form.check{margin-top:28px;display:grid;grid-template-columns:1fr 1fr;gap:12px 14px;max-width:560px}
form.check .full{grid-column:1/-1}label{display:block;font-weight:500;font-size:13px;color:var(--mute)}
input{display:block;width:100%;padding:12px 13px;margin-top:5px;border:1px solid #cfc9bc;border-radius:8px;font:16px "IBM Plex Sans",sans-serif;background:#fff}
input:focus{outline:2px solid var(--orange);outline-offset:1px;border-color:transparent}
button,.btn{font:600 15px "IBM Plex Sans",sans-serif;cursor:pointer;border-radius:8px;text-decoration:none;display:inline-flex;align-items:center;gap:8px}
.primary{background:var(--orange);color:#fff;border:0;padding:13px 22px}.primary:hover{background:#cf6609}
.ghost{background:#fff;border:1px solid #cfc9bc;color:var(--ink);padding:8px 13px;font-size:14px}.ghost:hover{border-color:var(--navy)}
/* the answer object */
.answer{background:#fff;border:1px solid var(--line);border-radius:16px;box-shadow:0 24px 48px -28px rgba(19,35,63,.35);overflow:hidden}
.answer .q{background:var(--navy);color:#fff;padding:16px 20px;font-size:15px}.answer .q b{color:#ffb36b;font-weight:500}
.answer ol{margin:0;padding:8px 20px 4px 20px;list-style:none;counter-reset:n}.answer li{counter-increment:n;display:flex;gap:14px;align-items:center;padding:13px 0;border-bottom:1px solid var(--soft)}
.answer li:before{content:counter(n);font:500 13px "IBM Plex Mono",monospace;color:var(--mute);width:18px}
.answer li>span:first-child{flex:1}.answer li.you{color:var(--no)}.answer li.you span{text-decoration:line-through;text-decoration-color:var(--no)}
.answer .cap{padding:12px 20px 16px;font-size:13px;color:var(--mute)}
.steps{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:24px 0 64px}
.steps div{padding:26px 26px 28px 0}.steps div+div{padding-left:26px;border-left:1px solid var(--line)}.steps .n{font:500 13px "IBM Plex Mono",monospace;color:var(--orange)}
.offer{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:72px}.offer .price{font-family:Fraunces,serif;font-size:40px;color:var(--navy);font-weight:650;line-height:1}
.offer ul{padding-left:18px;margin:12px 0 0}.offer li{margin:4px 0}.offer .card.hl{border:2px solid var(--navy)}
/* report */
.rhead{padding:44px 0 22px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:24px;align-items:flex-end}
.rhead h1{font-size:46px;margin:0 0 8px;max-width:none}.actions{display:flex;gap:8px;flex-wrap:wrap}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--line)}
.kpi{padding:22px 22px 22px 0}.kpi+.kpi{padding-left:22px;border-left:1px solid var(--line)}.kpi .v{font-family:Fraunces,serif;font-size:44px;font-weight:650;color:var(--navy);line-height:1.05;font-variant-numeric:tabular-nums}
.kpi .v small{font-size:20px;color:var(--mute)}.kpi .l{font-size:13px;color:var(--mute)}.kpi .d{font-size:13px;margin-top:4px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:40px;align-items:start}.layout.one{grid-template-columns:minmax(0,1fr)}
aside{position:sticky;top:76px;margin-top:40px}
section.s{padding:40px 0 8px}section.s .num{font:500 12px "IBM Plex Mono",monospace;color:var(--orange);letter-spacing:.08em}
.sh{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:16px}.sh p{margin:0}
.rank{list-style:none;margin:0;padding:0}.rank li{display:grid;grid-template-columns:28px minmax(0,1fr) 180px 48px;gap:12px;align-items:center;padding:11px 0;border-bottom:1px solid var(--line)}
.rank .i{font:500 13px "IBM Plex Mono",monospace;color:var(--mute)}.rank .b{height:8px;background:var(--soft);border-radius:4px}.rank .b i{display:block;height:8px;background:var(--navy);border-radius:4px}
.rank li.you{background:#fff6eb;margin:0 -12px;padding:11px 12px;border-radius:8px;border-bottom-color:transparent}.rank li.you .b i{background:var(--orange)}.rank .p{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.blur{filter:blur(5px);user-select:none}
.chip{display:inline-block;padding:2px 9px;border-radius:99px;font:600 12px "IBM Plex Sans",sans-serif;white-space:nowrap}.chip.ok{background:#e1f1e8;color:var(--ok)}.chip.no{background:#f9e5dc;color:var(--no)}
.chip.you{background:var(--orange);color:#fff}.chip.p1{background:var(--no);color:#fff}.chip.p2{background:#f6d8b8;color:#7a3d05}.chip.p3{background:var(--soft);color:var(--mute)}.chip.lo{background:#e1f1e8;color:var(--ok)}.chip.md{background:#fbecd6;color:#7a4a05}.chip.hi{background:#f9e5dc;color:var(--no)}
.bar{display:inline-block;width:90px;height:8px;background:var(--soft);border-radius:4px;vertical-align:middle}.bar i{display:block;height:8px;background:var(--orange);border-radius:4px}
.engines{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:20px}
.eng .v{font-family:Fraunces,serif;font-size:34px;color:var(--navy);font-weight:650}.eng .dots{display:flex;gap:5px;margin-top:8px}.eng .dots i{width:16px;height:16px;border-radius:4px;background:var(--soft);border:1px solid var(--line)}.eng .dots i.on{background:var(--orange);border-color:var(--orange)}
table.plan{width:100%;border-collapse:collapse;font-size:15px;background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
table.plan th{text-align:left;font:600 12px "IBM Plex Mono",monospace;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);background:var(--soft);padding:10px 14px}
table.plan td{padding:14px;border-top:1px solid var(--line);vertical-align:top}table.plan td.t b{display:block}table.plan td.t span{color:var(--mute);font-size:14px}
.quotes{display:grid;grid-template-columns:1fr 1fr;gap:14px}.quote{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px}
.quote .meta{display:flex;justify-content:space-between;gap:10px;font-size:13px;color:var(--mute);margin-bottom:8px}.quote .qq{font-weight:600;margin-bottom:8px}.quote p{margin:0;font-size:15px;color:#353b45}
mark{background:#e9edf5;color:var(--navy);padding:0 3px;border-radius:3px}mark.you{background:#ffe2c4;color:#7a3d05}
.ex{background:#fff6eb;border:1px solid #f0c48f;padding:12px 16px;margin:0 0 18px;border-radius:10px;font-size:14px;color:#6b3a06}
.locked{position:relative;border:1px dashed #cfc9bc;border-radius:12px;padding:22px;background:repeating-linear-gradient(135deg,#fff,#fff 10px,#faf8f3 10px,#faf8f3 20px)}
.order .li{display:flex;justify-content:space-between;padding:8px 0;font-size:15px}.order .tot{border-top:1px solid var(--line);margin-top:6px;padding-top:12px;font-weight:600;font-size:17px}
.order ul{padding-left:18px;margin:8px 0 14px;font-size:14px;color:#353b45}.order li{margin:3px 0}
.receipt{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;font-size:14px}
pre.code{background:#0f1b30;color:#e8edf5;padding:16px;border-radius:10px;overflow:auto;font:13px/1.55 "IBM Plex Mono",monospace;white-space:pre-wrap;margin:0;max-height:360px}
.fix{margin:0 0 18px}.fixhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
ul.checks{list-style:none;padding:0;margin:0}ul.checks li{display:grid;grid-template-columns:56px 1fr;gap:12px;align-items:baseline;padding:12px 0;border-bottom:1px solid var(--line)}ul.checks .d{color:var(--mute);font-size:14px}
.trend{display:flex;gap:28px;align-items:center;flex-wrap:wrap}.trend>div:last-child{min-width:300px}.gridhead{display:flex;justify-content:space-between;align-items:center;margin:22px 0 10px}
footer{border-top:1px solid var(--line);margin-top:64px;padding:22px 0 40px;color:var(--mute);font-size:13px}
@media (max-width:900px){.hero,.layout,.offer,.quotes,.steps{grid-template-columns:1fr}.kpis{grid-template-columns:1fr 1fr}aside{position:static}.steps div+div{border-left:0;padding-left:0}}
@media print{.top,aside,.actions,.noprint,footer .noprint{display:none!important}body{background:#fff}.layout{grid-template-columns:1fr}section.s{break-inside:avoid-page}pre.code{max-height:none;background:#f3f0e8;color:#14181f}}
</style>"""

def ai_label():
    on = [e[0] for e in engines_on()]
    return ", ".join(on) if on else ("example data" if os.getenv("GFW_DEMO_ANSWERS") == "1" else "no key")

def page(title, body):
    ai_warn = "" if engines_on() else " warn"
    return (f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{E(title)}</title>{CSS}{GRID_JS}"
            f"<div class=top><a class=brand href=/><i></i>GeoForge Watch</a><div class=pills><span class=pill>PayPal · {pay_mode()}</span>"
            f"<span class='pill{ai_warn}'>AI · {E(ai_label())}</span></div></div>"
            f"<div class=wrap>{body}<footer>GeoForge Watch is an AI tool. AI answers change from day to day, so each report is a snapshot, not a ranking promise. "
            f"Open source, MIT license. Tables by AG Grid Community.</footer></div></html>")

# ---------- report pieces ----------
def ranking(ans, business, full):
    """Every business AI named, ranked by share of answers. You are always shown; others unlock with the report."""
    rows = [{"name": business, "rate": ans["mention_rate"], "you": True}] + [{"name": c["name"], "rate": c["rate"], "you": False} for c in ans["competitors"]]
    rows.sort(key=lambda r: (-r["rate"], not r["you"])); out = []
    for i, r in enumerate(rows[:8], 1):
        nm = E(r["name"]) + (" <span class='chip you'>You</span>" if r["you"] else "")
        if not full and not r["you"]: nm = f"<span class=blur>{'Locked business name'[:max(8, len(r['name']))]}</span>"
        out.append(f"<li class='{'you' if r['you'] else ''}'><span class=i>{i}</span><span>{nm}</span><span class=b><i style='width:{r['rate']}%'></i></span><span class=p>{r['rate']}%</span></li>")
    return "<ul class=rank>" + "".join(out) + "</ul>"

def your_rank(ans, business):
    rates = sorted({c["rate"] for c in ans["competitors"]} | {ans["mention_rate"]}, reverse=True)
    pos = 1 + sum(1 for c in ans["competitors"] if c["rate"] > ans["mention_rate"])
    return pos, 1 + len(ans["competitors"])

def engine_cards(ans):
    out = []
    for e, v in ans["engines"].items():
        recs = [r for r in ans["records"] if r["provider"] == e]
        dots = "".join(f"<i class='{'on' if r['mentioned'] else ''}' title='{E(r['prompt'])}'></i>" for r in recs)
        pct = round(100 * v["mentions"] / v["answers"]) if v["answers"] else 0
        out.append(f"<div class='card eng'><div class='mono small mute'>{E(e)}</div><div class=v>{pct}%</div>"
                   f"<div class='small mute'>named you in {v['mentions']} of {v['answers']} answers</div><div class=dots>{dots}</div></div>")
    return "<div class=engines>" + "".join(out) + "</div>"

def heat_grid(ans, business):
    """Business x engine matrix: share of each engine's answers that name the business (AG Grid, heat-colored)."""
    engines = list(ans["engines"])
    def share(name, e, you=False):
        recs = [r for r in ans["records"] if r["provider"] == e]
        n = sum(1 for r in recs if (r["mentioned"] if you else name in r["named"]))
        return round(100 * n / len(recs)) if recs else 0
    rows = [{"business": business, "you": True, **{e: share(business, e, True) for e in engines}}]
    rows += [{"business": c["name"], "you": False, **{e: share(c["name"], e) for e in engines}} for c in ans["competitors"]]
    for r in rows: r["avg"] = round(sum(r[e] for e in engines) / len(engines)) if engines else 0
    cols = [{"field": "business", "headerName": "Business", "flex": 2, "minWidth": 200, "pinned": "left", "you": True}] + \
           [{"field": e, "headerName": e, "flex": 1, "minWidth": 120, "heat": True} for e in engines] + \
           [{"field": "avg", "headerName": "All engines", "flex": 1, "minWidth": 120, "heat": True, "sort": "desc"}]
    return (f"<div class=gridhead><h3>Share of each engine's answers, by business</h3><button class='ghost noprint' onclick=\"gfwCsv('gh','geoforge-engine-matrix.csv')\">Export CSV</button></div>"
            f"<div id=gh></div><script>gfwGrid('gh',{_cfg(rows, cols, heatMax=max([r['avg'] for r in rows] + [r[e] for r in rows for e in engines] + [1]))})</script>")

def answers_grid(ans, full):
    rows = [{"engine": r["provider"], "query": r["prompt"], "mentioned": "Yes" if r["mentioned"] else "No",
             "named": ", ".join(r["named"]) if full else f"{len(r['named'])} names, locked"} for r in ans["records"]]
    cols = [{"field": "engine", "headerName": "Engine", "width": 170, "pinned": "left"},
            {"field": "query", "headerName": "Question asked", "flex": 2, "minWidth": 260, "wrapText": True, "autoHeight": True},
            {"field": "mentioned", "headerName": "Named you", "width": 120, "yes": True},
            {"field": "named", "headerName": "Named instead", "flex": 2, "minWidth": 220, "wrapText": True, "autoHeight": True}]
    csv = "<button class='ghost noprint' onclick=\"gfwCsv('ga','geoforge-ai-answers.csv')\">Export CSV</button>" if full else ""
    return f"<div class=gridhead><h3>All answers</h3>{csv}</div><div id=ga></div><script>gfwGrid('ga',{_cfg(rows, cols)})</script>"

def hl(text, names, business):
    t = E(text)
    for n in sorted(set(names), key=len, reverse=True):
        if n: t = re.sub(re.escape(E(n)), lambda m: f"<mark>{m.group(0)}</mark>", t, flags=re.I)
    return re.sub(re.escape(E(business)), lambda m: f"<mark class=you>{m.group(0)}</mark>", t, flags=re.I) if business else t

def quotes(ans, business):
    """Full answer text as readable cards (replaces the cramped excerpt column)."""
    out = []
    for r in ans["records"]:
        badge = "<span class='chip ok'>Named you</span>" if r["mentioned"] else "<span class='chip no'>Not named</span>"
        out.append(f"<div class=quote><div class=meta><span class=mono>{E(r['provider'])}</span>{badge}</div><div class=qq>{E(r['prompt'])}</div>"
                   f"<p>{hl(r['answer'][:700], r['named'], business)}</p></div>")
    return "<div class=quotes>" + "".join(out) + "</div>"

PLAN_META = {  # check -> (impact, effort, where, why)
    "AI crawlers allowed in robots.txt": ("High", "Low", "robots.txt", "Blocked crawlers can't read the site at all."),
    "Business schema (JSON-LD)": ("High", "Low", "Homepage <head>", "Gives AI your name, phone, area and trade as structured facts."),
    "llms.txt present": ("Medium", "Low", "/llms.txt", "A plain summary written for AI readers."),
    "Question-style content": ("High", "Medium", "FAQ page", "AI answers questions; pages that answer them get quoted."),
    "Phone number in page text": ("Medium", "Low", "Header or footer", "AI can't read a number inside an image."),
    "Meta description": ("Low", "Low", "Homepage <head>", "Often reused as the one-line summary of you."),
    "Page title": ("Medium", "Low", "Homepage <head>", "Should say the trade and the city."),
    "Homepage loads": ("High", "Medium", "Hosting", "Nothing else matters if the page doesn't load."),
}
def action_plan(rd, ans, business, full):
    items = []
    for c in rd["checks"]:
        if c["ok"]: continue
        imp, eff, where, why = PLAN_META.get(c["check"], ("Medium", "Medium", "Website", ""))
        items.append({"t": c["fix"], "why": why, "imp": imp, "eff": eff, "where": where})
    if ans and ans["competitors"] and ans["mention_rate"] < (ans["competitors"][0]["rate"] or 0):
        top = ans["competitors"][0]
        items.append({"t": f"Close the gap with the most-named business ({top['rate']}% vs your {ans['mention_rate']}%).",
                      "why": "Get listed on the same review sites and directories the answers draw on; ask recent customers for reviews that name the city.",
                      "imp": "High", "eff": "High", "where": "Reviews and listings"})
    items.append({"t": "Re-check in two weeks, after the fixes are live.", "why": "AI engines re-read sites slowly; track whether the rate moves.",
                  "imp": "Medium", "eff": "Low", "where": "GeoForge Watch"})
    score = {"High": 3, "Medium": 2, "Low": 1}
    items.sort(key=lambda x: (-(score[x["imp"]] * 2 - score[x["eff"]])))
    rows = []
    for i, x in enumerate(items):
        pr = "P1" if i < 2 else "P2" if i < 4 else "P3"
        cls = {"High": "hi", "Medium": "md", "Low": "lo"}
        why = E(x["why"]) if full or i < 2 else "<span class=blur>Unlock the report for the reason and the paste-ready fix</span>"
        rows.append(f"<tr><td><span class='chip {pr.lower()}'>{pr}</span></td><td class=t><b>{E(x['t'])}</b><span>{why}</span></td>"
                    f"<td><span class='chip {cls[x['imp']] if x['imp']!='Low' else 'p3'}'>{x['imp']}</span></td><td><span class='chip {cls[x['eff']]}'>{x['eff']}</span></td><td class=small>{E(x['where'])}</td></tr>")
    return "<table class=plan><tr><th>Priority</th><th>Do this</th><th>Impact</th><th>Effort</th><th>Where</th></tr>" + "".join(rows) + "</table>"

def checks_list(rd):
    return "<ul class=checks>" + "".join(
        f"<li><span class='chip {'ok' if c['ok'] else 'no'}'>{'Pass' if c['ok'] else 'Fix'}</span><div><b>{E(c['check'])}</b> "
        f"<span class=d>· {E(c['detail'])}</span></div></li>" for c in rd["checks"]) + "</ul>"

def spark(vals, w=240, h=72, col="#13233f"):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2: vals = vals * 2 if vals else [0, 0]
    step = w / (len(vals) - 1); pts = " ".join(f"{i*step:.1f},{h-4-(h-8)*v/100:.1f}" for i, v in enumerate(vals))
    return (f"<svg width={w} height={h} viewBox='0 0 {w} {h}' role=img aria-label='trend'><polyline points='{pts}' fill=none stroke='{col}' stroke-width=2.5 stroke-linejoin=round />"
            f"<line x1=0 y1={h-4} x2={w} y2={h-4} stroke='#e2ddd2' /><line x1=0 y1=4 x2={w} y2=4 stroke='#e2ddd2' stroke-dasharray='3 4' />"
            + "".join(f"<circle cx={i*step:.1f} cy={h-4-(h-8)*v/100:.1f} r=3.5 fill='{col}' />" for i, v in enumerate(vals)) + "</svg>")

def trend(hist):
    if not hist: return "<p class=mute>No checks recorded yet.</p>"
    last = hist[-1]; first = hist[0]
    d = (last["score"] - first["score"]); dr = None if last["rate"] is None or first["rate"] is None else last["rate"] - first["rate"]
    ex = " (example AI data)" if any(h.get("example") for h in hist) else ""
    def delta(x): return "<span class=mute>no change</span>" if not x else f"<b style='color:{'var(--ok)' if x>0 else 'var(--no)'}'>{x:+d}</b>"
    return (f"<div class='card trend'><div><div class='mono small mute'>Site readiness</div>{spark([h['score'] for h in hist])}<div class=small>{last['score']}/100 · {delta(d)}</div></div>"
            f"<div><div class='mono small mute'>AI mention rate{ex}</div>{spark([h['rate'] for h in hist], col='#e8730c')}<div class=small>{last['rate'] if last['rate'] is not None else '–'}% · {delta(dr)}</div></div>"
            f"<div style='flex:1'><table class=plan style='font-size:13px'><tr><th>Checked</th><th>Site</th><th>AI rate</th></tr>"
            + "".join(f"<tr><td class=mono>{E(x['ts'])}</td><td>{x['score']}</td><td>{'–' if x['rate'] is None else str(x['rate']) + '%'}</td></tr>" for x in hist[-5:][::-1])
            + f"</table><p class='small mute' style='margin:6px 0 0'>{len(hist)} check{'s' if len(hist)!=1 else ''} on record. Scale 0–100; dashed line is 100.</p></div></div>")

def fixes_html(fx):
    src = fx["source"]
    lab = (f"<p class='small mute'>Written for this site by the AI agent ({E(src[3:])}) from facts found on the page. Check it before you publish.</p>" if src.startswith("ai:")
           else "<div class=ex><b>Template, not AI-written.</b> No AI key is set, so these are filled in from a fixed template using facts found on the site. "
                "Replace every [BRACKETED] placeholder before you publish.</div>")
    ld = json.dumps(fx["jsonld"], indent=2) if not isinstance(fx["jsonld"], str) else fx["jsonld"]
    faq = "\n\n".join(f"Q: {f.get('q','')}\nA: {f.get('a','')}" for f in fx["faq"] if isinstance(f, dict))
    def blk(i, title, where, body):
        return (f"<div class=fix><div class=fixhead><h3>{title}</h3><button class='ghost noprint' onclick=\"gfwCopy('{i}',this)\">Copy</button></div>"
                f"<p class='small mute' style='margin:0 0 8px'>{where}</p><pre class=code id={i}>{E(body)}</pre></div>")
    return (lab + blk("fx1", "llms.txt", "Save as /llms.txt at the root of your site.", fx["llms_txt"]) +
            blk("fx2", "LocalBusiness schema (JSON-LD)", "Paste inside &lt;script type=\"application/ld+json\"&gt; in your homepage &lt;head&gt;.", ld) +
            blk("fx3", "FAQ copy", "Add to your homepage or an FAQ page as plain HTML text.", faq))

def sec(n, title, sub, body, right=""):
    return f"<section class=s><div class=num>{n}</div><div class=sh><div><h2>{title}</h2><p class=mute>{sub}</p></div>{right}</div>{body}</section>"

def report_view(rd, ans, q, full, key=None, fx=None, cap=None, share=None, hist=None, sub=None):
    biz, trade, city = q["business"], q.get("trade", ""), q.get("city", "")
    when = time.strftime("%b %-d, %Y")
    acts = ""
    if full:
        acts = ("<div class='actions noprint'>" + (f"<button class=ghost data-url='{E(share)}' onclick='gfwShare(this)'>Copy share link</button>" if share else "")
                + "<button class=ghost onclick='window.print()'>Save as PDF</button>"
                + f"<a class='btn ghost' href='/check?{urllib.parse.urlencode({'url': rd['url'], 'business': biz, 'trade': trade, 'city': city})}'>Re-check</a></div>")
    head = (f"<div class=rhead><div><div class=eyebrow>{'AI visibility report' if full else 'Free check'} · {when}</div><h1>{E(biz)}</h1>"
            f"<div class=mute><span class=mono>{E(host_of(rd['url']))}</span> · {E(trade)} · {E(city)}</div></div>{acts}</div>")
    if ans:
        pos, n = your_rank(ans, biz)
        top = ans["competitors"][0]["rate"] if ans["competitors"] else 0
        kpi_ai = (f"<div class=kpi><div class=v>{ans['mention_rate']}<small>%</small></div><div class=l>of AI answers name you</div><div class='d mute'>{ans['mentions']} of {ans['answers']} answers</div></div>"
                  f"<div class=kpi><div class=v>#{pos}<small> of {n}</small></div><div class=l>your place among businesses named</div><div class='d mute'>top business: {top}%</div></div>"
                  f"<div class=kpi><div class=v>{len(ans['engines'])}</div><div class=l>AI engines asked</div><div class='d mute'>{len(prompts(trade, city))} homeowner questions each</div></div>")
    else:
        kpi_ai = "<div class=kpi><div class=v>–</div><div class=l>AI answers not measured</div></div>"
    fails = sum(1 for c in rd["checks"] if not c["ok"])
    kpis = (f"<div class=kpis><div class=kpi><div class=v>{rd['score']}<small>/100</small></div><div class=l>can AI read your site</div>"
            f"<div class='d mute'>{fails} fix{'es' if fails != 1 else ''} found</div></div>{kpi_ai}</div>")
    main = ""
    if cap:
        main += (f"<div class='card' style='margin-top:28px;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap'><div><span class='chip ok'>Payment {E(cap['status'])}</span> "
                 f"<b style='margin-left:6px'>$29.00 USD</b> <span class=mute>· PayPal {E(cap['mode'])}</span></div><div class=receipt><span class=mute>Order</span><code>{E(str(cap.get('order_id')))}</code>"
                 f"<span class=mute>Capture</span><code>{E(str(cap.get('capture_id')))}</code></div></div>")
    if ans:
        main += sec("01", "Who AI names when a homeowner asks", f"Share of {ans['answers']} answers that named each business.",
                    example_banner(ans) + ranking(ans, biz, full) + ("" if full else "<p class='small mute'>Names of other businesses unlock with the full report.</p>"))
        main += sec("02", "Engine by engine", "Each square is one question. Orange means that engine named you.",
                    engine_cards(ans) + (heat_grid(ans, biz) if full else "<div class=locked><b>Business × engine matrix</b><p class='mute small' style='margin:4px 0 0'>See which engine favors which competitor, with CSV export. Included in the full report.</p></div>"))
    main += sec("03" if ans else "01", "Action plan", "Ordered by impact for the effort. Do the P1 items first.", action_plan(rd, ans, biz, full))
    if full and fx: main += sec("04", "Fixes, ready to paste", "Written for this site. Copy each block into place.", fixes_html(fx))
    if ans:
        main += sec("05" if full else "04", "What the engines said", "The full answers, with every business name marked. Yours is orange.",
                    (quotes(ans, biz) if full else "") + answers_grid(ans, full))
    main += sec("06" if full else "05", "Site check", f"{len(rd['checks'])} things AI crawlers look for on {E(host_of(rd['url']))}.", f"<div class=card>{checks_list(rd)}</div>")
    main += sec("07" if full else "06", "Over time", "Each check is saved, so you can see whether your fixes moved the numbers.", trend(hist or []))
    side = ""
    if not full and key:
        side = (f"<aside><div class='card order'><div class=eyebrow style='margin-bottom:10px'>Full report</div><div class=li><span>AI visibility report<br><span class='small mute'>{E(host_of(rd['url']))}</span></span><span>$29.00</span></div>"
                f"<div class='li tot'><span>Total</span><span>$29.00 USD</span></div><ul><li>Every business AI named, and how often</li><li>Business × engine matrix, CSV export</li>"
                f"<li>llms.txt, schema and FAQ written for your site</li><li>Share link and PDF</li></ul>{checkout(key)}</div>"
                f"<div class='card' style='margin-top:14px'><b>Weekly watch</b> <span class=mute>· $29/month</span><p class='small mute' style='margin:6px 0 12px'>Same questions every week, with the trend line above. Cancel anytime in PayPal.</p>"
                f"<a class='btn ghost' href='/watch?k={key}'>Set up weekly watch</a></div></aside>")
    if full and sub is not None:
        side = ""
    layout = "layout" if side else "layout one"
    return head + kpis + f"<div class='{layout}'><div>{main}</div>{side}</div>"

# ---------- local demo server ----------
PENDING = {}; ORDERS = {}; ANS = {}; PORT = 8787
LANDING = """<div class=hero><div><div class=eyebrow>For local trades · AI search check</div>
<h1>Ask AI for a roofer in your town. Are you in the answer?</h1>
<p class=lede>Homeowners now ask ChatGPT who to call. GeoForge Watch asks AI engines the same questions a homeowner would and shows you who gets named. Then it writes the fixes for your site.</p>
<form method=get action=/check class=check><label class=full>Website<input name=url required placeholder="yourcompany.com"></label>
<label class=full>Business name<input name=business required placeholder="As customers know it"></label>
<label>Trade<input name=trade value=roofer></label><label>City<input name=city value="Birmingham, AL"></label>
<div class=full><button class=primary>Run the free check</button> <span class='small mute' style='margin-left:10px'>About 20 seconds. No sign-up.</span></div></form></div>
<div><div class=answer><div class=q>“Who's the best <b>roofer</b> in <b>Birmingham, AL</b>?”</div><ol>
<li><span>A roofer the AI likes</span><span class='chip p3'>named</span></li><li><span>Another roofer</span><span class='chip p3'>named</span></li>
<li><span>A third roofer</span><span class='chip p3'>named</span></li><li class=you><span>Your business</span><span class='chip no'>missing</span></li></ol>
<div class=cap>Illustration. Your check uses real answers when an AI key is set; until then it's labeled example data.</div></div></div></div>
<div class=steps><div><div class=n>01</div><h3>Read your site the way AI does</h3><p class='mute small'>Crawler rules, llms.txt, business schema, phone in text, FAQ-style content. Scored out of 100.</p></div>
<div><div class=n>02</div><h3>Ask the engines</h3><p class='mute small'>Five questions a homeowner would type, to every engine with a key. We count who gets named.</p></div>
<div><div class=n>03</div><h3>Fix it, then watch it</h3><p class='mute small'>Paste-ready llms.txt, schema and FAQ for your site. Re-check to see the numbers move.</p></div></div>
<div class=offer><div class=card><div class=eyebrow>Free</div><div class=price>$0</div><ul><li>Site readiness score out of 100</li><li>Your mention rate and your place in line</li></ul></div>
<div class='card hl'><div class=eyebrow>Full report</div><div class=price>$29</div><ul><li>Every business named, by engine</li><li>The full action plan</li><li>llms.txt, schema and FAQ written for your site</li><li>A share link and a PDF</li></ul><p class='small mute'>One payment through PayPal.</p></div>
<div class=card><div class=eyebrow>Weekly watch</div><div class=price>$29<span style='font-size:18px'>/mo</span></div><ul><li>Same questions every week</li><li>Trend line for both scores</li><li>Cancel anytime in PayPal</li></ul><p class='small mute'>PayPal Subscriptions.</p></div></div>"""

def checkout(key):
    cid = _creds()[0]
    if not cid:
        return f"<p class='small mute'>PayPal is in mock mode (no sandbox keys). Nothing is charged.</p><a class='btn primary' href='/buy?k={key}'>Pay $29 (mock checkout)</a>"
    return f"""<div id=pp></div><script src="https://www.paypal.com/sdk/js?client-id={E(cid)}&currency=USD&intent=capture"></script>
<script>paypal.Buttons({{style:{{shape:'rect',height:44}},createOrder:()=>fetch('/api/orders?k={key}',{{method:'POST'}}).then(r=>r.json()).then(o=>o.id),
onApprove:d=>{{document.getElementById('pp').style.display='none';document.getElementById('cf').style.display='block';location.href='/paid?k={key}&token='+d.orderID}}}}).render('#pp');</script>
<p id=cf style="display:none;font-weight:600">Confirming payment&hellip;</p><p class='small mute' style='margin:8px 0 0'>PayPal sandbox: test money only.</p>"""

def watch_page(key, rd, q):
    plan = paypal_plan_id(); cid = _creds()[0]; host = E(host_of(rd["url"]))
    if not plan: btn = "<p>Couldn't set up the PayPal plan right now. Try again in a minute.</p>"
    elif not cid: btn = f"<p class='small mute'>PayPal is in mock mode. Nothing is charged.</p><a class='btn primary' href='/watching?k={key}&sid=MOCK-SUB'>Start weekly watch (mock)</a>"
    else:
        btn = (f"<div id=ps></div><script src='https://www.paypal.com/sdk/js?client-id={E(cid)}&vault=true&intent=subscription'></script>"
               f"<script>paypal.Buttons({{style:{{shape:'rect',label:'subscribe',height:44}},createSubscription:(d,a)=>a.subscription.create({{plan_id:'{E(plan)}',custom_id:'{E(host)}'}}),"
               f"onApprove:d=>{{location.href='/watching?k={key}&sid='+d.subscriptionID}}}}).render('#ps');</script><p class='small mute'>PayPal sandbox: test money only.</p>")
    return (f"<div class=rhead><div><div class=eyebrow>Weekly watch</div><h1>Keep an eye on {E(q['business'])}</h1><div class=mute><span class=mono>{host}</span></div></div></div>"
            f"<div class=layout><div>{sec('01', 'What you get', 'Every Monday, the same questions go to every engine with a key.', '<ul><li>Mention rate and your place, week by week</li><li>Site readiness re-scored</li><li>A note when you drop out of an answer you used to be in</li></ul>')}"
            f"{sec('02', 'So far', 'Checks on record for this site.', trend(history_for(rd['url'])))}</div>"
            f"<aside><div class='card order'><div class=li><span>Weekly watch<br><span class='small mute'>billed monthly</span></span><span>$29.00</span></div><div class='li tot'><span>Per month</span><span>$29.00 USD</span></div>"
            f"<p class='small mute'>Cancel anytime from your PayPal account.</p>{btn}<p class='small mute mono' style='margin-top:10px'>plan {E(str(plan))}</p></div></aside></div>")

def save_report(blob):
    tok = base64.urlsafe_b64encode(os.urandom(9)).decode().rstrip("=")
    _jsave(os.path.join(DATA, "reports", tok + ".json"), blob); return tok

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def send(self, body, code=200, ctype="text/html; charset=utf-8"):
        b = body.encode(); self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(b)
    def base(self): return f"http://{self.headers.get('Host') or '127.0.0.1:%d' % PORT}"
    def _order(self, k):
        o = paypal_create_order(return_url=f"{self.base()}/paid?k={k}", cancel_url=f"{self.base()}/")
        if o.get("id"): ORDERS[o["id"]] = k
        return o
    def do_POST(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/api/orders" and q.get("k") in PENDING:
            o = self._order(q["k"]); return self.send(json.dumps({"id": o.get("id")}), 201 if o.get("id") else 502, "application/json")
        self.send("not found", 404)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/": return self.send(page("GeoForge Watch: are you in the AI answer?", LANDING))
        if u.path == "/check":
            if not q.get("url") or not q.get("business"): return self.send(page("GeoForge Watch", LANDING), 400)
            rd = readiness(q["url"]); key = str(len(PENDING) + 1); PENDING[key] = (rd, q)
            ans = gather(q["business"], q.get("trade", "roofer"), q.get("city", ""), os.getenv("GFW_ANSWERS_FILE")); ANS[key] = ans
            hist = record_history(rd, ans)
            return self.send(page(f"Free check · {q['business']}", report_view(rd, ans, q, False, key=key, hist=hist)))
        if u.path == "/watch":
            k = q.get("k")
            if k not in PENDING: return self.send("unknown check", 404)
            rd, form = PENDING[k]; return self.send(page("Weekly watch · GeoForge Watch", watch_page(k, rd, form)))
        if u.path == "/watching":
            k = q.get("k"); s = paypal_subscription(q.get("sid", ""))
            ok = s.get("status") in ("ACTIVE", "APPROVED")
            body = (f"<div class=rhead><div><div class=eyebrow>Weekly watch</div><h1>{'You are on the watch list.' if ok else 'Subscription not active.'}</h1>"
                    f"<div class=mute>PayPal subscription <code>{E(str(s.get('id')))}</code> · status <b>{E(str(s.get('status')))}</b> · {E(s['mode'])}</div></div></div>"
                    + (f"<p class=lede>The first weekly check runs next Monday.</p><p><a class='btn ghost' href='/watch?k={E(k or '')}'>Back</a></p>" if ok else "<p>Nothing was charged.</p>"))
            return self.send(page("Weekly watch", body), 200 if ok else 402)
        if u.path == "/buy":
            k = q.get("k")
            if k not in PENDING: return self.send("unknown check", 404)
            o = self._order(k)
            if not o.get("approve"): return self.send(f"<p>PayPal order failed (HTTP {o.get('http')}, {E(str(o.get('error')))}).</p>", 502)
            loc = o["approve"] + (f"&k={k}" if o["mode"] == "mock" else "")
            self.send_response(302); self.send_header("Location", loc); self.end_headers(); return
        if u.path == "/paid":
            oid = q.get("token", ""); k = ORDERS.get(oid) or (q.get("k") if pay_mode() == "mock" else None)
            if not oid or k not in PENDING: return self.send("<p>Unknown order.</p>", 400)
            t0 = time.time(); cap = paypal_capture(oid); dt = time.time() - t0
            print(f"capture {cap.get('status')} HTTP {cap.get('http')} in {dt:.2f}s", flush=True)
            if cap.get("status") != "COMPLETED":
                return self.send(page("Payment not completed", f"<div class=card style='margin-top:40px'><p>Payment not completed (HTTP {cap.get('http')}, {E(str(cap.get('issue') or cap.get('error')))}). Nothing was charged and the report stays locked.</p><p><a href='/buy?k={k}'>Try again</a></p></div>"), 402)
            ORDERS.pop(oid, None); rd, form = PENDING[k]; biz, trade, city = form["business"], form.get("trade", ""), form.get("city", "")
            ans = ANS.get(k) or gather(biz, trade, city, os.getenv("GFW_ANSWERS_FILE"))
            fx = make_fixes(rd, ans, biz, trade, city)
            tok = save_report({"rd": rd, "ans": ans, "q": form, "fx": fx, "cap": cap})
            self.send_response(303); self.send_header("Location", f"/r/{tok}"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if u.path.startswith("/r/"):
            tok = re.sub(r"[^A-Za-z0-9_-]", "", u.path[3:]); b = _jload(os.path.join(DATA, "reports", tok + ".json"), None)
            if not b: return self.send(page("Not found", "<p style='margin-top:40px'>No report at this link.</p>"), 404)
            return self.send(page(f"AI visibility report · {b['q']['business']}", report_view(b["rd"], b["ans"], b["q"], True, fx=b["fx"], cap=b["cap"],
                             share=f"{self.base()}/r/{tok}", hist=history_for(b["rd"]["url"]))))
        self.send("not found", 404)
    def log_message(self, *a): pass

def main():
    global FORCE_MOCK, PORT
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("readiness"); a.add_argument("url"); a.add_argument("--json", action="store_true")
    b = sp.add_parser("prompts"); b.add_argument("--trade", required=True); b.add_argument("--city", required=True)
    c = sp.add_parser("answers"); [c.add_argument(x, required=True) for x in ("--business", "--trade", "--city")]; c.add_argument("--answers-file")
    d = sp.add_parser("report"); d.add_argument("url"); [d.add_argument(x, required=True) for x in ("--business", "--trade", "--city")]
    d.add_argument("--answers-file"); d.add_argument("--out")
    f = sp.add_parser("fixes", help="print llms.txt, JSON-LD and FAQ for a site"); f.add_argument("url"); [f.add_argument(x, required=True) for x in ("--business", "--trade", "--city")]
    sv = sp.add_parser("serve"); sv.add_argument("--mock", action="store_true", help="force mock checkout")
    sv.add_argument("--demo-answers", action="store_true", help="with no AI key, use the EXAMPLE sample-answers.json fixture (labeled)")
    sv.add_argument("--port", type=int, default=int(os.getenv("PORT", "8787"))); sv.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    sp.add_parser("paypal-selftest", help="create a sandbox order and try an unapproved capture")
    sp.add_parser("engines", help="list AI engines that have a key set (names only)")
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
    elif o.cmd == "fixes":
        fx = make_fixes(readiness(o.url), None, o.business, o.trade, o.city); print(json.dumps(fx, indent=2))
    elif o.cmd == "engines":
        print("engines with keys:", ", ".join(e[0] for e in engines_on()) or "none")
    elif o.cmd == "paypal-selftest":
        c = paypal_create_order(); print("mode", c["mode"], "create HTTP", c["http"], "status", c.get("status"), "approve link", "yes" if c.get("approve") else "no")
        if c.get("approve") and c["mode"] == "sandbox": print("approve host", urllib.parse.urlparse(c["approve"]).netloc)
        if c.get("id"):
            k = paypal_capture(c["id"]); print("unapproved capture HTTP", k["http"], "status", k["status"], k.get("issue", ""))
    elif o.cmd == "serve":
        if o.mock: FORCE_MOCK = True
        if o.demo_answers: os.environ["GFW_DEMO_ANSWERS"] = "1"
        PORT = o.port
        print(f"GeoForge Watch demo on http://{o.host}:{PORT} (PayPal: {pay_mode()}, AI: {', '.join(e[0] for e in engines_on()) or 'none'})", flush=True)
        ThreadingHTTPServer((o.host, PORT), H).serve_forever()

if __name__ == "__main__": main()
