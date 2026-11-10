"""Catálogo de países para expansão: idioma, moeda, fuso, capital (posição da base remota no mapa),
lei de proteção de dados, imposto sobre consumo e plataformas de venda fortes.

Ponto de partida, não parecer jurídico/fiscal: tudo é marcado "confirmar com advogado/contador local"
e o agente Jurídico pesquisa notícias recentes. Países fora do catálogo: idioma e moeda são informados na criação.
"""
from __future__ import annotations

import unicodedata

GLOBAL_PLATFORMS = ["Loja própria (Shopify, Nuvemshop, WooCommerce)", "Amazon (verificar se opera no país)", "eBay", "Etsy (produtos artesanais)",
                    "Google Shopping", "Instagram/Facebook Shop", "TikTok Shop (verificar disponibilidade)"]

# iso2: (nome_pt, aliases em inglês, idioma BCP-47, moeda, fuso, (lat, lng) capital, lei de dados, imposto consumo, plataformas)
C = {
    "BR": ("Brasil", ["Brazil"], "pt-BR", "BRL", "America/Sao_Paulo", (-15.79, -47.88), "LGPD (Lei 13.709/2018) e Código de Defesa do Consumidor",
           "ICMS/ISS/PIS/COFINS (reforma: IBS/CBS em transição)", ["Mercado Livre", "Shopee", "Amazon.com.br", "Magazine Luiza", "Americanas"]),
    "PT": ("Portugal", ["Portugal"], "pt-PT", "EUR", "Europe/Lisbon", (38.72, -9.14), "RGPD/GDPR (UE)", "IVA (UE; regime OSS para vendas à distância)",
           ["Worten Marketplace", "Amazon.es (entrega em Portugal)", "FNAC", "KuantoKusta", "OLX"]),
    "US": ("Estados Unidos", ["United States of America", "United States", "USA"], "en-US", "USD", "America/New_York", (38.90, -77.04),
           "Leis estaduais (ex.: CCPA/CPRA na Califórnia) e regras da FTC", "Sales tax por estado (nexo econômico)",
           ["Amazon.com", "Walmart Marketplace", "eBay", "Etsy", "TikTok Shop"]),
    "CA": ("Canadá", ["Canada"], "en-CA", "CAD", "America/Toronto", (45.42, -75.70), "PIPEDA (e Lei 25 em Quebec)", "GST/HST e PST por província",
           ["Amazon.ca", "Walmart.ca", "Best Buy Marketplace", "eBay.ca"]),
    "MX": ("México", ["Mexico"], "es-MX", "MXN", "America/Mexico_City", (19.43, -99.13), "LFPDPPP", "IVA",
           ["Mercado Libre", "Amazon.com.mx", "Walmart.com.mx", "Liverpool", "Coppel"]),
    "AR": ("Argentina", ["Argentina"], "es-AR", "ARS", "America/Argentina/Buenos_Aires", (-34.60, -58.38), "Lei 25.326", "IVA",
           ["Mercado Libre", "Tienda Nube (loja própria)"]),
    "CL": ("Chile", ["Chile"], "es-CL", "CLP", "America/Santiago", (-33.45, -70.67), "Lei 19.628 e nova Lei 21.719 (transição)", "IVA",
           ["Mercado Libre", "Falabella", "Paris", "Ripley"]),
    "CO": ("Colômbia", ["Colombia"], "es-CO", "COP", "America/Bogota", (4.71, -74.07), "Lei 1581 de 2012", "IVA",
           ["Mercado Libre", "Falabella", "Éxito"]),
    "PE": ("Peru", ["Peru"], "es-PE", "PEN", "America/Lima", (-12.05, -77.04), "Lei 29733", "IGV", ["Mercado Libre", "Falabella", "Ripley", "Plaza Vea"]),
    "UY": ("Uruguai", ["Uruguay"], "es-UY", "UYU", "America/Montevideo", (-34.90, -56.16), "Lei 18.331", "IVA", ["Mercado Libre"]),
    "PY": ("Paraguai", ["Paraguay"], "es-PY", "PYG", "America/Asuncion", (-25.26, -57.58), "Lei 6534/2020 (dados de crédito) — verificar", "IVA", []),
    "BO": ("Bolívia", ["Bolivia"], "es-BO", "BOB", "America/La_Paz", (-16.50, -68.15), "Sem lei geral específica — verificar", "IVA", []),
    "EC": ("Equador", ["Ecuador"], "es-EC", "USD", "America/Guayaquil", (-0.18, -78.47), "Lei Orgânica de Proteção de Dados (2021)", "IVA", ["Mercado Libre"]),
    "VE": ("Venezuela", ["Venezuela"], "es-VE", "VES", "America/Caracas", (10.48, -66.90), "Verificar", "IVA", []),
    "CR": ("Costa Rica", ["Costa Rica"], "es-CR", "CRC", "America/Costa_Rica", (9.93, -84.08), "Lei 8968", "IVA", []),
    "PA": ("Panamá", ["Panama"], "es-PA", "USD", "America/Panama", (8.98, -79.52), "Lei 81 de 2019", "ITBMS", []),
    "DO": ("República Dominicana", ["Dominican Rep.", "Dominican Republic"], "es-DO", "DOP", "America/Santo_Domingo", (18.49, -69.93), "Lei 172-13", "ITBIS", []),
    "GT": ("Guatemala", ["Guatemala"], "es-GT", "GTQ", "America/Guatemala", (14.63, -90.51), "Verificar", "IVA", []),
    "ES": ("Espanha", ["Spain"], "es-ES", "EUR", "Europe/Madrid", (40.42, -3.70), "RGPD/GDPR (UE) e LOPDGDD", "IVA (UE; OSS)",
           ["Amazon.es", "El Corte Inglés", "PcComponentes", "Carrefour Marketplace", "Wallapop"]),
    "FR": ("França", ["France"], "fr-FR", "EUR", "Europe/Paris", (48.86, 2.35), "RGPD/GDPR (UE)", "TVA (UE; OSS)",
           ["Amazon.fr", "Cdiscount", "Fnac", "Leboncoin", "ManoMano"]),
    "DE": ("Alemanha", ["Germany"], "de-DE", "EUR", "Europe/Berlin", (52.52, 13.40), "DSGVO/GDPR (UE) e BDSG", "MwSt/USt (UE; OSS); Impressum obrigatório no site",
           ["Amazon.de", "eBay.de", "Otto", "Kaufland", "Zalando"]),
    "IT": ("Itália", ["Italy"], "it-IT", "EUR", "Europe/Rome", (41.90, 12.50), "GDPR (UE) e Código de Privacidade", "IVA (UE; OSS)",
           ["Amazon.it", "eBay.it", "Subito", "ManoMano"]),
    "GB": ("Reino Unido", ["United Kingdom", "UK"], "en-GB", "GBP", "Europe/London", (51.51, -0.13), "UK GDPR e Data Protection Act 2018", "VAT",
           ["Amazon.co.uk", "eBay.co.uk", "OnBuy", "Etsy", "ManoMano"]),
    "IE": ("Irlanda", ["Ireland"], "en-IE", "EUR", "Europe/Dublin", (53.35, -6.26), "GDPR (UE)", "VAT (UE; OSS)", ["Amazon (entrega na Irlanda)", "DoneDeal", "Adverts.ie"]),
    "NL": ("Países Baixos", ["Netherlands"], "nl-NL", "EUR", "Europe/Amsterdam", (52.37, 4.90), "AVG/GDPR (UE)", "BTW (UE; OSS)", ["Bol.com", "Amazon.nl", "Marktplaats"]),
    "BE": ("Bélgica", ["Belgium"], "nl-BE", "EUR", "Europe/Brussels", (50.85, 4.35), "GDPR (UE)", "TVA/BTW (UE; OSS)", ["Bol.com", "Amazon.com.be", "2dehands"]),
    "PL": ("Polônia", ["Poland"], "pl-PL", "PLN", "Europe/Warsaw", (52.23, 21.01), "RODO/GDPR (UE)", "VAT (UE; OSS)", ["Allegro", "Amazon.pl", "OLX"]),
    "SE": ("Suécia", ["Sweden"], "sv-SE", "SEK", "Europe/Stockholm", (59.33, 18.07), "GDPR (UE)", "Moms (UE; OSS)", ["Amazon.se", "CDON", "Blocket", "Tradera"]),
    "NO": ("Noruega", ["Norway"], "nb-NO", "NOK", "Europe/Oslo", (59.91, 10.75), "Lei de dados pessoais (GDPR via EEE)", "MVA", ["Finn.no", "Komplett"]),
    "DK": ("Dinamarca", ["Denmark"], "da-DK", "DKK", "Europe/Copenhagen", (55.68, 12.57), "GDPR (UE)", "Moms (UE; OSS)", ["DBA", "Amazon.de (entrega)"]),
    "FI": ("Finlândia", ["Finland"], "fi-FI", "EUR", "Europe/Helsinki", (60.17, 24.94), "GDPR (UE)", "ALV (UE; OSS)", ["Verkkokauppa.com", "Tori.fi"]),
    "CH": ("Suíça", ["Switzerland"], "de-CH", "CHF", "Europe/Zurich", (46.95, 7.45), "nLPD/FADP", "MWST/TVA", ["Galaxus", "Ricardo", "Brack"]),
    "AT": ("Áustria", ["Austria"], "de-AT", "EUR", "Europe/Vienna", (48.21, 16.37), "DSGVO/GDPR (UE)", "USt (UE; OSS)", ["Amazon.de", "willhaben"]),
    "CZ": ("Tchéquia", ["Czechia", "Czech Republic", "Czech Rep."], "cs-CZ", "CZK", "Europe/Prague", (50.08, 14.44), "GDPR (UE)", "DPH (UE; OSS)", ["Alza", "Mall.cz", "Heureka"]),
    "GR": ("Grécia", ["Greece"], "el-GR", "EUR", "Europe/Athens", (37.98, 23.73), "GDPR (UE)", "FPA (UE; OSS)", ["Skroutz", "Public"]),
    "RO": ("Romênia", ["Romania"], "ro-RO", "RON", "Europe/Bucharest", (44.43, 26.10), "GDPR (UE)", "TVA (UE; OSS)", ["eMAG", "OLX"]),
    "HU": ("Hungria", ["Hungary"], "hu-HU", "HUF", "Europe/Budapest", (47.50, 19.04), "GDPR (UE)", "ÁFA (UE; OSS)", ["eMAG", "Árukereső"]),
    "TR": ("Turquia", ["Turkey", "Türkiye"], "tr-TR", "TRY", "Europe/Istanbul", (39.93, 32.85), "KVKK", "KDV", ["Trendyol", "Hepsiburada", "N11", "Amazon.com.tr"]),
    "IN": ("Índia", ["India"], "hi-IN", "INR", "Asia/Kolkata", (28.61, 77.21), "DPDP Act 2023", "GST", ["Amazon.in", "Flipkart", "Meesho", "JioMart"]),
    "CN": ("China", ["China"], "zh-CN", "CNY", "Asia/Shanghai", (39.90, 116.41), "PIPL (regras rígidas de transferência de dados)", "VAT",
           ["Tmall", "JD.com", "Pinduoduo", "Douyin"]),
    "JP": ("Japão", ["Japan"], "ja-JP", "JPY", "Asia/Tokyo", (35.68, 139.69), "APPI", "Imposto sobre consumo", ["Amazon.co.jp", "Rakuten Ichiba", "Yahoo! Shopping", "Mercari"]),
    "KR": ("Coreia do Sul", ["South Korea", "Korea", "Republic of Korea", "S. Korea"], "ko-KR", "KRW", "Asia/Seoul", (37.57, 126.98), "PIPA", "VAT",
           ["Coupang", "Naver Smart Store", "Gmarket", "11st"]),
    "ID": ("Indonésia", ["Indonesia"], "id-ID", "IDR", "Asia/Jakarta", (-6.21, 106.85), "Lei PDP (27/2022)", "PPN", ["Shopee", "Tokopedia", "Lazada", "Blibli"]),
    "TH": ("Tailândia", ["Thailand"], "th-TH", "THB", "Asia/Bangkok", (13.76, 100.50), "PDPA", "VAT", ["Shopee", "Lazada"]),
    "VN": ("Vietnã", ["Vietnam", "Viet Nam"], "vi-VN", "VND", "Asia/Ho_Chi_Minh", (21.03, 105.85), "Decreto 13/2023 e lei de dados pessoais", "VAT", ["Shopee", "Lazada", "Tiki"]),
    "PH": ("Filipinas", ["Philippines"], "en-PH", "PHP", "Asia/Manila", (14.60, 120.98), "Data Privacy Act 2012", "VAT", ["Shopee", "Lazada"]),
    "MY": ("Malásia", ["Malaysia"], "ms-MY", "MYR", "Asia/Kuala_Lumpur", (3.14, 101.69), "PDPA 2010", "SST", ["Shopee", "Lazada"]),
    "SG": ("Singapura", ["Singapore"], "en-SG", "SGD", "Asia/Singapore", (1.35, 103.82), "PDPA", "GST", ["Shopee", "Lazada", "Amazon.sg"]),
    "AU": ("Austrália", ["Australia"], "en-AU", "AUD", "Australia/Sydney", (-35.28, 149.13), "Privacy Act 1988", "GST", ["Amazon.com.au", "eBay.com.au", "Kogan", "Gumtree"]),
    "NZ": ("Nova Zelândia", ["New Zealand"], "en-NZ", "NZD", "Pacific/Auckland", (-41.29, 174.78), "Privacy Act 2020", "GST", ["Trade Me"]),
    "ZA": ("África do Sul", ["South Africa"], "en-ZA", "ZAR", "Africa/Johannesburg", (-25.75, 28.19), "POPIA", "VAT", ["Takealot", "Amazon.co.za", "Makro"]),
    "NG": ("Nigéria", ["Nigeria"], "en-NG", "NGN", "Africa/Lagos", (9.08, 7.40), "NDPA 2023", "VAT", ["Jumia", "Konga"]),
    "KE": ("Quênia", ["Kenya"], "en-KE", "KES", "Africa/Nairobi", (-1.29, 36.82), "Data Protection Act 2019", "VAT", ["Jumia", "Kilimall"]),
    "EG": ("Egito", ["Egypt"], "ar-EG", "EGP", "Africa/Cairo", (30.04, 31.24), "Lei 151/2020", "VAT", ["Amazon.eg", "Noon", "Jumia"]),
    "MA": ("Marrocos", ["Morocco"], "fr-MA", "MAD", "Africa/Casablanca", (34.02, -6.83), "Lei 09-08", "TVA", ["Jumia", "Avito"]),
    "AO": ("Angola", ["Angola"], "pt-AO", "AOA", "Africa/Luanda", (-8.84, 13.23), "Lei 22/11", "IVA", []),
    "MZ": ("Moçambique", ["Mozambique"], "pt-MZ", "MZN", "Africa/Maputo", (-25.97, 32.57), "Verificar", "IVA", []),
    "AE": ("Emirados Árabes Unidos", ["United Arab Emirates"], "ar-AE", "AED", "Asia/Dubai", (24.45, 54.38), "PDPL (Decreto-Lei 45/2021)", "VAT", ["Amazon.ae", "Noon"]),
    "SA": ("Arábia Saudita", ["Saudi Arabia"], "ar-SA", "SAR", "Asia/Riyadh", (24.71, 46.68), "PDPL", "VAT", ["Amazon.sa", "Noon"]),
}

LANG_NAME = {"pt": "português", "en": "inglês", "es": "espanhol", "fr": "francês", "de": "alemão", "it": "italiano", "nl": "holandês", "pl": "polonês",
             "sv": "sueco", "nb": "norueguês", "da": "dinamarquês", "fi": "finlandês", "cs": "tcheco", "el": "grego", "ro": "romeno", "hu": "húngaro",
             "tr": "turco", "hi": "hindi", "zh": "chinês", "ja": "japonês", "ko": "coreano", "id": "indonésio", "th": "tailandês", "vi": "vietnamita",
             "ms": "malaio", "ar": "árabe"}


def _norm(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(ch)).strip()


_BY_NAME = {}
for _iso, _v in C.items():
    for _n in [_v[0], *_v[1]]:
        _BY_NAME[_norm(_n)] = _iso


def lookup(name_or_iso):
    """Dados do país por nome (inglês do mapa ou português) ou código ISO-2. None se fora do catálogo."""
    k = (name_or_iso or "").strip()
    iso = k.upper() if k.upper() in C else _BY_NAME.get(_norm(k))
    if not iso:
        return None
    pt, aliases, lang, cur, tz, cap, lei, imposto, plats = C[iso]
    return {"iso2": iso, "name_pt": pt, "name_en": aliases[0], "lang": lang, "lang_name": LANG_NAME.get(lang.split("-")[0], lang),
            "currency": cur, "tz": tz, "capital": cap, "data_law": lei, "consumption_tax": imposto,
            "platforms": plats + [p for p in GLOBAL_PLATFORMS if not any(p.split(" ")[0] in x for x in plats)][:3],
            "aviso": "Ponto de partida automático: confirme com advogado e contador do país antes de operar."}
