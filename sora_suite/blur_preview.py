#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Диалог предпросмотра видео и настройки зон блюра."""

from __future__ import annotations

from pathlib import Path
from typing import List, Dict

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem


class BlurPreviewDialog(QtWidgets.QDialog):
    """Показывает видео и позволяет редактировать координаты delogo-зон."""

    def __init__(self, parent, preset_name: str, zones: List[Dict[str, int]], source_dirs: List[Path]):
        super().__init__(parent)
        self.setWindowTitle(f"Предпросмотр блюра — {preset_name}")
        self.resize(960, 720)

        self._zones: List[Dict[str, int]] = [dict(z) for z in zones] if zones else []
        self._scene = QtWidgets.QGraphicsScene(self)
        self._video_item = QGraphicsVideoItem()
        self._scene.addItem(self._video_item)
        self._overlay_items: List[QtWidgets.QGraphicsRectItem] = []

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video_item)

        self._video_item.nativeSizeChanged.connect(self._update_overlay_geometry)
        self._player.positionChanged.connect(self._sync_position)
        self._player.durationChanged.connect(self._sync_duration)

        layout = QtWidgets.QVBoxLayout(self)

        picker_layout = QtWidgets.QHBoxLayout()
        picker_layout.addWidget(QtWidgets.QLabel("Видео:"))
        self.cmb_video = QtWidgets.QComboBox()
        self.cmb_video.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self._video_sources = self._collect_videos(source_dirs)
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
        self.view.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        view_layout.addWidget(self.view)
        layout.addWidget(view_container, 1)

        controls = QtWidgets.QHBoxLayout()
        self.btn_play = QtWidgets.QPushButton("▶")
        self.btn_pause = QtWidgets.QPushButton("⏸")
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.lbl_time = QtWidgets.QLabel("00:00 / 00:00")
        controls.addWidget(self.btn_play)
        controls.addWidget(self.btn_pause)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.lbl_time)
        layout.addLayout(controls)

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
        self.lbl_hint = QtWidgets.QLabel("Выбери видео и настрой координаты. Кнопка ОК сохранит изменения.")
        self.lbl_hint.setStyleSheet("color:#94a3b8")
        footer.addWidget(self.lbl_hint, 1)
        self.btn_ok = QtWidgets.QPushButton("Сохранить")
        self.btn_cancel = QtWidgets.QPushButton("Отмена")
        footer.addWidget(self.btn_ok)
        footer.addWidget(self.btn_cancel)
        layout.addLayout(footer)

        self.btn_play.clicked.connect(self._player.play)
        self.btn_pause.clicked.connect(self._player.pause)
        self.slider.sliderMoved.connect(self._player.setPosition)
        self.btn_zone_add.clicked.connect(self._add_zone)
        self.btn_zone_remove.clicked.connect(self._remove_zone)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        self.tbl_zones.itemChanged.connect(self._on_zone_item_changed)
        self.cmb_video.currentIndexChanged.connect(self._on_video_selected)
        self.btn_browse_video.clicked.connect(self._browse_video)
        self.btn_reload_list.clicked.connect(lambda: self._reload_sources(source_dirs))

        self._populate_zone_table()
        if self._video_sources:
            self._on_video_selected(0)

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

    def _reload_sources(self, dirs: List[Path]):
        self._video_sources = self._collect_videos(dirs)
        self.cmb_video.blockSignals(True)
        self.cmb_video.clear()
        for path in self._video_sources:
            self.cmb_video.addItem(path.name, str(path))
        self.cmb_video.blockSignals(False)
        if self._video_sources:
            self._on_video_selected(0)

    def _on_video_selected(self, index: int):
        path = Path(self.cmb_video.itemData(index) or "")
        if path and path.exists():
            self._player.pause()
            self._player.setPosition(0)
            self._player.setSource(QtCore.QUrl.fromLocalFile(str(path)))
            self.lbl_hint.setText(f"Открыт файл: {path}")
        else:
            self.lbl_hint.setText("Выбери видео для предпросмотра")

    def _browse_video(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Выбери видео", "", "Видео (*.mp4 *.mov *.m4v *.webm)")
        if path:
            if self.cmb_video.findData(path) == -1:
                self.cmb_video.addItem(Path(path).name, path)
            self.cmb_video.setCurrentIndex(self.cmb_video.findData(path))

    def _sync_position(self, pos: int):
        self.slider.blockSignals(True)
        self.slider.setValue(pos)
        self.slider.blockSignals(False)
        total = max(self._player.duration(), 1)
        self.lbl_time.setText(f"{self._fmt_ms(pos)} / {self._fmt_ms(total)}")

    def _sync_duration(self, duration: int):
        self.slider.setRange(0, duration)
        self.lbl_time.setText(f"00:00 / {self._fmt_ms(duration)}")

    def _fmt_ms(self, ms: int) -> str:
        seconds = int(ms / 1000)
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

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
        while len(self._overlay_items) < len(self._zones):
            rect = QtWidgets.QGraphicsRectItem()
            rect.setPen(QtGui.QPen(QtGui.QColor("#4c6ef5"), 3))
            rect.setBrush(QtGui.QColor(76, 110, 245, 60))
            rect.setZValue(10)
            self._scene.addItem(rect)
            self._overlay_items.append(rect)
        for rect in self._overlay_items[len(self._zones):]:
            self._scene.removeItem(rect)
        self._overlay_items = self._overlay_items[:len(self._zones)]
        self._update_overlay_geometry()

    def _update_overlay_geometry(self):
        size = self._video_item.nativeSize()
        if not size or size.width() == 0 or size.height() == 0:
            return
        for rect_item, zone in zip(self._overlay_items, self._zones):
            rect_item.setRect(zone.get("x", 0), zone.get("y", 0), zone.get("w", 0), zone.get("h", 0))

    def zones(self) -> List[Dict[str, int]]:
        return [dict(z) for z in self._zones]

