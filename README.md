# Merač dopravných udalostí z videa (kruhový objazd)

Desktopová aplikácia v Pythone (PySide6) na ručné zaznamenávanie dopravných udalostí
z videa klávesnicou. Čas udalosti sa berie z aktuálnej pozície videa (presnosť 0,1 s).
Výstupom sú tri CSV súbory pre Excel.

## Rýchly štart

Funguje na **Windows, Linuxe aj macOS**. Potrebujete iba Python 3.10 alebo novší.

### Windows

1. Nainštalujte Python z <https://www.python.org/downloads/> a pri inštalácii zaškrtnite
   **„Add python.exe to PATH“**. (Verzie 3.10–3.13 sú overené; Python zo služby Microsoft Store
   tiež funguje.)
2. Stiahnite projekt: na GitHube **Code → Download ZIP** a rozbaľte, alebo
   `git clone https://github.com/BeresTomas/MAS_roundabout_measurement.git`.
3. Dvakrát kliknite na **`start.bat`**. Pri prvom spustení si skript sám vytvorí priečinok
   `.venv` a stiahne PySide6 (cca 150 MB, chvíľu to trvá). Ďalšie spustenia sú okamžité.

Ručne bez skriptu (v príkazovom riadku v priečinku projektu):

```bat
py -m pip install -r requirements.txt
py app.py
```

### Linux / macOS

```bash
git clone https://github.com/BeresTomas/MAS_roundabout_measurement.git
cd MAS_roundabout_measurement
./start.sh
```

alebo ručne `python3 -m pip install -r requirements.txt` a `python3 app.py`.

### Potom

V dialógu vyberte video 1 (koniec radu), voliteľne video 2 (kruhový objazd), merané rameno
a stlačte **Spustiť**. Medzerník = príchod, 1–4 = výjazd na rameno 1–4, Tab = iné auto pre výjazd,
N = typ vozidla, X = vylúčený úsek, Backspace = späť, Ctrl+S = export CSV.

## Riešenie problémov

- **„pip“ sa nenašiel (pip is not recognized)** – nepíšte `pip install …`, ale
  `py -m pip install -r requirements.txt` (na Linuxe/macOS `python3 -m pip …`).
  Najjednoduchšie je nepísať nič a spustiť `start.bat`, ktorý inštaláciu spraví sám.
  Príkazy sa píšu do príkazového riadku (cmd / PowerShell), nie do okna Pythonu s `>>>`.
- **„python“ sa nenašiel / otvorí sa Microsoft Store** – Python nie je v PATH. Použite `py`
  namiesto `python`, alebo preinštalujte Python so zaškrtnutým „Add python.exe to PATH“.
- **`pip install pyside6` zlyhá** – skontrolujte verziu Pythonu (`py --version`). Pre úplne
  novú verziu Pythonu nemusí byť PySide6 ešte vydané; nainštalujte Python 3.12 alebo 3.13.
- **Okno sa otvorí, ale video je čierne alebo sa neprehrá** – chýbajú kodeky prehrávača.
  Aplikácia na Windows používa FFmpeg zabudovaný v PySide6. Ak nepomôže, skúste druhý
  backend: v príkazovom riadku `set QT_MEDIA_BACKEND=windows` a potom `py app.py`
  (na Linuxe `QT_MEDIA_BACKEND=gstreamer python3 app.py`). Pomôže aj prekonvertovať video
  na H.264 MP4 (napr. v aplikácii HandBrake).
- **Aplikácia spadne** – zobrazí sa okno s chybou a podrobnosti sa zapíšu do súboru
  `merac_error.log` vedľa `app.py`. Pošlite ho autorovi. Zaznamenané udalosti sú vždy
  v autosave súbore vedľa videa, nič sa nestratí.
- **Klávesy nereagujú** – kliknite do okna aplikácie, aby malo fokus. Skratky fungujú
  v celom okne, nie je potrebné klikať na video.

## Čo sa meria

Pre každé auto z meraného ramena:

1. **rameno príchodu** – merané rameno relácie (napr. S),
2. **čas príchodu** – prejazd referenčným bodom (video 1, koniec radu),
3. **čas výjazdu** – prejazd čiarou výjazdu z kruhu (video 2, kruhový objazd),
4. **rameno výjazdu** – kam auto išlo (1–4, vrátane otočenia).

Občas: **vylúčené úseky** (keď rad siaha za referenčný bod) a **typ vozidla** pri inom vozidle
ako osobné auto (dodávka, kamión, autobus, motorka), aby sa dalo pri vyhodnotení vynechať.
Voliteľne ako poistka: **čas vjazdu do kruhu**.

Všetko sa zaznamenáva v jednom prechode videom.

## Nastavenie relácie (dialóg pri štarte)

- **Video 1 (príchody)** – video z konca radu, kde je referenčný bod. Jeho čas je hlavná
  časová os relácie (`cas_video_s` v exporte).
- **Video 2 (výjazdy)** – voliteľné video kruhového objazdu. Pridať alebo odobrať sa dá aj
  neskôr v menu *Súbor*.
- **Video 2 začalo neskôr o** – o koľko sekúnd neskôr bolo spustené video 2 než video 1
  (záporné = skôr). Stačí približne, presne sa doladí počas prehrávania (pozri nižšie).
- **Rameno príchodu (merané)** – 1–4. Jedna relácia = jedno merané rameno.
- **Pozorovateľ** – voliteľné.
- **Skutočný čas začiatku videa 1** – voliteľné `HH:MM:SS`; v exporte potom pribudnú
  reálne časy (`cas_realny`, `cas_prichodu_realny`, `cas_vyjazdu_realny`).
- **Názvy ramien** – voliteľné, napr. tlačidlom **Vyplniť S / V / J / Z** (1 = S, 2 = V,
  3 = J, 4 = Z) alebo názvami ulíc. Sú spoločné pre celé video, pri ďalšej relácii sa
  predvyplnia. Zmeniť sa dajú aj v menu *Nastavenia → Názvy ramien…*. V CSV je vedľa každého
  čísla ramena stĺpec s jeho názvom; čísla zostávajú kvôli výpočtom.
- **Formát CSV** – slovenský Excel (bodkočiarka + desatinná čiarka, predvolené) alebo
  čiarka + desatinná bodka. Dá sa prepnúť aj neskôr v menu *Nastavenia*.
- **Pokračovať v rozpracovanej relácii** – ak vedľa videa 1 existuje autosave
  `<video>_rameno<k>.json`, pole sa sprístupní (predvolene zapnuté). Načíta sa aj video 2
  a posun, video sa posunie na čas poslednej udalosti. Ak pole odškrtnete, starý autosave
  sa premenuje na `*.json.<dátum>.bak` a začne sa nová relácia.

## Dve videá a synchronizácia

Ak je zadané video 2, obe videá sa zobrazia vedľa seba (*Zobrazenie → Videá vedľa seba /
pod sebou*, `Ctrl+L`) a ovládajú sa spolu: play/pauza, pretáčanie, snímky aj rýchlosť
platia pre obe. Zvuk ide len z videa 1.

Video 2 sa drží na pozícii *čas videa 1 − posun*. Postup synchronizácie:

1. Pozastavte (`P`) video 1 v okamihu, ktorý je jednoznačne vidno aj na videu 2
   (napr. auto, ktoré je v zábere oboch kamier, alebo tlesknutie pri štarte).
2. Posúvajte len video 2 klávesmi `Ctrl+←` / `Ctrl+→` (o 0,1 s) alebo
   `Ctrl+Shift+←` / `Ctrl+Shift+→` (o 1 s), prípadne tlačidlami pod videom,
   kým video 2 neukazuje ten istý okamih. Posun je vidno v poli „video 2 začalo neskôr o“.

Výjazdy a vjazdy sa ukladajú **v čase videa 2** a na os videa 1 sa prepočítavajú posunom.
Ak teda zistíte, že synchronizácia bola o kúsok vedľa, stačí opraviť posun a všetky už
zaznamenané výjazdy sa opravia samy. Kým video 2 ešte nezačalo alebo už skončilo, berie sa
čas z videa 1 (v nadpise videa 2 je „ešte nezačalo“ / „skončilo“).

## Ovládanie prehrávača

| Kláves | Funkcia |
|---|---|
| `P` | play / pauza |
| `←` / `→` | −1 s / +1 s |
| `Shift+←` / `Shift+→` | −5 s / +5 s |
| `,` / `.` | snímok späť / vpred (podľa fps videa 1, inak ±0,04 s) |
| `[` / `]` | rýchlosť 0,25× · 0,5× · 1× · 1,5× · 2× |
| `↑` / `↓` | hlasitosť +10 % / −10 % |
| `Ctrl+←` / `Ctrl+→` | video 2 o 0,1 s späť / vpred (synchronizácia) |
| `Ctrl+Shift+←` / `Ctrl+Shift+→` | video 2 o 1 s späť / vpred |

Medzerník prehrávanie **neovláda**. Pod videom je posuvník, čas videa (0,1 s), rýchlosť
a vpravo tlačidlo stlmenia 🔊/🔇 s posuvníkom hlasitosti.

## Režim „Časy + výjazdy“ (predvolený)

| Kláves | Udalosť |
|---|---|
| `MEDZERNÍK` | **PRÍCHOD** – auto prešlo referenčným bodom; nové auto s ďalším ID, čas z videa 1 |
| `1` `2` `3` `4` | **VÝJAZD** – auto prešlo čiarou výjazdu na rameno 1–4; uloží sa čas aj rameno naraz |
| `Tab` / `Shift+Tab` | vybrať iné auto pre najbližší výjazd (predbiehanie v kruhu) |
| `Esc` | zrušiť výber, výjazd dostane opäť najstaršie auto |
| `N` | **typ vozidla / poznámka** – dodávka, kamión, autobus, motorka, iné; priradí sa k autu |
| `X` | začiatok / koniec **VYLÚČENÉHO ÚSEKU** (prepínač); autá, ktoré prišli v úseku, majú `vylucene = 1` |
| `ENTER` | **VJAZD** do kruhu – voliteľná poistka; dostane ho najstaršie auto bez vjazdu (FIFO) |
| `BACKSPACE` | undo poslednej udalosti (opakovateľne) |
| `M` | prepnutie do režimu len Smery |
| `Ctrl+S` | export CSV |

**Komu sa priradí výjazd.** Na paneli je zoznam *Autá v systéme* (prešli referenčným bodom
a ešte neprešli čiarou výjazdu). Šípka ► ukazuje auto, ktoré dostane najbližší výjazd –
predvolene najstaršie. Keď v kruhu auto predbehne iné (napr. auto 2 odbočí na prvom výjazde
skôr, než auto 1 objde kruh), stlačte `Tab` (alebo kliknite na auto v zozname) a až potom
číslo výjazdu. Po zázname sa výber vráti na najstaršie auto.

**Typ vozidla.** `N` otvorí okno s typom vozidla (stačí stlačiť prvé písmeno: d, k, a, m),
autom a voliteľnou poznámkou. Predvolené je auto, ktoré naposledy prešlo referenčným bodom,
takže pri kamióne stačí `MEDZERNÍK`, `N`, `k`, `Enter`. Bez auta sa dá uložiť aj všeobecná
poznámka (napr. „chodec na priechode“).

**Vjazd (Enter)** používajte buď pre všetky autá, alebo vôbec – priraďuje sa v poradí
príchodu a pri vynechaných vjazdoch by sa posunul na nesprávne auto.

## Režim „len Smery“ (klávesa M)

Pôvodný samostatný režim: `1`–`4` zaznamená len smer bez priradenia k autu, Medzerník
a Enter sú vypnuté. Pri meraní s výjazdmi ho netreba. V `*_smery.csv` sa sčítavajú výjazdy
z oboch režimov, preto pre to isté video nepoužívajte oba.

## Ako sa počíta stav

Udalosti sa ukladajú do zoznamu v poradí zaznamenania. Stav (ID áut, výjazdy, `n_pred`,
časy v systéme, vylúčené úseky) sa vždy **prepočíta od začiatku zo zoznamu udalostí
zoradených podľa času videa 1**. Preto:

- ak pretočíte video dozadu a zaznamenáte udalosť, zaradí sa správne podľa času
  (zobrazí sa nenápadné varovanie v stavovom riadku);
- undo iba odstráni poslednú zaznamenanú udalosť a stav sa prepočíta;
- ID áut zodpovedajú poradiu príchodov; výjazd je naviazaný na konkrétny príchod, takže
  pri dodatočnom vložení príchodu zostane pri správnom aute aj po prečíslovaní.

**`n_pred`** = počet áut, ktoré pri príchode daného auta už boli v systéme (prišli skôr
a ešte neprešli čiarou výjazdu). Ak sa v relácii výjazdy nezaznamenávajú, rozhoduje vjazd.

Nekonzistentné udalosti (napr. výjazd skôr než príchod po zlej synchronizácii) aplikácia
nezahodí, ale označí: na paneli je červené počítadlo *Chybné udalosti*, v zozname udalostí
je pri nich ⚠ a v exporte stĺpec `kontrola`.

## Ukladanie a export

- **Autosave** – po každej udalosti sa celá relácia (nastavenia, video 2, posun, udalosti)
  uloží do `<video>_rameno<k>.json` vedľa videa 1 (atomicky, cez dočasný súbor). Pri páde
  programu sa nič nestratí. Staré autosave súbory z predošlej verzie sa načítajú.
- **Export** (tlačidlo alebo `Ctrl+S`) vytvorí vedľa videa 1 tri CSV súbory:

  1. `<video>_rameno<k>_auta.csv` – **jeden riadok = jedno auto**:

     | stĺpec | význam |
     |---|---|
     | `auto_id` | poradové číslo auta podľa príchodu |
     | `rameno_prichodu`, `rameno_prichodu_nazov` | merané rameno (napr. 1, S) |
     | `t_prichod_s`, `cas_prichodu_realny` | prejazd referenčným bodom |
     | `t_vyjazd_s`, `cas_vyjazdu_realny` | prejazd čiarou výjazdu |
     | `rameno_vyjazdu`, `rameno_vyjazdu_nazov` | kam auto išlo |
     | `cas_v_systeme_s` | `t_vyjazd − t_prichod` |
     | `t_vjazd_s`, `cakanie_s` | voliteľný vjazd a `t_vjazd − t_prichod` |
     | `n_pred` | počet áut v systéme pri príchode |
     | `vylucene` | 1 = prišlo vo vylúčenom úseku |
     | `typ_vozidla`, `ine_vozidlo` | napr. kamión; `ine_vozidlo` = 1 pre všetko okrem osobného auta |
     | `interval_od_predch_s` | `t_prichod − t_prichod` predchádzajúceho; prázdne pre prvé auto a pri vylúčenom úseku |
     | `poznamka` | poznámky k autu |
     | `kontrola` | napr. „bez výjazdu“, „vjazd po výjazde“ – prázdne = v poriadku |

     Pri vyhodnotení stačí v Exceli filtrovať `vylucene = 0` a `ine_vozidlo = 0`.
  2. `<video>_rameno<k>_udalosti.csv` – všetky udalosti:
     `event_id; typ; cas_video_s; cas_video2_s; zdroj_video; cas_realny; rameno; rameno_nazov;
     auto_id; ciel_rameno; ciel_rameno_nazov; typ_vozidla; poznamka; kontrola`
     (typ: `PRICHOD`, `VYJAZD`, `VJAZD`, `VYLUC_ZACIATOK`, `VYLUC_KONIEC`, `POZNAMKA`, `SMER`).
  3. `<video>_rameno<k>_smery.csv` –
     `interval_od_s; rameno_z; rameno_z_nazov; rameno_na; rameno_na_nazov; pocet; podiel` –
     riadky „spolu“ a potom po 15-minútových intervaloch času videa 1 (podľa času výjazdu).

- CSV je v UTF-8 s BOM; predvolene bodkočiarka a desatinná čiarka (slovenský Excel sa
  otvorí dvojklikom správne). Alternatíva: čiarka + desatinná bodka.
  V LibreOffice Calc pri importe nechajte ako oddeľovač iba *Bodkočiarku* (odškrtnite *Čiarku*)
  a nastavte *Locale* na slovenčinu, inak sa desatinné čiarky rozdelia do ďalších stĺpcov.
- Pri zatvorení okna s neexportovanými zmenami aplikácia ponúkne export.

## Overenie logiky bez okna

Logika (triedy `Session`, `compute_state`) nezávisí od GUI. Rýchla kontrola na 3 autách,
z ktorých druhé predbehne prvé:

```bash
QT_QPA_PLATFORM=offscreen python -c "
import app
s = app.Session('test.mp4', 1, nazvy_ramien=app.DEFAULT_ARM_NAMES)
a = [s.add_event(app.T_PRICHOD, t) for t in (1.0, 2.0, 3.5)]
s.add_event(app.T_VYJAZD, 8.0, ciel_rameno=2, ref_seq=a[1].seq)
s.add_event(app.T_VYJAZD, 9.5, ciel_rameno=4, ref_seq=a[0].seq)
s.add_event(app.T_VYJAZD, 12.0, ciel_rameno=1, ref_seq=a[2].seq)
for c in s.state().cars: print(c.auto_id, c.t_prichod, c.n_pred, c.t_vyjazd, c.rameno_vyjazdu, c.cas_v_systeme)
"
```
