# job-agent

Ein **lokaler** Bewerbungs- und Job-Matching-Agent. Er bewertet Jobs aus einer
CSV-Datei gegen dein Profil, speichert die Ergebnisse in SQLite, schreibt eine
`matches.csv` und erzeugt einfache Bewerbungs-**Entwürfe**. Optional lässt er
sich über einen Telegram-Bot steuern.

## Wichtige Grundsätze (Sicherheit)

- ❌ **Kein Scraping** von Indeed, LinkedIn oder anderen Plattformen.
- ❌ **Keine automatischen Bewerbungen** – es wird nichts versendet.
- ❌ **Keine Captcha-Umgehung.**
- ✅ Version 1 arbeitet komplett **lokal** mit `data/jobs.csv`.
- ✅ Version 2 (Telegram) speichert **nur lokale Status** – ebenfalls ohne Versand.
- ✅ Möglichst **Python Standard Library**. PyYAML ist optional (Fallback eingebaut),
  der Telegram-Bot nutzt `urllib` – kein Zusatzpaket nötig.

## Projektstruktur

```
job-agent/
├─ README.md
├─ requirements.txt
├─ .env.example
├─ .gitignore
├─ config/
│  ├─ profile.yaml        # dein Profil (Skills, Rollen, Standorte, Projekte)
│  └─ search.yaml         # Schwellwerte & Punktegewichte (optional)
├─ data/
│  ├─ jobs.csv            # Eingabe: deine Jobliste
│  └─ job_agent.sqlite    # wird automatisch erzeugt
├─ output/
│  ├─ matches.csv         # Ergebnis der Bewertung
│  └─ applications/       # generierte Bewerbungs-Entwürfe (.md)
├─ src/
│  ├─ main.py             # CLI-Einstieg
│  ├─ config_loader.py    # YAML/.env laden
│  ├─ matcher.py          # Scoring-Logik
│  ├─ database.py         # SQLite-Persistenz
│  ├─ application_generator.py
│  ├─ telegram_bot.py     # Telegram-Bot (urllib, Long-Polling)
│  └─ models.py           # Datenmodelle
└─ tests/
   └─ test_matcher.py
```

## Voraussetzungen

- Python 3.10+ (getestet mit 3.11)
- Optional: `pip install -r requirements.txt` (PyYAML)

## Schnellstart

Alle Befehle aus dem Ordner `job-agent/` ausführen.

```bash
# 1. (optional) Abhängigkeiten installieren
pip install -r requirements.txt

# 2. Jobs bewerten -> SQLite + output/matches.csv
python src/main.py match

# 3. Beste Matches anzeigen
python src/main.py top

# 4. Bewerbungs-Entwürfe (Score >= 75) erzeugen
python src/main.py generate

# 5. (optional) Telegram-Bot starten – nur mit Token
python src/main.py telegram
```

> Unter Windows mit PowerShell funktionieren dieselben Befehle.

## CLI-Befehle

| Befehl                        | Wirkung                                                        |
|-------------------------------|----------------------------------------------------------------|
| `python src/main.py match`    | Lädt Profil + Jobs, bewertet, schreibt DB und `matches.csv`.   |
| `python src/main.py top`      | Zeigt die besten Matches (Default 10, `--limit N`).            |
| `python src/main.py generate` | Erstellt Entwürfe für Jobs ab 75 Punkten in `output/applications/`. |
| `python src/main.py package`  | Erstellt fertige Bewerbungs**pakete** (`--id <id>` oder `--min-score N`). |
| `python src/main.py import-email` | Importiert Indeed-Alert-Mails per IMAP (`--dry-run` zum Testen). |
| `python src/main.py telegram` | Startet den Telegram-Bot (nur wenn ein Token gesetzt ist).     |

## Eingabe: `data/jobs.csv`

Spalten: `id, title, company, location, link, description`.
Trage deine Jobs einfach von Hand ein (Copy & Paste aus Stellenanzeigen).
Beispieldaten sind bereits enthalten.

## Bewertung (Matching)

Score von **0–100**. Empfehlung:

| Score    | Empfehlung |
|----------|------------|
| ≥ 85     | Sehr gut   |
| ≥ 75     | Gut        |
| ≥ 60     | Prüfen     |
| < 60     | Ablehnen   |

**Pluspunkte:** Junior-Rolle, Kern-Tech (DevOps/Cloud/Kubernetes/Docker/AWS/CI/CD/
Terraform/Helm), Angular/Laravel, passender Standort (Remote/NRW/Deutschland),
Quereinsteiger-/Projektfreundlichkeit, Erwähnung von GitHub/Portfolio/Linux/
Automatisierung.

**Minuspunkte:** Senior zwingend, 5+ (bzw. 3+) Jahre Erfahrung, abgeschlossenes
Studium zwingend, abgeschlossene Ausbildung ohne Alternative, Fremd-Stack
(z. B. SAP ABAP, Embedded C++), reine Support-/Helpdesk-Stelle, reine
Netzwerkadministration ohne Cloud/DevOps.

Die Gewichte und Schwellwerte stehen in `config/search.yaml` und lassen sich
ohne Code-Änderung anpassen. Fehlt die Datei, gelten die Defaults aus
`src/matcher.py`.

## Ausgabe: `output/matches.csv`

Spalten: `id, title, company, location, link, score, recommendation,
positive_reasons, negative_reasons, skills_to_emphasize, cover_letter_hint`.

## Bewerbungs-Entwürfe

`python src/main.py generate` erzeugt pro Job ab 75 Punkten eine Markdown-Datei
in `output/applications/`. Das sind **Entwürfe zum manuellen Prüfen** – es wird
nichts automatisch verschickt.

## Bewerbungspaket (manuelles Hochladen)

`python src/main.py package` (oder der Telegram-Button „✅ Bewerben vorbereiten")
legt pro Job einen fertigen Ordner an:

```
output/applications/<job-id>/
├─ Lebenslauf.pdf      (Kopie aus src/pdf/, falls vorhanden)
├─ Zeugnisse.pdf       (Kopie aus src/pdf/, falls vorhanden)
├─ Anschreiben.md      (KI- oder lokaler Entwurf)
└─ README.txt          (Link + Hinweis: manuell hochladen)
```

Beispiele:
```bash
python src/main.py package --id 1     # nur Job 1
python src/main.py package            # alle Jobs ab Score 75
```

> **Wichtig / Sicherheitsgrenze:** Der Agent **meldet sich NICHT bei Indeed an
> und sendet KEINE Bewerbung**. Er bereitet nur die Dateien vor. Du öffnest den
> Link selbst, lädst die Dateien hoch und klickst selbst auf Absenden. Das ist
> bewusst so (Indeed verbietet Automatisierung; du behältst die Kontrolle).

Die persönlichen PDFs liegen in `src/pdf/` (per `.gitignore` ausgeschlossen) und
werden nur kopiert – Dateinamen `Lebenslauf*.pdf` und `Zeugnis*.pdf`.

## OpenAI (optional) – intelligente Analyse

OpenAI wird bewusst **einfach** genutzt: Lebenslauf, Zeugnisse und die
Stellenbeschreibung werden als **ein gemeinsamer Kontext** geschickt. OpenAI
**ersetzt das lokale Scoring nicht** – es ergänzt nur Erklärung und Texte.
**Ohne Key läuft alles lokal weiter** – ohne Fehler.

### CV & Zeugnis als Kontext

Trage deine echten Infos als Freitext ein (keine feste Struktur nötig):

- `config/cv.md` – dein Lebenslauf
- `config/zeugnis.md` – Zeugnisse / Qualifikationen

Beide Dateien sind in `.gitignore` ausgenommen (persönliche Daten) und werden
**nur als Kontext** an OpenAI geschickt – nichts wird versendet.

### Einrichten

1. (Einmalig) Paket installieren: `pip install openai`
2. In `.env` eintragen:
   ```
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-5.5-mini
   ```
   `OPENAI_MODEL` ist optional (Default: `gpt-5.5-mini`).
3. `config/cv.md` und `config/zeugnis.md` mit deinen Daten füllen.

### Was OpenAI macht

- **Analyse** (`analyze_job`): aus CV + Zeugnis + Stelle → *passt / passt nicht*,
  kurze Begründung (max. 5 Punkte), wichtigste Job-Skills, kurze Empfehlung.
  Im Telegram-Bot erscheint die Analyse zusätzlich zum lokalen Score in der
  Detailansicht (`/job <id>` bzw. Button „📄 Details").
- **Anschreiben** (`generate_cover_letter_with_ai`): personalisiertes deutsches
  Anschreiben auf Basis von CV + Zeugnis + Stelle (Button „✅ Bewerben
  vorbereiten" oder `python src/main.py generate`).

### Was OpenAI NICHT macht

- **Versendet keine Bewerbungen** – es entstehen nur Dateien in
  `output/applications/`.
- Ersetzt nicht das lokale Scoring; kein Scraping, keine Auto-Bewerbung,
  keine Captcha-Umgehung.
- Der **API-Key wird nie ausgegeben oder geloggt**.
- Fällt der Aufruf aus (kein Key, kein Paket, Netzwerkfehler), greift **ohne
  Abbruch** die lokale Bewertung / der lokale Generator.

## Telegram-Bot (Version 2)

1. Bot bei [@BotFather](https://t.me/BotFather) anlegen und Token kopieren.
2. `.env.example` nach `.env` kopieren und ausfüllen:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=123456789
   ```
   - **Ohne Token** startet der Bot nicht – das übrige Programm funktioniert
     trotzdem normal.
   - Ist `TELEGRAM_CHAT_ID` gesetzt, reagiert der Bot **nur** auf diese Chat-ID.
3. Starten: `python src/main.py telegram`
   (Ohne gültigen Token startet der Bot nicht und das Programm endet sauber.)

### Bot-Befehle

| Befehl          | Wirkung                                   |
|-----------------|-------------------------------------------|
| `/start`        | Hilfe anzeigen                            |
| `/top`          | beste Matches – je Job mit Buttons        |
| `/job <id>`     | Details zu einem Job – mit Buttons        |
| `/apply <id>`   | Status → **bewerben**                     |
| `/done <id>`    | Status → **beworben**                     |
| `/reject <id>`  | Status → **abgelehnt**                    |
| `/later <id>`   | Status → **später**                       |
| `/status`       | Übersicht der gespeicherten Status        |

### Inline-Buttons (InlineKeyboard)

Bei `/top` erscheint **unter jedem Job** eine Button-Reihe, bei `/job <id>`
**unter den Details**:

| Button                       | Callback-Daten   | Aktion                                                                 |
|------------------------------|------------------|-----------------------------------------------------------------------|
| 📄 Details                   | `details:<id>`   | zeigt dieselben Details wie `/job <id>`                                |
| ✅ Bewerben vorbereiten      | `apply:<id>`     | erstellt/aktualisiert den Entwurf in `output/applications/`, Status → **bewerben** |
| ⏳ Später                    | `later:<id>`     | Status → **später**                                                   |
| ❌ Ablehnen                  | `reject:<id>`    | Status → **abgelehnt**                                                |
| ✅ Als beworben markieren    | `done:<id>`      | Status → **beworben**                                                 |

Buttons von `/top`: 📄 Details · ✅ Bewerben vorbereiten · ⏳ Später · ❌ Ablehnen.
Buttons von `/job`: ✅ Bewerben vorbereiten · ⏳ Später · ❌ Ablehnen · ✅ Als beworben markieren.

Nach jedem Klick antwortet der Bot kurz, z. B. `Status gesetzt: später` oder
`✅ Bewerbungsentwurf erstellt`.

### Sicherheitsgrenze

Der Bot **zeigt nur lokale Jobs an und ändert nur lokale Status** in der
SQLite-Datenbank bzw. erzeugt lokale Bewerbungs-**Entwürfe**. Er **versendet
keine Bewerbungen**, macht kein Scraping und umgeht keine Captchas. „Bewerben
vorbereiten" heißt ausdrücklich: Entwurf vorbereiten, **nicht** absenden.

## Indeed-Job-Alerts per E-Mail importieren

Statt Jobs von Hand einzutragen, kann der Agent **Indeed-Job-Alert-Mails** aus
deinem eigenen Postfach lesen (IMAP), die Jobs extrahieren, bewerten und passende
Treffer an Telegram schicken.

> **Wichtig:** Es wird **kein Indeed gescraped und kein Indeed-Login** verwendet.
> Der Agent liest **nur dein eigenes Postfach**, löscht nichts und versendet keine
> Bewerbung.

### 1. Indeed-Job-Alert einrichten
1. Bei Indeed eine Jobsuche speichern und **Job-Alert per E-Mail** aktivieren
   (z. B. „Junior DevOps NRW"). Indeed schickt dir dann regelmäßig Alert-Mails.

### 2. IMAP-Zugang in `.env` eintragen
```
EMAIL_IMAP_HOST=          # leer lassen = wird aus der E-Mail-Domain erraten
EMAIL_IMAP_PORT=993
EMAIL_USERNAME=deine@mail.de
EMAIL_PASSWORD=            # App-Passwort (siehe unten), NICHT dein Login-Passwort
EMAIL_FOLDER=INBOX
EMAIL_FROM_FILTER=indeed   # nur Mails von Indeed
EMAIL_LOOKBACK_DAYS=3      # nur Mails der letzten N Tage
EMAIL_MIN_SCORE_FOR_OPENAI=60     # OpenAI-Analyse erst ab diesem Score (Kosten sparen)
EMAIL_MIN_SCORE_FOR_TELEGRAM=75   # Telegram-Push erst ab diesem Score
```

**Gmail / Outlook (mit 2FA):** das normale Passwort funktioniert nicht. Du
brauchst ein **App-Passwort**:
- Gmail: Google-Konto → Sicherheit → App-Passwörter. Host wird zu `imap.gmail.com`.
- Outlook/Hotmail: Microsoft-Konto → Sicherheit → App-Passwörter. Host wird zu
  `outlook.office365.com`.
- Generischer IMAP-Server: `EMAIL_IMAP_HOST` selbst setzen.

### 3. Importieren
```bash
python src/main.py import-email --dry-run   # liest + zeigt Treffer, speichert nichts
python src/main.py import-email             # speichert + benachrichtigt
```
Ablauf: ungelesene Indeed-Mails → Jobs extrahieren → **deduplizieren** → lokaler
Score → **OpenAI-Analyse nur ab `EMAIL_MIN_SCORE_FOR_OPENAI`** → Telegram-Push
**nur ab `EMAIL_MIN_SCORE_FOR_TELEGRAM`** (mit Buttons 📄 ✅ ⏳ ❌). Neue Jobs
landen in SQLite und `data/jobs.csv`. Erfolgreich verarbeitete Mails werden als
**gelesen** markiert (nie gelöscht).

**Kostenhinweis OpenAI:** Die KI-Analyse läuft bewusst nur für **neue** Jobs ab
dem lokalen Schwellwert – so zahlst du nicht für offensichtlich unpassende oder
bereits bekannte Treffer. Ohne OpenAI-Key läuft der Import komplett lokal weiter.

### Automatischer Import im laufenden Bot (Option A)

Statt `import-email` von Hand zu starten, kann der **laufende Telegram-Bot** das
Postfach selbst in Intervallen prüfen und neue Treffer pushen. In `.env`:
```
EMAIL_AUTO_IMPORT_ENABLED=true
EMAIL_AUTO_IMPORT_INTERVAL_MINUTES=30
EMAIL_AUTO_IMPORT_INCLUDE_SEEN=false
```
Dann nur den Bot starten:
```
python src/main.py telegram
```
Beim Start zeigt der Bot „E-Mail-Auto-Import aktiv: ja | Intervall: 30 Minuten".
Ab dann holt er alle 30 Minuten neue Indeed-Mails, bewertet sie, dedupliziert
(keine Doppelimporte) und pusht passende Jobs nach Telegram – ohne dass du etwas
eintippst. Fehler (IMAP/OpenAI/Telegram) werden geloggt, der Bot **läuft weiter**.

Voraussetzungen, damit es automatisch läuft:
- **Dein PC muss eingeschaltet sein.**
- **Der Telegram-Bot muss laufen** (`python src/main.py telegram`) – schließt du
  das Fenster, stoppt auch der Auto-Import (und die Buttons).
- `INCLUDE_SEEN=false` (Standard) verarbeitet nur **ungelesene** Mails; `true`
  bezieht auch gelesene ein (bereits bekannte Jobs werden trotzdem dedupliziert).

## Tests

```bash
python -m unittest discover -s tests
# oder, falls pytest installiert ist:
pytest
```

## Dein Profil anpassen

Bearbeite `config/profile.yaml` (Zielrollen, Skills, Projekte, Standorte).
Diese Werte fließen in das Matching und in die Entwürfe ein.

### Echte Identität lokal halten (Name/E-Mail)

`config/profile.yaml` ist als **öffentliches Template** mit Platzhaltern gedacht
(`name: Max Mustermann`, `email: example@example.com`). Deine **echten** Daten
gehören in eine lokale, **git-ignorierte** Datei:

```bash
cp config/profile.local.example.yaml config/profile.local.yaml
# dann config/profile.local.yaml mit echtem Namen + E-Mail füllen
```

Beim Start mischt der Job-Agent `profile.local.yaml` über `profile.yaml`
(lokale Werte gewinnen). So erscheinen in lokal erzeugten Anschreiben deine
echten Daten, während im Repo nur Platzhalter liegen. `profile.local.yaml`
steht in `.gitignore` und wird nie committet.

## Erweiterungs-Ideen (später)

- Echte Stellenquellen über **offizielle APIs** statt Scraping anbinden.
- Mehr Status / Erinnerungen / Fristen.
