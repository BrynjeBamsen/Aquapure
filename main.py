import os
import json
import datetime
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import FileResponse, HTMLResponse

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import stripe
import httpx

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

app = FastAPI(title="AquaPure")

# ---------------------------------------------------------------------------
# Server-side prisliste (DKK). Klienten bestemmer ALDRIG priser.
# Vejledende EUR->DKK-omregninger — sæt jeres endelige danske salgspriser her.
# ---------------------------------------------------------------------------
PRODUCTS = {
    "evofilter":         {"name": "EVOfilter",          "dkk": 25744},
    "evofilter-plus":    {"name": "EVOfilter plus",     "dkk": 28341},
    "evofilter-premium": {"name": "EVOfilter premium",  "dkk": 37285},
    "evodescale":        {"name": "EVOdescale",         "dkk": 20508},
    "evoadsorb":         {"name": "EVOadsorb",          "dkk": 21254},
    "evosorb":           {"name": "EVOsorb",            "dkk": 20508},
    "evotransform":      {"name": "EVOtransform",       "dkk": 10660},
    "evocharge":         {"name": "EVOcharge",          "dkk": 2663},
    "evobooster":        {"name": "EVObooster",         "dkk": 12301},
    "ew111":             {"name": "Vandhane EW111",     "dkk": 1708},
    "ew312":             {"name": "Vandhane EW312",     "dkk": 2604},
    "ew411":             {"name": "Vandhane EW411",     "dkk": 3573},
    # Reservefiltre (kan købes enkeltvis ELLER som årligt abonnement)
    "rf-evofilter":      {"name": "Reservefilter EVOfilter",  "dkk": 4456},
    "rf-evoadsorb":      {"name": "Reservefilter EVOadsorb",  "dkk": 4461},
    "rf-evodescale":     {"name": "Reservefilter EVOdescale", "dkk": 3573},
    "rf-evosorb":        {"name": "Reservefilter EVOsorb",    "dkk": 3573},
    "rf-evodrink":       {"name": "Reservefilter EVOdrink",   "dkk": 1231},
}

# System -> tilhørende filterabonnement (årligt). EVOfilter er udeladt:
# dets skifteinterval er 4–7 år, og Stripe-abonnementer kan højst løbe pr. 3 år.
SUBSCRIPTION_MAP = {
    "evodescale": "rf-evodescale",
    "evoadsorb":  "rf-evoadsorb",
    "evosorb":    "rf-evosorb",
}
SUBSCRIBABLE = set(SUBSCRIPTION_MAP.values())


# ---------------------------------------------------------------------------
# Supabase-hjælpere (via REST — ingen ekstra SDK)
# ---------------------------------------------------------------------------
def supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


async def get_user(authorization: str | None):
    """Verificér en Supabase JWT og returnér brugeren, ellers None."""
    if not (supabase_enabled() and authorization and authorization.startswith("Bearer ")):
        return None
    token = authorization.split(" ", 1)[1]
    async with httpx.AsyncClient(timeout=8) as cx:
        r = await cx.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
        )
    return r.json() if r.status_code == 200 else None


async def upsert_profile(user_id: str, email: str, customer_id: str | None):
    """Gem bruger<->Stripe-kunde-koblingen (kræver service key)."""
    if not (supabase_enabled() and SUPABASE_SERVICE_KEY):
        with open("stripe_links.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"user_id": user_id, "email": email,
                                "stripe_customer_id": customer_id}) + "\n")
        return
    payload = {"user_id": user_id, "email": email, "updated_at":
               datetime.datetime.utcnow().isoformat() + "Z"}
    if customer_id:
        payload["stripe_customer_id"] = customer_id
    async with httpx.AsyncClient(timeout=8) as cx:
        await cx.post(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
            json=payload,
        )


async def get_customer_id(user) -> str | None:
    """Find Stripe-kunde for en bruger: først i profiles, ellers via e-mail i Stripe."""
    if supabase_enabled() and SUPABASE_SERVICE_KEY:
        async with httpx.AsyncClient(timeout=8) as cx:
            r = await cx.get(
                f"{SUPABASE_URL}/rest/v1/profiles",
                params={"user_id": f"eq.{user['id']}", "select": "stripe_customer_id"},
                headers={"apikey": SUPABASE_SERVICE_KEY,
                         "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            )
        rows = r.json() if r.status_code == 200 else []
        if rows and rows[0].get("stripe_customer_id"):
            return rows[0]["stripe_customer_id"]
    # fallback: slå op i Stripe på verificeret e-mail
    if stripe.api_key and user.get("email"):
        found = stripe.Customer.list(email=user["email"], limit=1)
        if found.data:
            cid = found.data[0].id
            await upsert_profile(user["id"], user["email"], cid)
            return cid
    return None


# ---------------------------------------------------------------------------
# Checkout-byggesten (ren funktion -> let at teste)
# ---------------------------------------------------------------------------
def build_line_items(items: list, subscriptions: list):
    """Returnerer (line_items, has_subscription). Validerer alt mod serverens katalog."""
    line_items, has_sub = [], False
    for it in items or []:
        product = PRODUCTS.get(it.get("id"))
        try:
            qty = int(it.get("qty", 1))
        except (TypeError, ValueError):
            qty = 1
        if not product or qty < 1:
            continue
        line_items.append({
            "price_data": {
                "currency": "dkk",
                "product_data": {"name": product["name"]},
                "unit_amount": product["dkk"] * 100,
            },
            "quantity": qty,
        })
    for s in subscriptions or []:
        pid = s.get("id")
        if pid not in SUBSCRIBABLE:
            continue
        try:
            qty = int(s.get("qty", 1))
        except (TypeError, ValueError):
            qty = 1
        if qty < 1:
            continue
        product = PRODUCTS[pid]
        has_sub = True
        line_items.append({
            "price_data": {
                "currency": "dkk",
                "product_data": {"name": f"{product['name']} — årligt abonnement"},
                "unit_amount": product["dkk"] * 100,
                "recurring": {"interval": "year", "interval_count": 1},
            },
            "quantity": qty,
        })
    return line_items, has_sub


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return FileResponse("index.html")


@app.get("/api/config")
def config():
    """Offentlig konfiguration til frontenden (anon key er offentlig pr. design)."""
    return {
        "supabaseUrl": SUPABASE_URL,
        "supabaseAnonKey": SUPABASE_ANON_KEY,
        "authEnabled": supabase_enabled(),
    }


@app.post("/api/signup")
async def signup(req: Request):
    data = await req.json()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    zip_code = (data.get("zip") or "").strip()
    if not name or "@" not in email:
        raise HTTPException(status_code=400, detail="Ugyldige data")
    record = {"name": name, "email": email, "zip": zip_code,
              "ts": datetime.datetime.utcnow().isoformat() + "Z"}
    with open("signups.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print("NY VANDANALYSE-FORESPØRGSEL:", record)
    return {"ok": True}


@app.post("/create-checkout-session")
async def create_checkout_session(req: Request, authorization: str | None = Header(None)):
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY mangler")

    data = await req.json()
    line_items, has_sub = build_line_items(data.get("items", []),
                                           data.get("subscriptions", []))
    if not line_items:
        raise HTTPException(status_code=400, detail="Tom kurv")

    user = await get_user(authorization)
    base = str(req.base_url).rstrip("/")

    params = {
        "mode": "subscription" if has_sub else "payment",
        "line_items": line_items,
        "success_url": f"{base}/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base}/?cancelled=1#katalog",
        "shipping_address_collection": {"allowed_countries": ["DK"]},
        "billing_address_collection": "required",
        "locale": "da",
    }
    if user:
        params["client_reference_id"] = user["id"]
        existing = await get_customer_id(user)
        if existing:
            params["customer"] = existing
        else:
            params["customer_email"] = user.get("email")

    session = stripe.checkout.Session.create(**params)
    return {"url": session.url}


@app.post("/create-portal-session")
async def create_portal_session(req: Request, authorization: str | None = Header(None)):
    """Stripe Customer Portal — KUN for verificerede (indloggede) brugere."""
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY mangler")
    user = await get_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Log ind for at administrere abonnement")
    customer_id = await get_customer_id(user)
    if not customer_id:
        raise HTTPException(status_code=404, detail="Ingen Stripe-kunde fundet endnu")
    base = str(req.base_url).rstrip("/")
    portal = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=f"{base}/")
    return {"url": portal.url}


@app.get("/api/me/subscriptions")
async def my_subscriptions(authorization: str | None = Header(None)):
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY mangler")
    user = await get_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Ikke logget ind")
    customer_id = await get_customer_id(user)
    if not customer_id:
        return {"subscriptions": []}
    subs = stripe.Subscription.list(customer=customer_id, status="all", limit=20)
    out = []
    for s in subs.auto_paging_iter():
        for item in s["items"]["data"]:
            out.append({
                "id": s["id"],
                "status": s["status"],
                "product": item["price"]["product"] if isinstance(item["price"].get("product"), str) else "",
                "name": item["price"].get("nickname") or "",
                "amount": (item["price"]["unit_amount"] or 0) / 100,
                "interval": item["price"]["recurring"]["interval"] if item["price"].get("recurring") else None,
                "current_period_end": s.get("current_period_end"),
                "cancel_at_period_end": s.get("cancel_at_period_end"),
            })
    return {"subscriptions": out}


@app.post("/webhook")
async def webhook(req: Request):
    payload = await req.body()
    if WEBHOOK_SECRET:
        sig = req.headers.get("stripe-signature", "")
        try:
            event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook-fejl: {e}")
    else:
        event = json.loads(payload or b"{}")

    etype = event.get("type")
    obj = event.get("data", {}).get("object", {})

    if etype == "checkout.session.completed":
        print("BETALING GENNEMFØRT:", obj.get("id"), "mode:", obj.get("mode"))
        # Kæd Supabase-bruger sammen med Stripe-kunden
        user_id = obj.get("client_reference_id")
        customer_id = obj.get("customer")
        email = (obj.get("customer_details") or {}).get("email") or obj.get("customer_email")
        if user_id and customer_id:
            await upsert_profile(user_id, email or "", customer_id)

    elif etype == "invoice.paid":
        print("ABONNEMENT FORNYET:", obj.get("id"), obj.get("customer"))
        # Her: opret forsendelsesordre på reservefilteret hos jer/3PL.

    elif etype == "invoice.payment_failed":
        print("FORNYELSE FEJLEDE:", obj.get("customer"))

    return {"received": True}


@app.get("/success", response_class=HTMLResponse)
def success():
    return """<!doctype html><html lang="da"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tak for din ordre — AquaPure</title>
<style>
  body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:#062336;color:#fff;
       display:flex;min-height:100vh;align-items:center;justify-content:center;text-align:center;padding:24px}
  .c{max-width:480px}
  .ic{width:84px;height:84px;border:1px solid rgba(63,227,242,.5);display:flex;
      align-items:center;justify-content:center;margin:0 auto 26px}
  h1{font-size:30px;margin:0 0 14px;text-transform:uppercase;letter-spacing:.02em}
  p{color:rgba(255,255,255,.7);font-size:16px;line-height:1.6}
  a{display:inline-block;margin-top:28px;background:#3FE3F2;color:#062336;font-weight:700;
    padding:14px 28px;text-decoration:none;text-transform:uppercase;font-size:13px;letter-spacing:.08em}
</style></head><body><div class="c">
  <div class="ic"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#3FE3F2" stroke-width="2.5">
    <path d="M20 6 9 17l-5-5"/></svg></div>
  <h1>Tak for din ordre</h1>
  <p>Din betaling er gennemført. Har du valgt et filterabonnement, fornyes det automatisk —
     du kan altid se eller opsige det under «Min konto».</p>
  <a href="/">Tilbage til AquaPure</a>
</div></body></html>"""
