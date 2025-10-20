#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Диалог предпросмотра видео и настройки зон блюра без зависимости от QtMultimedia."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

try:  # pragma: no cover - OpenCV не всегда установлен в тестовой среде
    import cv2
except Exception:  # noqa: BLE001 - в бою важно отловить любую ошибку
    cv2 = None  # type: ignore[assignment]


VIDEO_PREVIEW_AVAILABLE = cv2 is not None
VIDEO_PREVIEW_TIP = (
    "Установи библиотеку opencv-python (pip install opencv-python) — она включает ffmpeg-бэкенд, "
    "который нужен для предпросмотра видео."
)


class BlurPreviewDialog(QtWidgets.QDialog):
    """Показывает видео и позволяет редактировать координаты delogo-зон."""

    def __init__(self, parent, preset_name: str, zones: List[Dict[str, int]], source_dirs: List[Path]):
        super().__init__(parent)
        self.setWindowTitle(f"Предпросмотр блюра — {preset_name}")
        self.resize(960, 720)

        self._zones: List[Dict[str, int]] = [dict(z) for z in zones] if zones else []
        self._overlay_items: List[QtWidgets.QGraphicsRectItem] = []
        self._video_sources: List[Path] = []
        self._source_dirs: List[Path] = [Path(d) for d in source_dirs if d]
        self._video_enabled = VIDEO_PREVIEW_AVAILABLE

        self._capture: Optional["cv2.VideoCapture"] = None
        self._current_frame = 0
        self._frame_count = 0
        self._fps = 25.0
        self._frame_size = QtCore.QSizeF()

        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance_frame)

        layout = QtWidgets.QVBoxLayout(self)

        if self._video_enabled:
            self._scene = QtWidgets.QGraphicsScene(self)
            self._pixmap_item = QtWidgets.QGraphicsPixmapItem()
            self._scene.addItem(self._pixmap_item)

            picker_layout = QtWidgets.QHBoxLayout()
            picker_layout.addWidget(QtWidgets.QLabel("Видео:"))
            self.cmb_video = QtWidgets.QComboBox()
            self.cmb_video.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
            self._video_sources = self._collect_videos(self._source_dirs)
            for path in self._video_sources:
                self.cmb_video.addItem(path.name, str(path))
            picker_layout.addWidget(self.cmb_video, 1)
            self.btn_browse_video = QtWidgets.QPushButton("Выбрать файл…")
            picker_layout.addWidget(self.btn_browse_video)
            self.btn_reload_list = QtWidgets.QPushButton("Обновить")
            picker_layout.addWidget(self.btn_reload_list)
            layout.addLayout(picker_layout)

            view_container = QtWidgets.QFrame()
            view_container.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
            view_layout = QtWidgets.QVBoxLayout(view_container)
            view_layout.setContentsMargins(0, 0, 0, 0)
            self.view = QtWidgets.QGraphicsView(self._scene)
            self.view.setRenderHints(
                QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.SmoothPixmapTransform
            )
            self.view.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            view_layout.addWidget(self.view)
            layout.addWidget(view_container, 1)

            controls = QtWidgets.QHBoxLayout()
            self.btn_play = QtWidgets.QPushButton("▶")
            self.btn_pause = QtWidgets.QPushButton("⏸")
            self.btn_pause.setEnabled(False)
            self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            self.slider.setRange(0, 0)
            self.slider.setEnabled(False)
            self.lbl_time = QtWidgets.QLabel("00:00 / 00:00")
            controls.addWidget(self.btn_play)
            controls.addWidget(self.btn_pause)
            controls.addWidget(self.slider, 1)
            controls.addWidget(self.lbl_time)
            layout.addLayout(controls)
        else:
            self._scene = None
            self._pixmap_item = None
            info_box = QtWidgets.QGroupBox("Предпросмотр недоступен")
            info_layout = QtWidgets.QVBoxLayout(info_box)
            label = QtWidgets.QLabel(
                "Не удалось загрузить библиотеку OpenCV. Предпросмотр отключён, "
                "но координаты можно отредактировать вручную.\n\n"
                f"{VIDEO_PREVIEW_TIP}"
            )
            label.setWordWrap(True)
            label.setStyleSheet("color:#cbd5f5")
            info_layout.addWidget(label)
            layout.addWidget(info_box)

        zones_box = QtWidgets.QGroupBox("Зоны delogo")
        zones_layout = QtWidgets.QVBoxLayout(zones_box)
        self.tbl_zones = QtWidgets.QTableWidget(0, 4)
        self.tbl_zones.setHorizontalHeaderLabels(["x", "y", "w", "h"])
        header = self.tbl_zones.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        zones_layout.addWidget(self.tbl_zones, 1)
        zone_btns = QtWidgets.QHBoxLayout()
        self.btn_zone_add = QtWidgets.QPushButton("Добавить зону")
        self.btn_zone_remove = QtWidgets.QPushButton("Удалить выделенную")
        zone_btns.addWidget(self.btn_zone_add)
        zone_btns.addWidget(self.btn_zone_remove)
        zone_btns.addStretch(1)
        zones_layout.addLayout(zone_btns)
        layout.addWidget(zones_box)

        footer = QtWidgets.QHBoxLayout()
        hint = (
            "Выбери видео и настрой координаты. Кнопка ОК сохранит изменения."
            if self._video_enabled
            else "Видеопросмотр отключён, но координаты из таблицы сохранятся после нажатия ОК."
        )
        self.lbl_hint = QtWidgets.QLabel(hint)
        self.lbl_hint.setStyleSheet("color:#94a3b8")
        footer.addWidget(self.lbl_hint, 1)
        self.btn_ok = QtWidgets.QPushButton("Сохранить")
        self.btn_cancel = QtWidgets.QPushButton("Отмена")
        footer.addWidget(self.btn_ok)
        footer.addWidget(self.btn_cancel)
        layout.addLayout(footer)

        self.btn_zone_add.clicked.connect(self._add_zone)
        self.btn_zone_remove.clicked.connect(self._remove_zone)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        self.tbl_zones.itemChanged.connect(self._on_zone_item_changed)

        if self._video_enabled:
            self.btn_play.clicked.connect(self._play)
            self.btn_pause.clicked.connect(self._pause)
            self.slider.sliderMoved.connect(self._on_slider_moved)
            self.cmb_video.currentIndexChanged.connect(self._on_video_selected)
            self.btn_browse_video.clicked.connect(self._browse_video)
            self.btn_reload_list.clicked.connect(self._reload_sources)

        self._populate_zone_table()
        if self._video_enabled and self._video_sources:
            self._on_video_selected(0)

    # ----- работа с видео -----
    def _collect_videos(self, dirs: List[Path]) -> List[Path]:
        videos: List[Path] = []
        seen = set()
        for folder in dirs:
            if not folder:
                continue
            try:
                for pattern in ("*.mp4", "*.mov", "*.m4v", "*.webm"):
                    for file in folder.glob(pattern):
                        if file not in seen:
                            videos.append(file)
                            seen.add(file)
            except Exception:
                continue
        return videos

    def _reload_sources(self):
        self._video_sources = self._collect_videos(self._source_dirs)
        self.cmb_video.blockSignals(True)
        self.cmb_video.clear()
        for path in self._video_sources:
            self.cmb_video.addItem(path.name, str(path))
        self.cmb_video.blockSignals(False)
        if self._video_sources:
            self._on_video_selected(0)
        else:
            self._release_capture()
            self._reset_video_controls()

    def _on_video_selected(self, index: int):
        if not self._video_enabled:
            return
        path = Path(self.cmb_video.itemData(index) or "")
        if not path.exists():
            self.lbl_hint.setText("Выбери видео для предпросмотра")
            self._release_capture()
            self._reset_video_controls()
            return
        self._load_video(path)

    def _browse_video(self):
        if not self._video_enabled:
            return
        start_dir = self._source_dirs[0] if self._source_dirs else Path.home()
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Выбрать видео для предпросмотра",
            str(start_dir),
            "Видео (*.mp4 *.mov *.m4v *.webm);;Все файлы (*)",
        )
        if not file_path:
            return
        path = Path(file_path)
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "Файл не найден", f"Файл {path} недоступен.")
            return
        if path not in self._video_sources:
            self._video_sources.append(path)
            self.cmb_video.addItem(path.name, str(path))
        parent_dir = path.parent
        if parent_dir and parent_dir not in self._source_dirs:
            self._source_dirs.insert(0, parent_dir)
        index = self.cmb_video.findData(str(path))
        if index >= 0:
            self.cmb_video.setCurrentIndex(index)
            self._on_video_selected(index)

    def _load_video(self, path: Path):
        assert self._video_enabled
        self._release_capture()
        if cv2 is None:
            self.lbl_hint.setText(VIDEO_PREVIEW_TIP)
            self._reset_video_controls()
            return

        cap = cv2.VideoCapture(str(path))
        if not cap or not cap.isOpened():
            self.lbl_hint.setText("Не удалось открыть файл для предпросмотра")
            self._reset_video_controls()
            return

        self._capture = cap
        self._fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        if self._fps <= 0:
            self._fps = 25.0
        self._frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if self._frame_count < 0:
            self._frame_count = 0
        self.slider.setEnabled(self._frame_count > 0)
        self.slider.setRange(0, max(self._frame_count - 1, 0))
        self.btn_play.setEnabled(self._frame_count > 0)
        self.btn_pause.setEnabled(False)
        self._current_frame = 0
        self._frame_size = QtCore.QSizeF()
        self._seek_to(self._current_frame)
        self.lbl_hint.setText(f"Открыт файл: {path}")

    def _seek_to(self, frame_index: int):
        if not self._capture:
            return
        frame_index = max(0, frame_index)
        if self._frame_count:
            frame_index = min(frame_index, self._frame_count - 1)
        self._capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = self._capture.read()
        if not ok:
            return
        self._current_frame = frame_index
        self._show_frame(frame)

    def _advance_frame(self):
        if not self._capture:
            return
        ok, frame = self._capture.read()
        if not ok:
            self._timer.stop()
            self.btn_play.setEnabled(True)
            self.btn_pause.setEnabled(False)
            return
        self._current_frame += 1
        self._show_frame(frame)

    def _show_frame(self, frame):
        if self._pixmap_item is None:
            return
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if cv2 is not None else frame
        height, width, _ = frame_rgb.shape
        bytes_per_line = width * 3
        image = QtGui.QImage(frame_rgb.data, width, height, bytes_per_line, QtGui.QImage.Format.Format_RGB888)
        pixmap = QtGui.QPixmap.fromImage(image)
        self._pixmap_item.setPixmap(pixmap)
        self._frame_size = QtCore.QSizeF(width, height)
        self.view.fitInView(self._pixmap_item, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        self._update_overlay_geometry()
        self.slider.blockSignals(True)
        self.slider.setValue(self._current_frame)
        self.slider.blockSignals(False)
        self._update_time_label()

    def _play(self):
        if not self._capture or self._frame_count <= 0:
            return
        interval = max(1, int(1000 / self._fps))
        self._timer.start(interval)
        self.btn_play.setEnabled(False)
        self.btn_pause.setEnabled(True)

    def _pause(self):
        if self._timer.isActive():
            self._timer.stop()
        self.btn_play.setEnabled(True)
        self.btn_pause.setEnabled(False)

    def _on_slider_moved(self, value: int):
        self._pause()
        self._seek_to(value)

    def _update_time_label(self):
        current_seconds = self._current_frame / self._fps if self._fps else 0
        total_seconds = self._frame_count / self._fps if self._fps and self._frame_count else 0
        self.lbl_time.setText(f"{self._fmt_seconds(current_seconds)} / {self._fmt_seconds(total_seconds)}")

    def _fmt_seconds(self, seconds: float) -> str:
        total = int(max(0, seconds))
        m, s = divmod(total, 60)
        return f"{m:02d}:{s:02d}"

    def _reset_video_controls(self):
        if not self._video_enabled:
            return
        self._timer.stop()
        self.btn_play.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.slider.setEnabled(False)
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self.lbl_time.setText("00:00 / 00:00")
        self._frame_count = 0
        self._current_frame = 0
        self._frame_size = QtCore.QSizeF()
        if self._pixmap_item:
            self._pixmap_item.setPixmap(QtGui.QPixmap())
        self._update_overlay_geometry()

    def _release_capture(self):
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:  # pragma: no cover - защитный случай
                pass
        self._capture = None

    def closeEvent(self, event):  # noqa: N802 - Qt-стиль
        self._timer.stop()
        self._release_capture()
        super().closeEvent(event)

    # ----- таблица зон -----
    def _populate_zone_table(self):
        self.tbl_zones.blockSignals(True)
        self.tbl_zones.setRowCount(0)
        for zone in self._zones or []:
            row = self.tbl_zones.rowCount()
            self.tbl_zones.insertRow(row)
            for col, key in enumerate(["x", "y", "w", "h"]):
                item = QtWidgets.QTableWidgetItem(str(int(zone.get(key, 0))))
                item.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
                self.tbl_zones.setItem(row, col, item)
        self.tbl_zones.blockSignals(False)
        self._update_overlay_items()

    def _add_zone(self):
        self.tbl_zones.blockSignals(True)
        row = self.tbl_zones.rowCount()
        self.tbl_zones.insertRow(row)
        for col in range(4):
            item = QtWidgets.QTableWidgetItem("0")
            item.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
            self.tbl_zones.setItem(row, col, item)
        self.tbl_zones.blockSignals(False)
        self._zones.append({"x": 0, "y": 0, "w": 0, "h": 0})
        self._update_overlay_items()
        self._mark_hint()

    def _remove_zone(self):
        row = self.tbl_zones.currentRow()
        if row < 0 or row >= len(self._zones):
            return
        self.tbl_zones.blockSignals(True)
        self.tbl_zones.removeRow(row)
        self.tbl_zones.blockSignals(False)
        del self._zones[row]
        self._update_overlay_items()
        self._mark_hint()

    def _on_zone_item_changed(self, item: QtWidgets.QTableWidgetItem):
        try:
            value = max(0, int(item.text()))
        except ValueError:
            value = 0
        item.setText(str(value))
        row = item.row()
        if row >= len(self._zones):
            return
        key = ["x", "y", "w", "h"][item.column()]
        self._zones[row][key] = value
        self._update_overlay_items()
        self._mark_hint()

    def _mark_hint(self):
        self.lbl_hint.setText("Изменения не сохранены — нажми «Сохранить», чтобы применить их.")

    def _update_overlay_items(self):
        if not self._video_enabled or not self._scene:
            return
        while len(self._overlay_items) < len(self._zones):
            rect = QtWidgets.QGraphicsRectItem()
            rect.setPen(QtGui.QPen(QtGui.QColor("#4c6ef5"), 3))
            rect.setBrush(QtGui.QColor(76, 110, 245, 60))
            rect.setZValue(10)
            self._scene.addItem(rect)
            self._overlay_items.append(rect)
        for rect in self._overlay_items[len(self._zones):]:
            self._scene.removeItem(rect)
        self._overlay_items = self._overlay_items[: len(self._zones)]
        self._update_overlay_geometry()

    def _update_overlay_geometry(self):
        if not self._video_enabled or not self._frame_size or self._frame_size.isEmpty():
            return
        for rect_item, zone in zip(self._overlay_items, self._zones):
            rect_item.setRect(zone.get("x", 0), zone.get("y", 0), zone.get("w", 0), zone.get("h", 0))

    def zones(self) -> List[Dict[str, int]]:
        return [dict(z) for z in self._zones]


