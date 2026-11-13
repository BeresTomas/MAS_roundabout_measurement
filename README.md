# Merač dopravných udalostí z videa (kruhový objazd)

Desktopová aplikácia v Pythone (PySide6) na ručné zaznamenávanie dopravných udalostí
z videa klávesnicou. Čas udalosti sa berie z aktuálnej pozície videa (presnosť 0,1 s).
Výstupom sú tri CSV súbory pre Excel.

## Rýchly štart

1. Nainštalujte Python 3.10+ (<https://www.python.org/downloads/>, na Windows zaškrtnite *Add to PATH*).
2. Stiahnite projekt a nainštalujte jedinú závislosť:
   ```bash
   git clone https://github.com/BeresTomas/MAS_roundabout_measurement.git
   cd MAS_roundabout_measurement
   pip install pyside6
   ```
3. Spustite aplikáciu:
   ```bash
   python app.py
   ```
4. V dialógu vyberte video, číslo meraného ramena a stlačte **Spustiť**.
   Medzerník = príchod, Enter = vjazd, M = režim smerov (1–4), Backspace = späť, Ctrl+S = export CSV.

## Inštalácia a spustenie (podrobne)

```bash
pip install pyside6
python app.py
```

Vyžaduje Python 3.10+. Jediná závislosť je PySide6 (prehrávanie cez QtMultimedia –
na Linuxe potrebuje FFmpeg knižnice, ktoré sú súčasťou balíka PySide6; na Windows/macOS
sa použijú systémové kodeky alebo FFmpeg z PySide6).

## Nastavenie relácie (dialóg pri štarte)

- **Video** – výber súboru.
- **Merané rameno** – číslo vjazdu 1–4.
- **Pozorovateľ** – voliteľné.
- **Skutočný čas začiatku videa** – voliteľné `HH:MM:SS`; ak je zadaný, v exporte je aj
  stĺpec `cas_realny`.
- **Názvy ramien** – voliteľné pomenovanie ramien 1–4 (napr. ulica alebo smer). Názvy sú
  spoločné pre celé video: pri ďalšej relácii nad tým istým videom sa predvyplnia z autosave.
  Zmeniť sa dajú aj počas práce v menu *Nastavenia → Názvy ramien…*. Zobrazujú sa na paneli
  a v CSV pribudnú stĺpce `rameno_nazov`, `ciel_rameno_nazov`, `rameno_z_nazov`, `rameno_na_nazov`
  (číselné stĺpce zostávajú).
- **Formát CSV** – slovenský Excel (bodkočiarka + desatinná čiarka, predvolené) alebo
  čiarka + desatinná bodka. Dá sa prepnúť aj neskôr v menu *Nastavenia*.
- **Pokračovať v rozpracovanej relácii** – ak vedľa videa existuje autosave
  `<video>_rameno<k>.json`, zaškrtávacie pole sa sprístupní (predvolene zapnuté).
  Po spustení sa video posunie na čas poslednej udalosti.
  Ak pole odškrtnete, starý autosave sa premenuje na `*.json.<dátum>.bak` a začne sa nová relácia.

Jedna relácia = jedno video + jedno rameno.

## Ovládanie prehrávača

| Kláves | Funkcia |
|---|---|
| `P` | play / pauza |
| `←` / `→` | −1 s / +1 s |
| `Shift+←` / `Shift+→` | −5 s / +5 s |
| `,` / `.` | snímok späť / vpred (podľa fps videa, inak ±0,04 s) |
| `[` / `]` | rýchlosť 0,25× · 0,5× · 1× · 1,5× · 2× |

Medzerník prehrávanie **neovláda**. Pod videom je posuvník, čas videa (0,1 s) a rýchlosť.

## Režim „Časy“ (predvolený)

| Kláves | Udalosť |
|---|---|
| `MEDZERNÍK` | **PRÍCHOD** – nové auto s ďalším ID, zaradí sa na koniec frontu; uloží sa `n_pred` (počet áut vo fronte v tom momente) |
| `ENTER` | **VJAZD** do kruhu – najstaršie auto vo fronte dostane `t_vjazd`; pri prázdnom fronte sa nič neuloží a zobrazí sa upozornenie |
| `X` | začiatok / koniec **VYLÚČENÉHO ÚSEKU** (prepínač); počas úseku je nad videom červený pruh; autá, ktoré prišli v úseku, majú `vylucene = 1` |
| `N` | poznámka s časom |
| `BACKSPACE` | undo poslednej udalosti (opakovateľne) |
| `M` | prepnutie do režimu Smery |
| `Ctrl+S` | export CSV |

V režime Časy sú klávesy `1`–`4` vypnuté.

## Režim „Smery“

| Kláves | Udalosť |
|---|---|
| `1` `2` `3` `4` | auto z meraného ramena išlo na výjazd 1 / 2 / 3 / 4 (vrátane otočenia) |
| `BACKSPACE` | undo |
| `M` | späť do režimu Časy |

Na paneli sa priebežne zobrazujú počty a percentá pre každý výjazd.
V režime Smery sú Medzerník a Enter vypnuté.

## Ako sa počíta stav

Udalosti sa ukladajú do zoznamu v poradí zaznamenania. Stav (front, ID áut, `n_pred`,
`cakanie_s`, vylúčené úseky) sa vždy **prepočíta od začiatku zo zoznamu udalostí
zoradených podľa času videa**. Preto:

- ak pretočíte video dozadu a zaznamenáte udalosť, zaradí sa správne podľa času
  (zobrazí sa nenápadné varovanie v stavovom riadku);
- undo iba odstráni poslednú zaznamenanú udalosť a stav sa prepočíta;
- ID áut zodpovedajú poradiu príchodov podľa času videa.

Ak po pretočení alebo undo zostane VJAZD, pre ktorý vo fronte nie je auto, zobrazí sa
na paneli červené počítadlo „VJAZD bez auta vo fronte“ a v exporte má taká udalosť prázdne `auto_id`.

## Ukladanie a export

- **Autosave** – po každej udalosti sa celá relácia (nastavenia + udalosti) uloží do
  `<video>_rameno<k>.json` vedľa videa (atomicky, cez dočasný súbor). Pri páde programu
  sa nič nestratí; pri ďalšom spustení zvoľte „Pokračovať v rozpracovanej relácii“.
- **Export** (tlačidlo alebo `Ctrl+S`) vytvorí vedľa videa tri CSV súbory:

  1. `<video>_rameno<k>_udalosti.csv` –
     `event_id; typ; cas_video_s; cas_realny; rameno; auto_id; ciel_rameno; poznamka; rameno_nazov; ciel_rameno_nazov`
     (typ: `PRICHOD`, `VJAZD`, `VYLUC_ZACIATOK`, `VYLUC_KONIEC`, `POZNAMKA`, `SMER`)
  2. `<video>_rameno<k>_auta.csv` – jeden riadok = jedno auto:
     `auto_id; rameno; t_prichod_s; n_pred; t_vjazd_s; cakanie_s; vylucene; interval_od_predch_s; rameno_nazov`
     – `cakanie_s` je prázdne, ak auto nevošlo; `interval_od_predch_s` je prázdne pre prvé
     auto a pre autá, kde ono alebo predchodca je vo vylúčenom úseku.
  3. `<video>_rameno<k>_smery.csv` –
     `interval_od_s; rameno_z; rameno_na; pocet; podiel; rameno_z_nazov; rameno_na_nazov` – riadky „spolu“ a potom
     rovnaké počty po 15-minútových intervaloch času videa (`interval_od_s` = 0, 900, 1800, …).

- CSV je v UTF-8 s BOM; predvolene bodkočiarka a desatinná čiarka (slovenský Excel sa
  otvorí dvojklikom správne). Alternatíva: čiarka + desatinná bodka.
  V LibreOffice Calc pri importe nechajte ako oddeľovač iba *Bodkočiarku* (odškrtnite *Čiarku*)
  a nastavte *Locale* na slovenčinu, inak sa desatinné čiarky rozdelia do ďalších stĺpcov.
- Pri zatvorení okna s neexportovanými zmenami aplikácia ponúkne export.

## Overenie logiky bez okna

Logika (triedy `Session`, `compute_state`) nezávisí od GUI. Rýchla kontrola na 5 autách:

```bash
QT_QPA_PLATFORM=offscreen python -c "
import app
s = app.Session('test.mp4', 1)
for t in (1.0, 2.0, 3.5): s.add_event(app.T_PRICHOD, t)
s.add_event(app.T_VJAZD, 5.0)
s.add_event(app.T_PRICHOD, 6.0)
s.add_event(app.T_VJAZD, 7.0)
st = s.state()
for c in st.cars: print(c.auto_id, c.t_prichod, c.n_pred, c.t_vjazd, c.cakanie)
"
```
