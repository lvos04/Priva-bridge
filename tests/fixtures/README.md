# Testbestanden

## `synthetic/` — SYNTHETISCH, in git

Verzonnen Priva-exports, gemaakt met [`synthetic/genereer.py`](synthetic/genereer.py). Ze volgen exact het
formaat van de echte Priva Office Direct-rapportage-export (kopregel
`label;pcu;type_1;idx_1;type_2;idx_2;startdate;enddate;value`, puntkomma, `d-m-jj`, decimale komma, lege
velden, echte nullen) met een deelverzameling van de echte labels en objecttypes. Alle getallen zijn
verzonnen; de bestandsnamen beginnen met `SYNTHETISCH_` en de kolom `pcu` bevat `SYNTHETISCH`.

| Bestand | Inhoud |
|---|---|
| `SYNTHETISCH_klimaat_2026-10-19_2026-10-25.csv` | Klimaat, afdeling 1, UTF-8. Etmaaltemperatuur op 25-10 leeg (→ `ontbreekt`), schermdoek 2 echt 0, bekende lege kolommen en CompCO2 = 0 (→ overgeslagen), autonoom doek, CO2-doseerruimte, autonome verwarming. |
| `SYNTHETISCH_belichting_2026-10-19_2026-10-25_cp1252.csv` | Belichting, module 101–140, kanaal 1 en 2, **Windows-1252** (en-dash in `pcu`). Module 105 kanaal 1 op 22-10 leeg, module 106 kanaal 2 echt 0, op 25-10 (25-uursdag) meer dan 24 uur brandtijd, groeilicht sturing 1–3. |

Opnieuw maken: `python tests/fixtures/synthetic/genereer.py`.

## `echt/` — de echte exports van Leon, NIET in git

De vijf echte CSV's (Klimaat, Belichting, Kranen, Algemeen Bedrijf, Algemeen Afdeling) horen lokaal in
**`tests/fixtures/echt/`**. Die map staat in `.gitignore`: de repository is publiek en het zijn
bedrijfsgegevens. Zet ze er alleen neer op een eigen machine.

Zijn ze aanwezig, dan draaien automatisch de tests in [`tests/test_echte_fixtures.py`](../test_echte_fixtures.py);
anders (zoals in CI) slaan ze over:

- `test_alle_vijf_families_op_inhoud_herkend` — alle vijf rapportfamilies worden op inhoud herkend.
- `test_klimaat_en_belichting_importeren_zonder_handwerk_en_rest_stopt_netjes` — Klimaat en Belichting
  importeren zonder fouten; Kranen en de twee Algemeen-rapporten stoppen met de melding "pas vanaf slice 2".
- `test_driemaal_echte_bestanden_geen_dubbele_records` — driemaal uploaden, gelijk aantal observaties.
- `test_tien_steekproefwaarden_daily_gelijk_aan_rapport` — tien steekproefwaarden (vijf per rapport) uit het
  ruwe bestand, onafhankelijk ingelezen, zijn gelijk aan `/api/v1/daily`.
- `test_steekproef_van_leon` — alleen als `echt/steekproef.csv` bestaat: minstens tien waarden die Leon
  rechtstreeks uit het Priva-scherm of de PDF overneemt, met kopregel `datum;scope;metric;waarde`, bijvoorbeeld

  ```
  datum;scope;metric;waarde
  2026-11-10;afdeling:1;temp_etmaal_avg;21,4
  2025-11-12;bron:module:112;lamp_brandseconden;43140
  ```

  Dit is de eigenlijke "tien steekproefwaarden gelijk aan het Priva-rapport" uit slice 1.
