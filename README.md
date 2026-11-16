# Priva Bridge

Lokale, alleen-lezen gegevensbron met Priva-dagwaarden voor de eigen tuinbouwapps (gewichtsvoorspelling,
teeltplanning). Eén container op TrueNAS SCALE: handmatige upload van Priva-rapportage-exports, controle,
ruw archief, en een API met dagwaarden per afdeling, kap en bronapparaat — met dekking en kwaliteit, zodat
een ontbrekende dag nooit als 0 telt.

Leidend document: [`Priva_Bridge_TrueNAS_implementatieplan_v1.1.md`](Priva_Bridge_TrueNAS_implementatieplan_v1.1.md).
Gemaakte keuzes en open vragen: [`DECISIONS.md`](DECISIONS.md).

**Stand: slice 1** (versie 0.1.0) — upload, parsers voor Klimaat en Belichting, ruw archief, ledger,
catalogus, vaste mapping, `/health`, `/api/v1/status`, `/api/v1/metrics`, `/api/v1/daily`, tokens via het
opstartcommando, statuspagina. Daarnaast de alleen-lezen [Bedrijfskoppeling API v1](docs/koppeling-api.md).

De Bridge stuurt niets naar Priva, gebruikt geen Priva-poorten of -database en schrijft nooit naar de
Priva-server.

## Installatie op TrueNAS (sectie 10, zonder YAML)

1. Maak een dataset aan, bijvoorbeeld `priva-bridge`. Geef de gebruiker `apps` (UID/GID 568) schrijfrechten.
2. **Apps → Discover Apps → Custom App**.
3. Application Name: `priva-bridge`.
4. Image: repository `ghcr.io/lvos04/priva-bridge`, tag bijvoorbeeld `0.1.0` (of `0.1.0-rc` voor een testbuild).
   Het register is privé (open punt O11): voeg in TrueNAS een registry-credential voor `ghcr.io` toe
   (GitHub-gebruiker + token met `read:packages`).
5. Omgevingsvariabelen:

   | Variabele | Waarde |
   |---|---|
   | `TZ` | `Europe/Amsterdam` |
   | `BRIDGE_ADMIN_PASSWORD` | wachtwoord voor de beheer-UI |
   | `BRIDGE_SECRET_KEY` | lange willekeurige tekst (sessies en tokenhashes); **niet meer wijzigen**, anders worden alle tokens ongeldig. Leeg = de app maakt er zelf één in `/data/db/.secret_key` |
   | `DATABASE_URL` | leeg laten voor SQLite in `/data` |
   | `BRIDGE_RATE_LIMIT_PER_MINUTE` | optioneel, standaard `120` verzoeken per token per minuut |
   | `SMB_*`, `NOTIFY_WEBHOOK_URL` | pas vanaf slice 4 |

6. Netwerk: containerpoort **8000** naar een vrije poort op de TrueNAS. Alleen intern netwerk; niet achter
   de Cloudflare Tunnel zetten.
7. Opslag: **Host Path** = de dataset, in de container als **`/data`**. Daarin komen `db/`, `raw/`,
   `staging/` en `backups/`.
8. Installeren en openen via `http://<truenas>:<poort>/` → inloggen met `BRIDGE_ADMIN_PASSWORD`.

**Bijwerken:** de tag aanpassen. Databasemigraties draaien automatisch bij het starten; vóór elke migratie
maakt de app een kopie van de database in `/data/backups`.

**Back-up:** dagelijkse ZFS-snapshots van de dataset plus de bestaande back-uproutine.

De veldnamen in het formulier kunnen per TrueNAS-versie iets verschillen.

## Tokens voor afnemende apps (opstartcommando)

Eén token per afnemende app, alleen bij aanmaak zichtbaar. Op TrueNAS: **Apps → priva-bridge → Shell**
(of `docker exec -it <container> sh`), dan:

```sh
bridge token aanmaken --naam gewichtsapp --scope read:data
bridge token lijst
bridge token intrekken 3
```

Scopes: `read:data` (alle GET-endpoints) en `admin:imports` (upload/herimport via de API, vanaf slice 3).

Koppelsleutels voor het Bedrijfsdashboard: zie [docs/koppeling-api.md](docs/koppeling-api.md)
(`bridge koppeling aan|uit|aanmaken|lijst|intrekken`, of in de beheer-UI onder *Bedrijfskoppeling*).

## Werkwijze: weekexport uploaden

1. Exporteer in Priva-rapportage de rapporten **Klimaat** en **Belichting** als CSV.
2. Beheer-UI → **Upload** → bestand kiezen. De Bridge herkent het rapport aan de inhoud en toont een preview:
   herkend rapport, periode, aantal regels, lege velden, echte nullen, overgeslagen kolommen, bronnen en
   meldingen.
3. Klik **Verwerken**. Hetzelfde bestand nogmaals uploaden doet niets (zelfde SHA-256); overlappende
   bestanden geven geen dubbele waarden. Een afwijkende waarde wordt een nieuwe revisie, de oude blijft.
4. Een onbekende kolom of rapportvariant **stopt** de import van dat bestand met een melding; er wordt dan
   niets opgeslagen. Breid in dat geval `catalog/metrics.yaml` uit (geen codewijziging nodig).

Kranen, Algemeen Bedrijf en Algemeen Afdeling worden herkend maar pas vanaf slice 2 geïmporteerd.

## API (`/api/v1`)

Specificatie: `GET /api/v1/openapi.json` (vastgelegd in [`openapi/openapi.json`](openapi/openapi.json)),
interactief op `/api/v1/docs`.

```sh
curl -H "Authorization: Bearer $TOKEN" \
  "http://truenas:8000/api/v1/daily?scope=kap:12&scope=afdeling:1&from=2026-11-09&to=2026-11-15&metric=lamp_brandseconden&metric=temp_etmaal_avg&inherit=true"
```

| Endpoint | Betekenis |
|---|---|
| `GET /health` | Leeft de app (zonder token) |
| `GET /api/v1/status` | Laatste import, laatste complete dag, ontbrekende dagen, waarschuwingen |
| `GET /api/v1/metrics` | Catalogus met eenheid, niveau en aggregatie |
| `GET /api/v1/daily` | Dagwaarden met `coverage`, `quality`, `resolved_from`, `sources`, `import_id`, `revision` |

- Scopes: `bedrijf`, `afdeling:1`–`4`, `kap:1`–`40`, `bron:<type>:<nr>` (module, kraan, doek,
  doseerruimte, verwarming, groeilicht). Meerdere `scope`/`metric` als herhaalde parameter.
- Elke gevraagde dag komt terug; zonder data met `quality: "ontbreekt"` en `value: null` (nooit 0).
- `limit` standaard 500, maximaal 5000; vervolg met `next_cursor`.
- Fouten als `application/problem+json` (400, 401, 403, 404, 422, 429 met `Retry-After`).

## Ontwikkelen

```sh
python3.12 -m venv .venv && . .venv/bin/activate      # Python 3.12 of 3.13
pip install -r requirements-dev.txt && pip install -e . --no-deps
pytest                       # tests (synthetische fixtures; echte exports in tests/fixtures/echt/ als aanwezig)
ruff check . && ruff format --check .
DATA_DIR=./data BRIDGE_ADMIN_PASSWORD=dev bridge serve --port 8000
bridge openapi --schrijf openapi/openapi.json   # alleen na een bewuste, niet-brekende API-wijziging
```

Mappen: `bridge/` (app), `catalog/` (meetwaardencatalogus en standaardmapping), `openapi/`,
`tests/fixtures/synthetic/` (synthetische testbestanden), `docs/`.

Images: elke push naar een `ccr-*`-branch bouwt `ghcr.io/lvos04/priva-bridge:<versie>-rc`, `branch-…` en
`sha-…` (nooit `latest`); de standaardbranch en tags `v*` bouwen `<versie>` en `latest`.
