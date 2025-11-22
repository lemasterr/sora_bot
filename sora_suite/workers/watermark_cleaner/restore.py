#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remove jumping watermarks by replacing them with clean video fragments."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2  # type: ignore[import]
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "downloads"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "restored"
DEFAULT_TEMPLATE = PROJECT_ROOT / "watermark.png"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LogRecord = Dict[str, object]
BBox = Tuple[int, int, int, int]


def _log(payload: LogRecord) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _status(message: str) -> None:
    print(message, flush=True)


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bbox_iou(a: BBox, b: BBox) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2 = ax + aw
    ay2 = ay + ah
    bx2 = bx + bw
    by2 = by + bh
    inter_x1 = max(ax, bx)
    inter_y1 = max(ay, by)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0:
        return 0.0
    area_a = aw * ah
    area_b = bw * bh
    union = max(area_a + area_b - inter_area, 1)
    return inter_area / union


def _expand_bbox(bbox: BBox, frame_w: int, frame_h: int, padding_px: int, padding_pct: float) -> BBox:
    x, y, w, h = bbox
    pad_x = max(padding_px, int(round(w * padding_pct)))
    pad_y = max(padding_px, int(round(h * padding_pct)))
    x0 = max(0, x - pad_x)
    y0 = max(0, y - pad_y)
    x1 = min(frame_w, x + w + pad_x)
    y1 = min(frame_h, y + h + pad_y)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _collect_videos(folder: Path) -> List[Path]:
    patterns = ("*.mp4", "*.mov", "*.m4v", "*.webm", "*.mkv")
    files: List[Path] = []
    for pattern in patterns:
        files.extend(folder.glob(pattern))
    return sorted(files)


def _load_video(path: Path) -> Tuple[List[np.ndarray], float]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео: {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frames: List[np.ndarray] = []
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        frames.append(frame)
    cap.release()
    if not frames:
        raise RuntimeError(f"Видео пустое: {path}")
    return frames, fps


def _write_video(path: Path, frames: Sequence[np.ndarray], fps: float) -> None:
    first = frames[0]
    height, width = first.shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, max(fps, 1.0), (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"Не удалось открыть файл для записи: {path}")
    for frame in frames:
        if frame.shape[0] != height or frame.shape[1] != width:
            resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)
            writer.write(resized)
        else:
            writer.write(frame)
    writer.release()


def _build_detection_map(
    video_path: Path,
    *,
    template_path: Path,
    template_package,
    detect_cfg: Dict[str, object],
    per_frame: bool,
) -> Dict[int, List[BBox]]:
    from .watermark_detector import detect_watermark

    kwargs = dict(detect_cfg)
    kwargs.update({
        "template_path": str(template_path),
        "template_package": template_package,
        "return_details": True,
        "return_series": True,
    })
    if per_frame:
        cap = cv2.VideoCapture(str(video_path))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        if total > 0:
            kwargs["frames"] = total
    result = detect_watermark(str(video_path), str(template_path), **kwargs)
    series = []
    if isinstance(result, dict):
        series = result.get("series") or []
    detections: Dict[int, List[BBox]] = {}
    for entry in series:
        if not isinstance(entry, dict):
            continue
        frame_idx = entry.get("frame")
        bbox = entry.get("bbox")
        score = entry.get("score")
        accepted = entry.get("accepted", False)
        if frame_idx is None or bbox is None:
            continue
        try:
            frame_no = int(frame_idx)
        except (TypeError, ValueError):
            continue
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        if score is None:
            continue
        if not accepted and float(score) < float(kwargs.get("threshold", 0.7)):
            continue
        detections.setdefault(frame_no, []).append(tuple(int(v) for v in bbox))  # type: ignore[arg-type]
    return detections


def _median_patch(patches: Sequence[np.ndarray]) -> Optional[np.ndarray]:
    valid = [p for p in patches if isinstance(p, np.ndarray) and p.size > 0]
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    stack = np.stack(valid, axis=0)
    median = np.median(stack, axis=0)
    return median.astype(np.uint8)


def _choose_donors(
    frames: Sequence[np.ndarray],
    detections: Dict[int, List[BBox]],
    frame_idx: int,
    bbox: BBox,
    *,
    search_span: int,
    max_iou: float,
    pool_size: int,
) -> List[int]:
    donors: List[int] = []
    total = len(frames)
    for offset in range(1, max(search_span, 1) + 1):
        for candidate in (frame_idx - offset, frame_idx + offset):
            if candidate < 0 or candidate >= total:
                continue
            overlaps = detections.get(candidate, [])
            if any(_bbox_iou(bbox, other) > max_iou for other in overlaps):
                continue
            donors.append(candidate)
            if len(donors) >= pool_size:
                return donors
    return donors


def _replace_bbox(
    frame: np.ndarray,
    patch: np.ndarray,
    region: BBox,
    *,
    blend_mode: str,
) -> np.ndarray:
    x, y, w, h = region
    mask = np.full((h, w), 255, dtype=np.uint8)
    try:
        clone_flag = cv2.NORMAL_CLONE if blend_mode != "mixed" else cv2.MIXED_CLONE
        center = (x + w // 2, y + h // 2)
        result = cv2.seamlessClone(patch, frame, mask, center, clone_flag)
        return result
    except cv2.error:
        # fallback: direct paste
        frame = frame.copy()
        frame[y : y + h, x : x + w] = patch
        return frame


def _inpaint_region(frame: np.ndarray, region: BBox, *, radius: int, method: int) -> np.ndarray:
    x, y, w, h = region
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[y : y + h, x : x + w] = 255
    return cv2.inpaint(frame, mask, max(radius, 1), method)


def _process_video(
    source: Path,
    dest: Path,
    *,
    template_path: Path,
    template_package,
    detect_cfg: Dict[str, object],
    padding_px: int,
    padding_pct: float,
    min_size: int,
    search_span: int,
    max_iou: float,
    pool_size: int,
    blend_mode: str,
    inpaint_radius: int,
    inpaint_method: str,
    per_frame: bool,
) -> None:
    frames, fps = _load_video(source)
    frame_h, frame_w = frames[0].shape[:2]
    detections = _build_detection_map(
        source,
        template_path=template_path,
        template_package=template_package,
        detect_cfg=detect_cfg,
        per_frame=per_frame,
    )
    if not detections:
        _status(f"[WMR] Водяной знак не найден → {source.name}")
        _write_video(dest, frames, fps)
        return

    processed = frames.copy()
    inpaint_flag = cv2.INPAINT_TELEA if inpaint_method != "ns" else cv2.INPAINT_NS

    for idx, frame in enumerate(frames):
        regions = detections.get(idx, [])
        if not regions:
            continue
        for bbox in regions:
            x, y, w, h = bbox
            if w < min_size or h < min_size:
                continue
            region = _expand_bbox(bbox, frame_w, frame_h, padding_px, padding_pct)
            donors = _choose_donors(
                frames,
                detections,
                idx,
                region,
                search_span=search_span,
                max_iou=max_iou,
                pool_size=pool_size,
            )
            patches = []
            for donor_idx in donors:
                donor = frames[donor_idx]
                dx, dy, dw, dh = region
                patch = donor[dy : dy + dh, dx : dx + dw]
                if patch.shape[0] == dh and patch.shape[1] == dw:
                    patches.append(patch)
            patch = _median_patch(patches)
            if patch is None:
                processed[idx] = _inpaint_region(frame, region, radius=inpaint_radius, method=inpaint_flag)
                continue
            try:
                processed[idx] = _replace_bbox(frame, patch, region, blend_mode=blend_mode)
            except Exception:
                processed[idx] = _inpaint_region(frame, region, radius=inpaint_radius, method=inpaint_flag)
        if idx % 10 == 0:
            _status(f"[WMR] Обработка {source.name}: кадр {idx + 1}/{len(frames)}")

    _write_video(dest, processed, fps)


def main() -> int:
    from .watermark_detector import prepare_template

    env = os.environ
    source_dir = Path(env.get("WMR_SOURCE_DIR", str(DEFAULT_SOURCE_DIR)))
    output_dir = Path(env.get("WMR_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    template_path = Path(env.get("WMR_TEMPLATE", str(DEFAULT_TEMPLATE)))
    padding_px = max(0, _coerce_int(env.get("WMR_PADDING_PX"), 12))
    padding_pct = max(0.0, min(1.0, _coerce_float(env.get("WMR_PADDING_PCT"), 0.18)))
    min_size = max(2, _coerce_int(env.get("WMR_MIN_SIZE"), 32))
    search_span = max(1, _coerce_int(env.get("WMR_SEARCH_SPAN"), 12))
    max_iou = max(0.0, min(1.0, _coerce_float(env.get("WMR_MAX_IOU"), 0.25)))
    pool_size = max(1, _coerce_int(env.get("WMR_POOL"), 4))
    blend_mode = (env.get("WMR_BLEND", "normal") or "normal").strip().lower()
    inpaint_radius = max(1, _coerce_int(env.get("WMR_INPAINT_RADIUS"), 6))
    inpaint_method = (env.get("WMR_INPAINT_METHOD", "telea") or "telea").strip().lower()
    threshold = _coerce_float(env.get("WMR_THRESHOLD"), 0.78)
    frames_to_scan = max(1, _coerce_int(env.get("WMR_FRAMES"), 120))
    downscale = max(0, _coerce_int(env.get("WMR_DOWNSCALE"), 1080))
    scale_min = _coerce_float(env.get("WMR_SCALE_MIN"), 0.85)
    scale_max = _coerce_float(env.get("WMR_SCALE_MAX"), 1.2)
    scale_steps = max(3, _coerce_int(env.get("WMR_SCALE_STEPS"), 9))
    mask_threshold = max(0, _coerce_int(env.get("WMR_MASK_THRESHOLD"), 8))
    full_scan_flag = env.get("WMR_FULL_SCAN")
    donor_per_frame = True if full_scan_flag is None else full_scan_flag not in {"0", "false", "False", "no"}

    detect_cfg = {
        "threshold": threshold,
        "frames": frames_to_scan,
        "downscale": downscale,
        "scale_min": scale_min,
        "scale_max": scale_max,
        "scale_steps": scale_steps,
        "mask_threshold": mask_threshold,
    }

    if not source_dir.exists():
        _status(f"[WMR] Папка с исходниками не найдена: {source_dir}")
        return 1
    if not template_path.exists():
        _status(f"[WMR] Шаблон водяного знака не найден: {template_path}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    template_image = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
    if template_image is None or template_image.size == 0:
        _status(f"[WMR] Не удалось загрузить шаблон: {template_path}")
        return 1

    template_package = prepare_template(template_image, template_path, mask_threshold=mask_threshold)

    videos = _collect_videos(source_dir)
    if not videos:
        _status("[WMR] Нет видео для обработки")
        return 0

    _status(f"[WMR] Найдено {len(videos)} видео → обработка…")
    start_time = time.time()
    processed = 0
    errors = 0

    for video in videos:
        rel = video.name
        target = output_dir / rel
        try:
            _status(f"[WMR] Обработка {rel}")
            _process_video(
                video,
                target,
                template_path=template_path,
                template_package=template_package,
                detect_cfg=detect_cfg,
                padding_px=padding_px,
                padding_pct=padding_pct,
                min_size=min_size,
                search_span=search_span,
                max_iou=max_iou,
                pool_size=pool_size,
                blend_mode=blend_mode,
                inpaint_radius=inpaint_radius,
                inpaint_method=inpaint_method,
                per_frame=donor_per_frame,
            )
            processed += 1
            _status(f"[WMR] ✅ {rel} готов")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            _status(f"[WMR] ⚠️ Ошибка для {rel}: {exc}")

    elapsed = time.time() - start_time
    summary = {
        "event": "watermark_restore",
        "processed": processed,
        "errors": errors,
        "source": str(source_dir),
        "output": str(output_dir),
        "template": str(template_path),
        "seconds": round(elapsed, 2),
    }
    _log(summary)
    if errors:
        _status(f"[WMR] Завершено с ошибками: {processed} ок, {errors} ошибок")
        return 1
    _status(f"[WMR] Завершено: {processed} файлов, {elapsed:.1f} с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
