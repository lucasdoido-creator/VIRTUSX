"""SOMENTE PARA TESTES OFFLINE: grava um mapa simplificado (contornos aproximados) em DATA_DIR/geo.
Em uso normal o sistema baixa o mapa real do Natural Earth na primeira vez."""
import json, sys
from pathlib import Path

d = Path(sys.argv[1]) / "geo"
(d / "states").mkdir(parents=True, exist_ok=True)
def box(w, s, e, n): return [w, s, e, s, e, n, w, n]
countries = [
    {"id": "076", "name": "Brazil", "name_pt": "Brasil", "rings": [[-73, -8, -60, 5, -50, 4, -35, -5, -39, -15, -48, -26, -53, -34, -58, -30, -62, -20, -70, -11]]},
    {"id": "620", "name": "Portugal", "name_pt": "Portugal", "rings": [box(-9.5, 37, -6.2, 42)]},
    {"id": "840", "name": "United States of America", "name_pt": "Estados Unidos", "rings": [box(-124, 25, -67, 49)]},
    {"id": "032", "name": "Argentina", "name_pt": "Argentina", "rings": [[-73, -55, -65, -55, -58, -38, -53, -27, -62, -22, -68, -22, -70, -38]]},
    {"id": "724", "name": "Spain", "name_pt": "Espanha", "rings": [box(-6.2, 36, 3.3, 43.7)]},
]
for c in countries:
    r = c["rings"][0]; xs, ys = r[0::2], r[1::2]; c["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
(d / "countries.json").write_text(json.dumps({"available": True, "source": "FIXTURE DE TESTE", "countries": countries}))
states = {"Brazil": [("São Paulo", "SP", box(-53, -25.3, -44.2, -19.8)), ("Minas Gerais", "MG", box(-51, -22.9, -39.9, -14.2)),
                     ("Paraná", "PR", box(-54.6, -26.7, -48, -22.5)), ("Rio de Janeiro", "RJ", box(-44.9, -23.4, -41, -20.8))],
          "Portugal": [("Lisboa", "LI", box(-9.5, 38.6, -8.8, 39.3))], "United States of America": [("Texas", "TX", box(-106, 26, -93.5, 36.5))]}
slug = {"Brazil": "brazil", "Portugal": "portugal", "United States of America": "united_states_of_america"}
for country, sts in states.items():
    out = [{"name": n, "code": c, "type": "Estado", "rings": [r], "bbox": [min(r[0::2]), min(r[1::2]), max(r[0::2]), max(r[1::2])]} for n, c, r in sts]
    (d / "states" / f"{slug[country]}.json").write_text(json.dumps({"country": country, "states": out}))
(d / "states" / "_pronto").write_text("fixture")
print("fixture de mapa gravada em", d)
