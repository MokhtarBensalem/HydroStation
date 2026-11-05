# -*- coding: utf-8 -*-
"""
HYDROSTATION
Interface moderne pour :
- Bilan Hydrique Thornthwaite
- Méthode de Gumbel

Lancement :
    python main.py
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import statistics
import sys
import tempfile
import uuid
from pathlib import Path
from statistics import NormalDist
from typing import (
    List,
    Optional,
)

try:
    from PySide6.QtCore import (
        QFile,
        QIODevice,
        Qt,
        QEvent,
        QProcess,
        QProcessEnvironment,
        QSize,
        QTimer,
    )
    from PySide6.QtGui import (
        QColor,
        QFont,
        QIcon,
        QPalette,
        QPixmap,
    )
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QButtonGroup,
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QListWidget,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSizePolicy,
        QStackedWidget,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QProgressBar,
        QStyledItemDelegate,
        QVBoxLayout,
        QWidget,
    )
except Exception as exc:
    print("PySide6 n'est pas installé.")
    print("Installez les dépendances avec : python -m pip install -r requirements.txt")
    print(exc)
    raise

from PySide6.QtUiTools import QUiLoader

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib import rcParams
except Exception as exc:
    print("matplotlib n'est pas installé.")
    print("Installez les dépendances avec : python -m pip install -r requirements.txt")
    print(exc)
    raise

BASE_DIR = Path(__file__).resolve().parent
MODULES_DIR = BASE_DIR / "modules"
OUTPUTS_DIR = BASE_DIR / "outputs"
CONFIG_FILE = BASE_DIR / "config.json"
APP_FONT_FAMILY = "Segoe UI"  # Police unique de l’interface et des graphiques (Windows).
APP_FONT_SIZE_PT = 10
APP_BG_COLOR = "#F5F7FB"
APP_TEXT_COLOR = "#1F2937"
APP_BORDER_COLOR = "#DCE5F0"
APP_ICON_PATH = BASE_DIR / "assets" / "hydrostation.ico"
APP_LOGO_PATH = BASE_DIR / "assets" / "hydrostation_logo.png"
UI_DIR = BASE_DIR / "ui"


def load_designer_ui(filename: str, parent=None) -> QWidget:
    """Load a Qt Designer .ui file using PySide6 only."""
    ui_file = QFile(str(UI_DIR / filename))
    if not ui_file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError(f"Impossible d'ouvrir l'interface : {filename}")
    try:
        widget = QUiLoader().load(ui_file, parent)
    finally:
        ui_file.close()
    if widget is None:
        raise RuntimeError(f"Impossible de charger l'interface : {filename}")
    return widget


def ui_child(root: QWidget, widget_type, name: str):
    widget = root.findChild(widget_type, name)
    if widget is None:
        raise RuntimeError(f"Widget Qt Designer manquant : {name}")
    return widget


def put_in_host(root: QWidget, host_name: str, widget: QWidget) -> None:
    host = ui_child(root, QWidget, host_name)
    layout = host.layout()
    if layout is None:
        raise RuntimeError(f"Layout Qt Designer manquant : {host_name}")
    layout.addWidget(widget)


def windows_build_number() -> Optional[int]:
    """Return the real Windows build (not the Qt or Python version)."""
    if sys.platform != "win32":
        return None
    try:
        return int(sys.getwindowsversion().build)
    except (AttributeError, OSError, TypeError, ValueError):
        # When OS version detection is unavailable, use the conservative
        # native-titlebar path instead of risking a colored empty client area.
        return None


def supports_integrated_native_titlebar() -> bool:
    """Use Qt's expanded native caption only when Windows supports our DWM colors.

    On Windows 10, the Qt 6.9 expanded-client-area flags still exist, but
    DWMWA_CAPTION_COLOR / DWMWA_BORDER_COLOR are unsupported. Windows may
    consequently expose the user's *system accent color* across the enlarged
    window during maximize/restore instead of the app's painted background.
    The Windows 10 fallback keeps native resize/Snap/caption controls and
    leaves the actual application widgets and stylesheet untouched.
    """
    build = windows_build_number()
    return bool(
        build is not None and build >= 22000  # Windows 11
        and hasattr(Qt.WindowType, "ExpandedClientAreaHint")
        and hasattr(Qt.WindowType, "NoTitleBarBackgroundHint")
        and hasattr(Qt.WidgetAttribute, "WA_ContentsMarginsRespectsSafeArea")
    )


class NativeCaptionBlendBar(QFrame):
    """Empty app-colored draggable area; Windows itself renders caption buttons.

    Do not place Qt buttons in this widget: the rightmost part overlaps the
    Windows-managed minimize, maximize and close buttons on Qt 6.9+.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NativeCaptionBlendBar")
        self.setFixedHeight(44)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            host = self.window()
            if host.isMaximized():
                host.showNormal()
            else:
                host.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def match_native_windows_titlebar(window) -> None:
    """Configure the real Windows caption without touching the Qt application UI.

    Windows 11 supports per-window DWM caption/border colors. Windows 10 does
    not: asking DWM for these unsupported colors never fixed the observed
    purple accent-color flash. Instead, Windows 10 uses the ordinary native
    caption (see supports_integrated_native_titlebar), and its window-scoped
    DWM transition is disabled to avoid displaying a stale frame against an
    accent-colored background while maximising / restoring.
    """
    build = windows_build_number()
    if build is None:
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(window.winId()))
        dwmapi = ctypes.WinDLL("dwmapi")
        dwm_set = dwmapi.DwmSetWindowAttribute
        dwm_set.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm_set.restype = ctypes.c_long

        def rgbref(color: str) -> int:
            hex_color = color.lstrip("#")
            r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
            return r | (g << 8) | (b << 16)

        def set_dwm_dword(attribute: int, value: int) -> None:
            arg = wintypes.DWORD(value)
            dwm_set(hwnd, attribute, ctypes.byref(arg), ctypes.sizeof(arg))

        if build < 22000:
            # Supported since Windows Vista; applies to this HWND only (not
            # global Windows appearance or other applications). No change to
            # the native caption buttons, layout or application stylesheet.
            DWMWA_TRANSITIONS_FORCEDISABLED = 3
            set_dwm_dword(DWMWA_TRANSITIONS_FORCEDISABLED, 1)
            return

        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_BORDER_COLOR = 34
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36
        DWMWA_COLOR_NONE = 0xFFFFFFFE

        set_dwm_dword(DWMWA_USE_IMMERSIVE_DARK_MODE, 0)
        set_dwm_dword(DWMWA_CAPTION_COLOR, rgbref(APP_BG_COLOR))
        set_dwm_dword(DWMWA_TEXT_COLOR, rgbref(APP_TEXT_COLOR))
        if getattr(window, "_native_caption_integrated", False):
            set_dwm_dword(DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE)
        else:
            set_dwm_dword(DWMWA_BORDER_COLOR, rgbref(APP_BORDER_COLOR))
    except (AttributeError, OSError, ValueError):
        # DWM may be unavailable (or reject an attribute); the OS still owns
        # the buttons and all ordinary window operations.
        pass

MOIS = [
    "Septembre", "Octobre", "Novembre", "Décembre", "Janvier", "Février",
    "Mars", "Avril", "Mai", "Juin", "Juillet", "Août"
]
MOIS_CLES = ["sept", "oct", "nov", "dec", "janv", "fev", "mars", "avril", "mai", "juin", "juil", "aout"]
CL_DEFAULT = [1.03, 0.97, 0.86, 0.81, 0.87, 0.85, 1.03, 1.10, 1.21, 1.22, 1.24, 1.16]
RFU_MAX = 100.0
EXTS = {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods", ".csv", ".txt", ".tsv"}


def normaliser_texte(value) -> str:
    """Normalize text for robust station/month/header matching."""
    import unicodedata
    if value is None:
        return ""
    text = str(value).strip().lower()
    if text.lower() == "nan":
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    for old in ["\n", "\r", "\t", "_", "-", ".", ":", ";", "/", "\\", "(", ")", "[", "]", "{", "}"]:
        text = text.replace(old, " ")
    return " ".join(text.split())


def normaliser_compact(value) -> str:
    return normaliser_texte(value).replace(" ", "")


def load_config() -> dict:
    default = {
        "project_root": str(BASE_DIR),
        "templates": {
            "gumbel": "",
        },
        "last_paths": {},
    }
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            default.update(data)
            for stale in ("gis_engine", "auto_gis", "auto_arcmap"):
                default.pop(stale, None)
            paths = default.get("last_paths", {})
            for stale in list(paths):
                if "map" in stale or "gis" in stale or stale.startswith("tools_"):
                    paths.pop(stale, None)
        except Exception:
            pass
    return default


def saved_user_path(config: dict, key: str) -> str:
    """Only restore real, existing paths selected on this computer.

    A packaged relative output directory or stale path on another PC must
    never be mistaken for a user-selected file.
    """
    value = str(config.get("last_paths", {}).get(key, "")).strip()
    if not value:
        return ""
    path = Path(value)
    return str(path) if path.is_absolute() and path.exists() else ""


def validate_processing_paths(parent, sources: list[tuple[str, str]], output: str) -> bool:
    """Valide les sources et la sortie. Le modele Excel est desormais optionnel."""
    for label, path_text in sources:
        if not path_text.strip():
            QMessageBox.warning(parent, "Chemin requis", f"Sélectionnez un fichier ou dossier pour : {label}.")
            return False
        if not file_list(path_text):
            QMessageBox.warning(
                parent,
                "Données introuvables",
                f"Aucun fichier de données valide pour : {label}.\n{path_text}",
            )
            return False
    if not output.strip():
        QMessageBox.warning(
            parent,
            "Dossier requis",
            "Sélectionnez le dossier de sortie avant de lancer le traitement.",
        )
        return False
    target = resolve_user_path(output)
    if target.exists() and not target.is_dir():
        QMessageBox.warning(parent, "Dossier incorrect", "Le chemin de sortie désigne un fichier, pas un dossier.")
        return False
    return True


def save_config(config: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def resolve_user_path(path_text: str, base: Path = BASE_DIR) -> Path:
    path_text = str(path_text).strip().strip('"').strip("'")
    p = Path(path_text)
    if not p.is_absolute():
        p = base / p
    try:
        return p.resolve()
    except Exception:
        return p.absolute()


def file_list(path_text: str) -> List[Path]:
    p = resolve_user_path(path_text)
    if p.is_file():
        return [p] if p.suffix.lower() in EXTS and p.stat().st_size > 0 else []
    if p.is_dir():
        return sorted(
            [x for x in p.iterdir()
             if x.is_file() and x.suffix.lower() in EXTS and x.stat().st_size > 0],
            key=lambda x: x.name.lower()
        )
    return []










def fmt(value, nd=2) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        if not math.isfinite(float(value)):
            return ""
        if abs(float(value)) < 1e-12:
            value = 0.0
        return f"{float(value):.{nd}f}"
    except Exception:
        return ""








def error_word(count: int) -> str:
    return f"{count} erreur{'s' if count != 1 else ''}"




def parse_float(text: str) -> Optional[float]:
    text = str(text).strip().replace(",", ".")
    if text == "":
        return None
    try:
        value = float(text)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None



def export_table_xlsx(parent, table: QTableWidget, dialog_title: str, filename: str, sheet_title: str) -> None:
    try:
        from openpyxl import Workbook
    except Exception:
        QMessageBox.warning(parent, "openpyxl manquant", "Installez openpyxl avec requirements.txt")
        return

    path, _ = QFileDialog.getSaveFileName(
        parent, dialog_title, str(OUTPUTS_DIR / filename), "Excel (*.xlsx)"
    )
    if not path:
        return

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title
    for column in range(table.columnCount()):
        sheet.cell(1, column + 1).value = table.horizontalHeaderItem(column).text()
    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            item = table.item(row, column)
            text = item.text() if item else ""
            number = parse_float(text)
            sheet.cell(row + 2, column + 1).value = number if number is not None else text
    workbook.save(path)
    QMessageBox.information(parent, "Export", "Fichier créé :\n" + path)


def open_existing_output_folder(parent, text: str) -> None:
    folder = resolve_user_path(text) if text.strip() else None
    if folder is None or not folder.is_dir():
        QMessageBox.information(parent, "Dossier sortie", "Sélectionnez d'abord un dossier de sortie existant.")
        return
    if os.name == "nt":
        os.startfile(str(folder))
    else:
        import webbrowser
        webbrowser.open(folder.as_uri())


def export_station_report(parent, process: "ProcessBox") -> None:
    """Create the CSV only when the user chooses to save the final report."""
    if not process.report_ready:
        QMessageBox.warning(
            parent, "Rapport", "Aucun rapport disponible. Lancez d'abord un traitement."
        )
        return

    default_path = (process.report_directory or OUTPUTS_DIR) / "rapport_stations.csv"
    destination, _ = QFileDialog.getSaveFileName(
        parent,
        "Enregistrer le rapport des stations",
        str(default_path),
        "Fichier CSV (*.csv)",
    )
    if not destination:
        return

    try:
        with Path(destination).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=process.report_fields, delimiter=";")
            writer.writeheader()
            writer.writerows(process.report_rows)
    except (OSError, ValueError) as exc:
        QMessageBox.warning(parent, "Rapport", f"Enregistrement impossible :\n{exc}")
        return

    QMessageBox.information(parent, "Rapport", f"Rapport CSV :\n{destination}")


class ModernButton(QPushButton):
    def __init__(self, text: str, kind: str = "primary"):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setProperty("kind", kind)


class PathPicker(QWidget):
    def __init__(
        self,
        label: str,
        mode: str = "both",
        default: str = "",
        file_filter: str = "Fichiers (*.xlsx *.xls *.xlsm *.xlsb *.ods *.csv *.txt *.tsv);;Tous (*.*)",
    ):
        super().__init__()
        self.mode = mode
        self.file_filter = file_filter
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.label = QLabel(label)
        self.label.setObjectName("ProcessingFieldLabel")
        self.label.setFixedWidth(150)
        self.edit = QLineEdit(default)
        self.edit.setMinimumWidth(0)
        self.edit.setFixedHeight(38)
        self.edit.setClearButtonEnabled(True)
        if mode == "both":
            placeholder = "Aucun fichier ou dossier sélectionné"
        elif mode == "dir":
            placeholder = "Sélectionner un dossier"
        else:
            placeholder = label
        self.edit.setPlaceholderText(placeholder)
        self.btn_file = ModernButton("Fichier", "secondary")
        self.btn_dir = ModernButton("Dossier", "secondary")
        self.btn_file.setFixedWidth(82)
        self.btn_dir.setFixedWidth(82)
        layout.addWidget(self.label)
        layout.addWidget(self.edit, 1)
        if mode in ("both", "file"):
            layout.addWidget(self.btn_file)
        elif mode == "dir":
            # Reserve the file button's column so every input ends at
            # exactly the same x-position, including "Dossier sortie".
            layout.addSpacing(82)
        if mode in ("both", "dir"):
            layout.addWidget(self.btn_dir)
        self.btn_file.clicked.connect(self.choose_file)
        self.btn_dir.clicked.connect(self.choose_dir)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choisir un fichier", self.text() or str(BASE_DIR), self.file_filter
        )
        if path:
            self.edit.setText(path)

    def choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Choisir un dossier", self.text() or str(BASE_DIR))
        if path:
            self.edit.setText(path)


class ProcessBox(QWidget):
    """Lance les calculs sans terminal visible et affiche seulement l'avancement."""

    def __init__(self):
        super().__init__()
        self.process: Optional[QProcess] = None
        self.report_directory: Optional[Path] = None
        self.report_fields: List[str] = []
        self.report_rows: List[dict] = []
        self.report_ready = False
        self._cancel_path: Optional[Path] = None
        self._stream_buffer = ""
        self._total = 0
        self._processed = 0
        self._errors = 0
        self._complete = False
        self._cancelled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)

        self.status = QLabel("Prêt à lancer le traitement")
        self.status.setObjectName("ProgressStatus")
        layout.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p %")
        self.progress.setTextVisible(True)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.cancel_btn = ModernButton(
            "Annuler après la station en cours", "secondary"
        )
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self.cancel_safely)
        layout.addWidget(self.cancel_btn)

    def cancel_safely(self):
        """Termine la station en cours avant de préparer le rapport."""
        if not self.process or self.process.state() == QProcess.NotRunning:
            return
        if not self._cancel_path:
            return
        try:
            self._cancel_path.write_text("cancel", encoding="ascii")
        except OSError as exc:
            QMessageBox.warning(self, "Annulation", str(exc))
            return
        self._cancelled = True
        self.status.setText("Annulation demandée : fin de la station en cours…")
        self.cancel_btn.setEnabled(False)

    def clear(self):
        if self.process and self.process.state() != QProcess.NotRunning:
            return
        self.report_directory = None
        self.report_fields = []
        self.report_rows = []
        self.report_ready = False
        self._stream_buffer = ""
        self._total = 0
        self._processed = 0
        self._errors = 0
        self._complete = False
        self._cancelled = False
        self.progress.setValue(0)
        self.progress.hide()
        self.cancel_btn.hide()
        self.status.setText("Prêt à lancer le traitement")

    def append(self, text: str):
        """Analyse les messages du processus sans les afficher dans l'interface."""
        self._stream_buffer += str(text)
        while "\n" in self._stream_buffer:
            line, self._stream_buffer = self._stream_buffer.split("\n", 1)
            self._read_status_line(line.strip())
        if len(self._stream_buffer) > 20000:
            self._stream_buffer = self._stream_buffer[-20000:]

    def _read_status_line(self, line: str):
        folder = re.match(r"Dossier de sortie(?: cree)?\s*:\s*(.+)$", line)
        if folder:
            self.report_directory = Path(folder.group(1).strip())

        if line.startswith("HYDRO_REPORT_FIELDS:"):
            try:
                self.report_fields = json.loads(line.split(":", 1)[1])
            except (ValueError, TypeError):
                self.report_fields = []
            return
        if line.startswith("HYDRO_REPORT_ROW:"):
            try:
                self.report_rows.append(json.loads(line.split(":", 1)[1]))
            except (ValueError, TypeError):
                self.report_ready = False
            return
        if line == "HYDRO_REPORT_READY":
            self.report_ready = bool(self.report_fields)
            return

        # Display the current preparation stage.
        if line.startswith("HYDRO_PHASE:"):
            self.status.setText(line.split(":", 1)[1])
            return

        # Show real file-checking progress (first 40 percent).
        preflight = re.fullmatch(r"HYDRO_PREFLIGHT:(\d+)/(\d+)", line)
        if preflight:
            done = int(preflight.group(1))
            count = int(preflight.group(2))
            self.progress.setRange(0, 100)
            percent = round(40 * done / count) if count else 40
            self.progress.setValue(percent)
            self.status.setText(f"Vérification des fichiers : {done}/{count}")
            return

        total = re.search(r"HYDRO_TOTAL:(\d+)", line)
        progress = re.search(r"HYDRO_PROGRESS:(\d+)/(\d+)", line)
        errors = re.search(r"HYDRO_ERRORS:(\d+)", line)

        if total:
            self._total = int(total.group(1))
            self.progress.setRange(0, 100)
            self.progress.setValue(40)
            self.status.setText(
                f"Traitement en cours — 0/{self._total} stations"
            )
        if progress:
            self._processed = int(progress.group(1))
            self._total = int(progress.group(2))
            if self._total:
                percent = 40 + round(59 * self._processed / self._total)
                self.progress.setValue(min(99, percent))
            self.status.setText(
                f"Traitement en cours — {self._processed}/{self._total} stations"
            )
        if errors:
            self._errors = int(errors.group(1))
        if "HYDRO_CANCELLED" in line:
            self._cancelled = True
        if "HYDRO_DONE" in line:
            self._complete = True

    def _read_output(self):
        output = bytes(self.process.readAllStandardOutput())
        if output:
            self.append(output.decode("utf-8", errors="replace"))

    def run(self, args: List[str], cwd: Optional[Path] = None, on_finished=None):
        if self.process and self.process.state() != QProcess.NotRunning:
            QMessageBox.warning(self, "Traitement", "Un traitement est déjà en cours.")
            return

        self.clear()
        self.progress.show()
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText("Chargement des bibliothèques et préparation du traitement…")

        filename = "hydro_cancel_" + uuid.uuid4().hex + ".flag"
        self._cancel_path = Path(tempfile.gettempdir()) / filename

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.setProgram(sys.executable)
        self.process.setArguments([*args, "--cancel-file", str(self._cancel_path)])
        self.process.setWorkingDirectory(str(cwd or BASE_DIR))

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(
            lambda code, status: self._finished(code, on_finished)
        )
        self.process.errorOccurred.connect(self._process_error)
        self.process.start()

    def _process_error(self, error):
        if error != QProcess.FailedToStart:
            return
        self.status.setText("Erreur de lancement : " + self.process.errorString())
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.hide()
        self.progress.hide()
        self._remove_cancel_file()

    def _remove_cancel_file(self):
        if self._cancel_path:
            try:
                self._cancel_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._cancel_path = None

    def _finished(self, code, on_finished):
        self._read_output()
        if self._stream_buffer:
            self._read_status_line(self._stream_buffer.strip())
            self._stream_buffer = ""
        self._remove_cancel_file()
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.hide()
        self.progress.hide()

        if self._cancelled:
            message = "Traitement annulé ; consultez le rapport des stations"
        elif code == 0 and self._complete and self._total:
            self.progress.setValue(100)
            suffix = f" — {error_word(self._errors)}" if self._errors else ""
            message = (
                f"Traitement terminé : {self._processed}/{self._total} stations"
                f"{suffix}"
            )
        elif code == 0 and self._complete:
            message = "Traitement terminé : aucune station à traiter"
        elif code == 2 and self._complete:
            message = (
                f"Traitement terminé : {self._processed}/{self._total} stations"
                f" — {self._errors} fichiers ignorés ou en erreur"
            )
        else:
            message = f"Traitement interrompu ou échoué (code {code})"
        self.status.setText(message)

        if on_finished:
            on_finished(code)


class TableCellDelegate(QStyledItemDelegate):
    """Use a flat, full-cell editor instead of the global rounded path editor."""

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setObjectName("TableCellEditor")
            editor.setFrame(False)
            editor.setStyleSheet(
                "QLineEdit { background-color: #2563EB; color: white; "
                "selection-background-color: #1D4ED8; selection-color: white; "
                "border: none; border-radius: 0; margin: 0; padding: 0 6px; "
                "min-height: 0; }"
            )
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class ResponsiveTable(QTableWidget):
    """Adaptive columns: fit within useful limits, then scroll instead of clipping."""

    def __init__(self, rows: int, headers: List[str], minimums: List[int], maximums: List[int]):
        super().__init__(rows, len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.setItemDelegate(TableCellDelegate(self))
        self.verticalHeader().setDefaultSectionSize(32)
        self.horizontalHeader().setFixedHeight(39)
        self.setObjectName("DataTable")
        metrics = self.fontMetrics()
        self._column_preferreds = [max(w, metrics.horizontalAdvance(label) + 24)
                                   for w, label in zip(minimums, headers)]
        self._column_maximums = [max(mx, pref) for mx, pref in zip(maximums, self._column_preferreds)]
        # Hard minimums are deliberately smaller than the preferred widths.
        # This lets the table contract with the window before a horizontal
        # scrollbar becomes necessary. The first column keeps extra room for
        # month/observation names; compact numerical columns can shrink further.
        self._column_hard_minimums = []
        for index, (label, preferred) in enumerate(zip(headers, self._column_preferreds)):
            if index == 0:
                hard = 72
            else:
                hard = max(44, min(68, metrics.horizontalAdvance(label) + 12))
            self._column_hard_minimums.append(min(preferred, hard))
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.horizontalHeader().setMinimumSectionSize(40)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.horizontalHeader().setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._adapt_columns()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adapt_columns()

    def _adapt_columns(self):
        available = max(0, self.viewport().width() - 2)
        preferred = list(self._column_preferreds)
        hard = list(self._column_hard_minimums)

        if available >= sum(preferred):
            # Large window: start from the preferred widths and distribute the
            # free space proportionally, capped by each column maximum.
            widths = preferred
            remaining = available - sum(widths)
            while remaining > 0:
                expandable = [i for i, w in enumerate(widths) if w < self._column_maximums[i]]
                if not expandable:
                    break
                total_weight = sum(preferred[i] for i in expandable) or len(expandable)
                assigned = 0
                for i in expandable:
                    share = max(1, round(remaining * preferred[i] / total_weight))
                    delta = min(share, self._column_maximums[i] - widths[i], remaining - assigned)
                    if delta <= 0:
                        continue
                    widths[i] += delta
                    assigned += delta
                    if assigned >= remaining:
                        break
                if assigned <= 0:
                    break
                remaining -= assigned
            # If all caps were reached, spread the last pixels so the table
            # still touches both side borders instead of leaving a white gap.
            free = max(0, available - sum(widths))
            if free and widths:
                base, extra = divmod(free, len(widths))
                widths = [w + base + (1 if i < extra else 0) for i, w in enumerate(widths)]
        elif available >= sum(hard):
            # Smaller window: shrink every column proportionally down to its
            # hard minimum. This is the responsive behaviour requested for the
            # desktop window when it is restored from maximized state.
            widths = preferred
            need = sum(widths) - available
            capacities = [p - h for p, h in zip(preferred, hard)]
            total_capacity = sum(capacities)
            if total_capacity > 0:
                shrunk = 0
                for i, capacity in enumerate(capacities):
                    delta = min(capacity, round(need * capacity / total_capacity))
                    widths[i] -= delta
                    shrunk += delta
                # Correct rounding exactly so columns occupy the viewport.
                remaining_need = need - shrunk
                while remaining_need > 0:
                    changed = False
                    for i in range(len(widths)):
                        if widths[i] > hard[i]:
                            widths[i] -= 1
                            remaining_need -= 1
                            changed = True
                            if remaining_need == 0:
                                break
                    if not changed:
                        break
                free = max(0, available - sum(widths))
                if free and widths:
                    base, extra = divmod(free, len(widths))
                    widths = [w + base + (1 if i < extra else 0) for i, w in enumerate(widths)]
        else:
            # Extremely narrow window: keep a readable hard minimum and allow
            # the horizontal scrollbar only as a last resort.
            widths = hard

        for col, width in enumerate(widths):
            width = max(40, int(width))
            if self.columnWidth(col) != width:
                self.setColumnWidth(col, width)
        for col in range(self.columnCount()):
            header_item = self.horizontalHeaderItem(col)
            if header_item:
                header_item.setToolTip(header_item.text())


class PlotCanvas(FigureCanvas):
    def __init__(self, width=7, height=4, dpi=100):
        self.figure = Figure(figsize=(width, height), dpi=dpi, tight_layout=False)
        super().__init__(self.figure)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


class BilanPage(QWidget):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.block_update = False
        self.results = {}
        self.last_output_dir = None
        self.last_result_dir = None

        self._recalculate_timer = QTimer(self)
        self._recalculate_timer.setSingleShot(True)
        self._recalculate_timer.setInterval(300)
        self._recalculate_timer.timeout.connect(self.recalculate)

        form = load_designer_ui("bilan_page.ui", self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(form)

        self.tabs = ui_child(form, QTabWidget, "BilanTabs")
        self.table = self.tabs.widget(0)
        self.curves = self.tabs.widget(1)
        self.files = self.tabs.widget(2)
        self.table.layout().setStretch(1, 1)
        self.curves.layout().setStretch(2, 1)
        self.files.layout().setStretch(6, 1)

        self.bilan_table = ResponsiveTable(
            13, ["Mois", "Tp", "IT", "CL", "ETPC", "Pr", "BH", "CH", "VR", "RFU", "ETR", "Déficit", "Excédent"],
            [114] + [66] * 10 + [90, 94],
            [180] + [120] * 10 + [138, 140],
        )
        put_in_host(form, "BilanTableHost", self.bilan_table)
        self.bilan_table.itemChanged.connect(self.schedule_recalculate)

        self.bilan_canvas_climat = PlotCanvas(10, 5)
        self.bilan_canvas_rfu = PlotCanvas(10, 5)
        put_in_host(form, "BilanClimatHost", self.bilan_canvas_climat)
        put_in_host(form, "BilanRfuHost", self.bilan_canvas_rfu)
        self.bilan_curve_tabs = ui_child(form, QTabWidget, "BilanCurveTabs")

        self.bilan_temp = PathPicker("Température", "both", saved_user_path(config, "bilan_temp"))
        self.bilan_pr = PathPicker("Précipitation", "both", saved_user_path(config, "bilan_pr"))
        self.bilan_out = PathPicker("Dossier sortie", "dir", saved_user_path(config, "bilan_out"))
        put_in_host(form, "BilanTempHost", self.bilan_temp)
        put_in_host(form, "BilanPrHost", self.bilan_pr)
        put_in_host(form, "BilanOutHost", self.bilan_out)

        files_heading = ui_child(form, QLabel, "FilesSectionTitle")
        files_heading.setObjectName("SectionTitle")
        self.bilan_no_charts = ui_child(form, QPushButton, "BilanNoCharts")
        self.bilan_with_charts = ui_child(form, QPushButton, "BilanWithCharts")
        for button in (self.bilan_no_charts, self.bilan_with_charts):
            button.setObjectName("ChoiceToggle")
        self.bilan_mode_group = QButtonGroup(self)
        self.bilan_mode_group.setExclusive(True)
        self.bilan_mode_group.addButton(self.bilan_no_charts)
        self.bilan_mode_group.addButton(self.bilan_with_charts)
        self.bilan_no_charts.setChecked(True)

        self.bilan_process = ProcessBox()
        put_in_host(form, "BilanProcessHost", self.bilan_process)
        self.bilan_report_btn = ui_child(form, QPushButton, "BilanReportButton")

        ui_child(form, QPushButton, "BilanResetButton").clicked.connect(self.reset_table)
        ui_child(form, QPushButton, "BilanExportButton").clicked.connect(self.export_table)
        ui_child(form, QPushButton, "BilanRunButton").clicked.connect(self.run_bilan)
        ui_child(form, QPushButton, "BilanOpenButton").clicked.connect(
            lambda: self.open_output_folder(self.bilan_out.text())
        )
        self.bilan_report_btn.clicked.connect(self.save_bilan_report)

        self.reset_table()
        self.recalculate()

    def reset_table(self):
        self.block_update = True
        for r in range(12):
            vals = [MOIS[r], "", "", fmt(CL_DEFAULT[r], 2), "", "", "", "", "", "", "", "", ""]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c not in (1, 3, 5):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.bilan_table.setItem(r, c, item)
        for c, v in enumerate(["Annuel"] + [""] * 12):
            item = QTableWidgetItem(v)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.bilan_table.setItem(12, c, item)
        self.block_update = False
        self.recalculate()

    def schedule_recalculate(self):
        if self.block_update:
            return
        self._recalculate_timer.start()

    def _table_value(self, row: int, col: int, default: float = 0.0) -> float:
        item = self.bilan_table.item(row, col)
        val = parse_float(item.text() if item else "")
        return val if val is not None else default

    def _set_item(self, row: int, col: int, text: str, editable: bool = False):
        item = self.bilan_table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            self.bilan_table.setItem(row, col, item)
        if item.text() != text:
            item.setText(text)
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)

    def recalculate(self):
        if self.block_update:
            return
        self.block_update = True
        tp = [self._table_value(i, 1) for i in range(12)]
        cl = [self._table_value(i, 3, CL_DEFAULT[i]) for i in range(12)]
        pr = [self._table_value(i, 5) for i in range(12)]
        it = [(t / 5.0) ** 1.514 if t > 0 else 0.0 for t in tp]
        it_annuel = sum(it)
        etpc = []
        for i in range(12):
            if tp[i] > 0 and it_annuel > 0:
                exp = (1.6 * it_annuel / 100.0) + 0.5
                etpc.append(16.0 * ((10.0 * tp[i] / it_annuel) ** exp) * cl[i])
            else:
                etpc.append(0.0)
        bh, ch, vr, rfu, etr, deficit, exc = [], [], [], [], [], [], []
        rfu_prev = 0.0
        for i in range(12):
            bh_i = pr[i] - etpc[i]
            ch_i = 0.0 if etpc[i] == 0 else bh_i / etpc[i]
            rfu_i = max(0.0, min(RFU_MAX, rfu_prev + pr[i] - etpc[i]))
            vr_i = rfu_i - rfu_prev
            etr_i = min(etpc[i], pr[i] + max(0.0, rfu_prev - rfu_i))
            def_i = max(0.0, etpc[i] - etr_i)
            exc_i = max(0.0, rfu_prev + pr[i] - etpc[i] - RFU_MAX)
            bh.append(bh_i)
            ch.append(ch_i)
            vr.append(vr_i)
            rfu.append(rfu_i)
            etr.append(etr_i)
            deficit.append(def_i)
            exc.append(exc_i)
            rfu_prev = rfu_i
        columns = {2: it, 4: etpc, 6: bh, 7: ch, 8: vr, 9: rfu, 10: etr, 11: deficit, 12: exc}
        for c, values in columns.items():
            for r, value in enumerate(values):
                self._set_item(r, c, fmt(value, 2))
        annual = {
            1: sum(tp) / 12.0 if tp else 0, 2: sum(it), 3: "", 4: sum(etpc), 5: sum(pr), 6: sum(bh),
            7: "", 8: "", 9: "", 10: sum(etr), 11: sum(deficit), 12: sum(exc)
        }
        for c in range(1, 13):
            val = annual.get(c, "")
            self._set_item(12, c, fmt(val, 2) if not isinstance(val, str) else val)
        self.results = {
            "tp": tp,
            "cl": cl,
            "pr": pr,
            "it": it,
            "etpc": etpc,
            "bh": bh,
            "ch": ch,
            "vr": vr,
            "rfu": rfu,
            "etr": etr,
            "def": deficit,
            "exc": exc,
        }
        self.block_update = False
        self.update_plot()

    def update_plot(self):
        if not hasattr(self, "bilan_canvas_climat") or not hasattr(self, "bilan_canvas_rfu") or not self.results:
            return
        signature = tuple((key, tuple(round(float(v), 9) for v in self.results[key]))
                          for key in ("pr", "etpc", "etr", "tp", "exc", "def", "rfu"))
        if signature == getattr(self, "_last_plot_signature", None):
            return
        self._last_plot_signature = signature
        x = list(range(1, 13))

        fig1 = self.bilan_canvas_climat.figure
        fig1.clear()
        ax1 = fig1.add_axes([0.075, 0.12, 0.82, 0.65])
        h1, = ax1.plot(x, self.results["pr"], color="#2563EB", marker="o", linewidth=2, label="Précipitations")
        h2, = ax1.plot(x, self.results["etpc"], color="#E68A00", marker="s", linewidth=2, label="ETP")
        h3, = ax1.plot(x, self.results["etr"], color="#159D75", marker="^", linewidth=2, label="ETR")
        ax1.set_xticks(x)
        ax1.set_xticklabels([m[:3] for m in MOIS])
        ax1.set_ylabel("Lame d'eau (mm)")
        ax1.grid(True, linestyle=":", alpha=0.55)
        ax1b = ax1.twinx()
        h4, = ax1b.plot(x, self.results["tp"], color="#8946C9", marker="D", linewidth=1.8, label="Température")
        ax1b.set_ylabel("Température (°C)")
        fig1.suptitle("Moyenne pluriannuelle des éléments climatiques", y=0.98, fontsize=13, weight="bold")
        fig1.legend([h1, h2, h3, h4], [x.get_label() for x in (h1, h2, h3, h4)],
                    loc="upper center", bbox_to_anchor=(0.5, 0.915), ncol=4, fontsize=9, frameon=False)
        self.bilan_canvas_climat.draw_idle()

        fig2 = self.bilan_canvas_rfu.figure
        fig2.clear()
        ax2 = fig2.add_axes([0.075, 0.12, 0.82, 0.65])
        h1, = ax2.plot(x, self.results["exc"], color="#2563EB", marker="s", linewidth=2, label="Excédent")
        h2, = ax2.plot(x, self.results["def"], color="#E85D3F", marker="^", linewidth=2, label="Déficit")
        ax2.set_xticks(x)
        ax2.set_xticklabels([m[:3] for m in MOIS])
        ax2.set_ylabel("Excédent / Déficit (mm)")
        ax2.grid(True, linestyle=":", alpha=0.55)
        ax2b = ax2.twinx()
        h3, = ax2b.plot(x, self.results["rfu"], color="#159D75", marker="x", linewidth=2, label="RFU")
        ax2b.set_ylabel("RFU (mm)")
        fig2.suptitle("RFU, Excédent et Déficit", y=0.98, fontsize=13, weight="bold")
        fig2.legend([h1, h2, h3], [h.get_label() for h in (h1, h2, h3)],
                    loc="upper center", bbox_to_anchor=(0.5, 0.915), ncol=3, fontsize=9, frameon=False)
        self.bilan_canvas_rfu.draw_idle()

    def export_table(self):
        export_table_xlsx(self, self.bilan_table, "Exporter le bilan", "bilan_tableau.xlsx", "Bilan hydrique")

    def open_output_folder(self, text: str):
        open_existing_output_folder(self, text)

    def save_bilan_report(self):
        export_station_report(self, self.bilan_process)

    def update_config_paths(self):
        paths = self.config.setdefault("last_paths", {})
        paths["bilan_temp"] = self.bilan_temp.text()
        paths["bilan_pr"] = self.bilan_pr.text()
        paths["bilan_out"] = self.bilan_out.text()
        save_config(self.config)


    def run_bilan(self):
        if self.bilan_process.process and self.bilan_process.process.state() != QProcess.NotRunning:
            QMessageBox.warning(self, "Traitement en cours", "Un traitement est déjà en cours.")
            return
        if not validate_processing_paths(
            self, [("Température", self.bilan_temp.text()), ("Précipitation", self.bilan_pr.text())],
            self.bilan_out.text(),
        ):
            return
        temp_path = resolve_user_path(self.bilan_temp.text())
        pr_path = resolve_user_path(self.bilan_pr.text())
        temp = str(temp_path)
        pr = str(pr_path)
        # Argument historique conserve pour ne pas casser la ligne de commande du module.
        # Il ne pointe plus vers un fichier Excel et n'est plus utilise par le calcul.
        template = "PYTHON_NATIVE"
        out = str(resolve_user_path(self.bilan_out.text()))

        # Aucun changement permanent de l'interface : cette question n'apparait
        # que lorsqu'un fichier unique est combine avec un dossier.
        if temp_path.is_file() and pr_path.is_file():
            pairing_mode = "manual"
        elif temp_path.is_file() != pr_path.is_file():
            selected_kind = "température" if temp_path.is_file() else "précipitation"
            folder_kind = "précipitation" if temp_path.is_file() else "température"
            answer = QMessageBox.question(
                self,
                "Synchronisation Température / Précipitation",
                f"Vous avez choisi un fichier de {selected_kind} et un dossier de {folder_kind}.\n\n"
                "Voulez-vous synchroniser par station ?\n"
                "Le programme cherchera le même code ou nom de station dans le nom du fichier "
                "et, s'il n'y est pas, à l'intérieur du fichier Excel.\n\n"
                "Oui : utiliser uniquement le fichier correspondant à la même station.\n"
                "Non : utiliser le fichier sélectionné avec TOUS les fichiers du dossier.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Cancel:
                return
            pairing_mode = "sync" if answer == QMessageBox.Yes else "broadcast"
        else:
            pairing_mode = "sync"
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Dossier sortie", str(exc))
            return
        self.update_config_paths()
        self.last_output_dir = Path(out)
        args = [str((MODULES_DIR / "bilan_hydrique.py").resolve()), temp, pr, template, out,
                "--pairing-mode", pairing_mode]
        args.append("--fast" if self.bilan_no_charts.isChecked() else "--charts")
        self.bilan_process.clear()
        self.bilan_report_btn.setEnabled(False)
        self.bilan_process.run(args, cwd=BASE_DIR, on_finished=self._after_bilan_run)

    def _after_bilan_run(self, code):
        self.bilan_report_btn.setEnabled(
            self.bilan_process.report_ready
        )



class GumbelPage(QWidget):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.block_update = False
        self.rows = [""] * 10
        self.last_output_dir = None
        self.last_result_dir = None

        self._calculate_timer = QTimer(self)
        self._calculate_timer.setSingleShot(True)
        self._calculate_timer.setInterval(300)
        self._calculate_timer.timeout.connect(self.calculate)

        form = load_designer_ui("gumbel_page.ui", self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(form)

        self.tabs = ui_child(form, QTabWidget, "GumbelTabs")
        self.table = self.tabs.widget(0)
        self.curves = self.tabs.widget(1)
        self.files = self.tabs.widget(2)
        self.table.layout().setStretch(2, 1)
        self.curves.layout().setStretch(2, 1)
        self.files.layout().setStretch(5, 1)

        self.mode_label = ui_child(form, QLabel, "ModeLabel")
        self.gradex_label = ui_child(form, QLabel, "GradexLabel")
        self.n_label = ui_child(form, QLabel, "NLabel")
        self.ic_edit = ui_child(form, QLineEdit, "IcEdit")
        self.u_label = ui_child(form, QLabel, "UGaussLabel")
        self.ic_edit.textChanged.connect(self.schedule_calculate)

        columns = [
            "Obs", "Valeurs saisies", "Ordre", "Fréquence", "Variable réduite",
            "Valeur expérimentale", "Valeur théorique", "Borne inf", "Borne sup",
        ]
        self.gumbel_table = ResponsiveTable(
            10, columns,
            [88, 145, 85, 106, 148, 168, 152, 105, 105],
            [110, 215, 105, 130, 195, 220, 205, 150, 150],
        )
        put_in_host(form, "GumbelTableHost", self.gumbel_table)
        self.gumbel_table.itemChanged.connect(self.schedule_calculate)

        self.gumbel_canvas = PlotCanvas(10, 6)
        put_in_host(form, "GumbelCurveHost", self.gumbel_canvas)

        self.gumbel_data = PathPicker("Pluie journalière", "both", saved_user_path(config, "gumbel_data"))
        self.gumbel_out = PathPicker("Dossier sortie", "dir", saved_user_path(config, "gumbel_out"))
        put_in_host(form, "GumbelDataHost", self.gumbel_data)
        put_in_host(form, "GumbelOutHost", self.gumbel_out)

        files_heading = ui_child(form, QLabel, "FilesSectionTitle")
        files_heading.setObjectName("SectionTitle")
        self.gumbel_no_charts = ui_child(form, QPushButton, "GumbelNoCharts")
        self.gumbel_with_charts = ui_child(form, QPushButton, "GumbelWithCharts")
        for button in (self.gumbel_no_charts, self.gumbel_with_charts):
            button.setObjectName("ChoiceToggle")
        self.gumbel_mode_group = QButtonGroup(self)
        self.gumbel_mode_group.setExclusive(True)
        self.gumbel_mode_group.addButton(self.gumbel_no_charts)
        self.gumbel_mode_group.addButton(self.gumbel_with_charts)
        self.gumbel_no_charts.setChecked(True)

        self.gumbel_process = ProcessBox()
        put_in_host(form, "GumbelProcessHost", self.gumbel_process)
        self.gumbel_report_btn = ui_child(form, QPushButton, "GumbelReportButton")

        ui_child(form, QPushButton, "GumbelAddButton").clicked.connect(self.add_line)
        ui_child(form, QPushButton, "GumbelDeleteButton").clicked.connect(self.delete_line)
        ui_child(form, QPushButton, "GumbelResetButton").clicked.connect(self.reset_table)
        ui_child(form, QPushButton, "GumbelExportButton").clicked.connect(self.export_table)
        ui_child(form, QPushButton, "GumbelRunButton").clicked.connect(self.run_gumbel)
        ui_child(form, QPushButton, "GumbelOpenButton").clicked.connect(
            lambda: self.open_output_folder(self.gumbel_out.text())
        )
        self.gumbel_report_btn.clicked.connect(self.save_gumbel_report)

        self.reset_table()
        self.calculate()

    def reset_table(self):
        self.block_update = True
        self.rows = [""] * 10
        self.gumbel_table.setRowCount(len(self.rows))
        for r in range(len(self.rows)):
            for c in range(self.gumbel_table.columnCount()):
                item = QTableWidgetItem("Obs.%d" % (r + 1) if c == 0 else "")
                if c != 1:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.gumbel_table.setItem(r, c, item)
        self.block_update = False
        self.calculate()

    def schedule_calculate(self):
        if self.block_update:
            return
        self._calculate_timer.start()

    def add_line(self):
        self.sync_rows()
        self.rows.append("")
        self.gumbel_table.setRowCount(len(self.rows))
        r = len(self.rows) - 1
        for c in range(self.gumbel_table.columnCount()):
            item = QTableWidgetItem("Obs.%d" % (r + 1) if c == 0 else "")
            if c != 1:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.gumbel_table.setItem(r, c, item)
        self.calculate()

    def delete_line(self):
        if self.gumbel_table.rowCount() <= 1:
            return
        self.gumbel_table.removeRow(self.gumbel_table.rowCount() - 1)
        self.sync_rows()
        self.calculate()

    def sync_rows(self):
        self.rows = []
        for r in range(self.gumbel_table.rowCount()):
            item = self.gumbel_table.item(r, 1)
            self.rows.append(item.text() if item else "")

    def _set(self, r, c, text):
        item = self.gumbel_table.item(r, c)
        if item is None:
            item = QTableWidgetItem()
            self.gumbel_table.setItem(r, c, item)
        if item.text() != text:
            item.setText(text)
        if c != 1:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)

    def calculate(self):
        if self.block_update or not hasattr(self, "gumbel_table"):
            return
        self.block_update = True
        self.sync_rows()
        values = [parse_float(v) for v in self.rows]
        valid = sorted(v for v in values if v is not None)
        n = len(valid)
        ic = parse_float(self.ic_edit.text()) or 90.0
        if not (0 < ic < 100):
            ic = 90.0
        u = NormalDist().inv_cdf(1 - (1 - ic / 100.0) / 2)
        if n >= 2:
            gradex = 0.78 * statistics.stdev(valid)
            mode = statistics.mean(valid) - 0.577 * gradex
        elif n == 1:
            gradex = 0.0
            mode = valid[0]
        else:
            gradex = 0.0
            mode = 0.0
        self.curve_data = {"x": [], "exp": [], "theo": [], "inf": [], "sup": [], "mode": mode, "gradex": gradex}
        for r in range(self.gumbel_table.rowCount()):
            self._set(r, 0, f"Obs.{r+1}")
            if r >= n:
                for c in range(2, 9):
                    self._set(r, c, "")
                continue
            value = valid[r]  # sorted rank, independent of input row order
            order = r + 1
            freq = (order - 0.5) / n if n else None
            y = -math.log(-math.log(freq)) if freq and 0 < freq < 1 else None
            theo = mode + gradex * y if y is not None else None
            inf = sup = None
            if y is not None and n:
                denom = 1 - 1.1 * u * u / n
                rac = (0.714 + 0.108 * y + 0.671 * y * y) / n
                if rac >= 0 and abs(denom) > 1e-12:
                    term_inf = (u * math.sqrt(rac) - u*u/n*(0.859*y+0.074)) / denom * gradex / 0.78 if gradex else 0
                    term_sup = (u * math.sqrt(rac) + u*u/n*(0.859*y+0.074)) / denom * gradex / 0.78 if gradex else 0
                    inf, sup = theo - term_inf, theo + term_sup
            self._set(r, 2, str(order))
            self._set(r, 3, fmt(freq, 4))
            self._set(r, 4, fmt(y, 4))
            self._set(r, 5, fmt(value, 3))
            self._set(r, 6, fmt(theo, 3))
            self._set(r, 7, fmt(inf, 3))
            self._set(r, 8, fmt(sup, 3))
            if y is not None:
                self.curve_data["x"].append(y)
                self.curve_data["exp"].append(value)
                self.curve_data["theo"].append(theo)
                self.curve_data["inf"].append(inf)
                self.curve_data["sup"].append(sup)
        # La saisie et les valeurs calculables répondent dès la première observation.
        # Aucune valeur fictive n'est affichée tant que le tableau est vide.
        self.mode_label.setText("Mode= " + (fmt(mode, 3) if n else ""))
        self.gradex_label.setText("Gradex= " + (fmt(gradex, 3) if n >= 2 else ""))
        self.n_label.setText("Taille n= " + str(n))
        self.u_label.setText("U Gauss= " + (fmt(u, 3) if n else ""))
        self.block_update = False
        self.update_curve()

    def update_curve(self):
        if not hasattr(self, "gumbel_canvas") or not hasattr(self, "curve_data"):
            return
        signature = tuple(tuple(self.curve_data.get(key, ())) for key in ("x", "theo", "inf", "sup", "exp"))
        signature += (self.curve_data.get("mode"), self.curve_data.get("gradex"))
        if signature == getattr(self, "_last_gumbel_plot_signature", None):
            return
        self._last_gumbel_plot_signature = signature
        fig = self.gumbel_canvas.figure
        fig.clear()
        # Toujours dessiner les axes, la légende et les courbes, même si le
        # tableau est vide. Les premières valeurs apparaissent immédiatement.
        ax = fig.add_axes([0.09, 0.13, 0.86, 0.65])
        x = self.curve_data["x"]
        ax.plot(x, self.curve_data["theo"], color="#2563EB", linewidth=2,
                marker="o", markersize=5, label="Valeur théorique")
        ax.plot(x, self.curve_data["inf"], color="#E68A00", linewidth=2,
                linestyle="--", label="Borne inférieure")
        ax.plot(x, self.curve_data["sup"], color="#8946C9", linewidth=2,
                linestyle="--", label="Borne supérieure")
        ax.plot(x, self.curve_data["exp"], color="#159D75", linewidth=2,
                marker="D", markersize=5, label="Valeurs expérimentales")
        fig.legend(*ax.get_legend_handles_labels(), loc="upper center",
                   bbox_to_anchor=(0.5, 0.90), ncol=4, fontsize=9, frameon=False)
        title = "Ajustement Gumbel"
        if x:
            title += (f" (Mode={fmt(self.curve_data.get('mode'), 2)}, "
                      f"Gradex={fmt(self.curve_data.get('gradex'), 2)})")
        fig.suptitle(title, y=0.98, fontsize=13, weight="bold")
        ax.set_xlabel("Variable réduite de Gumbel")
        ax.set_ylabel("Valeurs naturelles")
        ax.grid(True, linestyle=":", alpha=0.55)
        self.gumbel_canvas.draw_idle()

    def export_table(self):
        export_table_xlsx(self, self.gumbel_table, "Exporter Gumbel", "gumbel_tableau.xlsx", "Gumbel")

    def open_output_folder(self, text: str):
        open_existing_output_folder(self, text)

    def save_gumbel_report(self):
        export_station_report(self, self.gumbel_process)

    def update_config_paths(self):
        paths = self.config.setdefault("last_paths", {})
        paths["gumbel_data"] = self.gumbel_data.text()
        paths["gumbel_out"] = self.gumbel_out.text()
        save_config(self.config)


    def run_gumbel(self):
        if self.gumbel_process.process and self.gumbel_process.process.state() != QProcess.NotRunning:
            QMessageBox.warning(self, "Traitement en cours", "Un traitement est déjà en cours.")
            return
        if not validate_processing_paths(
            self, [("Pluie journalière", self.gumbel_data.text())],
            self.gumbel_out.text(),
        ):
            return
        data = str(resolve_user_path(self.gumbel_data.text()))
        out = str(resolve_user_path(self.gumbel_out.text()))
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Dossier sortie", str(exc))
            return
        self.update_config_paths()
        self.last_output_dir = Path(out)
        args = [str((MODULES_DIR / "Gumbel.py").resolve()), "--data", data, "--outdir", out]
        # The user's chart option only controls chart objects. It must not
        # change the number of worksheets or the calculation method.
        args += ["--no-split", "--no-charts" if self.gumbel_no_charts.isChecked() else "--charts"]
        self.gumbel_process.clear()
        self.gumbel_report_btn.setEnabled(False)
        self.gumbel_process.run(args, cwd=BASE_DIR, on_finished=self._after_gumbel_run)

    def _after_gumbel_run(self, code):
        self.gumbel_report_btn.setEnabled(
            self.gumbel_process.report_ready
        )





class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self._native_caption_integrated = supports_integrated_native_titlebar()
        self._native_caption_margins_connected = False
        if self._native_caption_integrated:
            # Extend ONLY the client area under the native Windows title bar.
            # No FramelessWindowHint, custom caption buttons or nativeEvent hacks.
            self.setWindowFlags(
                self.windowFlags()
                | Qt.WindowType.WindowTitleHint
                | Qt.WindowType.WindowSystemMenuHint
                | Qt.WindowType.WindowMinMaxButtonsHint
                | Qt.WindowType.WindowCloseButtonHint
                | Qt.WindowType.ExpandedClientAreaHint
                | Qt.WindowType.NoTitleBarBackgroundHint
            )
            # Qt Widgets normally inset the top-level layout by the safe-area
            # height. Disable that inset so our 44px strip truly fills the
            # system caption region; all interactive content stays below it.
            self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        # An opaque Qt background that matches the native DWM caption
        # prevents a temporary system-accent gap on window state changes.
        window_palette = self.palette()
        window_palette.setColor(QPalette.ColorRole.Window, QColor(APP_BG_COLOR))
        self.setPalette(window_palette)
        self.setAutoFillBackground(True)
        self.setWindowTitle("HydroStation")
        if APP_ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.resize(1400, 850)
        self.setMinimumSize(960, 620)
        central = QWidget()
        central.setPalette(window_palette)
        central.setAutoFillBackground(True)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.native_caption_strip = None
        if self._native_caption_integrated:
            self.native_caption_strip = NativeCaptionBlendBar(central)
            root.addWidget(self.native_caption_strip)

        # The visible shell is stored as a Qt Designer file. Processing logic
        # stays in Python; changing the .ui file changes layout only.
        body = load_designer_ui("main_window.ui", central)
        body.setPalette(window_palette)
        body.setAutoFillBackground(True)
        root.addWidget(body, 1)

        self.nav = body.findChild(QListWidget, "SideNavigation")
        self.stack = body.findChild(QStackedWidget, "PageStack")
        logo = body.findChild(QLabel, "BrandLogo")
        if self.nav is None or self.stack is None or logo is None:
            raise RuntimeError("main_window.ui est incomplet")

        for index in range(self.nav.count()):
            self.nav.item(index).setSizeHint(QSize(205, 52))
        if APP_LOGO_PATH.is_file():
            pixmap = QPixmap(str(APP_LOGO_PATH))
            logo.setPixmap(pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.bilan = BilanPage(self.config)
        self.gumbel = GumbelPage(self.config)
        self.stack.addWidget(self.bilan)
        self.stack.addWidget(self.gumbel)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

    def _sync_native_caption_area(self, margins=None):
        """Keep the ordinary app controls clear of scaled Windows chrome."""
        if self.native_caption_strip is None:
            return
        handle = self.windowHandle()
        if margins is None and handle is not None:
            margins = handle.safeAreaMargins()
        top = max(0, margins.top()) if margins is not None else 0
        self.native_caption_strip.setFixedHeight(max(44, top + 4))

    def showEvent(self, event):
        super().showEvent(event)
        # Conserver les vrais boutons Windows, leurs animations et Snap Layouts.
        if self._native_caption_integrated:
            self._sync_native_caption_area()
            handle = self.windowHandle()
            if handle is not None and not self._native_caption_margins_connected:
                handle.safeAreaMarginsChanged.connect(self._sync_native_caption_area)
                self._native_caption_margins_connected = True
        match_native_windows_titlebar(self)

    def changeEvent(self, event):
        super().changeEvent(event)
        # Windows can refresh its non-client colors on maximize/restore and
        # window activation. Reapply only for those events, not every resize.
        if (self._native_caption_integrated
                and event.type() in (QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange)):
            match_native_windows_titlebar(self)

    def closeEvent(self, event):
        active = [box for box in (self.bilan.bilan_process, self.gumbel.gumbel_process)
                  if box.process is not None and box.process.state() != QProcess.NotRunning]
        if active:
            for box in active:
                box.cancel_safely()
            QMessageBox.information(self, "Traitement en cours",
                "Annulation demandée. Le traitement courant termine son fichier et écrit son rapport avant fermeture.")
            event.ignore()
        else:
            super().closeEvent(event)

def apply_style(app: QApplication):
    # La police est définie une seule fois pour les composants Qt et Matplotlib.
    app.setFont(QFont(APP_FONT_FAMILY, APP_FONT_SIZE_PT))
    rcParams["font.family"] = [APP_FONT_FAMILY, "DejaVu Sans"]
    rcParams["font.sans-serif"] = [APP_FONT_FAMILY, "DejaVu Sans"]
    arrow_path = (BASE_DIR / "assets" / "combo_arrow.svg").as_posix()
    style = """
        QMainWindow, QWidget { background: #F5F7FB; color: #1F2937; font-family: "__APP_FONT__"; font-size: 10pt; }
        QFrame#NativeCaptionBlendBar { background: #F5F7FB; border: none; }
        QLabel { background: transparent; }
        /* Police unique pour les textes, tableaux, onglets et boutons. */
        QLabel#ProcessingFieldLabel { font-size: 10pt; font-weight: 700; color: #1F2937; }
        QTabWidget { background: transparent; }
        QTabBar { background: transparent; }
        QFrame#SidebarPanel { background: #E8EDF3; border: 1px solid #DFE7F0; border-radius: 18px; }
        QLabel#BrandTitle { font-size: 11pt; color: #17385B; font-weight: 700; }
        QListWidget#SideNavigation {
            background: transparent;
            color: #344256;
            border: none;
            border-radius: 14px;
            padding: 0;
            font-size: 11pt;
            font-weight: 700;
            outline: none;
        }
        QListWidget#SideNavigation::item {
            padding: 12px;
            border: none;
            border-radius: 12px;
            margin: 4px 1px;
            color: #344256;
            outline: none;
            font-weight: 700;
        }
        QListWidget#SideNavigation::item:hover { background: #D6E2EF; }
        QListWidget#SideNavigation::item:selected {
            background: #2563EB;
            color: white;
            border: none;
            outline: none;
        }
        QListWidget#SideNavigation::item:focus { outline: none; border: none; }
        QTabWidget::pane { border: none; border-radius: 0; background: white; }
        QTabBar::tab {
            padding: 11px 18px;
            background: #E5E7EB;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            margin-right: 4px;
            min-height: 22px;
            font-size: 11pt;
            font-weight: 700;
            color: #334155;
        }
        QTabBar::tab:selected { background: white; color: #1D4ED8; font-weight: 700; }
        QTableWidget {
            background: white;
            alternate-background-color: #F8FAFC;
            border: none;
            gridline-color: #E5E7EB;
            selection-background-color: #2563EB;
            selection-color: white;
            outline: none;
        }
        QTableWidget#DataTable {
            border-left: 1px solid #CBD5E1;
            border-right: 1px solid #CBD5E1;
            border-top: 0;
            border-bottom: 0;
        }
        QTableWidget::item:selected { background: #2563EB; color: white; }
        QTableWidget QLineEdit#TableCellEditor {
            background: #2563EB;
            color: white;
            border: none;
            border-radius: 0;
            padding: 0 6px;
            margin: 0;
            min-height: 0;
        }
        QScrollBar:horizontal { background: #EEF2F8; height: 12px; border: none; border-radius: 6px; margin: 0; }
        QScrollBar::handle:horizontal { background: #94A3B8; min-width: 44px; border-radius: 6px; }
        QScrollBar::handle:horizontal:hover { background: #2563EB; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
            border: none;
            background: none;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
        QScrollBar:vertical { background: #EEF2F8; width: 12px; border: none; border-radius: 6px; }
        QScrollBar::handle:vertical { background: #94A3B8; min-height: 36px; border-radius: 6px; }
        QScrollBar::handle:vertical:hover { background: #2563EB; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; background: none; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        QHeaderView::section {
            background: #DBEAFE;
            color: #111827;
            padding: 7px;
            border: 1px solid #BFDBFE;
            font-weight: 700;
        }
        QLineEdit, QComboBox {
            background: white;
            border: 1px solid #CBD5E1;
            border-radius: 7px;
            padding: 6px 10px;
            min-height: 20px;
        }
        QLineEdit:focus { border: 1px solid #2563EB; }
        QPushButton#ChoiceToggle {
            background: #EEF2F7;
            color: #334155;
            border: 1px solid #CBD5E1;
            border-radius: 8px;
            padding: 7px 14px;
            font-weight: 700;
        }
        QPushButton#ChoiceToggle:hover { background: #E2E8F0; border-color: #94A3B8; }
        QPushButton#ChoiceToggle:checked { background: #DBEAFE; color: #1D4ED8; border: 1px solid #2563EB; }
        QComboBox { padding-right: 34px; }
        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 30px;
            border-left: 1px solid #CBD5E1;
            border-top-right-radius: 8px;
            border-bottom-right-radius: 8px;
            background: #F1F5F9;
        }
        QComboBox::down-arrow { image: url(__COMBO_ARROW__); width: 12px; height: 8px; }
        QComboBox QAbstractItemView {
            background: white;
            border: 1px solid #CBD5E1;
            selection-background-color: #DBEAFE;
            padding: 4px;
        }
        QPushButton { border: none; border-radius: 9px; padding: 8px 14px; font-size: 10pt; font-weight: 700; }
        QPushButton[kind="primary"] { background: #2563EB; color: white; }
        QPushButton[kind="primary"]:hover { background: #1D4ED8; }
        QPushButton[kind="secondary"] { background: #E2E8F0; color: #0F172A; }
        QPushButton[kind="secondary"]:hover { background: #CBD5E1; }
        QPushButton:checked { background: #2563EB; color: white; }
        /* Titres principaux des sous-menus: Tableau, Courbe et Traitement fichiers. */
        /* « Courbe calculée depuis le tableau » est un titre principal, pas une annotation. */
        QLabel#SectionTitle, QLabel#CurveCaption {
            color: #0F172A;
            font-size: 15pt;
            font-weight: 700;
            padding: 0;
            margin: 0;
            min-height: 36px;
        }
        QLabel#HelpText { color: #64748B; font-size: 9pt; padding-top: 2px; }
        QGroupBox {
            border: 1px solid #D8DEE9;
            border-radius: 10px;
            margin-top: 10px;
            padding-top: 10px;
            font-weight: 700;
            background: #F8FAFC;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 5px;
            color: #0F172A;
            font-size: 11pt;
        }
        QCheckBox { padding: 4px; }
        QPushButton:disabled { background: #E9EDF4; color: #94A3B8; font-weight: 700; }
        QProgressBar {
            background: #E3E9F1;
            border: 1px solid #CBD5E1;
            border-radius: 8px;
            min-height: 24px;
            text-align: center;
            font-weight: 700;
            color: #1E293B;
        }
        QProgressBar::chunk { background: #2563EB; border-radius: 7px; }
        QLabel#ProgressStatus { color: #475569; font-weight: 600; padding: 4px 0; }
    """
    app.setStyleSheet(style.replace("__COMBO_ARROW__", arrow_path).replace("__APP_FONT__", APP_FONT_FAMILY))


def main():
    app = QApplication(sys.argv)
    if APP_ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HydroStation")
        except (AttributeError, OSError):
            pass
    apply_style(app)
    win = MainWindow()
    # Win32 HWND must be configured before the first on-screen frame to avoid
    # an initial purple system-accent titlebar/border flash.
    match_native_windows_titlebar(win)
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
