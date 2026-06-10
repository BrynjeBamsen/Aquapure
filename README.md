# AquaPure — testklar webshop (FastAPI + Stripe)

En komplet, delbar testversion: rigtig URL der virker på alle enheder, plus et
fungerende købsflow i **Stripe test mode** og et signup-endpoint.

## Filer
- `index.html` — landingssiden/webshoppen (samme som før, men "Til kassen" og signup kalder nu backend)
- `main.py` — FastAPI: serverer siden + `/create-checkout-session`, `/api/signup`, `/webhook`, `/success`
- `requirements.txt`, `Procfile`, `.env.example`

---

## 1. Stripe (2 min)
1. Opret/log ind på Stripe → slå **"Test mode"** til (kontakten øverst til højre).
2. Developers → API keys → kopiér din **Secret key** (`sk_test_...`).

## 2. Kør lokalt (valgfrit)
```bash
pip install -r requirements.txt
export STRIPE_SECRET_KEY=sk_test_...     # Windows: set STRIPE_SECRET_KEY=...
uvicorn main:app --reload
```
Åbn http://localhost:8000

## 3. Deploy på Railway
1. Læg filerne i et Git-repo og vælg **New Project → Deploy from GitHub** (eller upload).
2. Under **Variables**: tilføj `STRIPE_SECRET_KEY = sk_test_...`
3. Railway bygger via `requirements.txt` og starter via `Procfile`.
4. Tilføj et **public domain** under Settings → Networking → Generate Domain.

> **$PORT-faldgrube (den du ramte sidst):** start-kommandoen skal bruge Railways
> `$PORT`, og den skal evalueres af en shell. `Procfile`-linjen herover gør det.
> Hvis du i stedet sætter en custom **Start Command** og porten ikke binder, så
> wrap den i en shell: `bash -c "uvicorn main:app --host 0.0.0.0 --port $PORT"`.
> Bruger builderen Railpack frem for Nixpacks, så tilføj evt. en `nixpacks.toml`
> eller sæt Builder = Nixpacks under Settings.

## 4. Test betalingsflowet
Læg en vare i kurven → **Til kassen** → på Stripes side:
- Kortnummer: `4242 4242 4242 4242`
- Udløb: en dato i fremtiden · CVC: 3 vilkårlige cifre · postnr: hvad som helst

Du sendes til `/success`. Ingen rigtige penge bevæger sig i test mode.

## 5. Del med testbrugere
Send Railway-URL'en. Den virker som en helt normal hjemmeside — også på mobil.
Tilmeldinger lander i `signups.jsonl` og i deploy-loggen.

---

## Abonnementer & brugerkonti (nyt)

**Sådan virker det:** Lægger kunden et system i kurven (EVOdescale / EVOadsorb / EVOsorb),
foreslår kurven automatisk det tilhørende reservefilter som **årligt abonnement**. Vælges det,
kører hele købet i ét Stripe Checkout (mode=subscription): systemet betales én gang, filteret
fornyes automatisk hvert år. EVOfilter er bevidst udeladt (skiftes hvert 4.–7. år — over Stripes
maksimale interval); dets reservefilter sælges som engangskøb.

**Brugerkonti (Supabase):**
1. Opret et Supabase-projekt → kør `supabase_migration.sql` i SQL-editoren.
2. Tilføj på Railway: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`.
3. (Test) Slå "Confirm email" fra under Authentication → Providers → Email, ellers skal
   testbrugere bekræfte e-mail før login.

Frontend viser så "Log ind / Opret konto" i headeren. Indloggede kunder får:
- deres e-mail forudfyldt i Checkout og købet knyttet til deres konto (via webhook),
- "Min konto" med aktive abonnementer og en knap til **Stripe Customer Portal**
  (skift kort, opsig, se fakturaer).

**Stripe-opsætning til abonnementer:**
- Aktivér Customer Portal: Dashboard → Settings → Billing → Customer portal → Save.
- Webhook (anbefalet): peg et endpoint mod `/webhook` med events
  `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`,
  og sæt `STRIPE_WEBHOOK_SECRET`. `invoice.paid` er jeres signal til at afsende
  et nyt filter ved hver årlig fornyelse.
- Uden Supabase virker alt stadig som gæstekøb — abonnementet oprettes på kundens
  e-mail i Stripe, og I kan administrere det fra Stripe Dashboard.

---

## Vigtigt før rigtig launch
- **Priser:** beløbene i `main.py` (`PRODUCTS`) er vejledende EUR→DKK-omregninger.
  Stripe debiterer ud fra **disse** server-side værdier — sæt jeres rigtige danske
  salgspriser her (klientens priser bruges aldrig til betaling).
- **Billeder:** `index.html` henter pt. EVODROPs billeder via deres CDN. Få den
  officielle asset-pakke fra EVODROP og host dem selv (fx en `/static`-mappe).
- **Signup → Supabase:** byt fil-lagringen i `/api/signup` ud med et Supabase-insert
  (du har allerede mønstret fra dine andre projekter).
- **Live Stripe:** skift til `sk_live_...` først når moms, handelsbetingelser og
  fortrydelsesret er på plads.
