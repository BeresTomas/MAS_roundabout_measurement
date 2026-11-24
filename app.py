#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merač dopravných udalostí – ručné zaznamenávanie udalostí z videa
(meranie na kruhovom objazde).

Spustenie:  pip install pyside6 ;  python app.py

Jeden súbor. Logika (udalosti, prepočet stavu, export) je oddelená od GUI,
takže sa dá testovať bez okna (viď README).
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ERROR_LOG = os.path.join(APP_DIR, "merac_error.log")

if sys.version_info < (3, 10):
    sys.exit(f"Táto aplikácia potrebuje Python 3.10 alebo novší (máte {sys.version.split()[0]}).\n"
             "Stiahnite ho z https://www.python.org/downloads/ (na Windows zaškrtnite 'Add to PATH').")

try:
    from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, QRegularExpression
    from PySide6.QtGui import (QAction, QActionGroup, QFontDatabase, QKeySequence,
                               QRegularExpressionValidator)
except ImportError as _ex:  # pragma: no cover
    sys.exit("Chýba knižnica PySide6 (alebo sa nedá načítať).\n"
             "Nainštalujte ju príkazom:   python -m pip install pyside6\n"
             "(na Windows prípadne:       py -m pip install pyside6)\n"
             f"Pôvodná chyba: {_ex}")
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSizePolicy, QSlider, QSpinBox, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

# --------------------------------------------------------------------------------------
# Konštanty
# --------------------------------------------------------------------------------------

SPEEDS = [0.25, 0.5, 1.0, 1.5, 2.0]
DEFAULT_FRAME_MS = 40         # krok po snímkoch, ak nepoznáme fps (0,04 s)
SYNC_TOLERANCE_MS = 250       # povolený rozchod videa 2 počas prehrávania
SYNC_COOLDOWN_S = 1.0         # najkratší odstup medzi dvoma dorovnaniami videa 2 počas prehrávania

T_PRICHOD = "PRICHOD"         # prejazd referenčným bodom (video 1)
T_VJAZD = "VJAZD"             # vjazd do kruhu – voliteľná poistka
T_VYJAZD = "VYJAZD"           # prejazd čiarou výjazdu + rameno výjazdu (video 2)
T_VYLUC = "VYLUC"             # interný prepínač; pri prepočte sa stane VYLUC_ZACIATOK / VYLUC_KONIEC
T_POZNAMKA = "POZNAMKA"       # poznámka / typ vozidla, voliteľne priradená k autu
T_SMER = "SMER"               # len smer bez auta (režim Smery)

MODE_CASY = "CASY"
MODE_SMERY = "SMERY"

ARMS = (1, 2, 3, 4)
DEFAULT_ARM_NAMES = {1: "S", 2: "V", 3: "J", 4: "Z"}
VEHICLE_TYPES = ["dodávka", "kamión", "autobus", "motorka", "iné"]

CSV_FORMATS = {
    "sk": {"delimiter": ";", "decimal": ",", "label": "bodkočiarka + desatinná čiarka (slovenský Excel)"},
    "en": {"delimiter": ",", "decimal": ".", "label": "čiarka + desatinná bodka"},
}


def round_t(seconds: float) -> float:
    """Čas videa zaokrúhlený na 0,1 s."""
    return round(seconds + 1e-9, 1)


def sk_num(x: float, nd: int = 1) -> str:
    """Číslo s desatinnou čiarkou pre texty v UI."""
    return f"{x:.{nd}f}".replace(".", ",")


def fmt_t(seconds: Optional[float]) -> str:
    """h:mm:ss.d pre zobrazenie na obrazovke."""
    if seconds is None:
        return "–"
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:04.1f}"


def parse_hms(text: str) -> Optional[int]:
    """'HH:MM:SS' -> sekundy od polnoci, None ak prázdne; ValueError ak neplatné."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError("Čas musí mať tvar HH:MM:SS")
    h, m, s = (int(p) for p in parts)
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        raise ValueError("Neplatný čas")
    return h * 3600 + m * 60 + s


# --------------------------------------------------------------------------------------
# Dátový model
# --------------------------------------------------------------------------------------

@dataclass
class Event:
    seq: int                       # poradie zaznamenania (pre undo, stabilný identifikátor)
    typ: str                       # PRICHOD / VJAZD / VYJAZD / VYLUC / POZNAMKA / SMER
    t: float                       # čas vo videu `zdroj` v sekundách (0,1 s)
    ciel_rameno: Optional[int] = None
    poznamka: str = ""
    ref_seq: Optional[int] = None  # VYJAZD / POZNAMKA: seq príchodu auta, ku ktorému patrí
    typ_vozidla: str = ""          # POZNAMKA: dodávka / kamión / ...
    zdroj: int = 1                 # 1 = čas z videa 1, 2 = čas z videa 2 (prepočíta sa posunom)

    def to_json(self) -> dict:
        d = {"seq": self.seq, "typ": self.typ, "t": self.t,
             "ciel_rameno": self.ciel_rameno, "poznamka": self.poznamka}
        if self.ref_seq is not None:
            d["ref_seq"] = self.ref_seq
        if self.typ_vozidla:
            d["typ_vozidla"] = self.typ_vozidla
        if self.zdroj != 1:
            d["zdroj"] = self.zdroj
        return d

    @staticmethod
    def from_json(d: dict) -> "Event":
        typ = d["typ"]
        if typ in ("VYLUC_ZACIATOK", "VYLUC_KONIEC"):
            typ = T_VYLUC
        ciel = d.get("ciel_rameno")
        ref = d.get("ref_seq")
        return Event(seq=int(d["seq"]), typ=typ, t=float(d["t"]),
                     ciel_rameno=int(ciel) if ciel is not None else None,
                     poznamka=d.get("poznamka") or "",
                     ref_seq=int(ref) if ref is not None else None,
                     typ_vozidla=d.get("typ_vozidla") or "",
                     zdroj=int(d.get("zdroj", 1) or 1))


def event_time(e: Event, posun: float) -> float:
    """Čas udalosti na časovej osi videa 1 (video 2 sa prepočíta posunom)."""
    return round_t(e.t + posun) if e.zdroj == 2 else e.t


@dataclass
class Car:
    auto_id: int
    t_prichod: float
    seq_prichod: int
    vylucene: int
    n_pred: int = 0
    t_vjazd: Optional[float] = None
    seq_vjazd: Optional[int] = None
    t_vyjazd: Optional[float] = None
    seq_vyjazd: Optional[int] = None
    rameno_vyjazdu: Optional[int] = None
    typ_vozidla: str = ""
    poznamky: list = field(default_factory=list)
    kontrola: list = field(default_factory=list)

    @property
    def cakanie(self) -> Optional[float]:
        """t_vjazd − t_prichod (len ak bol zaznamenaný vjazd)."""
        if self.t_vjazd is None:
            return None
        return round_t(self.t_vjazd - self.t_prichod)

    @property
    def cas_v_systeme(self) -> Optional[float]:
        """t_vyjazd − t_prichod (referenčný bod → čiara výjazdu)."""
        if self.t_vyjazd is None:
            return None
        return round_t(self.t_vyjazd - self.t_prichod)

    @property
    def ine_vozidlo(self) -> int:
        return 1 if self.typ_vozidla else 0


@dataclass
class State:
    """Stav prepočítaný zo zoznamu udalostí zoradených podľa času (os videa 1)."""
    cars: list = field(default_factory=list)                 # list[Car] podľa poradia príchodu
    segments: list = field(default_factory=list)             # list[(start, end|None)]
    rows: list = field(default_factory=list)                 # udalosti v časovom poradí s odvodenými údajmi
    smery: dict = field(default_factory=lambda: {k: 0 for k in ARMS})
    meria_vyjazdy: bool = False                               # True = v relácii je aspoň jeden VYJAZD

    @property
    def by_seq(self) -> dict:
        return {r["seq"]: r for r in self.rows}

    def leave_key(self, c: Car) -> Optional[tuple]:
        """Kedy auto opustilo systém: výjazd (ak sa meria), inak vjazd do kruhu."""
        if self.meria_vyjazdy:
            return (c.t_vyjazd, c.seq_vyjazd) if c.t_vyjazd is not None else None
        return (c.t_vjazd, c.seq_vjazd) if c.t_vjazd is not None else None

    def in_system_at(self, now: float) -> list:
        """Autá, ktoré v čase `now` prešli referenčným bodom a ešte neopustili systém."""
        out = []
        for c in self.cars:
            if c.t_prichod > now:
                continue
            lk = self.leave_key(c)
            if lk is None or lk[0] > now:
                out.append(c)
        return out

    def exit_candidates(self, now: float) -> list:
        """Autá, ktorým sa dá v čase `now` priradiť výjazd (prišli a výjazd ešte nemajú)."""
        return [c for c in self.cars if c.t_prichod <= now and c.t_vyjazd is None]

    def entry_candidates(self, now: float) -> list:
        """Autá, ktorým sa dá v čase `now` priradiť vjazd (FIFO)."""
        return [c for c in self.cars if c.t_prichod <= now and c.t_vjazd is None
                and (c.t_vyjazd is None or c.t_vyjazd > now)]

    def excluded_at(self, now: float) -> bool:
        return any(s <= now and (e is None or now < e) for s, e in self.segments)

    @property
    def n_prichody(self) -> int:
        return len(self.cars)

    @property
    def n_vjazdy(self) -> int:
        return sum(1 for c in self.cars if c.t_vjazd is not None)

    @property
    def n_vyjazdy(self) -> int:
        return sum(1 for c in self.cars if c.t_vyjazd is not None)

    @property
    def n_vyluc(self) -> int:
        return len(self.segments)

    @property
    def n_ine(self) -> int:
        return sum(c.ine_vozidlo for c in self.cars)

    @property
    def n_chyby(self) -> int:
        return sum(1 for r in self.rows if r["chyba"])


def compute_state(events: list, posun: float = 0.0) -> State:
    """Prehrá udalosti od začiatku v poradí podľa času (pri rovnosti podľa poradia zápisu)."""
    st = State()
    st.meria_vyjazdy = any(e.typ == T_VYJAZD for e in events)
    by_arrival: dict = {}
    excluded = False
    notes = []
    for e in sorted(events, key=lambda ev: (event_time(ev, posun), ev.seq)):
        t = event_time(e, posun)
        row = {"seq": e.seq, "typ": e.typ, "t": t, "t_vlastny": e.t, "zdroj": e.zdroj,
               "auto_id": None, "ciel_rameno": e.ciel_rameno, "poznamka": e.poznamka,
               "typ_vozidla": e.typ_vozidla, "chyba": ""}
        if e.typ == T_PRICHOD:
            car = Car(auto_id=len(st.cars) + 1, t_prichod=t, seq_prichod=e.seq,
                      vylucene=1 if excluded else 0)
            st.cars.append(car)
            by_arrival[e.seq] = car
            row["auto_id"] = car.auto_id
        elif e.typ == T_VJAZD:
            car = next((c for c in st.cars if c.t_vjazd is None and c.t_vyjazd is None), None)
            if car is not None:
                car.t_vjazd, car.seq_vjazd = t, e.seq
                row["auto_id"] = car.auto_id
            else:
                row["chyba"] = "vjazd bez auta vo fronte"
        elif e.typ == T_VYJAZD:
            if e.ref_seq is not None:
                car = by_arrival.get(e.ref_seq)
            else:
                car = next((c for c in st.cars if c.t_vyjazd is None), None)
            if car is None:
                row["chyba"] = "výjazd bez príchodu auta (príchod chýba alebo je neskôr)"
            elif car.t_vyjazd is not None:
                row["auto_id"] = car.auto_id
                row["chyba"] = "auto už má výjazd"
            else:
                car.t_vyjazd, car.seq_vyjazd, car.rameno_vyjazdu = t, e.seq, e.ciel_rameno
                row["auto_id"] = car.auto_id
                if e.ciel_rameno in st.smery:
                    st.smery[e.ciel_rameno] += 1
        elif e.typ == T_VYLUC:
            if excluded:
                start, _ = st.segments[-1]
                st.segments[-1] = (start, t)
                excluded = False
                row["typ"] = "VYLUC_KONIEC"
            else:
                st.segments.append((t, None))
                excluded = True
                row["typ"] = "VYLUC_ZACIATOK"
        elif e.typ == T_POZNAMKA:
            notes.append((e, row))
        elif e.typ == T_SMER:
            if e.ciel_rameno in st.smery:
                st.smery[e.ciel_rameno] += 1
        st.rows.append(row)

    # poznámky / typ vozidla k autám (nezávisle od času poznámky)
    for e, row in notes:
        if e.ref_seq is None:
            continue
        car = by_arrival.get(e.ref_seq)
        if car is None:
            row["chyba"] = "poznámka k neexistujúcemu autu"
            continue
        row["auto_id"] = car.auto_id
        if e.typ_vozidla:
            car.typ_vozidla = e.typ_vozidla
        if e.poznamka:
            car.poznamky.append(e.poznamka)

    # n_pred = autá pred ním, ktoré ešte neopustili systém; kontrola konzistencie
    left_keys: list = []
    for i, c in enumerate(st.cars):
        key = (c.t_prichod, c.seq_prichod)
        c.n_pred = i - bisect.bisect_right(left_keys, key)
        lk = st.leave_key(c)
        if lk is not None:
            bisect.insort(left_keys, lk)
        if st.meria_vyjazdy and c.t_vyjazd is None:
            c.kontrola.append("bez výjazdu")
        if c.t_vjazd is not None and c.t_vyjazd is not None and c.t_vjazd > c.t_vyjazd:
            c.kontrola.append("vjazd po výjazde")
    return st


# --------------------------------------------------------------------------------------
# Relácia (nastavenia + udalosti + autosave + export)
# --------------------------------------------------------------------------------------

class Session:
    VERSION = 2

    def __init__(self, video_path: str, rameno: int, pozorovatel: str = "",
                 realny_zaciatok: Optional[str] = None, csv_format: str = "sk",
                 nazvy_ramien: Optional[dict] = None, video2_path: Optional[str] = None,
                 posun_video2_s: float = 0.0):
        self.video_path = video_path
        self.video2_path = video2_path or None
        self.posun_video2_s = round(float(posun_video2_s or 0.0), 1)
        self.rameno = int(rameno)
        self.nazvy_ramien = {k: "" for k in ARMS}
        self.set_nazvy(nazvy_ramien)
        self.pozorovatel = pozorovatel or ""
        self.realny_zaciatok = realny_zaciatok or None      # "HH:MM:SS" alebo None
        self.csv_format = csv_format if csv_format in CSV_FORMATS else "sk"
        self.events: list = []
        self.next_seq = 1
        self.exported = True          # True = žiadne neexportované zmeny
        self.ulozene: Optional[str] = None

    # ---- názvy ramien ------------------------------------------------------------
    def set_nazvy(self, nazvy: Optional[dict]):
        for k, v in (nazvy or {}).items():
            try:
                k = int(k)
            except (TypeError, ValueError):
                continue
            if k in self.nazvy_ramien:
                self.nazvy_ramien[k] = (v or "").strip()

    def nazov(self, k: Optional[int]) -> str:
        """Názov ramena k (prázdny reťazec, ak nie je pomenované)."""
        return self.nazvy_ramien.get(int(k), "") if k is not None else ""

    def rameno_label(self, k: Optional[int]) -> str:
        """'2 (V)' alebo len '2' pre zobrazenie v UI."""
        if k is None:
            return "–"
        n = self.nazov(k)
        return f"{k} ({n})" if n else str(k)

    @staticmethod
    def nazvy_from_sibling_autosaves(video_path: str) -> dict:
        """Názvy ramien z ľubovoľného autosave toho istého videa (ramená sú spoločné)."""
        found = {k: "" for k in ARMS}
        for k in ARMS:
            path = Session.autosave_path_for(video_path, k)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            for kk, v in (d.get("nazvy_ramien") or {}).items():
                try:
                    kk = int(kk)
                except (TypeError, ValueError):
                    continue
                if kk in found and v and not found[kk]:
                    found[kk] = v.strip()
        return found

    # ---- cesty -------------------------------------------------------------------
    @staticmethod
    def autosave_path_for(video_path: str, rameno: int) -> str:
        base, _ = os.path.splitext(video_path)
        return f"{base}_rameno{int(rameno)}.json"

    @property
    def autosave_path(self) -> str:
        return self.autosave_path_for(self.video_path, self.rameno)

    @property
    def export_base(self) -> str:
        base, _ = os.path.splitext(self.video_path)
        return f"{base}_rameno{self.rameno}"

    # ---- udalosti ----------------------------------------------------------------
    @property
    def last_event(self) -> Optional[Event]:
        return self.events[-1] if self.events else None

    def effective_t(self, e: Event) -> float:
        return event_time(e, self.posun_video2_s)

    def add_event(self, typ: str, t: float, ciel_rameno: Optional[int] = None,
                  poznamka: str = "", ref_seq: Optional[int] = None,
                  typ_vozidla: str = "", zdroj: int = 1) -> Event:
        e = Event(seq=self.next_seq, typ=typ, t=round_t(t), ciel_rameno=ciel_rameno,
                  poznamka=poznamka, ref_seq=ref_seq, typ_vozidla=typ_vozidla, zdroj=zdroj)
        self.next_seq += 1
        self.events.append(e)
        self.exported = False
        return e

    def undo(self) -> Optional[Event]:
        if not self.events:
            return None
        e = self.events.pop()
        self.exported = False
        return e

    def state(self) -> State:
        return compute_state(self.events, self.posun_video2_s)

    def video2_time(self, t: float) -> Optional[float]:
        """Čas vo videu 2 zodpovedajúci času `t` videa 1 (None, ak video 2 nie je alebo ešte nebežalo)."""
        if not self.video2_path and not any(e.zdroj == 2 for e in self.events):
            return None
        v = round_t(t - self.posun_video2_s)
        return v if v >= 0 else None

    # ---- JSON autosave -----------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "version": self.VERSION,
            "video": self.video_path,
            "video2": self.video2_path,
            "posun_video2_s": self.posun_video2_s,
            "rameno": self.rameno,
            "pozorovatel": self.pozorovatel,
            "realny_zaciatok": self.realny_zaciatok,
            "csv_format": self.csv_format,
            "nazvy_ramien": {str(k): v for k, v in self.nazvy_ramien.items()},
            "exportovane": self.exported,
            "next_seq": self.next_seq,
            "ulozene": dt.datetime.now().isoformat(timespec="seconds"),
            "udalosti": [e.to_json() for e in self.events],
        }

    @classmethod
    def from_json(cls, d: dict, video_path: Optional[str] = None) -> "Session":
        s = cls(video_path or d["video"], d.get("rameno", 1), d.get("pozorovatel", ""),
                d.get("realny_zaciatok"), d.get("csv_format", "sk"), d.get("nazvy_ramien"),
                d.get("video2"), d.get("posun_video2_s", 0.0))
        s.events = [Event.from_json(x) for x in d.get("udalosti", [])]
        s.next_seq = int(d.get("next_seq", (max((e.seq for e in s.events), default=0) + 1)))
        s.exported = bool(d.get("exportovane", True))
        s.ulozene = d.get("ulozene")
        return s

    def save(self) -> str:
        """Atomický zápis autosave súboru (tmp + rename)."""
        path = self.autosave_path
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str, video_path: Optional[str] = None) -> "Session":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(json.load(f), video_path)

    # ---- export CSV --------------------------------------------------------------
    def _num(self, x: Optional[float], nd: int = 1) -> str:
        if x is None:
            return ""
        return f"{x:.{nd}f}".replace(".", CSV_FORMATS[self.csv_format]["decimal"])

    def real_time(self, t: Optional[float]) -> str:
        if not self.realny_zaciatok or t is None:
            return ""
        start = parse_hms(self.realny_zaciatok) or 0
        total = (start + t) % 86400
        h = int(total // 3600)
        m = int((total % 3600) // 60)
        s = total - h * 3600 - m * 60
        return f"{h:02d}:{m:02d}:{s:04.1f}".replace(".", CSV_FORMATS[self.csv_format]["decimal"])

    EXPORT_COLUMNS = [
        "zaznam", "auto_id", "rameno_prichodu", "rameno_prichodu_nazov",
        "t_prichod_s", "cas_prichodu_realny",
        "t_vyjazd_s", "cas_vyjazdu_realny", "t_vyjazd_video2_s", "rameno_vyjazdu", "rameno_vyjazdu_nazov",
        "cas_v_systeme_s", "t_vjazd_s", "cakanie_s", "n_pred", "vylucene",
        "typ_vozidla", "ine_vozidlo", "interval_od_predch_s", "poznamka", "kontrola",
        "cas_od_s", "cas_do_s", "cas_od_realny", "cas_do_realny",
    ]

    @property
    def export_path(self) -> str:
        return self.export_base + ".csv"

    def export_rows(self) -> list:
        """Riadky exportu (bez hlavičky): najprv všetky autá podľa príchodu, potom ostatné záznamy.

        zaznam = AUTO            jeden riadok = jedno auto (všetky časy, ramená, typ vozidla, ...)
                 VYLUCENY_USEK   cas_od_s – cas_do_s
                 POZNAMKA        všeobecná poznámka bez auta (cas_od_s)
                 SMER            smer bez auta z režimu len Smery (t_vyjazd_s, rameno_vyjazdu)
                 CHYBA           udalosť, ktorú sa nepodarilo priradiť (popis v stĺpci kontrola)
        """
        st = self.state()
        cols = self.EXPORT_COLUMNS
        out = []

        def row(**kw) -> list:
            r = {c: "" for c in cols}
            r["rameno_prichodu"] = self.rameno
            r["rameno_prichodu_nazov"] = self.nazov(self.rameno)
            for k, v in kw.items():
                r[k] = "" if v is None else v
            return [r[c] for c in cols]

        prev: Optional[Car] = None
        for c in st.cars:
            interval = None
            if prev is not None and not prev.vylucene and not c.vylucene:
                interval = round_t(c.t_prichod - prev.t_prichod)
            out.append(row(
                zaznam="AUTO", auto_id=c.auto_id,
                t_prichod_s=self._num(c.t_prichod), cas_prichodu_realny=self.real_time(c.t_prichod),
                t_vyjazd_s=self._num(c.t_vyjazd), cas_vyjazdu_realny=self.real_time(c.t_vyjazd),
                t_vyjazd_video2_s=self._num(self.video2_time(c.t_vyjazd)) if c.t_vyjazd is not None else "",
                rameno_vyjazdu=c.rameno_vyjazdu, rameno_vyjazdu_nazov=self.nazov(c.rameno_vyjazdu),
                cas_v_systeme_s=self._num(c.cas_v_systeme), t_vjazd_s=self._num(c.t_vjazd),
                cakanie_s=self._num(c.cakanie), n_pred=c.n_pred, vylucene=c.vylucene,
                typ_vozidla=c.typ_vozidla, ine_vozidlo=c.ine_vozidlo,
                interval_od_predch_s=self._num(interval), poznamka=" | ".join(c.poznamky),
                kontrola=", ".join(c.kontrola)))
            prev = c

        for start, end in st.segments:
            out.append(row(zaznam="VYLUCENY_USEK", cas_od_s=self._num(start), cas_do_s=self._num(end),
                           cas_od_realny=self.real_time(start), cas_do_realny=self.real_time(end),
                           kontrola="" if end is not None else "úsek nie je ukončený (X)"))

        for r in st.rows:
            if r["typ"] == T_POZNAMKA and r["auto_id"] is None and not r["chyba"]:
                out.append(row(zaznam="POZNAMKA", poznamka=r["poznamka"], typ_vozidla=r["typ_vozidla"],
                               cas_od_s=self._num(r["t"]), cas_od_realny=self.real_time(r["t"])))
        for r in st.rows:
            if r["typ"] == T_SMER:
                out.append(row(zaznam="SMER", t_vyjazd_s=self._num(r["t"]),
                               cas_vyjazdu_realny=self.real_time(r["t"]),
                               t_vyjazd_video2_s=self._num(self.video2_time(r["t"])),
                               rameno_vyjazdu=r["ciel_rameno"], rameno_vyjazdu_nazov=self.nazov(r["ciel_rameno"])))
        for r in st.rows:
            if r["chyba"]:
                out.append(row(zaznam="CHYBA", auto_id=r["auto_id"], rameno_vyjazdu=r["ciel_rameno"],
                               rameno_vyjazdu_nazov=self.nazov(r["ciel_rameno"]),
                               typ_vozidla=r["typ_vozidla"], poznamka=r["poznamka"],
                               kontrola=f"{r['typ']}: {r['chyba']}",
                               cas_od_s=self._num(r["t"]), cas_od_realny=self.real_time(r["t"])))
        return out

    def _csv_line(self, values) -> str:
        """Riadok CSV. Každé pole s čiarkou, bodkočiarkou alebo úvodzovkou je v úvodzovkách,
        takže sa stĺpce rozdelia správne aj keď má tabuľkový program ako oddeľovač aj čiarku."""
        delim = CSV_FORMATS[self.csv_format]["delimiter"]
        out = []
        for v in values:
            s = "" if v is None else str(v)
            if any(ch in s for ch in ',;"\r\n\t'):
                s = '"' + s.replace('"', '""') + '"'
            out.append(s)
        return delim.join(out)

    def export_csv(self) -> list:
        """Vytvorí jeden súbor <video>_rameno<k>.csv vedľa videa 1. Vráti [cesta]."""
        path = self.export_path
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            for values in [self.EXPORT_COLUMNS] + self.export_rows():
                f.write(self._csv_line(values) + "\r\n")
        os.replace(tmp, path)
        self.exported = True
        try:
            self.save()
        except OSError:
            pass
        return [path]


# --------------------------------------------------------------------------------------
# Dialógy
# --------------------------------------------------------------------------------------

VIDEO_FILTER = "Video (*.mp4 *.mkv *.avi *.mov *.m4v *.webm *.mts *.ts *.wmv);;Všetky súbory (*)"


class ArmNamesWidget(QGroupBox):
    """Štyri polia na voliteľné názvy ramien 1–4."""

    def __init__(self, parent=None):
        super().__init__("Názvy ramien (voliteľné, spoločné pre celé video)", parent)
        form = QFormLayout(self)
        self.edits = {}
        for k in ARMS:
            ed = QLineEdit()
            ed.setPlaceholderText(f"napr. {DEFAULT_ARM_NAMES[k]} alebo názov ulice")
            form.addRow(f"Rameno {k}:", ed)
            self.edits[k] = ed
        btn = QPushButton("Vyplniť S / V / J / Z")
        btn.setAutoDefault(False)
        btn.setToolTip("Rameno 1 = S, 2 = V, 3 = J, 4 = Z")
        btn.clicked.connect(lambda: self.set_values(DEFAULT_ARM_NAMES))
        form.addRow("", btn)

    def values(self) -> dict:
        return {k: ed.text().strip() for k, ed in self.edits.items()}

    def set_values(self, nazvy: dict, only_empty: bool = False):
        for k, ed in self.edits.items():
            v = (nazvy or {}).get(k, "") or ""
            if v and (not only_empty or not ed.text().strip()):
                ed.setText(v)


class ArmNamesDialog(QDialog):
    def __init__(self, parent, nazvy: dict):
        super().__init__(parent)
        self.setWindowTitle("Názvy ramien")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        self.widget = ArmNamesWidget()
        self.widget.set_values(nazvy)
        lay.addWidget(self.widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Zrušiť")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> dict:
        return self.widget.values()


class NoteDialog(QDialog):
    """Typ vozidla a/alebo poznámka, voliteľne priradená ku konkrétnemu autu."""

    def __init__(self, parent, t: float, cars: list, default_seq: Optional[int]):
        super().__init__(parent)
        self.setWindowTitle("Typ vozidla / poznámka")
        self.setMinimumWidth(420)
        form = QFormLayout()

        self.cb_typ = QComboBox()
        self.cb_typ.addItem("osobné auto (bez označenia)", "")
        for v in VEHICLE_TYPES:
            self.cb_typ.addItem(v, v)
        self.cb_typ.setToolTip("Stačí stlačiť prvé písmeno: d = dodávka, k = kamión, a = autobus, m = motorka")
        form.addRow("Typ vozidla:", self.cb_typ)

        self.cb_car = QComboBox()
        self.cb_car.addItem("— bez auta (všeobecná poznámka) —", None)
        for label, seq in cars:
            self.cb_car.addItem(label, seq)
        idx = self.cb_car.findData(default_seq) if default_seq is not None else -1
        self.cb_car.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Auto:", self.cb_car)

        self.ed_text = QLineEdit()
        self.ed_text.setPlaceholderText("voliteľné, napr. „s prívesom“, „chodec na priechode“")
        form.addRow("Poznámka:", self.ed_text)

        self.lbl_err = QLabel("")
        self.lbl_err.setStyleSheet("color:#c62828;")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Zrušiť")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Čas: <b>{fmt_t(t)}</b>"))
        lay.addLayout(form)
        lay.addWidget(self.lbl_err)
        lay.addWidget(buttons)
        self.cb_typ.setFocus()

    def values(self) -> tuple:
        """(ref_seq alebo None, typ_vozidla, poznámka)"""
        return self.cb_car.currentData(), self.cb_typ.currentData() or "", self.ed_text.text().strip()

    def accept(self):
        ref, typ, text = self.values()
        if not typ and not text:
            self.lbl_err.setText("Vyberte typ vozidla alebo napíšte poznámku.")
            return
        if typ and ref is None:
            self.lbl_err.setText("Typ vozidla sa musí priradiť k autu.")
            return
        super().accept()


class SetupDialog(QDialog):
    def __init__(self, parent=None, initial: Optional[Session] = None):
        super().__init__(parent)
        self.setWindowTitle("Nastavenie relácie")
        self.setMinimumWidth(600)
        self._loaded: Optional[Session] = None

        form = QFormLayout()

        row = QHBoxLayout()
        self.ed_video = QLineEdit()
        self.ed_video.setPlaceholderText("Video z konca radu – tu sa zaznamenávajú príchody")
        btn = QPushButton("Vybrať…")
        btn.setAutoDefault(False)
        btn.clicked.connect(lambda: self.pick_into(self.ed_video))
        row.addWidget(self.ed_video, 1)
        row.addWidget(btn)
        form.addRow("Video 1 (príchody):", row)

        row = QHBoxLayout()
        self.ed_video2 = QLineEdit()
        self.ed_video2.setPlaceholderText("voliteľné – video kruhového objazdu (výjazdy)")
        btn = QPushButton("Vybrať…")
        btn.setAutoDefault(False)
        btn.clicked.connect(lambda: self.pick_into(self.ed_video2))
        row.addWidget(self.ed_video2, 1)
        row.addWidget(btn)
        form.addRow("Video 2 (výjazdy):", row)

        self.sp_posun = QDoubleSpinBox()
        self.sp_posun.setRange(-86400.0, 86400.0)
        self.sp_posun.setDecimals(1)
        self.sp_posun.setSingleStep(0.1)
        self.sp_posun.setSuffix(" s")
        self.sp_posun.setToolTip("O koľko sekúnd neskôr bolo spustené video 2 oproti videu 1 "
                                 "(záporné = skôr). Presne sa doladí počas prehrávania.")
        form.addRow("Video 2 začalo neskôr o:", self.sp_posun)

        self.sp_rameno = QSpinBox()
        self.sp_rameno.setRange(1, 4)
        form.addRow("Rameno príchodu (merané):", self.sp_rameno)

        self.ed_obs = QLineEdit()
        self.ed_obs.setPlaceholderText("voliteľné")
        form.addRow("Pozorovateľ:", self.ed_obs)

        self.ed_real = QLineEdit()
        self.ed_real.setPlaceholderText("HH:MM:SS (voliteľné)")
        self.ed_real.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^(|([01]?\d|2[0-3]):[0-5]\d:[0-5]\d)$")))
        form.addRow("Skutočný čas začiatku videa 1:", self.ed_real)

        self.cb_csv = QComboBox()
        for key, info in CSV_FORMATS.items():
            self.cb_csv.addItem(info["label"], key)
        form.addRow("Formát CSV:", self.cb_csv)

        self.arm_names = ArmNamesWidget()
        form.addRow(self.arm_names)

        self.chk_cont = QCheckBox("Pokračovať v rozpracovanej relácii (načítať autosave)")
        self.chk_cont.setEnabled(False)
        form.addRow("", self.chk_cont)

        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setStyleSheet("color: #555;")
        form.addRow("", self.lbl_info)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Spustiť")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Zrušiť")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        intro = QLabel("Jedna relácia = video 1 + jedno merané rameno príchodu (+ voliteľne video 2 "
                       "s kruhovým objazdom). Autosave sa ukladá vedľa videa 1 ako "
                       "<i>&lt;video&gt;_rameno&lt;k&gt;.json</i>.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(buttons)

        self.ed_video.textChanged.connect(self.update_continue)
        self.sp_rameno.valueChanged.connect(self.update_continue)
        self.chk_cont.toggled.connect(self.on_cont_toggled)

        if initial is not None:
            self.ed_video.setText(initial.video_path)
            self.ed_video2.setText(initial.video2_path or "")
            self.sp_posun.setValue(initial.posun_video2_s)
            self.sp_rameno.setValue(initial.rameno)
            self.ed_obs.setText(initial.pozorovatel)
            self.ed_real.setText(initial.realny_zaciatok or "")
            idx = self.cb_csv.findData(initial.csv_format)
            if idx >= 0:
                self.cb_csv.setCurrentIndex(idx)
            self.arm_names.set_values(initial.nazvy_ramien)
        self.update_continue()

    def pick_into(self, edit: QLineEdit):
        start = edit.text() or self.ed_video.text() or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "Vyberte video", start, VIDEO_FILTER)
        if path:
            edit.setText(path)

    def update_continue(self):
        video = self.ed_video.text().strip()
        self._loaded = None
        if not video or not os.path.isfile(video):
            self.chk_cont.setChecked(False)
            self.chk_cont.setEnabled(False)
            self.lbl_info.setText("")
            return
        self.arm_names.set_values(Session.nazvy_from_sibling_autosaves(video), only_empty=True)
        path = Session.autosave_path_for(video, self.sp_rameno.value())
        if os.path.isfile(path):
            try:
                self._loaded = Session.load(path, video)
                n = len(self._loaded.events)
                last = max((self._loaded.effective_t(e) for e in self._loaded.events), default=0.0)
                self.chk_cont.setEnabled(True)
                self.chk_cont.setChecked(True)
                self.on_cont_toggled(True)
                self.lbl_info.setText(
                    f"Nájdená rozpracovaná relácia: {n} udalostí, posledná v čase {fmt_t(last)}, "
                    f"uložená {self._loaded.ulozene or '?'}.\n{path}")
            except (OSError, ValueError, KeyError) as ex:
                self._loaded = None
                self.chk_cont.setChecked(False)
                self.chk_cont.setEnabled(False)
                self.lbl_info.setText(f"Autosave sa nepodarilo načítať ({ex}). Začne sa nová relácia.")
        else:
            self.chk_cont.setChecked(False)
            self.chk_cont.setEnabled(False)
            self.lbl_info.setText("Pre toto video a rameno zatiaľ nie je autosave – začne sa nová relácia.")

    def on_cont_toggled(self, checked: bool):
        if checked and self._loaded is not None:
            self.ed_obs.setText(self._loaded.pozorovatel)
            self.ed_real.setText(self._loaded.realny_zaciatok or "")
            if self._loaded.video2_path:
                self.ed_video2.setText(self._loaded.video2_path)
            self.sp_posun.setValue(self._loaded.posun_video2_s)
            idx = self.cb_csv.findData(self._loaded.csv_format)
            if idx >= 0:
                self.cb_csv.setCurrentIndex(idx)
            self.arm_names.set_values(self._loaded.nazvy_ramien)

    def accept(self):
        video = self.ed_video.text().strip()
        if not video or not os.path.isfile(video):
            QMessageBox.warning(self, "Chýba video", "Vyberte existujúci video súbor (video 1).")
            return
        video2 = self.ed_video2.text().strip()
        if video2 and not os.path.isfile(video2):
            QMessageBox.warning(self, "Chýba video 2", "Video 2 neexistuje. Opravte cestu alebo pole vymažte.")
            return
        try:
            parse_hms(self.ed_real.text())
        except ValueError as ex:
            QMessageBox.warning(self, "Neplatný čas", str(ex))
            return
        super().accept()

    def result_session(self) -> tuple:
        """Vráti (Session, cas_na_ktory_posunut_video alebo None)."""
        video = self.ed_video.text().strip()
        video2 = self.ed_video2.text().strip() or None
        posun = round(self.sp_posun.value(), 1)
        real = self.ed_real.text().strip() or None
        csv_fmt = self.cb_csv.currentData()
        if self.chk_cont.isChecked() and self._loaded is not None:
            s = self._loaded
            s.video_path = video
            s.video2_path = video2
            s.posun_video2_s = posun
            s.pozorovatel = self.ed_obs.text().strip()
            s.realny_zaciatok = real
            s.csv_format = csv_fmt
            s.set_nazvy(self.arm_names.values())
            seek = max((s.effective_t(e) for e in s.events), default=None)
            return s, seek
        s = Session(video, self.sp_rameno.value(), self.ed_obs.text().strip(), real, csv_fmt,
                    self.arm_names.values(), video2, posun)
        if os.path.isfile(s.autosave_path):
            # používateľ odmietol pokračovať – starý autosave odložíme, aby sa neprepísal bez stopy
            bak = s.autosave_path + "." + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
            try:
                os.replace(s.autosave_path, bak)
            except OSError:
                pass
        return s, None


# --------------------------------------------------------------------------------------
# Hlavné okno
# --------------------------------------------------------------------------------------

SEP = " &nbsp;·&nbsp; "
LEGEND_PLAYER = (
    f"<b>P</b> play/pauza{SEP}<b>←/→</b> ±1 s{SEP}<b>Shift+←/→</b> ±5 s<br>"
    f"<b>, / .</b> snímok späť/vpred{SEP}<b>[ / ]</b> rýchlosť{SEP}<b>↑/↓</b> hlasitosť<br>"
)
LEGEND_CASY = LEGEND_PLAYER + (
    f"<b>MEDZERNÍK</b> príchod (referenčný bod)<br>"
    f"<b>1 / 2 / 3 / 4</b> výjazd na rameno 1–4 (čas + smer)<br>"
    f"<b>Tab / Shift+Tab</b> odchádza iné auto než ►{SEP}<b>Esc</b> späť na najstaršie<br>"
    f"<b>ENTER</b> vjazd do kruhu (voliteľné){SEP}<b>X</b> vylúčený úsek<br>"
    f"<b>N</b> typ vozidla / poznámka{SEP}<b>BACKSPACE</b> undo<br>"
    f"<b>M</b> režim len Smery{SEP}<b>Ctrl+S</b> export"
)
LEGEND_SMERY = LEGEND_PLAYER + (
    f"<b>1 / 2 / 3 / 4</b> auto išlo na výjazd 1–4 (bez priradenia k autu)<br>"
    f"<b>BACKSPACE</b> undo{SEP}<b>M</b> režim Časy{SEP}<b>Ctrl+S</b> export<br>"
    "<i>Medzerník a Enter sú v tomto režime vypnuté.</i>"
)
LEGEND_SYNC = f"<br><b>Ctrl+←/→</b> video 2 o 0,1 s späť/vpred{SEP}<b>Ctrl+Shift+←/→</b> o 1 s"

TITLE_V1 = "Video 1 – koniec radu (príchody)"
TITLE_V2 = "Video 2 – kruhový objazd (výjazdy)"


class MainWindow(QMainWindow):
    def __init__(self, session: Session, seek_to: Optional[float] = None):
        super().__init__()
        self.session = session
        self.state: State = session.state()
        self.mode = MODE_CASY
        self.speed_idx = SPEEDS.index(1.0)
        self.pending_seek: Optional[float] = seek_to
        self.frame_ms = DEFAULT_FRAME_MS
        self.slider_down = False
        self.duration_ms = 0
        self.duration2_ms = 0
        self.video2_ready = False
        self.v2_state = "ok"                       # ok / pred (ešte nezačalo) / po (skončilo)
        self._p2_requested: Optional[int] = None
        self._last_resync = 0.0
        self.selected_seq: Optional[int] = None    # ručne zvolené auto pre ďalší výjazd

        self._build_ui()
        self._build_menu()
        self._build_player()

        QApplication.instance().installEventFilter(self)

        self.tick = QTimer(self)
        self.tick.setInterval(50)
        self.tick.timeout.connect(self.on_tick)
        self.tick.start()

        self.warn_timer = QTimer(self)
        self.warn_timer.setSingleShot(True)
        self.warn_timer.timeout.connect(lambda: self.lbl_warn.setText(""))

        self.load_media()
        self.refresh()
        self.resize(1500, 860)

    # ---- UI ------------------------------------------------------------------------
    def _video_box(self, title: str) -> tuple:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight:bold;")
        lay.addWidget(lbl)
        video = QVideoWidget()
        video.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        video.setMinimumSize(240, 135)
        video.setStyleSheet("background:black;")
        lay.addWidget(video, 1)
        return box, lbl, video

    def _build_ui(self):
        central = QWidget()
        central.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # ľavá časť – videá
        left = QVBoxLayout()
        self.lbl_banner = QLabel("VYLÚČENÝ ÚSEK")
        self.lbl_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_banner.setStyleSheet(
            "background:#c62828; color:white; font-size:26px; font-weight:bold; padding:8px;")
        self.lbl_banner.setVisible(False)
        left.addWidget(self.lbl_banner)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.box1, self.lbl_v1, self.video = self._video_box(TITLE_V1)
        self.box2, self.lbl_v2, self.video2 = self._video_box(TITLE_V2)
        self.splitter.addWidget(self.box1)
        self.splitter.addWidget(self.box2)
        left.addWidget(self.splitter, 1)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self.slider.sliderMoved.connect(self.on_slider_moved)
        left.addWidget(self.slider)

        ctrl = QHBoxLayout()
        self.btn_play = QPushButton("▶ Play (P)")
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.clicked.connect(self.toggle_play)
        ctrl.addWidget(self.btn_play)

        self.lbl_pos = QLabel("0:00:00.0 / 0:00:00.0")
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        f.setPointSize(13)
        f.setBold(True)
        self.lbl_pos.setFont(f)
        ctrl.addWidget(self.lbl_pos)

        self.lbl_speed = QLabel("1×")
        self.lbl_speed.setFont(f)
        ctrl.addWidget(self.lbl_speed)
        ctrl.addStretch(1)

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_mute.setCheckable(True)
        self.btn_mute.setToolTip("Stlmiť / zapnúť zvuk (zvuk ide z videa 1)")
        self.btn_mute.setFixedWidth(40)
        self.btn_mute.toggled.connect(self.set_muted)
        ctrl.addWidget(self.btn_mute)

        self.sld_volume = QSlider(Qt.Orientation.Horizontal)
        self.sld_volume.setRange(0, 100)
        self.sld_volume.setValue(100)
        self.sld_volume.setFixedWidth(110)
        self.sld_volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sld_volume.setToolTip("Hlasitosť (↑/↓)")
        self.sld_volume.valueChanged.connect(self.set_volume)
        ctrl.addWidget(self.sld_volume)

        self.lbl_volume = QLabel("100 %")
        self.lbl_volume.setMinimumWidth(48)
        ctrl.addWidget(self.lbl_volume)

        self.btn_export = QPushButton("Export CSV (Ctrl+S)")
        self.btn_export.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export.clicked.connect(self.export)
        ctrl.addWidget(self.btn_export)
        left.addLayout(ctrl)

        # synchronizácia videa 2
        self.sync_bar = QWidget()
        sl = QHBoxLayout(self.sync_bar)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("Synchronizácia – video 2 začalo neskôr o:"))
        self.sp_offset = QDoubleSpinBox()
        self.sp_offset.setRange(-86400.0, 86400.0)
        self.sp_offset.setDecimals(1)
        self.sp_offset.setSingleStep(0.1)
        self.sp_offset.setSuffix(" s")
        self.sp_offset.setKeyboardTracking(False)
        self.sp_offset.valueChanged.connect(self.on_offset_changed)
        self.sp_offset.editingFinished.connect(self.release_focus)
        sl.addWidget(self.sp_offset)
        sl.addWidget(QLabel("  posunúť video 2:"))
        for label, delta in (("◀ 1 s", -1.0), ("◀ 0,1 s", -0.1), ("0,1 s ▶", 0.1), ("1 s ▶", 1.0)):
            b = QPushButton(label)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, d=delta: self.shift_video2(d))
            sl.addWidget(b)
        hint = QLabel("(Ctrl+←/→, Ctrl+Shift+←/→)")
        hint.setStyleSheet("color:#666;")
        sl.addWidget(hint)
        sl.addStretch(1)
        left.addWidget(self.sync_bar)
        root.addLayout(left, 1)

        # pravá časť – panel
        right = QVBoxLayout()
        self.lbl_mode = QLabel()
        self.lbl_mode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_mode.setMinimumHeight(44)
        right.addWidget(self.lbl_mode)

        self.lbl_head = QLabel()
        self.lbl_head.setTextFormat(Qt.TextFormat.RichText)
        right.addWidget(self.lbl_head)

        self.lbl_counts = QLabel()
        self.lbl_counts.setTextFormat(Qt.TextFormat.RichText)
        right.addWidget(self.lbl_counts)

        self.grp_system = QGroupBox("Autá v systéme")
        gl = QVBoxLayout(self.grp_system)
        self.list_system = QListWidget()
        self.list_system.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_system.setMaximumHeight(190)
        self.list_system.setToolTip("► = auto, ktorému sa priradí ďalší výjazd (1–4); predvolene najstaršie. "
                                    "Ak z kruhu odchádza iné auto (napr. ide na bližší výjazd), "
                                    "vyberte ho Tab / Shift+Tab alebo kliknutím.")
        self.list_system.itemClicked.connect(self.on_system_clicked)
        gl.addWidget(self.list_system)
        right.addWidget(self.grp_system)

        g = QGroupBox("Posledných 10 udalostí")
        gl = QVBoxLayout(g)
        self.list_events = QListWidget()
        self.list_events.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        gl.addWidget(self.list_events)
        right.addWidget(g, 1)

        self.grp_smery = QGroupBox("Výjazdy z meraného ramena")
        gl = QVBoxLayout(self.grp_smery)
        self.lbl_smery = QLabel()
        self.lbl_smery.setTextFormat(Qt.TextFormat.RichText)
        gl.addWidget(self.lbl_smery)
        right.addWidget(self.grp_smery)

        g = QGroupBox("Klávesy")
        gl = QVBoxLayout(g)
        self.lbl_legend = QLabel()
        self.lbl_legend.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_legend.setWordWrap(True)
        gl.addWidget(self.lbl_legend)
        right.addWidget(g)

        panel = QWidget()
        panel.setLayout(right)
        panel.setFixedWidth(410)
        root.addWidget(panel)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setStyleSheet("color:#b35c00; font-weight:bold;")
        self.statusBar().addPermanentWidget(self.lbl_warn, 1)

    def _build_menu(self):
        m_file = self.menuBar().addMenu("&Súbor")
        a = QAction("Export CSV", self)
        a.setShortcut(QKeySequence("Ctrl+S"))
        a.triggered.connect(self.export)
        m_file.addAction(a)
        a = QAction("Nová relácia…", self)
        a.triggered.connect(self.new_session)
        m_file.addAction(a)
        m_file.addSeparator()
        a = QAction("Video 2 (kruhový objazd)…", self)
        a.triggered.connect(self.choose_video2)
        m_file.addAction(a)
        a = QAction("Odobrať video 2", self)
        a.triggered.connect(self.remove_video2)
        m_file.addAction(a)
        m_file.addSeparator()
        a = QAction("Koniec", self)
        a.setShortcut(QKeySequence("Ctrl+Q"))
        a.triggered.connect(self.close)
        m_file.addAction(a)

        m_set = self.menuBar().addMenu("&Nastavenia")
        grp = QActionGroup(self)
        grp.setExclusive(True)
        self.csv_actions = {}
        for key, info in CSV_FORMATS.items():
            a = QAction("CSV: " + info["label"], self)
            a.setCheckable(True)
            a.setChecked(key == self.session.csv_format)
            a.triggered.connect(lambda checked, k=key: self.set_csv_format(k))
            grp.addAction(a)
            m_set.addAction(a)
            self.csv_actions[key] = a
        m_set.addSeparator()
        a = QAction("Názvy ramien…", self)
        a.triggered.connect(self.edit_arm_names)
        m_set.addAction(a)

        m_view = self.menuBar().addMenu("&Zobrazenie")
        a = QAction("Videá vedľa seba / pod sebou", self)
        a.setShortcut(QKeySequence("Ctrl+L"))
        a.triggered.connect(self.toggle_layout)
        m_view.addAction(a)

    def _build_player(self):
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(self.sld_volume.value() / 100.0)
        self.audio.setMuted(self.btn_mute.isChecked())
        self.player.setVideoOutput(self.video)
        self.player.durationChanged.connect(self.on_duration)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.playbackStateChanged.connect(self.on_play_state)
        self.player.errorOccurred.connect(self.on_player_error)

        # video 2 bez zvuku (bez audio výstupu Qt zvuk neprehráva)
        self.player2 = QMediaPlayer(self)
        self.player2.setVideoOutput(self.video2)
        self.player2.durationChanged.connect(self.on_duration2)
        self.player2.mediaStatusChanged.connect(self.on_media_status2)
        self.player2.errorOccurred.connect(self.on_player_error)

    def load_media(self):
        self.player.stop()
        self.player.setPlaybackRate(SPEEDS[self.speed_idx])
        self.player.setSource(QUrl.fromLocalFile(self.session.video_path))
        self.load_video2()
        self.setWindowTitle(f"Merač – {os.path.basename(self.session.video_path)} – "
                            f"rameno {self.session.rameno_label(self.session.rameno)}")
        for k, a in self.csv_actions.items():
            a.setChecked(k == self.session.csv_format)

    def load_video2(self):
        self.player2.stop()
        self.video2_ready = False
        self.duration2_ms = 0
        self._p2_requested = None
        has2 = bool(self.session.video2_path)
        self.box2.setVisible(has2)
        self.sync_bar.setVisible(has2)
        self.lbl_v1.setVisible(has2)
        self.sp_offset.blockSignals(True)
        self.sp_offset.setValue(self.session.posun_video2_s)
        self.sp_offset.blockSignals(False)
        if has2:
            self.player2.setPlaybackRate(SPEEDS[self.speed_idx])
            self.player2.setSource(QUrl.fromLocalFile(self.session.video2_path))
        else:
            self.player2.setSource(QUrl())

    # ---- prehrávač -------------------------------------------------------------------
    def on_duration(self, ms: int):
        self.duration_ms = ms
        self.slider.setRange(0, max(0, ms))

    def on_duration2(self, ms: int):
        self.duration2_ms = ms

    def on_media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            fps = self.player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
            try:
                fps = float(fps)
            except (TypeError, ValueError):
                fps = 0.0
            self.frame_ms = int(round(1000.0 / fps)) if fps and fps > 0 else DEFAULT_FRAME_MS
            if self.pending_seek is not None:
                ms = int(self.pending_seek * 1000)
                self.player.setPosition(ms)
                self.pending_seek = None
                self.player.pause()
                self.sync_video2(force=True, master_ms=ms)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.warn("Video 1 sa nedá prehrať (nepodporovaný formát alebo chýbajúce kodeky) – "
                      "pozri README, časť Riešenie problémov.", 20000)

    def on_media_status2(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            if not self.video2_ready:
                self.video2_ready = True
                self.sync_video2(force=True)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.warn("Video 2 sa nedá prehrať (nepodporovaný formát alebo chýbajúce kodeky).", 20000)

    def on_player_error(self, error, text):
        if error != QMediaPlayer.Error.NoError:
            self.warn(f"Chyba prehrávača: {text}", 15000)

    def on_play_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setText("⏸ Pauza (P)" if playing else "▶ Play (P)")

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def now_s(self) -> float:
        return round_t(self.player.position() / 1000.0)

    # ---- synchronizácia videa 2 ------------------------------------------------------
    def video2_active(self) -> bool:
        """Video 2 je načítané a v aktuálnom čase má obraz (nezačalo neskôr / neskončilo)."""
        return bool(self.session.video2_path) and self.video2_ready and self.v2_state == "ok"

    def pos2_ms(self) -> int:
        if not self.is_playing() and self._p2_requested is not None:
            return self._p2_requested
        return self.player2.position()

    def sync_video2(self, force: bool = False, master_ms: Optional[int] = None):
        """Udržiava video 2 na pozícii (video 1 − posun)."""
        if not self.session.video2_path or not self.video2_ready:
            return
        m = self.player.position() if master_ms is None else master_ms
        target = int(round(m - self.session.posun_video2_s * 1000))
        p2_playing = self.player2.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if target < 0 or (self.duration2_ms and target > self.duration2_ms):
            self.v2_state = "pred" if target < 0 else "po"
            if p2_playing:
                self.player2.pause()
            clamp = 0 if target < 0 else self.duration2_ms
            if force or self._p2_requested != clamp:
                self.player2.setPosition(clamp)
                self._p2_requested = clamp
            return
        self.v2_state = "ok"
        if self.is_playing():
            now = time.monotonic()
            if not p2_playing:
                self.player2.setPlaybackRate(SPEEDS[self.speed_idx])
                self.player2.setPosition(target)
                self.player2.play()
                self._p2_requested = target
                self._last_resync = now
            elif force or (abs(self.player2.position() - target) > SYNC_TOLERANCE_MS
                           and now - self._last_resync > SYNC_COOLDOWN_S):
                self.player2.setPosition(target)
                self._p2_requested = target
                self._last_resync = now
        else:
            if p2_playing:
                self.player2.pause()
            if force or self._p2_requested != target:
                self.player2.setPosition(target)
                self._p2_requested = target

    def on_offset_changed(self, value: float):
        self.session.posun_video2_s = round(value, 1)
        if self.session.events:
            self.session.exported = False
        self._after_change(info=None)
        self.sync_video2(force=True)

    def shift_video2(self, delta_s: float):
        """Posunie obraz videa 2 o delta_s (+ = dopredu) voči videu 1."""
        if not self.session.video2_path:
            self.warn("Video 2 nie je načítané (Súbor → Video 2).")
            return
        self.sp_offset.setValue(round(self.sp_offset.value() - delta_s, 1))
        self.info(f"Video 2 začalo neskôr o {sk_num(self.session.posun_video2_s)} s")

    def release_focus(self):
        self.centralWidget().setFocus()

    def choose_video2(self):
        start = self.session.video2_path or os.path.dirname(self.session.video_path)
        path, _ = QFileDialog.getOpenFileName(self, "Video 2 – kruhový objazd", start, VIDEO_FILTER)
        if not path:
            return
        self.session.video2_path = path
        self._after_change(info=None)
        self.load_video2()
        self.info("Video 2 načítané. Synchronizujte ho posunom (Ctrl+←/→).")

    def remove_video2(self):
        if not self.session.video2_path:
            return
        self.session.video2_path = None
        self._after_change(info=None)
        self.load_video2()
        self.info("Video 2 odobraté. Výjazdy zaznamenané z videa 2 si ponechávajú posun "
                  f"{sk_num(self.session.posun_video2_s)} s.")

    def toggle_layout(self):
        o = self.splitter.orientation()
        self.splitter.setOrientation(Qt.Orientation.Vertical if o == Qt.Orientation.Horizontal
                                     else Qt.Orientation.Horizontal)

    # ---- ovládanie prehrávania -------------------------------------------------------
    def toggle_play(self):
        if self.is_playing():
            self.player.pause()
            self.sync_video2(force=True)
        else:
            self.player.setPlaybackRate(SPEEDS[self.speed_idx])
            self.player2.setPlaybackRate(SPEEDS[self.speed_idx])
            self.player.play()
            self.sync_video2(force=True)

    def seek_to_ms(self, pos: int):
        pos = int(max(0, min(pos, self.duration_ms or pos)))
        self.player.setPosition(pos)
        self.sync_video2(force=True, master_ms=pos)

    def seek_rel(self, delta_ms: int):
        self.seek_to_ms(self.player.position() + delta_ms)

    def step_frame(self, direction: int):
        if self.is_playing():
            self.player.pause()
        self.seek_rel(direction * self.frame_ms)

    def change_speed(self, delta: int):
        self.speed_idx = max(0, min(len(SPEEDS) - 1, self.speed_idx + delta))
        self.player.setPlaybackRate(SPEEDS[self.speed_idx])
        self.player2.setPlaybackRate(SPEEDS[self.speed_idx])
        self.lbl_speed.setText(f"{SPEEDS[self.speed_idx]:g}×")

    def set_volume(self, value: int):
        self.audio.setVolume(value / 100.0)
        self.lbl_volume.setText(f"{value} %")
        if value > 0 and self.btn_mute.isChecked():
            self.btn_mute.setChecked(False)

    def change_volume(self, delta: int):
        self.sld_volume.setValue(max(0, min(100, self.sld_volume.value() + delta)))

    def set_muted(self, muted: bool):
        self.audio.setMuted(muted)
        self.btn_mute.setText("🔇" if muted else "🔊")

    def on_slider_pressed(self):
        self.slider_down = True

    def on_slider_released(self):
        self.slider_down = False
        self.seek_to_ms(self.slider.value())

    def on_slider_moved(self, value: int):
        self.seek_to_ms(value)

    def on_tick(self):
        pos = self.player.position()
        if not self.slider_down:
            self.slider.setValue(pos)
        self.sync_video2()
        now = pos / 1000.0
        self.lbl_pos.setText(f"{fmt_t(now)} / {fmt_t(self.duration_ms / 1000.0)}   ({sk_num(now)} s)")
        if self.session.video2_path:
            if not self.video2_ready:
                suffix = "  – načítava sa…"
            elif self.v2_state == "pred":
                suffix = "  – ešte nezačalo"
            elif self.v2_state == "po":
                suffix = "  – skončilo"
            else:
                suffix = f"   {fmt_t(self.pos2_ms() / 1000.0)}"
            self.lbl_v2.setText(TITLE_V2 + suffix)
            self.lbl_v1.setText(f"{TITLE_V1}   {fmt_t(now)}")
        self.lbl_banner.setVisible(self.state.excluded_at(round_t(now)))
        self.update_system_list(round_t(now))

    # ---- klávesy -------------------------------------------------------------------
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.KeyPress and self.isActiveWindow() \
                and QApplication.activeModalWidget() is None:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)):
                return super().eventFilter(obj, ev)
            if self.handle_key(ev):
                ev.accept()
                return True
        return super().eventFilter(obj, ev)

    def handle_key(self, ev) -> bool:
        key = ev.key()
        text = ev.text()
        mods = ev.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        repeat = ev.isAutoRepeat()

        # rýchlosť – aj cez AltGr na slovenskej klávesnici
        if key == Qt.Key.Key_BracketLeft or text == "[":
            self.change_speed(-1)
            return True
        if key == Qt.Key.Key_BracketRight or text == "]":
            self.change_speed(+1)
            return True
        # čísla 1–4 (na SK rozložení so Shiftom alebo na numerickej klávesnici)
        if not ctrl and (key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4)
                         or text in ("1", "2", "3", "4")):
            if repeat:
                return True
            k = {Qt.Key.Key_1: 1, Qt.Key.Key_2: 2, Qt.Key.Key_3: 3, Qt.Key.Key_4: 4}.get(key) or int(text)
            if self.mode == MODE_SMERY:
                self.record_smer(k)
            else:
                self.record_exit(k)
            return True
        # synchronizácia videa 2
        if ctrl and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 1.0 if shift else 0.1
            self.shift_video2(step if key == Qt.Key.Key_Right else -step)
            return True

        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                   | Qt.KeyboardModifier.MetaModifier):
            return False

        if key == Qt.Key.Key_P:
            if not repeat:
                self.toggle_play()
            return True
        if key == Qt.Key.Key_Left:
            self.seek_rel(-(5000 if shift else 1000))
            return True
        if key == Qt.Key.Key_Right:
            self.seek_rel(5000 if shift else 1000)
            return True
        if key == Qt.Key.Key_Up:
            self.change_volume(+10)
            return True
        if key == Qt.Key.Key_Down:
            self.change_volume(-10)
            return True
        if key == Qt.Key.Key_Comma:
            self.step_frame(-1)
            return True
        if key == Qt.Key.Key_Period:
            self.step_frame(+1)
            return True
        if key == Qt.Key.Key_Tab:
            self.cycle_target(+1)
            return True
        if key == Qt.Key.Key_Backtab:
            self.cycle_target(-1)
            return True
        if key == Qt.Key.Key_Escape:
            if self.selected_seq is not None:
                self.selected_seq = None
                self.update_system_list(self.now_s())
                self.info("Ďalší výjazd: najstaršie auto v systéme.")
            return True
        if key == Qt.Key.Key_M:
            if not repeat:
                self.toggle_mode()
            return True
        if key == Qt.Key.Key_Backspace:
            if not repeat:
                self.undo()
            return True
        if key == Qt.Key.Key_N:
            if not repeat:
                self.add_note()
            return True
        if key == Qt.Key.Key_X:
            if not repeat:
                self.toggle_exclusion()
            return True
        if key == Qt.Key.Key_Space:
            if repeat:
                return True
            if self.mode == MODE_CASY:
                self.record_arrival()
            else:
                self.warn("Medzerník je v režime Smery vypnutý (prepnúť: M).")
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if repeat:
                return True
            if self.mode == MODE_CASY:
                self.record_entry()
            else:
                self.warn("Enter je v režime Smery vypnutý (prepnúť: M).")
            return True
        return False

    # ---- udalosti ------------------------------------------------------------------
    def _time_for(self, source: int) -> tuple:
        """(čas vo vlastnom videu, zdroj 1/2, čas na osi videa 1).

        Príchody sa merajú na videu 1, výjazdy a vjazdy na videu 2 (ak je načítané a beží);
        čas z videa 2 sa ukladá vo vlastnom čase videa 2, takže neskoršia oprava posunu
        automaticky opraví aj zaznamenané výjazdy.
        """
        if source == 2 and self.video2_active():
            t2 = round_t(self.pos2_ms() / 1000.0)
            return t2, 2, round_t(t2 + self.session.posun_video2_s)
        t = self.now_s()
        return t, 1, t

    def _check_order(self, t_eff: float):
        last = self.session.last_event
        if last is not None:
            last_t = self.session.effective_t(last)
            if t_eff < last_t:
                self.warn(f"Udalosť ({fmt_t(t_eff)}) je skôr než posledná zaznamenaná ({fmt_t(last_t)}) – "
                          f"zaradí sa podľa času videa.")

    def _after_change(self, info: Optional[str] = None):
        self.state = self.session.state()
        try:
            self.session.save()
        except OSError as ex:
            self.warn(f"AUTOSAVE ZLYHAL: {ex}", 15000)
        self.refresh()
        if info:
            self.info(info)

    def car_by_seq(self, seq: Optional[int]) -> Optional[Car]:
        return next((c for c in self.state.cars if c.seq_prichod == seq), None)

    def current_target(self, now: float) -> Optional[Car]:
        cands = self.state.exit_candidates(now)
        if not cands:
            return None
        for c in cands:
            if c.seq_prichod == self.selected_seq:
                return c
        return cands[0]

    def cycle_target(self, direction: int):
        if self.mode != MODE_CASY:
            return
        _, _, te = self._time_for(2)
        cands = self.state.exit_candidates(te)
        if not cands:
            self.warn("V systéme nie je žiadne auto.")
            return
        cur = self.current_target(te)
        i = cands.index(cur) if cur in cands else 0
        nxt = cands[(i + direction) % len(cands)]
        self.selected_seq = nxt.seq_prichod
        self.update_system_list(self.now_s())
        self.info(f"Ďalší výjazd sa priradí autu #{nxt.auto_id}")

    def on_system_clicked(self, item: QListWidgetItem):
        seq = item.data(Qt.ItemDataRole.UserRole)
        _, _, te = self._time_for(2)
        if any(c.seq_prichod == seq for c in self.state.exit_candidates(te)):
            self.selected_seq = seq
            car = self.car_by_seq(seq)
            self.update_system_list(self.now_s())
            if car is not None:
                self.info(f"Ďalší výjazd sa priradí autu #{car.auto_id}")
        else:
            self.warn("Toto auto už má zaznamenaný výjazd.")

    def record_arrival(self):
        t, z, te = self._time_for(1)
        self._check_order(te)
        e = self.session.add_event(T_PRICHOD, t, zdroj=z)
        self._after_change()
        car = self.car_by_seq(e.seq)
        if car is not None:
            self.info(f"PRÍCHOD auto #{car.auto_id} v {fmt_t(te)}, pred ním v systéme {car.n_pred} áut")

    def record_exit(self, k: int):
        t, z, te = self._time_for(2)
        target = self.current_target(te)
        if target is None:
            self.warn(f"V čase {fmt_t(te)} nie je v systéme žiadne auto bez výjazdu – výjazd sa nezaznamenal.")
            return
        self._check_order(te)
        self.session.add_event(T_VYJAZD, t, ciel_rameno=k, ref_seq=target.seq_prichod, zdroj=z)
        self.selected_seq = None
        self._after_change()
        car = self.car_by_seq(target.seq_prichod)
        if car is not None and car.t_vyjazd is not None:
            src = " (čas z videa 2)" if z == 2 else ""
            self.info(f"VÝJAZD auto #{car.auto_id} → rameno {self.session.rameno_label(k)} v {fmt_t(te)}, "
                      f"v systéme {sk_num(car.cas_v_systeme)} s{src}")
        else:
            self.warn("Výjazd sa nepodarilo priradiť autu – skontrolujte posledné udalosti.")

    def record_entry(self):
        t, z, te = self._time_for(2)
        if not self.state.entry_candidates(te):
            self.warn(f"Front je prázdny v čase {fmt_t(te)} – vjazd sa nezaznamenal.")
            return
        self._check_order(te)
        e = self.session.add_event(T_VJAZD, t, zdroj=z)
        self._after_change()
        row = self.state.by_seq.get(e.seq, {})
        car = next((c for c in self.state.cars if c.auto_id == row.get("auto_id")), None)
        if car is not None:
            self.info(f"VJAZD auto #{car.auto_id} v {fmt_t(te)}, čakalo {sk_num(car.cakanie)} s")

    def toggle_exclusion(self):
        t, z, te = self._time_for(1)
        self._check_order(te)
        e = self.session.add_event(T_VYLUC, t, zdroj=z)
        self._after_change()
        row = self.state.by_seq.get(e.seq, {})
        if row.get("typ") == "VYLUC_ZACIATOK":
            self.info(f"ZAČIATOK vylúčeného úseku v {fmt_t(te)}")
        else:
            self.info(f"KONIEC vylúčeného úseku v {fmt_t(te)}")

    def add_note(self):
        t, z, te = self._time_for(1)
        was_playing = self.is_playing()
        if was_playing:
            self.toggle_play()
        # autá v systéme, najnovšie hore (typicky vozidlo, ktoré práve prešlo referenčným bodom)
        cars = list(reversed(self.state.in_system_at(te)))
        target = self.current_target(te)
        if target is not None and target not in cars:
            cars.append(target)
        items = []
        for c in cars:
            label = f"#{c.auto_id}  (príchod {fmt_t(c.t_prichod)})"
            if c.typ_vozidla:
                label += f"  [{c.typ_vozidla}]"
            items.append((label, c.seq_prichod))
        default = cars[0].seq_prichod if cars else None
        dlg = NoteDialog(self, te, items, default)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        ref, typ, text = dlg.values()
        dlg.deleteLater()
        if accepted:
            self._check_order(te)
            self.session.add_event(T_POZNAMKA, t, poznamka=text, ref_seq=ref, typ_vozidla=typ, zdroj=z)
            car = self.car_by_seq(ref)
            self._after_change()
            what = " ".join(x for x in (typ, f"„{text}“" if text else "") if x)
            self.info(f"POZNÁMKA {('auto #' + str(car.auto_id) + ' ') if car else ''}{what}")
        if was_playing:
            self.toggle_play()

    def record_smer(self, k: int):
        t, z, te = self._time_for(2)
        self._check_order(te)
        self.session.add_event(T_SMER, t, ciel_rameno=k, zdroj=z)
        self._after_change()
        self.info(f"SMER: rameno {self.session.rameno_label(self.session.rameno)} → "
                  f"výjazd {self.session.rameno_label(k)} v {fmt_t(te)}")

    def undo(self):
        e = self.session.undo()
        if e is None:
            self.warn("Nie je čo vrátiť.")
            return
        self._after_change()
        label = {T_PRICHOD: "PRÍCHOD", T_VJAZD: "VJAZD", T_VYJAZD: f"VÝJAZD → {e.ciel_rameno}",
                 T_VYLUC: "vylúčený úsek", T_POZNAMKA: "poznámka",
                 T_SMER: f"smer → {e.ciel_rameno}"}.get(e.typ, e.typ)
        self.info(f"UNDO: {label} v {fmt_t(self.session.effective_t(e))}")

    def toggle_mode(self):
        self.mode = MODE_SMERY if self.mode == MODE_CASY else MODE_CASY
        self.refresh()
        self.info("Režim SMERY: klávesy 1–4 = len smer, bez priradenia k autu" if self.mode == MODE_SMERY
                  else "Režim ČASY: medzerník = príchod, 1–4 = výjazd")

    # ---- export / relácia -----------------------------------------------------------
    def export(self) -> bool:
        try:
            paths = self.session.export_csv()
        except OSError as ex:
            QMessageBox.critical(self, "Export zlyhal",
                                 f"{ex}\n\nAk je súbor otvorený v Exceli alebo LibreOffice, zatvorte ho "
                                 "a exportujte znova.")
            return False
        self.info("Exportované do " + ", ".join(os.path.basename(p) for p in paths), 10000)
        self.refresh()
        return True

    def set_csv_format(self, key: str):
        self.session.csv_format = key
        try:
            self.session.save()
        except OSError:
            pass
        self.info(f"Formát CSV: {CSV_FORMATS[key]['label']}")

    def edit_arm_names(self):
        dlg = ArmNamesDialog(self, self.session.nazvy_ramien)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        values = dlg.values()
        dlg.deleteLater()
        if not accepted:
            return
        self.session.set_nazvy(values)
        self._after_change("Názvy ramien uložené.")

    def new_session(self):
        if not self.confirm_unexported():
            return
        dlg = SetupDialog(self, initial=self.session)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        result = dlg.result_session() if accepted else None
        dlg.deleteLater()
        if result is None:
            return
        self.session, self.pending_seek = result
        self.state = self.session.state()
        self.mode = MODE_CASY
        self.selected_seq = None
        self.load_media()
        self.refresh()

    def confirm_unexported(self) -> bool:
        """True = môže sa pokračovať (zavrieť / vymeniť reláciu)."""
        if self.session.exported or not self.session.events:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Neexportované zmeny")
        box.setText("Relácia má zmeny, ktoré neboli exportované do CSV.\n"
                    "Autosave (JSON) je uložený, takže sa nič nestratí.")
        b_exp = box.addButton("Exportovať a pokračovať", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Pokračovať bez exportu", QMessageBox.ButtonRole.DestructiveRole)
        b_cancel = box.addButton("Zrušiť", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_exp)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_cancel:
            return False
        if clicked is b_exp:
            return self.export()
        return True

    def closeEvent(self, ev):
        if self.confirm_unexported():
            self.tick.stop()
            self.player.stop()
            self.player2.stop()
            ev.accept()
        else:
            ev.ignore()

    # ---- zobrazenie ----------------------------------------------------------------
    def warn(self, text: str, ms: int = 5000):
        self.lbl_warn.setStyleSheet("color:#b35c00; font-weight:bold;")
        self.lbl_warn.setText("⚠ " + text)
        self.warn_timer.start(ms)

    def info(self, text: str, ms: int = 4000):
        self.lbl_warn.setStyleSheet("color:#1b5e20; font-weight:bold;")
        self.lbl_warn.setText(text)
        self.warn_timer.start(ms)

    def refresh(self):
        s = self.session
        st = self.state
        names = SEP.join(f"<b>{k}</b> = {s.nazov(k)}" for k in ARMS if s.nazov(k))
        names = f"<br>Ramená: {names}" if names else ""
        sync = LEGEND_SYNC if s.video2_path else ""
        if self.mode == MODE_CASY:
            self.lbl_mode.setText("REŽIM: ČASY + VÝJAZDY  (M → len Smery)")
            self.lbl_mode.setStyleSheet("background:#1565c0; color:white; font-size:17px; font-weight:bold; padding:6px;")
            self.lbl_legend.setText(LEGEND_CASY + sync + names)
        else:
            self.lbl_mode.setText("REŽIM: LEN SMERY  (M → Časy)")
            self.lbl_mode.setStyleSheet("background:#6a1b9a; color:white; font-size:17px; font-weight:bold; padding:6px;")
            self.lbl_legend.setText(LEGEND_SMERY + sync + names)

        obs = f"{SEP}{s.pozorovatel}" if s.pozorovatel else ""
        real = f"{SEP}začiatok {s.realny_zaciatok}" if s.realny_zaciatok else ""
        exp = "" if s.exported else f"{SEP}<span style='color:#b35c00'>neexportované</span>"
        self.lbl_head.setText(f"<b>Rameno príchodu {s.rameno_label(s.rameno)}</b>{obs}{real}{exp}")

        now = self.now_s()
        err = (f"<br><span style='color:#c62828'><b>Chybné udalosti: {st.n_chyby}</b> "
               f"(pozri zoznam udalostí, opravte Backspace)</span>") if st.n_chyby else ""
        self.lbl_counts.setText(
            f"<table cellspacing=5>"
            f"<tr><td>Príchody:</td><td><b>{st.n_prichody}</b></td>"
            f"<td>Výjazdy:</td><td><b>{st.n_vyjazdy}</b></td></tr>"
            f"<tr><td>V systéme teraz:</td><td><b>{len(st.in_system_at(now))}</b></td>"
            f"<td>Vjazdy (volit.):</td><td><b>{st.n_vjazdy}</b></td></tr>"
            f"<tr><td>Vylúčené úseky:</td><td><b>{st.n_vyluc}</b></td>"
            f"<td>Iné vozidlá:</td><td><b>{st.n_ine}</b></td></tr>"
            f"</table>{err}")

        # posledných 10 udalostí podľa poradia zaznamenania (najnovšia hore)
        by_seq = st.by_seq
        self.list_events.clear()
        for e in reversed(s.events[-10:]):
            r = by_seq.get(e.seq, {})
            typ = r.get("typ", e.typ)
            car = f"  auto #{r['auto_id']}" if r.get("auto_id") is not None else ""
            if e.typ == T_VYJAZD:
                detail = f"{car} → {s.rameno_label(e.ciel_rameno)}"
            elif e.typ == T_SMER:
                detail = f"  → {s.rameno_label(e.ciel_rameno)}"
            elif e.typ == T_POZNAMKA:
                detail = car + (f"  [{e.typ_vozidla}]" if e.typ_vozidla else "") + \
                    (f"  „{e.poznamka}“" if e.poznamka else "")
            else:
                detail = car
            if e.zdroj == 2:
                detail += "  (v2)"
            if r.get("chyba"):
                detail += f"  ⚠ {r['chyba']}"
            self.list_events.addItem(f"{fmt_t(r.get('t', e.t)):>10}  {typ}{detail}")

        total = sum(st.smery.values())
        rows = "".join(
            f"<tr><td>{s.rameno} → <b>{k}</b> {s.nazov(k)}</td><td align=right><b>{n}</b></td>"
            f"<td align=right>{(100.0 * n / total) if total else 0.0:.1f} %</td></tr>"
            for k, n in sorted(st.smery.items()))
        self.lbl_smery.setText(f"<table cellspacing=5>{rows}<tr><td>spolu</td>"
                               f"<td align=right><b>{total}</b></td><td></td></tr></table>")

        self.update_system_list(now)

    def update_system_list(self, now: float):
        st = self.state
        cars = st.in_system_at(now)
        target = self.current_target(now) if self.mode == MODE_CASY else None
        entries = []
        for c in cars:
            mark = "►" if c is target else "   "
            if c.t_vyjazd is not None:
                status = f"odíde v {fmt_t(c.t_vyjazd)}"
            elif c.t_vjazd is not None and c.t_vjazd <= now:
                status = f"v kruhu, v systéme {sk_num(max(0.0, now - c.t_prichod))} s"
            else:
                status = f"čaká {sk_num(max(0.0, now - c.t_prichod))} s"
            extra = (f"  [{c.typ_vozidla}]" if c.typ_vozidla else "") + ("  [vylúčené]" if c.vylucene else "")
            entries.append((f"{mark} #{c.auto_id}  –  {status}{extra}", c.seq_prichod))
        lw = self.list_system
        if lw.count() != len(entries):
            lw.clear()
            for text, seq in entries:
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, seq)
                lw.addItem(item)
        else:
            for i, (text, seq) in enumerate(entries):
                item = lw.item(i)
                if item.text() != text:
                    item.setText(text)
                if item.data(Qt.ItemDataRole.UserRole) != seq:
                    item.setData(Qt.ItemDataRole.UserRole, seq)
        title = f"Autá v systéme: {len(cars)}" + ("   (► = ďalší výjazd, Tab = iné auto)" if target else "")
        if self.grp_system.title() != title:
            self.grp_system.setTitle(title)


# --------------------------------------------------------------------------------------

def _log_exception(exc_type, exc, tb):
    """Neošetrená chyba: zapíše sa do merac_error.log a zobrazí sa v okne (aj bez konzoly)."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {dt.datetime.now().isoformat(timespec='seconds')} "
                    f"Python {sys.version.split()[0]} {sys.platform} =====\n{text}")
    except OSError:
        pass
    sys.__excepthook__(exc_type, exc, tb)
    try:
        if QApplication.instance() is not None:
            QMessageBox.critical(None, "Chyba aplikácie",
                                 "Nastala neočakávaná chyba. Zaznamenané udalosti sú v autosave súbore.\n\n"
                                 f"Podrobnosti sú v súbore:\n{ERROR_LOG}\n\n{text[-1500:]}")
    except Exception:  # noqa: BLE001 – posledná záchrana, nič viac sa nedá urobiť
        pass


def _dispose(*objs):
    """Zmaže okná, kým ešte žije QApplication.

    Bez toho PySide6 pri ukončení Pythonu maže okná až po QApplication a program
    spadne (Segmentation fault / Bus error), napr. po zrušení úvodného dialógu.
    """
    import shiboken6
    for o in objs:
        try:
            if o is not None and shiboken6.isValid(o):
                shiboken6.delete(o)
        except Exception:  # noqa: BLE001 – pri ukončovaní nič iné nerobíme
            pass


def main() -> int:
    sys.excepthook = _log_exception
    if sys.platform == "win32":
        # Na Windows je FFmpeg backend v PySide6 najspoľahlivejší pre H.264 z telefónov;
        # dá sa prepísať premennou prostredia QT_MEDIA_BACKEND=windows.
        os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    app = QApplication(sys.argv)
    app.setApplicationName("Merač dopravných udalostí")
    dlg = SetupDialog()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        _dispose(dlg)
        return 0
    session, seek = dlg.result_session()
    _dispose(dlg)
    win = MainWindow(session, seek)
    win.show()
    rc = app.exec()
    _dispose(win)
    return rc


if __name__ == "__main__":
    sys.exit(main())
