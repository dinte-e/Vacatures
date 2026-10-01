# Vacature-alert

Elke ochtend automatisch een mail (en/of Telegram-bericht) met nieuwe vacatures die passen bij de zoektocht: educatie, journalistiek, welzijn en persoonlijke ontwikkeling, en maatschappelijk werk met impact. Elke vacature wordt maar één keer gemeld, ook als hij op meerdere sites staat.

**Hoe het werkt:** grote vacaturesites zoals Indeed en LinkedIn bieden geen open feed die je automatisch kunt uitlezen. Google Alerts pikt nieuwe vacaturepagina's van al die sites wél op en levert ze als RSS-feed. Het script haalt die feeds (plus vacaturesites met een eigen feed) dagelijks op, filtert op de zoektermen in `config.json`, gooit nieuwsberichten, senior-, stage- en leidinggevende functies eruit en stuurt de rest door.

Instellen kost ongeveer 20 minuten.

---

## Stap 1 · Google Alerts aanmaken

In `config.json` staan 12 zoekopdrachten klaar (bij `google_zoekopdracht`). Maak voor elk een alert:

1. Ga naar **google.com/alerts** en log in met een Google-account.
2. Plak de zoekopdracht in het zoekveld en klik op **Opties weergeven**.
3. Kies: *Hoe vaak:* maximaal één keer per dag · *Taal:* Nederlands · *Regio:* Nederland · *Hoeveel:* alle resultaten · **Bezorgen aan: RSS-feed**.
4. Klik **Melding maken**. Klik daarna op het RSS-icoontje naast de alert en kopieer de link.
5. Plak die link in `config.json` op de plek van `PLAK_HIER_DE_RSS_LINK` bij de bijbehorende feed.

De zoekopdrachten zijn:

```
vacature "student wellbeing officer" OR studentenwelzijn OR "student welzijn"
vacature museum "medewerker educatie" OR educator OR museumdocent
vacature onderwijsontwikkelaar OR "e-learning ontwikkelaar" OR onderwijskundige
vacature opleidingsontwikkelaar OR "learning & development" OR "learning designer"
vacature mediawijsheid OR "digitale geletterdheid"
vacature stichting programmamedewerker OR projectmedewerker OR projectcoördinator
vacature impactadviseur OR "maatschappelijke impact" OR "sociale onderneming"
vacature "junior redacteur" OR redactiemedewerker OR "jonge journalist"
vacature adviseur laaggeletterdheid OR basisvaardigheden
vacature yoga OR mindfulness OR vitaliteit coördinator OR trainer OR docent
"zij-instroom" vacature docent
traineeship maatschappelijk OR onderwijs OR overheid aanmelden
```

Een feed die je (nog) niet invult wordt gewoon overgeslagen. Je kunt ook feeds toevoegen, bijvoorbeeld een alert die één site volgt: `site:werkenvoornederland.nl onderwijs OR cultuur`.

## Stap 2 · Zoektermen naar wens aanpassen (optioneel)

Alles staat in `config.json`:

- **`zoektermen`**: woorden waarop vacaturesites met een brede feed (zoals Culturele vacatures) gefilterd worden. Een `*` aan het eind betekent "begint met": `educati*` vindt ook *educatief* en *educatieve*. Hoofdletters en accenten maken niet uit.
- **`uitsluiten_als_in_titel`**: vacatures met deze woorden in de titel worden overgeslagen (senior, stage, directeur, enz.).
- **`voorkeursplaatsen`**: vul bijvoorbeeld `["Utrecht", "Amersfoort", "thuiswerk*"]` in. Vacatures die een van deze plaatsen noemen komen bovenaan met een 📍. Er wordt níet op gefilterd, je mist dus niets.
- **`filter`** per feed: `"titel"` (zoektermen in de titel), `"alles"` (ook in de beschrijving) of `"geen"` (alles doorlaten, handig voor Google Alerts die al specifiek zijn).

De feed van Culturele vacatures (`https://www.culturele-vacatures.nl/feed/`) heb ik niet live kunnen testen. Controleer hem met `--test-feeds` (zie stap 5); werkt hij niet, zet dan `"actief": false` erbij.

## Stap 3 · Meldingen instellen

**E-mail via Gmail** (aanrader):

1. Zet op het Google-account dat de mail verstuurt *Verificatie in twee stappen* aan.
2. Maak een app-wachtwoord via **myaccount.google.com/apppasswords**.
3. Je hebt dan nodig: `SMTP_USER` = het Gmail-adres, `SMTP_PASSWORD` = het app-wachtwoord (16 tekens), `MAIL_TO` = één of meer ontvangers, gescheiden door komma's.

Ander mailaccount? Vul dan ook `SMTP_HOST` en `SMTP_PORT` in (465 voor SSL, 587 voor STARTTLS).

**Telegram** (optioneel, kan naast of in plaats van e-mail):

1. Stuur in Telegram een bericht naar **@BotFather**, typ `/newbot` en volg de stappen. Je krijgt een token: dat is `TELEGRAM_BOT_TOKEN`.
2. Stuur je nieuwe bot een willekeurig bericht en open `https://api.telegram.org/bot<TOKEN>/getUpdates`. Het getal bij `"chat":{"id":` is `TELEGRAM_CHAT_ID`.

## Stap 4a · Gratis laten draaien via GitHub (aanrader)

1. Maak een (gratis) account op **github.com** en een nieuwe **private** repository, bijvoorbeeld `vacature-alert`.
2. Upload alle bestanden uit de zip (*Add file → Upload files*).
   Let op: de map `.github` is op een Mac standaard verborgen. Lukt uploaden niet, kies dan *Add file → Create new file*, typ als naam `.github/workflows/vacature-alert.yml` en plak de inhoud van dat bestand erin.
3. Ga naar *Settings → Secrets and variables → Actions → New repository secret* en voeg toe: `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` (en eventueel `SMTP_HOST`, `SMTP_PORT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
4. Ga naar het tabblad **Actions**, kies *Vacature-alert → Run workflow* en kies modus **test-melding**. Komt de testmail binnen? Dan werkt alles.

Vanaf nu draait het elke ochtend rond 8:00 vanzelf. Wil je een ander tijdstip, pas dan de `cron`-regel in het workflow-bestand aan (tijd in UTC).

Goed om te weten: GitHub pauzeert geplande taken als er 60 dagen niets in de repository verandert. Dat gebeurt hier zelden, omdat het script bij elke nieuwe vacature `gezien.json` bijwerkt. Mocht het toch gebeuren, dan krijg je een mail van GitHub en zet je het met één klik weer aan. Mislukt de stap "Onthoud gemelde vacatures", zet dan onder *Settings → Actions → General → Workflow permissions* de optie *Read and write permissions* aan.

## Stap 4b · Of op een eigen server

Python 3.9 of nieuwer is genoeg, er zijn geen extra pakketten nodig.

1. Zet de map op de server en maak er een bestand `.env` in:
   ```
   SMTP_USER=jouwadres@gmail.com
   SMTP_PASSWORD=jouwapp-wachtwoord
   MAIL_TO=jij@voorbeeld.nl, haar@voorbeeld.nl
   ```
2. Voeg met `crontab -e` toe:
   ```
   0 8 * * * cd /pad/naar/vacature-alert && /usr/bin/python3 vacature_alert.py >> alert.log 2>&1
   ```

## Stap 5 · Testen en handige opdrachten

```
python vacature_alert.py --test-melding   testbericht versturen
python vacature_alert.py --test-feeds     per feed zien of hij werkt en wat erin past
python vacature_alert.py --droog          tonen wat gemeld zou worden, zonder te versturen
python vacature_alert.py --stil           alles wat er nu staat als gezien markeren
python vacature_alert.py                  normale run
```

Op GitHub kies je deze modi bij *Run workflow*.

## Goed om te weten

- Als versturen mislukt, worden de vacatures niet als gezien gemarkeerd. Ze komen de volgende dag gewoon opnieuw.
- Als alle feeds tegelijk niet werken, mislukt de run en stuurt GitHub je daarover een mail.
- Werkt een enkele feed niet, dan staat dat onderaan de melding.
- Komt er veel ruis binnen? Voeg woorden toe aan `uitsluiten_als_in_titel` of maak een Google Alert specifieker.
- Aanvullend: de meeste grote vacaturesites (Indeed, LinkedIn, Werken voor Nederland, AcademicTransfer) hebben ook een eigen e-mailalert. Die kun je er prima naast gebruiken.
