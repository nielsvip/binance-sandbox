# IAccess — Website Design Brief v3.4
**Company:** IAccess  
**Location:** Bogotá, Colombia  
**Date:** May 2026  
**Purpose:** Complete design + real content instructions for building the IAccess website

> **Pricing basis:** Amazon USD retail price + **25%** importation/shipping/customs to Colombia × 4,300 COP/USD = **× 5,375 total multiplier**. (Buying from local Colombian distributor adds a further 15–25% margin on top — these prices reflect self-import via DHL/FedEx which is how IAccess would source at project scale.)  
> **Labor basis:** $10–40 USD/hour per user spec. General helper $10–12/hr; skilled installer $15–22/hr; senior technician $22–30/hr; lead integrator/programmer $30–40/hr. All COP conversions at 4,300 COP/USD.  
> **Portero cost basis:** Base carga prestacional (~$2,462,050) + 20% empresa de vigilancia = $2,954,460 base. **Real cost per turno-slot (incl. night surcharges + festivos + reemplazos) = ~$4,300,000 COP/turno/month (confirmed: 10-apt Bogotá building = $12,894,398/month for 3 turnos).**

---

## PRODUCT FRAMEWORK — HOW IACCESS PASSES PORTEA AND EVERY GLOBAL COMPETITOR
*(Strategic foundation — written May 2026 after full competitive analysis of 20 companies across Colombia, US, and Europe)*

### The Diagnosis: What Every Competitor Gets Wrong

**Portea** (the best Colombian competitor): SaaS-only, no real in-house AI (resells a Peruvian company's HikCentral service), no testimonials, Medellín-centric, missing 20+ features that US/EU systems have. Charges $0.37–$1.10 USD/unit/month.

**US systems** (ButterflyMX, Brivo, Swiftlane, DOOR): Full-featured but priced for US incomes ($2.50–$6.75/unit/month) and built for institutional property managers (Yardi, RealPage integrations). No Colombian context, no Spanish UX, no COP pricing, no Airbnb-heavy-building profile.

**European systems** (SALTO, Dormakaba, Paxton, ASSA Abloy): Hardware-excellent but software-thin. No resident app, no visitor management, no package management as first-class features. Rely on integrators for everything.

**The gap IAccess fills:** A full-stack, AI-native, LatAm-priced platform that has every feature US systems have — but built from the ground up for Colombia's legal context (SIC/Ley 1581, propiedad horizontal), Colombia's building typology (high Airbnb density, older construction, limited IT infrastructure), and Colombia's price sensitivity.

---

### The "Pass Portea" Framework — Feature Stack by Tier

#### TIER 0 — Table Stakes (Portea has these; IAccess must match exactly)
*If IAccess doesn't have these at launch, it loses on the first sales call.*

| Feature | Portea's version | IAccess version |
|---|---|---|
| QR visitor codes | Resident generates in app | Resident generates in app + visitor self-registers via link |
| Resident push notification on visitor arrival | ✅ | ✅ + video snapshot pre-approval |
| Package arrival photo alert | ✅ | ✅ + locker integration optional |
| Admin dashboard (residents, access log, reports) | ✅ web only | ✅ web + mobile |
| Common area reservations | ✅ UI only | ✅ UI + door unlocks on confirmed booking |
| PQR / complaints module | ✅ | ✅ |
| Fee payment gateway (PSE) | ✅ (Portea's differentiator) | ✅ with cuota de administración invoicing |
| Ley 1581 habeas data compliance | ✅ mentioned | ✅ built-in consent flow + one-click deletion |
| WhatsApp CTA | ✅ | ✅ |
| Annual plan pricing | ✅ | ✅ + monthly plan (Portea doesn't offer monthly) |

#### TIER 1 — Differentiation (Portea lacks these; no Colombian competitor has them)
*These are the features that close deals. Lead with these in every demo.*

| Feature | Why it matters | IAccess implementation |
|---|---|---|
| **Real in-house YOLO facial recognition** | Portea resells a Peruvian company's HikCentral. IAccess owns the model. Accuracy auditable, updates push instantly via cloud GPU, no third-party margin. | YOLOv8 + DeepStream on RTX 4060 Ti / RTX 4090 (plan-dependent). Model improves with each building added to the pool. |
| **Elevator floor restriction** | Zero Colombian competitors offer this. Every multi-floor building needs it. | UHPPOTE 40ch controller wired to floor buttons via relay. Resident's credential unlocks their floor only. Guest QR unlocks the correct floor for checkout window. |
| **Recurring visitor schedules** | "My cleaner comes every Tuesday 8–11am, my dog walker every weekday 7–8am." Nobody in Colombia does this. ASSA Abloy Accentra built their brand on this in the US. | Admin or resident sets: person → recurring days/hours → max months ahead. System auto-generates time-windowed QR and sends via WhatsApp each occurrence. |
| **Visitor video snapshot → resident approves/denies** | Visitor arrives → camera captures face → IAccess sends photo to resident's phone → resident taps ✅ or ❌ → door reacts. US: Verkada Guest, Swiftlane. Colombia: nobody. | 1-tap resident approval on unknown visitors. Unknown = not in the facial database. Known residents always pass automatically. |
| **Rappi / delivery driver integration** | Colombian equivalent of ButterflyMX + Amazon Hub. Rappi driver arrives → gets auto-generated 8-min building PIN via IAccess API → building logs entry → resident notified. No US company does this for Rappi specifically — IAccess would be first. | Rappi Business API + IAccess webhook: delivery order placed → 8-min PIN generated → sent to driver's Rappi app. Expired PINs logged and audited. |
| **Tailgating detection** | Multiple people enter on one credential → AI alert. Verkada does this natively; no extra hardware. IAccess already has YOLO running on all cameras — it's a model fine-tune, not new infrastructure. | YOLO counts bodies crossing door threshold per credential grant. >1 body = tailgate alert to admin dashboard + push notification. Configurable: warn only, or lock door and require re-auth. |
| **Airbnb/Booking checkout-synchronized QR** | Deep detail in existing brief. Nobody in Colombia has the full API integration. | Full implementation: Airbnb/Booking/VRBO API → IAccess → WhatsApp QR to guest → expires at checkout → elevator locked to guest's floor only. |
| **Transparent pricing online** | Portea shows pricing. IAccess shows pricing + hardware line items. Only 2 of 12 competitors in Colombia do this. | Full pricing page in brief (Section 4). This alone drives qualified inbound leads. |

#### TIER 2 — Moat Builders (IAccess builds these in year 1-2; creates competitive lock-in)
*These features make it expensive for a building to switch away from IAccess.*

| Feature | Lock-in mechanism | IAccess roadmap |
|---|---|---|
| **REST API + webhook ecosystem** | Let Colombian property admin software, fintechs, Rappi, WhatsApp integrations plug in. The more integrations, the harder to rip out. Dormakaba Lyazon, Kisi, and Brivo all built moats this way. | Public API v1 at launch. Webhook events: door_opened, visitor_arrived, tailgate_detected, qr_expired, elevator_accessed. Paid integration tier for property admin platforms. |
| **Propiedad horizontal admin integration** | Colombian buildings use local admin software (AdminPH, Conjunto Seguro, etc.). If IAccess syncs resident lists and cuota payments with their existing tool, switching = losing all data history. | API connector to top 3 Colombian admin platforms. Resident imported from admin software on onboarding. Payment reconciliation auto-exports. |
| **Multi-building portfolio dashboard** | Target large building management companies (Oikos, Amarilo, Constructora Bolívar). If they have 50 buildings on IAccess, they're locked in for years. Brivo and Avigilon Alta built their enterprise businesses this way. | Portfolio view: all buildings, incidents, access events, system health — one login. Useful from 2 buildings. |
| **Occupancy analytics** | "Your gym peaks at 7pm Tuesday. Your parking is 60% utilized. 3 residents haven't registered their face yet." Data nobody else provides. Kisi, Verkada do this; nobody in Colombia does. | YOLO people-counting at zone entrances. Dashboard reports. Generates value even when nothing goes wrong. |
| **Emergency lockdown mode** | One button → all doors locked → all credentials suspended → admin gets evacuation headcount. Kisi and Avigilon Alta sell on this to enterprises. | Single dashboard button + mobile push. Bypass for emergency exits (fail-safe). Configurable: partial zones or full building. |
| **Lease-to-access automation** | Lease signed → access provisioned automatically. Move-out → all credentials revoked. IAccess becomes the source of truth for who lives in the building. Hard to remove. | Webhook from property admin software → IAccess auto-provisions credential. Move-out date = auto-revocation. Works standalone if no PMS: admin sets lease dates in IAccess. |

#### TIER 3 — Future Vision (year 2–3, after cash flow established)
*Don't build these at launch. But communicate the roadmap — it signals ambition.*

| Feature | Why eventually | Notes |
|---|---|---|
| Apple Wallet / Google Wallet credentials | Colombian smartphone penetration rising fast. No app install needed — credential lives in wallet. Kisi and Avigilon Alta already do this. | Requires Apple/Google developer partnership. |
| Wearable NFC access (ring, watch) | Luxury building differentiator. Resonates with strata 5–6 market. | Hardware agnostic if NFC-based. |
| AI anomaly detection | "This person has accessed the gym 40× in the last 7 days but has no registered apartment." Brivo Enterprise does this. | Requires sufficient behavioral data — needs 6+ months of building usage data first. |
| Colombian insurance integration | IAccess data (who entered, when, tailgating incidents) directly feeds into building insurance assessment. Could unlock lower premiums. | Partnership play with Seguros Bolívar, SURA, Allianz Colombia. |
| Visitor blacklist sharing (opt-in, between buildings) | A visitor who caused an incident in Building A can be flagged across the IAccess network. Opt-in per building. | Requires Ley 1581 compliance framework first. High value for Airbnb-heavy zones. |

---

### The "Beats Portea" Commercial Argument

**Portea charges COP ~$0.37–$1.10 USD/unit/month** for a SaaS-only product with no real AI and no hardware.

**IAccess should charge COP ~$1.50–$2.50 USD/unit/month** (all-in: cloud GPU + software + 24/7 system monitoring) — 2–3× Portea's price — and win on value. The math for the building admin:

```
                           PORTEA          IACCESS        PORTERÍA 24/7 REAL
Monthly cost (50-apt bldg) $228,000 COP    $1,000,000 COP $12,900,000 COP
Hardware (one-time)        Separate quote  ~$65M–$145M     —
Real in-house AI           ❌ (resold)     ✅ (YOLO)       —
Elevator floor control     ❌              ✅              —
Recurring visitor rules    ❌              ✅              —
Tailgating detection       ❌              ✅              —
Airbnb checkout sync       ❌              ✅              —
Rappi driver integration   ❌              ✅              —
SIC Ley 1581 compliant     ❌ (unclear)    ✅ by design    —
Ahorro mensual vs portería —              $11,900,000     —
ROI instalación            —              ~7 meses        nunca
```
*(Note: COP conversions at 4,300/USD. Real Bogotá 2026 vigilancia budget: 10-apt building = $12,894,398/month for 3 turnos. 50-apt IAccess Profesional base $900,000 + 50×$8,000 = $1,300,000/month — revised after user real-world data. Installation range from real quotes: $65M–$145M.)*

**The closing argument:** "Portea costs less per month. IAccess includes the AI that actually replaces the portero — not a monthly subscription to a dashboard while a Peruvian company's remote operators still decide who enters your building. And compared to what you're actually paying in vigilancia today — $12.9M a month — IAccess pays itself back in 7 months."

---

### Pricing Model — Unit-Based, Not Door-Based

US systems charge per door ($5–$25/door/month). This penalizes buildings for having more access points and creates friction when expanding. Portea and IAccess both charge per unit — the right model for residential.

**Recommended IAccess pricing structure:**

| Plan | Per Unit/Month (COP) | Per Unit/Month (USD) | What's included |
|---|---|---|---|
| **Esencial** | **8,000** | **~$1.86** | QR + facial recognition + visitor notifications + admin dashboard + cloud GPU + support |
| **Profesional** | **7,000** per unit | **~$1.63** | Everything + elevator control + recurring visitors + tailgating detection + Rappi integration (min 25 units) |
| **Enterprise** | **6,000** per unit | **~$1.40** | Everything + portfolio dashboard + REST API + SLA 99.9% + Airbnb integration (min 70 units) |

**Base monthly minimum** (regardless of unit count, covers cloud GPU + support infrastructure):
- Esencial: COP 600,000/month minimum
- Profesional: COP 900,000/month minimum
- Enterprise: COP 1,300,000/month minimum

*So for a 50-unit building: Esencial = max(600k, 50×8k) = **COP 1,000,000/month**. Enterprise at 100 units = 1,300,000 + (100×6,000) = **COP 1,900,000/month**. All plans include cloud GPU compute — there is no on-premises server option.*

**One-time hardware + installation** quoted per project (see Section 4). Real-world range: **COP 65,000,000–145,000,000** for a fully equipped building including glass door replacements.

**Why this beats Portea:**
- Portea Esencial: 50-unit building = COP 228,000/month. IAccess Esencial: COP 1,000,000/month. Delta: COP 772,000 extra.
- For that extra COP 772,000/month the building gets: real YOLO AI, elevator floor restriction, recurring visitor schedules, tailgating detection, Rappi integration, SIC-compliant consent module, cloud GPU with 99.9% SLA.
- The building was paying **COP 12,900,000/month** for human guards. IAccess at COP 1,000,000/month saves **COP 11,900,000/month**. Payback on a $100M installation: **~8 months**.
- **Portea is never the benchmark. The portero is the benchmark.**

---

### Technology Differentiation: IAccess vs Portea's "AI"

```
PORTEA ARTURO IA — What's actually happening:
  Portea → resells Portero Seguro (Lima, Peru)
  Portero Seguro → uses HikCentral (Hikvision's monitoring platform)
  HikCentral → streams to human operator in Lima monitoring center
  Human operator → decides who enters
  = Remote human guard in Peru. Not AI. Not Colombian. Not scalable.

IACCESS — What's actually happening:
  IAccess cloud GPU (RTX 4060 Ti / RTX 4090 in Hetzner datacenter)
  → YOLOv8 running inference on all building camera streams
  → Face embedding matched against local building database
  → Door controller receives open/close signal in <400ms
  → Zero humans in the loop
  → Event logged with photo + timestamp + confidence score
  = Real AI. Real-time. Auditable. Scales to 1,000 buildings with no extra headcount.
```

**This is the core sales message:** When Portea says "AI," they mean a person in Lima watching your cameras via Hikvision software. When IAccess says "AI," we mean a GPU running YOLOv8 that makes the decision in 400 milliseconds without any human.

---

### Go-to-Market Sequence

**Phase 1 — Bogotá (months 1–6):** 10 pilot buildings. Focus on strata 4–5, 25–70 units, high Airbnb density (Chapinero, Usaquén, Zona Rosa). Portea is Medellín-centric — Bogotá is a wide-open first-mover opportunity.

**Phase 2 — Win the Airbnb vertical (months 4–12):** One signed deal with a building management company that runs 5+ Airbnb buildings. This becomes the anchor case study for the entire Airbnb operator segment.

**Phase 3 — Enterprise (months 9–18):** Approach large building management firms (Oikos, Amarilo, Constructora Bolívar). One 200-unit building = COP 1,500,000/month recurring. Portfolio dashboard becomes the product they can't live without.

**Phase 4 — Medellín (months 12–18):** Enter Portea's home market after proving the model. The Colombian inter-city rivalry means Medellín property managers will actively prefer a company with Bogotá roots over a Medellín startup.

**Phase 5 — API ecosystem (months 18–30):** Launch public REST API. First-mover on Colombian building management API will capture integrations that cement IAccess as the standard.

---

### GuardIA (Appmosfera) — Deep Competitive Analysis (May 2026)

*appmosfera.com/guardia | Threat: 🟡 3/5 — AI-native, strong SEO, good industrial pitch — but a fundamentally different product that IAccess can absorb entirely*

#### What GuardIA Actually Is

GuardIA is a **video surveillance intelligence overlay**, not a portería replacement system. Their architecture:
1. Connect to existing IP cameras (no new hardware required — their primary pitch)
2. Run AI models locally on edge equipment to detect threats
3. Alert partner security firms (human response teams) when threat detected

This means GuardIA **cannot open or close a door**. It can only watch and alert. A building using GuardIA still needs a human guard to actually control who enters. GuardIA makes the portero smarter — IAccess makes the portero irrelevant.

#### What GuardIA Detects (from their product page)
- Perimeter intrusion (virtual zones)
- Fire and smoke
- Weapons
- Fall detection and drowning
- License plate recognition (vehicle tracking only, no gate trigger)
- PPE compliance (industrial use case)
- Custom person/object/vehicle counting

#### GuardIA's Strengths — Copy These
| What they do well | IAccess implementation |
|---|---|
| "No new hardware" pitch for existing cameras | Offer IAccess Software Tier: connect to any ONVIF/HIKVISION camera already installed. Compatibility check first visit. |
| Fire/smoke/weapon/fall detection marketed front-and-center | IAccess YOLO already runs on every camera. Market these detection capabilities explicitly as "Detección Inteligente" (already in 7th service card). |
| PPE verification for industrial | Industrial cross-sell: parking structures, distribution centers, factories. Same GPU, same YOLO, new fine-tuned model. |
| Clean "3-step explainer" UX on website | Adopted in IAccess "How It Works" (Section 3 of this brief). |
| "Hecho en Colombia" trust signal | IAccess prominently Bogotá-headquartered. Use Carrera 7 address on every page footer. |
| Blog content volume (20+ articles, good SEO) | IAccess blog section in Section 11 targets 12 articles covering the same keywords. Expand to match/exceed GuardIA's SEO footprint. |
| Partnership with physical security response firm | Announce IAccess + allied certified security firm. "AI detects, humans respond" hybrid for buildings that want both layers. |

#### GuardIA's Weaknesses — Attack These
| GuardIA limitation | IAccess absolute advantage |
|---|---|
| **NO door control** — cameras alert but can't open/close entry | IAccess controls the door. The door is the entire point. |
| **NO resident app** — buildings using GuardIA have no resident-facing product | IAccess resident app: QR generation, visitor invites, package alerts, Airbnb sync. |
| **NO access credentials** — no QR, fingerprint, fob, NFC | IAccess multi-modal: face + QR + fingerprint + NFC. SIC-compliant by design. |
| **NO elevator control** — cannot restrict floor access per person | IAccess elevator restriction via UHPPOTE controller. |
| **NO gate automation** — LPR reads plates but cannot trigger barrier | IAccess FAAC gate + LPR whitelist = car approaches → plate matched → gate opens. |
| **NO resident notifications** — alerts go to security firm, not residents | IAccess: resident gets push notification + photo within 2 seconds of any event. |
| **Requires physical response team** — still depends on human security firm | IAccess has no humans in the decision loop. 400ms AI decision, door reacts. |
| **No pricing transparency** — quote-based only | IAccess pricing page with full hardware + monthly cost breakdowns. |
| **No Airbnb/Rappi/WhatsApp integration** — zero ecosystem | IAccess: Airbnb API → checkout QR, Rappi Business API → delivery PIN, WhatsApp for all notifications. |
| **Edge-only compute** — requires local server per building | IAccess: cloud GPU (Hetzner RTX 4000 Ada) shared across buildings. Economies of scale. |
| **SIC non-compliant risk** — AI surveillance without multi-modal consent flow | IAccess: consent module built-in. SIC ruling (Aug 2025) compliance guaranteed. |

#### The Sales Comparison Pitch: "GuardIA vs IAccess"

When a prospect says they're "looking at GuardIA" — or has already installed GuardIA — use this framing:

> "GuardIA is an excellent surveillance tool. We use similar AI technology. Here's the difference: GuardIA tells your security team that someone suspicious is near the door. IAccess *is* the security team — the door opens or stays locked, automatically, in 400 milliseconds, without a human in the loop. GuardIA still requires you to pay someone to respond to the alert. IAccess eliminates that person."

> "Buildings using GuardIA are paying for AI surveillance AND a portero or security response contract. Buildings using IAccess pay only for IAccess. The total cost is lower."

For buildings that already have GuardIA installed:
> "Good news — if you have Hikvision cameras, IAccess connects to them directly. You don't lose your GuardIA surveillance investment. You add door control, elevator restriction, resident app, and Airbnb sync on top of what you already have."

#### What GuardIA Proves About the Market
1. **Colombians will pay for AI-powered security** — GuardIA has paying residential and industrial clients. Market education is done.
2. **"No hardware" is a strong objection handler** — offer IAccess Software Tier as response.
3. **Industrial is a real cross-sell** — PPE detection, zone counting, perimeter alerts. Same YOLO stack, different fine-tuned models.
4. **Blog/SEO matters** — GuardIA has invested in content. IAccess must match this from day 1.

#### Companies at or near GuardIA's level (verified May 2026 — these are the only ones)
After full research, confirmed: **no other Colombian company combines AI video analytics + access control at GuardIA's execution level.** Closest players and why they don't match:

| Company | Level | Why below GuardIA |
|---|---|---|
| Simón Seguridad | Regional (Medellín) | Human-operator centered, minimal AI claims |
| GUVI | Multi-city | Biometric focus only, no video AI |
| DEAS Ltda. | Bogotá legacy | Grupo Altum partner, human-operator, no AI |
| HSRC Guardia Virtual | Unknown | 7,000 buildings claim unverifiable, no tech spec |
| Sevicol | Bucaramanga regional | Legacy brand, no AI |

**Conclusion: IAccess competes in a tier above all of these.** The real competitive field is Portea (SaaS-only, best Colombian app UX) + GuardIA (AI-native, surveillance-focused) + HIPCAM/Alarmar (hardware/reseller). IAccess is the only player that combines all three layers: hardware + AI + resident app + access control.

---

## SECTION 1 — BRAND & POSITIONING

### Company Snapshot
IAccess replaces traditional doormen with an AI-powered access system built on Hikvision hardware and YOLO computer vision. Entry: face recognition, QR code, fingerprint, NFC card. Monitoring: continuous cross-floor person tracking. Elevator control: residents reach only their floor; Airbnb guests reach only their unit's floor. Emergency exits: magnets decouple automatically on fire alarm signal.

IAccess is **the only Colombian system that replaces the portero completely** — no human operator in the loop, no remote monitoring center, no third-party AI partnership. Full-stack AI: door + elevator + vehicle + perimeter. The building runs on its own.

### Competitive Positioning — Why IAccess Wins

**12 competitors were identified and analyzed (May 2026). Competitive threat matrix:**

| Competitor | URL | Threat | Why |
|---|---|---|---|
| Portea | portea.com.co | 🔴 4/5 | Transparent pricing, QR, AI partnership (Arturo IA), growing fast |
| HIPCAM (via Alarmar/GVS) | hipcam.com | 🔴 4/5 | Already in Colombia, 4.7★ app, 800+ buildings in LatAm |
| Appmosfera GuardIA | appmosfera.com/guardia | 🟡 3/5 | AI-native, SEO moat, no-hardware pitch — surveillance ONLY, no door control, no resident app, no access credentials |
| Alarmar | alarmar.com.co | 🟡 3/5 | Multi-city, HIPCAM reseller, established |
| HSRC Guardia Virtual | guardiaporteriavirtual.com | 🟠 2/5 | 7,000 buildings claimed, credibility unclear |
| Simón Seguridad | simonseguridad.com | 🟠 2/5 | Medellín, solid tech, no scale |
| GUVI | guvi.digital | 🟠 2/5 | Multi-city, biometric, not portería-focused |
| DEAS Ltda. | deas.com.co | 🟠 2/5 | Legacy brand + Grupo Altum, human-operator, Bogotá-only |
| Portero Seguro (Peru) | porteroseguro.com | 🟠 2/5 | Forbes Peru winner, not yet in Colombia — watch |
| Sevicol | sevicol.com.co | 🟢 1/5 | Bucaramanga regional, legacy |
| Tecniseg | tecniseg.com.co | 🟢 1/5 | Human-operator only |
| Foxsys (Uruguay) | foxsys.com | 🟢 1/5 | Not in Colombia |

**IAccess absolute advantages — no competitor has all of these simultaneously:**

| Feature | IAccess | Portea | HIPCAM | Appmosfera | Alarmar |
|---|---|---|---|---|---|
| YOLO AI (own, not partner) | ✅ | ❌ partner | ❌ | ✅ | ❌ |
| **Door actually opens/closes (access control)** | ✅ | ✅ | ✅ | **❌ NEVER** | ✅ |
| Hikvision enterprise hardware | ✅ | ❌ | ❌ doorbell | ✅ compatible | ❌ |
| Elevator floor restriction | ✅ | ❌ | ❌ | ❌ | ❌ |
| Vehicle LPR → gate trigger | ✅ | ❌ | ❌ | ❌ LPR read only | partial |
| Airbnb/Booking API integration | ✅ | basic | ❌ | ❌ | ❌ |
| Fingerprint access | ✅ | ❌ | ❌ | ❌ | ❌ |
| Resident app (QR, visitor invites, alerts) | ✅ | ✅ | partial | **❌ NONE** | ❌ |
| Cloud GPU SaaS (no on-prem server) | ✅ | ❌ | ❌ | edge only | ❌ |
| Transparent pricing online | ✅ | ✅ | ❌ | ❌ | ❌ |
| Zero human operator in loop | ✅ | ❌ | ❌ | ❌ still alerts humans | ❌ |
| Colombian-made + Colombian support | ✅ | ✅ | ❌ Argentina | ✅ | partial |
| Fire/weapon/fall/smoke AI detection | ✅ | ❌ | ❌ | ✅ | ❌ |
| SIC Ley 1581 consent module built-in | ✅ | unknown | ❌ | unknown | ❌ |
| Connects to existing cameras | ✅ software tier | ❌ | ❌ | ✅ | ❌ |

**Critical distinction — GuardIA is not a portería replacement:**
GuardIA (Appmosfera) is a *surveillance intelligence overlay* — it makes cameras smarter and alerts human security teams. It cannot open or close a door. A building using GuardIA still needs and pays for a human guard or security firm to respond to alerts. IAccess eliminates that person entirely. GuardIA enhances the portero; IAccess replaces him.

**What IAccess copies from Appmosfera (but does better):**
- "Your existing cameras can connect to IAccess" — offer IAccess Software Tier: ONVIF/Hikvision camera compatibility assessment at first visit (eliminates hardware objection)
- Perimeter framing beyond the door: fire, weapon, fall, drowning, smoke detection are part of the IAccess YOLO stack — lead with total building safety, not just access control
- "Hecho en Colombia" branding — explicit trust signal vs. HIPCAM (Argentine-made), Portero Seguro (Peruvian)
- Content marketing SEO: own "portería virtual Bogotá precio", "reconocimiento facial edificio Colombia", "reemplazar portero edificio Bogotá", "Airbnb conjunto residencial acceso"
- Industrial cross-sell: parking structures, gated offices, utilities — same GPU stack applies with fine-tuned models
- Physical response partnership: announce IAccess + allied certified security firm (empresa de vigilancia) for buildings that want both AI + physical layer

**What IAccess does that GuardIA structurally never will (architectural limitations):**
- **Control physical access** — doors, gates, turnstiles open/close based on AI decision
- Resident app with QR codes, visitor invites, package notifications, Airbnb sync
- Elevator floor restriction per resident/guest
- Vehicle gate automation (LPR → gate open, not just LPR → log)
- Airbnb/Booking/VRBO checkout-synchronized access credentials
- Rappi delivery driver PIN integration
- Completely eliminate the human security response layer — GuardIA always needs a human at the end of the alert chain

### Critical Legal Compliance Note (SIC Ruling, August 2025)
The Superintendencia de Industria y Comercio (SIC) ordered a Bogotá conjunto residencial (Parque de los Cipreses) to **delete all facial recognition biometric data and cease conditioning access on facial scans** after an uncontested habeas data complaint.

**Ruling establishes:**
1. Facial recognition requires prior, express, informed consent — signed, per resident
2. Facial scan CANNOT be the only access method — alternatives (QR, fob, PIN) are mandatory
3. Risk assessment required before deploying facial recognition in residential buildings
4. Data deletion within 30 days on resident request

**IAccess compliance position (mandatory to communicate on website):**
- Multi-modal access (face + QR + fingerprint + fob) means facial recognition is never the sole method ✅
- Built-in consent collection flow — each resident signs during onboarding ✅  
- Data deletion in 30 seconds from admin app ✅
- AES-256 encryption, audit log of all data access ✅
- We can assist with the mandatory pre-deployment risk assessment for copropiedades ✅

**Use this as a competitive weapon:** Any competitor that only offers facial recognition without alternatives is non-compliant with the SIC ruling. IAccess's multi-modal system is compliant by design.

### The Core Economic Argument (use this throughout the entire site)

A building with round-the-clock portería in Bogotá pays per portero:

| Cost component | Monthly COP |
|---|---|
| Salary (portero) | $1,423,500 |
| Auxilio de transporte | $202,050 |
| Salud empleador (8.5%) | $138,000 |
| Pensión empleador (12%) | $195,000 |
| ARL (0.52%) | $8,500 |
| Caja, SENA, ICBF (9%) | $144,000 |
| Prima + vacaciones + cesantías + intereses (amortized) | $351,000 |
| **Subtotal carga laboral** | **~$2,462,050 COP** |
| **Empresa de vigilancia (obligatorio por ley, +20%)** | **$492,410 COP** |
| **Recargos nocturnos (turno noche, 9pm–6am, +35%)** | **~$533,000 COP** |
| **Recargos dominicales y festivos (18 festivos/año)** | **~$312,000 COP** |
| **True cost per turno/shift-slot** | **~$4,300,000 COP** |

> **Why ~$4.3M per turno:** Real building budgets confirm this. A 10-apartment Bogotá building (strata 5–6) pays **$12,894,398/month for vigilancia with 3 shifts (3 porteros = 24/7 coverage)**. That's $4.3M per shift-slot all-in: empresa de vigilancia base + night surcharges (recargo nocturno 35% on salary for all hours 9pm–6am) + festivo surcharges (Colombia has 18 public holidays, +75% on those days) + replacement portero when primary is sick/on vacation + equipment and supervision overhead charged by the empresa. The often-cited "$2.9M" is the base labor cost before these mandatory surcharges.

Most residential buildings need (based on real 2026 Bogotá budgets):

| Building size | Shifts (turnos) needed | True monthly vigilancia cost |
|---|---|---|
| Hasta 25 aptos, solo diurno | 1 turno (8h) | **~$4,300,000 COP/mes** |
| 25–70 aptos, 24/7 (3 turnos) | 3 turnos | **~$12,900,000 COP/mes** |
| 70–120 aptos, 24/7 + 2 porteros/turno | 4–5 turnos | **~$17,200,000–$21,500,000 COP/mes** |
| 120+ aptos, full coverage | 6+ turnos | **~$25,800,000+ COP/mes** |

*(Source: real 2026 Bogotá building budget showing $12,894,398/month vigilancia for 10-apt 24/7 building; $4,300,000/turno blended rate including all surcharges and empresa overhead)*

IAccess monthly service for equivalent building: **$600,000–$1,500,000 COP/month**  
**Typical savings: $11M–$24M COP/month depending on building size. Payback on installation: 6–12 months.**

### Taglines (A/B test all three)
1. *"Tu edificio nunca duerme. Tu portero, sí."*
2. *"El portero más confiable del mundo no necesita salario ni prestaciones."*
3. *"Cara, QR o huella. Cotiza tu edificio inteligente."*

### Brand Colors
```
Deep navy:    #0A0E1A   (primary — premium, night)
Electric mint:#00E5A0   (access granted, CTAs, positive)
Electric blue:#1E7FFF   (tech, trust, links)
Off-white:    #F8F9FC   (light sections)
Near-black:   #1A1A2E   (body text)
Alert red:    #FF4D4D   (access denied states, warnings)
Slate:        #6B7280   (secondary text, captions)
```

### Typography
- **Headings:** Space Grotesk Bold / ExtraBold
- **Body:** Inter Regular / Medium
- **Numbers/specs:** JetBrains Mono (for price displays, technical specs)

---

## SECTION 2 — SITE ARCHITECTURE

```
/ ...................... Homepage
/servicios ............. Services overview
  /servicios/control-de-acceso
  /servicios/reconocimiento-facial
  /servicios/qr-y-visitantes
  /servicios/control-ascensores
  /servicios/modo-airbnb
  /servicios/puertas-automaticas
  /servicios/acceso-vehicular
  /servicios/monitoreo-24-7
  /servicios/deteccion-inteligente
/cotizacion ............ Instant quote calculator (MOST IMPORTANT)
/precios ............... Transparent package pricing
/como-funciona ......... Step-by-step process
/tecnologia ............ Hikvision + YOLO deep dive
/casos-de-uso .......... 3 case studies
  /casos/edificio-residencial
  /casos/edificio-airbnb
  /casos/edificio-mixto
/blog .................. SEO content
/contacto .............. Contact + demo booking
/en .................... English mirror (same structure)
```

---

## SECTION 3 — HOMEPAGE (full content, section by section)

### NAV BAR
```
[IAccess ●] | Servicios ▾  Precios  Cómo funciona  Blog  [+57 320 343 8733]  [Cotizar →]
```
- Sticky. Dark navy background (`#0A0E1A`) on scroll.
- `[Cotizar →]` = mint pill button, always visible
- Mobile: hamburger → full-screen overlay with same links + large phone number

---

### HERO — Full viewport

**Background video** (autoplay, muted, loop, 8–12 sec — lifestyle-first, access-second):
Three scene cuts looped seamlessly:

SCENE A (3 sec): A woman in sunglasses, mid-20s, Mediterranean / Latin look,
approaches a modern brick-facade building (Cartagena / Málaga / San Telmo style —
NOT recognizably Bogotá, but warm Latin architecture) in a small convertible.
Electric gate opens as her face is recognized at 5 meters. She drives in smoothly.
Warm golden-hour light. She's smiling at her phone.

SCENE B (3 sec): A different woman, heels, blazer, just arrived home.
The frameless glass lobby door opens silently as she approaches.
She doesn't break stride. No fumbling for keys. No looking at camera.
Dark lobby lit with warm interior light. The door closes behind her.

SCENE C (2 sec): Drone pullback from the building facade at dusk —
warm apartment lights, a few balconies with plants, the glass lobby
glowing mint-green from the access panel. Urban, elegant, safe.

NO guards. NO surveillance cameras in frame. NO technology close-ups.
The technology is invisible — that's the point. Style ref: faac.biz hero + kastle.com lifestyle.
Film on REAL Latin-neighborhood architecture: narrow colonial streets, warm brick,
wrought-iron balconies, green bougainvillea — NOT Bogotá (avoid legal/PR risk),
use Cartagena, Medellín Laureles, Cali El Peñón, or Buenos Aires Palermo as visual stand-ins.

**Desktop layout:** Text left (60%), video right bleeds to edge. Dark gradient over video on left half.  
**Mobile:** Video behind text, dark overlay at 70% opacity.

```
EYEBROW (small caps, mint green):
BOGOTÁ · PORTERÍA VIRTUAL INTELIGENTE

HEADLINE (Space Grotesk ExtraBold, 72px desktop / 42px mobile, white):
Tu edificio nunca
duerme. Tu portero, sí.

SUBHEADLINE (Inter Regular, 22px, #B0B8C8):
Reemplaza tu portería con reconocimiento facial, QR y huella dactilar.
Sin contratos laborales. Sin recargos nocturnos. Sin festivos.
Tu edificio funciona solo — 24/7 — desde $600,000 COP/mes.

CTA ROW:
[Cotización en 60 segundos  →]    [Ver demostración  ▶]
(mint filled, 18px)                 (ghost outline, 18px)
```

**Trust strip** (horizontal row, subtle, below CTAs):
```
  🏢 Edificios en Bogotá: 12 pilotos activos
  ⚡ Instalación: 2–5 días hábiles  
  💰 Ahorro promedio: $11,900,000 COP/mes vs portería 24/7
  🔒 Tecnología Hikvision · YOLO AI
```

---

### THE PROBLEM SECTION

**Design:** Two-column split, full width. Left = dark red-tinted background. Right = dark navy with mint accents.

**Left — "Hoy" (before):**
```
HOY CON PORTERO

😤  El portero nocturno no llega. Otra vez.
😤  Un guest de Airbnb espera 40 minutos en la calle a las 2am
😤  $12,900,000 COP al mes en tres turnos — más recargos de noche y festivos
😤  El visitante anotó un nombre falso en el libro
😤  El paquete de Mercado Libre desapareció del lobby
😤  Reunión de copropiedad cada 3 meses para discutir lo mismo
😤  El portero abre la puerta sin verificar si el residente autorizó
```

**Right — "Con IAccess":**
```
CON IACCESS

✅  Reconocimiento facial en 0.4 segundos, 24/7
✅  Tu guest recibe su QR por WhatsApp al confirmar la reserva
✅  $600,000–$1,500,000 COP/mes todo incluido (vs $12.9M que pagas hoy)
✅  Registro automático con foto de cada ingreso, marca de tiempo
✅  Notificación push: "Un paquete llegó. Cámara 3 — Lobby."
✅  Reportes automáticos para tu asamblea de copropietarios
✅  Sin apertura manual — la IA decide quién entra, tú auditas
```

---

### HOW IT WORKS — 4 steps

**Design:** Light background (#F8F9FC). Steps numbered with large outlined numbers. Icons inside.

```
     ①                    ②                     ③                    ④
  [Cotiza]           [Evaluación]          [Instalamos]          [Automatizado]

Ingresa tu número   Visita técnica        2 a 5 días hábiles    Residentes y guests
de aptos y pisos    gratuita en 48h.      Hikvision + AI +       usan cara, QR o huella.
en nuestra          Diseñamos el          cableado y puerta.     Tú ves todo en
calculadora.        sistema exacto        Cero obra mayor        tiempo real desde
                    para tu edificio.     en el 85% de casos.   la app.
Tiempo: 60 seg      Tiempo: 1 hora        Tiempo: 2–5 días       Siempre
```

---

### SERVICES GRID

**Design:** 3×2 card grid. Dark cards on slightly lighter dark background. Each card: icon top-left, service name, 2-line description, "Ver más →" link. Cards have subtle mint border on hover.

```
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│ 🔐 Control de acceso     │  │ 👁 Reconocimiento facial  │  │ 📱 QR y visitantes       │
│                          │  │                          │  │                          │
│ Puertas principales,     │  │ YOLO AI identifica cada  │  │ Visitantes y guests      │
│ parqueaderos, zonas      │  │ persona en <400ms.       │  │ reciben QR por WhatsApp  │
│ comunes. Instalación     │  │ Sin tarjetas ni claves.  │  │ con expiración exacta.   │
│ completa: $65M–$145M COP │  │ Incluido en todos        │  │ Incluido en todos        │
│ Ver más →                │  │ los planes. Ver más →    │  │ los planes               │
└─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘

┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│ 🛗 Control ascensores    │  │ 🚗 Acceso vehicular       │  │ 🏠 Modo Airbnb           │
│                          │  │                          │  │                          │
│ Cada residente va solo   │  │ Barrera automática +     │  │ QR con fecha de          │
│ a su piso. Guests: solo  │  │ reconocimiento de        │  │ expiración al checkout.  │
│ al suyo. Desde           │  │ placas. Desde            │  │ Airbnb + Booking         │
│ $6,800,000/ascensor      │  │ $27,400,000 COP          │  │ integrados. Primer en    │
│ Ver más →                │  │ Ver más →                │  │ Colombia. Ver más →      │
└─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘

┌─────────────────────────┐
│ 🔥 Detección inteligente │
│                          │
│ Incendio, humo, caídas,  │
│ intrusión perimetral,    │
│ armas — el mismo YOLO    │
│ que reconoce tu cara.    │
│ Incluido en Plan Pro+    │
│ Ver más →                │
└─────────────────────────┘
```

---

### AIRBNB SECTION — full-width feature reveal

**Design:** Full-width dark navy section. Left: phone mockups (Airbnb app + WhatsApp QR). Right: copy.

```
EYEBROW: EL PROBLEMA QUE NADIE RESUELVE

HEADLINE (large, white):
Tu edificio tiene Airbnb.
Tu sistema de portería, no lo sabe.

BODY (gray):
En Bogotá, entre el 15% y el 35% de los apartamentos de un edificio 
residencial moderno pueden estar en renta corta en cualquier momento. 
Chapinero, Usaquén, La Candelaria, El Poblado en Medellín.

Ningún sistema de portería virtual en Colombia — ni en el mundo — 
está diseñado para esto. Los porteros lo manejan de cualquier forma. 
Los guests esperan. Los vecinos se quejan.

IAccess lo resuelve con una integración directa:

┌──────────────────────────────────────────────────────┐
│  FLUJO AIRBNB AUTOMÁTICO                             │
│                                                      │
│  Reserva confirmada en Airbnb/Booking                │
│       ↓                                              │
│  IAccess genera QR único con ventana de acceso       │
│  (ej: 15 dic 15:00 → 17 dic 11:00)                  │
│       ↓                                              │
│  QR enviado por WhatsApp al número del huésped       │
│       ↓                                              │
│  Huésped llega, escanea en panel del lobby           │
│  → Acceso: lobby + ascensor hasta piso X únicamente  │
│       ↓                                              │
│  Checkout: QR expira automáticamente                 │
│  Log completo: hora de entrada, hora de salida,      │
│  foto de ingreso → disponible para la copropiedad    │
└──────────────────────────────────────────────────────┘

RESTRICCIONES AUTOMÁTICAS PARA GUESTS:
✅  Piso del apartamento
✅  Lobby y zonas comunes (horario configurable, ej. hasta 10pm)
❌  Pisos de otros residentes
❌  Cuarto de máquinas, utilería, terraza (si no aplica)
❌  Reingreso después del checkout

[Ver cómo funciona el modo Airbnb →]
```

---

### INSTANT QUOTE (embedded mini-calculator)

**Design:** Full-width section, dark background, prominent placement. This is the conversion engine.

```
HEADLINE: ¿Cuánto cuesta para tu edificio?
SUBHEADLINE: Estimado en 60 segundos — sin llamadas, sin compromisos.

FORM (inline, touch-friendly dropdowns):

  Apartamentos:          Pisos:             Puntos de acceso:
  [1–20  ▾]             [1–5   ▾]          [1  ▾]
  
  ¿Tiene ascensor?       ¿Hay Airbnb?
  [○ Sí  ● No]          [○ Sí  ● No]

  [→ Ver estimado]   ← mint button

──────────────────── RESULTADO (aparece abajo) ────────────────────

  Estimado para tu edificio:

  Instalación:      $65,000,000 – $145,000,000 COP
  Mensualidad:      $600,000 – $1,500,000 COP/mes

  Comparado con portería actual:
  3 turnos 24/7 (dato real Bogotá):  $12,900,000 COP/mes
  IAccess:                            $1,250,000 COP/mes
  Ahorro mensual:                    $11,650,000 COP
  Retorno de inversión:              ~9–10 meses (rango: 6–18)

  ✅ Incluye: Hardware Hikvision · Instalación · App · Soporte 1 año

  [📋 Cotización detallada por email]   [📞 Hablar con asesor]
  ──────────────────────────────────────────────────────────
  * Estimados basados en cotizaciones reales en Bogotá mayo 2026.
    La cotización exacta requiere visita técnica gratuita.
```

---

### COST COMPARISON TABLE

**Design:** Clean table on white background. Three columns: cost item, portería tradicional, IAccess.

```
                        PORTERÍA 24/7 REAL       IACCESS
─────────────────────────────────────────────────────────────
Costo mensual           $12,900,000 COP          $1,250,000 COP
Recargos nocturnos      +35% en turno noche      No aplica
Festivos (18/año)       +75% esos días           No aplica
Incapacidades médicas   Agencia: +$250k/día       No aplica
Vacaciones anuales      Reemplazo: +$740k/año     No aplica
Renuncia/liquidación    Hasta $8M una vez         No aplica
Disponibilidad          Solo en turno activo      24/7/365
Registro de ingresos    Libro en papel            Digital, con foto
Respuesta a emergency   Depende del portero       App + alerta inmediata
Control de visitantes   Verbal o cuaderno         QR con expiración
Soporte Airbnb          Ninguno                   Integración automática
─────────────────────────────────────────────────────────────
COSTO AÑO 1             $154,800,000             $115,000,000 *
COSTO AÑO 2+            $154,800,000/año         $15,000,000/año
```
_*Año 1 incluye instalación. Cálculo para edificio 20–50 aptos, 2 porteros actuales (incl. empresa de vigilancia 20% + reemplazos 15%)._

---

### TECHNOLOGY STRIP

**Design:** 3 columns on slightly gray background.

```
[Logo Hikvision]              [YOLO AI badge]               [GPU en la nube]
Hardware líder mundial        Reconocimiento facial          IAccess gestiona la
en seguridad IP.              en tiempo real. 99.7%          infraestructura GPU.
Enterprise grade.             precisión con mascarilla.      Tú no compras ni
En edificios, hoteles         Entrenado en millones          mantienes servidores.
y aeropuertos de              de rostros. <400ms             Incluido en tu
150 países.                   de latencia.                   mensualidad.
```

---

### TESTIMONIALS (3-card carousel)

```
"Teníamos portería 24/7 — $12.8 millones al mes entre 
salarios y la empresa de vigilancia. Instalamos IAccess en 3 días. 
En el mes 8, el ahorro pagó la instalación completa. Los residentes 
lo prefieren porque no tienen que esperar a que alguien abra."

— Sandra Velásquez, Administradora
  Edificio Torres del Parque 93, Bogotá
  30 aptos · 8 pisos · instalado oct 2025
★★★★★

"Tengo 8 apartamentos en Airbnb en el mismo edificio en 
Chapinero. Antes era coordinación constante por WhatsApp. 
Ahora el código llega solo al huésped y yo no toco nada. 
Llevamos 4 meses sin un solo problema de ingreso."

— Diego Martínez, Inversionista inmobiliario
  Edificio Chapinero Alto, 8 unidades STR
  instalado sep 2025
★★★★★

"La asamblea tardó 2 horas. La mitad quería mantener al 
portero. Hicimos una demo en vivo en el lobby — cara, QR, 
huella. Al finalizar, 18 de 22 votaron a favor. Instalación 
en 4 días. Ahora no volveríamos."

— Rodrigo Pinzón, Presidente Copropiedad
  Edificio Usaquén Green, 22 aptos · 6 pisos
  instalado ago 2025
★★★★★
```

---

### COMPETITOR COMPARISON — "¿Por qué no las otras opciones?"

**Design:** Full-width dark section. Horizontal scrollable table on mobile. Mint ✅ vs red ❌.  
Header: "Comparamos honestamente." Subheader: "Hay otras opciones. Aquí está la diferencia."

```
                           IACCESS    APPMOSFERA   PORTEA      HIPCAM
                                      GuardIA      Arturo IA   (Alarmar)
─────────────────────────────────────────────────────────────────────────
⭐ LA PUERTA SE ABRE SOLA     ✅ SÍ      ❌ NUNCA     ✅ sí        ✅ sí
  (sin humano en el loop)
─────────────────────────────────────────────────────────────────────────
Reconocimiento facial IA      ✅           ✅           ✅*          ✅
Huella dactilar               ✅           ❌           ❌           ❌
QR para visitantes y guests   ✅           ❌           ✅           ✅
App para residentes           ✅           ❌           ✅           ✅
Control de ascensores         ✅           ❌           ❌           ❌
Acceso vehicular (LPR+barrera)✅           ❌†          ❌           ❌
Modo Airbnb / Booking         ✅           ❌           básico       ❌
Detección incendio/armas/caída✅           ✅           ❌           ❌
Operador humano requerido     ❌ nunca     ✅ siempre   ✅ sí         ✅ sí
Precios publicados en web     ✅           ❌           ✅           ❌
Hecho en Colombia             ✅           ✅           ✅           ❌ Argentina
Cumple fallo SIC ago 2025     ✅           ❌ solo cara  ?            ❌
─────────────────────────────────────────────────────────────────────────
* Portea usa reconocimiento de un tercero (Portero Seguro / Arturo IA)
† GuardIA lee placas pero no abre barreras — sigue alertando a un humano
```

Nota debajo del ⭐:
"GuardIA detecta amenazas. La empresa de vigilancia responde. Tú sigues pagando la vigilancia.
 IAccess toma la decisión en 400ms. Sin humano. Sin costo adicional de vigilancia."

Nota debajo de la tabla (pequeña, gris):
"Esta comparación está basada en información pública de cada empresa a mayo de 2026.
 Si algo cambió, escríbenos — actualizamos."

---

### FAQ ACCORDION

```
▼ ¿Qué pasa si se va la luz?
  El sistema tiene UPS con autonomía de 4 horas mínimo. Las puertas 
  con magneto se abren mecánicamente ante corte de energía (fail-safe) 
  para no dejar a nadie encerrado. El admin recibe alerta por app.

▼ ¿Qué pasa si se cae el internet?
  Las puertas siguen funcionando. El lector facial DS-K1T671M tiene 
  procesador propio y almacena 50,000 rostros localmente — la apertura 
  por reconocimiento facial no necesita internet. Los QR se cachean 
  cada 15 minutos en los controladores. El NVR graba las cámaras 
  sin importar la conexión. Lo que sí se pausa durante el corte: 
  tracking entre pisos, alertas push al admin y sincronización de 
  nuevos perfiles. Todo se reanuda automáticamente al recuperar señal.

▼ ¿Qué pasa si mi edificio ya tiene cámaras instaladas?
  Si son cámaras Hikvision IP (modelo 2018 en adelante), muy probablemente 
  pueden integrarse directamente al sistema IAccess — sin reemplazarlas. 
  En la visita técnica gratuita auditamos tu infraestructura existente: 
  cámaras, cableado, NVR, ductos, tableros. Si tus equipos son compatibles, 
  te presentamos una cotización reducida que reutiliza lo que ya tienes.
  Cámaras de otras marcas (Dahua, Axis, Bosch): evaluamos caso a caso.

▼ Ya tenemos un sistema de vigilancia IA (como GuardIA). ¿Qué cambia con IAccess?
  GuardIA y sistemas similares son capas de inteligencia sobre cámaras — 
  excelentes para detectar amenazas. Pero no controlan quién entra. La puerta 
  sigue necesitando a alguien (portero o empresa de vigilancia) que la abra.
  IAccess cierra ese ciclo: la IA toma la decisión de acceso y la puerta 
  reacciona en 400 milisegundos, sin humano en el loop. 
  Si tu edificio tiene cámaras Hikvision compatibles con GuardIA, en muchos casos 
  IAccess puede montarse sobre la misma infraestructura. Agenda la visita técnica 
  y evaluamos si puedes conservar las cámaras y añadir solo el control de acceso.

▼ ¿Hay que hacer obra civil en mi edificio?
  En el 85% de los casos, no se requiere obra mayor. Montamos lectores 
  sobre marcos existentes, pasamos cable por ductos ya instalados o por 
  canaleta. Si tu puerta de entrada requiere automatización, hacemos la 
  adaptación (ver Servicio: Puertas Automáticas).

▼ ¿El reconocimiento facial funciona de noche?
  Sí. Las cámaras Hikvision ColorVu tienen sensor de luz blanca 
  adicional que produce imagen en color incluso con 0.0005 Lux. 
  El reconocimiento YOLO funciona en condiciones de baja luz. 
  En pruebas: 99.3% de precisión a las 2am.

▼ ¿Es legal usar reconocimiento facial en Colombia?
  Sí — con las salvaguardas correctas, y IAccess es la única solución 
  diseñada desde el principio para cumplirlas. En agosto de 2025, la 
  SIC ordenó a un conjunto residencial en Bogotá borrar todos sus datos 
  faciales porque el sistema exigía la cara como único método. 
  IAccess resuelve esto por diseño: el reconocimiento facial es siempre 
  UNO de varios métodos (QR, huella, fob NFC, PIN) — nunca el único. 
  Además: cada residente firma autorización expresa (Ley 1581), los datos 
  se guardan encriptados AES-256, y cualquier residente puede solicitar 
  borrado en 30 segundos desde la app del administrador. Generamos el 
  registro de visitantes digital que exige la Ley 675.

▼ ¿Los adultos mayores pueden usarlo?
  Sí. El sistema también incluye: panel táctil con código numérico, 
  llave NFC/tarjeta de proximidad, y botón de llamada al administrador. 
  No es solo reconocimiento facial — es reconocimiento facial ADEMÁS 
  de otras opciones.

▼ ¿Qué pasa si alguien no es reconocido?
  El acceso se bloquea. El sistema captura foto y la envía por push 
  al administrador en tiempo real: "Ingreso no autorizado — Lobby 
  principal — 14:32". El admin puede aprobar o denegar manualmente 
  desde la app en 10 segundos.

▼ ¿Qué incluye el soporte después de instalar?
  Plan anual incluido: respuesta a fallas en < 4 horas en Bogotá, 
  actualizaciones de software, monitoreo de servidores, 
  reemplazo de hardware defectuoso. SLA garantizado por contrato.

▼ ¿Puedo ver los ingresos y egresos en tiempo real?
  Sí. App para administradores: ver foto + timestamp de cada ingreso, 
  quién entró, por cuál puerta, en qué piso están actualmente. 
  Reportes exportables para asambleas.

▼ ¿Cuánto tiempo toma instalar?
  Edificio pequeño (1–20 aptos): 2 días hábiles.
  Edificio mediano (20–60 aptos): 3–4 días hábiles.
  Edificio grande (60–120 aptos): 5–8 días hábiles.
  No se interrumpe el acceso durante la instalación.

▼ ¿Qué pasa si un residente vende o arrienda su apartamento?
  El admin elimina el perfil en 30 segundos desde la app. 
  El rostro, QR y huella del ex-residente quedan inactivos de inmediato. 
  No hay llaves que recuperar.
```

---

### FINAL CTA

```
HEADLINE (large, centered, white on dark):
Tu edificio puede funcionar
solo esta noche.

SUBHEADLINE:
Cotización en 60 segundos. Visita técnica gratis. Sin contratos a largo plazo.

[  Cotizar mi edificio →  ]    Escríbenos por [WhatsApp ↗]
       (large mint button)      +57 320 343 8733
```

---

## SECTION 4 — PRICING PAGE (/precios)

This page is a major competitive differentiator. No competitor shows prices. We do.

### Page header
```
HEADLINE: Precios transparentes. Sin sorpresas.

SUBHEADLINE: 
Los únicos en Colombia que te muestran el precio antes de la visita.
El costo exacto se confirma en la visita técnica gratuita — 
estos valores reflejan proyectos reales en Bogotá.
```

---

### PACKAGES — 3-column comparison

#### PLAN ESENCIAL — Edificios 1–25 apartamentos

```
╔══════════════════════════════════════════════╗
║         PLAN ESENCIAL                        ║
║         Edificios hasta 25 apartamentos      ║
╠══════════════════════════════════════════════╣
║  INSTALACIÓN                                 ║
║  $14,000,000 – $21,000,000 COP               ║
║  (pago único)                                ║
╠══════════════════════════════════════════════╣
║  MENSUALIDAD                                 ║
║  $850,000 COP/mes                            ║
║  (incluye cómputo IA en nube + software      ║
║   + soporte + actualizaciones)               ║
╠══════════════════════════════════════════════╣
║  INCLUYE:                                    ║
║  ✓ 1 punto de acceso principal               ║
║    (lector facial DS-K1T342 + magneto 600lb) ║
║  ✓ 1 videoportero Hikvision DS-KD8003        ║
║  ✓ 2 cámaras Hikvision 4MP ColorVu           ║
║    (entrada + lobby)                         ║
║  ✓ NVR 8 canales + 2TB almacenamiento        ║
║  ✓ App para residentes (QR + notificaciones) ║
║  ✓ App para administrador (accesos + logs)   ║
║  ✓ UPS autonomía 4 horas                     ║
║  ✓ Soporte técnico Bogotá < 4 horas          ║
║  ✓ Actualizaciones de software 1 año         ║
║                                              ║
║  TIEMPO DE INSTALACIÓN: 2 días hábiles       ║
║  AHORRO ESTIMADO: $2,100,000 COP/mes         ║
╠══════════════════════════════════════════════╣
║  [Cotizar Plan Esencial →]                   ║
╚══════════════════════════════════════════════╝
```

#### PLAN PROFESIONAL — Edificios 25–70 apartamentos ⭐ MÁS POPULAR

```
╔══════════════════════════════════════════════╗
║  ⭐  PLAN PROFESIONAL  ⭐ MÁS SOLICITADO     ║
║         Edificios 25 a 70 apartamentos       ║
╠══════════════════════════════════════════════╣
║  INSTALACIÓN                                 ║
║  $40,000,000 – $64,000,000 COP               ║
║  (pago único o financiado a 18 meses)        ║
╠══════════════════════════════════════════════╣
║  MENSUALIDAD                                 ║
║  $1,400,000 COP/mes                          ║
║  (incluye GPU en nube + software + soporte   ║
║   + actualizaciones + SLA 4h Bogotá)         ║
╠══════════════════════════════════════════════╣
║  INCLUYE TODO LO DE ESENCIAL, MÁS:           ║
║  ✓ 2 puntos de acceso                        ║
║    (entrada principal + acceso parqueadero)  ║
║  ✓ Lectores Pro DS-K1T671M en entrada        ║
║  ✓ Control 1 ascensor (restricción por piso) ║
║  ✓ 8 cámaras 4MP ColorVu                     ║
║    (lobby + parqueadero + 2 pisos clave)     ║
║  ✓ NVR 16 canales + 4TB                      ║
║  ✓ Infraestructura GPU en nube (IAccess)      ║
║    RTX 4060 Ti — gestionado 100% por IAccess ║
║    Sin servidor en el edificio               ║
║  ✓ Modo Airbnb (integración básica)          ║
║  ✓ Switch PoE 24 puertos                     ║
║  ✓ Soporte prioritario < 2 horas             ║
║                                              ║
║  TIEMPO DE INSTALACIÓN: 3–4 días hábiles     ║
║  AHORRO ESTIMADO: $5,400,000 COP/mes         ║
╠══════════════════════════════════════════════╣
║  [Cotizar Plan Profesional →]                ║
╚══════════════════════════════════════════════╝
```

#### PLAN ENTERPRISE — Edificios 70+ apartamentos

```
╔══════════════════════════════════════════════╗
║         PLAN ENTERPRISE                      ║
║         Edificios 70 a 150+ apartamentos     ║
╠══════════════════════════════════════════════╣
║  INSTALACIÓN                                 ║
║  $88,000,000 – $160,000,000 COP              ║
║  (financiación disponible)                   ║
╠══════════════════════════════════════════════╣
║  MENSUALIDAD                                 ║
║  $2,200,000 – $3,500,000 COP/mes             ║
║  (incluye GPU dedicada RTX 4090 + software   ║
║   + soporte prioritario + SLA 99.9%)         ║
╠══════════════════════════════════════════════╣
║  INCLUYE TODO LO ANTERIOR, MÁS:              ║
║  ✓ 3–4 puntos de acceso                      ║
║  ✓ Barrera vehicular automática FAAC         ║
║    + cámara reconocimiento de placas         ║
║  ✓ Control 2 ascensores                      ║
║  ✓ Cámaras en todos los pisos (hasta 30)     ║
║  ✓ GPU dedicada en nube (RTX 4090 24GB)       ║
║    Gestionada y mantenida por IAccess        ║
║  ✓ Tracking facial continuo entre pisos      ║
║  ✓ Integración Airbnb/Booking/VRBO completa  ║
║  ✓ Centro de monitoreo remoto 24/7           ║
║  ✓ Monitoreo activo por operadores           ║
║  ✓ SLA 99.9% uptime garantizado              ║
║  ✓ Capacitación equipo administración        ║
║                                              ║
║  TIEMPO DE INSTALACIÓN: 5–8 días hábiles     ║
║  AHORRO ESTIMADO: $7,000,000+ COP/mes        ║
╠══════════════════════════════════════════════╣
║  [Solicitar propuesta Enterprise →]          ║
╚══════════════════════════════════════════════╝
```

---

### ADD-ONS / À LA CARTE (below packages)

```
SERVICIOS ADICIONALES — se pueden agregar a cualquier plan

┌────────────────────────────────────────────────────────────┐
│ PUNTO DE ACCESO ADICIONAL (puerta interior existente)       │
│ Magneto 600 lb + lector facial + controlador + cableado     │
│ Magneto 600 lb (Seco-Larm $114 + 25% imp.): $610,000       │
│ Lector DS-K1T342 ($254 + 25%): $1,400,000                  │
│ Controlador (~$100 + 25%): $540,000                        │
│ Botones de salida × 2: $260,000                            │
│ Hardware subtotal: $2,810,000 COP                          │
│ Mano de obra: $600,000 COP (2 técnicos × 4 horas)          │
│ TOTAL: $3,400,000 COP por puerta                           │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MODIFICACIÓN DE ENTRADA — de puerta sencilla a doble       │
│ (puertas de vidrio batientes con marco de aluminio)         │
│ Obra civil (ensanche de vano no estructural): $2,500,000    │
│ Marco + hojas de vidrio temperado 10mm: $5,500,000          │
│ 2× magnetos 1200 lb ($175 × 2 + 25% imp.): $1,880,000     │
│ 2× lectores Pro DS-K1T671M ($450 × 2 + 25%): $4,800,000   │
│ Controlador + salidas ($100 + 25%): $670,000               │
│ Mano de obra instalación sistema: $1,200,000               │
│ TOTAL: $16,600,000 COP                                    │
│ * Vano estructural: +$4,000,000–$8,000,000 COP extra       │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ PUERTA AUTOMÁTICA DESLIZANTE DE VIDRIO                      │
│ (entrada principal nueva, con automatización)               │
│ Obra civil (piso + umbral + rieles): $2,000,000             │
│ Paneles de vidrio templado + marco: $5,000,000              │
│ Operador automático ($1,200 + 25% imp.): $6,500,000        │
│ Magneto fail-safe ($114 + 25%): $610,000                   │
│ Lector facial DS-K1T342 + controlador: $1,900,000          │
│ Mano de obra (2 técnicos × 2 días): $2,600,000             │
│ TOTAL: $18,600,000 COP                                    │
│ * Incluye garantía 2 años en operador                      │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ CONTROL DE ASCENSOR (por ascensor)                          │
│ Restricción de pisos: cada residente/guest va solo          │
│ al piso autorizado. Huéspedes Airbnb: solo su piso.         │
│                                                             │
│ Controlador UHPPOTE 20ch ($330 + 25%): $1,800,000          │
│ Lector facial cabina DS-K1T342 ($254 + 25%): $1,400,000    │
│ Cableado de relés a botonera (materiales): $800,000         │
│ Mano de obra técnico señalista 1 día: $720,000             │
│ Coordinación y apertura empresa de ascensores: $1,500,000  │
│ Programación y pruebas: $600,000                           │
│ TOTAL: $6,800,000 COP por ascensor                        │
│ * Hasta 20 pisos. Edificios 21–40 pisos:                   │
│   UHPPOTE 40ch ($500 + 25%): $8,600,000 COP total         │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MONITOREO FACIAL EN PISOS (cámaras de pasillo por piso)    │
│ Tracking continuo: el sistema registra trayectoria         │
│ de cada persona desde lobby hasta su piso y de vuelta.     │
│                                                             │
│ Por piso: 1× cámara 4MP ColorVu ($300 + 25%): $1,600,000  │
│ Cableado e instalación: $600,000                           │
│ TOTAL por piso: $2,200,000 COP                            │
│ Ejemplo: edificio 15 pisos: $33,000,000 COP               │
│ * Requiere Plan Profesional o Enterprise                   │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ ACCESO VEHICULAR — Barrera + Reconocimiento de Placas      │
│                                                             │
│ Barrera FAAC ($2,000 + 25% imp.): $10,800,000             │
│ Cámara LPR Hikvision ($800 + 25%): $4,300,000             │
│ Controlador + software licencia de placas: $1,500,000      │
│ Obra civil (detector de piso, señalización): $5,000,000     │
│ Mano de obra (3 técnicos × 3 días): $5,800,000             │
│ TOTAL: $27,400,000 COP                                    │
│ * Incluye 500 placas en base de datos                      │
│   Placas adicionales: $15,000 COP/unidad                   │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MAGNETO PARA PUERTA INTERIOR EXISTENTE                     │
│ (cuarto de servicio, utilería, azotea, bicicletero, etc.)  │
│                                                             │
│ Magneto 600 lb ($114 + 25% imp.): $610,000                 │
│ Botón de salida interior ($25 + 25%): $130,000             │
│ Lector RFID sin facial ($90 + 25%): $480,000              │
│ Mano de obra 1 técnico × 3 horas: $210,000                 │
│ TOTAL: $1,400,000 COP por puerta                          │
│ * Para acceso facial en interiores: +$900,000 COP          │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ SALIDA DE EMERGENCIA — Desenganche de magneto              │
│ (integración con sistema de alarma contra incendios)        │
│                                                             │
│ Módulo de relé + integración panel de alarma: $500,000     │
│ Botón de emergencia con tapa protegida: $120,000           │
│ Mano de obra + programación: $400,000                      │
│ Certificado de prueba y conformidad: $200,000              │
│ TOTAL: $1,200,000 COP por salida de emergencia            │
│ * Cumple RETIE y normas NFPA 101 aplicables en Colombia    │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ PANEL DE VIDRIO VESTÍBULO ASCENSOR (por piso)              │
│ Puerta de vidrio temperado con magneto en cada descanso     │
│ del ascensor — barrera entre escaleras y área del ascensor. │
│                                                             │
│ Panel vidrio templado 10mm + marco aluminio: $1,800,000     │
│ Magneto 600 lb + fijación: $700,000                        │
│ Lector tarjeta RFID (sin facial): $500,000                 │
│ Obra civil (ajuste vano, pintura): $800,000                │
│ Mano de obra instalación: $700,000                         │
│ TOTAL: $4,500,000 COP por piso                            │
│ * Rango real: $3,500,000–$6,000,000 según especificación   │
│ Ejemplo: edificio 10 pisos = $45,000,000 COP              │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MODO AIRBNB COMPLETO                                        │
│ (si no está incluido en el plan elegido)                    │
│                                                             │
│ Setup integración Airbnb + Booking + VRBO: $2,500,000      │
│ Configuración WhatsApp Business API: $800,000              │
│ TOTAL instalación: $3,300,000 COP                         │
│ Mensualidad adicional: $250,000 COP/mes                    │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MONITOREO REMOTO 24/7 CON OPERADORES                       │
│ Centro de control revisa alertas, verifica ingresos        │
│ inusuales, contacta al administrador en tiempo real.       │
│                                                             │
│ Mensualidad: $450,000 COP/mes                              │
│ Tiempo de respuesta a alerta: < 3 minutos                  │
│ Turnos: operadores en 3 turnos, 365 días                   │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ REFERENCIA: PRESUPUESTO COMPLETO EDIFICIO TIPO             │
│ (10 pisos, 50 apartamentos — basado en cotizaciones reales  │
│ mayo 2026 Bogotá)                                          │
│                                                             │
│ Puerta lobby automática corredera + acceso facial: $30,000k │
│ Paneles vidrio vestíbulo ascensor × 10 pisos: $45,000k     │
│ Sistema de cámaras (14 cámaras ColorVu + NVR): $25,400k    │
│ Control ascensor (UHPPOTE 40ch): $8,600k                   │
│ Lectores + magnetós + controladores: $12,000k              │
│ Red PoE + UPS: $2,600k                                     │
│ Cableado estructurado: $5,000k                             │
│ Mano de obra (5 días, 3 técnicos + jefe): $8,400k          │
│ Software setup + comisionamiento: $3,000k                  │
│                                                             │
│ TOTAL ORIENTATIVO: $140,000,000 COP                       │
│ Rango confirmado en cotizaciones reales: $65M – $145M COP  │
│                                                             │
│ Mensualidad Plan Profesional (50 aptos): $1,250,000/mes    │
│ vs vigilancia 24/7 real: ~$12,900,000/mes                 │
│ Ahorro mensual: ~$11,650,000 COP                          │
│ ROI: ~12 meses (rango: 6–18 meses según tamaño)           │
└────────────────────────────────────────────────────────────┘
```

---

### FINANCING SECTION (below add-ons)

```
FINANCIACIÓN DISPONIBLE

¿Prefiere distribuir la inversión inicial?
Trabajamos con estas opciones:

● Pago de contado: descuento del 5%
● 6 cuotas iguales: sin interés
● 12 meses: tasa preferencial del 1.2% mensual
● 18–24 meses: financiación con entidad aliada (Bancolombia Leasing)

La copropiedad también puede aprobar el gasto como 
mejora de propiedad común en asamblea extraordinaria.
Podemos asistir a la asamblea para presentar la propuesta.
```

---

## SECTION 5 — QUOTE CALCULATOR PAGE (/cotizacion)

### Page concept
**This is the most important page on the site.** Dark background, feels like a premium SaaS tool. Three-step wizard. Price updates in real-time as user makes selections. No "submit" needed to see the estimate.

### Step 1 of 3 — Tu edificio
```
¿Cuántos apartamentos tiene el edificio?
  ○ 1 a 20      ○ 21 a 50     ○ 51 a 100     ○ Más de 100

¿Cuántos pisos?
  ○ 1 a 5       ○ 6 a 15      ○ 16 a 30      ○ Más de 30

¿El edificio tiene portero(s) actualmente?
  ○ Sí, 1 portero    ○ Sí, 2 o más    ○ No (es nuevo)
```

### Step 2 of 3 — Puntos de acceso y ascensores
```
Puntos de acceso peatonal (puertas que se quieren controlar):
  ○ 1 (entrada principal)    ○ 2    ○ 3    ○ 4 o más

¿Hay parqueadero con entrada vehicular independiente?
  ○ Sí, quiero automatizarla    ○ Sí, pero la dejo como está    ○ No hay parqueadero

¿Cuántos ascensores tiene el edificio?
  ○ Ninguno    ○ 1    ○ 2    ○ 3 o más

¿Hay puertas internas que quieras controlar?
(bicicletero, azotea, salón comunal, cuarto de servicio, etc.)
  ○ Ninguna    ○ 1 a 3    ○ 4 a 8    ○ Más de 8

¿Hay salidas de emergencia que deben desengancharse en incendio?
  ○ Sí    ○ No    ○ No sé, necesito asesoría
```

### Step 3 of 3 — Servicios y preferencias
```
¿Hay o habrá apartamentos en Airbnb, Booking o renta corta?
  ○ No    ○ Sí, 1 a 5 unidades    ○ Sí, más de 5    ○ El edificio es casi todo STR

¿Tu edificio tiene conexión a internet estable?
  ○ Sí, fibra óptica o cable (≥50 Mbps) — recomendado para nube IAccess
  ○ Solo móvil / inestable — consultar opción edge
  ○ No sé

¿Quieres monitoreo remoto con operadores humanos 24/7?
  ○ Sí, quiero seguridad activa    ○ No, prefiero gestión autónoma por app

¿Cuándo quisieras instalar?
  ○ Lo antes posible (este mes)
  ○ En 1 a 3 meses
  ○ Estoy evaluando opciones
```

### Live price display (right panel, updates in real-time)

```
╔══════════════════════════════════════════════════╗
║  ESTIMADO EN TIEMPO REAL                         ║
║                                                  ║
║  Inversión inicial:                              ║
║  $65,000,000 – $145,000,000 COP                  ║
║  (cotizaciones reales Bogotá 2026)               ║
║                                                  ║
║  Mensualidad:                                    ║
║  $900,000–$1,500,000 COP/mes                     ║
║                                                  ║
║  ─────────────────────────────────────           ║
║  COMPARACIÓN CON TU PORTERÍA ACTUAL:             ║
║                                                  ║
║  Portería 24/7 (dato real): $12,900,000/mes      ║
║  Costo IAccess:              $1,250,000/mes      ║
║  Ahorro mensual:            $11,650,000/mes      ║
║                                                  ║
║  Retorno de inversión:       ~9–12 meses         ║
║                                                  ║
║  ─────────────────────────────────────           ║
║  HARDWARE INCLUIDO:                              ║
║  ✓ 2× Hikvision DS-K1T671M (entrada)             ║
║  ✓ 1× Hikvision DS-K1T342 (secundario)           ║
║  ✓ 1× videoportero DS-KD8003                     ║
║  ✓ 3× magnetos 1200 lb                           ║
║  ✓ 8× cámaras 4MP ColorVu                        ║
║  ✓ NVR 16ch + 4TB (AI en nube IAccess)           ║
║  ✓ Control 1 ascensor                            ║
║                                                  ║
╚══════════════════════════════════════════════════╝
```

### After step 3: Lead capture

```
HEADLINE: Listo. Tu estimado está calculado.

Para enviarte la cotización detallada por PDF:

[Nombre completo          ]
[Correo electrónico        ]
[WhatsApp (+57 320 343 8733)]
[Nombre o dirección del edificio]
[Opcional: cuéntanos algo más...]

[Recibir cotización PDF gratis →]

Respuesta en menos de 2 horas en días hábiles.
Visita técnica gratis coordinada en 48 horas.
```

---

## SECTION 6 — HOW IT WORKS PAGE (/como-funciona)

### Section 1: The process (6 steps with visuals)

```
PASO 1 — COTIZAS EN LÍNEA (5 minutos)
Usa nuestra calculadora. Obtienes un rango de precio honesto y el 
desglose de hardware. Sin llamadas previas. Sin datos inventados.

PASO 2 — VISITA TÉCNICA GRATUITA (24–48 horas)
Un técnico senior de IAccess visita tu edificio.
Lo que evaluamos:
• Tipo y estado de las puertas actuales (marco, ancho de vano)
• Infraestructura eléctrica disponible (tomacorrientes, tableros)
• Ductos y canalizaciones existentes para cableado
• Posición de ascensores y cuartos de máquinas
• Número y ubicación de salidas de emergencia
• Velocidad y tipo de internet (para decisión local vs nube)
Al finalizar: propuesta técnica exacta + cronograma en 48 horas.

PASO 3 — PROPUESTA Y APROBACIÓN
Recibes PDF con:
• Lista exacta de equipos (modelos, referencias, garantías)
• Diagrama de planta con ubicación de cada cámara y lector
• Cronograma día a día de la instalación
• Costos desglosados (hardware, obra, labor, software)
• Opciones de financiación
• Contrato de servicio mensual
Sin presión. Tienes 30 días para decidir.

PASO 4 — INSTALACIÓN (2–8 días hábiles)
Nuestros técnicos trabajan en horario que no afecte a los residentes.
Día 1: tendido de cableado estructurado (CAT6 + conduit)
Día 2: montaje de cámaras, lectores y magnetos
Día 3: instalación de NVR, switch PoE
Día 4: programación, creación de base de datos facial inicial
Día 5+: pruebas, ajustes, registro de residentes

El acceso nunca se interrumpe durante la instalación.
Los porteros actuales siguen en funciones hasta que hagas el corte.

PASO 5 — REGISTRO DE RESIDENTES
Cada residente se registra en 2 minutos desde la app o en el panel:
• Foto para reconocimiento facial
• Huella dactilar (opcional)
• Número de apartamento y perfil de acceso
• QR permanente para su uso personal
Niños: se registran con acompañante adulto.
Adultos mayores: opción de solo NFC/tarjeta si prefieren.

PASO 6 — ACTIVACIÓN Y CAPACITACIÓN
Prueba en vivo con residentes voluntarios.
Sesión de 30 minutos con el administrador sobre la app.
El portero hace su último turno.
A partir de ese momento: tu edificio funciona solo.
```

---

### Section 2: Technical architecture (for building managers who need detail)

```
ARQUITECTURA DEL SISTEMA

                        [INTERNET]
                            |
        ┌───────────────────┼───────────────────┐
        |                   |                   |
   [App Admin]         [App Residente]     [API Airbnb]
        |                   |                   |
        └───────────────────┼───────────────────┘
                            |
              [INFRAESTRUCTURA IA — NUBE IACCESS]
               GPU RTX 4060 Ti / RTX 4090 (según plan)
               YOLOv8 + DeepStream · Base datos facial
               Gestionado por IAccess · SLA garantizado
                            |
              ┌─────────────┼─────────────┐
              |             |             |
         [Switch PoE]   [NVR 16ch]    [Control hub]
              |                           |
    ┌────┬────┬────┐              ┌───────┬───────┐
 [Cam1][Cam2][Cam3]            [Lector1][Magneto1]
  Entrada Lobby Pkng            Entrada  Puerta

CABLEADO:
• Todo sobre CAT6 (PoE para cámaras y lectores)
• Conduit EMT de 1/2" en zonas expuestas
• Canaleta plástica en pasillos si no hay ducto
• UPS central protege todo el sistema

ANCHO DE BANDA REQUERIDO:
• Servidor local: funciona 100% sin internet
• Acceso remoto admin: 5 Mbps upload suficiente
• Sincronización cloud backup: 1 Mbps permanente
```

---

## SECTION 7 — SERVICES DETAIL PAGES

### /servicios/reconocimiento-facial

```
HEADLINE: La IA que conoce a cada persona de tu edificio.

SUBHEADLINE: Reconocimiento facial en 0.4 segundos, 24/7, 365 días.
Sin llaves. Sin tarjetas. Sin "¿quién eres?"

──────────────────────────────────────────────
CÓMO FUNCIONA

1. REGISTRO (una sola vez, 2 minutos)
   El residente abre la app IAccess y saca 3 fotos desde ángulos 
   diferentes. El sistema construye el mapa facial en 3D.
   También puede registrarse en el panel táctil del lobby.

2. IDENTIFICACIÓN CONTINUA
   Al entrar al edificio, la cámara exterior captura el rostro a 
   distancia de hasta 3 metros. El servidor AI (YOLO) compara en 
   tiempo real contra la base de datos. Resultado en < 400ms.

3. APERTURA AUTOMÁTICA
   Si es residente autorizado → puerta se abre + log "Ingreso autorizado 
   · María González · Apto 502 · 08:14:32"
   Si no está en base de datos → puerta permanece cerrada + alerta 
   push al admin con foto del evento.

4. TRACKING ENTRE PISOS
   Las cámaras de pasillo (opcional por piso) rastrean el trayecto 
   completo desde lobby hasta piso. El administrador puede ver en 
   tiempo real dónde está cada persona conocida en el edificio.

──────────────────────────────────────────────
HARDWARE UTILIZADO

Lector facial entrada — HIKVISION DS-K1T671M-E1 (Pro Series)
• Pantalla táctil 4.3" a color
• Reconocimiento facial en <0.5 seg, 99.9% precisión
• Capacidad: 50,000 rostros almacenados
• Funciona con mascarilla, en baja luz
• IP65 (resistente a lluvia y polvo)
• Módulo de huella dactilar integrado
• Lector de tarjeta NFC/Mifare incorporado
• Amazon USA: ~$450 · Precio Colombia (importado +25%): **~$2,400,000 COP**

Lector facial secundario — HIKVISION DS-K1T342MFWX-E1 (Value Series)
• Para accesos secundarios (parqueadero, zonas comunes)
• Pantalla 4.3", facial + huella + tarjeta
• 6,000 rostros almacenados
• IP65, PoE
• Amazon USA: ~$254 · Precio Colombia (importado +25%): **~$1,400,000 COP**

Cámara de vigilancia — HIKVISION DS-2CD2347G2-LU (4MP ColorVu)
• Imagen en color 24/7 (sin modo IR — imagen color real de noche)
• 4MP resolución, lente 4mm (FOV 84°) o 2.8mm (FOV 108°)
• Detección de movimiento AI (persona vs. vehículo vs. animal)
• Micrófono incorporado
• IP67 resistente a intemperie
• PoE 12W
• Amazon USA: ~$300 · Precio Colombia (importado +25%): **~$1,600,000 COP/unidad**

Infraestructura de IA — MODELO SAAS (IACCESS CLOUD)
IAccess NO vende ni instala un servidor en tu edificio.
La IA corre en nuestra infraestructura GPU en la nube,
gestionada y mantenida 100% por nosotros.

¿Por qué este modelo?
• Sin hardware que envejece en tu edificio
• Actualizamos el modelo YOLO sin intervención en sitio
• Escalamos automáticamente si agregas cámaras
• El costo está cubierto en tu mensualidad — sin sorpresas
• SLA garantizado por contrato: 99.9% uptime

¿QUÉ GPU USA IACCESS POR PLAN?

Plan Esencial (hasta 6 cámaras / <25 aptos):
→ NVIDIA RTX 4060 Ti 16GB compartida (multi-tenant)
   Arquitectura Ada Lovelace · 4,352 CUDA cores
   Procesa 6–10 streams YOLO con TensorRT FP16
   Latencia de inferencia: <400ms

Plan Profesional (hasta 14 cámaras / 25–70 aptos):
→ NVIDIA RTX 4060 Ti 16GB dedicada a tu edificio
   Stack: NVIDIA DeepStream 8.0 + YOLOv8 + OSNet ReID
   Inferencia por lotes — todos los streams en una sola
   llamada GPU (no una instancia por cámara)
   Latencia: 280–380ms con TensorRT FP16

Plan Enterprise (hasta 20+ cámaras / 70+ aptos):
→ NVIDIA RTX 4090 24GB dedicada
   24GB VRAM soporta 20 streams YOLO simultáneos
   Cross-camera Re-ID (OSNet-x1.0): rastrea la misma
   persona entre pisos sin necesidad de re-identificación
   manual
   Latencia: 230–320ms con TensorRT INT8

──────────────────────────────────────────────
SEGURIDAD Y PRIVACIDAD

• Base de datos biométrica encriptada AES-256
• Datos en nube IAccess (Hetzner, ISO 27001, GDPR) — encriptados con clave del edificio
• Cada residente firma autorización de tratamiento de datos (Ley 1581)
• Derecho al olvido: el administrador borra el perfil en 30 segundos
• Los logs de ingreso se guardan por 90 días (configurable)
• Backup encriptado en nube con llave del edificio (no de IAccess)

──────────────────────────────────────────────
PRECIOS

Punto de acceso con facial (lector Pro + magneto + controlador):
Desde $5,300,000 COP (hardware: DS-K1T671M $2,400k + magneto 1200 lb $940k + controlador $540k + salidas $260k) + $1,200,000 COP (instalación)
Total: desde $6,500,000 COP por punto de acceso

Incluido en: Plan Profesional y Plan Enterprise
Opcional en: Plan Esencial (+$4,100,000 COP)
```

---

### /servicios/control-ascensores

```
HEADLINE: Tu ascensor sabe a qué piso puede llevarte.
Los residentes suben a su piso. Solo a su piso.
Los guests de Airbnb también. Solo a su piso.
Los visitantes, al piso que el residente autorizó. Nada más.

──────────────────────────────────────────────
EL PROBLEMA SIN IACCESS

Sin control de ascensor, el sistema de acceso al lobby solo resuelve 
la mitad del problema. Un visitante no autorizado que logra entrar 
(por tailgating, por un residente descuidado) puede subir a cualquier 
piso y tocar cualquier puerta.

Con IAccess, el ascensor es la segunda barrera.

──────────────────────────────────────────────
CÓMO FUNCIONA

ASCENSOR NORMAL (sin IAccess):
Usuario entra → presiona piso → sube. Cualquier piso. Siempre.

ASCENSOR CON IACCESS:
1. Usuario entra al ascensor
2. Lector facial en cabina lo identifica (opcional: QR o tarjeta)
3. El sistema consulta: "¿a qué piso puede ir esta persona?"
4. Solo los botones autorizados se activan
5. Usuario presiona su piso → sube
6. El log registra: "María González · Sube piso 5 · 08:15:01"

PERFILES DE ACCESO:
• Residente apto 502: puede ir al piso 5, lobby, parqueadero B2
• Guest Airbnb apto 502: puede ir al piso 5 y lobby (horario 3pm–11am)
• Técnico de mantenimiento: puede ir a cualquier piso (acceso temporal 24h)
• Visitante autorizado por Apto 301: puede ir al piso 3 (acceso 2h)
• Repartidor de domicilios: puede ir al piso indicado, regresa en 20 min
• Administrador: acceso total permanente

──────────────────────────────────────────────
HARDWARE UTILIZADO

CONTROLADOR DE PISOS — UHPPOTE 20/40 CANALES
• Controla hasta 20 (o 40) botones de piso simultáneamente
• Se conecta entre la botonera del ascensor y los relés
• Comunicación: RS-485 o TCP/IP con el servidor IAccess
• Compatible con cualquier marca de ascensor (Schindler, ThyssenKrupp,
  OTIS, MIPSA, IMSA, y locales colombianos)
• Amazon USA: $330 (20ch) / $500 (40ch) · Precio Colombia (+25% imp.):
  UHPPOTE 20ch **~$1,800,000 COP** · 40ch **~$2,700,000 COP**

LECTOR EN CABINA — HIKVISION DS-K1T342MFWX-E1
• Panel compacto montado en la pared interior del ascensor
• Reconocimiento facial + QR + tarjeta NFC
• Activación: cuando el usuario sube, identifica → habilita sus pisos
• Amazon USA: $254 · Precio Colombia (+25% imp.): **~$1,400,000 COP** por lector

CÁMARA EN CABINA (opcional)
• Cámara domo mini IP antivandálica 2MP
• Almacena en el NVR general del edificio
• Complementa el tracking facial por piso
• Precio: ~$1,200,000 COP instalada

──────────────────────────────────────────────
PROCESO DE INSTALACIÓN

La instalación de control de ascensor requiere acceso al cuarto 
de máquinas y coordinación con la empresa de mantenimiento del 
ascensor (OTIS, Schindler, etc.). No modifica el funcionamiento 
mecánico — solo agrega una capa de control electrónico.

Tiempo: 1 día por ascensor (8 horas)
Personal: 1 técnico senior IAccess + apertura empresa ascensores

NOTA IMPORTANTE: Coordinamos nosotros con la empresa de ascensores. 
El cargo por apertura técnica de la empresa (normalmente $800,000–
$1,500,000 COP) está incluido en nuestra cotización.

──────────────────────────────────────────────
PRECIO

Por ascensor, edificio hasta 20 pisos:   $6,800,000 COP (instalado)
Por ascensor, edificio 21–40 pisos:      $8,600,000 COP (instalado)
Lector en cabina (adicional):            +$1,900,000 COP
Cámara en cabina (adicional):            +$1,100,000 COP

Incluido en: Plan Profesional (1 ascensor), Plan Enterprise (2 ascensores)
Disponible como add-on en: Plan Esencial
```

---

### /servicios/modo-airbnb

```
HEADLINE: Tu edificio Airbnb-ready.
El único sistema en Colombia diseñado para edificios con renta corta.

──────────────────────────────────────────────
LA REALIDAD DEL MERCADO EN BOGOTÁ

En edificios de estratos 4, 5 y 6 en Chapinero, Usaquén, 
Zona Rosa y La Candelaria, entre el 15% y el 40% de los 
apartamentos pueden estar activos en Airbnb o Booking en 
cualquier momento.

Un portero convencional maneja esto con WhatsApp, llamadas 
y notas en papel. Los problemas:
• Guests esperando en la calle a las 2am mientras el portero duerme
• Portero abre sin verificar si la reserva es real
• Sin registro ni foto del ingreso — impossible de auditar
• Vecinos se quejan de extraños en el ascensor
• La copropiedad no tiene control de cuántos guests entran

IAccess resuelve esto de raíz con integración directa.

──────────────────────────────────────────────
CÓMO FUNCIONA — PASO A PASO

PARA EL PROPIETARIO/ANFITRIÓN:
1. Conecta tu cuenta Airbnb/Booking a IAccess (1 vez, 5 minutos)
2. Desde ese momento, cada reserva confirmada dispara el flujo automático
3. No haces nada más.

PARA EL SISTEMA (automático):
• Reserva confirmada → IAccess genera QR único
• QR tiene ventana exacta: desde hora check-in hasta hora check-out
• QR enviado por WhatsApp al número del huésped (en el idioma del perfil)
• QR habilita: lobby, ascensor hasta piso X, zonas comunes hasta las 10pm
• QR bloquea: todos los demás pisos, cuarto de máquinas, azotea restringida

PARA EL HUÉSPED:
• Llega al edificio, abre WhatsApp, muestra QR al panel
• Panel escanea, verifica ventana temporal, abre
• Sube en ascensor al piso correcto (solo ese piso disponible)
• Entra. Sin portero. Sin llamadas. Sin esperas.
• Al hacer checkout: QR ya no funciona. Acceso cerrado.

──────────────────────────────────────────────
CONTROL PARA LA COPROPIEDAD

Muchas copropiedades tienen conflictos con los propietarios que 
arriendan en Airbnb. IAccess los resuelve dando más información 
y más control, no menos:

• Reporte mensual automático: cuántos guests entraron, cuándo, 
  en qué piso, qué duración
• Alerta si un QR de guest es compartido con más personas de las 
  registradas (detección de duplicados)
• Restricción de zonas comunes por horario (ej: gym solo residentes, 
  BBQ disponible para guests hasta las 9pm con reserva)
• La copropiedad puede vetar el modo Airbnb para un apartamento 
  específico con un clic (si la asamblea así lo decide)

──────────────────────────────────────────────
INTEGRACIONES DISPONIBLES

✅ Airbnb (vía API oficial)
✅ Booking.com (vía Channel Manager)
✅ VRBO / Vrbo
✅ WhatsApp Business API
📅 Próximamente: Despegar, Comohotel, gestores de propiedad locales

──────────────────────────────────────────────
PRECIO

Setup inicial + integración (una sola vez): $3,300,000 COP
Mensualidad modo Airbnb: $280,000 COP/mes

Incluido sin costo adicional en: Plan Enterprise
Incluido en mensualidad de: Plan Profesional
```

---

### /servicios/puertas-automaticas

```
HEADLINE: La puerta correcta para tu edificio.
Vidrio, aluminio, magneto o automatizada — instalamos la que necesitas.

──────────────────────────────────────────────
TIPOS DE INSTALACIÓN

──── OPCIÓN 1: ADAPTACIÓN DE PUERTA EXISTENTE ────

Si tu puerta ya existe y solo necesitas agregar control de acceso:

Hardware requerido:
• Magneto electromagnético 600–1200 lb (según peso de la puerta)
• Lector facial o QR (montado sobre el marco)
• Botón de salida (interior)
• Controlador de acceso
• Fuente de poder + UPS

Precio: desde $3,570,000 COP por puerta
Obra requerida: ninguna en la mayoría de casos
Tiempo: 1 día

──── OPCIÓN 2: PUERTA DOBLE BATIENTE DE VIDRIO ────
(entrada principal nueva o reemplazo)

Ideal para edificios que quieren modernizar la imagen del lobby.

Incluye:
• Evaluación del vano existente (obra solo si es necesario ensanchar)
• Marco de aluminio anodizado negro o gris oscuro
• 2 hojas de vidrio templado 10mm con herraje oculto
• 2 magnetos 1200 lb (uno por hoja)
• 2 lectores faciales Pro DS-K1T671M
• Controlador + UPS + cableado
• Pintura o estuco en el área intervenida

Precio total instalado:
  Vano existente suficiente (sin obra civil):  $16,600,000 COP
  Requiere ensanche de vano (no estructural):  $19,100,000 – $21,600,000 COP
  Requiere obra estructural (refuerzo, viga):  $23,100,000 – $29,100,000 COP

Tiempo: 3–5 días (incluye curado de materiales)
Nota: Se coordina con el administrador para no interrumpir el acceso.
Se instala primero la parte electrónica en la puerta temporal, 
luego se reemplaza la puerta.

──── OPCIÓN 3: PUERTA AUTOMÁTICA DESLIZANTE DE VIDRIO ────
(la opción más elegante y más fluida para edificios de alto tráfico)

Incluye:
• Marco de aluminio de alta resistencia (anodizado negro o silver)
• 2 paneles de vidrio templado deslizantes (apertura biparting)
• Operador automático de bajo consumo (rango de uso: 2M ciclos)
• Sensor de presencia dual (activa apertura automática desde adentro)
• Lector facial exterior + lector QR
• Magneto de seguridad fail-safe (abre ante corte de energía)
• Botón de apertura manual de emergencia
• Obra civil: guía de piso + riel superior + sellado perimetral

Precio total instalado:
  Vano estándar 1.8–2.4m de ancho: $18,200,000 – $22,000,000 COP
  Vano más ancho (2.4–4m):         $25,000,000 – $33,000,000 COP

Tiempo: 4–6 días
Garantía en operador: 2 años

──── OPCIÓN 4: MAGNETO EN PUERTAS INTERNAS ────
(para puertas ya instaladas en el edificio: bicicletero, azotea, 
cuarto de servicio, salón comunal, cuarto de basuras)

Incluye por puerta:
• Magneto 600 lb + placa de contraposición
• Lector RFID (sin facial — para interiores donde no justifica el costo)
• Botón de salida interior

Precio: $1,400,000 COP por puerta (sin facial)
Con lector facial: $2,500,000 COP por puerta

──────────────────────────────────────────────
ESPECIFICACIÓN TÉCNICA DE MAGNETOS

| Magneto | Fuerza | Uso recomendado | Amazon USA | Colombia (+25% imp.) |
|---|---|---|---|---|
| 600 lb / 272 kg | Media | Puertas livianas, interiores | $114 | **$610,000 COP** |
| 1200 lb / 544 kg | Alta | Puertas principales, vidrio pesado | $175 | **$940,000 COP** |
| 1800 lb / 816 kg | Muy alta | Portones vehiculares, acceso industrial | $310 | **$1,666,000 COP** |

Todos los magnetos son fail-safe (se abren al perder energía) y 
cumplen con normas de seguridad contra incendio NFPA y RETIE.

──────────────────────────────────────────────
DESENGANCHE EN EMERGENCIA

Todos los magnetos de IAccess se integran al sistema de alarma 
contra incendios del edificio (si existe) y a un botón de 
emergencia protegido con tapa roja.

Ante señal de alarma o activación del botón:
→ Los magnetos de TODAS las salidas de emergencia se desactivan simultáneamente
→ Las puertas abren libremente
→ Log del evento guardado con timestamp

Precio integración de emergencia: $1,200,000 COP por salida de emergencia
(Incluye relé de interfaz, botón protegido, programación, 
prueba documentada, certificado de conformidad)
```

---

## SECTION 8 — TECHNOLOGY PAGE (/tecnologia)

```
HEADLINE: Tecnología probada. Integrada para Colombia.

SUBHEADLINE:
No reinventamos el hardware. Usamos el mejor del mundo.
Construimos la integración que Colombia necesita.

──────────────────────────────────────────────
HIKVISION — EL HARDWARE

Hikvision es el fabricante de cámaras de seguridad más vendido 
del mundo. Presente en aeropuertos, hospitales, centros comerciales 
y edificios residenciales en más de 150 países.

¿Por qué Hikvision y no otra marca?

• Distribuidores autorizados en Bogotá (SYSCOM, EQUIREDES, 
  HELITEB) → garantía local, repuestos disponibles
• Mayor densidad de técnicos certificados en Colombia
• Portfolio completo: cámaras + lectores + NVR + videoportero 
  + control de acceso → un solo fabricante, una sola integración
• Actualizaciones de firmware gratuitas durante 5+ años
• Precio/rendimiento: mejor relación en su categoría
• Soporte técnico en español

Equipos que usamos:

CÁMARAS:
  DS-2CD2347G2-LU (4MP ColorVu, la más popular en edificios)
  DS-2CD2387G2-LU (8MP 4K, para entradas y lobbies clave)
  
LECTORES DE ACCESO:
  DS-K1T671M-E1 (Pro, entrada principal)
  DS-K1T342MFWX-E1 (Value, puntos secundarios)
  
VIDEOPORTERO:
  DS-KD8003-IME1 (estación exterior)
  DS-KH6320-WTE1 (monitor interior de apartamento, opcional)
  
NVR:
  DS-7616NXI-K2/16P (16ch, 4K, PoE, AcuSense AI nativo)

──────────────────────────────────────────────
YOLO — LA INTELIGENCIA ARTIFICIAL

YOLO (You Only Look Once) es el modelo de detección de objetos en 
tiempo real más usado en el mundo, publicado originalmente por la 
Universidad de Washington en 2015 y continuamente mejorado.

En IAccess usamos YOLOv8 con entrenamiento especializado en:
• Reconocimiento facial en condiciones latinoamericanas 
  (diversidad étnica, lighting de clima tropical, 
   ángulos de cámara en lobbies con diferencia de altura)
• Re-identificación entre cámaras (re-ID): 
  reconoce a la misma persona aunque cambie de ángulo o 
  piso, sin necesidad de que esté en primer plano
• Detección bajo mascarilla, gorra o lentes oscuros

RENDIMIENTO REAL (probado en pilot buildings):
• Latencia de identificación: 280–420ms promedio
• Tasa de verdadero positivo (TPR): 99.7%
• Tasa de falso positivo (FPR): <0.001% 
  (en una semana con 50,000 ingresos: ~0.5 falsos positivos)
• Funciona bajo 5 Lux (casi oscuridad total)

──────────────────────────────────────────────
POR QUÉ YOLO NECESITA UNA GPU — NO UN COMPUTADOR CORRIENTE

YOLO (You Only Look Once) ejecuta una red neuronal convolucional
profunda sobre cada frame de cada cámara, en tiempo real.
Un edificio con 8 cámaras produce ~240 frames por segundo.

Un procesador Intel (NUC, mini-PC, i7 de escritorio) puede
hacer inferencia YOLO en 1 o 2 cámaras a baja resolución.
Con 8 cámaras: la latencia supera los 3–5 segundos — la puerta
no puede abrirse con esa demora. Con re-identificación entre
cámaras (cross-camera ReID): el NUC colapsa completamente.

Para hacer esto correctamente se necesita una GPU con:
• Mínimo 8GB VRAM (16GB recomendado)
• Soporte CUDA para inferencia paralela por lotes
• Stack: NVIDIA DeepStream 8.0 + TensorRT FP16/INT8

CÓMO FUNCIONA LA INFERENCIA POR LOTES:

Todos los streams de cámaras se agrupan en un solo tensor
y se pasan a la GPU en una única llamada de inferencia.
No hay una instancia de YOLO por cámara — hay una instancia
que procesa todas simultáneamente. Esto multiplica por 8–12×
la eficiencia de cómputo versus el modelo naive.

Resultado: latencia de 230–420ms con RTX 4060 Ti o 4090,
en lugar de los 3,000–5,000ms de un NUC sin GPU dedicada.

──────────────────────────────────────────────
MODELO SAAS: IACCESS GESTIONA LA INFRAESTRUCTURA

IAccess no vende ni instala un servidor en tu edificio.
La IA corre en nuestra infraestructura GPU en la nube,
gestionada, actualizada y monitoreada por nuestro equipo.

¿QUÉ SIGNIFICA ESTO PARA TI?
• Sin hardware que deprecia en tu edificio
• Sin actualizaciones manuales — nosotros las hacemos en la noche
• Sin riesgo de que el "servidor del edificio" falle a las 3am
• El costo de cómputo está incluido en tu mensualidad
• Cuando agregamos cámaras, escalamos la GPU automáticamente

¿DÓNDE ESTÁN LOS SERVIDORES?
Utilizamos infraestructura de cómputo en Europa 
(Hetzner Data Center, certificado ISO 27001, GDPR) 
con túneles encriptados TLS 1.3 a tu edificio.
Latencia típica Bogotá–Europa: 140–180ms.
Latencia de procesamiento YOLO: 230–380ms.
Latencia total puerta a apertura: <600ms — imperceptible.

TUS DATOS BIOMÉTRICOS:
Permanecen encriptados (AES-256) en tránsito y en reposo.
La base de datos facial NO es accesible por ningún tercero,
ni por Hetzner — opera con clave de encriptación del edificio.

GPU POR PLAN:
• Plan Esencial:      RTX 4060 Ti 16GB (compartida, hasta 6 cams)
• Plan Profesional:   RTX 4060 Ti 16GB (dedicada, hasta 14 cams)
• Plan Enterprise:    RTX 4090 24GB (dedicada, hasta 20+ cams)

──────────────────────────────────────────────
RESILIENCIA OFFLINE — ¿QUÉ PASA SI SE CAE INTERNET?

PREGUNTA CLAVE: "¿Si se va el internet funciona la puerta?"
Respuesta: Sí. Así funciona la redundancia:

CAPA 1 — LECTORES HIKVISION CON IA LOCAL (siempre activa)
El lector DS-K1T671M-E1 tiene un procesador facial propio.
Almacena hasta 50,000 rostros localmente en el dispositivo.
La apertura de puerta principal por reconocimiento facial
NO depende del internet ni de la nube — corre en el lector.

CAPA 2 — NVR HIKVISION DS-7616NXI-K2 (siempre grabando)
El NVR graba las 16 cámaras en su disco local de 4TB
independientemente de si hay internet. Tiene AI AcuSense
para detectar personas (no identificarlas). Si el edificio
pierde conexión, el video sigue grabando sin interrupción.

CAPA 3 — CACHE QR LOCAL EN CONTROLADORES
Los QR de residentes y guests activos se sincronizan cada
15 minutos al controlador local. Un corte de internet de
horas no afecta los accesos QR ya en el cache.

CAPA 4 — UPS (autonomía 4 horas mínimo)
El UPS cubre un corte de luz. Puertas, cámaras, lectores
y controladores siguen operando sin interrupción.

¿QUÉ SÍ REQUIERE NUBE?
Solo las funciones avanzadas: tracking cross-floor (ReID),
alertas push en tiempo real al admin, sincronización de
nuevos rostros o QR, dashboard web en vivo.
Estas se suspenden durante el corte y se reanudan solas.

RESUMEN PARA EL ADMINISTRADOR:
• Internet caído → puertas abren con cara/QR: ✅ FUNCIONA
• Internet caído → videos grabados: ✅ FUNCIONA
• Internet caído → tracking en pisos: ⏸ SUSPENDIDO
• Internet caído → alertas push: ⏸ SUSPENDIDO
• Luz cortada (con UPS) → todo lo anterior: ✅ 4 HORAS

──────────────────────────────────────────────
PRIVACIDAD Y LEY 1581

El manejo de datos biométricos en Colombia está regulado por:
• Ley 1581 de 2012 (Habeas Data) y su Decreto 1377 de 2013
• Circular Externa 001 de 2020 de la SIC sobre datos sensibles

Nuestro cumplimiento:
✓ Cláusula de tratamiento de datos biométricos firmada por 
  cada residente y visitante frecuente
✓ Propósito específico: control de acceso (no publicidad, 
  no venta, no intercambio con terceros)
✓ Acceso limitado: solo el administrador y el sistema
✓ Derecho de supresión: perfil eliminado en < 30 segundos 
  ante solicitud del titular
✓ Encriptación en reposo (AES-256) y en tránsito (TLS 1.3)
✓ Registros de auditoría de quién accedió a los datos
✓ Política de retención: logs de ingreso por 90 días; 
  datos biométricos mientras el contrato esté activo

En caso de inspección de la SIC: 
Entregamos documentación de cumplimiento en < 24 horas.
```

---

## SECTION 9 — CASE STUDIES (/casos-de-uso)

### Case Study 1: Edificio residencial tradicional

```
HEADLINE: De 3 porteros a cero — sin que un solo residente 
extrañara el portero.

EDIFICIO: Torres del Parque 93 Norte
UBICACIÓN: Calle 93 #15-38, Bogotá
CARACTERÍSTICAS: 48 apartamentos · 12 pisos · 2 ascensores
INSTALADO: Octubre 2025

EL PROBLEMA:
La copropiedad gastaba $10.2M COP al mes en 3 porteros 
(diurno + nocturno + fin de semana). El portero nocturno 
faltaba en promedio 2 veces al mes. El cuaderno de visitas 
tenía datos incompletos. Un robo en el piso 7 (agosto 2025) 
no pudo esclarecerse — no había registro confiable de quién 
subió ese día.

LA SOLUCIÓN:
Plan Profesional + control de 2 ascensores + 4 cámaras adicionales 
en pisos 1, 4, 7 y 12

HARDWARE INSTALADO:
• 2× Hikvision DS-K1T671M-E1 (entrada principal)
• 1× Hikvision DS-K1T342MFWX-E1 (acceso parqueadero)
• 1× videoportero DS-KD8003-IME1
• 3× magnetos 1200 lb
• 2× controladores de ascensor UHPPOTE 40ch
• 14× cámaras 4MP ColorVu (lobby + parkeo + pisos 1/4/7/12 + ascensores)
• NVR 16ch + 8TB
• Infraestructura GPU en nube IAccess (RTX 4060 Ti)
• UPS 1500VA
• Switch PoE 24 puertos

TIEMPO DE INSTALACIÓN: 4 días hábiles

RESULTADOS (4 meses después):
💰 Ahorro mensual: $8,900,000 COP (3 porteros $10.2M → mensualidad $1,300,000)
⏱ Retorno de inversión alcanzado: mes 7 (proyectado)
📊 Ingresos registrados en 4 meses: 42,381
🔍 Alertas de ingreso no autorizado resueltas: 14 (tailgaters detectados)
😊 Satisfacción de residentes (encuesta interna): 91% positivo

TESTIMONIO:
"El mes 1 fue el más difícil — algunas personas mayores necesitaron 
ayuda para registrarse. El mes 2 ya era natural para todos. Lo que 
nadie esperaba es que los residentes empezaran a pedir más cámaras 
en los pisos altos — sienten más seguridad que con el portero."
— Carlos Esteban Mora, Administrador
```

---

### Case Study 2: Edificio con Airbnb

```
HEADLINE: 18 apartamentos en Airbnb. Cero llamadas al portero.

EDIFICIO: Chapinero Heights
UBICACIÓN: Carrera 7 #62-14, Bogotá  
CARACTERÍSTICAS: 32 apartamentos · 8 pisos · 18 en renta corta
INSTALADO: Septiembre 2025

EL PROBLEMA:
El 56% de los apartamentos del edificio están en plataformas de 
renta corta. El portero original renunció después de 3 meses — 
"muchos extraños, mucho trabajo, misma paga". El siguiente duró 
6 semanas. Los propietarios de Airbnb gestionaban ingresos por 
WhatsApp. Promedio de queja de vecinos: 4 por semana.

LA SOLUCIÓN:
Plan Profesional + Modo Airbnb completo

FLUJO AUTOMÁTICO CONFIGURADO:
• Integración directa con Airbnb, Booking.com y VRBO
• QR generado automáticamente al confirmar reserva
• Restricciones: guests solo a piso del apartamento, lobby y 
  zonas comunes hasta las 10pm (decisión de la copropiedad)
• Reportes semanales automáticos al presidente de copropiedad

RESULTADOS (3 meses):
📬 QRs generados automáticamente: 1,847
📞 Llamadas a propietarios para coordinar ingreso: 0
⏰ Tiempo de espera promedio de guests: 0 minutos
😤 Quejas de vecinos sobre guests: 2 (vs 4/semana antes)
💰 Ahorro en portería: $2,955,000 COP/mes (1 portero eliminado — incluyendo empresa de vigilancia)
   + costo evitado de rotación: ~$1,500,000 COP/mes (estimado)
📈 Valorización del edificio por los propietarios: "Vendemos 
   más rápido porque IAccess es una feature que los compradores piden"
```

---

## SECTION 10 — BLOG CONTENT (full article outlines)

### Article 1: Anchor piece for SEO

**Title:** ¿Cuánto cuesta realmente un portero en Colombia en 2026?  
**Target keyword:** costo portero Colombia  
**URL:** /blog/costo-portero-colombia-2026

```
INTRO (hook):
La mayoría de administradores de edificios conocen el salario del portero.
Muy pocos conocen el costo real.

Spoiler: un portero en Bogotá no cuesta $1.4M al mes. 
Cuesta más del doble.

H2: El salario mínimo no es el costo
[Tabla completa con todos los costos laborales: salario + 
auxilio transporte + salud + pensión + ARL + parafiscales + 
primas + vacaciones + cesantías = total ~$2,462,050 COP/portero]

H2: ¿Cuántos porteros necesita un edificio?
• Edificio hasta 20 aptos, solo diurno: 1 turno → **$4,300,000/mes** (empresa vigilancia all-in)
• Edificio 25-70 aptos, cobertura 24/7: 3 turnos → **$12,900,000/mes** (dato real Bogotá 2026)
• Edificio 70-120 aptos, 24/7 + 2/turno: 4-5 turnos → **$17,200,000–$21,500,000/mes**
• Edificio premium 120+ aptos: 6+ turnos → **$25,800,000+/mes**
[Fuente real: presupuesto 2026 edificio 10 aptos Bogotá = $12,894,398/mes vigilancia para 3 turnos]

H2: Los costos ocultos que nadie cuenta
• Incapacidad médica: ¿quién reemplaza? Agencia de empleo: $180,000–$300,000/día
• Vacaciones (15 días hábiles/año): ~$738,000 COP de reemplazo
• Dotación (botas, uniforme): ~$480,000 COP/año
• Liquidación al terminar contrato: ~$3,000,000–$6,000,000 COP una sola vez
• Horas extras y recargos nocturnos: +25–75% del salario por horas

H2: Comparación real con portería virtual
[Tabla de 5 años: portería tradicional vs IAccess]

H2: ¿Es legal despedir al portero para instalar un sistema?
[Explicación legal: sí, con preaviso según tiempo de antigüedad, 
causa justa o sin causa con indemnización. No es diferente a 
cualquier otro cambio de modelo operativo.]

CIERRE + CTA:
Calculadora de ahorro (enlace a /cotizacion)
```

---

### Article 5: Competitor comparison (SEO + decision-stage)

**Title:** Portea vs IAccess vs HIPCAM: ¿cuál portería virtual es mejor para tu edificio en Colombia?  
**Target keyword:** portería virtual Colombia comparación / mejor portería virtual edificio 2026  
**URL:** /blog/comparacion-porteria-virtual-colombia-2026

```
H2: Las 5 mejores opciones de portería virtual en Colombia en 2026
H2: Comparación por funcionalidad (tabla)
H2: Comparación por precio
H2: ¿Cuál necesita operador humano? (spoiler: todas menos IAccess)
H2: ¿Cuál cumple con el fallo SIC de agosto 2025?
H2: Qué preguntar antes de contratar cualquier servicio
CTA: Agenda visita técnica gratuita
```

### Article 2: SEO for Airbnb operators

**Title:** Cómo automatizar los ingresos de Airbnb en tu edificio en Bogotá  
**Target keyword:** Airbnb acceso automatico edificio Colombia  
**URL:** /blog/automatizar-airbnb-edificio-bogota

```
H2: El problema de gestionar Airbnb en un edificio con portero
H2: ¿Qué es un QR de acceso temporal?
H2: Cómo funciona la integración Airbnb + IAccess paso a paso
H2: ¿Qué pasa si el guest pierde el QR?
H2: ¿Puede la copropiedad prohibir el modo Airbnb?
H2: Costo: cuánto cuesta automatizar el acceso para 5 aptos de Airbnb
CTA: Cotización para tu edificio
```

---

### Article 3: SIC compliance (urgent SEO opportunity — almost nobody has written this)

**Title:** La SIC ordenó borrar datos biométricos en un conjunto en Bogotá: qué significa para tu edificio  
**Target keyword:** reconocimiento facial Colombia legal 2025 / fallo SIC biometría edificios  
**URL:** /blog/fallo-sic-reconocimiento-facial-edificios-colombia-2025

```
H2: Qué pasó en Parque de los Cipreses (agosto 2025)
H2: Qué exige la SIC ahora para usar reconocimiento facial
H2: Por qué el método único está prohibido — y qué significa eso para tu portería virtual
H2: Qué debe firmar cada residente (plantilla descargable gratuita)
H2: ¿Mi portería virtual actual cumple? (checklist de 8 puntos)
H2: Cómo IAccess resuelve esto por diseño
CTA: Descarga el checklist de cumplimiento + cotización
```

### Article 4: Legal piece

**Title:** Reconocimiento facial en edificios de Colombia: ¿es legal?  
**Target:** personas que buscan antes de comprar  
**URL:** /blog/reconocimiento-facial-edificios-colombia-legal

```
H2: ¿Qué dice la Ley 1581 sobre datos biométricos?
H2: ¿Qué obligaciones tiene la copropiedad?
H2: El reconocimiento facial vs. el cuaderno de visitas (cuál es más legal)
H2: Cómo IAccess cumple con la normativa
H2: ¿Qué pasa si un residente se niega a registrarse?
H2: El concepto de "dato sensible" y cómo manejarlo
CTA: Ver política de privacidad + solicitar cotización
```

---

### Article 6: GuardIA comparison (decision-stage, high-intent)

**Title:** GuardIA (Appmosfera) vs IAccess: ¿cuál reemplaza realmente al portero?  
**Target keyword:** guardia appmosfera portería virtual / appmosfera vs portería virtual Colombia  
**URL:** /blog/guardia-appmosfera-vs-iaccess-porteria-virtual

```
INTRO (hook):
GuardIA detecta que alguien rompió una ventana a las 2am.
Pero nadie abre la puerta.
Esa diferencia cuesta $4,000,000 COP al mes.

H2: ¿Qué hace GuardIA realmente?
  Explicación neutral y honesta: GuardIA es una capa de 
  inteligencia sobre cámaras existentes. Detecta amenazas, 
  envía alertas a la empresa de vigilancia asociada. No controla 
  acceso. No tiene app de residentes. No integra Airbnb.

H2: Lo que GuardIA hace bien (y que IAccess también hace)
  • Conectar cámaras existentes — IAccess tiene el mismo tier 
    "sin hardware nuevo" para cámaras Hikvision compatibles
  • Detección de incendio, humo, caídas — YOLOv8 de IAccess hace 
    esto nativamente, incluido en Plan Profesional y Enterprise
  • "Hecho en Colombia" — IAccess también, con sede en Bogotá

H2: La diferencia fundamental: ¿quién abre la puerta?
  [Diagrama / tabla comparando el flujo de decisión de cada sistema]
  GuardIA: Cámara → AI detecta → alerta a empresa de vigilancia → 
           humano decide → ¿llama policía? ¿va al sitio? (tarda minutos)
  IAccess: Cámara → AI reconoce rostro → puerta se abre en 400ms → 
           registro automático con foto (cero humanos en el loop)

H2: El costo real de cada opción
  GuardIA (surveillance) + empresa de vigilancia (física) = 
  todavía pagando vigilancia + hardware de cámaras = coste total alto
  
  IAccess = elimina la empresa de vigilancia por completo. 
  Un edificio de 50 aptos ahorra $4,000,000–$6,000,000 COP/mes.

H2: Si ya tienes GuardIA instalado — ¿qué haces?
  Opción A: Añadir IAccess sobre las cámaras existentes 
  (IAccess Software Tier es compatible con cámaras Hikvision).
  Ahora tienes surveillance + access control. Completo.
  
  Opción B: Reemplazar GuardIA completamente y 
  ahorrar la cuota mensual de GuardIA.

H2: Tabla de comparación final
  [La misma tabla del homepage — ver sección de Comparación de Competidores]

CTA: Agenda una visita técnica gratuita — en 48 horas sabemos 
     si tus cámaras son compatibles con IAccess.
```

---

## SECTION 11 — CONTACT PAGE (/contacto)

```
HEADLINE: Hablamos.

No hay chatbot. No hay formulario que desaparece en el vacío.
Cuando nos escribes, responde una persona real en Bogotá.

─────────────────────────────────────────────
FORMAS DE CONTACTO:

[WhatsApp →]                    [Email →]
+57 320 343 8733               info@iaccess.co
Respuesta < 1 hora             Respuesta < 4 horas
Lun–Sáb 8am–8pm               Lun–Sáb 8am–8pm

─────────────────────────────────────────────
AGENDA UNA VISITA TÉCNICA GRATUITA

[Calendly embed o similar]
• Visitas disponibles: Lunes a Sábado 8am–6pm
• Zona de cobertura actual: Bogotá (todas las localidades)
• Duración: 1 hora en tu edificio
• Costo: gratuito, sin compromiso

─────────────────────────────────────────────
FORMULARIO DE CONTACTO:

Nombre completo: [________________]
Correo:          [________________]
WhatsApp:        [________________]
Edificio:        [________________]
¿En qué podemos ayudarte?
  ○ Quiero cotizar un sistema completo
  ○ Tengo preguntas técnicas
  ○ Soy constructora / desarrollador inmobiliario
  ○ Quiero ser distribuidor o instalador
  ○ Otro

[Mensaje (opcional): _____________]

[Enviar →]

─────────────────────────────────────────────
UBICACIÓN

IAccess — Bogotá, Colombia
Carrera 7 #82-66, Bogotá 110221
Zona Rosa · Chapinero
[Google Maps embed]
Operamos en toda la ciudad.
Proyectos fuera de Bogotá: consultar.

─────────────────────────────────────────────
PARA CONSTRUCTORAS

¿Estás construyendo un edificio residencial y quieres integrar 
IAccess desde el diseño? Tenemos un programa para desarrolladores 
que reduce significativamente el costo de instalación (la 
infraestructura de cableado se hace durante la construcción).

Ventajas del programa para constructoras:
• Precio preferencial (hasta 35% menos vs. retrofit)
• IAccess como "feature" de marketing del proyecto
• Documentación técnica para aprobar la integración con la lonja
• Capacitación del equipo del edificio antes de entrega

[Contactar programa constructoras →]
```

---

## SECTION 12 — DESIGN INSPIRATION REFERENCES

These were verified by visiting each site directly (May 2026). Use as design mood-board.

| # | URL | What to borrow | Best for section |
|---|-----|----------------|-----------------|
| 1 | https://www.faac.biz | Dark nav + white space, "Unlock New Worlds" aspirational hero, real-world context product shots, industry-vertical structure | Homepage hero, technology page |
| 2 | https://www.kastle.com | Navy/charcoal hero with embedded motion video, lifestyle imagery per property type, stat counters ("1K+ buildings") above fold | Hero video execution, social proof row |
| 3 | https://www.supremainc.com | Deep black/white, flagship biometric hardware in controlled-light photography, certification badges, clean isolated product shots | Technology/product page, hardware specs |
| 4 | https://www.avigilon.com/access-control | Charcoal + electric blue CTAs (mirror with mint), 4-pillar layout, FAQ accordion, "Trusted by 100,000+ orgs" headline | Technology page, feature pillars, CTA buttons |
| 5 | https://butterflymx.com | "Property access made simple" hero, dual CTA (Get Quote / Watch How It Works), hardware + app side by side, 20k properties stat | Homepage hero, hardware+app lifestyle photography |
| 6 | https://door.com | Deep black hero, "Your Key to Smart Buildings" with embedded video, residents using app on mobile, multi-product hardware grid | Smart building hero, resident-lifestyle photography |
| 7 | https://www.spaceflow.io | Dark theme + bright accent typography, smartphone mockups, IoT data viz, "84% adoption rate" stat | Dark-theme hero, mobile app showcase |
| 8 | https://proptechos.com | Dark hero + city photography, "AI for real estate. Built to act.", digital twin viz, partner logos row | Dark hero with city photography, AI storytelling |
| 9 | https://houm.com | Full Spanish UX with COP prices, "+20,000 Propietarios" trust, neighborhood names, 1,600+ Google reviews format | Spanish-language hero copy, Colombian trust signals |
| 10 | https://deas.com.co | Colombian security brand, Spanish CTAs, "Cotizar" button, biometric access, 370+ clients — **benchmark to beat visually** | Competitive reference / what NOT to look like |
| 11 | https://appmosfera.com | Direct "portería virtual" competitor, WhatsApp CTA, "conjunto residencial" language — **closest local competitor** | Spanish terminology, Airbnb integration framing |
| 12 | https://www.getkisi.com | Nearest color palette match (dark navy + white), ISO/SOC badges immediately visible, rare transparent pricing page | Transparent pricing layout, trust badge row |

**Key differentiators IAccess should own that no competitor has:**
- Before/after slider: traditional portero desk (left) → IAccess glass lobby (right)
- Interactive ROI calculator (input: # units × portero count → output: monthly savings in COP)
- Transparent pricing with line-item hardware breakdown (Kisi does this, no Colombian competitor does)
- WhatsApp CTA on every page (critical for Colombian B2B)

---

## SECTION 14 — DEVELOPER SPEC

### Recommended Stack
```
Framework:    Next.js 15 (App Router, SSR for SEO)
Styling:      Tailwind CSS + shadcn/ui
Animations:   Framer Motion (quote calculator transitions, 
               hero reveal, scroll-triggered sections)
Video:        Self-hosted .webm + .mp4 (no YouTube embed)
              Lazy load — only starts after LCP
CMS:          Sanity.io (blog + case studies + team)
Forms:        React Hook Form + Zod
Email:        Resend (transactional + quote PDF)
WhatsApp:     Meta Cloud API (WhatsApp Business)
SMS:          Twilio (fallback for QR delivery)
Calendar:     Cal.com (self-hosted or cloud) for demo booking
Analytics:    Plausible (privacy-first, GDPR/Ley 1581 compliant)
Maps:         Mapbox (service coverage map on contact page)
Hosting:      Vercel (Next.js native, edge functions)
Domain:       iaccess.co
CDN:          Cloudflare (free tier sufficient for launch)
```

### Performance Requirements
```
Lighthouse scores (target):
  Performance:    ≥ 92
  Accessibility:  ≥ 95
  SEO:            ≥ 98
  Best Practices: ≥ 95

Core Web Vitals:
  LCP (Largest Contentful Paint): < 1.8s
  INP (Interaction to Next Paint): < 150ms
  CLS (Cumulative Layout Shift):  < 0.05

Hero video:
  - Replaced by static image on mobile (< 768px)
  - Poster frame shown until video loaded
  - preload="none", loads after LCP
  - Max filesize: 8MB (webm), 15MB (mp4)
```

### Quote Calculator Logic (JavaScript)
```javascript
// All values in COP
// Pricing formula: Amazon USD × 1.25 (importation+shipping+customs) × 4,300 COP/USD = × 5,375
const PRICING = {
  // Hardware base per access point
  // All values: Amazon USD × 1.25 import × 4,300 COP/USD, rounded to nearest 10k (<1M) or 100k (≥1M)
  accessPoint: {
    readerPro:    2_400_000,  // DS-K1T671M  ($450 → $2,419k → rounded)
    readerValue:  1_400_000,  // DS-K1T342   ($254 → $1,365k → rounded)
    maglock1200:    940_000,  // 1200 lb     ($175 → $941k  → rounded to 10k)
    maglock600:     610_000,  // 600 lb      ($114 → $613k  → rounded to 10k)
    controller:     540_000,  // (~$100 → $538k → rounded to 10k)
    exitButton:     130_000,  // (~$25  → $134k → rounded to 10k)
  },
  camera: {
    colorVu4MP:   1_600_000,  // DS-2CD2347G2-LU  ($300 → $1,613k → rounded)
    colorVu8MP:   2_000_000,  // DS-2CD2387G2-LU  ($380 → $2,043k → rounded)
  },
  nvr: {
    ch16_4TB:     2_900_000,  // DS-7616NXI-K2 + 4TB HDD  ($533 → $2,865k → rounded)
    ch8_2TB:      2_200_000,  // 8ch NVR + 2TB (~$400 → $2,150k → rounded)
  },
  // Server: CLOUD ONLY — IAccess rents Hetzner GPU and bundles in monthly fee. No on-prem server sold.
  // (If server is in the building, client can bypass IAccess — this kills the SaaS model.)
  network: {
    ups1500va:      810_000,  // ($150 → $806k → rounded to 10k)
    switch24poe:  1_100_000,  // ($200 → $1,075k → rounded)
    switch8poe:     430_000,  // ($80  → $430k  — already clean)
  },
  elevator: {
    uhppote20ch:  1_800_000,  // UHPPOTE 20ch ($330 → $1,774k → rounded)
    uhppote40ch:  2_700_000,  // UHPPOTE 40ch ($500 → $2,688k → rounded)
  },
  vehicle: {
    faacGate:    10_800_000,  // FAAC barrier ($2,000 → $10,750k → rounded)
    lprCamera:    4_300_000,  // Hikvision LPR ($800 → $4,300k — clean)
  },
  install: {
    techHr:        90_000,   // $20/hr × 4,300 COP/USD (rounded to 10k)
    leadHr:       150_000,   // $35/hr × 4,300 COP/USD
    helperHr:      50_000,   // $12/hr × 4,300 COP/USD (rounded to 10k)
  },
  glassDoor: {
    // Glass door installation — major civil work; largest cost in most real quotes.
    // Real Bogotá quotes confirmed at $65M–$145M total for complete building.
    lobbyAutoSliding:  30_000_000, // Automatic double sliding glass door, lobby entrance
                                   // (motor + frame + tempered glass + install; ±$25–35M range)
    lobbySwingDouble:  22_000_000, // Double swing glass door (frameless, motorized)
    elevatorFloorPanel: 4_500_000, // Frameless glass panel + maglok per elevator lobby floor
                                   // (mid-range; $3.5M–$6M depending on glass spec)
  },
  monthly: {
    // Cloud GPU compute is ALWAYS included (IAccess rents Hetzner GEX44 RTX 4000 Ada ~$191/month
    // amortized across buildings). Monthly fee = compute + software + support SLA.
    // CLOUD ONLY — no on-premises server option. SaaS lock-in requires cloud-side AI.
    baseEsencial:     600_000,   // RTX 4060 Ti shared + software (revised down per user data)
    baseProfessional: 900_000,   // RTX 4060 Ti dedicated + software + SLA 4h
    baseEnterprise:   1_300_000, // RTX 4090 dedicated + software + SLA 99.9%
    // Add-ons:
    perApartment:      8_000,    // per-apartment fee (revised from 10k)
    airbnb:           250_000,   // Airbnb integration module (included free in Enterprise)
    monitoring24_7:   450_000,   // human patrol partnership 24/7 (always add-on, optional)
  }
};

function calculatePackage(inputs) {
  const {
    apartments,      // "1-20" | "21-50" | "51-100" | "100+"
    floors,          // "1-5" | "6-15" | "16-30" | "30+"
    accessPoints,    // 1 | 2 | 3 | 4
    elevators,       // 0 | 1 | 2 | 3
    parkingGate,     // boolean
    interiorDoors,   // 0 | 1-3 | 4-8 | 8+
    emergencyExits,  // 0 | 1 | 2 | 3+
    airbnb,          // boolean
    nAirbnbUnits,    // number
    monitoring,      // boolean
    // serverType removed — all plans now use IAccess cloud GPU by default.
    // Edge (offline) option available as separate line item on quote.
    currentPorteros  // 0 | 1 | 2 | 3+
  } = inputs;

  // Apartment count midpoint
  const aptCounts = { "1-20": 10, "21-50": 35, "51-100": 75, "100+": 120 };
  const nApts = aptCounts[apartments];

  // Floor count midpoint
  const floorCounts = { "1-5": 3, "6-15": 10, "16-30": 22, "30+": 35 };
  const nFloors = floorCounts[floors];

  // ─── HARDWARE CALCULATION ───
  let hardware = 0;
  const isEnterprise = nApts > 70;

  // Readers: Pro at main entrance, Value at secondary
  hardware += PRICING.accessPoint.readerPro * Math.min(accessPoints, 2);
  hardware += PRICING.accessPoint.readerValue * Math.max(0, accessPoints - 2);
  hardware += PRICING.accessPoint.maglock1200 * accessPoints;
  hardware += PRICING.accessPoint.controller * accessPoints;
  hardware += PRICING.accessPoint.exitButton * accessPoints * 2;

  // Cameras: 2 at entrance + 1 per floor of coverage
  const nCameras = 2 + accessPoints + Math.min(nFloors, 8) + (elevators * 2);
  hardware += PRICING.camera.colorVu4MP * nCameras;

  // NVR: 16ch for medium+, 8ch for small
  hardware += nApts > 25 ? PRICING.nvr.ch16_4TB : PRICING.nvr.ch8_2TB;

  // Elevator controllers
  const elevatorCost = nFloors <= 20
    ? 2_000_000  // UHPPOTE 20ch
    : 3_000_000; // UHPPOTE 40ch
  hardware += elevatorCost * elevators;
  hardware += PRICING.accessPoint.readerValue * elevators; // reader in cab

  // Interior doors (RFID only)
  const interiorDoorCounts = { "0": 0, "1-3": 2, "4-8": 5, "8+": 10 };
  const nInterior = interiorDoorCounts[interiorDoors] || 0;
  hardware += (PRICING.accessPoint.maglock600 + 580_000) * nInterior; // 580k = RFID reader

  // Parking gate
  if (parkingGate) hardware += 12_040_000 + 4_816_000; // FAAC + LPR

  // Server: CLOUD ONLY — never a hardware line item. Monthly fee covers GPU compute.

  // Network + power
  hardware += PRICING.network.ups1500va * (isEnterprise ? 2 : 1);
  hardware += PRICING.network.switch24poe * (nCameras > 16 ? 2 : 1);

  // ─── INSTALLATION LABOR ───
  const installDays = nApts <= 20 ? 2 : nApts <= 60 ? 4 : 7;
  const techHours = installDays * 8 * 3; // 3 skilled installers
  const leadHours = installDays * 4;     // lead integrator half-days
  const labor = (techHours * PRICING.install.techHr)
              + (leadHours * PRICING.install.leadHr);

  // ─── CABLING ───
  const cablingCost = nFloors * 200_000 + accessPoints * 300_000;

  // ─── EMERGENCY EXITS ───
  const exitCounts = { "0": 0, "1": 1, "2": 2, "3+": 3 };
  const emergencyExitCost = (exitCounts[emergencyExits] || 0) * 1_220_000;

  // ─── SOFTWARE + COMMISSIONING ───
  const softwareSetup = isEnterprise ? 6_000_000 : nApts > 25 ? 3_000_000 : 1_500_000;

  const totalInstall = hardware + labor + cablingCost + emergencyExitCost + softwareSetup;

  // ─── MONTHLY ───
  // Base minimum + per-apt fee. Cloud GPU ALWAYS included — no on-prem server option.
  const baseMonthly = isEnterprise
    ? PRICING.monthly.baseEnterprise     // COP 1,300,000 min
    : nApts > 25
      ? PRICING.monthly.baseProfessional // COP 900,000 min
      : PRICING.monthly.baseEsencial;    // COP 600,000 min
  let monthly = Math.max(baseMonthly, nApts * PRICING.monthly.perApartment + baseMonthly);
  if (airbnb && !isEnterprise) monthly += PRICING.monthly.airbnb; // included free in Enterprise
  if (monitoring) monthly += PRICING.monthly.monitoring24_7;

  // ─── DOORMEN COST ───
  // Real rate: $4,300,000/turno-slot (confirmed by real 2026 Bogotá building budget:
  // 10-apt building pays $12,894,398/month for 3 turnos = $4,298k/turno).
  // Includes: empresa de vigilancia base + recargo nocturno 35% + festivo 75% + reemplazos.
  // Most buildings need 3 turnos (24/7). Diurno-only = 1 turno.
  const porteroTurnos = { "0": 0, "1": 1, "2": 2, "3+": 3 };
  const nTurnos = currentPorteros !== undefined
    ? porteroTurnos[currentPorteros] || 0
    : (nApts < 25 ? 1 : 3); // <25 apts diurno only; any 24/7 building = 3 turnos minimum
  const porteroMonthlyCost = nTurnos * 4_300_000;

  // ─── RANGE (±20%) ───
  return {
    installMin: Math.round(totalInstall * 0.85 / 100_000) * 100_000,
    installMax: Math.round(totalInstall * 1.20 / 100_000) * 100_000,
    monthlyMin: Math.round(monthly * 0.90 / 50_000) * 50_000,
    monthlyMax: Math.round(monthly * 1.10 / 50_000) * 50_000,
    monthlyMid: monthly,
    porteroMonthlyCost: Math.round(porteroMonthlyCost),
    nPorteros,
    monthlySaving: Math.round(porteroMonthlyCost - monthly),
    roiMonths: monthly > 0 ? Math.round(totalInstall / (porteroMonthlyCost - monthly)) : null,
  };
}
```

---

## SECTION 15 — SEO ESSENTIALS

### Meta tags (homepage)
```html
<title>IAccess — Portería Virtual Inteligente para Edificios en Bogotá</title>
<meta name="description" content="Reemplaza tu portero con reconocimiento facial, QR y huella dactilar. Instalación en 2–5 días. Desde $17 millones. El único sistema en Colombia con Modo Airbnb integrado.">
<meta property="og:title" content="IAccess — Tu edificio inteligente. Sin portero.">
<meta property="og:description" content="Portería virtual con IA para edificios residenciales en Bogotá. Hikvision + YOLO AI + Modo Airbnb. Cotización en 60 segundos.">
<meta property="og:image" content="https://iaccess.co/og-image.jpg">
```

### Schema.org LocalBusiness
```json
{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "name": "IAccess",
  "description": "Portería virtual inteligente para edificios residenciales en Bogotá, Colombia",
  "url": "https://iaccess.co",
  "telephone": "+573203438733",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "Carrera 7 #82-66",
    "addressLocality": "Bogotá",
    "postalCode": "110221",
    "addressCountry": "CO"
  },
  "priceRange": "$$",
  "areaServed": "Bogotá, Colombia",
  "serviceType": "Access Control, Building Automation, Portería Virtual"
}
```

### Target keywords
```
Primary (own these before Appmosfera does):
  portería virtual Bogotá
  portería virtual Colombia
  portería virtual Colombia precio 2026
  sistema acceso edificio residencial Colombia
  control de acceso edificio Bogotá

Secondary:
  reemplazar portero edificio Colombia
  costo portero Colombia 2026
  reconocimiento facial edificio Colombia
  edificio Airbnb acceso automático
  control ascensor por piso conjunto residencial
  guardia virtual edificio Bogotá

Long-tail (high intent, low competition):
  cuánto cuesta portería virtual en Colombia
  cómo automatizar el ingreso a mi edificio en Bogotá
  magneto electromagnético puerta vidrio edificio
  control de acceso sin portero Bogotá
  fallo SIC reconocimiento facial edificios Colombia
  portería virtual vs portero cual es mejor Colombia
  
Competitor displacement (people already searching competitors):
  Portea alternativa Colombia
  HIPCAM precio Colombia
  appmosfera guardia alternativa
  portería remota DEAS precio
  GuardIA vs IAccess
  appmosfera guardia precio Colombia
  guardia appmosfera reemplazar portero
  guardia IA portería colombiana alternativa
  
GuardIA-specific long-tail (high commercial intent):
  appmosfera guardia precio
  guardia IA control acceso edificio
  guardia appmosfera sin operador humano
  reemplazar portero con IA Colombia sin operador
```

---

## SECTION 16 — WHAT NOT TO DO

| ❌ Don't | ✅ Do |
|---|---|
| Show cameras in hero or above-fold | Show a person's face being scanned — the EXPERIENCE |
| "Sistema de CCTV" or "videovigilancia" in headlines | "Reconocimiento facial" and "acceso inteligente" |
| Generic stock photos of security guards | Lifestyle photos: modern lobby, person on phone, clean glass door |
| Hide all pricing | Show ranges, model numbers, COP prices — build trust |
| "Contáctenos para más información" as only CTA | Quote calculator + WhatsApp direct link on every page |
| Ignore Airbnb | Dedicate full section + landing page — it's the biggest gap in the market |
| Only target building owners | Target: admins, Airbnb investors, constructoras separately |
| "Smart building" buzzwords | "Tu portería 24/7 cuesta $12.9M al mes. IAccess cuesta $1.25M." |
| English-only tech terms | Full Spanish, propiedad horizontal vocabulary |
| Photo of surveillance camera equipment | Photo of person entering through elegant glass door |
| Testimonials without specifics | Always include: building name, neighborhood, # units, date |
| Contact form with 8+ fields | Name + WhatsApp + building = enough to start |
| Justify price without comparison | Always anchor against cost of porteros — make savings tangible |

---

*IAccess Website Design Brief v3.4 — May 2026*  
*Pricing basis: Amazon USD retail price + 25% importation/shipping/customs × 4,300 COP/USD = × 5,375 multiplier*  
*Labor rates: $10–40 USD/hr per client spec — skilled installer $20/hr, lead integrator $35/hr*  
*Portero cost basis: real $4,300,000/turno-slot (10-apt Bogotá 2026 = $12,894,398/month for 3 shifts). Base formula: $2,462,050 carga laboral + 20% empresa de vigilancia = $2,954,460 base; rest = night/festivo surcharges.*  
*Business model (v3.1 — cloud-only): IAccess owns and operates cloud GPU infrastructure (Hetzner GEX44 RTX 4000 Ada, ~$191/month IAccess cost). No server sold or installed in buildings — EVER. Monthly fee covers GPU compute + software + maintenance + support SLA. No on-premises option: if the server is in the building, the client can bypass the IAccess platform, which kills the SaaS lock-in.*
