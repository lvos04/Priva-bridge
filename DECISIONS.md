# Besluiten en aannames — Priva Bridge

Bij een open punt uit sectie 12 van het implementatieplan zonder antwoord geldt de standaardkeuze. Dit
bestand legt per punt vast wat geldt, en welke eigen keuzes in slice 1 zijn gemaakt. Status per 16-11-2026.

## Open punten uit sectie 12

| Nr | Vraag | Wat nu geldt | Bron |
|---|---|---|---|
| O1 | Kranen/strengen boven 40 | **Opgelost.** Kraan 41–44 = gevelkranen, kraan 45 = achtertuin: bekende bronapparaten met zone `gevel`/`achtertuin`, mapping-status `bevestigd`, níet aan een kap of afdeling gekoppeld, tellen niet mee in kap- of afdelingsaggregaten; opvraagbaar als `bron:kraan:41` t/m `45`. Een kraan 46+ wordt `unmapped` opgeslagen. Vastgelegd in `catalog/mapping.yaml` (`kranen_zonder_kap`) en getest (`tests/test_mapping.py`). Het Kranen-rapport zelf wordt pas in slice 2 geïmporteerd. | Bevestigd door Leon, 16-11-2026 |
| O2 | Afdeling 2–4 in het klimaatrapport? | Standaard: alleen afdeling 1 wordt verwacht (de echte Klimaat-export bevat alleen `Afdeling 1`); afdeling 2–4 tonen als `ontbreekt`. Instelbaar in `mapping.yaml` → `verwachting.klimaat.afdelingen`. Let op: het rapport **Algemeen Bedrijf** bevat wél afdeling 1–4 (slice 2). | Standaard |
| O2b | Klimaat-afdelingen idx 5–8 | Niet importeren, wel zichtbaar overslaan: `mapping.yaml` → `afdelingen_genegeerd` (status `genegeerd`); de preview/importsamenvatting meldt "Overgeslagen: afdeling 5–8, genegeerd volgens mapping (N regels)". Een afdeling 9+ stopt de import met een melding. Zie vraag V1: in de aangeleverde bestanden komt `Afdeling 5–8` niet voor. | Bevestigd door Leon, 16-11-2026 |
| O3 | Buitenstraling als dagsom | Metric `buitenstraling_som` staat in de catalogus met status `ontbreekt`; altijd `ontbreekt`. (Algemeen Bedrijf bevat `Sum_Day_Radiation` met objecttype `Straling`; beoordelen in slice 2.) | Standaard |
| O4 | Kanaal 1 en 2 bij lichtmodules; telt PAR beide lamptypes | Beide kanalen apart opgeslagen: `lamp_*` = kanaal 1, `lamp_*_kanaal2` = kanaal 2. Afgeleide lichtsom (slice 2) alleen uit brandseconden en stand. | Standaard |
| O5 | Data eerdere lampconfiguraties | Lampconfiguratie geldig vanaf 2024-08-01; daarvóór is de module-mapping niet geldig en komt er geen afgeleide lichtsom. | Standaard |
| O6 | Verduisteringsdoek; autonoom doek 1–8 | Alle doeken apart onder hun Priva-naam: `scherm_seconden_doek1/2` (per afdeling), `scherm_autonoom_seconden_doek1/2` per `bron:doek:1–8` (nog zonder kap/afdeling). | Standaard |
| O7 | Eenheden vochtdeficit, CalculatedRadiation, watergift | Eenheid `onbevestigd` in de catalogus, waarde ongewijzigd doorgegeven, `verwachte_eenheid` als documentatie. Toegepast op álle eenheden die het plan als "bevestigen" markeert (ook abs. vocht, PAR-som lampen, groeilichtsom, lampstraling en -energie, CO2-dosering). | Standaard |
| O8 | CO2-meter (CompCO2 = 0) | `Ave_*_CompCO2` wordt overgeslagen zolang de waarde leeg of 0 is; een andere waarde stopt de import (dan is er kennelijk wél een meter). Alleen de dosering (`co2_dosering`) wordt opgeslagen. | Standaard |
| O9 | Meldingen | Statuspagina en `/api/v1/status`; webhook vanaf slice 4. | Standaard |
| O10 | SQLite of PostgreSQL | SQLite (WAL) in `/data/db/bridge.db`; PostgreSQL mogelijk via `DATABASE_URL` (geen automatische back-up vóór migratie voor PostgreSQL: maak zelf een dump). | Standaard |
| O11 | Image openbaar? | Privé-register `ghcr.io/lvos04/priva-bridge`; TrueNAS heeft een registry-credential nodig. | Standaard |
| O12 | Uitkomst Priva-proef | Handmatig blijven uploaden (`source_mode: manual`). | Standaard |
| — | Belichting-export met datums 10–16 nov **2025** | Klopt, bewust zo aangeleverd. | Bevestigd door Leon, 16-11-2026 |

## Formaat van de Priva-export

- Aanvankelijk overgenomen uit de importer van de gewichtsapp (gebouwd op echte bestanden), daarna getoetst
  op de vijf echte exports die lokaal in `tests/fixtures/echt/` staan: kopregel
  `label;pcu;type_1;idx_1;type_2;idx_2;startdate;enddate;value`, puntkomma, UTF-8 zonder BOM, LF,
  decimale komma, datums `d-m-jj` met begin = eind (dagwaarden), lege velden voor ontbrekende waarden.
- De parser accepteert daarnaast UTF-8 met BOM, Windows-1252 (terugval), tab als scheidingsteken, decimale
  punt en `d-m-jjjj`/`d/m/jj` (sectie 7). Tweecijferige jaren 00–69 = 2000–2069; 70–99 wordt geweigerd.
- Een tijdstip in de datum of begin ≠ eind = onbekende variant (slice 1 importeert alleen dagwaarden).
- **Echte exports niet in git**: de repository is publiek en het zijn bedrijfsgegevens. `tests/fixtures/echt/`
  staat in `.gitignore`; CI draait op synthetische fixtures en slaat de echte-bestandentests over.

## Eigen keuzes in slice 1

| Nr | Keuze | Reden |
|---|---|---|
| B1 | **Catalogus = de echte labels.** `catalog/metrics.yaml` dekt alle 85 labels uit de echte Klimaat- en alle 6 uit de Belichting-export (85 metrics, 18 bewust overgeslagen labels). Sleutels: de plansleutels (`temp_etmaal_avg`, `rv_avg`, `vochtdeficit_avg`, `abs_vocht_avg`, `dauwpuntsverschil_avg`, `scherm_seconden_*`, `par_som_lampen`, `straling_som_berekend`, `co2_dosering`, `lamp_*`) plus het schema `<grootheid>_<etmaal|dag|nacht>_<avg|min|max>` voor de overige Ave/Min/Max-varianten. | Beide voorbeeldrapporten importeren zonder handwerk; een onbekend label stopt nog steeds de import. |
| B2 | Bekende lege kolommen (`CalculatedPAR`, `MeasuredRadiation`, `Energy_Water(_Autonomous)`, `MeasuredPAR`, `CompCO2`, alle perioden) worden alleen overgeslagen met de in de catalogus toegestane waarde (leeg, of leeg/0). Een echte waarde in zo'n kolom stopt de import. | "Lege kolommen herkennen en overslaan" zonder ooit stil data weg te gooien. |
| B3 | Nieuwe brontypes uit de echte Klimaat/Belichting: `Autonoom doek` → `bron:doek:N`, `CO2 doseerruimte` → `bron:doseerruimte:N`, `Autonome verwarming` → `bron:verwarming:N`, `Groeilicht sturing` (Streng 1–4) → `bron:groeilicht:N`. Opgeslagen als `unmapped` en zo gemeld. | Datamodel kent `doek`/`doseerruimte`; kap/afdeling is onbekend (vragen V2–V4). |
| B4 | `co2_dosering` komt uit `Sum_24h_CO2Prog` (een etmaal**som**, per doseerruimte), niet uit een gemiddelde in ppm zoals sectie 5 aannam. Aggregatie `sum`, eenheid `onbevestigd`. | Zo staat het in de echte export. |
| B5 | `lamp_brandseconden` op een kap = **som** over de verwachte lichtmodules van die kap; `sources` noemt ze, `coverage` = aanwezig / verwacht. Per lamptype opvragen kan via `bron:module:N`. Lampstand/PAR/straling/energie alleen per module. | Volgt het voorbeeld in sectie 8.4; geen zinloos gemiddelde over twee lamptypes. |
| B6 | **Belichting bevat alleen module 101–140** (120 µmol). Module 1–40 (80 µmol) komt niet als `modgroeilichtsturing` voor. Daarom verwacht de Bridge alleen 101–140 (`mapping.yaml` → `verwachting.belichting`); de mapping 1–40 → kap 1–40 blijft staan. | Anders zou elke dag "onvolledig" zijn. Zie vraag V2. |
| B7 | Een dag is **compleet** als alle verwachte bronnen een waarde hebben: Klimaat voor de verwachte afdelingen én Belichting voor de verwachte modules. `missing_dates` = alle niet-complete dagen van de eerste dag met data t/m gisteren (lokale tijd). | Eén eenduidige definitie voor status, kalender en `last_complete_date`. |
| B8 | Revisies: gelijke waarde → niets nieuws; afwijkende waarde → nieuwe revisie (oude blijft); een **leeg** veld overschrijft nooit een bekende waarde (geteld als `leeg_niet_overschreven`). Twee verschillende waarden voor hetzelfde punt in één bestand stoppen de import. | Leeg is niet nul, en er verdwijnt niets. |
| B9 | Een metric die op een scope niet bestaat (bijv. `temp_etmaal_avg` op `kap:5` zonder `inherit`) wordt weggelaten; levert het hele verzoek niets op, dan `400` met uitleg. Met `inherit=true` krijgt een kap de afdelingswaarde, `resolved_from = "afdeling:M"`. | "Niet elk meetpunt bestaat per kap" zonder schijnbare gaten. |
| B10 | `/daily` is een vast raster scope × metric × datum met een offset-cursor (gebonden aan de parameters). Volgorde: scope zoals gevraagd, metric (catalogusvolgorde of zoals gevraagd), datum. Maximaal 3700 dagen en 200 scopes per verzoek. | Stabiel pagineren, ook als er tussendoor geïmporteerd wordt. |
| B11 | `lichtsom_lampen_kap` (mol/m²/dag) staat al in de catalogus maar is in slice 1 altijd `ontbreekt`; berekening in slice 2. | Contract met de gewichtsapp. |
| B12 | `/api/v1/metrics` geeft per metric `metric_key`, `unit`, `level` (hoofdniveau), `levels`, `aggregation`, plus omschrijving, verwachte eenheid, rapport, status en Priva-namen. | Afgestemd met de gewichtsapp. |
| B13 | Plausibiliteit → quality `verdacht` (waarde blijft): temperatuur −40…65 °C, RV 0…100 %, lampstand 0…100 %, seconden 0…lengte van die lokale dag (82 800 / 86 400 / 90 000 s). | Zomer-/wintertijd correct; geen onterechte "verdacht" op 25-uursdagen. |
| B14 | API-tokens: `pb_` + 32 bytes, opgeslagen als HMAC-SHA256 met `BRIDGE_SECRET_KEY`. Aanmaken via het opstartcommando `bridge token aanmaken` (beheer in de UI volgt in slice 5). Rate limit 120/min per token (instelbaar). | Sectie 8.1 en 11. |
| B15 | Bedrijfskoppeling: **aparte koppelsleutels** (`bk_`, SHA-256, eigen tabel), los van de `/api/v1`-tokens; schakelaar standaard uit. | Eenvoudigst veilig: een dashboardsleutel kan nooit `/api/v1` lezen en andersom. Zie `docs/koppeling-api.md`. |
| B16 | `daily_aggregate` (sectie 6) volgt met de aggregator in slice 2; in slice 1 rekent `/daily` op de observaties, die al dagwaarden zijn. Geen APScheduler in slice 1 (niets te plannen). | Slice-afbakening. |
| B17 | Alleen CSV-upload; ZIP volgt in slice 3. Bestandsgrootte max. 64 MB. | Slice-afbakening. |
| B18 | Beheer-UI via HTTP op het interne netwerk (sessiecookie niet `Secure`, wel `SameSite=Strict`, CSRF-token, 5 mislukte logins per 5 min per IP). | De Bridge blijft intern (sectie 3.1). |
| B19 | Migraties draaien bij elke start; bestaat er al een SQLite-database en is er een migratie te doen, dan eerst een consistente kopie (SQLite-backup-API) naar `/data/backups/bridge-<tijd>-voor-<revisie>.db`. | Sectie 10. |
| B20 | Herkenning van de rapportfamilie volgt de (op echte bestanden gebouwde) regels van de gewichtsapp, met één aanvulling: labels die alleen Klimaat heeft (`DewPointDifference`, `PAR_Growlight`) gaan vóór de regel "meerdere afdelingen = Algemeen Bedrijf", en genegeerde afdelingen (5–8) tellen niet mee. | Een Klimaat-export met afdeling 1–4 blijft Klimaat. Getoetst op alle vijf echte exports. |

## Vragen aan Leon

- **V1** — Je schreef dat Klimaat-afdeling idx 5–8 irrelevant is. In de aangeleverde Klimaat-export komt
  objecttype `Afdeling` alleen met nummer 1 voor; nummers 1–8 horen daar bij **`Autonoom doek`** (met
  `Doek 1` voor 1–8 en ook `Doek 2` voor 1–4). Bedoelde je autonoom doek 5–8? Die worden nu gewoon
  opgeslagen als `bron:doek:5–8`. De regel voor afdeling 5–8 staat er wel (voor het geval een andere export
  ze bevat).
- **V2** — Waar komen de 80 µmol-lampen (lichtmodule 1–40) vandaan? Het Belichtingsrapport heeft alleen
  `modgroeilichtsturing` 101–140, plus `Groeilicht sturing` 1–44 met `Streng` 1–4. Zijn dat de 80 µmol-lampen,
  en welke groeilichtsturing/streng hoort bij welke kap? (Nodig voor `lichtsom_lampen_kap` in slice 2.)
- **V3** — Wat zijn kanaal 1 en 2 van een lichtmodule (O4)? In de export brandt kanaal 1 langer met lagere
  stand en heeft kanaal 2 PAR 0.
- **V4** — Bij welke afdeling(en)/kappen horen autonoom doek 1–8, CO2-doseerruimte 1–2 en autonome
  verwarming 1–2? Wat is "extratemperatuur 1" en "Verwarming 1" (watertemperatuur) bij afdeling 1?
- **V5** — Eenheden (O7): `CompHD`/`CompAV` g/m³? `PAR_Growlight` mol/m²? `CalculatedRadiation` en
  `Growlight` J/cm²? `ModGrowlightElectricalEnergy` kWh? `CO2Prog` (een som) in kg?
- **V6** — In de Klimaat-export zijn 14 en 15 november 2026 helemaal leeg. Was de export te vroeg gemaakt,
  of ontbreken die dagen echt in Priva?
- **V7** — Wil je voor de steekproef (klaar-wanneer slice 1) tien waarden rechtstreeks uit het Priva-scherm
  of de PDF overnemen in `tests/fixtures/echt/steekproef.csv` (`datum;scope;metric;waarde`)? Die worden dan
  automatisch tegen `/api/v1/daily` getoetst.
- **V8** — Mogen (geanonimiseerde) echte exports in de publieke repository, of blijven ze lokaal?
