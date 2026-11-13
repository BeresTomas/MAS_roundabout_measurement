#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merač dopravných udalostí – ručné zaznamenávanie udalostí z videa
(meranie na kruhovom objazde).

Spustenie:  pip install pyside6 ;  python app.py

Jeden súbor. Logika (udalosti, prepočet frontu, export) je oddelená od GUI,
takže sa dá testovať bez okna (viď README).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
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
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
    QSlider, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

# --------------------------------------------------------------------------------------
# Konštanty
# --------------------------------------------------------------------------------------

SPEEDS = [0.25, 0.5, 1.0, 1.5, 2.0]
INTERVAL_S = 15 * 60          # 15-minútové intervaly pre smery
DEFAULT_FRAME_MS = 40         # krok po snímkoch, ak nepoznáme fps (0,04 s)

T_PRICHOD = "PRICHOD"
T_VJAZD = "VJAZD"
T_VYLUC = "VYLUC"             # interný prepínač; pri prepočte sa stane VYLUC_ZACIATOK / VYLUC_KONIEC
T_POZNAMKA = "POZNAMKA"
T_SMER = "SMER"

MODE_CASY = "CASY"
MODE_SMERY = "SMERY"

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
    seq: int                       # poradie zaznamenania (pre undo)
    typ: str                       # PRICHOD / VJAZD / VYLUC / POZNAMKA / SMER
    t: float                       # čas videa v sekundách (0,1 s)
    ciel_rameno: Optional[int] = None
    poznamka: str = ""

    def to_json(self) -> dict:
        return {"seq": self.seq, "typ": self.typ, "t": self.t,
                "ciel_rameno": self.ciel_rameno, "poznamka": self.poznamka}

    @staticmethod
    def from_json(d: dict) -> "Event":
        typ = d["typ"]
        if typ in ("VYLUC_ZACIATOK", "VYLUC_KONIEC"):
            typ = T_VYLUC
        return Event(seq=int(d["seq"]), typ=typ, t=float(d["t"]),
                     ciel_rameno=d.get("ciel_rameno"), poznamka=d.get("poznamka") or "")


@dataclass
class Car:
    auto_id: int
    t_prichod: float
    n_pred: int
    vylucene: int
    seq_prichod: int
    t_vjazd: Optional[float] = None

    @property
    def cakanie(self) -> Optional[float]:
        if self.t_vjazd is None:
            return None
        return round_t(self.t_vjazd - self.t_prichod)


@dataclass
class State:
    """Stav prepočítaný zo zoznamu udalostí zoradených podľa času videa."""
    cars: list = field(default_factory=list)                 # list[Car] podľa auto_id
    segments: list = field(default_factory=list)             # list[(start, end|None)]
    rows: list = field(default_factory=list)                 # udalosti v časovom poradí s odvodenými údajmi
    smery: dict = field(default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0})
    orphans: int = 0                                          # VJAZD pri prázdnom fronte (po undo/pretočení)

    @property
    def by_seq(self) -> dict:
        return {r["seq"]: r for r in self.rows}

    def queue_at(self, now: float) -> list:
        """Autá, ktoré v čase videa `now` čakajú (prišli a ešte nevošli)."""
        return [c for c in self.cars
                if c.t_prichod <= now and (c.t_vjazd is None or c.t_vjazd > now)]

    def excluded_at(self, now: float) -> bool:
        return any(s <= now and (e is None or now < e) for s, e in self.segments)

    @property
    def n_prichody(self) -> int:
        return len(self.cars)

    @property
    def n_vjazdy(self) -> int:
        return sum(1 for c in self.cars if c.t_vjazd is not None)

    @property
    def n_vyluc(self) -> int:
        return len(self.segments)


def compute_state(events: list) -> State:
    """Prehrá udalosti od začiatku v poradí podľa času videa (pri rovnosti podľa poradia zápisu)."""
    st = State()
    queue: list = []
    excluded = False
    for e in sorted(events, key=lambda ev: (ev.t, ev.seq)):
        row = {"seq": e.seq, "typ": e.typ, "t": e.t, "auto_id": None,
               "ciel_rameno": e.ciel_rameno, "poznamka": e.poznamka, "chyba": False}
        if e.typ == T_PRICHOD:
            car = Car(auto_id=len(st.cars) + 1, t_prichod=e.t, n_pred=len(queue),
                      vylucene=1 if excluded else 0, seq_prichod=e.seq)
            st.cars.append(car)
            queue.append(car)
            row["auto_id"] = car.auto_id
        elif e.typ == T_VJAZD:
            if queue:
                car = queue.pop(0)
                car.t_vjazd = e.t
                row["auto_id"] = car.auto_id
            else:
                st.orphans += 1
                row["chyba"] = True
        elif e.typ == T_VYLUC:
            if excluded:
                start, _ = st.segments[-1]
                st.segments[-1] = (start, e.t)
                excluded = False
                row["typ"] = "VYLUC_KONIEC"
            else:
                st.segments.append((e.t, None))
                excluded = True
                row["typ"] = "VYLUC_ZACIATOK"
        elif e.typ == T_SMER:
            if e.ciel_rameno in st.smery:
                st.smery[e.ciel_rameno] += 1
        st.rows.append(row)
    return st


# --------------------------------------------------------------------------------------
# Relácia (nastavenia + udalosti + autosave + export)
# --------------------------------------------------------------------------------------

class Session:
    VERSION = 1

    def __init__(self, video_path: str, rameno: int, pozorovatel: str = "",
                 realny_zaciatok: Optional[str] = None, csv_format: str = "sk",
                 nazvy_ramien: Optional[dict] = None):
        self.video_path = video_path
        self.rameno = int(rameno)
        self.nazvy_ramien = {k: "" for k in (1, 2, 3, 4)}
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
        """'2 (Od Nitry)' alebo len '2' pre zobrazenie v UI."""
        if k is None:
            return "–"
        n = self.nazov(k)
        return f"{k} ({n})" if n else str(k)

    @staticmethod
    def nazvy_from_sibling_autosaves(video_path: str) -> dict:
        """Názvy ramien z ľubovoľného autosave toho istého videa (ramená sú spoločné)."""
        found = {k: "" for k in (1, 2, 3, 4)}
        for k in (1, 2, 3, 4):
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

    def add_event(self, typ: str, t: float, ciel_rameno: Optional[int] = None,
                  poznamka: str = "") -> Event:
        e = Event(seq=self.next_seq, typ=typ, t=round_t(t), ciel_rameno=ciel_rameno,
                  poznamka=poznamka)
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
        return compute_state(self.events)

    # ---- JSON autosave -----------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "version": self.VERSION,
            "video": self.video_path,
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
                d.get("realny_zaciatok"), d.get("csv_format", "sk"), d.get("nazvy_ramien"))
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

    def real_time(self, t: float) -> str:
        if not self.realny_zaciatok:
            return ""
        start = parse_hms(self.realny_zaciatok) or 0
        total = (start + t) % 86400
        h = int(total // 3600)
        m = int((total % 3600) // 60)
        s = total - h * 3600 - m * 60
        return f"{h:02d}:{m:02d}:{s:04.1f}".replace(".", CSV_FORMATS[self.csv_format]["decimal"])

    def export_csv(self) -> list:
        """Vytvorí *_udalosti.csv, *_auta.csv, *_smery.csv vedľa videa. Vráti cesty."""
        fmt = CSV_FORMATS[self.csv_format]
        st = self.state()
        base = self.export_base
        paths = []

        def writer(path):
            f = open(path, "w", encoding="utf-8-sig", newline="")
            return f, csv.writer(f, delimiter=fmt["delimiter"], quoting=csv.QUOTE_MINIMAL)

        # 1) udalosti
        p = base + "_udalosti.csv"
        f, w = writer(p)
        with f:
            w.writerow(["event_id", "typ", "cas_video_s", "cas_realny", "rameno",
                        "auto_id", "ciel_rameno", "poznamka", "rameno_nazov", "ciel_rameno_nazov"])
            for r in st.rows:
                w.writerow([r["seq"], r["typ"], self._num(r["t"]), self.real_time(r["t"]),
                            self.rameno,
                            r["auto_id"] if r["auto_id"] is not None else "",
                            r["ciel_rameno"] if r["ciel_rameno"] is not None else "",
                            r["poznamka"] or "",
                            self.nazov(self.rameno), self.nazov(r["ciel_rameno"])])
        paths.append(p)

        # 2) autá
        p = base + "_auta.csv"
        f, w = writer(p)
        with f:
            w.writerow(["auto_id", "rameno", "t_prichod_s", "n_pred", "t_vjazd_s",
                        "cakanie_s", "vylucene", "interval_od_predch_s", "rameno_nazov"])
            prev: Optional[Car] = None
            for c in st.cars:
                interval = None
                if prev is not None and not prev.vylucene and not c.vylucene:
                    interval = round_t(c.t_prichod - prev.t_prichod)
                w.writerow([c.auto_id, self.rameno, self._num(c.t_prichod), c.n_pred,
                            self._num(c.t_vjazd), self._num(c.cakanie), c.vylucene,
                            self._num(interval), self.nazov(self.rameno)])
                prev = c
        paths.append(p)

        # 3) smery – spolu + po 15-minútových intervaloch
        p = base + "_smery.csv"
        f, w = writer(p)
        with f:
            w.writerow(["interval_od_s", "rameno_z", "rameno_na", "pocet", "podiel",
                        "rameno_z_nazov", "rameno_na_nazov"])
            smer_events = [e for e in self.events if e.typ == T_SMER and e.ciel_rameno in (1, 2, 3, 4)]

            def block(label, evs):
                total = len(evs)
                for k in (1, 2, 3, 4):
                    n = sum(1 for e in evs if e.ciel_rameno == k)
                    podiel = (n / total) if total else 0.0
                    w.writerow([label, self.rameno, k, n, self._num(podiel, 4),
                                self.nazov(self.rameno), self.nazov(k)])

            block("spolu", smer_events)
            if smer_events:
                last_start = int(max(e.t for e in smer_events) // INTERVAL_S) * INTERVAL_S
                for start in range(0, last_start + 1, INTERVAL_S):
                    evs = [e for e in smer_events if start <= e.t < start + INTERVAL_S]
                    block(start, evs)
        paths.append(p)

        self.exported = True
        try:
            self.save()
        except OSError:
            pass
        return paths


# --------------------------------------------------------------------------------------
# Dialóg nastavenia relácie
# --------------------------------------------------------------------------------------

class ArmNamesWidget(QGroupBox):
    """Štyri polia na voliteľné názvy ramien 1–4."""

    def __init__(self, parent=None):
        super().__init__("Názvy ramien (voliteľné, spoločné pre celé video)", parent)
        form = QFormLayout(self)
        self.edits = {}
        for k in (1, 2, 3, 4):
            ed = QLineEdit()
            ed.setPlaceholderText(f"napr. ulica / smer pre rameno {k}")
            form.addRow(f"Rameno {k}:", ed)
            self.edits[k] = ed

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


class SetupDialog(QDialog):
    def __init__(self, parent=None, initial: Optional[Session] = None):
        super().__init__(parent)
        self.setWindowTitle("Nastavenie relácie")
        self.setMinimumWidth(560)
        self._loaded: Optional[Session] = None

        form = QFormLayout()

        row = QHBoxLayout()
        self.ed_video = QLineEdit()
        self.ed_video.setPlaceholderText("Cesta k video súboru…")
        btn = QPushButton("Vybrať…")
        btn.clicked.connect(self.pick_video)
        row.addWidget(self.ed_video, 1)
        row.addWidget(btn)
        form.addRow("Video:", row)

        self.sp_rameno = QSpinBox()
        self.sp_rameno.setRange(1, 4)
        form.addRow("Merané rameno (vjazd):", self.sp_rameno)

        self.ed_obs = QLineEdit()
        self.ed_obs.setPlaceholderText("voliteľné")
        form.addRow("Pozorovateľ:", self.ed_obs)

        self.ed_real = QLineEdit()
        self.ed_real.setPlaceholderText("HH:MM:SS (voliteľné)")
        self.ed_real.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"^(|([01]?\d|2[0-3]):[0-5]\d:[0-5]\d)$")))
        form.addRow("Skutočný čas začiatku videa:", self.ed_real)

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
        intro = QLabel("Jedna relácia = jedno video + jedno merané rameno. "
                       "Autosave sa ukladá vedľa videa ako <i>&lt;video&gt;_rameno&lt;k&gt;.json</i>.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(buttons)

        self.ed_video.textChanged.connect(self.update_continue)
        self.sp_rameno.valueChanged.connect(self.update_continue)
        self.chk_cont.toggled.connect(self.on_cont_toggled)

        if initial is not None:
            self.ed_video.setText(initial.video_path)
            self.sp_rameno.setValue(initial.rameno)
            self.ed_obs.setText(initial.pozorovatel)
            self.ed_real.setText(initial.realny_zaciatok or "")
            idx = self.cb_csv.findData(initial.csv_format)
            if idx >= 0:
                self.cb_csv.setCurrentIndex(idx)
            self.arm_names.set_values(initial.nazvy_ramien)
        self.update_continue()

    def pick_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Vyberte video", self.ed_video.text() or os.path.expanduser("~"),
            "Video (*.mp4 *.mkv *.avi *.mov *.m4v *.webm *.mts *.ts *.wmv);;Všetky súbory (*)")
        if path:
            self.ed_video.setText(path)

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
                last = max((e.t for e in self._loaded.events), default=0.0)
                self.chk_cont.setEnabled(True)
                self.chk_cont.setChecked(True)
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
            idx = self.cb_csv.findData(self._loaded.csv_format)
            if idx >= 0:
                self.cb_csv.setCurrentIndex(idx)
            self.arm_names.set_values(self._loaded.nazvy_ramien)

    def accept(self):
        video = self.ed_video.text().strip()
        if not video or not os.path.isfile(video):
            QMessageBox.warning(self, "Chýba video", "Vyberte existujúci video súbor.")
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
        real = self.ed_real.text().strip() or None
        csv_fmt = self.cb_csv.currentData()
        if self.chk_cont.isChecked() and self._loaded is not None:
            s = self._loaded
            s.video_path = video
            s.pozorovatel = self.ed_obs.text().strip()
            s.realny_zaciatok = real
            s.csv_format = csv_fmt
            s.set_nazvy(self.arm_names.values())
            seek = max((e.t for e in s.events), default=None)
            return s, seek
        s = Session(video, self.sp_rameno.value(), self.ed_obs.text().strip(), real, csv_fmt,
                    self.arm_names.values())
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

LEGEND_CASY = (
    "<b>P</b> play/pauza &nbsp;·&nbsp; <b>←/→</b> ±1 s &nbsp;·&nbsp; <b>Shift+←/→</b> ±5 s<br>"
    "<b>, / .</b> snímok späť/vpred &nbsp;·&nbsp; <b>[ / ]</b> rýchlosť<br>"
    "<b>MEDZERNÍK</b> príchod &nbsp;·&nbsp; <b>ENTER</b> vjazd do kruhu<br>"
    "<b>X</b> vylúčený úsek (zač./koniec) &nbsp;·&nbsp; <b>N</b> poznámka<br>"
    "<b>BACKSPACE</b> undo &nbsp;·&nbsp; <b>M</b> režim Smery &nbsp;·&nbsp; <b>Ctrl+S</b> export"
)
LEGEND_SMERY = (
    "<b>P</b> play/pauza &nbsp;·&nbsp; <b>←/→</b> ±1 s &nbsp;·&nbsp; <b>Shift+←/→</b> ±5 s<br>"
    "<b>, / .</b> snímok späť/vpred &nbsp;·&nbsp; <b>[ / ]</b> rýchlosť<br>"
    "<b>1 / 2 / 3 / 4</b> auto išlo na výjazd 1–4<br>"
    "<b>BACKSPACE</b> undo &nbsp;·&nbsp; <b>M</b> režim Časy &nbsp;·&nbsp; <b>Ctrl+S</b> export<br>"
    "<i>Medzerník a Enter sú v tomto režime vypnuté.</i>"
)


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
        self.resize(1360, 820)

    # ---- UI ------------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # ľavá časť – video
        left = QVBoxLayout()
        self.lbl_banner = QLabel("VYLÚČENÝ ÚSEK")
        self.lbl_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_banner.setStyleSheet(
            "background:#c62828; color:white; font-size:26px; font-weight:bold; padding:8px;")
        self.lbl_banner.setVisible(False)
        left.addWidget(self.lbl_banner)

        self.video = QVideoWidget()
        self.video.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video.setStyleSheet("background:black;")
        left.addWidget(self.video, 1)

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

        self.btn_export = QPushButton("Export CSV (Ctrl+S)")
        self.btn_export.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export.clicked.connect(self.export)
        ctrl.addWidget(self.btn_export)
        left.addLayout(ctrl)
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

        g = QGroupBox("Front čakajúcich áut (ID – čaká)")
        gl = QVBoxLayout(g)
        self.list_queue = QListWidget()
        self.list_queue.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_queue.setMaximumHeight(150)
        gl.addWidget(self.list_queue)
        right.addWidget(g)

        g = QGroupBox("Posledných 10 udalostí")
        gl = QVBoxLayout(g)
        self.list_events = QListWidget()
        self.list_events.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        gl.addWidget(self.list_events)
        right.addWidget(g, 1)

        self.grp_smery = QGroupBox("Smery z meraného ramena")
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
        panel.setFixedWidth(400)
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

    def _build_player(self):
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.durationChanged.connect(self.on_duration)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.playbackStateChanged.connect(self.on_play_state)
        self.player.errorOccurred.connect(self.on_player_error)

    def load_media(self):
        self.player.stop()
        self.player.setPlaybackRate(SPEEDS[self.speed_idx])
        self.player.setSource(QUrl.fromLocalFile(self.session.video_path))
        self.setWindowTitle(f"Merač – {os.path.basename(self.session.video_path)} – rameno {self.session.rameno}")
        for k, a in self.csv_actions.items():
            a.setChecked(k == self.session.csv_format)

    # ---- player ------------------------------------------------------------------
    def on_duration(self, ms: int):
        self.duration_ms = ms
        self.slider.setRange(0, max(0, ms))

    def on_media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            fps = self.player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
            try:
                fps = float(fps)
            except (TypeError, ValueError):
                fps = 0.0
            self.frame_ms = int(round(1000.0 / fps)) if fps and fps > 0 else DEFAULT_FRAME_MS
            if self.pending_seek is not None:
                self.player.setPosition(int(self.pending_seek * 1000))
                self.pending_seek = None
                self.player.pause()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.warn("Video sa nedá prehrať (nepodporovaný formát alebo chýbajúce kodeky) – "
                      "pozri README, časť Riešenie problémov.", 20000)

    def on_player_error(self, error, text):
        if error != QMediaPlayer.Error.NoError:
            self.warn(f"Chyba prehrávača: {text}", 15000)

    def on_play_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setText("⏸ Pauza (P)" if playing else "▶ Play (P)")

    def now_s(self) -> float:
        return round_t(self.player.position() / 1000.0)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.setPlaybackRate(SPEEDS[self.speed_idx])
            self.player.play()

    def seek_rel(self, delta_ms: int):
        pos = self.player.position() + delta_ms
        pos = max(0, min(pos, self.duration_ms or pos))
        self.player.setPosition(int(pos))

    def step_frame(self, direction: int):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        self.seek_rel(direction * self.frame_ms)

    def change_speed(self, delta: int):
        self.speed_idx = max(0, min(len(SPEEDS) - 1, self.speed_idx + delta))
        self.player.setPlaybackRate(SPEEDS[self.speed_idx])
        self.lbl_speed.setText(f"{SPEEDS[self.speed_idx]:g}×")

    def on_slider_pressed(self):
        self.slider_down = True

    def on_slider_released(self):
        self.slider_down = False
        self.player.setPosition(self.slider.value())

    def on_slider_moved(self, value: int):
        self.player.setPosition(value)

    def on_tick(self):
        pos = self.player.position()
        if not self.slider_down:
            self.slider.setValue(pos)
        now = pos / 1000.0
        self.lbl_pos.setText(f"{fmt_t(now)} / {fmt_t(self.duration_ms / 1000.0)}   ({sk_num(now)} s)")
        self.lbl_banner.setVisible(self.state.excluded_at(round_t(now)))
        self.update_queue(round_t(now))

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
        repeat = ev.isAutoRepeat()

        # rýchlosť – aj cez AltGr na slovenskej klávesnici
        if key == Qt.Key.Key_BracketLeft or text == "[":
            self.change_speed(-1)
            return True
        if key == Qt.Key.Key_BracketRight or text == "]":
            self.change_speed(+1)
            return True
        # čísla 1–4 (na SK rozložení so Shiftom) – len v režime Smery
        if key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4) or text in ("1", "2", "3", "4"):
            if repeat:
                return True
            k = {Qt.Key.Key_1: 1, Qt.Key.Key_2: 2, Qt.Key.Key_3: 3, Qt.Key.Key_4: 4}.get(key) or int(text)
            if self.mode == MODE_SMERY:
                self.record_smer(k)
            else:
                self.warn("Klávesy 1–4 fungujú len v režime Smery (prepnúť: M).")
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
        if key == Qt.Key.Key_Comma:
            self.step_frame(-1)
            return True
        if key == Qt.Key.Key_Period:
            self.step_frame(+1)
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
    def _check_order(self, t: float):
        last = self.session.last_event
        if last is not None and t < last.t:
            self.warn(f"Udalosť ({fmt_t(t)}) je skôr než posledná zaznamenaná ({fmt_t(last.t)}) – "
                      f"zaradí sa podľa času videa.")

    def _after_change(self):
        self.state = self.session.state()
        try:
            self.session.save()
        except OSError as ex:
            self.warn(f"AUTOSAVE ZLYHAL: {ex}", 15000)
        self.refresh()

    def record_arrival(self):
        t = self.now_s()
        self._check_order(t)
        e = self.session.add_event(T_PRICHOD, t)
        self._after_change()
        row = self.state.by_seq.get(e.seq, {})
        self.info(f"PRÍCHOD auto #{row.get('auto_id')} v {fmt_t(t)}, pred ním {self.n_pred_of(e.seq)} áut")

    def n_pred_of(self, seq: int) -> int:
        for c in self.state.cars:
            if c.seq_prichod == seq:
                return c.n_pred
        return 0

    def record_entry(self):
        t = self.now_s()
        if not self.state.queue_at(t):
            self.warn(f"Front je prázdny v čase {fmt_t(t)} – vjazd sa nezaznamenal.")
            return
        self._check_order(t)
        e = self.session.add_event(T_VJAZD, t)
        self._after_change()
        row = self.state.by_seq.get(e.seq, {})
        car = next((c for c in self.state.cars if c.auto_id == row.get("auto_id")), None)
        if car is not None:
            self.info(f"VJAZD auto #{car.auto_id} v {fmt_t(t)}, čakalo {sk_num(car.cakanie)} s")

    def toggle_exclusion(self):
        t = self.now_s()
        self._check_order(t)
        e = self.session.add_event(T_VYLUC, t)
        self._after_change()
        row = self.state.by_seq.get(e.seq, {})
        if row.get("typ") == "VYLUC_ZACIATOK":
            self.info(f"ZAČIATOK vylúčeného úseku v {fmt_t(t)}")
        else:
            self.info(f"KONIEC vylúčeného úseku v {fmt_t(t)}")

    def add_note(self):
        t = self.now_s()
        was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if was_playing:
            self.player.pause()
        text, ok = QInputDialog.getText(self, "Poznámka", f"Poznámka k času {fmt_t(t)}:")
        if ok and text.strip():
            self._check_order(t)
            self.session.add_event(T_POZNAMKA, t, poznamka=text.strip())
            self._after_change()
            self.info(f"POZNÁMKA v {fmt_t(t)}: {text.strip()}")
        if was_playing:
            self.player.play()

    def record_smer(self, k: int):
        t = self.now_s()
        self._check_order(t)
        self.session.add_event(T_SMER, t, ciel_rameno=k)
        self._after_change()
        self.info(f"SMER: rameno {self.session.rameno_label(self.session.rameno)} → "
                  f"výjazd {self.session.rameno_label(k)} v {fmt_t(t)}")

    def undo(self):
        e = self.session.undo()
        if e is None:
            self.warn("Nie je čo vrátiť.")
            return
        self._after_change()
        label = {T_PRICHOD: "PRÍCHOD", T_VJAZD: "VJAZD", T_VYLUC: "vylúčený úsek",
                 T_POZNAMKA: "poznámka", T_SMER: f"smer → {e.ciel_rameno}"}.get(e.typ, e.typ)
        self.info(f"UNDO: {label} v {fmt_t(e.t)}")

    def toggle_mode(self):
        self.mode = MODE_SMERY if self.mode == MODE_CASY else MODE_CASY
        self.refresh()
        self.info("Režim SMERY: klávesy 1–4 = výjazd" if self.mode == MODE_SMERY
                  else "Režim ČASY: medzerník = príchod, Enter = vjazd")

    # ---- export / relácia -----------------------------------------------------------
    def export(self) -> bool:
        try:
            paths = self.session.export_csv()
        except OSError as ex:
            QMessageBox.critical(self, "Export zlyhal", str(ex))
            return False
        self.info("Exportované: " + ", ".join(os.path.basename(p) for p in paths), 10000)
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
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.session.set_nazvy(dlg.values())
        try:
            self.session.save()
        except OSError as ex:
            self.warn(f"AUTOSAVE ZLYHAL: {ex}", 15000)
        self.refresh()
        self.info("Názvy ramien uložené.")

    def new_session(self):
        if not self.confirm_unexported():
            return
        dlg = SetupDialog(self, initial=self.session)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.session, self.pending_seek = dlg.result_session()
        self.state = self.session.state()
        self.mode = MODE_CASY
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
        b_no = box.addButton("Pokračovať bez exportu", QMessageBox.ButtonRole.DestructiveRole)
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
        if self.mode == MODE_CASY:
            self.lbl_mode.setText("REŽIM: ČASY  (M → Smery)")
            self.lbl_mode.setStyleSheet("background:#1565c0; color:white; font-size:18px; font-weight:bold; padding:6px;")
            self.lbl_legend.setText(LEGEND_CASY)
        else:
            self.lbl_mode.setText("REŽIM: SMERY  (M → Časy)")
            self.lbl_mode.setStyleSheet("background:#6a1b9a; color:white; font-size:18px; font-weight:bold; padding:6px;")
            names = " &nbsp;·&nbsp; ".join(f"<b>{k}</b> = {s.nazov(k)}" for k in (1, 2, 3, 4) if s.nazov(k))
            self.lbl_legend.setText(LEGEND_SMERY + (f"<br>{names}" if names else ""))

        obs = f" &nbsp;·&nbsp; {s.pozorovatel}" if s.pozorovatel else ""
        real = f" &nbsp;·&nbsp; začiatok {s.realny_zaciatok}" if s.realny_zaciatok else ""
        exp = "" if s.exported else " &nbsp;·&nbsp; <span style='color:#b35c00'>neexportované</span>"
        self.lbl_head.setText(f"<b>Rameno {s.rameno_label(s.rameno)}</b>{obs}{real}{exp}")

        extra = f"<br><span style='color:#c62828'>VJAZD bez auta vo fronte: {st.orphans}</span>" if st.orphans else ""
        self.lbl_counts.setText(
            f"<table cellspacing=6>"
            f"<tr><td>Príchody:</td><td><b>{st.n_prichody}</b></td>"
            f"<td>Vjazdy:</td><td><b>{st.n_vjazdy}</b></td></tr>"
            f"<tr><td>Vo fronte (celkom):</td><td><b>{st.n_prichody - st.n_vjazdy}</b></td>"
            f"<td>Vylúčené úseky:</td><td><b>{st.n_vyluc}</b></td></tr>"
            f"<tr><td>Smery (spolu):</td><td><b>{sum(st.smery.values())}</b></td>"
            f"<td>Udalostí:</td><td><b>{len(s.events)}</b></td></tr>"
            f"</table>{extra}")

        # posledných 10 udalostí podľa poradia zaznamenania (najnovšia hore)
        by_seq = st.by_seq
        self.list_events.clear()
        for e in reversed(s.events[-10:]):
            r = by_seq.get(e.seq, {})
            typ = r.get("typ", e.typ)
            detail = ""
            if r.get("auto_id") is not None:
                detail = f"  auto #{r['auto_id']}"
            elif r.get("chyba"):
                detail = "  (bez auta!)"
            if e.typ == T_SMER:
                detail = f"  → výjazd {e.ciel_rameno}"
            if e.typ == T_POZNAMKA:
                detail = f"  „{e.poznamka}“"
            self.list_events.addItem(f"{fmt_t(e.t):>10}  {typ}{detail}")

        total = sum(st.smery.values())
        rows = "".join(
            f"<tr><td>{s.rameno} → <b>{k}</b> {s.nazov(k)}</td><td align=right><b>{n}</b></td>"
            f"<td align=right>{(100.0 * n / total) if total else 0.0:.1f} %</td></tr>"
            for k, n in sorted(st.smery.items()))
        self.lbl_smery.setText(f"<table cellspacing=6>{rows}<tr><td>spolu</td><td align=right><b>{total}</b></td><td></td></tr></table>")

        self.update_queue(self.now_s())

    def update_queue(self, now: float):
        q = self.state.queue_at(now)
        lines = [f"#{c.auto_id}  –  čaká {sk_num(max(0.0, now - c.t_prichod))} s" + ("  [vylúčené]" if c.vylucene else "")
                 for c in q]
        if [self.list_queue.item(i).text() for i in range(self.list_queue.count())] != lines:
            self.list_queue.clear()
            for ln in lines:
                self.list_queue.addItem(ln)
        title = f"Front čakajúcich áut: {len(q)}"
        if self.list_queue.parentWidget().title() != title:
            self.list_queue.parentWidget().setTitle(title)


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
        return 0
    session, seek = dlg.result_session()
    win = MainWindow(session, seek)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
