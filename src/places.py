#!/usr/bin/env python3
"""
PLACES — table UNIQUE des correspondances de cotation (audit C1).

Avant : chaque script portait sa propre copie, et celle des comptes
internationaux se trompait sur sept places. StockAnalysis nomme Milan `bit`,
Lisbonne `eli`, Dublin `ise`, Varsovie `wse`, Seoul `krx`, Taipei `tpe` et
Toronto `tsx` ; le script demandait `mil`, `els`, `dub`, `war`, `kos`, `twn`
et `tor`, recevait un 404 sur chaque page, et marquait la societe « sans
page » pour toujours. Toute l'Italie, le Portugal, l'Irlande, la Pologne, la
Coree, Taiwan et le Canada sortaient ainsi du screener sans un message.

VERIFIE le 30/09/2026 sur une page de cotation par place (Ferrari BIT:RACE,
Orlen WSE:PKN, Samsung KRX:005930, TSMC TPE:2330, Constellation TSX:CSU,
CEZ PRA:CEZ, OTP BUD:OTP, OPAP ATH:OPAP, Equinor OSL:EQNR, Arion ICE:ARION,
Nokia HEL:NOKIA, Novo CPH:NOVO.B, Volvo STO:VOLV.B, Inditex BME:ITX,
BAE LON:BA, SAP ETR:SAP, Toyota TYO:7203, CSL ASX:CSL, Tencent HKG:0700).
Une place ajoutee ici se verifie de la meme facon AVANT d'etre servie.
"""

# Suffixe Yahoo -> code de place StockAnalysis (chemin /quote/<code>/<symbole>/).
SA_CODE = {
    ".PA": "epa", ".AS": "ams", ".BR": "ebr", ".LS": "eli", ".IR": "ise",
    ".DE": "etr", ".MI": "bit", ".MC": "bme", ".VI": "vie", ".AT": "ath",
    ".ST": "sto", ".CO": "cph", ".HE": "hel", ".OL": "osl", ".IC": "ice",
    ".WA": "wse", ".PR": "pra", ".BD": "bud", ".L": "lon", ".SW": "swx",
    ".T": "tyo", ".KS": "krx", ".TW": "tpe", ".TO": "tsx",
    # Places des lignes VanEck internationales (MOTI) : sans elles, ces
    # societes etaient inscrites mais ne recevaient jamais de comptes.
    ".AX": "asx", ".HK": "hkg",
}

# Suffixe Yahoo -> pays de la PLACE (pas du siege).
PAYS_PLACE = {
    ".PA": "FR", ".AS": "NL", ".BR": "BE", ".LS": "PT", ".IR": "IE",
    ".DE": "DE", ".MI": "IT", ".MC": "ES", ".VI": "AT", ".AT": "GR",
    ".ST": "SE", ".CO": "DK", ".HE": "FI", ".OL": "NO", ".IC": "IS",
    ".WA": "PL", ".PR": "CZ", ".BD": "HU", ".L": "GB", ".SW": "CH",
    ".T": "JP", ".KS": "KR", ".TW": "TW", ".TO": "CA", ".AX": "AU",
    ".HK": "HK",
}

# PLACES MIXTES — elles cotent en nombre des societes etrangeres (Xetra,
# Vienne, Prague, SIX). Le pays de la place n'y dit RIEN du siege : une
# suisse lue sur Xetra ressortait allemande, donc eligible au PEA.
MIXTES = {".DE", ".VI", ".PR", ".SW"}

# UE + EEE : seul critere d'eligibilite au PEA, apprecie au SIEGE SOCIAL.
EEE = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "IS", "LI", "NO",
}

# Libelle anglais (StockAnalysis, VanEck) -> code ISO.
PAYS_ISO = {
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "croatia": "HR",
    "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ", "denmark": "DK",
    "estonia": "EE", "finland": "FI", "france": "FR", "germany": "DE",
    "greece": "GR", "hungary": "HU", "ireland": "IE", "italy": "IT",
    "latvia": "LV", "lithuania": "LT", "luxembourg": "LU", "malta": "MT",
    "netherlands": "NL", "the netherlands": "NL", "poland": "PL",
    "portugal": "PT", "romania": "RO", "slovakia": "SK", "slovenia": "SI",
    "spain": "ES", "sweden": "SE", "iceland": "IS", "liechtenstein": "LI",
    "norway": "NO", "united states": "US", "united kingdom": "GB",
    "switzerland": "CH", "japan": "JP", "china": "CN", "hong kong": "HK",
    "taiwan": "TW", "south korea": "KR", "korea": "KR", "india": "IN",
    "brazil": "BR", "canada": "CA", "australia": "AU", "singapore": "SG",
    "mexico": "MX", "south africa": "ZA", "israel": "IL", "indonesia": "ID",
    "thailand": "TH", "jersey": "JE", "guernsey": "GG", "isle of man": "IM",
    "bermuda": "BM", "cayman islands": "KY", "monaco": "MC",
    "united arab emirates": "AE", "turkey": "TR", "russia": "RU",
    "argentina": "AR", "chile": "CL", "new zealand": "NZ", "macau": "MO",
    "british virgin islands": "VG", "gibraltar": "GI", "malaysia": "MY",
    "philippines": "PH", "vietnam": "VN", "saudi arabia": "SA",
    "kazakhstan": "KZ", "georgia": "GE", "ukraine": "UA", "serbia": "RS",
}

# Prefixes d'ISIN qui ne designent pas un pays d'emetteur.
ISIN_NON_PAYS = {"XS", "EU", "QS", "XC", "XA", "XB", "XD", "XF"}


def suffixe(ticker):
    """« NOVO-B.CO » -> « .CO » ; ticker sans suffixe -> ''."""
    return "." + ticker.rsplit(".", 1)[-1] if "." in (ticker or "") else ""


def symbole_sa(ticker):
    """Symbole StockAnalysis d'un ticker Yahoo : « NOVO-B.CO » -> « NOVO.B »."""
    return ticker.rsplit(".", 1)[0].replace("-", ".")


def isin_valide(isin):
    """Format ET chiffre de controle (Luhn sur la chaine convertie). Un ISIN
    lu de travers ne doit jamais decider d'une eligibilite."""
    import re
    if not isin or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        return False
    chiffres = "".join(str(int(c, 36)) for c in isin[:-1])
    total, double = 0, True
    for c in reversed(chiffres):
        d = int(c)
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return (10 - total % 10) % 10 == int(isin[-1])
