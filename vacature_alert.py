#!/usr/bin/env python3
"""
Vacature-alert
==============
Controleert RSS/Atom-feeds (Google Alerts en vacaturesites) op nieuwe vacatures
die passen bij de zoektermen in config.json, en stuurt daarvan een melding per
e-mail en/of Telegram. Al gemelde vacatures worden onthouden in gezien.json,
zodat je elke vacature maar een keer krijgt.

Alleen standaard-Python nodig (3.9 of nieuwer), geen extra pakketten.

Gebruik:
  python vacature_alert.py                 zoeken, melden en onthouden
  python vacature_alert.py --droog         alleen tonen, niets versturen of opslaan
  python vacature_alert.py --stil          alles wat er nu staat als gezien markeren, zonder melding
  python vacature_alert.py --test-feeds    per feed tonen of hij werkt en wat er binnenkomt
  python vacature_alert.py --test-melding  testbericht sturen om e-mail/Telegram te controleren
"""

import argparse
import functools
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

BASIS = Path(__file__).resolve().parent
ATOM = "{http://www.w3.org/2005/Atom}"
USER_AGENT = "Mozilla/5.0 (compatible; vacature-alert/1.0; persoonlijk gebruik)"
BEWAAR_DAGEN = 180      # zo lang onthouden we een gemelde vacature
MAX_PER_MELDING = 60    # meer dan dit per bericht wordt onleesbaar


# --------------------------------------------------------------------------
# Instellingen en opslag
# --------------------------------------------------------------------------

def laad_env(pad):
    """Leest een optioneel .env-bestand (SLEUTEL=waarde), handig op een eigen server."""
    if not pad.exists():
        return
    for regel in pad.read_text(encoding="utf-8").splitlines():
        regel = regel.strip()
        if not regel or regel.startswith("#") or "=" not in regel:
            continue
        sleutel, waarde = regel.split("=", 1)
        os.environ.setdefault(sleutel.strip(), waarde.strip().strip('"').strip("'"))


def env(naam, standaard=""):
    return (os.environ.get(naam) or "").strip() or standaard


def laad_json(pad, standaard):
    if pad.exists():
        with open(pad, encoding="utf-8") as f:
            return json.load(f)
    return standaard


def bewaar_json(pad, data):
    tijdelijk = pad.with_suffix(".tmp")
    with open(tijdelijk, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    tijdelijk.replace(pad)


# --------------------------------------------------------------------------
# Feeds ophalen en lezen
# --------------------------------------------------------------------------

def haal_op(url, timeout=30):
    if "://" not in url:  # lokaal bestand, handig om te testen
        pad = Path(url)
        return (pad if pad.is_absolute() else BASIS / pad).read_bytes()
    verzoek = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(verzoek, timeout=timeout) as antwoord:
        return antwoord.read()


def zonder_html(tekst):
    if not tekst:
        return ""
    tekst = re.sub(r"<[^>]+>", " ", tekst)
    tekst = html.unescape(tekst)
    tekst = re.sub(r"<[^>]+>", " ", tekst)
    tekst = re.sub(r"\s+", " ", tekst)
    tekst = re.sub(r"\s+([,.;:!?)])", r"\1", tekst)  # geen spatie voor leestekens
    return re.sub(r"\(\s+", "(", tekst).strip()


def echte_link(url):
    """Google Alerts verpakt links als google.com/url?...&url=ECHTE_LINK; pak die uit."""
    url = (url or "").strip()
    deel = urllib.parse.urlparse(url)
    if "google." in deel.netloc and deel.path == "/url":
        waarden = urllib.parse.parse_qs(deel.query).get("url")
        if waarden:
            return waarden[0]
    return url


def maak_item(id_, titel, link, samenvatting, datum):
    link = echte_link(link)
    return {
        "id": (id_ or link or "").strip(),
        "titel": zonder_html(titel),
        "link": link,
        "samenvatting": zonder_html(samenvatting),
        "datum": (datum or "").strip(),
    }


def lees_feed(data):
    wortel = ET.fromstring(data)
    items = []
    if wortel.tag.rsplit("}", 1)[-1] == "feed":  # Atom, o.a. Google Alerts
        for e in wortel.findall(ATOM + "entry"):
            link = ""
            for l in e.findall(ATOM + "link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            items.append(maak_item(
                e.findtext(ATOM + "id"), e.findtext(ATOM + "title"), link,
                e.findtext(ATOM + "content") or e.findtext(ATOM + "summary"),
                e.findtext(ATOM + "published") or e.findtext(ATOM + "updated"),
            ))
    else:  # RSS 2.0, o.a. WordPress-vacaturesites
        for it in wortel.iter("item"):
            items.append(maak_item(
                it.findtext("guid"), it.findtext("title"), it.findtext("link"),
                it.findtext("description"), it.findtext("pubDate"),
            ))
    return items


# --------------------------------------------------------------------------
# Filteren
# --------------------------------------------------------------------------

def ontaccent(tekst):
    """'coördinator' -> 'coordinator', zodat accenten niet uitmaken."""
    return "".join(t for t in unicodedata.normalize("NFKD", tekst) if not unicodedata.combining(t))


@functools.lru_cache(maxsize=None)
def patroon(term):
    """Zoekterm als heel woord; een * aan het eind betekent 'begint met'."""
    term = ontaccent(term.strip().lower())
    begint_met = term.endswith("*")
    woorden = [re.escape(w) for w in term.rstrip("*").split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(woorden) + ("" if begint_met else r"(?!\w)"),
                      re.IGNORECASE)


def gevonden(tekst, termen):
    tekst = ontaccent(tekst)
    return [t for t in termen if patroon(t).search(tekst)]


class Filter:
    def __init__(self, cfg):
        self.categorieen = cfg.get("zoektermen", {})
        self.uitsluiten = cfg.get("uitsluiten_als_in_titel", [])
        self.plaatsen = cfg.get("voorkeursplaatsen", [])
        self.signaalwoorden = cfg.get("vacature_signaalwoorden", [])

    def beoordeel(self, item, feed):
        """Geeft (past, labels) terug."""
        titel = item["titel"]
        alles = f"{titel} {item['samenvatting']}"
        if gevonden(titel, self.uitsluiten):
            return False, []
        if feed.get("alleen_vacatures") and not gevonden(alles, self.signaalwoorden):
            return False, []  # waarschijnlijk een nieuwsbericht, geen vacature
        modus = feed.get("filter", "titel")
        if modus == "geen":
            label = feed.get("label") or re.sub(r"^google alert:\s*", "", feed.get("naam", ""), flags=re.I)
            return True, [label]
        tekst = titel if modus == "titel" else alles
        labels = [cat for cat, termen in self.categorieen.items() if gevonden(tekst, termen)]
        return bool(labels), labels

    def plaatsen_in(self, item):
        return gevonden(f"{item['titel']} {item['samenvatting']}", self.plaatsen)


def item_sleutels(item):
    """Een sleutel op link en een op titel, zodat dezelfde vacatures via
    verschillende sites niet steeds opnieuw gemeld worden."""
    sleutels = []
    link = (item["link"] or item["id"]).split("#")[0].rstrip("/").lower()
    if link:
        sleutels.append("l:" + hashlib.sha1(link.encode("utf-8")).hexdigest()[:16])
    titel = re.sub(r"\W+", " ", ontaccent(item["titel"].lower())).strip()
    if titel:
        sleutels.append("t:" + hashlib.sha1(titel.encode("utf-8")).hexdigest()[:16])
    return sleutels


def actieve_feeds(cfg):
    for feed in cfg.get("feeds", []):
        url = (feed.get("url") or "").strip()
        ingesteld = url and not url.upper().startswith("PLAK")
        yield feed, url, bool(ingesteld and feed.get("actief", True))


def verzamel(cfg, filt, gezien):
    """Haalt alle feeds op. Geeft (nieuwe_vacatures, fouten, aantal_actieve_feeds)."""
    nieuw, fouten, actief, deze_run = [], [], 0, set()
    for feed, url, aan in actieve_feeds(cfg):
        if not aan:
            continue
        actief += 1
        naam = feed.get("naam") or url
        try:
            items = lees_feed(haal_op(url))
        except Exception as fout:  # elke fout melden en doorgaan met de rest
            fouten.append(f"{naam}: {fout}")
            continue
        for item in items:
            past, labels = filt.beoordeel(item, feed)
            if not past:
                continue
            sleutels = item_sleutels(item)
            if any(s in gezien or s in deze_run for s in sleutels):
                continue
            deze_run.update(sleutels)
            item.update(bron=naam, labels=labels, plaatsen=filt.plaatsen_in(item), sleutels=sleutels)
            nieuw.append(item)
    nieuw.sort(key=lambda v: not v["plaatsen"])  # voorkeursplaatsen bovenaan
    return nieuw, fouten, actief


# --------------------------------------------------------------------------
# Melden
# --------------------------------------------------------------------------

def kort(tekst, n):
    if len(tekst) <= n:
        return tekst
    return tekst[: n - 1].rsplit(" ", 1)[0] + "…"


def onderwerp_voor(n):
    return f"{n} nieuwe vacature{'s' if n != 1 else ''} voor jullie zoektocht ({date.today():%d-%m-%Y})"


def maak_tekst(vacatures, fouten, totaal):
    regels = [onderwerp_voor(totaal), ""]
    for v in vacatures:
        plaats = f"  [{', '.join(v['plaatsen'])}]" if v["plaatsen"] else ""
        regels += [f"- {v['titel'] or '(zonder titel)'}{plaats}",
                   f"  {v['bron']} | past bij: {', '.join(v['labels'])}",
                   f"  {v['link']}", ""]
    if totaal > len(vacatures):
        regels.append(f"(+ {totaal - len(vacatures)} meer, die komen in een volgende melding)")
    if fouten:
        regels += ["", "Let op, deze feeds werkten niet:"] + [f"- {f}" for f in fouten]
    return "\n".join(regels)


def maak_html(vacatures, fouten, totaal):
    blokken = []
    for v in vacatures:
        plaats = (f' <span style="color:#2e7d32;font-size:13px">📍 {html.escape(", ".join(v["plaatsen"]))}</span>'
                  if v["plaatsen"] else "")
        blokken.append(
            '<div style="margin:0 0 14px;padding:12px 14px;border:1px solid #e3e3e3;border-radius:8px">'
            f'<a href="{html.escape(v["link"], quote=True)}" style="font-size:16px;font-weight:600;'
            f'color:#1a56db;text-decoration:none">{html.escape(v["titel"] or "(zonder titel)")}</a>{plaats}'
            f'<div style="font-size:13px;color:#666;margin:4px 0">{html.escape(v["bron"])} · past bij: '
            f'{html.escape(", ".join(v["labels"]))}</div>'
            f'<div style="font-size:14px;color:#333;line-height:1.4">{html.escape(kort(v["samenvatting"], 280))}</div>'
            "</div>")
    extra = ""
    if totaal > len(vacatures):
        extra += f"<p>+ {totaal - len(vacatures)} meer, die komen in een volgende melding.</p>"
    if fouten:
        extra += ('<p style="color:#b00020;font-size:13px">Let op, deze feeds werkten niet:<br>'
                  + "<br>".join(html.escape(f) for f in fouten) + "</p>")
    return ('<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:640px">'
            f'<h2 style="font-size:18px">{html.escape(onderwerp_voor(totaal))}</h2>'
            + "".join(blokken) + extra + "</div>")


def stuur_email(onderwerp, tekst, html_body):
    host = env("SMTP_HOST", "smtp.gmail.com")
    poort = int(env("SMTP_PORT", "465"))
    gebruiker, wachtwoord = env("SMTP_USER"), env("SMTP_PASSWORD")
    ontvangers = [a.strip() for a in env("MAIL_TO", gebruiker).split(",") if a.strip()]

    bericht = EmailMessage()
    bericht["Subject"] = onderwerp
    bericht["From"] = env("MAIL_FROM", gebruiker)
    bericht["To"] = ", ".join(ontvangers)
    bericht["Date"] = formatdate(localtime=True)
    bericht["Message-ID"] = make_msgid()
    bericht.set_content(tekst)
    bericht.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if poort == 465:
        with smtplib.SMTP_SSL(host, poort, context=context, timeout=30) as s:
            s.login(gebruiker, wachtwoord)
            s.send_message(bericht, to_addrs=ontvangers)
    else:
        with smtplib.SMTP(host, poort, timeout=30) as s:
            s.starttls(context=context)
            s.login(gebruiker, wachtwoord)
            s.send_message(bericht, to_addrs=ontvangers)


def stuur_telegram(vacatures, totaal):
    token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    regels = [f"<b>{html.escape(onderwerp_voor(totaal))}</b>"]
    for v in vacatures:
        plaats = f" 📍 {html.escape(', '.join(v['plaatsen']))}" if v["plaatsen"] else ""
        regels.append(f'• <a href="{html.escape(v["link"], quote=True)}">'
                      f'{html.escape(v["titel"] or "(zonder titel)")}</a>{plaats}\n'
                      f'  <i>{html.escape(v["bron"])}</i>')
    stukken, huidig = [], ""
    for regel in regels:  # Telegram staat max. 4096 tekens per bericht toe
        if huidig and len(huidig) + len(regel) > 3800:
            stukken.append(huidig)
            huidig = ""
        huidig += regel + "\n\n"
    stukken.append(huidig)
    for stuk in stukken:
        data = urllib.parse.urlencode({"chat_id": chat, "text": stuk, "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
        verzoek = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(verzoek, timeout=30) as antwoord:
            antwoord.read()


def meld(vacatures, fouten):
    """Stuurt via alle ingestelde kanalen. True als minstens een kanaal lukte."""
    totaal = len(vacatures)
    deel = vacatures[:MAX_PER_MELDING]
    kanalen, gelukt = 0, False
    if env("SMTP_USER") and env("SMTP_PASSWORD"):
        kanalen += 1
        try:
            stuur_email(onderwerp_voor(totaal), maak_tekst(deel, fouten, totaal), maak_html(deel, fouten, totaal))
            print("E-mail verstuurd.")
            gelukt = True
        except Exception as fout:
            print(f"E-mail versturen mislukt: {fout}", file=sys.stderr)
    if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
        kanalen += 1
        try:
            stuur_telegram(deel, totaal)
            print("Telegram-bericht verstuurd.")
            gelukt = True
        except Exception as fout:
            print(f"Telegram versturen mislukt: {fout}", file=sys.stderr)
    if kanalen == 0:
        print("Geen meldkanaal ingesteld (zie LEESMIJ.md, stap 3). "
              "De vacatures worden daarom niet als gezien gemarkeerd.", file=sys.stderr)
    return gelukt, deel


# --------------------------------------------------------------------------
# Testfuncties
# --------------------------------------------------------------------------

def test_feeds(cfg, filt):
    for feed, url, aan in actieve_feeds(cfg):
        naam = feed.get("naam") or url
        if not aan:
            print(f"[nog niet ingesteld] {naam}")
            continue
        try:
            items = lees_feed(haal_op(url))
        except Exception as fout:
            print(f"[FOUT] {naam}: {fout}")
            continue
        passend = [i for i in items if filt.beoordeel(i, feed)[0]]
        print(f"[OK] {naam}: {len(items)} items, waarvan {len(passend)} passend")
        for i in passend[:5]:
            print(f"      - {i['titel']}")


def test_melding():
    voorbeeld = [{
        "titel": "Testvacature: als je dit leest, werken de meldingen",
        "link": "https://example.com", "bron": "Vacature-alert",
        "labels": ["test"], "plaatsen": [],
        "samenvatting": "Dit is een testbericht. Vanaf nu krijg je hier nieuwe vacatures binnen.",
    }]
    gelukt, _ = meld(voorbeeld, [])
    return gelukt


# --------------------------------------------------------------------------
# Hoofdprogramma
# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="Meldt nieuwe vacatures die passen bij jullie zoektermen.")
    p.add_argument("--droog", action="store_true", help="alleen tonen, niets versturen of opslaan")
    p.add_argument("--stil", action="store_true", help="alles als gezien markeren zonder melding")
    p.add_argument("--test-feeds", action="store_true", help="controleer elke feed")
    p.add_argument("--test-melding", action="store_true", help="stuur een testbericht")
    p.add_argument("--config", default=str(BASIS / "config.json"))
    p.add_argument("--gezien", default=str(BASIS / "gezien.json"))
    args = p.parse_args(argv)

    laad_env(BASIS / ".env")
    config_pad, gezien_pad = Path(args.config), Path(args.gezien)
    if not config_pad.exists():
        print(f"config.json niet gevonden op {config_pad}", file=sys.stderr)
        return 1
    cfg = laad_json(config_pad, {})
    filt = Filter(cfg)

    if args.test_melding:
        return 0 if test_melding() else 1
    if args.test_feeds:
        test_feeds(cfg, filt)
        return 0

    staat = laad_json(gezien_pad, {"gezien": {}})
    gezien = staat.setdefault("gezien", {})
    nieuw, fouten, actief = verzamel(cfg, filt, gezien)

    print(f"{actief} feed(s) gecontroleerd, {len(nieuw)} nieuwe passende vacature(s).")
    for f in fouten:
        print(f"  Feed werkte niet: {f}", file=sys.stderr)
    if actief == 0:
        print("Nog geen feeds ingesteld. Plak eerst je Google Alerts-links in config.json.", file=sys.stderr)

    if args.droog:
        for v in nieuw:
            plaats = f" [{', '.join(v['plaatsen'])}]" if v["plaatsen"] else ""
            print(f"  - {v['titel']}{plaats}\n    {v['bron']} | {', '.join(v['labels'])}\n    {v['link']}")
        return 0

    gemeld = []
    if args.stil:
        gemeld = nieuw
        print("Stille modus: alles als gezien gemarkeerd, geen melding verstuurd.")
    elif nieuw:
        gelukt, gemeld = meld(nieuw, fouten)
        if not gelukt:
            return 1  # niets opslaan, volgende keer opnieuw proberen

    vandaag = date.today().isoformat()
    for v in gemeld:
        for s in v["sleutels"]:
            gezien[s] = vandaag
    grens = (date.today() - timedelta(days=BEWAAR_DAGEN)).isoformat()
    staat["gezien"] = {s: d for s, d in gezien.items() if d >= grens}
    bewaar_json(gezien_pad, staat)

    if actief and len(fouten) == actief:
        return 1  # alle feeds kapot: laat de run mislukken zodat je een seintje krijgt
    return 0


if __name__ == "__main__":
    sys.exit(main())
