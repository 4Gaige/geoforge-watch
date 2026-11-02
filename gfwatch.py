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


# ---------- AG Grid Community (MIT) tables ----------
AGG = "https://cdn.jsdelivr.net/npm/ag-grid-community@36.2.0/dist/ag-grid-community.min.js"
GRID_JS = """<script src="%s"></script><script>
function gfwGrid(id, cfg){ (cfg.columnDefs||[]).forEach(c=>{ if(c.bar) c.cellRenderer=p=>'<span class=bar><i style="width:'+(p.value||0)+'%%"></i></span> '+(p.value||0)+'%%';
  if(c.yes) c.cellRenderer=p=>'<span class="chip '+(p.value==='Yes'?'ok':'no')+'">'+p.value+'</span>'; });
  cfg.defaultColDef={sortable:true,filter:true,resizable:true}; cfg.domLayout='autoHeight';
  window['api_'+id]=agGrid.createGrid(document.getElementById(id),cfg); }
function gfwCsv(id,name){ window['api_'+id].exportDataAsCsv({fileName:name}); }
function gfwCopy(id,btn){ navigator.clipboard.writeText(document.getElementById(id).innerText).then(()=>{btn.textContent='Copied';setTimeout(()=>btn.textContent='Copy',1500)}); }
</script>""" % AGG

def _cfg(rows, cols): return json.dumps({"rowData": rows, "columnDefs": cols}).replace("</", "<\\/")

def example_banner(ans):
    return ("<div class=ex><strong>EXAMPLE DATA, NOT A LIVE MEASUREMENT.</strong> No AI provider key is set, so these rows come from the "
            "invented fixture sample-answers.json. Engine and business names are made up.</div>") if ans and ans.get("example") else ""

def answers_grid(ans, full):
    """Engine x query rows. The preview hides competitor names (they are the paid part)."""
    if not ans: return "<p class=note>AI answers not measured: no provider key set.</p>"
    rows = [{"engine": r["provider"], "query": r["prompt"], "mentioned": "Yes" if r["mentioned"] else "No",
             "named": ", ".join(r["named"]) if full else f"{len(r['named'])} named, unlock to see",
             "extraction": r.get("extraction", ""), "answer": r["answer"][:400] if full else ""} for r in ans["records"]]
    cols = [{"field": "engine", "headerName": "Engine", "width": 165, "pinned": "left"},
            {"field": "query", "headerName": "What the homeowner asked", "flex": 2, "wrapText": True, "autoHeight": True},
            {"field": "mentioned", "headerName": "Named you?", "width": 120, "yes": True},
            {"field": "named", "headerName": "Named instead", "flex": 2, "wrapText": True, "autoHeight": True}]
    if full:
        cols += [{"field": "answer", "headerName": "Answer excerpt", "flex": 3, "wrapText": True, "autoHeight": True},
                 {"field": "extraction", "headerName": "Names read by", "width": 140}]
    csv = "<button class=ghost onclick=\"gfwCsv('ga','geoforge-ai-answers.csv')\">Export CSV</button>" if full else ""
    return f"<div class=gridhead><h3>Every answer, by engine</h3>{csv}</div><div id=ga></div><script>gfwGrid('ga',{_cfg(rows, cols)})</script>"

def competitor_grid(ans, business):
    """One row per business AI named, grouped across engines and queries, with mention rate and per-engine counts."""
    if not ans: return ""
    engines = list(ans["engines"]); total = ans["answers"]
    you = {"business": business + " (you)", "count": ans["mentions"], "rate": ans["mention_rate"]}
    for e in engines: you[e] = ans["engines"][e]["mentions"]
    rows = [you]
    for c in ans["competitors"]:
        r = {"business": c["name"], "count": c["count"], "rate": c["rate"]}
        for e in engines: r[e] = sum(1 for x in ans["records"] if x["provider"] == e and c["name"] in x["named"])
        rows.append(r)
    cols = [{"field": "business", "headerName": "Business", "flex": 2, "pinned": "left"},
            {"field": "rate", "headerName": f"Mention rate (of {total})", "width": 200, "bar": True, "sort": "desc"},
            {"field": "count", "headerName": "Answers", "width": 110}] + \
           [{"field": e, "headerName": e, "width": 165, "headerTooltip": f"answers from {e} naming this business"} for e in engines]
    return (f"<div class=gridhead><h3>Who AI names, grouped by business</h3><button class=ghost onclick=\"gfwCsv('gc','geoforge-competitors.csv')\">Export CSV</button></div>"
            f"<div id=gc></div><script>gfwGrid('gc',{_cfg(rows, cols)})</script>")

# ---------- design (taste refs in commit note: GeoForge navy + orange, report-not-dashboard, plain trusted checkout) ----------
CSS = """<style>
:root{--navy:#13233f;--orange:#e8730c;--ink:#1c2430;--mute:#5b6675;--line:#e3e6ea;--paper:#fbfaf7;--ok:#1f7a4d;--no:#b4401b}
*{box-sizing:border-box}body{margin:0;font:16px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--paper)}
header{background:var(--navy);color:#fff;padding:14px 24px;display:flex;justify-content:space-between;align-items:center}
header b{letter-spacing:.02em}header span{font-size:13px;opacity:.8}main{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:30px;line-height:1.2;margin:8px 0 12px;color:var(--navy)}h2{color:var(--navy);margin:34px 0 10px;font-size:22px}h3{margin:0;font-size:17px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:20px;margin:16px 0}
.row{display:flex;gap:16px;flex-wrap:wrap}.row>.card{flex:1;min-width:280px;margin:0}
label{display:block;font-weight:600;font-size:14px;margin-top:10px}input{display:block;width:100%;padding:10px;margin:4px 0;border:1px solid #c9ced6;border-radius:6px;font-size:16px}
button{font:inherit;cursor:pointer;border-radius:6px}.primary{background:var(--orange);color:#fff;border:0;padding:12px 20px;font-weight:700;margin-top:14px}
.ghost{background:#fff;border:1px solid #c9ced6;padding:5px 12px;font-size:13px}
.gridhead{display:flex;justify-content:space-between;align-items:center;margin:22px 0 8px}
.ex{background:#fff4e0;border-left:4px solid var(--orange);padding:10px 14px;margin:10px 0;border-radius:4px}
.note{color:var(--mute);font-size:13px}.price{font-size:28px;font-weight:800;color:var(--navy)}
.chip{display:inline-block;padding:1px 10px;border-radius:99px;font-size:13px;font-weight:600}.chip.ok{background:#e3f3ea;color:var(--ok)}.chip.no{background:#fbe7df;color:var(--no)}
.bar{display:inline-block;width:90px;height:8px;background:#eef0f3;border-radius:4px;vertical-align:middle}.bar i{display:block;height:8px;background:var(--orange);border-radius:4px}
ul.checks{list-style:none;padding:0;margin:0}ul.checks li{padding:8px 0;border-bottom:1px solid var(--line)}ul.checks .d{color:var(--mute);font-size:14px}
pre.code{background:#0f1b30;color:#e8edf5;padding:14px;border-radius:8px;overflow:auto;font-size:13px;white-space:pre-wrap;margin:0}
.fix{margin:14px 0}.fixhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.receipt{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;font-size:14px}.receipt code{font-size:13px}
.gauge{text-align:center}.gauge .lbl{font-size:14px;color:var(--mute);margin-top:-8px}
table.t{border-collapse:collapse;width:100%;font-size:14px}table.t td,table.t th{border-bottom:1px solid var(--line);padding:8px;text-align:left}
footer{color:var(--mute);font-size:13px;margin-top:40px}
</style>"""

def ai_label():
    on = [e[0] for e in engines_on()]
    return ", ".join(on) if on else ("no key, example data" if os.getenv("GFW_DEMO_ANSWERS") == "1" else "no key")

def page(title, body):
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>{CSS}{GRID_JS}"
            f"<header><b>GeoForge Watch</b><span>PayPal checkout: {pay_mode()} · AI: {ai_label()}</span></header>"
            f"<main>{body}<footer>GeoForge Watch is an AI tool. AI answers change day to day; this is a snapshot, not a ranking guarantee. Open source, MIT license.</footer></main>")

def gauge(value, label):
    """Semicircle SVG gauge, 0-100."""
    import math
    v = max(0, min(100, value)); a = math.pi * (1 - v / 100); x, y = 100 + 80 * math.cos(a), 100 - 80 * math.sin(a)
    col = "#1f7a4d" if v >= 70 else "#e8730c" if v >= 40 else "#b4401b"
    return (f"<div class=gauge><svg viewBox='0 0 200 115' width=200 role=img aria-label='{html.escape(label)} {v} of 100'>"
            f"<path d='M20 100 A80 80 0 0 1 180 100' fill=none stroke='#eef0f3' stroke-width=16 stroke-linecap=round />"
            f"<path d='M20 100 A80 80 0 0 1 {x:.1f} {y:.1f}' fill=none stroke='{col}' stroke-width=16 stroke-linecap=round />"
            f"<text x=100 y=96 text-anchor=middle font-size=34 font-weight=800 fill='#13233f'>{v}</text></svg><div class=lbl>{html.escape(label)}</div></div>")

def checks_list(rd):
    return "<ul class=checks>" + "".join(
        f"<li><span class='chip {'ok' if c['ok'] else 'no'}'>{'Pass' if c['ok'] else 'Fix'}</span> <b>{html.escape(c['check'])}</b> "
        f"<span class=d>{html.escape(c['detail'])}</span>{'' if c['ok'] else '<div class=d>→ ' + html.escape(c['fix']) + '</div>'}</li>" for c in rd["checks"]) + "</ul>"

def fixes_html(fx):
    src = fx["source"]
    lab = (f"<p class=note>Written for this site by the AI agent ({html.escape(src[3:])}) from the facts found on the page. Review before publishing.</p>" if src.startswith("ai:")
           else "<div class=ex><strong>TEMPLATE, NOT AI-WRITTEN.</strong> No AI provider key is set, so these fixes are filled in from a fixed template "
                "using the facts found on the site. Replace every [BRACKETED] placeholder before publishing.</div>")
    ld = json.dumps(fx["jsonld"], indent=2) if not isinstance(fx["jsonld"], str) else fx["jsonld"]
    faq = "\n\n".join(f"Q: {f.get('q','')}\nA: {f.get('a','')}" for f in fx["faq"] if isinstance(f, dict))
    def blk(i, title, where, body):
        return (f"<div class=fix><div class=fixhead><h3>{title}</h3><button class=ghost onclick=\"gfwCopy('{i}',this)\">Copy</button></div>"
                f"<p class=note>{where}</p><pre class=code id={i}>{html.escape(body)}</pre></div>")
    return ("<h2>Your fixes, ready to paste</h2>" + lab +
            blk("fx1", "llms.txt", "Save as /llms.txt at the root of your site.", fx["llms_txt"]) +
            blk("fx2", "LocalBusiness schema (JSON-LD)", "Paste inside &lt;script type=\"application/ld+json\"&gt; in your homepage &lt;head&gt;.", ld) +
            blk("fx3", "FAQ copy", "Add to your homepage or an FAQ page, in plain HTML text.", faq))

# ---------- local demo server ----------
PENDING = {}; ORDERS = {}; ANS = {}; PORT = 8787
FORM = """<h1>When someone asks AI for a roofer in your town, are you the answer?</h1>
<p>Run a free check of your website. For <b>$29</b>, paid with PayPal, get the full report: which businesses ChatGPT-style assistants name instead of you, broken down by engine, plus fixes written for your site that you can paste in.</p>
<form method=get action=/check class=card><label>Website<input name=url required placeholder="https://yourcompany.com"></label><label>Business name<input name=business required></label>
<div class=row style="gap:12px"><div style="flex:1"><label>Trade<input name=trade value=roofer></label></div><div style="flex:1"><label>City<input name=city value="Birmingham, AL"></label></div></div>
<button class=primary>Run free check</button></form>"""

def checkout(key):
    cid = _creds()[0]
    if not cid:  # mock mode only: no PayPal, one plain button
        return f"<p class=note>PayPal is in MOCK mode (no sandbox keys). Nothing is charged.</p><a href='/buy?k={key}'><button class=primary>Pay $29 (mock checkout)</button></a>"
    return f"""<div id=pp></div><script src="https://www.paypal.com/sdk/js?client-id={html.escape(cid)}&currency=USD&intent=capture"></script>
<script>paypal.Buttons({{createOrder:()=>fetch('/api/orders?k={key}',{{method:'POST'}}).then(r=>r.json()).then(o=>o.id),
onApprove:d=>{{document.getElementById('pp').style.display='none';document.getElementById('cf').style.display='block';location.href='/paid?k={key}&token='+d.orderID}}}}).render('#pp');</script>
<p id=cf style="display:none;font-weight:600">Confirming payment&hellip;</p><p class=note>PayPal sandbox: test money only.</p>"""

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def send(self, body, code=200, ctype="text/html; charset=utf-8"):
        b = body.encode(); self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(b)
    def _order(self, k):
        o = paypal_create_order(return_url=f"http://127.0.0.1:{PORT}/paid?k={k}", cancel_url=f"http://127.0.0.1:{PORT}/")
        if o.get("id"): ORDERS[o["id"]] = k
        return o
    def do_POST(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/api/orders" and q.get("k") in PENDING:
            o = self._order(q["k"]); return self.send(json.dumps({"id": o.get("id")}), 201 if o.get("id") else 502, "application/json")
        self.send("not found", 404)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/": return self.send(page("GeoForge Watch", FORM))
        if u.path == "/check":
            rd = readiness(q["url"]); key = str(len(PENDING) + 1); PENDING[key] = (rd, q)
            ans = gather(q["business"], q.get("trade", "roofer"), q.get("city", ""), os.getenv("GFW_ANSWERS_FILE")); ANS[key] = ans
            g2 = gauge(ans["mention_rate"], "AI answers that name you (%)") if ans else ""
            body = (f"<h1>{html.escape(q['business'])}</h1><p class=note>{html.escape(rd['url'])} · {html.escape(q.get('trade',''))} · {html.escape(q.get('city',''))}</p>"
                    f"<div class=row><div class=card>{gauge(rd['score'], 'Can AI read your site? (score)')}</div><div class=card>{g2 or '<p class=note>AI answers not measured.</p>'}</div>"
                    f"<div class=card><div class=price>$29</div><p>Full report: who AI names instead of you, by engine, with CSV export, plus ready-to-paste fixes for your site.</p>{checkout(key)}</div></div>"
                    f"<h2>Site check</h2><div class=card>{checks_list(rd)}</div><h2>Does AI name you?</h2>{example_banner(ans)}{answers_grid(ans, False)}"
                    f"<p class=note>Tables: AG Grid Community (MIT). Sort by clicking a header; filter from the column menu.</p><p><a href=/>← New check</a></p>")
            return self.send(page("Free check · GeoForge Watch", body))
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
                return self.send(page("Payment not completed", f"<div class=card><p>Payment not completed (HTTP {cap.get('http')}, {html.escape(str(cap.get('issue') or cap.get('error')))}). No charge was made and the report stays locked.</p><p><a href='/buy?k={k}'>Try again</a></p></div>"), 402)
            ORDERS.pop(oid, None); rd, form = PENDING[k]; biz, trade, city = form["business"], form.get("trade", ""), form.get("city", "")
            ans = ANS.get(k) or gather(biz, trade, city, os.getenv("GFW_ANSWERS_FILE"))
            fx = make_fixes(rd, ans, biz, trade, city)
            eng = "".join(f"<li>{html.escape(e)}: named you in {v['mentions']} of {v['answers']}</li>" for e, v in (ans or {}).get("engines", {}).items())
            body = (f"<div class=card><b>Payment {html.escape(cap['status'])}</b> · $29.00 USD · PayPal {html.escape(cap['mode'])}"
                    f"<div class=receipt style='margin-top:8px'><span>PayPal order ID</span><code>{html.escape(str(cap.get('order_id')))}</code>"
                    f"<span>Capture ID</span><code>{html.escape(str(cap.get('capture_id')))}</code></div></div>"
                    f"<h1>GeoForge Watch report: {html.escape(biz)}</h1><p class=note>{html.escape(rd['url'])} · {html.escape(trade)} · {html.escape(city)}</p>"
                    f"<div class=row><div class=card>{gauge(rd['score'], 'Can AI read your site? (score)')}</div>"
                    + (f"<div class=card>{gauge(ans['mention_rate'], 'AI answers that name you (%)')}<ul class=note>{eng}</ul></div>" if ans else "") + "</div>"
                    f"<h2>Does AI name you?</h2>{example_banner(ans)}{competitor_grid(ans, biz)}{answers_grid(ans, True)}"
                    f"{fixes_html(fx)}<h2>Site check</h2><div class=card>{checks_list(rd)}</div>")
            return self.send(page("Full report · GeoForge Watch", body))
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
