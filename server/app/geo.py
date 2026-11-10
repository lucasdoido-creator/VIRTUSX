"""Geografia real: países, estados/províncias e coordenadas de cidades.

Fontes gratuitas (domínio público, Natural Earth), baixadas UMA vez e guardadas em data/geo:
- Países:  world-atlas (TopoJSON, Natural Earth 1:50m)   https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json
- Estados: Natural Earth admin-1 1:10m (GeoJSON)          raw.githubusercontent.com/nvkelso/natural-earth-vector/...
Coordenadas de cidades: tabela embutida (funciona offline) → cache → Nominatim/OpenStreetMap (grátis, 1 req/s).
Posição por cidade é sempre marcada como APROXIMADA; só endereço confirmado conta como localização confirmada.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from pathlib import Path

from .core import R
from .db import iso

COUNTRIES_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json"
ADMIN1_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_1_states_provinces.geojson"
TRANSPORT = {"get": None, "geocode": None}  # injeção para testes
_state = {"admin1": "idle", "admin1_error": None, "countries_error": None}
_lock = threading.Lock()

# Nomes Natural Earth das regiões operadas
REGION_COUNTRY = {"BR": "Brazil", "PT": "Portugal", "US": "United States of America"}
COUNTRY_PT = {"Brazil": "Brasil", "Portugal": "Portugal", "United States of America": "Estados Unidos", "Argentina": "Argentina",
              "Spain": "Espanha", "France": "França", "Germany": "Alemanha", "Italy": "Itália", "United Kingdom": "Reino Unido",
              "Mexico": "México", "Canada": "Canadá", "Chile": "Chile", "Uruguay": "Uruguai", "Paraguay": "Paraguai", "Peru": "Peru",
              "Colombia": "Colômbia", "Bolivia": "Bolívia", "Venezuela": "Venezuela", "China": "China", "Japan": "Japão",
              "India": "Índia", "Russia": "Rússia", "Australia": "Austrália", "South Africa": "África do Sul", "Angola": "Angola",
              "Mozambique": "Moçambique", "Netherlands": "Países Baixos", "Belgium": "Bélgica", "Switzerland": "Suíça"}

# (lat, lng) — sedes municipais; suficientes para posicionar no mapa sem internet
GAZETTEER = {
    "sao paulo|sp": (-23.5505, -46.6333), "campinas|sp": (-22.9056, -47.0608), "santos|sp": (-23.9608, -46.3336),
    "guarulhos|sp": (-23.4538, -46.5333), "osasco|sp": (-23.5325, -46.7917), "santo andre|sp": (-23.6639, -46.5383),
    "sao bernardo do campo|sp": (-23.6914, -46.5646), "sao caetano do sul|sp": (-23.6229, -46.5548), "diadema|sp": (-23.6861, -46.6228),
    "maua|sp": (-23.6677, -46.4613), "barueri|sp": (-23.5107, -46.8761), "carapicuiba|sp": (-23.5235, -46.8407),
    "cotia|sp": (-23.6022, -46.9190), "taboao da serra|sp": (-23.6261, -46.7917), "mogi das cruzes|sp": (-23.5229, -46.1854),
    "jundiai|sp": (-23.1857, -46.8978), "sorocaba|sp": (-23.5015, -47.4526), "sao jose dos campos|sp": (-23.1791, -45.8872),
    "taubate|sp": (-23.0204, -45.5558), "ribeirao preto|sp": (-21.1704, -47.8103), "piracicaba|sp": (-22.7253, -47.6492),
    "bauru|sp": (-22.3246, -49.0871), "sao jose do rio preto|sp": (-20.8113, -49.3758), "limeira|sp": (-22.5643, -47.4017),
    "americana|sp": (-22.7374, -47.3331), "indaiatuba|sp": (-23.0816, -47.2101), "sumare|sp": (-22.8219, -47.2669),
    "hortolandia|sp": (-22.8529, -47.2143), "valinhos|sp": (-22.9698, -46.9974), "vinhedo|sp": (-23.0302, -46.9833),
    "atibaia|sp": (-23.1171, -46.5563), "braganca paulista|sp": (-22.9527, -46.5419), "itu|sp": (-23.2636, -47.2992),
    "praia grande|sp": (-24.0058, -46.4028), "sao vicente|sp": (-23.9631, -46.3919), "guaruja|sp": (-23.9935, -46.2564),
    "cajamar|sp": (-23.3561, -46.8769), "franca|sp": (-20.5386, -47.4009), "marilia|sp": (-22.2139, -49.9458),
    "presidente prudente|sp": (-22.1207, -51.3925), "aracatuba|sp": (-21.2089, -50.4328), "araraquara|sp": (-21.7845, -48.1780),
    "sao carlos|sp": (-22.0174, -47.8909), "rio claro|sp": (-22.4149, -47.5651), "jacarei|sp": (-23.3051, -45.9658),
    "suzano|sp": (-23.5428, -46.3108), "itaquaquecetuba|sp": (-23.4864, -46.3484), "embu das artes|sp": (-23.6489, -46.8522),
    "itapevi|sp": (-23.5488, -46.9327), "santana de parnaiba|sp": (-23.4439, -46.9178), "paulinia|sp": (-22.7542, -47.1488),
    "rio de janeiro|rj": (-22.9068, -43.1729), "niteroi|rj": (-22.8832, -43.1034), "belo horizonte|mg": (-19.9167, -43.9345),
    "uberlandia|mg": (-18.9186, -48.2772), "curitiba|pr": (-25.4284, -49.2733), "londrina|pr": (-23.3045, -51.1696),
    "porto alegre|rs": (-30.0346, -51.2177), "florianopolis|sc": (-27.5949, -48.5482), "joinville|sc": (-26.3045, -48.8487),
    "salvador|ba": (-12.9777, -38.5016), "recife|pe": (-8.0476, -34.8770), "fortaleza|ce": (-3.7319, -38.5267),
    "brasilia|df": (-15.7939, -47.8828), "goiania|go": (-16.6869, -49.2648), "manaus|am": (-3.1190, -60.0217),
    "belem|pa": (-1.4558, -48.4902), "vitoria|es": (-20.3155, -40.3128), "campo grande|ms": (-20.4697, -54.6201),
    "cuiaba|mt": (-15.6014, -56.0979), "natal|rn": (-5.7945, -35.2110), "joao pessoa|pb": (-7.1195, -34.8450),
    "maceio|al": (-9.6658, -35.7353), "aracaju|se": (-10.9472, -37.0731), "teresina|pi": (-5.0920, -42.8038),
    "sao luis|ma": (-2.5307, -44.3068), "palmas|to": (-10.1840, -48.3336), "porto velho|ro": (-8.7612, -63.9004),
    "rio branco|ac": (-9.9754, -67.8249), "macapa|ap": (0.0349, -51.0694), "boa vista|rr": (2.8235, -60.6758),
    "lisboa|lisboa": (38.7223, -9.1393), "lisboa|": (38.7223, -9.1393), "porto|": (41.1579, -8.6291), "azambuja|lisboa": (39.0703, -8.8681),
    "austin|tx": (30.2672, -97.7431), "edison|nj": (40.5187, -74.4121), "new york|ny": (40.7128, -74.0060), "miami|fl": (25.7617, -80.1918),
}
COUNTRY_CENTER = {"BR": (-14.2, -51.9), "PT": (39.5, -8.0), "US": (39.8, -98.6)}
HQ_DEFAULT = (-23.5505, -46.6333)  # São Paulo


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").strip().lower())
    return re.sub(r"\s+", " ", "".join(ch for ch in s if not unicodedata.combining(ch)))


def _geo_dir() -> Path:
    d = R.settings.data_dir / "geo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url, timeout=120):
    if TRANSPORT["get"]:
        return TRANSPORT["get"](url)
    import httpx
    r = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"User-Agent": "VIRTUSX/1.0"})
    r.raise_for_status()
    return r.content


# ------------------------------------------------------------------ TopoJSON → anéis [lon,lat]
def topo_to_countries(topo):
    tr = topo.get("transform")
    arcs = []
    for arc in topo["arcs"]:
        pts, x, y = [], 0, 0
        for p in arc:
            if tr:
                x, y = x + p[0], y + p[1]
                pts.append((x * tr["scale"][0] + tr["translate"][0], y * tr["scale"][1] + tr["translate"][1]))
            else:
                pts.append((p[0], p[1]))
        arcs.append(pts)

    def ring(idx):
        out = []
        for i in idx:
            a = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
            out.extend(a if not out else a[1:])
        return out

    res = []
    for g in topo["objects"]["countries"]["geometries"]:
        polys = []
        if g.get("type") == "Polygon":
            polys = [g["arcs"]]
        elif g.get("type") == "MultiPolygon":
            polys = g["arcs"]
        rings = [ring(r) for p in polys for r in p]
        if rings:
            res.append({"id": str(g.get("id", "")), "name": (g.get("properties") or {}).get("name", ""), "rings": rings})
    return res


def _simplify(pts, tol):
    """Douglas–Peucker iterativo."""
    if len(pts) < 4:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) or 1e-12
        best, bi = 0.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
            d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, bi = d, i
        if bi >= 0 and best > tol * tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def _pack(rings, tol):
    out = []
    for r in rings:
        s = _simplify(r, tol)
        if len(s) >= 3:
            out.append([round(v, 3) for p in s for v in p])
    return out


def _bbox(packed):
    xs = [r[i] for r in packed for i in range(0, len(r), 2)]
    ys = [r[i] for r in packed for i in range(1, len(r), 2)]
    return [min(xs), min(ys), max(xs), max(ys)] if xs else [0, 0, 0, 0]


def countries():
    """{available, countries:[{id,name,name_pt,bbox,rings:[[lon,lat,...]]}], error}"""
    f = _geo_dir() / "countries.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        topo = json.loads(_get(COUNTRIES_URL))
        res = []
        for c in topo_to_countries(topo):
            rings = _pack(c["rings"], 0.02)
            if rings:
                res.append({"id": c["id"], "name": c["name"], "name_pt": COUNTRY_PT.get(c["name"], c["name"]), "bbox": _bbox(rings), "rings": rings})
        data = {"available": True, "source": "Natural Earth 1:50m via world-atlas", "countries": res, "fetched_at": iso()}
        f.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        _state["countries_error"] = None
        return data
    except Exception as e:
        _state["countries_error"] = f"{type(e).__name__}: {e}"[:200]
        return {"available": False, "countries": [], "error": "Mapa de países ainda não baixado (sem internet no servidor?)."}


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", norm(name)).strip("_") or "x"


def _build_admin1(raw: bytes):
    data = json.loads(raw)
    groups = {}
    for ft in data["features"]:
        pr = ft.get("properties") or {}
        g = ft.get("geometry") or {}
        polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else []
        rings = _pack([[tuple(p[:2]) for p in ring] for poly in polys for ring in poly[:1]], 0.01)
        if not rings:
            continue
        groups.setdefault(pr.get("admin") or "?", []).append({
            "name": pr.get("name") or pr.get("name_en") or "?", "code": (pr.get("postal") or pr.get("iso_3166_2") or "")[:10],
            "type": pr.get("type_en") or pr.get("type") or "", "bbox": _bbox(rings), "rings": rings})
    d = _geo_dir() / "states"
    d.mkdir(exist_ok=True)
    for country, states in groups.items():
        (d / f"{_slug(country)}.json").write_text(json.dumps({"country": country, "states": states}, separators=(",", ":")), encoding="utf-8")
    (d / "_pronto").write_text(iso())
    return len(groups)


def _admin1_job():
    try:
        _state["admin1"] = "baixando"
        raw = _get(ADMIN1_URL, timeout=600)
        _state["admin1"] = "processando"
        _build_admin1(raw)
        _state["admin1"], _state["admin1_error"] = "pronto", None
    except Exception as e:
        _state["admin1"], _state["admin1_error"] = "erro", f"{type(e).__name__}: {e}"[:200]


def states(country_name, wait=False):
    d = _geo_dir() / "states"
    if (d / "_pronto").exists():
        f = d / f"{_slug(country_name)}.json"
        if f.exists():
            return {"status": "pronto", **json.loads(f.read_text(encoding="utf-8"))}
        return {"status": "pronto", "country": country_name, "states": []}
    with _lock:
        if _state["admin1"] in ("idle", "erro"):
            if wait or TRANSPORT["get"]:
                _admin1_job()
            else:
                threading.Thread(target=_admin1_job, daemon=True).start()
                time.sleep(0.05)
    if _state["admin1"] == "pronto":
        return states(country_name)
    return {"status": _state["admin1"], "error": _state["admin1_error"], "country": country_name, "states": [],
            "note": "Baixando estados de todos os países (uma única vez, ~40 MB)."}


# ------------------------------------------------------------------ cidades → coordenadas
def _gaz(city, state):
    c, s = norm(city), norm(state)
    return GAZETTEER.get(f"{c}|{s}") or GAZETTEER.get(f"{c}|")


def geocode(city, state=None, country="BR"):
    """(lat, lng, fonte) ou None. Ordem: tabela embutida → cache → Nominatim (online)."""
    if not city:
        return None
    hit = _gaz(city, state)
    if hit:
        return (*hit, "cidade (tabela embutida)")
    key = f"{norm(city)}|{norm(state)}|{country}"
    row = R.db.one("SELECT payload FROM research_cache WHERE key=?", "geo:" + key)
    if row:
        v = json.loads(row["payload"])
        return (v[0], v[1], v[2]) if v else None
    try:
        if TRANSPORT["geocode"]:
            res = TRANSPORT["geocode"](city, state, country)
        else:
            if not R.settings.research_enabled:
                return None
            import httpx
            time.sleep(1.1)  # política de uso do Nominatim: no máximo 1 requisição por segundo
            r = httpx.get("https://nominatim.openstreetmap.org/search", timeout=20,
                          params={"city": city, "state": state or "", "country": country, "format": "json", "limit": 1},
                          headers={"User-Agent": "VIRTUSX/1.0 (operacao interna)"})
            r.raise_for_status()
            j = r.json()
            res = (float(j[0]["lat"]), float(j[0]["lon"])) if j else None
    except Exception:
        return None  # sem rede: tenta de novo depois, sem gravar cache negativo
    val = [res[0], res[1], "cidade (OpenStreetMap)"] if res else None
    with R.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO research_cache(key,fetched_at,payload) VALUES (?,?,?)", ("geo:" + key, iso(), json.dumps(val)))
    return tuple(val) if val else None


def geocode_missing(limit=5):
    """Agendador: preenche coordenadas que faltam (unidades, bases, armazéns)."""
    done = 0
    for table, ccol in (("partner_units", "country"), ("service_bases", None), ("warehouses", None)):
        rows = R.db.all(f"SELECT * FROM {table} WHERE lat IS NULL AND city IS NOT NULL LIMIT ?", limit)
        for r in rows:
            country = r.get(ccol) if ccol else (R.db.val("SELECT region_code FROM warehouses WHERE id=?", r["id"]) if table == "warehouses" else "BR")
            g = geocode(r["city"], r.get("state"), country or "BR")
            if g:
                with R.db.tx() as c:
                    c.execute(f"UPDATE {table} SET lat=?, lng=?, geo_source=? WHERE id=?", (g[0], g[1], g[2], r["id"]))
                done += 1
    return done


def status():
    d = _geo_dir()
    return {"countries": (d / "countries.json").exists(), "states": (d / "states" / "_pronto").exists(),
            "states_job": _state["admin1"], "error": _state["admin1_error"] or _state["countries_error"]}


# ------------------------------------------------------------------ imagens reais do terreno
# Satélite: NASA GIBS (Blue Marble, domínio público) em projeção geográfica — encaixa direto no mapa equiretangular.
# Ruas (zoom de cidade): OpenStreetMap, com cache em disco, User-Agent identificado e atribuição na tela
# (política de uso dos tiles OSM: sem download em massa; aqui só os tiles da área aberta, uma vez).
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
GIBS_LAYERS = ("BlueMarble_ShadedRelief_Bathymetry", "BlueMarble_NextGeneration")
OSM_TILE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
UA = "VIRTUSX/1.0 (+https://github.com/lucasdoido-creator/VIRTUSX)"
_tile_lock = threading.Lock()
_tile_last = [0.0]
_img_state = {"error": None}


def _get_img(url, timeout=90):
    if TRANSPORT["get"]:
        return TRANSPORT["get"](url)
    import httpx
    r = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.content


def _is_jpeg(b): return len(b) > 1000 and b[:3] == b"\xff\xd8\xff"
def _is_png(b): return len(b) > 60 and b[:8] == b"\x89PNG\r\n\x1a\n"


def _wms(bbox, w, h):
    last = None
    for layer in GIBS_LAYERS:
        url = (f"{GIBS_WMS}?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS={layer}&STYLES=&SRS=EPSG:4326"
               f"&BBOX={bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f}&WIDTH={w}&HEIGHT={h}&FORMAT=image/jpeg")
        try:
            b = _get_img(url)
            if _is_jpeg(b):
                return b, layer
            last = "resposta não é imagem: " + b[:120].decode("utf-8", "replace")
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(last or "falha")


def image_world(size=4096):
    """JPEG do mundo inteiro (equiretangular). None se ainda não foi possível baixar."""
    size = 4096 if size >= 4096 else 2048
    f = _geo_dir() / "img" / f"mundo_{size}.jpg"
    if f.exists():
        return f.read_bytes()
    try:
        b, layer = _wms((-180, -90, 180, 90), size, size // 2)
        f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b)
        (f.parent / "fonte.txt").write_text(f"NASA GIBS · {layer} (domínio público) · {iso()}", encoding="utf-8")
        _img_state["error"] = None
        return b
    except Exception as e:
        _img_state["error"] = str(e)[:200]
        return None


def image_box(w, s, e, n):
    """JPEG de uma região (estado) em resolução maior. Caixa arredondada para reaproveitar o cache."""
    w, s, e, n = (round(max(-180, w) - 0.25, 1), round(max(-90, s) - 0.25, 1), round(min(180, e) + 0.25, 1), round(min(90, n) + 0.25, 1))
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90) or (e - w) > 60 or (n - s) > 60:
        raise ValueError("região inválida ou grande demais")
    f = _geo_dir() / "img" / f"box_{w}_{s}_{e}_{n}.jpg"
    if f.exists():
        return f.read_bytes(), (w, s, e, n)
    aspect = (e - w) / (n - s)
    W, H = (2048, max(64, int(2048 / aspect))) if aspect >= 1 else (max(64, int(2048 * aspect)), 2048)
    b, _ = _wms((w, s, e, n), W, H)
    f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b)
    return b, (w, s, e, n)


def tile(z, x, y):
    """Tile de ruas (OSM) com cache permanente em disco; no máx. ~4 downloads/s, nunca em massa."""
    z, x, y = int(z), int(x), int(y)
    if not (3 <= z <= 17 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ValueError("tile inválido")
    f = _geo_dir() / "tiles" / str(z) / str(x) / f"{y}.png"
    if f.exists():
        return f.read_bytes()
    with _tile_lock:
        wait = 0.25 - (time.time() - _tile_last[0])
        if wait > 0:
            time.sleep(wait)
        _tile_last[0] = time.time()
    b = _get_img(OSM_TILE.format(z=z, x=x, y=y), timeout=30)
    if not _is_png(b):
        raise RuntimeError("tile inválido recebido")
    f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b)
    return b


def imagery_status():
    d = _geo_dir() / "img"
    return {"world": any(d.glob("mundo_*.jpg")) if d.exists() else False, "error": _img_state["error"]}
