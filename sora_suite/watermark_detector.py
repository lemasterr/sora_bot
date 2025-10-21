"""Вспомогательный модуль для автодетекта водяного знака на видео."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import cv2  # type: ignore[import]
import numpy as np


def _ensure_gray(image: Any) -> Optional[np.ndarray]:
    """Приведёт входное изображение к оттенкам серого."""

    if image is None:
        return None
    arr = np.asarray(image)
    if arr.size == 0:
        return None
    if arr.ndim == 2:
        return arr
    return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)


def _iter_sample_frames(total: int, desired: int) -> Iterable[int]:
    if total > 0:
        if total <= desired:
            return range(total)
        points = np.linspace(0, max(total - 1, 0), desired)
        return sorted({int(round(p)) for p in points})
    return range(desired)


def detect_watermark(
    video_path: Union[str, Path],
    template_path: Union[str, Path],
    **cfg: Any,
) -> Union[
    Optional[Tuple[int, int, int, int]],
    Tuple[Optional[Tuple[int, int, int, int]], Optional[float]],
]:
    """Попытка найти водяной знак на видео с помощью сопоставления шаблона.

    Возвращает (x, y, w, h) в пикселях или None, если уверенного совпадения нет.
    При ``return_score=True`` дополнительно возвращает максимальное значение метрики.
    """

    video_path = str(video_path)
    template_path = str(template_path)

    return_score = bool(cfg.get("return_score"))
    return_details = bool(cfg.get("return_details"))
    return_series = bool(cfg.get("return_series")) or return_details
    threshold = float(cfg.get("threshold", 0.7) or 0.7)
    frames_to_scan = max(int(cfg.get("frames", 5) or 1), 1)
    blur_kernel = int(cfg.get("blur_kernel", 5) or 0)
    downscale_raw = cfg.get("downscale")

    downscale_value: Optional[float]
    try:
        downscale_value = float(downscale_raw)
    except (TypeError, ValueError):
        downscale_value = None
    if downscale_value is not None and downscale_value <= 0:
        downscale_value = None

    template_image = cfg.get("template_image")
    template_gray = _ensure_gray(template_image)
    if template_gray is None:
        template_gray = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template_gray is None or template_gray.size == 0:
        raise ValueError(f"Не удалось загрузить шаблон водяного знака: {template_path}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео: {video_path}")

    best_score = -1.0
    best_loc: Optional[Tuple[int, int]] = None
    best_scale = 1.0
    best_template_shape = template_gray.shape
    best_frame_size: Optional[Tuple[int, int]] = None
    template_cache: Dict[float, np.ndarray] = {}
    series: List[Dict[str, Any]] = []

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frame_indices = list(_iter_sample_frames(frame_count, frames_to_scan))
    fps_raw = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    fps = fps_raw if fps_raw > 0 else None
    duration: Optional[float] = None
    if fps and frame_count > 0:
        duration = frame_count / fps

    try:
        for idx in frame_indices:
            if idx > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            orig_h, orig_w = gray.shape

            scale_factor = 1.0
            if downscale_value is not None:
                if 0 < downscale_value < 1:
                    scale_factor = downscale_value
                elif downscale_value > 1:
                    max_dim = max(orig_w, orig_h)
                    if max_dim > downscale_value:
                        scale_factor = downscale_value / max_dim
            scale_factor = max(min(scale_factor, 1.0), 1e-3)

            if scale_factor != 1.0:
                scaled_w = max(1, int(round(orig_w * scale_factor)))
                scaled_h = max(1, int(round(orig_h * scale_factor)))
                gray_scaled = cv2.resize(gray, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)
            else:
                gray_scaled = gray

            if blur_kernel >= 3 and blur_kernel % 2 == 1:
                gray_scaled = cv2.GaussianBlur(gray_scaled, (blur_kernel, blur_kernel), 0)

            scale_key = round(scale_factor, 6)
            tmpl = template_cache.get(scale_key)
            if tmpl is None:
                tmpl = template_gray
                if scale_factor != 1.0:
                    scaled_tw = max(1, int(round(template_gray.shape[1] * scale_factor)))
                    scaled_th = max(1, int(round(template_gray.shape[0] * scale_factor)))
                    tmpl = cv2.resize(template_gray, (scaled_tw, scaled_th), interpolation=cv2.INTER_AREA)
                template_cache[scale_key] = tmpl

            if gray_scaled.shape[0] < tmpl.shape[0] or gray_scaled.shape[1] < tmpl.shape[1]:
                continue

            result = cv2.matchTemplate(gray_scaled, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            tmpl_h, tmpl_w = tmpl.shape[:2]
            cur_bbox: Optional[Tuple[int, int, int, int]] = None
            if gray_scaled.shape[0] >= tmpl_h and gray_scaled.shape[1] >= tmpl_w:
                if scale_factor != 1.0:
                    inv = 1.0 / scale_factor
                    x = int(round(max_loc[0] * inv))
                    y = int(round(max_loc[1] * inv))
                    w = int(round(tmpl_w * inv))
                    h = int(round(tmpl_h * inv))
                else:
                    x = int(max_loc[0])
                    y = int(max_loc[1])
                    w = int(tmpl_w)
                    h = int(tmpl_h)

                x = max(0, min(x, orig_w - 1))
                y = max(0, min(y, orig_h - 1))
                w = max(1, min(w, orig_w - x))
                h = max(1, min(h, orig_h - y))
                cur_bbox = (x, y, w, h)

            if cur_bbox:
                entry_time: Optional[float] = None
                if fps and idx >= 0:
                    entry_time = idx / fps
                series.append(
                    {
                        "frame": idx,
                        "time": entry_time,
                        "score": float(max_val),
                        "bbox": cur_bbox,
                        "frame_size": (orig_w, orig_h),
                    }
                )

            if max_val > best_score and cur_bbox:
                best_score = float(max_val)
                best_loc = (int(max_loc[0]), int(max_loc[1]))
                best_scale = scale_factor
                best_template_shape = tmpl.shape
                best_frame_size = (orig_w, orig_h)
    finally:
        cap.release()

    best_bbox: Optional[Tuple[int, int, int, int]] = None
    if best_loc is not None and best_frame_size:
        frame_w, frame_h = best_frame_size
        tmpl_h, tmpl_w = best_template_shape
        if best_scale != 1.0:
            inv = 1.0 / best_scale
            x = int(round(best_loc[0] * inv))
            y = int(round(best_loc[1] * inv))
            w = int(round(tmpl_w * inv))
            h = int(round(tmpl_h * inv))
        else:
            x = int(best_loc[0])
            y = int(best_loc[1])
            w = int(tmpl_w)
            h = int(tmpl_h)

        x = max(0, min(x, frame_w - 1))
        y = max(0, min(y, frame_h - 1))
        w = max(1, min(w, frame_w - x))
        h = max(1, min(h, frame_h - y))
        best_bbox = (x, y, w, h)

    series_payload: List[Dict[str, Any]] = []
    if return_series:
        for entry in series:
            bbox = entry.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            series_payload.append(
                {
                    "frame": entry.get("frame"),
                    "time": entry.get("time"),
                    "score": entry.get("score"),
                    "bbox": bbox,
                    "frame_size": entry.get("frame_size"),
                    "accepted": bool(entry.get("score", 0) >= threshold),
                }
            )

    if best_bbox and best_score >= threshold:
        if return_details:
            payload: Dict[str, Any] = {
                "bbox": best_bbox,
                "best_bbox": best_bbox,
                "score": best_score,
                "frame_size": best_frame_size,
            }
            if return_series:
                payload.update(
                    {
                        "series": series_payload,
                        "threshold": threshold,
                        "frame_count": frame_count,
                        "fps": fps,
                        "duration": duration,
                    }
                )
            return payload
        if return_score:
            return (best_bbox, best_score)
        return best_bbox

    if return_details:
        payload = {
            "bbox": None,
            "best_bbox": best_bbox,
            "score": best_score if best_score >= 0 else None,
            "frame_size": best_frame_size,
        }
        if return_series:
            payload.update(
                {
                    "series": series_payload,
                    "threshold": threshold,
                    "frame_count": frame_count,
                    "fps": fps,
                    "duration": duration,
                }
            )
        return payload
    if return_score:
        score = best_score if best_score >= 0 else None
        return (None, score)
    return None
