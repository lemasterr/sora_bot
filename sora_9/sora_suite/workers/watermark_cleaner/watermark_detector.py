"""Watermark detection utilities used by the restoration worker."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2  # type: ignore[import]
import numpy as np


@dataclass
class TemplatePackage:
    template: np.ndarray
    mask: Optional[np.ndarray]
    grayscale: np.ndarray
    mask_for_gray: Optional[np.ndarray]
    original_shape: Sequence[int]
    path: str


def _ensure_grayscale(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _build_mask(image: np.ndarray, mask_threshold: int) -> Optional[np.ndarray]:
    if image.ndim == 2:
        return None
    if image.shape[2] == 4:
        alpha = image[:, :, 3]
        _, mask = cv2.threshold(alpha, mask_threshold, 255, cv2.THRESH_BINARY)
        return mask
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, mask_threshold, 255, cv2.THRESH_BINARY)
    unique = np.count_nonzero(mask)
    return mask if unique > 0 else None


def prepare_template(
    template_image: np.ndarray,
    template_path: Path,
    *,
    mask_threshold: int = 8,
) -> TemplatePackage:
    """Normalise template data and compute masks for detection."""
    if template_image is None or template_image.size == 0:
        raise ValueError("Template image is empty")
    template = template_image.copy()
    if template.ndim == 2:
        template_bgr = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)
    elif template.shape[2] == 4:
        template_bgr = cv2.cvtColor(template, cv2.COLOR_BGRA2BGR)
    else:
        template_bgr = template
    mask = _build_mask(template, mask_threshold)
    gray = _ensure_grayscale(template_bgr)
    mask_gray = mask.copy() if mask is not None else None
    return TemplatePackage(
        template=template_bgr,
        mask=mask,
        grayscale=gray,
        mask_for_gray=mask_gray,
        original_shape=template_bgr.shape[:2],
        path=str(template_path),
    )


def _iterate_frames(cap: cv2.VideoCapture, total_frames: int, target_frames: int) -> Iterable[int]:
    if target_frames <= 0 or total_frames <= target_frames:
        return range(total_frames)
    step = max(1, total_frames // target_frames)
    indices = list(range(0, total_frames, step))
    if indices[-1] != total_frames - 1:
        indices.append(total_frames - 1)
    return indices


def _resize_frame(frame: np.ndarray, downscale: int) -> Tuple[np.ndarray, float]:
    if downscale <= 0:
        return frame, 1.0
    height, width = frame.shape[:2]
    if height <= downscale:
        return frame, 1.0
    scale = downscale / float(height)
    resized = cv2.resize(frame, (int(width * scale), downscale), interpolation=cv2.INTER_AREA)
    return resized, scale


def _generate_scales(scale_min: float, scale_max: float, scale_steps: int) -> List[float]:
    if scale_steps <= 1 or abs(scale_max - scale_min) < 1e-6:
        return [max(scale_min, 1e-3)]
    if scale_max < scale_min:
        scale_min, scale_max = scale_max, scale_min
    return [float(x) for x in np.linspace(scale_min, scale_max, scale_steps)]


def _match_template(
    frame_gray: np.ndarray,
    tpl_gray: np.ndarray,
    *,
    mask: Optional[np.ndarray],
) -> tuple[float, tuple[int, int]]:
    result = cv2.matchTemplate(frame_gray, tpl_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return float(max_val), (int(max_loc[0]), int(max_loc[1]))


def detect_watermark(  # noqa: PLR0913
    video_path: str,
    template_path: str,
    *,
    template_package: Optional[TemplatePackage] = None,
    threshold: float = 0.78,
    frames: int = 120,
    downscale: int = 1080,
    scale_min: float = 0.85,
    scale_max: float = 1.2,
    scale_steps: int = 9,
    mask_threshold: int = 8,
    return_details: bool = False,
    return_series: bool = False,
) -> Dict[str, object]:
    """Detect watermark placements within a video using template matching."""
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(video_path)

    if template_package is None:
        template_image = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
        if template_image is None:
            raise FileNotFoundError(template_path)
        template_package = prepare_template(template_image, Path(template_path), mask_threshold=mask_threshold)

    tpl_gray = template_package.grayscale
    tpl_mask = template_package.mask_for_gray

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео {video_path}")
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frame_indices = list(_iterate_frames(cap, total_frames, frames if frames > 0 else total_frames))
    scale_values = _generate_scales(scale_min, scale_max, max(scale_steps, 1))

    details: List[Dict[str, object]] = []
    series: List[Dict[str, object]] = []

    for index in frame_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        resized_frame, frame_scale = _resize_frame(frame, downscale)
        frame_gray = _ensure_grayscale(resized_frame)
        best_score = -1.0
        best_location: Optional[tuple[int, int]] = None
        best_scale = 1.0
        best_size = template_package.original_shape

        for scale in scale_values:
            scaled_tpl = cv2.resize(tpl_gray, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if scaled_tpl.shape[0] == 0 or scaled_tpl.shape[1] == 0:
                continue
            if scaled_tpl.shape[0] > frame_gray.shape[0] or scaled_tpl.shape[1] > frame_gray.shape[1]:
                continue
            scaled_mask = None
            if tpl_mask is not None:
                scaled_mask = cv2.resize(tpl_mask, (scaled_tpl.shape[1], scaled_tpl.shape[0]), interpolation=cv2.INTER_NEAREST)
            score, loc = _match_template(frame_gray, scaled_tpl, mask=scaled_mask)
            if score > best_score:
                best_score = score
                best_location = loc
                best_scale = scale
                best_size = scaled_tpl.shape[:2]
        if best_location is None:
            continue
        accepted = best_score >= threshold
        width = int(best_size[1] / frame_scale)
        height = int(best_size[0] / frame_scale)
        left = int(best_location[0] / frame_scale)
        top = int(best_location[1] / frame_scale)
        record = {
            "frame": index,
            "bbox": [left, top, width, height],
            "score": best_score,
            "scale": best_scale,
            "accepted": accepted,
        }
        if return_details:
            details.append(record)
        if accepted and return_series:
            series.append(record)

    cap.release()

    return {
        "series": series if return_series else [],
        "details": details if return_details else [],
        "template": template_package.path,
        "frames_scanned": len(frame_indices),
    }


class WaterMarkDetector:
    """Backward-compatible wrapper used by legacy UI helpers.

    The original UI relied on an object with ``scan`` helpers and
    ``get_zone_masks`` utilities.  The functional API above supersedes it, but
    the application still instantiates :class:`WaterMarkDetector`.  This class
    keeps that surface while delegating the actual work to ``detect_watermark``.
    """

    def __init__(
        self,
        template_path: str,
        *,
        mask_threshold: int = 8,
        template_image: Optional[np.ndarray] = None,
        **default_cfg: object,
    ) -> None:
        self.template_path = str(template_path)
        self.mask_threshold = int(mask_threshold)
        self._default_cfg: Dict[str, object] = dict(default_cfg)
        self._template_package = None
        self._last_result: Dict[str, object] = {}

        image = template_image
        if image is None and Path(self.template_path).exists():
            image = cv2.imread(self.template_path, cv2.IMREAD_UNCHANGED)
        if image is not None and getattr(image, "size", 0):
            self._template_package = prepare_template(
                image,
                Path(self.template_path),
                mask_threshold=self.mask_threshold,
            )

    def _ensure_template(self) -> TemplatePackage:
        if self._template_package is not None:
            return self._template_package
        image = cv2.imread(self.template_path, cv2.IMREAD_UNCHANGED)
        if image is None or image.size == 0:
            raise FileNotFoundError(self.template_path)
        self._template_package = prepare_template(
            image,
            Path(self.template_path),
            mask_threshold=self.mask_threshold,
        )
        return self._template_package

    def detect(self, video_path: str, **overrides: object) -> Dict[str, object]:
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

    def scan(self, video_path: str, **overrides: object) -> Dict[str, object]:
        """Alias used by older code paths."""

        return self.detect(video_path, **overrides)

    def last_series(self) -> List[Dict[str, object]]:
        series = self._last_result.get("series") if isinstance(self._last_result, dict) else []
        return list(series or [])

    def _get_zone_masks(
        self,
        frame_size: Optional[Tuple[int, int]] = None,
        *,
        series: Optional[Sequence[Dict[str, object]]] = None,
    ) -> List[np.ndarray]:
        records = series if series is not None else self.last_series()
        if not records:
            return []
        width: Optional[int]
        height: Optional[int]
        if frame_size is not None:
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
        series: Optional[Sequence[Dict[str, object]]] = None,
    ) -> List[np.ndarray]:
        return self._get_zone_masks(frame_size, series=series)


__all__ = ["prepare_template", "detect_watermark", "TemplatePackage", "WaterMarkDetector"]
