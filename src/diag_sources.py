"""Diagnostic des SOURCES DE COURS depuis GitHub — lecture seule, aucune ecriture.

26/10/2026 : StockAnalysis a cesse de servir ses listes americaines en entier
(nyse : refus en page 5 ; nasdaq : 7 pages lues mais 1 000 cours seulement).
Avant de choisir une source de remplacement, on MESURE ce qui repond d'ici —
les adresses GitHub ne sont pas traitees comme un navigateur. Chaque constat
sort en annotation (::notice::), lisible par l'API sans le journal complet.
"""
import json, re, time, urllib.request, urllib.error

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def note(txt):
    print("::notice::" + txt.replace("\n", " ")[:900])


def get(url, headers=None, timeout=40):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), round(time.time() - t0, 1)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:2000], round(time.time() - t0, 1)
    except Exception as e:
        return None, str(e).encode()[:300], round(time.time() - t0, 1)


def stockanalysis():
    for chemin in ("nyse-stocks", "nasdaq-stocks"):
        for p in ("", "?page=2", "?page=3", "?page=5"):
            st, corps, dt = get(f"https://stockanalysis.com/list/{chemin}/{p}")
            html = corps.decode("utf-8", "replace") if st == 200 else ""
            syms = set(re.findall(r"/stocks/([A-Za-z0-9.\-]+)/", html))
            lignes = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
            prix = 0
            for tr in lignes:
                tds = [re.sub(r"<[^>]+>", " ", x).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                if any(re.fullmatch(r"\$?[\d,]+\.\d+", t.replace(" ", "")) for t in tds):
                    prix += 1
            indice = ""
            for mot in ("upgrade", "pro ", "sign in", "limit", "premium", "rate"):
                if mot in html.lower():
                    indice += mot.strip() + " "
            note(f"SA {chemin}{p or ' p1'} : HTTP {st} en {dt}s · {len(html)} car · {len(syms)} symboles · {len(lignes)} lignes · {prix} avec prix"
                 + (f" · mots : {indice}" if indice else "")
                 + ("" if st == 200 else f" · corps : {corps[:160]!r}"))
            time.sleep(1.5)
    # API interne de l'ecran (utilisee par le site lui-meme)
    st, corps, dt = get("https://stockanalysis.com/api/screener/s/f?m=marketCap&s=desc&c=no,s,n,marketCap,price&i=stocks",
                        {"Accept": "application/json"})
    try:
        j = json.loads(corps)
        d = j.get("data", {})
        n = len(d.get("data", [])) if isinstance(d, dict) else None
        note(f"SA API ecran : HTTP {st} en {dt}s · {n} lignes · cles {list(j)[:5]} · ex {str(d.get('data', [])[:1])[:200] if isinstance(d, dict) else ''}")
    except Exception as e:
        note(f"SA API ecran : HTTP {st} en {dt}s · illisible ({e}) · {corps[:160]!r}")


def nasdaq():
    st, corps, dt = get("https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true",
                        {"Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
                         "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}, timeout=60)
    try:
        j = json.loads(corps)
        rows = (j.get("data") or {}).get("rows") or []
        par = {r.get("symbol"): r for r in rows}
        ex = {k: (par.get(k) or {}) for k in ("AAPL", "JPM", "BRK/B", "PJT", "KO")}
        note(f"Nasdaq API : HTTP {st} en {dt}s · {len(rows)} lignes · {len(corps)} octets · champs {list(rows[0].keys()) if rows else []}")
        note("Nasdaq exemples : " + " | ".join(f"{k}: {v.get('lastsale')} cap {v.get('marketCap')} {v.get('country')}" for k, v in ex.items()))
        nul = sum(1 for r in rows if not r.get("lastsale") or r.get("lastsale") in ("NA", "$0.00"))
        note(f"Nasdaq : {nul} lignes sans dernier cours · symboles avec / : {sum(1 for r in rows if '/' in (r.get('symbol') or ''))} · avec ^ : {sum(1 for r in rows if '^' in (r.get('symbol') or ''))}")
    except Exception as e:
        note(f"Nasdaq API : HTTP {st} en {dt}s · illisible ({e}) · {corps[:200]!r}")


def yahoo():
    for u in ("https://query1.finance.yahoo.com/v7/finance/spark?symbols=AAPL,JPM,MC.PA&range=1d&interval=1d",
              "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1d",
              "https://query1.finance.yahoo.com/v7/finance/quote?symbols=AAPL"):
        st, corps, dt = get(u, {"Accept": "application/json"})
        note(f"Yahoo {u.split('/finance/')[1][:40]} : HTTP {st} en {dt}s · {corps[:140]!r}")
        time.sleep(1)


if __name__ == "__main__":
    for f in (nasdaq, stockanalysis, yahoo):
        try:
            f()
        except Exception as e:
            note(f"{f.__name__} : exception {type(e).__name__} {e}")
