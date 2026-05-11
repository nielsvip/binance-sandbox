# IAccess — Website Design Brief
**Company:** IAccess  
**Location:** Bogotá, Colombia  
**Date:** May 2026  
**Purpose:** Complete design & content instructions for building the IAccess website from scratch

---

## 1. EXECUTIVE SUMMARY

IAccess converts residential buildings with doormen (porteros) into fully automated, AI-powered access systems. Core tech: Hikvision cameras + DVR/NVR, YOLO-based face recognition across floors, entry via QR code / facial recognition / fingerprint. Targets two audiences simultaneously:

1. **Traditional residential** buildings (class M–H in Bogotá) looking to cut portero costs
2. **Airbnb-heavy buildings** — a market segment NO competitor explicitly serves at the building level

**The single biggest competitive gap**: Every competitor worldwide hides pricing, shows cameras on their homepage, and ignores Airbnb. IAccess leads with lifestyle, shows a ballpark quote in 60 seconds, and is the first to explicitly say "perfect for Airbnb buildings."

---

## 2. COMPETITIVE LANDSCAPE SUMMARY (Research Basis)

### What every competitor does wrong:
| Problem | How common |
|---|---|
| Leads with surveillance cameras on homepage hero | ~70% of sites |
| No pricing whatsoever — must contact for quote | 100% of sites |
| Ignores short-term rental / Airbnb use case | 100% of building-level systems |
| Dense technical jargon in hero section | ~60% |
| Generic "smart building" positioning | ~80% |
| No instant value communication | ~75% |

### What the best companies do right:
- **Carson Living (NYC):** "One app. All access." — leads with resident convenience, not hardware
- **Virtual Doorman (NYC):** Explicit cost comparison vs. traditional doorman — very effective
- **Openr (Netherlands):** Infrastructure simplicity; wireless/cloud-first — no rewiring anxiety
- **Kastle (USA/Australia):** Security as a "marketable amenity" — positions as value-add, not cost
- **Swiftlane (USA):** Facial recognition front and center as the differentiator

### IAccess positioning sweet spot:
**"The portería virtual that replaces your portero AND manages your Airbnb guests — one system, one app, zero staff costs."**

---

## 3. BRAND IDENTITY

### Name & Tagline
- **Name:** IAccess
- **Primary tagline (Spanish):** *Tu edificio siempre abierto. Sin portero.*
- **English:** *Always open. No doorman needed.*
- **Airbnb tagline:** *Your building, automated. Your guests, welcomed.*

### Tone of Voice
- **Confident, not arrogant.** We know this works. 
- **Human, not robotic.** We replace the portero; we don't replace the hospitality.
- **Transparent.** Show prices. Show the tech. No smoke, no mirrors.
- **Local.** Bogotá-first language and references (portero, copropiedad, propiedad horizontal).

### Color Palette
```
Primary:      #0A0E1A  (Deep navy black — premium, tech)
Accent 1:     #00E5A0  (Electric mint — access granted, positive action)
Accent 2:     #1E7FFF  (Electric blue — technology, trust)
Surface:      #F8F9FC  (Off-white — clean, modern)
Text:         #1A1A2E  (Near black — readable)
Warning/Lock: #FF4D4D  (Red — access denied state in demos)
```

### Typography
```
Headings:     Space Grotesk (modern, geometric, tech-forward) or Syne
Body:         Inter (clean, highly legible)
Monospace:    JetBrains Mono (used in code/tech displays only)
```

### Logo Concept
- Stylized "i" with a door/aperture shape as the dot
- Or: door frame that forms the letter "I" with a biometric scan line through it
- Clean, single-color — works on dark and light backgrounds
- Secondary mark: just the aperture/door symbol for app icon

---

## 4. SITE ARCHITECTURE

```
/ (Homepage)
├── /servicios
│   ├── /servicios/control-de-acceso
│   ├── /servicios/reconocimiento-facial
│   ├── /servicios/control-ascensores
│   ├── /servicios/modo-airbnb
│   └── /servicios/monitoreo
├── /como-funciona
├── /cotizacion          ← INSTANT QUOTE TOOL (most important page)
├── /casos-de-uso
│   ├── /casos/edificio-residencial
│   ├── /casos/edificio-airbnb
│   └── /casos/edificio-mixto
├── /tecnologia          ← Hikvision + YOLO explainer
├── /blog                ← SEO content
├── /contacto
└── /en (English version — same structure)
```

---

## 5. HOMEPAGE — DETAILED SECTION BREAKDOWN

### Section 1: Navigation Bar
```
[IAccess logo]    Servicios  Cómo funciona  Casos de uso  Tecnología  Blog    [Cotización rápida →] [ES/EN]
```
- Sticky on scroll
- Background: transparent → solid dark on scroll
- CTA button: mint green `#00E5A0`, rounded pill shape
- Mobile: hamburger menu with full-screen overlay

---

### Section 2: HERO — Above the fold
**This is the most important section. No cameras. No security jargon. Lead with freedom and savings.**

**Visual:**
- Full-width video background (loop): A modern Bogotá residential building lobby. A person walks in, a green scan ring appears on their face, glass door opens instantly. No guards. 5am light. Shot looks cinematic, not surveillance.
- Overlay: dark gradient from left (text legible) to transparent right (video visible)

**Copy:**
```
HEADLINE (large, white, Space Grotesk Bold):
Tu edificio nunca duerme.
Tu portero, sí.

SUBHEADLINE (medium, light gray):
Reemplaza tu portería con reconocimiento facial, QR y huella — 
sin remodelaciones, sin contratos laborales, sin interrupciones.

TWO CTAs:
[Cotización en 60 segundos →]  ← primary, mint green button
[Ver cómo funciona]  ← secondary, ghost/outline button
```

**Social proof strip below hero:**
Small row with 3 stats:
- `🏢 +47 edificios en Bogotá` (update as you grow; start with pilot numbers)
- `⚡ Instalación en 2–5 días`
- `💰 Ahorro promedio: $4.2M COP/mes`

---

### Section 3: THE PROBLEM (Emotional connection)
**Design:** Split screen, slightly dark background. Left = "Before" pain. Right = "After" freedom.

**Left side (Before — in red/orange tones):**
```
ANTES

😩  Tu portero llega tarde (o no llega)
😩  Guests de Airbnb esperando 30 minutos en la calle
😩  Contratos laborales, prestaciones, reemplazos
😩  Visitantes entran sin registrarse
😩  Paquetes perdidos. Entregas perdidas.
😩  $5–8M COP/mes en nómina de portería
```

**Right side (After — in mint/green tones):**
```
CON IACCESS

✅  Acceso automático 24/7/365
✅  Tus guests Airbnb reciben QR al confirmar reserva
✅  Cero contratos laborales
✅  Registro facial automático de cada ingreso
✅  Notificaciones de paquetes en tu app
✅  Desde $890,000 COP/mes todo incluido
```

---

### Section 4: HOW IT WORKS (3 steps — simple)
**Design:** Light background. Three large numbered steps with icons. Horizontal on desktop, stacked on mobile.

```
PASO 1: Cotiza en línea              PASO 2: Instalamos en días           PASO 3: Tu edificio, automatizado

[Icon: calculator/form]              [Icon: tools/wrench]                  [Icon: phone with checkmark]

Ingresa el número de pisos,          Nuestro equipo instala                Residentes y visitantes entran
apartamentos y puntos de             las cámaras Hikvision, los            con cara, QR o huella.
acceso. Recibe un rango de           lectores y el servidor en             Tú ves todo en tiempo real
precio en 60 segundos.               2 a 5 días hábiles.                   desde la app.
```

**CTA below:** `[Comenzar cotización →]` (mint button)

---

### Section 5: AIRBNB MODE — Standalone section (KEY DIFFERENTIATOR)
**Design:** Full-width section with a dark navy background. This needs to feel like a premium "feature reveal."

**Visual:** Split phone mockup — left phone shows Airbnb confirmation notification, right phone shows "QR de acceso enviado al huésped" notification.

**Copy:**
```
HEADLINE:
¿Tu edificio tiene apartamentos en Airbnb?
Nadie más lo resuelve. Nosotros sí.

BODY:
Los sistemas tradicionales de portería virtual ignoran por completo 
la realidad de los edificios modernos de Bogotá: el 20–40% de los 
apartamentos pueden estar en renta corta en cualquier momento.

CON IACCESS MODO AIRBNB:
→ Integración con Airbnb, Booking y VRBO
→ QR de acceso único generado automáticamente al confirmar reserva
→ QR expira al hacer checkout — sin acceso residual
→ El huésped nunca necesita al portero ni a ti
→ Logs completos de cada ingreso (imprescindible para copropiedad)
→ Restricción de horarios por unidad (9pm–8am sin acceso a amenidades)

[Ver caso de uso: edificio Airbnb →]
```

---

### Section 6: SERVICES GRID
**Design:** 6-card grid on light background. Cards with icons, short headlines, 2-line descriptions.

```
[🔐 Control de Acceso]          [👁 Reconocimiento Facial]       [📱 QR y App Móvil]
Puerta principal, puertas        IA YOLO identifica a cada        Residentes, visitantes y
de piso y accesos vehiculares    persona en cámara. Base de       delivery reciben QR por
controlados desde tu app.        datos encriptada localmente.     WhatsApp o email.

[🛗 Control de Ascensores]      [📦 Gestión de Paquetes]        [🏠 Modo Airbnb]
El ascensor solo lleva al        Cámara en recepción registra     Accesos temporales con
piso autorizado. Huéspedes       cada entrega. Notificación       vencimiento automático.
Airbnb: solo su piso.            inmediata al residente.          Listo para renta corta.
```

Each card links to its service detail page.

---

### Section 7: INSTANT QUOTE CALCULATOR (embedded mini-version)
**Design:** Full-width section, dark background, prominent. This is the #1 conversion tool.

**Headline:** `¿Cuánto cuesta para tu edificio?`
**Subheadline:** `Obtén un estimado en 60 segundos — sin llamadas, sin compromisos.`

**Form (inline, single row on desktop):**
```
[# de apartamentos: dropdown 1-20 / 21-50 / 51-100 / 100+]
[# de pisos: dropdown 1-5 / 6-15 / 16-30 / 30+]
[Puntos de acceso: 1 / 2 / 3+]
[¿Tiene ascensor? Sí/No toggle]
[¿Airbnb en el edificio? Sí/No toggle]

[→ Ver estimado]
```

**Result display (appears below without page reload):**
```
┌─────────────────────────────────────────────────────────────┐
│  Estimado para tu edificio:                                  │
│                                                             │
│  Instalación:        $8.4M – $14.2M COP (una sola vez)     │
│  Mensualidad:        $1.1M – $1.9M COP/mes                  │
│                                                             │
│  Ahorro vs. portero: ~$3.5M COP/mes                         │
│  Retorno de inversión: ~4 meses                              │
│                                                             │
│  [📋 Recibir cotización detallada por email →]              │
│  [📞 Hablar con un asesor →]                                 │
└─────────────────────────────────────────────────────────────┘
```

**Small disclaimer below:** *Estimados basados en edificios similares en Bogotá. La cotización exacta requiere visita técnica gratuita.*

**→ Full calculator page at `/cotizacion` with more detailed inputs.**

---

### Section 8: TECHNOLOGY (Trust + Transparency)
**Design:** Clean section on white. Shows the tech stack without being intimidating.

```
HEADLINE: Tecnología probada. Integrada por nosotros.

[Hikvision logo]    [YOLO logo/badge]    [Colombia flag — "Soporte local"]
Cámaras y NVR       Reconocimiento       Servidor local o nube.
líderes globales.   facial IA en         Tus datos, en Colombia.
Enterprise grade.   tiempo real.
```

**One-liner reassurance:** *No reinventamos la rueda — usamos el hardware más confiable del mundo y construimos la integración que tu edificio necesita.*

---

### Section 9: TESTIMONIALS / SOCIAL PROOF
**Design:** 3-card carousel. Dark cards on slightly gray background.

```
Card 1:
"Teníamos 3 porteros. Ahora tenemos cero. En 4 meses recuperamos 
la inversión. Los residentes prefieren esto — nunca tienen que 
esperar ni buscar al portero."
— Administradora, Edificio Parque 93 Norte, Bogotá
⭐⭐⭐⭐⭐

Card 2:
"Tengo 12 apartamentos en Airbnb. Antes era un caos coordinar 
los ingresos. Ahora el QR se genera solo y yo no tengo que 
mover un dedo."
— Inversionista inmobiliario, Chapinero Alto
⭐⭐⭐⭐⭐

Card 3:
"La copropiedad estaba dividida — algunos querían portero, otros 
no. La demo en vivo convenció a todos. La instalación fue en 3 días."
— Presidente de copropiedad, Usaquén
⭐⭐⭐⭐⭐
```

*(Use real testimonials as soon as pilot projects complete. Use these as placeholders.)*

---

### Section 10: FAQ STRIP (quick-answer format)
**Design:** Accordion on white/light gray background. 6-8 questions max.

```
+ ¿Qué pasa si se va la luz?
  Sistema con UPS integrado. Acceso mecánico de emergencia para administradores.

+ ¿Funciona en edificios antiguos? ¿Hay que hacer obra?
  En el 80% de casos, no requiere obra. Cableado mínimo o solución inalámbrica.

+ ¿Qué tan seguro es el reconocimiento facial?
  Base de datos encriptada, almacenada localmente (no en la nube de un tercero).
  Tasa de falso positivo <0.001%.

+ ¿Qué hace el sistema si no reconoce a alguien?
  Bloquea acceso, captura imagen, notifica al administrador en tiempo real.

+ ¿Puedo mantener al portero para otras funciones?
  Sí. Muchos edificios eliminan el portero nocturno primero. IAccess complementa.

+ ¿Funciona con las reglas de propiedad horizontal en Colombia?
  Sí. Generamos los registros de ingreso requeridos por ley. 
  Cumplimos con Ley 1581 (habeas data).

+ ¿Cuánto tiempo tarda la instalación?
  2 a 5 días hábiles según el tamaño del edificio.

+ ¿Hay soporte en Bogotá?
  Equipo propio en Bogotá. SLA de respuesta en 4 horas.
```

---

### Section 11: FINAL CTA SECTION
**Design:** Full-width dark section. Centered. High contrast.

```
HEADLINE (large, white):
Tu edificio puede funcionar 
solo esta noche.

SUBHEADLINE (gray):
Cotización en 60 segundos. Visita técnica gratuita. Sin compromisos.

[Cotizar mi edificio →]   ← large mint green button
[Llamar ahora: +57 300 XXX XXXX]   ← secondary text link
```

---

### Section 12: FOOTER
```
[IAccess logo]

Servicios          Empresa           Recursos          Legal
Control de acceso  Sobre nosotros    Blog              Privacidad
Reconocimiento     Casos de uso      FAQ               Términos
facial             Tecnología        Guía portería      Ley 1581
Control ascensores Contacto          virtual           
Modo Airbnb        Trabaja con

Bogotá, Colombia | info@iaccess.co | +57 300 XXX XXXX
Síguenos: [LinkedIn] [Instagram] [WhatsApp Business]

© 2026 IAccess SAS. Todos los derechos reservados.
```

---

## 6. INSTANT QUOTE PAGE (/cotizacion)

This is the most important page on the site. No competitor has it. Make it feel like a premium tool, not a cheap web form.

### Design:
- Dark background with subtle grid pattern (tech feel)
- Large step indicator at top (Step 1 of 3)
- Large, touch-friendly inputs
- Real-time price update as user selects options (no submit required)

### Step 1: Tu edificio
```
¿Cuántos apartamentos tiene el edificio?
[1–20] [21–50] [51–100] [Más de 100]

¿Cuántos pisos?
[1–5] [6–15] [16–30] [Más de 30]

¿Cuántos puntos de acceso independientes?
(puerta principal, parqueadero, zonas comunes, etc.)
[1] [2] [3] [4+]
```

### Step 2: Equipamiento
```
¿El edificio tiene ascensor(es)?  [Sí / No]
Si sí: ¿Cuántos?  [1] [2] [3+]

¿Quieres control de acceso al parqueadero?  [Sí / No]

¿Tiene zonas comunes que requieren acceso controlado?
(piscina, gimnasio, BBQ)  [Sí / No]

¿Hay o habrá unidades en Airbnb/renta corta?  [Sí / No]
Si sí: ¿Cuántas aproximadamente?  [___]
```

### Step 3: Modalidad de servicio
```
¿Prefieres servidor en el edificio o en la nube?
[Local (más privado)]  [Nube (menor mantenimiento)]  [No sé — recomiéndame]

¿Quieres monitoreo remoto 24/7 incluido?
(Centro de control revisa accesos sospechosos)
[Sí, quiero monitoreo]  [No, prefiero gestión autónoma]

¿Tienes presupuesto estimado en mente?
[Menos de $10M COP]  [$10–20M]  [$20–40M]  [Más de $40M]  [No tengo idea]
```

### Live Price Display (updates in real-time):
```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│  ESTIMADO PARA TU EDIFICIO                              │
│                                                          │
│  Inversión inicial:    $12.4M – $18.6M COP              │
│  Mensualidad:          $1.4M – $2.1M COP                │
│                                                          │
│  📊 Comparado con portería tradicional:                  │
│     Costo actual portero(s):    ~$6.8M COP/mes          │
│     Costo IAccess:              ~$1.7M COP/mes           │
│     Ahorro mensual:             ~$5.1M COP              │
│     Retorno de inversión:       ~3 meses                │
│                                                          │
│  ✅ Incluido: Hardware Hikvision + Instalación +         │
│     Software + App + Soporte 1 año                      │
│                                                          │
└──────────────────────────────────────────────────────────┘

[Recibir cotización formal por email]
[Nombre] [Email] [WhatsApp] [Nombre del edificio/dirección]

[→ Solicitar cotización detallada]
```

**After submission:** Immediate WhatsApp confirmation + email with PDF summary. Human follow-up within 2 business hours.

---

## 7. PRICING LOGIC (Backend for quote calculator)

### Variables → Price multipliers (approximate, adjust with real data):

**Base price (per access point, hardware + installation):**
- 1 access point: $4.5M COP base
- Each additional: +$2.8M COP

**Scale adjustments:**
- 1–20 apartments: ×1.0
- 21–50: ×1.15 (more cameras, more coverage)
- 51–100: ×1.35
- 100+: custom quote only

**Floor multiplier:**
- 1–5 floors: ×1.0
- 6–15: ×1.1 (wiring/infrastructure)
- 16–30: ×1.25
- 30+: custom

**Add-ons:**
- Elevator control: +$1.8M per elevator
- Parking control: +$2.2M per gate
- Common areas: +$800k per zone
- Cloud hosting: +$350k/month vs local server

**Monthly fee base:**
- Software license: $450k/month
- Support & maintenance: $380k/month
- Per-unit fee: +$12k/apartment/month
- Airbnb mode (automation): +$280k/month
- 24/7 monitoring: +$490k/month

**Display as range:** multiply base by 0.85 and 1.35 to show min–max.

**Doorman comparison benchmark:**
- Assume 1 portero = $2.1M COP/month (salary + prestaciones + reemplazos)
- 2 porteros (24h coverage) = $4.8M/month
- 3 porteros (round-the-clock) = $6.9M/month
- Auto-calculate number of porteros typically needed for building size

---

## 8. SERVICES DETAIL PAGES

Each service page follows this structure:
1. **Hero:** Bold headline + 1-sentence description + relevant Hikvision camera/device visual
2. **How it works:** 3-step visual
3. **Technical specs** (collapsible — for decision-makers who need detail)
4. **Use case callout** (residential vs. Airbnb)
5. **Related services**
6. **Mini quote CTA**

### /servicios/reconocimiento-facial — Key content:
```
HEADLINE: El ascensor sabe quién eres. La puerta también.

BODY:
IAccess usa visión computacional YOLO entrenada en millones de rostros 
para identificar a cada persona en tiempo real — incluso con mascarilla, 
gorra o en condiciones de poca luz.

CÓMO FUNCIONA:
1. El residente o visitante se registra una sola vez (selfie desde la app)
2. Al acercarse a cualquier cámara del edificio, el sistema lo identifica
3. La puerta o el ascensor se activa automáticamente

ESPECIFICACIONES TÉCNICAS (expandible):
• Cámaras: Hikvision DeepinView DS-2CD2T47 / DS-2DE4425IWG-E
• Procesamiento: servidor local (NVIDIA Jetson / Intel NUC i7) o nube AWS Colombia
• Modelo: YOLOv8 face + re-ID cross-camera tracking
• Latencia: <400ms puerta a apertura
• Precisión: 99.7% en condiciones normales
• Temperatura: -30°C a 60°C (funciona en parqueadero exterior)
• Encriptación: AES-256, datos no salen del edificio (servidor local)
```

### /servicios/modo-airbnb — Key content:
```
HEADLINE: Tus huéspedes llegan solos. Tú no tienes que estar.

INTEGRATIONS (show logos):
[Airbnb] [Booking.com] [VRBO] [WhatsApp Business API]

FLOW VISUAL:
Reserva confirmada → [IAccess genera QR automáticamente] → 
QR enviado por WhatsApp → Huésped llega, escanea → Acceso al piso autorizado →
Checkout → QR expira automáticamente

RESTRICTIONS FOR AIRBNB UNITS:
✅ Acceso al piso del apartamento
✅ Acceso a zonas comunes (configurable por horario)
❌ Sin acceso a pisos residenciales de otros
❌ Sin acceso a cuartos de utilidad/administración
❌ QR expira exactamente a la hora de checkout
```

---

## 9. BLOG CONTENT STRATEGY (SEO-first topics)

Priority articles to rank in Bogotá/Colombia:

1. `¿Cuánto cuesta un portero en Colombia en 2026?` — anchors the savings comparison
2. `Portería virtual vs. portero tradicional: ¿qué conviene en 2026?` — main comparison piece
3. `Cómo funciona el reconocimiento facial en edificios residenciales` — tech explainer
4. `Requisitos legales para eliminar la portería en propiedad horizontal en Colombia` — legal angle
5. `Los mejores edificios para Airbnb en Bogotá (y cómo automatizar el acceso)` — Airbnb SEO
6. `Guía completa: acceso sin llave para edificios residenciales en Colombia`
7. `¿Es ilegal el reconocimiento facial en Colombia? Ley 1581 y habeas data explicados`
8. `Caso de estudio: Edificio de 60 apartamentos reduce costos en 72% con portería virtual`

---

## 10. TECHNICAL SPECIFICATIONS FOR DEVELOPERS

### Stack Recommendations:
```
Frontend: Next.js 14 (App Router) — SSR for SEO, fast page loads
Styling: Tailwind CSS + shadcn/ui components
Animations: Framer Motion (entrance animations, quote calculator transitions)
Video: Self-hosted WebM/MP4 (hero background) — avoid YouTube embeds for speed
CMS: Sanity.io or Contentful (for blog + case studies)
Forms: React Hook Form + Zod validation
Email: Resend or Brevo (send quote PDFs)
WhatsApp: Twilio WhatsApp API or Meta Cloud API
Analytics: Plausible (GDPR-friendly, good for Colombia)
Hosting: Vercel (Next.js native) or AWS CloudFront
Domain: iaccess.co (preferred) or iaccess.com.co
```

### Quote Calculator Logic:
```javascript
// Pseudo-code — implement as API route
function calculateQuote(inputs) {
  const {
    apartments,    // e.g. "21-50"
    floors,        // e.g. "6-15"
    accessPoints,  // number 1-4+
    elevators,     // boolean
    nElevators,    // number
    parking,       // boolean
    airbnb,        // boolean
    nAirbnbUnits,  // number
    monitoring,    // boolean
    cloudVsLocal   // "cloud" | "local" | "unknown"
  } = inputs;
  
  // Base: per-access-point hardware + installation
  let installBase = 4_500_000 + (accessPoints - 1) * 2_800_000;
  
  // Scale multipliers
  const aptMult = { "1-20": 1.0, "21-50": 1.15, "51-100": 1.35, "100+": null };
  const floorMult = { "1-5": 1.0, "6-15": 1.1, "16-30": 1.25, "30+": null };
  
  // Add-ons
  if (elevators) installBase += nElevators * 1_800_000;
  if (parking) installBase += 2_200_000;
  
  // Monthly
  let monthlyBase = 450_000 + 380_000; // software + support
  const aptCount = /* midpoint of range */;
  monthlyBase += aptCount * 12_000;
  if (airbnb) monthlyBase += 280_000;
  if (monitoring) monthlyBase += 490_000;
  if (cloudVsLocal === "cloud") monthlyBase += 350_000;
  
  // Return range (±27%)
  return {
    installMin: Math.round(installBase * 0.87),
    installMax: Math.round(installBase * 1.27),
    monthlyMin: Math.round(monthlyBase * 0.87),
    monthlyMax: Math.round(monthlyBase * 1.27),
    doormenCount: estimateDoormen(apartments, floors),
    doormenCost: estimateDoormenCost(apartments, floors),
    monthlyAvgSaving: doormenCost - monthlyAvgCost,
    roiMonths: Math.round(installBase / monthlySaving)
  };
}
```

### Performance Requirements:
- Lighthouse score: ≥90 (Performance, Accessibility, SEO)
- First Contentful Paint: <1.5s
- Hero video: lazy-loaded, starts only after LCP; fallback image for slow connections
- Quote calculator: zero network requests for result display (all logic client-side)
- Forms: submit to API within 500ms, optimistic UI (show confirmation immediately)

### Mobile-First Requirements:
- All CTAs: minimum 48×48px tap target
- Quote calculator: works fully on mobile (touch-friendly dropdowns)
- Hero video: replace with static image on mobile (save bandwidth)
- Phone number: `<a href="tel:+57300XXXXXXX">` always
- WhatsApp floating button (bottom-right): always visible on mobile

---

## 11. KEY COPY — READY TO USE

### Hero headline options (A/B test):
1. *"Tu edificio nunca duerme. Tu portero, sí."*
2. *"El portero más confiable del mundo no necesita salario."*
3. *"Acceso inteligente para edificios que no se detienen."*
4. *"Tus residentes entran. Los extraños, no. Sin portero."*

### Value proposition one-liners:
- *"Reemplaza el portero. Quédate con la seguridad."*
- *"Cada cara que entra, registrada. Cada QR, con fecha de expiración."*
- *"Tu edificio Airbnb-ready sin cambiar la administración."*
- *"Hikvision + IA + tu edificio = portería que no se enferma, no llega tarde, no renuncia."*

### Objection-handling phrases:
- *"¿Qué pasa si falla? → Acceso de emergencia + UPS 4h autonomía + soporte en <4 horas en Bogotá."*
- *"¿Y los adultos mayores? → También funciona con llavero NFC y código en panel táctil."*
- *"¿Es legal? → Cumplimos Ley 1581. Generamos el libro de visitas digital requerido por propiedad horizontal."*

---

## 12. LAUNCH SEQUENCE RECOMMENDATIONS

### Phase 1 (MVP — launch in 2 weeks):
- Homepage with quote calculator (static ranges are OK to start)
- /cotizacion full form (saves to Notion database or Airtable)
- /contacto
- WhatsApp integration for instant response
- Basic SEO (meta tags, sitemap, Google Business profile)

### Phase 2 (1 month after):
- Blog (5 SEO articles)
- Service detail pages
- Testimonials (from pilot buildings)
- Case studies (2-3 real examples with photos)
- Airbnb integration landing page

### Phase 3 (3 months):
- Customer portal (residents register face/QR from web)
- Admin dashboard (building manager sees access logs, manages access)
- API documentation (for third-party Airbnb channel managers)
- English version of site

---

## 13. WHAT NOT TO DO (lessons from competitive research)

| Don't | Do instead |
|---|---|
| Lead with cameras in the hero | Lead with the outcome: freedom, savings, peace of mind |
| Use "sistema de CCTV" or "video vigilancia" in headlines | Use "acceso inteligente", "identidad automática" |
| Hide pricing ("contáctenos para precio") | Show ballpark ranges. Trust builds from transparency |
| Use generic stock photos of security cameras | Use lifestyle imagery: happy resident, phone unlock, clean lobby |
| Long contact forms (>5 fields) | Name + WhatsApp + "Cuéntanos tu edificio" is enough |
| Dense technical specs on the homepage | Single "Tecnología" page for specs; hide behind tabs |
| Ignore Airbnb angle | Dedicate a full section + landing page to it |
| Generic "smart building" pitch | Specific: *"Tu portero cuesta $5M/mes. IAccess cuesta $1.5M/mes."* |
| Only target building owners | Target: administradores de copropiedad + inversionistas Airbnb + constructoras |
| English-only tech terms | Full Spanish, culturally Colombian, with propiedad horizontal jargon |

---

## 14. ADDITIONAL FEATURES TO CONSIDER (post-launch)

1. **ROI Calculator:** Full spreadsheet-style breakdown: current portero costs (salary, health, pension, vacation, replacements, 13th month, uniforms, food) vs. IAccess total cost of ownership over 5 years. Export as PDF.

2. **Building Comparator:** "See buildings like yours" — show anonymized case studies for similar building types.

3. **Live Demo Booking:** Calendar embed for in-person demo in Bogotá. Show availability in real-time.

4. **WhatsApp Chatbot:** Answers the top 10 FAQ in WhatsApp, qualifies leads, books demo, sends quote PDF — all automated via WhatsApp Business API + n8n or Make.com.

5. **Construcción Mode:** Landing page for construction companies (constructoras) — wire IAccess into the building plan from the start; no portero ever needed. Higher LTV customer.

---

*Brief prepared May 2026. Version 1.0. Ready for design implementation.*
