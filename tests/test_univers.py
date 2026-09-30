"""Univers international hors reseau : ordre des places, tickers londoniens, places mixtes."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import univers_intl as U
def table(lignes):
    return ("<table><tr><th>No.</th><th>Symbol</th><th>Company Name</th><th>Market Cap</th></tr>"
            + "".join(f"<tr><td>{i}</td><td>{s}</td><td>{n}</td><td>{c}</td></tr>" for i,(s,n,c) in enumerate(lignes,1)) + "</table>")
pages = {
 "euronext-amsterdam": table([("ASML","ASML Holding N.V.","250B"), ("SHELL","Shell plc","180B")]),
 "deutsche-boerse-xetra": table([("SAP","SAP SE","250B"), ("NESR","Nestlé S.A.","200B"), ("ASME","ASML Holding N.V.","250B"), ("EBO","Erste Group Bank AG","20B")]),
 "six-swiss-exchange": table([("NESN","Nestlé S.A.","200B")]),
 "vienna-stock-exchange": table([("EBS","Erste Group Bank AG","20B")]),
 "prague-stock-exchange": table([("ERBAG","Erste Group Bank AG","20B")]),
 "london-stock-exchange": table([("BA.","BAE Systems plc","60B"), ("SHEL","Shell plc","180B")]),
}
U.decouvrir_chemins = lambda *a: {"ams":"euronext-amsterdam","etr":"deutsche-boerse-xetra","swx":"six-swiss-exchange","vie":"vienna-stock-exchange","pra":"prague-stock-exchange","lon":"london-stock-exchange"}
U.lire = lambda url, timeout=45: pages[url.rstrip("/").split("/")[-1]]
U.time.sleep = lambda x: None
retenues = []
orig = U.main
sys.argv = ["u", "--places", "etr,ams,swx,vie,pra,lon", "--cap-min", "0"]
# capter les lignes retenues via ecrire
U.ecrire = lambda l: retenues.extend(l)
sys.argv.append("--ecrire")
U.charger_sec = lambda: {"zzz": "X"}
sys.argv.append("--d1")
U.main()
print()
t = {l["ticker"]: (l["pays"], l["pea"]) for l in retenues}
for k, v in sorted(t.items()): print(" ", k, v)
ECHECS = []
def ok(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        ECHECS.append(m)
ok("BA.L" in t and "BA-.L" not in t, "BAE : BA.L, plus de BA-.L")
ok("NESN.SW" in t and "NESR.DE" in t, "Nestlé sur deux places mixtes : les deux écrites, l'ISIN tranchera (incr12)")
ok("ASML.AS" in t and "ASME.DE" not in t, "ASML : place d'origine avant Xetra")
ok(t.get("NESN.SW") == (None, None), "place mixte : siège et PEA laissés vides")
ok("SAP.DE" in t and t["SAP.DE"] == (None, None), "SAP (Xetra, mixte) : à profiler")
ok(sum(1 for k in t if k.startswith(("EBS","ERBAG","EBO"))) == 3, "Erste sur trois places mixtes : les trois écrites, l'ISIN tranchera")
ok("SHELL.AS" in t and "SHEL.L" not in t, "Shell : une seule ligne (profil corrigera le PEA)")
sys.exit(1 if ECHECS else 0)
