"""Genereert de SYNTHETISCHE Priva-fixtures in deze map. Geen bedrijfsgegevens.

Het formaat volgt exact de echte Priva Office Direct-rapportage-export (kopregel, puntkomma,
d-m-jj, decimale komma, lege velden, echte nullen); de labels en objecttypes zijn een deelverzameling
van wat in de echte Klimaat- en Belichtingsexports staat. Alle getallen zijn verzonnen.

Periode: 19 t/m 25 oktober 2026; 25 oktober is een dag van 25 uur (wintertijd).

    python tests/fixtures/synthetic/genereer.py
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
HEADER = "label;pcu;type_1;idx_1;type_2;idx_2;startdate;enddate;value"
DAYS = [date(2026, 10, 19) + timedelta(days=i) for i in range(7)]


def d(day: date) -> str:
    return f"{day.day}-{day.month}-{day.year % 100:02d}"


def num(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def row(label, pcu, type_1, idx_1, type_2, idx_2, day, value) -> str:
    return ";".join([label, pcu, type_1, str(idx_1), type_2, str(idx_2), d(day), d(day), value])


def klimaat() -> str:
    pcu = "SYNTHETISCH"
    lines = [HEADER]
    for i, day in enumerate(DAYS):
        last = day == DAYS[-1]
        temp = 19.5 + i * 0.25  # 19,5 19,75 20 ...
        values = {
            "Ave_24h_CompTemp": "" if last else num(temp),  # laatste dag leeg: ontbreekt, geen 0
            "Min_24h_CompTemp": "" if last else num(temp - 2.4),
            "Max_24h_CompTemp": "" if last else num(temp + 4.1),
            "Ave_Day_CompTemp": num(temp + 1),
            "Ave_Night_CompTemp": num(temp - 1),
            "Ave_24h_CompRV": str(70 + i),
            "Ave_24h_CompHD": num(3.5 + i / 10),
            "Ave_24h_CompAV": num(12.25 + i / 10),
            "Ave_24h_DewPointDifference": num(3.75),
            "Sum_24h_PAR_Growlight": num(3.35 + i / 100),
            "Sum_24h_CalculatedRadiation": str(500 + i * 10),
        }
        for label, value in values.items():
            lines.append(row(label, pcu, "Afdeling", 1, "", "", day, value))
        # Bekende lege kolommen en nepnullen: herkend en overgeslagen, nooit als 0 opgeslagen.
        lines.append(row("Sum_24h_CalculatedPAR", pcu, "Afdeling", 1, "", "", day, ""))
        lines.append(row("Sum_24h_MeasuredPAR", pcu, "Afdeling", 1, "", "", day, "0"))
        lines.append(row("Ave_24h_CompCO2", pcu, "Afdeling", 1, "", "", day, "0"))
        # Schermdoeken: doek 2 is echt 0 seconden dicht geweest.
        lines.append(row("Sum_24h_CurtainHours", pcu, "Afdeling", 1, "Doek", 1, day, str(60000 + i * 60)))
        lines.append(row("Sum_24h_CurtainHours", pcu, "Afdeling", 1, "Doek", 2, day, "0"))
        for doek in (1, 2):
            lines.append(row("Sum_24h_AutonomousCurtainHours", pcu, "Autonoom doek", doek, "Doek", 1, day, str(70000 + doek)))
        lines.append(row("Sum_24h_CO2Prog", pcu, "CO2 doseerruimte", 1, "", "", day, "0" if i % 2 else "12,5"))
        lines.append(row("Ave_24h_WaterTemp_Autonomous", pcu, "Autonome verwarming", 1, "", "", day, "33"))
    return "\n".join(lines) + "\n"


def belichting() -> str:
    pcu = "SYNTHETISCH – testdata"  # en-dash: alleen in Windows-1252 als 0x96
    lines = [HEADER]
    for module in range(101, 141):
        for i, day in enumerate(DAYS):
            for kanaal in (1, 2):
                if kanaal == 1:
                    seconds = 36000 + (module - 100) * 60 + i * 10
                    if day == date(2026, 10, 25):
                        seconds = 88200 + module  # meer dan 24 uur kan alleen op een 25-uursdag
                    stand, par = 50 + (module % 5), 90 + (module % 7)
                else:
                    seconds, stand, par = (0, 100, 0) if module == 106 else (19500 + i * 60, 100, 0)
                blank = module == 105 and kanaal == 1 and day == date(2026, 10, 22)
                values = {
                    "Sum_24h_ModGrowlightSysHours": "" if blank else str(seconds),
                    "Ave_24h_ModGrowlightPosition": "" if blank else str(stand),
                    "Ave_24h_ModGrowlightPAR": "" if blank else str(par),
                    "Ave_24h_ModGrowlightRadiation": "" if blank else str(80 + kanaal),
                    "Sum_24h_ModGrowlightElectricalEnergy": num(100 + module / 10 + kanaal / 4),
                }
                for label, value in values.items():
                    lines.append(row(label, pcu, "modgroeilichtsturing", module, "kanaal", kanaal, day, value))
    for groep in (1, 2, 3):
        for day in DAYS:
            lines.append(row("Sum_24h_GrowlightSysHours", pcu, "Groeilicht sturing", groep, "Streng", 1, day,
                             "0" if groep != 2 else "43140"))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    (HERE / "SYNTHETISCH_klimaat_2026-10-19_2026-10-25.csv").write_bytes(klimaat().encode("utf-8"))
    (HERE / "SYNTHETISCH_belichting_2026-10-19_2026-10-25_cp1252.csv").write_bytes(belichting().encode("cp1252"))
    print("Synthetische fixtures geschreven.")
