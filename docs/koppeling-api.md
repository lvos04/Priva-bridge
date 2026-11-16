# Bedrijfskoppeling API v1 — Priva Bridge

Alleen-lezen koppeling zodat het **Bedrijfsdashboard** (en andere bedrijfsapps) server-naar-server de
actualiteit van de Priva Bridge kan tonen. Gedeeld contract voor alle bedrijfsapps; hieronder de
app-specifieke invulling.

- App-slug: **`priva-bridge`**
- Pad-prefix: **`/api/koppeling/v1`** (naast de consumenten-API `/api/v1`)
- Alleen `GET`, JSON, UTF-8. Tijden ISO 8601 in UTC (`Z`); datums `YYYY-MM-DD`. Ontbrekend = `null`, nooit 0.

## Aanzetten en sleutels

De schakelaar **"Bedrijfskoppeling ingeschakeld"** staat standaard **uit**. Dan antwoordt elke route
`403 {"fout": "koppeling_uitgeschakeld"}`, ook met een geldige sleutel.

**Keuze:** koppelsleutels staan **los van de `/api/v1`-tokens** (eigen tabel `koppelsleutel`). Dat is het
eenvoudigst veilig: een dashboardsleutel kan nooit de Priva-dagwaarden van `/api/v1` lezen, en een
`/api/v1`-token werkt niet op de koppeling (getest).

- Sleutel: `bk_` + 32 bytes cryptografisch willekeurig (urlsafe base64). Alleen de **SHA-256-hash** en de
  eerste 8 tekens (`voorvoegsel`) worden opgeslagen; de sleutel is alleen bij aanmaak zichtbaar.
- Velden: id, naam, voorvoegsel, hash, aangemaakt_op, aangemaakt_door (`beheerder` of `cli`),
  laatst_gebruikt_op (per minuut bijgewerkt), ingetrokken_op.
- Rate limit: 120 verzoeken per minuut per sleutel → `429 {"fout": "te_veel_verzoeken"}` met `Retry-After`.

Beheer alleen door de beheerder:

- **Beheer-UI** → *Bedrijfskoppeling*: schakelaar aan/uit, sleutel aanmaken (eenmalig getoond,
  `Cache-Control: no-store`), lijst (nooit hash of sleutel), intrekken. Sessie-login met
  `BRIDGE_ADMIN_PASSWORD` en CSRF-token op elk formulier.
- **Opstartcommando** (TrueNAS: Apps → priva-bridge → Shell):

  ```sh
  bridge koppeling aan                      # of: uit
  bridge koppeling aanmaken --naam Bedrijfsdashboard
  bridge koppeling lijst
  bridge koppeling intrekken 1
  ```

Er is geen sleutelbeheer via een JSON-API: de Bridge heeft geen gebruikersbeheer of beheer-API, alleen de
server-side beheer-UI (sectie 9 van het plan). De UI-formulieren zijn de beheer-endpoints:
`GET /beheer/koppeling`, `POST /beheer/koppeling/sleutels` (`naam`), `POST /beheer/koppeling/sleutels/{id}/intrekken`,
`POST /beheer/koppeling/schakelaar` (`aan=1|0`). Zonder beheerderssessie → doorverwijzing naar de login, er
verandert niets.

Fouten:

| Situatie | Antwoord |
|---|---|
| Schakelaar uit | `403 {"fout": "koppeling_uitgeschakeld"}` |
| Geen, onbekende of ingetrokken sleutel | `401 {"fout": "ongeldige_koppelsleutel"}` |
| Onbekende dataset | `404 {"fout": "onbekende_dataset"}` |
| Ongeldige parameters | `422 {"fout": "ongeldige_parameters", "detail": "…"}` |
| Te veel verzoeken | `429 {"fout": "te_veel_verzoeken"}` + `Retry-After` |

## `GET /api/koppeling/v1/info`

```json
{"app": "priva-bridge", "naam": "Priva Bridge", "versie": "0.1.0", "api_versie": "1",
 "bedrijf": null, "datasets": ["imports"], "tijd": "2026-11-16T15:00:00Z"}
```

## `GET /api/koppeling/v1/samenvatting`

KPI's uit `/api/v1/status`:

| sleutel | waarde | eenheid | status |
|---|---|---|---|
| `laatste_import` | uren sinds de laatste verwerkte import | `uur geleden` | ok ≤ 8 dagen, let_op ≤ 14 dagen, anders actie; `onbekend` zonder import |
| `laatste_complete_dag` | dagen tussen gisteren en de laatste complete dag | `dagen` | ok ≤ 7, let_op ≤ 14, anders actie; `onbekend` zonder complete dag |
| `ontbrekende_dagen` | niet-complete dagen in de laatste 30 dagen (t/m gisteren) | `dagen` | ok ≤ 7, let_op ≤ 14, anders actie |
| `waarschuwingen` | aantal waarschuwingen in `/api/v1/status` | – | ok bij 0, anders let_op |

De drempels passen bij de wekelijkse handmatige upload. "Compleet" = Klimaat voor de verwachte afdelingen
én Belichting voor de verwachte lichtmodules (zie `catalog/mapping.yaml`).

`meldingen`: elke waarschuwing uit `/api/v1/status` als `{"niveau": "waarschuwing", "tekst", "sinds"}`
(`sinds` = begin van de dag waarop de melding ontstond, of `null`).

## `GET /api/koppeling/v1/datasets/imports?sinds=<ISO>&limiet=<1..500, standaard 100>&cursor=<opaak>`

Importbatches, gesorteerd op id (stabiel):

```json
{"app": "priva-bridge", "dataset": "imports", "gegenereerd_op": "2026-11-16T15:00:00Z",
 "items": [{"id": "imp_0001", "rapportfamilie": "klimaat", "status": "verwerkt",
            "periode_van": "2026-11-09", "periode_tot": "2026-11-15", "aantal_waarden": 581,
            "ontvangen_op": "2026-11-16T14:02:11Z", "bijgewerkt_op": "2026-11-16T14:02:15Z"}],
 "volgende_cursor": null}
```

- `sinds` filtert op `bijgewerkt_op` (ontvangen of verwerkt). `status`: `preview`, `verwerkt`, `geweigerd`.
- Privacy: geen bestandsnamen (die kunnen bedrijfs- of persoonsnamen bevatten), geen tokens of sleutels.
