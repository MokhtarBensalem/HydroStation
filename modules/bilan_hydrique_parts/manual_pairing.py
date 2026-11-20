# -*- coding: utf-8 -*-
"""Manual temperature/precipitation pairing dialog with draggable node ports."""

from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QFile, QIODevice, QTimer, QEvent
from PySide6.QtGui import QColor, QBrush, QPen, QPainter, QPainterPath, QFont, QFontMetricsF
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QScrollBar,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


FILE_FILTER = (
    "Fichiers de données (*.xlsx *.xls *.xlsm *.xlsb *.ods *.csv *.txt *.tsv);;"
    "Tous les fichiers (*.*)"
)


class PairingPortItem(QGraphicsEllipseItem):
    """A draggable input/output socket located on the inner edge of a node."""

    def __init__(self, node):
        super().__init__(-8, -8, 16, 16, node)
        self.node = node
        self.setZValue(5)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setBrush(QBrush(QColor("#F59E0B" if node.kind == "temperature" else "#3B82F6")))
        self.setPen(QPen(QColor("#E2E8F0"), 2))

    def hoverEnterEvent(self, event):
        self.setRect(-10, -10, 20, 20)
        self.setPen(QPen(QColor("#FFFFFF"), 2))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setRect(-8, -8, 16, 16)
        self.setPen(QPen(QColor("#E2E8F0"), 2))
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            scene = self.scene()
            if isinstance(scene, PairingScene):
                scene.begin_connection(self)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        scene = self.scene()
        if isinstance(scene, PairingScene):
            scene.update_temporary_connection(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        scene = self.scene()
        if isinstance(scene, PairingScene):
            scene.finish_connection(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PairingNodeItem(QGraphicsObject):
    WIDTH = 310
    HEIGHT = 82

    def __init__(self, path, kind, row):
        super().__init__()
        self.path = Path(path)
        self.kind = kind
        self.row = row
        self.base_y = 0.0
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setToolTip(str(self.path))
        self.port = PairingPortItem(self)
        # The socket always faces the central connection area.
        self.port.setPos(
            self.WIDTH if kind == "temperature" else 0,
            self.HEIGHT / 2,
        )

    def boundingRect(self):
        return QRectF(0, 0, self.WIDTH, self.HEIGHT)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.kind == "temperature":
            accent = QColor("#F59E0B")
            title = "TEMPÉRATURE"
            text_left = 18
        else:
            accent = QColor("#3B82F6")
            title = "PRÉCIPITATION"
            text_left = 30

        # Both file cards share the same dark-blue body. The type color is
        # reserved for the connection socket and the narrow side marker.
        border = QColor("#334155")
        card_fill = QColor("#172B49")

        rect = self.boundingRect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(QBrush(card_fill))
        painter.drawRoundedRect(rect, 12, 12)

        accent_rect = QRectF(
            0 if self.kind == "temperature" else self.WIDTH - 4,
            12, 4, self.HEIGHT - 24
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawRoundedRect(accent_rect, 2, 2)

        title_font = QFont("Segoe UI", 8)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#A8B8CC"))
        painter.drawText(
            QRectF(text_left, 12, self.WIDTH - text_left - 22, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )

        filename = self.path.name
        file_font = QFont("Segoe UI", 9)
        file_font.setBold(True)
        painter.setFont(file_font)
        painter.setPen(QColor("#F8FAFC"))
        available = self.WIDTH - text_left - 26
        metrics = QFontMetricsF(file_font)
        shown = metrics.elidedText(filename, Qt.TextElideMode.ElideMiddle, int(available))
        painter.drawText(
            QRectF(text_left, 34, available, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            shown,
        )

        detail_font = QFont("Segoe UI", 7)
        painter.setFont(detail_font)
        painter.setPen(QColor("#8193A9"))
        detail = str(self.path.parent)
        detail = QFontMetricsF(detail_font).elidedText(
            detail, Qt.TextElideMode.ElideMiddle, int(available)
        )
        painter.drawText(
            QRectF(text_left, 56, available, 15),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            detail,
        )

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            scene = self.scene()
            if isinstance(scene, PairingScene):
                scene.update_connections()
        return result


class PairingWireItem(QGraphicsPathItem):
    def __init__(self, scene_owner):
        super().__init__()
        self.scene_owner = scene_owner
        self.setZValue(-1)
        self.setPen(QPen(QColor("#64748B"), 2.6, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        self.setAcceptedMouseButtons(Qt.MouseButton.RightButton)
        self.setToolTip("Clic droit pour supprimer cette liaison")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.scene_owner.remove_connection(self)
            event.accept()
            return
        super().mousePressEvent(event)


class PairingScene(QGraphicsScene):
    connectionChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nodes = {"temperature": [], "precipitation": []}
        self.connections = []
        self.drag_start = None
        self.temporary_wire = None
        self.column_offsets = {"temperature": 0, "precipitation": 0}
        self.viewport_height = 500
        self._applying_scroll = False
        self.setBackgroundBrush(QBrush(QColor("#F5F7FB")))

    def drawBackground(self, painter, rect):
        # Remove the large blue workspace background; the dark-blue cards
        # remain the only filled elements in the two file columns.
        painter.fillRect(rect, QColor("#F5F7FB"))

    def add_file_node(self, path, kind):
        row = len(self.nodes[kind])
        node = PairingNodeItem(path, kind, row)
        x = 40 if kind == "temperature" else 730
        node.base_y = 28 + row * 108
        node.setPos(x, node.base_y)
        self.addItem(node)
        self.nodes[kind].append(node)
        self._refresh_scene_rect()
        return node

    def _refresh_scene_rect(self):
        self.setSceneRect(0, 0, 1080, max(1, self.viewport_height))

    def set_viewport_height(self, height):
        self.viewport_height = max(1, int(height))
        self._refresh_scene_rect()

    def set_column_scroll(self, kind, value):
        self.column_offsets[kind] = max(0, int(value))
        offset = self.column_offsets[kind]
        self._applying_scroll = True
        try:
            for node in self.nodes[kind]:
                node.setY(node.base_y - offset)
        finally:
            self._applying_scroll = False
        self.update_connections()

    @staticmethod
    def _connection_path(start, end):
        path = QPainterPath(start)
        direction = 1 if end.x() >= start.x() else -1
        handle = max(70.0, abs(end.x() - start.x()) * 0.42)
        path.cubicTo(
            QPointF(start.x() + direction * handle, start.y()),
            QPointF(end.x() - direction * handle, end.y()),
            end,
        )
        return path

    def begin_connection(self, port):
        if self.temporary_wire is not None:
            self.removeItem(self.temporary_wire)
        self.drag_start = port
        self.temporary_wire = QGraphicsPathItem()
        self.temporary_wire.setZValue(-0.5)
        self.temporary_wire.setPen(
            QPen(QColor("#94A3B8"), 2.4, Qt.PenStyle.DashLine,
                 Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        )
        self.addItem(self.temporary_wire)
        self.update_temporary_connection(port.mapToScene(QPointF(0, 0)))

    def update_temporary_connection(self, cursor_pos):
        if self.drag_start is None or self.temporary_wire is None:
            return
        start = self.drag_start.mapToScene(QPointF(0, 0))
        self.temporary_wire.setPath(self._connection_path(start, cursor_pos))

    def finish_connection(self, scene_pos):
        if self.drag_start is None:
            return

        start_port = self.drag_start
        target_port = None
        for item in self.items(scene_pos):
            if isinstance(item, PairingPortItem):
                target_port = item
                break

        if self.temporary_wire is not None:
            self.removeItem(self.temporary_wire)
            self.temporary_wire = None
        self.drag_start = None

        if target_port is None or target_port is start_port:
            return
        if target_port.node.kind == start_port.node.kind:
            return

        if start_port.node.kind == "temperature":
            temperature_node, precipitation_node = start_port.node, target_port.node
        else:
            temperature_node, precipitation_node = target_port.node, start_port.node

        if any(
            t.path == temperature_node.path and p.path == precipitation_node.path
            for t, p, _ in self.connections
        ):
            return

        # Many-to-many: either file may connect to several files on the opposite side.
        wire = PairingWireItem(self)
        self.addItem(wire)
        self.connections.append((temperature_node, precipitation_node, wire))
        self.update_connections()
        self.connectionChanged.emit(len(self.connections))

    def update_connections(self):
        for temperature_node, precipitation_node, wire in self.connections:
            start = temperature_node.port.mapToScene(QPointF(0, 0))
            end = precipitation_node.port.mapToScene(QPointF(0, 0))
            wire.setPath(self._connection_path(start, end))
        if self.drag_start is not None and self.temporary_wire is not None:
            start = self.drag_start.mapToScene(QPointF(0, 0))
            self.temporary_wire.setPath(self._connection_path(start, start))

    def remove_connection(self, wire):
        for connection in list(self.connections):
            if connection[2] is wire:
                self.connections.remove(connection)
                self.removeItem(wire)
                self.connectionChanged.emit(len(self.connections))
                break

    def clear_connections(self):
        for _, _, wire in list(self.connections):
            self.removeItem(wire)
        self.connections.clear()
        self.connectionChanged.emit(0)

    def get_pairs(self):
        return [
            (temperature_node.path, precipitation_node.path)
            for temperature_node, precipitation_node, _ in self.connections
        ]


def _load_manual_pairing_form(parent):
    """Load the manual pairing layout authored in Qt Designer."""
    ui_path = Path(__file__).resolve().parents[2] / "ui" / "manual_pairing_dialog.ui"
    ui_file = QFile(str(ui_path))
    if not ui_file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError(f"Impossible d'ouvrir l'interface Qt Designer : {ui_path}")
    try:
        form = QUiLoader().load(ui_file, parent)
    finally:
        ui_file.close()
    if form is None:
        raise RuntimeError(f"Impossible de charger l'interface Qt Designer : {ui_path}")
    return form


class ManualPairingDialog(QDialog):
    """Qt Designer form with two independently scrollable file columns."""

    def __init__(self, temperature_files, precipitation_files, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Association manuelle — Température / Précipitation")
        self.setMinimumSize(980, 520)

        # Fit the dialog inside the usable desktop area (excluding the Windows
        # taskbar), so the Cancel/Confirm buttons remain visible at high DPI.
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(1240, max(980, available.width() - 32))
            height = min(700, max(520, available.height() - 32))
            self.resize(width, height)
            self.move(
                available.x() + (available.width() - width) // 2,
                available.y() + (available.height() - height) // 2,
            )
        else:
            self.resize(1180, 640)
        self._seen = {"temperature": set(), "precipitation": set()}
        self._touch_scroll_kind = None
        self._touch_last_viewport_y = 0.0
        self._touch_connection_port = None
        self._touch_last_scene_pos = QPointF()

        self.form = _load_manual_pairing_form(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.form)

        def child(widget_type, name):
            widget = self.form.findChild(widget_type, name)
            if widget is None:
                raise RuntimeError(f"Widget Qt Designer manquant : {name}")
            return widget

        self.add_temp_button = child(QPushButton, "AddTemperatureButton")
        self.add_rain_button = child(QPushButton, "AddPrecipitationButton")
        self.clear_button = child(QPushButton, "ClearConnectionsButton")
        self.cancel_button = child(QPushButton, "CancelButton")
        self.confirm_button = child(QPushButton, "ConfirmButton")
        self.status = child(QLabel, "PairingStatus")
        self.view = child(QGraphicsView, "PairingView")
        self.temperature_scrollbar = child(QScrollBar, "TemperatureScrollBar")
        self.precipitation_scrollbar = child(QScrollBar, "PrecipitationScrollBar")

        self.scene = PairingScene(self)
        self.view.setScene(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.view.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.view.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate
        )
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        # Enable native touch events on Windows touch screens. Touch gestures
        # scroll the panel; only the small connection sockets initiate links.
        self.view.viewport().setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.view.viewport().installEventFilter(self)

        self.temperature_scrollbar.valueChanged.connect(
            lambda value: self.scene.set_column_scroll("temperature", value)
        )
        self.precipitation_scrollbar.valueChanged.connect(
            lambda value: self.scene.set_column_scroll("precipitation", value)
        )

        self.add_temp_button.clicked.connect(
            lambda: self._add_from_dialog("temperature")
        )
        self.add_rain_button.clicked.connect(
            lambda: self._add_from_dialog("precipitation")
        )
        self.clear_button.clicked.connect(self.scene.clear_connections)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self._confirm)

        self._add_initial_files(temperature_files, "temperature")
        self._add_initial_files(precipitation_files, "precipitation")
        self.scene.connectionChanged.connect(self._update_status)
        self._update_status()
        self._update_scrollbars()
        QTimer.singleShot(0, self._update_scrollbars)

    def _add_initial_files(self, files, kind):
        for path in files:
            self._add_file(path, kind)

    def _add_file(self, path, kind):
        path = Path(path)
        try:
            key = str(path.resolve()).casefold()
        except Exception:
            key = str(path).casefold()
        if key in self._seen[kind] or not path.is_file():
            return
        self._seen[kind].add(key)
        self.scene.add_file_node(path, kind)
        self._update_scrollbars()

    def _update_scrollbars(self):
        viewport_height = max(1, self.view.viewport().height())
        self.scene.set_viewport_height(viewport_height)
        start_y = 28
        row_gap = 108
        bottom_margin = 22

        for kind, scrollbar in (
            ("temperature", self.temperature_scrollbar),
            ("precipitation", self.precipitation_scrollbar),
        ):
            nodes = self.scene.nodes[kind]
            if nodes:
                lowest_card_bottom = max(
                    node.base_y + PairingNodeItem.HEIGHT for node in nodes
                )
                content_height = max(
                    viewport_height,
                    lowest_card_bottom + bottom_margin,
                )
            else:
                content_height = viewport_height
            maximum = max(0, content_height - viewport_height)
            current_value = min(scrollbar.value(), maximum)
            scrollbar.blockSignals(True)
            scrollbar.setRange(0, maximum)
            scrollbar.setPageStep(viewport_height)
            scrollbar.setSingleStep(64)
            scrollbar.setValue(current_value)
            scrollbar.blockSignals(False)
            self.scene.set_column_scroll(kind, current_value)

    def _scrollbar_for_kind(self, kind):
        return (
            self.temperature_scrollbar
            if kind == "temperature"
            else self.precipitation_scrollbar
        )

    def _kind_at_viewport_x(self, x):
        # The view is split into two independent touch/scroll zones.
        return (
            "temperature"
            if x < self.view.viewport().width() / 2
            else "precipitation"
        )

    def _apply_panel_scroll_delta(self, kind, delta):
        scrollbar = self._scrollbar_for_kind(kind)
        target = scrollbar.value() - int(round(delta))
        scrollbar.setValue(max(scrollbar.minimum(), min(scrollbar.maximum(), target)))

    def eventFilter(self, watched, event):
        if watched is self.view.viewport():
            event_type = event.type()

            # Mouse wheel and precision touchpad scrolling: only the panel
            # underneath the pointer moves. The opposite panel remains still.
            if event_type == QEvent.Type.Wheel:
                position = event.position()
                kind = self._kind_at_viewport_x(position.x())
                pixel_delta = event.pixelDelta().y()
                if pixel_delta:
                    delta = pixel_delta
                else:
                    angle_delta = event.angleDelta().y()
                    if not angle_delta:
                        return False
                    delta = (angle_delta / 120.0) * self._scrollbar_for_kind(kind).singleStep()
                self._apply_panel_scroll_delta(kind, delta)
                event.accept()
                return True

            # Touch on a connection socket starts a link. Touch anywhere else
            # scrolls only the side where the finger first touched.
            if event_type == QEvent.Type.TouchBegin:
                points = event.points()
                if not points:
                    return False

                viewport_pos = points[0].position().toPoint()
                scene_pos = self.view.mapToScene(viewport_pos)
                hit_items = self.scene.items(scene_pos)
                port = next(
                    (item for item in hit_items if isinstance(item, PairingPortItem)),
                    None,
                )

                if port is not None:
                    self._touch_connection_port = port
                    self._touch_scroll_kind = None
                    self._touch_last_scene_pos = scene_pos
                    self.scene.begin_connection(port)
                else:
                    self._touch_connection_port = None
                    self._touch_scroll_kind = self._kind_at_viewport_x(viewport_pos.x())
                    self._touch_last_viewport_y = float(viewport_pos.y())

                event.accept()
                return True

            elif event_type == QEvent.Type.TouchUpdate:
                points = event.points()
                if points:
                    viewport_pos = points[0].position().toPoint()
                    scene_pos = self.view.mapToScene(viewport_pos)

                    if self._touch_connection_port is not None:
                        self._touch_last_scene_pos = scene_pos
                        self.scene.update_temporary_connection(scene_pos)
                    elif self._touch_scroll_kind is not None:
                        current_y = float(viewport_pos.y())
                        finger_delta = current_y - self._touch_last_viewport_y
                        # Finger moving upward reveals lower cards, so the
                        # vertical scroll offset must increase with negative dy.
                        self._apply_panel_scroll_delta(
                            self._touch_scroll_kind,
                            finger_delta,
                        )
                        self._touch_last_viewport_y = current_y

                event.accept()
                return True

            elif event_type == QEvent.Type.TouchEnd:
                if self._touch_connection_port is not None:
                    self.scene.finish_connection(self._touch_last_scene_pos)
                self._touch_connection_port = None
                self._touch_scroll_kind = None
                event.accept()
                return True

            elif event_type == QEvent.Type.TouchCancel:
                if self.scene.temporary_wire is not None:
                    self.scene.removeItem(self.scene.temporary_wire)
                    self.scene.temporary_wire = None
                self.scene.drag_start = None
                self._touch_connection_port = None
                self._touch_scroll_kind = None
                event.accept()
                return True

        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "view"):
            QTimer.singleShot(0, self._update_scrollbars)

    def _add_from_dialog(self, kind):
        label = (
            "Ajouter des fichiers de température"
            if kind == "temperature"
            else "Ajouter des fichiers de précipitation"
        )
        paths, _ = QFileDialog.getOpenFileNames(self, label, "", FILE_FILTER)
        for path in paths:
            self._add_file(path, kind)

    def _update_status(self, count=None):
        count = len(self.scene.connections) if count is None else count
        self.status.setText(
            f"{count} liaison{'s' if count != 1 else ''} créée{'s' if count != 1 else ''}."
        )

    def _confirm(self):
        pairs = self.scene.get_pairs()
        if not pairs:
            QMessageBox.warning(
                self,
                "Aucune liaison",
                "Reliez au moins un fichier de température à un fichier de précipitation.",
            )
            return
        self.accept()

    def get_pairs(self):
        return self.scene.get_pairs()

