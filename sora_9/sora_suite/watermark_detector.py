from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import cv2  # type: ignore[import]
import numpy as np


@dataclass
class TemplateAssets:
    """Prepared template buffers reused across detections."""

    gray: np.ndarray
    mask: Optional[np.ndarray]
    original_size: Tuple[int, int]


def _ensure_gray(image: Any) -> Optional[np.ndarray]:
    """Convert an arbitrary image array into grayscale."""

    if image is None:
        return None
    arr = np.asarray(image)
    if arr.size == 0:
        return None
    if arr.ndim == 2:
        return np.ascontiguousarray(arr)
    return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)


def _iter_sample_frames(total: int, desired: int) -> Iterable[int]:
    if total > 0:
        if total <= desired:
            return range(total)
        points = np.linspace(0, max(total - 1, 0), desired)
        return sorted({int(round(p)) for p in points})
    return range(desired)


def prepare_template(
    template_source: Any,
    template_path: Union[str, Path],
    *,
    mask_threshold: int = 8,
) -> TemplateAssets:
    """Load template image, extract grayscale copy and optional alpha mask."""

    path = str(template_path)
    if isinstance(template_source, np.ndarray):
        raw = template_source
    else:
        raw = cv2.imread(path, cv2.IMREAD_UNCHANGED)

    if raw is None or getattr(raw, "size", 0) == 0:
        raise ValueError(f"Не удалось загрузить шаблон водяного знака: {path}")

    arr = np.asarray(raw)
    if arr.ndim == 2:
        gray = arr
        alpha = None
    elif arr.ndim == 3:
        if arr.shape[2] == 4:
            bgr = arr[:, :, :3]
            alpha = arr[:, :, 3]
        else:
            bgr = arr
            alpha = None
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError("Некорректное изображение шаблона: требуется 1, 3 или 4 канала")

    mask: Optional[np.ndarray] = None
    if alpha is not None:
        mask = (alpha.astype(np.uint8) > max(0, int(mask_threshold))) * 255
        if mask.any():
            coords = cv2.findNonZero(mask)
            if coords is not None:
                x, y, w, h = cv2.boundingRect(coords)
                gray = gray[y : y + h, x : x + w]
                mask = mask[y : y + h, x : x + w]
        else:
            mask = None

    gray = np.ascontiguousarray(gray)
    if gray.dtype != np.uint8:
        gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    if mask is not None:
        mask = np.ascontiguousarray(mask.astype(np.uint8))

    h, w = gray.shape[:2]
    if h <= 1 or w <= 1:
        raise ValueError("Слишком маленький шаблон водяного знака")

    return TemplateAssets(gray=gray, mask=mask, original_size=(w, h))


def _coerce_template_package(package: Any) -> Optional[TemplateAssets]:
    if isinstance(package, TemplateAssets):
        return package
    if isinstance(package, dict) and "gray" in package:
        gray = _ensure_gray(package.get("gray"))
        if gray is None:
            return None
        mask_obj = package.get("mask")
        mask = _ensure_gray(mask_obj) if mask_obj is not None else None
        original = package.get("original_size")
        if isinstance(original, (tuple, list)) and len(original) == 2:
            try:
                original_size = (int(original[0]), int(original[1]))
            except Exception:  # noqa: BLE001
                original_size = (gray.shape[1], gray.shape[0])
        else:
            original_size = (gray.shape[1], gray.shape[0])
        return TemplateAssets(
            gray=np.ascontiguousarray(gray),
            mask=np.ascontiguousarray(mask.astype(np.uint8)) if mask is not None else None,
            original_size=original_size,
        )
    return None


def detect_watermark(
    video_path: Union[str, Path],
    template_path: Union[str, Path],
    **cfg: Any,
) -> Union[
    Optional[Tuple[int, int, int, int]],
    Tuple[Optional[Tuple[int, int, int, int]], Optional[float]],
]:
    """Попытка найти водяной знак на видео с помощью сопоставления шаблона."""

    video_path = str(video_path)
    template_path = str(template_path)

    return_score = bool(cfg.get("return_score"))
    return_details = bool(cfg.get("return_details"))
    return_series = bool(cfg.get("return_series")) or return_details
    threshold = float(cfg.get("threshold", 0.7) or 0.7)
    frames_to_scan = max(int(cfg.get("frames", 5) or 1), 1)
    blur_kernel = int(cfg.get("blur_kernel", 5) or 0)
    downscale_raw = cfg.get("downscale")

    try:
        downscale_value = float(downscale_raw)
    except (TypeError, ValueError):
        downscale_value = None
    if downscale_value is not None and downscale_value <= 0:
        downscale_value = None

    try:
        mask_threshold = int(cfg.get("mask_threshold", 8))
    except (TypeError, ValueError):
        mask_threshold = 8

    template_package = _coerce_template_package(cfg.get("template_package"))
    if template_package is None:
        template_source = cfg.get("template_image")
        try:
            template_package = prepare_template(template_source, template_path, mask_threshold=mask_threshold)
        except Exception:  # noqa: BLE001
            gray = _ensure_gray(template_source)
            if gray is None:
                template_package = prepare_template(None, template_path, mask_threshold=mask_threshold)
            else:
                template_package = TemplateAssets(
                    gray=np.ascontiguousarray(gray),
                    mask=None,
                    original_size=(gray.shape[1], gray.shape[0]),
                )

    template_gray = template_package.gray
    template_mask = template_package.mask
    method = cv2.TM_CCORR_NORMED if template_mask is not None else cv2.TM_CCOEFF_NORMED

    scale_variants_cfg = cfg.get("scales")
    scale_variants: List[float] = []
    if isinstance(scale_variants_cfg, (list, tuple)):
        for value in scale_variants_cfg:
            try:
                val = float(value)
            except (TypeError, ValueError):
                continue
            if val > 0:
                scale_variants.append(val)
    if not scale_variants:
        try:
            scale_min = float(cfg.get("scale_min", 0.9))
        except (TypeError, ValueError):
            scale_min = 0.9
        try:
            scale_max = float(cfg.get("scale_max", 1.1))
        except (TypeError, ValueError):
            scale_max = 1.1
        try:
            scale_steps = int(cfg.get("scale_steps", 5))
        except (TypeError, ValueError):
            scale_steps = 5
        if scale_steps <= 1 or scale_min >= scale_max:
            scale_variants = [1.0]
        else:
            lin = np.linspace(scale_min, scale_max, scale_steps)
            scale_variants = [float(v) for v in lin]
        if 1.0 not in scale_variants:
            scale_variants.append(1.0)
    scale_variants = sorted({max(0.05, min(3.0, float(v))) for v in scale_variants})

    try:
        edge_weight = float(cfg.get("edge_weight", 0.3))
    except (TypeError, ValueError):
        edge_weight = 0.3
    edge_weight = min(max(edge_weight, 0.0), 1.0)
    use_edges = edge_weight > 0.0

    try:
        canny_low = int(cfg.get("canny_low", 40))
    except (TypeError, ValueError):
        canny_low = 40
    try:
        canny_high = int(cfg.get("canny_high", 120))
    except (TypeError, ValueError):
        canny_high = 120
    if canny_high <= canny_low:
        canny_high = canny_low * 2 or 80

    try:
        z_weight = float(cfg.get("score_z_weight", 0.25))
    except (TypeError, ValueError):
        z_weight = 0.25
    z_weight = min(max(z_weight, 0.0), 1.0)

    try:
        score_bias = float(cfg.get("score_bias", 0.0))
    except (TypeError, ValueError):
        score_bias = 0.0
    try:
        score_floor = float(cfg.get("score_floor", 0.0))
    except (TypeError, ValueError):
        score_floor = 0.0
    score_floor = max(0.0, min(1.0, score_floor))

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео: {video_path}")

    best_score = -1.0
    best_raw_score = -1.0
    best_edge_score: Optional[float] = None
    best_z_score = 0.0
    best_loc: Optional[Tuple[int, int]] = None
    best_scale = 1.0
    best_variant = 1.0
    best_template_shape = template_gray.shape
    best_frame_size: Optional[Tuple[int, int]] = None
    template_cache: Dict[Tuple[float, float], Dict[str, np.ndarray]] = {}
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

            edges_frame: Optional[np.ndarray]
            if use_edges:
                edges_frame = cv2.Canny(gray_scaled, canny_low, canny_high)
                edges_frame = np.ascontiguousarray(edges_frame)
            else:
                edges_frame = None

            for variant in scale_variants:
                overall_scale = scale_factor * variant
                key = (round(scale_factor, 6), round(overall_scale, 6))
                cached = template_cache.get(key)
                if cached is None:
                    tmpl_gray = template_gray
                    tmpl_mask = template_mask
                    if overall_scale != 1.0:
                        scaled_tw = max(1, int(round(template_gray.shape[1] * overall_scale)))
                        scaled_th = max(1, int(round(template_gray.shape[0] * overall_scale)))
                        tmpl_gray = cv2.resize(template_gray, (scaled_tw, scaled_th), interpolation=cv2.INTER_AREA)
                        if template_mask is not None:
                            tmpl_mask = cv2.resize(template_mask, (scaled_tw, scaled_th), interpolation=cv2.INTER_NEAREST)
                    tmpl_gray = np.ascontiguousarray(tmpl_gray)
                    if tmpl_mask is not None:
                        tmpl_mask = np.ascontiguousarray(tmpl_mask.astype(np.uint8))
                    tmpl_edges: Optional[np.ndarray] = None
                    if use_edges:
                        tmpl_edges = cv2.Canny(tmpl_gray, canny_low, canny_high)
                        if tmpl_mask is not None:
                            tmpl_edges = cv2.bitwise_and(tmpl_edges, tmpl_mask)
                        tmpl_edges = np.ascontiguousarray(tmpl_edges)
                    cached = {"gray": tmpl_gray, "mask": tmpl_mask, "edges": tmpl_edges}
                    template_cache[key] = cached
                tmpl = cached["gray"]
                tmpl_mask = cached.get("mask")
                tmpl_edges = cached.get("edges")

                if gray_scaled.shape[0] < tmpl.shape[0] or gray_scaled.shape[1] < tmpl.shape[1]:
                    continue

                if tmpl_mask is not None:
                    result = cv2.matchTemplate(gray_scaled, tmpl, method, mask=tmpl_mask)
                else:
                    result = cv2.matchTemplate(gray_scaled, tmpl, method)

                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                if not np.isfinite(max_val):
                    continue
                mean_val = float(np.mean(result)) if result.size else 0.0
                std_val = float(np.std(result)) if result.size else 0.0
                z_score = 0.0
                if std_val > 1e-6:
                    z_score = max(0.0, (float(max_val) - mean_val) / (std_val + 1e-6))
                z_component = math.tanh(z_score / 3.0) if z_score > 0 else 0.0

                edge_val: Optional[float] = None
                if use_edges and edges_frame is not None and tmpl_edges is not None:
                    if edges_frame.shape[0] >= tmpl_edges.shape[0] and edges_frame.shape[1] >= tmpl_edges.shape[1]:
                        edge_result = cv2.matchTemplate(edges_frame, tmpl_edges, cv2.TM_CCOEFF_NORMED)
                        _, edge_max, _, _ = cv2.minMaxLoc(edge_result)
                        if np.isfinite(edge_max):
                            edge_val = float(edge_max)

                combined = float(max_val)
                if edge_val is not None:
                    combined = (1.0 - edge_weight) * combined + edge_weight * max(edge_val, 0.0)
                    combined = max(combined, float(max_val))
                if z_weight > 0.0:
                    before_z = combined
                    combined = (1.0 - z_weight) * combined + z_weight * z_component
                    combined = max(combined, before_z)
                combined = max(combined, float(max_val))
                combined += score_bias
                combined = max(score_floor, combined)
                combined = float(max(0.0, min(1.0, combined)))

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
                            "score": float(combined),
                            "bbox": cur_bbox,
                            "frame_size": (orig_w, orig_h),
                            "raw_score": float(max_val),
                            "edge_score": edge_val,
                            "z_score": z_score if z_score > 0 else None,
                            "variant": float(variant),
                        }
                    )

                if combined > best_score and cur_bbox:
                    best_score = combined
                    best_raw_score = float(max_val)
                    best_edge_score = edge_val
                    best_z_score = z_score
                    best_loc = (int(max_loc[0]), int(max_loc[1]))
                    best_scale = scale_factor
                    best_variant = variant
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
                    "raw_score": entry.get("raw_score"),
                    "edge_score": entry.get("edge_score"),
                    "z_score": entry.get("z_score"),
                    "variant": entry.get("variant"),
                    "accepted": bool(entry.get("score", 0) >= threshold),
                }
            )

    if best_bbox and best_score >= threshold:
        if return_details:
            payload: Dict[str, Any] = {
                "bbox": best_bbox,
                "best_bbox": best_bbox,
                "score": best_score,
                "raw_score": best_raw_score if best_raw_score >= 0 else None,
                "edge_score": best_edge_score,
                "z_score": best_z_score if best_z_score > 0 else None,
                "frame_size": best_frame_size,
                "scale": best_scale,
                "variant": best_variant,
                "method": "TM_CCORR_NORMED" if template_mask is not None else "TM_CCOEFF_NORMED",
                "edge_weight": edge_weight,
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
            "raw_score": best_raw_score if best_raw_score >= 0 else None,
            "edge_score": best_edge_score,
            "z_score": best_z_score if best_z_score > 0 else None,
            "frame_size": best_frame_size,
            "scale": best_scale,
            "variant": best_variant,
            "method": "TM_CCORR_NORMED" if template_mask is not None else "TM_CCOEFF_NORMED",
            "edge_weight": edge_weight,
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


class WaterMarkDetector:
    """Compatibility wrapper mirroring the historic detector API."""

    def __init__(
        self,
        template_path: Union[str, Path],
        *,
        mask_threshold: int = 8,
        template_image: Optional[np.ndarray] = None,
        **default_cfg: Any,
    ) -> None:
        self.template_path = str(template_path)
        self.mask_threshold = int(mask_threshold)
        self._default_cfg: Dict[str, Any] = dict(default_cfg)
        self._template_package: Optional[TemplateAssets] = None
        self._last_result: Dict[str, Any] = {}

        image = template_image
        if image is None and Path(self.template_path).exists():
            image = cv2.imread(self.template_path, cv2.IMREAD_UNCHANGED)
        if image is not None and getattr(image, "size", 0):
            self._template_package = prepare_template(
                image,
                self.template_path,
                mask_threshold=self.mask_threshold,
            )

    def _ensure_template(self) -> TemplateAssets:
        if self._template_package is not None:
            return self._template_package
        image = cv2.imread(self.template_path, cv2.IMREAD_UNCHANGED)
        if image is None or getattr(image, "size", 0) == 0:
            raise FileNotFoundError(self.template_path)
        self._template_package = prepare_template(
            image,
            self.template_path,
            mask_threshold=self.mask_threshold,
        )
        return self._template_package

    def detect(self, video_path: Union[str, Path], **overrides: Any) -> Dict[str, Any]:
        cfg = dict(self._default_cfg)
        cfg.update(overrides)
        package = overrides.get("template_package")
        if package is None:
            package = self._ensure_template()
        result = detect_watermark(
            video_path,
            self.template_path,
            template_package=package,
            return_details=bool(cfg.pop("return_details", True)),
            return_series=bool(cfg.pop("return_series", True)),
            **cfg,
        )
        if isinstance(result, dict):
            self._last_result = result
        else:
            self._last_result = {"series": [], "details": []}
        return self._last_result

    def scan(self, video_path: Union[str, Path], **overrides: Any) -> Dict[str, Any]:
        return self.detect(video_path, **overrides)

    def last_series(self) -> List[Dict[str, Any]]:
        data = self._last_result.get("series") if isinstance(self._last_result, dict) else []
        return list(data or [])

    def _get_zone_masks(
        self,
        frame_size: Optional[Tuple[int, int]] = None,
        *,
        series: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> List[np.ndarray]:
        records = series if series is not None else self.last_series()
        if not records:
            return []
        width: Optional[int]
        height: Optional[int]
        if frame_size:
            width, height = frame_size
        else:
            width = height = None
            for entry in records:
                size = entry.get("frame_size")
                if isinstance(size, (list, tuple)) and len(size) == 2:
                    try:
                        width = int(size[0])
                        height = int(size[1])
                        break
                    except (TypeError, ValueError):
                        width = height = None
        if not width or not height:
            raise ValueError("Frame size is required to build masks")
        masks: List[np.ndarray] = []
        for entry in records:
            bbox = entry.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            x, y, w, h = [int(v) for v in bbox]
            mask = np.zeros((height, width), dtype=np.uint8)
            x = max(0, min(x, width - 1))
            y = max(0, min(y, height - 1))
            w = max(1, min(w, width - x))
            h = max(1, min(h, height - y))
            mask[y : y + h, x : x + w] = 255
            masks.append(mask)
        return masks

    def get_zone_masks(
        self,
        frame_size: Optional[Tuple[int, int]] = None,
        *,
        series: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> List[np.ndarray]:
        return self._get_zone_masks(frame_size, series=series)


def scan_region_for_flash(
    video_path: Union[str, Path],
    region: Tuple[int, int, int, int],
    *,
    frames: int = 90,
    brightness_threshold: int = 245,
    coverage_ratio: float = 0.02,
    method: str = "flash",
    edge_ratio_threshold: float = 0.006,
    min_hits: int = 1,
    downscale: float = 2.0,
) -> bool:
    """Проверяет появление знака по яркости или контрасту в указанной зоне.

    Допустимые ``method``:
        - ``flash`` (по умолчанию) — классическая проверка вспышки: считается
          соотношение ярких пикселей (по умолчанию покрытие 0.002–0.02).
        - ``edges`` — ищет резкие границы, которые появляются/исчезают в зоне
          знака (порог по доле пикселей после Canny).
        - ``hybrid`` — достаточно выполнения любого из двух критериев, что
          полезно, если знак бывает и ярким, и прозрачным.

    ``downscale`` ускоряет вычисления: при значении 2.0 зона уменьшается вдвое
    по каждой стороне перед анализом. Значение ``0`` отключает уменьшение.
    """

    x, y, w, h = [max(0, int(v)) for v in region]
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    indices = list(_iter_sample_frames(total_frames, max(frames, 1)))

    hits = 0
    min_hits = max(1, int(min_hits))
    method = (method or "flash").lower()
    edge_ratio_threshold = max(0.0, float(edge_ratio_threshold))
    coverage_ratio = max(coverage_ratio, 0.0005)

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = _ensure_gray(frame)
        if gray is None:
            continue
        height, width = gray.shape[:2]
        rx, ry = min(x, width - 1), min(y, height - 1)
        rw, rh = max(1, min(w, width - rx)), max(1, min(h, height - ry))
        region_crop = gray[ry : ry + rh, rx : rx + rw]
        if region_crop.size == 0:
            continue

        if downscale and downscale > 0:
            try:
                scale = float(downscale)
            except (TypeError, ValueError):
                scale = 0.0
            if scale > 0 and (region_crop.shape[0] > 48 or region_crop.shape[1] > 48):
                region_crop = cv2.resize(
                    region_crop,
                    (max(1, int(region_crop.shape[1] / scale)), max(1, int(region_crop.shape[0] / scale))),
                    interpolation=cv2.INTER_AREA,
                )

        bright_ratio = float(np.mean(region_crop >= max(0, min(brightness_threshold, 255))))
        edge_ratio = 0.0
        if method in {"edges", "hybrid"}:
            blurred = cv2.GaussianBlur(region_crop, (3, 3), 0)
            edges = cv2.Canny(blurred, 40, 120)
            edge_ratio = float(np.mean(edges > 0))

        flash_hit = bright_ratio >= coverage_ratio if method in {"flash", "hybrid"} else False
        edge_hit = edge_ratio >= edge_ratio_threshold if method in {"edges", "hybrid"} else False

        if flash_hit or edge_hit:
            hits += 1
            if hits >= min_hits:
                return True
    return False


def flip_video_if_no_watermark(
    video_path: Union[str, Path],
    *,
    region: Tuple[int, int, int, int],
    output_path: Optional[Union[str, Path]] = None,
    frames: int = 90,
    brightness_threshold: int = 245,
    coverage_ratio: float = 0.02,
    method: str = "flash",
    edge_ratio_threshold: float = 0.006,
    min_hits: int = 1,
    downscale: float = 2.0,
) -> Dict[str, object]:
    """
    Флипает видео горизонтально, если в указанной области не найден всплеск яркости
    (условный водяной знак). Возвращает словарь с путём результата и флагом.
    """

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    has_mark = scan_region_for_flash(
        video_path,
        region,
        frames=frames,
        brightness_threshold=brightness_threshold,
        coverage_ratio=coverage_ratio,
        method=method,
        edge_ratio_threshold=edge_ratio_threshold,
        min_hits=min_hits,
        downscale=downscale,
    )
    if has_mark:
        return {"flipped": False, "output": str(video_path), "reason": "watermark_detected"}

    target = Path(output_path) if output_path else video_path.with_name(f"{video_path.stem}_flipped{video_path.suffix}")
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "warning",
        "-i",
        str(video_path),
        "-vf",
        "hflip",
        str(target),
    ]
    result = subprocess.run(cmd, capture_output=True)
    return {
        "flipped": result.returncode == 0,
        "output": str(target),
        "rc": int(result.returncode),
        "stderr": (result.stderr or b"").decode(errors="ignore"),
    }


def flip_video_with_check(
    video_path: Union[str, Path],
    *,
    region: Tuple[int, int, int, int],
    output_path: Optional[Union[str, Path]] = None,
    frames: int = 120,
    brightness_threshold: int = 245,
    coverage_ratio: float = 0.02,
    flip_when: str = "missing",
    flip_direction: str = "left",
    method: str = "hybrid",
    edge_ratio_threshold: float = 0.006,
    min_hits: int = 1,
    downscale: float = 2.0,
) -> Dict[str, object]:
    """Горизонтально/вертикально отражает видео в зависимости от наличия вспышки.

    flip_when:
        - "missing" — флипнуть, если в зоне нет яркой вспышки (водяной знак не найден)
        - "present" — флипнуть, если яркая вспышка обнаружена
    flip_direction:
        - "left"  — горизонтальное отражение (hflip)
        - "right" — вертикальное отражение (vflip) для сохранения управляемости
    """

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    detected = scan_region_for_flash(
        video_path,
        region,
        frames=frames,
        brightness_threshold=brightness_threshold,
        coverage_ratio=coverage_ratio,
        method=method,
        edge_ratio_threshold=edge_ratio_threshold,
        min_hits=min_hits,
        downscale=downscale,
    )
    should_flip = (flip_when == "present" and detected) or (flip_when != "present" and not detected)

    target = Path(output_path) if output_path else video_path.with_name(f"{video_path.stem}_flipped{video_path.suffix}")

    if not should_flip:
        return {
            "flipped": False,
            "output": str(video_path),
            "reason": "skip_condition",
            "detected": detected,
        }

    filter_name = "vflip" if flip_direction == "right" else "hflip"
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "warning",
        "-i",
        str(video_path),
        "-vf",
        filter_name,
        str(target),
    ]
    result = subprocess.run(cmd, capture_output=True)
    return {
        "flipped": result.returncode == 0,
        "output": str(target),
        "rc": int(result.returncode),
        "stderr": (result.stderr or b"").decode(errors="ignore"),
        "detected": detected,
        "filter": filter_name,
    }


__all__ = [
    "prepare_template",
    "detect_watermark",
    "TemplateAssets",
    "WaterMarkDetector",
    "scan_region_for_flash",
    "flip_video_if_no_watermark",
    "flip_video_with_check",
]
