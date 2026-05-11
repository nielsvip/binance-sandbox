# IAccess — Website Design Brief v2.0
**Company:** IAccess  
**Location:** Bogotá, Colombia  
**Date:** May 2026  
**Purpose:** Complete design + real content instructions for building the IAccess website

> **Pricing basis:** Amazon USD retail price + **25%** importation/shipping/customs to Colombia × 4,300 COP/USD = **× 5,375 total multiplier**. (Buying from local Colombian distributor adds a further 15–25% margin on top — these prices reflect self-import via DHL/FedEx which is how IAccess would source at project scale.)  
> **Labor basis:** $10–40 USD/hour per user spec. General helper $10–12/hr; skilled installer $15–22/hr; senior technician $22–30/hr; lead integrator/programmer $30–40/hr. All COP conversions at 4,300 COP/USD.  
> **Portero cost basis:** Full carga prestacional (~$2,462,050) **+ 20% empresa de vigilancia mandatory margin** = **$2,954,460 COP/portero/month true cost.**

---

## SECTION 1 — BRAND & POSITIONING

### Company Snapshot
IAccess replaces traditional doormen with an AI-powered access system built on Hikvision hardware and YOLO computer vision. Entry: face recognition, QR code, fingerprint, NFC card. Monitoring: continuous cross-floor person tracking. Elevator control: residents reach only their floor; Airbnb guests reach only their unit's floor. Emergency exits: magnets decouple automatically on fire alarm signal.

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
| **True cost per portero** | **~$2,954,460 COP** |

> **Why the 20%:** Colombian law (Decreto 356/1994 + Ley 1539/2012) requires that building guards and porteros be contracted through a licensed *empresa de vigilancia y seguridad privada* supervised by the Superintendencia de Vigilancia. These companies charge a 15–25% margin on top of full labor cost. 20% is the conservative estimate used throughout this document.

Most residential buildings need:
- **Día (6am–6pm) + Noche (6pm–6am) = 2 porteros = ~$5,908,920 COP/month**
- Plus vacation/sick-day replacements, typically +15% = **~$6,795,000 COP/month**

IAccess monthly service for equivalent building: **$850,000–$1,600,000 COP/month**  
**Typical savings: $5.2M–$6.5M COP/month. Payback: 6–14 months.**

### Taglines (A/B test all three)
1. *"Tu edificio nunca duerme. Tu portero, sí."*
2. *"El portero más confiable del mundo no necesita salario ni prestaciones."*
3. *"Cara, QR o huella. Tu edificio inteligente desde $13 millones."*

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
[IAccess ●] | Servicios ▾  Precios  Cómo funciona  Blog  [+57 315 XXX XXXX]  [Cotizar →]
```
- Sticky. Dark navy background (`#0A0E1A`) on scroll.
- `[Cotizar →]` = mint pill button, always visible
- Mobile: hamburger → full-screen overlay with same links + large phone number

---

### HERO — Full viewport

**Background video** (autoplay, muted, loop, 8–12 sec):  
Shot inside a modern Bogotá residential lobby. 5:45am light. A woman in business clothes approaches — a thin mint-green scan ring briefly overlays her face on screen — the frameless glass door opens silently. She walks in. No guard. No desk. Just clean lobby, plants, clean concrete. Cinematic grade.

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
Sin remodelaciones mayores. Sin contratos laborales. Sin interrupciones.
Desde $13,000,000 COP instalado.

CTA ROW:
[Cotización en 60 segundos  →]    [Ver demostración  ▶]
(mint filled, 18px)                 (ghost outline, 18px)
```

**Trust strip** (horizontal row, subtle, below CTAs):
```
  🏢 Edificios en Bogotá: 12 pilotos activos
  ⚡ Instalación: 2–5 días hábiles  
  💰 Ahorro promedio: $5,800,000 COP/mes
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
😤  $6,800,000 COP al mes en dos porteros — más reemplazos
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
✅  $1,200,000–$1,800,000 COP/mes todo incluido
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
│ comunes — desde          │  │ Sin tarjetas ni claves.  │  │ con expiración exacta.   │
│ $3,384,000 COP/punto     │  │ Desde $6,566,000 COP     │  │ Incluido en todos        │
│ Ver más →                │  │ Ver más →                │  │ los planes               │
└─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘

┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│ 🛗 Control ascensores    │  │ 🚗 Acceso vehicular       │  │ 🏠 Modo Airbnb           │
│                          │  │                          │  │                          │
│ Cada residente va solo   │  │ Barrera automática +     │  │ QR con fecha de          │
│ a su piso. Guests: solo  │  │ reconocimiento de        │  │ expiración al checkout.  │
│ al suyo. Desde           │  │ placas. Desde            │  │ Airbnb + Booking         │
│ $6,759,000/ascensor      │  │ $27,355,000 COP          │  │ integrados. Primer en    │
│ Ver más →                │  │ Ver más →                │  │ Colombia. Ver más →      │
└─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘
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

  Instalación:      $17,000,000 – $26,000,000 COP
  Mensualidad:      $850,000 – $1,200,000 COP/mes

  Comparado con portería actual:
  2 porteros (estimado):     $6,795,000 COP/mes
  IAccess:                   $1,025,000 COP/mes
  Ahorro mensual:            $5,770,000 COP
  Retorno de inversión:      ~3–4 meses

  ✅ Incluye: Hardware Hikvision · Instalación · App · Soporte 1 año

  [📋 Cotización detallada por email]   [📞 Hablar con asesor]
  ──────────────────────────────────────────────────────────
  * Estimados basados en proyectos similares en Bogotá.
    La cotización exacta requiere visita técnica gratuita.
```

---

### COST COMPARISON TABLE

**Design:** Clean table on white background. Three columns: cost item, portería tradicional, IAccess.

```
                        PORTERÍA TRADICIONAL     IACCESS
─────────────────────────────────────────────────────────────
Costo mensual           $6,795,000 COP           $1,025,000 COP
Incapacidades médicas   Tú los asumes            No aplica
Vacaciones              Tú cubres el reemplazo   No aplica
Renuncia/liquidación    Hasta $8M COP una vez     No aplica
Disponibilidad          Solo en turno activo      24/7/365
Registro de ingresos    Libro en papel            Digital, con foto
Respuesta a emergency   Depende del portero       App + alerta inmediata
Control de visitantes   Verbal o cuaderno         QR con expiración
Soporte Airbnb          Ninguno                   Integración automática
─────────────────────────────────────────────────────────────
COSTO AÑO 1             $81,540,000              $29,300,000 *
COSTO AÑO 2+            $81,540,000/año           $12,300,000/año
```
_*Año 1 incluye instalación. Cálculo para edificio 20–50 aptos, 2 porteros actuales (incl. empresa de vigilancia 20% + reemplazos 15%)._

---

### TECHNOLOGY STRIP

**Design:** 3 columns on slightly gray background.

```
[Logo Hikvision]              [YOLO AI badge]               [Servidor local / nube]
Hardware líder mundial        Reconocimiento facial          Tus datos quedan
en seguridad IP.              en tiempo real. 99.7%          en Colombia.
Enterprise grade.             precisión con mascarilla.      Cumple Ley 1581.
En edificios, hoteles         Entrenado en millones          Opción nube AWS
y aeropuertos de              de rostros. <400ms             Colombia o servidor
150 países.                   de latencia.                   en tu edificio.
```

---

### TESTIMONIALS (3-card carousel)

```
"Teníamos portero diurno y nocturno — $7.1 millones al mes entre 
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

### FAQ ACCORDION

```
▼ ¿Qué pasa si se va la luz?
  El sistema tiene UPS con autonomía de 4 horas mínimo. Las puertas 
  con magneto se abren mecánicamente ante corte de energía (fail-safe) 
  para no dejar a nadie encerrado. El admin recibe alerta por app.

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
  Sí, con las salvaguardas correctas. Cumplimos la Ley 1581 de 2012 
  (Habeas Data): cada residente firma autorización de tratamiento de 
  datos biométricos, los datos se almacenan encriptados (AES-256) y 
  nunca se comparten con terceros. Generamos el registro de visitantes 
  digital que exige la Ley 675 (Propiedad Horizontal).

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
       (large mint button)      +57 315 XXX XXXX
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
║  $13,000,000 – $19,000,000 COP               ║
║  (pago único)                                ║
╠══════════════════════════════════════════════╣
║  MENSUALIDAD                                 ║
║  $850,000 COP/mes                            ║
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
║  $36,000,000 – $58,000,000 COP               ║
║  (pago único o financiado a 18 meses)        ║
╠══════════════════════════════════════════════╣
║  MENSUALIDAD                                 ║
║  $1,350,000 COP/mes                          ║
╠══════════════════════════════════════════════╣
║  INCLUYE TODO LO DE ESENCIAL, MÁS:           ║
║  ✓ 2 puntos de acceso                        ║
║    (entrada principal + acceso parqueadero)  ║
║  ✓ Lectores Pro DS-K1T671M en entrada        ║
║  ✓ Control 1 ascensor (restricción por piso) ║
║  ✓ 8 cámaras 4MP ColorVu                     ║
║    (lobby + parqueadero + 2 pisos clave)     ║
║  ✓ NVR 16 canales + 4TB                      ║
║  ✓ Servidor IA local Intel NUC i7            ║
║    (reconocimiento en tiempo real,           ║
║     sin depender de internet)                ║
║  ✓ Modo Airbnb (integración básica)          ║
║  ✓ Switch PoE 24 puertos                     ║
║  ✓ Soporte prioritario < 2 horas             ║
║                                              ║
║  TIEMPO DE INSTALACIÓN: 3–4 días hábiles     ║
║  AHORRO ESTIMADO: $5,445,000 COP/mes         ║
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
║  $82,000,000 – $140,000,000 COP              ║
║  (financiación disponible)                   ║
╠══════════════════════════════════════════════╣
║  MENSUALIDAD                                 ║
║  $2,200,000 – $3,500,000 COP/mes             ║
║  (según servicios adicionales contratados)   ║
╠══════════════════════════════════════════════╣
║  INCLUYE TODO LO ANTERIOR, MÁS:              ║
║  ✓ 3–4 puntos de acceso                      ║
║  ✓ Barrera vehicular automática FAAC         ║
║    + cámara reconocimiento de placas         ║
║  ✓ Control 2 ascensores                      ║
║  ✓ Cámaras en todos los pisos (hasta 30)     ║
║  ✓ Servidor Jetson Orin NX (IA dedicada)     ║
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
│ Magneto 600 lb (Seco-Larm $114 + 25% imp.): $613,000       │
│ Lector DS-K1T342 ($254 + 25%): $1,365,000                  │
│ Controlador (~$100 + 25%): $538,000                        │
│ Botones de salida × 2: $268,000                            │
│ Hardware subtotal: $2,784,000 COP                          │
│ Mano de obra: $600,000 COP (2 técnicos × 4 horas)          │
│ TOTAL: $3,384,000 COP por puerta                           │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MODIFICACIÓN DE ENTRADA — de puerta sencilla a doble       │
│ (puertas de vidrio batientes con marco de aluminio)         │
│ Obra civil (ensanche de vano no estructural): $2,500,000    │
│ Marco + hojas de vidrio temperado 10mm: $5,500,000          │
│ 2× magnetos 1200 lb ($175 × 2 + 25% imp.): $1,882,000     │
│ 2× lectores Pro DS-K1T671M ($450 × 2 + 25%): $4,838,000   │
│ Controlador + salidas ($100 + 25%): $672,000               │
│ Mano de obra instalación sistema: $1,200,000               │
│ TOTAL: $16,592,000 COP                                    │
│ * Vano estructural: +$4,000,000–$8,000,000 COP extra       │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ PUERTA AUTOMÁTICA DESLIZANTE DE VIDRIO                      │
│ (entrada principal nueva, con automatización)               │
│ Obra civil (piso + umbral + rieles): $2,000,000             │
│ Paneles de vidrio templado + marco: $5,000,000              │
│ Operador automático ($1,200 + 25% imp.): $6,450,000        │
│ Magneto fail-safe ($114 + 25%): $613,000                   │
│ Lector facial DS-K1T342 + controlador: $1,903,000          │
│ Mano de obra (2 técnicos × 2 días): $2,580,000             │
│ TOTAL: $18,546,000 COP                                    │
│ * Incluye garantía 2 años en operador                      │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ CONTROL DE ASCENSOR (por ascensor)                          │
│ Restricción de pisos: cada residente/guest va solo          │
│ al piso autorizado. Huéspedes Airbnb: solo su piso.         │
│                                                             │
│ Controlador UHPPOTE 20ch ($330 + 25%): $1,774,000          │
│ Lector facial cabina DS-K1T342 ($254 + 25%): $1,365,000    │
│ Cableado de relés a botonera (materiales): $800,000         │
│ Mano de obra técnico señalista 1 día: $720,000             │
│ Coordinación y apertura empresa de ascensores: $1,500,000  │
│ Programación y pruebas: $600,000                           │
│ TOTAL: $6,759,000 COP por ascensor                        │
│ * Hasta 20 pisos. Edificios 21–40 pisos:                   │
│   UHPPOTE 40ch ($500 + 25%): $8,636,000 COP total         │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MONITOREO FACIAL EN PISOS (cámaras de pasillo por piso)    │
│ Tracking continuo: el sistema registra trayectoria         │
│ de cada persona desde lobby hasta su piso y de vuelta.     │
│                                                             │
│ Por piso: 1× cámara 4MP ColorVu ($300 + 25%): $1,613,000  │
│ Cableado e instalación: $600,000                           │
│ TOTAL por piso: $2,213,000 COP                            │
│ Ejemplo: edificio 15 pisos: $33,195,000 COP               │
│ * Requiere Plan Profesional o Enterprise                   │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ ACCESO VEHICULAR — Barrera + Reconocimiento de Placas      │
│                                                             │
│ Barrera FAAC ($2,000 + 25% imp.): $10,750,000             │
│ Cámara LPR Hikvision ($800 + 25%): $4,300,000             │
│ Controlador + software licencia de placas: $1,500,000      │
│ Obra civil (detector de piso, señalización): $5,000,000     │
│ Mano de obra (3 técnicos × 3 días): $5,805,000             │
│ TOTAL: $27,355,000 COP                                    │
│ * Incluye 500 placas en base de datos                      │
│   Placas adicionales: $15,000 COP/unidad                   │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MAGNETO PARA PUERTA INTERIOR EXISTENTE                     │
│ (cuarto de servicio, utilería, azotea, bicicletero, etc.)  │
│                                                             │
│ Magneto 600 lb ($114 + 25% imp.): $613,000                 │
│ Botón de salida interior ($25 + 25%): $134,000             │
│ Lector RFID sin facial ($90 + 25%): $484,000              │
│ Mano de obra 1 técnico × 3 horas: $215,000                 │
│ TOTAL: $1,446,000 COP por puerta                          │
│ * Para acceso facial en interiores: +$935,000 COP          │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ SALIDA DE EMERGENCIA — Desenganche de magneto              │
│ (integración con sistema de alarma contra incendios)        │
│                                                             │
│ Módulo de relé + integración panel de alarma: $500,000     │
│ Botón de emergencia con tapa protegida: $120,000           │
│ Mano de obra + programación: $400,000                      │
│ Certificado de prueba y conformidad: $200,000              │
│ TOTAL: $1,220,000 COP por salida de emergencia            │
│ * Cumple RETIE y normas NFPA 101 aplicables en Colombia    │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MODO AIRBNB COMPLETO                                        │
│ (si no está incluido en el plan elegido)                    │
│                                                             │
│ Setup integración Airbnb + Booking + VRBO: $2,500,000      │
│ Configuración WhatsApp Business API: $800,000              │
│ TOTAL instalación: $3,300,000 COP                         │
│ Mensualidad adicional: $280,000 COP/mes                    │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ MONITOREO REMOTO 24/7 CON OPERADORES                       │
│ Centro de control revisa alertas, verifica ingresos        │
│ inusuales, contacta al administrador en tiempo real.       │
│                                                             │
│ Mensualidad: $490,000 COP/mes                              │
│ Tiempo de respuesta a alerta: < 3 minutos                  │
│ Turnos: operadores en 3 turnos, 365 días                   │
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

¿Dónde prefieres guardar los datos y el sistema de IA?
  ○ Servidor local en el edificio (más privado, sin depender de internet)
  ○ Nube (menor mantenimiento, requiere buena conexión)
  ○ No sé — recomiéndenme

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
║  $42,000,000 – $68,000,000 COP                   ║
║                                                  ║
║  Mensualidad:                                    ║
║  $1,350,000 COP/mes                              ║
║                                                  ║
║  ─────────────────────────────────────           ║
║  COMPARACIÓN CON TU PORTERÍA ACTUAL:             ║
║                                                  ║
║  Costo estimado 2 porteros:  $6,795,000/mes      ║
║  Costo IAccess:              $1,350,000/mes      ║
║  Ahorro mensual:             $5,445,000/mes      ║
║                                                  ║
║  Retorno de inversión:       ~9 meses            ║
║                                                  ║
║  ─────────────────────────────────────           ║
║  HARDWARE INCLUIDO:                              ║
║  ✓ 2× Hikvision DS-K1T671M (entrada)             ║
║  ✓ 1× Hikvision DS-K1T342 (secundario)           ║
║  ✓ 1× videoportero DS-KD8003                     ║
║  ✓ 3× magnetos 1200 lb                           ║
║  ✓ 8× cámaras 4MP ColorVu                        ║
║  ✓ NVR 16ch + 4TB + servidor IA                  ║
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
[WhatsApp (+57 XXX XXX XXXX)]
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
Día 3: instalación de NVR, servidor IA y switch PoE
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
                    [SERVIDOR LOCAL]
                  Intel NUC i7 o Jetson Orin NX
                  YOLO AI · Base datos facial
                  Servidor IAccess · Logs
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
• Temperatura de operación: -30°C a 60°C
• IP65 (resistente a lluvia y polvo)
• Módulo de huella dactilar integrado
• Lector de tarjeta NFC/Mifare incorporado
• Amazon USA: ~$450 · Precio Colombia (importado +25%): **~$2,419,000 COP**

Lector facial secundario — HIKVISION DS-K1T342MFWX-E1 (Value Series)
• Para accesos secundarios (parqueadero, zonas comunes)
• Pantalla 4.3", facial + huella + tarjeta
• 6,000 rostros almacenados
• IP65, PoE
• Amazon USA: ~$254 · Precio Colombia (importado +25%): **~$1,365,000 COP**

Cámara de vigilancia — HIKVISION DS-2CD2347G2-LU (4MP ColorVu)
• Imagen en color 24/7 (sin modo IR — imagen color real de noche)
• 4MP resolución, lente 4mm (FOV 84°) o 2.8mm (FOV 108°)
• Detección de movimiento AI (persona vs. vehículo vs. animal)
• Micrófono incorporado
• IP67 resistente a intemperie
• PoE 12W
• Amazon USA: ~$300 · Precio Colombia (importado +25%): **~$1,613,000 COP/unidad**

Servidor de IA local — INTEL NUC 13 PRO i7-1370P
(para edificios hasta 60 aptos / 16 cámaras)
• Procesador Intel Core i7 13ª generación, 12 núcleos
• 32GB RAM DDR4
• 1TB SSD NVMe
• Intel Iris Xe Graphics (aceleración de inferencia YOLO)
• Consumo: 28W en reposo, 64W máximo
• Amazon USA: ~$750 · Precio Colombia (importado +25%): **~$4,031,000 COP**

Servidor de IA local — NVIDIA JETSON ORIN NX 16GB
(para edificios 60+ aptos o con 20+ cámaras)
• GPU 1024 núcleos Ampere (aceleración nativa YOLOv8)
• CPU: 8 núcleos Cortex-A78AE
• 16GB LPDDR5 RAM
• 128GB SSD NVMe
• Hasta 100 TOPS de rendimiento de IA
• Procesa 20+ flujos de video simultáneos en tiempo real
• Amazon/distributor USA: ~$1,200 · Precio Colombia (importado +25%): **~$6,450,000 COP**

──────────────────────────────────────────────
SEGURIDAD Y PRIVACIDAD

• Base de datos biométrica encriptada AES-256
• Datos almacenados solo en servidor local del edificio (no en la nube de IAccess)
• Cada residente firma autorización de tratamiento de datos (Ley 1581)
• Derecho al olvido: el administrador borra el perfil en 30 segundos
• Los logs de ingreso se guardan por 90 días (configurable)
• Backup encriptado en nube con llave del edificio (no de IAccess)

──────────────────────────────────────────────
PRECIOS

Punto de acceso con facial (lector Pro + magneto + controlador):
Desde $5,366,000 COP (hardware: DS-K1T671M $2,419k + magneto 1200 lb $941k + controlador $538k + salidas $268k) + $1,200,000 COP (instalación)
Total: desde $6,566,000 COP por punto de acceso

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
• Amazon USA: $330 (20ch) / $500 (40ch) · Precio Colombia (+20% imp.):
  UHPPOTE 20ch **~$1,774,000 COP** · 40ch **~$2,688,000 COP**

LECTOR EN CABINA — HIKVISION DS-K1T342MFWX-E1
• Panel compacto montado en la pared interior del ascensor
• Reconocimiento facial + QR + tarjeta NFC
• Activación: cuando el usuario sube, identifica → habilita sus pisos
• Amazon USA: $254 · Precio Colombia (+25% imp.): **~$1,365,000 COP** por lector

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

Por ascensor, edificio hasta 20 pisos:   $6,759,000 COP (instalado)
Por ascensor, edificio 21–40 pisos:      $8,636,000 COP (instalado)
Lector en cabina (adicional):            +$1,903,000 COP
Cámara en cabina (adicional):            +$1,075,000 COP

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
  Vano existente suficiente (sin obra civil):  $16,270,000 COP
  Requiere ensanche de vano (no estructural):  $18,770,000 – $21,270,000 COP
  Requiere obra estructural (refuerzo, viga):  $22,770,000 – $28,770,000 COP

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
  Vano estándar 1.8–2.4m de ancho: $18,187,000 – $22,000,000 COP
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

Precio: $1,485,000 COP por puerta (sin facial)
Con lector facial: $2,585,000 COP por puerta

──────────────────────────────────────────────
ESPECIFICACIÓN TÉCNICA DE MAGNETOS

| Magneto | Fuerza | Uso recomendado | Amazon USA | Colombia (+20% imp.) |
|---|---|---|---|---|
| 600 lb / 272 kg | Media | Puertas livianas, interiores | $114 | **$613,000 COP** |
| 1200 lb / 544 kg | Alta | Puertas principales, vidrio pesado | $175 | **$941,000 COP** |
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

Precio integración de emergencia: $1,220,000 COP por salida de emergencia
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
SERVIDOR LOCAL vs. NUBE

SERVIDOR LOCAL (recomendado para la mayoría):
Toda la IA y los datos se procesan en un servidor dentro del 
edificio. Internet no es requerido para que el sistema funcione.

+ Sin dependencia de conexión a internet
+ Datos biométricos nunca salen del edificio
+ Mejor latencia (procesamiento en la misma red local)
+ Sin costo de nube en el largo plazo
- Requiere espacio físico (cuarto de sistemas o rack)
- Mantenimiento hardware de responsabilidad del edificio 
  (cubierto en contrato de servicio IAccess)

SERVIDOR EN NUBE (AWS Colombia, Bogotá):
La IA se ejecuta en AWS South America (São Paulo) o 
AWS región Colombia cuando esté disponible.

+ Sin hardware en el edificio
+ IAccess maneja toda la infraestructura
+ Escalable sin cambio de hardware
- Requiere internet estable (mínimo 50 Mbps simétrico)
- Latencia ligeramente mayor (80–200ms adicionales)
- Costo mensual adicional ($350,000 COP/mes)
- Datos pasan por infraestructura de terceros (con encriptación)

NUESTRA RECOMENDACIÓN:
Para edificios con buena infraestructura eléctrica y 
espacio para un pequeño rack o gabinete: servidor local.
Para edificios pequeños (<25 aptos) sin cuarto de sistemas: nube.

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
• Servidor local Intel NUC i7
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
• Edificio hasta 20 aptos, diurno solo: 1 portero → **$2.95M/mes** (con empresa de vigilancia)
• Edificio hasta 60 aptos, día y noche: 2 porteros → **$5.91M/mes** raw, **$6.79M/mes** con reemplazos
• Edificio 60-120 aptos, 24/7 con reemplazos: 3 porteros → **$8.86M/mes** raw, **$10.2M/mes** con reemplazos
• Edificio premium 120+ aptos: 4+ porteros → **$11.8M+/mes** raw, **$13.6M+/mes** con reemplazos

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

### Article 3: Legal piece

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

## SECTION 11 — CONTACT PAGE (/contacto)

```
HEADLINE: Hablamos.

No hay chatbot. No hay formulario que desaparece en el vacío.
Cuando nos escribes, responde una persona real en Bogotá.

─────────────────────────────────────────────
FORMAS DE CONTACTO:

[WhatsApp →]                    [Email →]
+57 315 XXX XXXX               info@iaccess.co
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
Bogotá D.C. (dirección exacta disponible a clientes)
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

## SECTION 12 — DEVELOPER SPEC

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
  accessPoint: {
    readerPro:    2_419_000,  // DS-K1T671M  ($450 × 5,375)
    readerValue:  1_365_000,  // DS-K1T342   ($254 × 5,375)
    maglock1200:    941_000,  // 1200 lb     ($175 × 5,375)
    maglock600:     613_000,  // 600 lb      ($114 × 5,375)
    controller:     538_000,  // (~$100 × 5,375)
    exitButton:     134_000,  // (~$25  × 5,375)
  },
  camera: {
    colorVu4MP:   1_613_000,  // DS-2CD2347G2-LU  ($300 × 5,375)
    colorVu8MP:   2_043_000,  // DS-2CD2387G2-LU  ($380 × 5,375)
  },
  nvr: {
    ch16_4TB:     2_865_000,  // DS-7616NXI-K2 + 4TB HDD  ($533 × 5,375)
    ch8_2TB:      2_150_000,  // 8ch NVR + 2TB (~$400 × 5,375)
  },
  server: {
    nucI7:        4_031_000,  // Intel NUC 13 Pro i7  ($750 × 5,375)
    jetsonOrinNX: 6_450_000,  // NVIDIA Jetson Orin NX 16GB  ($1,200 × 5,375)
  },
  network: {
    ups1500va:      806_000,  // ($150 × 5,375)
    switch24poe:  1_075_000,  // ($200 × 5,375)
    switch8poe:     430_000,  // ($80  × 5,375)
  },
  elevator: {
    uhppote20ch:  1_774_000,  // UHPPOTE 20ch ($330 × 5,375)
    uhppote40ch:  2_688_000,  // UHPPOTE 40ch ($500 × 5,375)
  },
  vehicle: {
    faacGate:    10_750_000,  // FAAC barrier ($2,000 × 5,375)
    lprCamera:    4_300_000,  // Hikvision LPR ($800 × 5,375)
  },
  install: {
    techHr:        86_000,   // $20/hr × 4,300 COP/USD (skilled installer)
    leadHr:       150_000,   // $35/hr × 4,300 COP/USD (lead integrator)
    helperHr:      52_000,   // $12/hr × 4,300 COP/USD
  },
  monthly: {
    softwareLicense: 450_000,
    support:         380_000,
    perApartment:     12_000,
    airbnb:          280_000,
    monitoring24_7:  490_000,
    cloud:           350_000,
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
    serverType,      // "local" | "cloud" | "unknown"
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

  // Server
  const serverCost = isEnterprise
    ? PRICING.server.jetsonOrinNX
    : PRICING.server.nucI7;
  if (serverType !== "cloud") hardware += serverCost;

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
  let monthly = PRICING.monthly.softwareLicense + PRICING.monthly.support;
  monthly += nApts * PRICING.monthly.perApartment;
  if (airbnb) monthly += PRICING.monthly.airbnb;
  if (monitoring) monthly += PRICING.monthly.monitoring24_7;
  if (serverType === "cloud") monthly += PRICING.monthly.cloud;

  // ─── DOORMEN COST ───
  const porteroCount = { "0": 0, "1": 1, "2": 2, "3+": 3 };
  const nPorteros = currentPorteros !== undefined
    ? porteroCount[currentPorteros] || 0
    : (nApts < 25 ? 1 : nApts < 80 ? 2 : 3);
  // $2,462,050 base × 1.20 empresa de vigilancia mandatory cut × 1.15 replacement buffer
  const porteroMonthlyCost = nPorteros * 2_462_050 * 1.20 * 1.15;

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

## SECTION 13 — SEO ESSENTIALS

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
  "telephone": "+573XXXXXXXXX",
  "address": {
    "@type": "PostalAddress",
    "addressLocality": "Bogotá",
    "addressCountry": "CO"
  },
  "priceRange": "$$",
  "areaServed": "Bogotá, Colombia",
  "serviceType": "Access Control, Building Automation, Portería Virtual"
}
```

### Target keywords
```
Primary:
  portería virtual Bogotá
  portería virtual Colombia
  sistema acceso edificio residencial Colombia
  control de acceso edificio Bogotá
  
Secondary:
  reemplazar portero edificio
  costo portero Colombia
  reconocimiento facial edificio Colombia
  edificio Airbnb acceso automático
  control ascensor por piso
  
Long-tail:
  cuánto cuesta portería virtual en Colombia
  cómo automatizar el ingreso a mi edificio
  magneto electromagnético puerta vidrio edificio
  control de acceso sin portero Bogotá
```

---

## SECTION 14 — WHAT NOT TO DO

| ❌ Don't | ✅ Do |
|---|---|
| Show cameras in hero or above-fold | Show a person's face being scanned — the EXPERIENCE |
| "Sistema de CCTV" or "videovigilancia" in headlines | "Reconocimiento facial" and "acceso inteligente" |
| Generic stock photos of security guards | Lifestyle photos: modern lobby, person on phone, clean glass door |
| Hide all pricing | Show ranges, model numbers, COP prices — build trust |
| "Contáctenos para más información" as only CTA | Quote calculator + WhatsApp direct link on every page |
| Ignore Airbnb | Dedicate full section + landing page — it's the biggest gap in the market |
| Only target building owners | Target: admins, Airbnb investors, constructoras separately |
| "Smart building" buzzwords | "Tu portero cuesta $5.6M al mes. IAccess cuesta $1.35M." |
| English-only tech terms | Full Spanish, propiedad horizontal vocabulary |
| Photo of surveillance camera equipment | Photo of person entering through elegant glass door |
| Testimonials without specifics | Always include: building name, neighborhood, # units, date |
| Contact form with 8+ fields | Name + WhatsApp + building = enough to start |
| Justify price without comparison | Always anchor against cost of porteros — make savings tangible |

---

*IAccess Website Design Brief v2.1 — May 2026*  
*Pricing basis: Amazon USD retail price + 20% importation/shipping × 4,300 COP/USD = × 5,160 multiplier*  
*Labor rates: $10–40 USD/hr per client spec — skilled installer $20/hr, lead integrator $35/hr*  
*Portero cost basis: 2026 salario mínimo $1,423,500 + full carga prestacional ($2,462,050) + 20% empresa de vigilancia = $2,954,460/portero/month*
