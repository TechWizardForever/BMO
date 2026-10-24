"""Bounded local image-assistance tools for Bemo vision roles.

These tools deliberately separate pixel extraction/OCR from model inference. They
never infer what text *means*; they only crop/enlarge/preprocess pixels and ask
Tesseract for bounded OCR with coordinates/confidence when available.
"""
from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VisionToolError(ValueError):
    pass


def _safe_name(name: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or fallback).name)
    return value or fallback


@dataclass
class VisionToolkit:
    output_root: Path
    max_file_bytes: int = 100 * 1024 * 1024
    max_pixels: int = 80_000_000

    def __post_init__(self):
        self.output_root = Path(self.output_root).expanduser().resolve()
        (self.output_root / "crops").mkdir(parents=True, exist_ok=True)
        (self.output_root / "ocr").mkdir(parents=True, exist_ok=True)

    def _image_path(self, raw: str) -> Path:
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise VisionToolError(f"Image not found: {path}")
        if path.stat().st_size > self.max_file_bytes:
            raise VisionToolError("Image exceeds configured vision-tool file limit")
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            raise VisionToolError("Unsupported image type")
        return path

    @staticmethod
    def _region(image, region: dict[str, Any] | None):
        if not region:
            return image, (0, 0, image.width, image.height)
        x = max(0, int(region.get("x", 0)))
        y = max(0, int(region.get("y", 0)))
        width = int(region.get("width", image.width - x))
        height = int(region.get("height", image.height - y))
        if width <= 0 or height <= 0:
            raise VisionToolError("Crop width/height must be positive")
        x2 = min(image.width, x + width)
        y2 = min(image.height, y + height)
        if x >= x2 or y >= y2:
            raise VisionToolError("Crop region is outside the image")
        return image.crop((x, y, x2, y2)), (x, y, x2, y2)

    def crop_zoom(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        path = self._image_path(str(arguments.get("path", "")))
        with Image.open(path) as source:
            source.load()
            if source.width * source.height > self.max_pixels:
                raise VisionToolError("Image exceeds configured pixel limit")
            image, box = self._region(source, dict(arguments.get("region") or {}))
            scale = float(arguments.get("scale", 2.0))
            if not math.isfinite(scale) or scale < 1.0 or scale > 8.0:
                raise VisionToolError("scale must be between 1 and 8")
            if scale != 1.0:
                image = image.resize(
                    (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            preprocess = str(arguments.get("preprocess", "none")).lower()
            if preprocess in {"grayscale", "contrast", "threshold", "sharpen"}:
                image = ImageOps.grayscale(image)
            if preprocess == "contrast":
                image = ImageEnhance.Contrast(image).enhance(float(arguments.get("contrast", 1.8)))
            elif preprocess == "threshold":
                threshold = max(0, min(255, int(arguments.get("threshold", 170))))
                image = image.point(lambda p: 255 if p >= threshold else 0)
            elif preprocess == "sharpen":
                image = image.filter(ImageFilter.SHARPEN)
                image = ImageEnhance.Contrast(image).enhance(1.35)
            elif preprocess not in {"none", "grayscale"}:
                raise VisionToolError("preprocess must be none/grayscale/contrast/threshold/sharpen")
            if bool(arguments.get("invert", False)):
                if image.mode not in {"L", "RGB"}:
                    image = image.convert("RGB")
                image = ImageOps.invert(image)

            requested = _safe_name(str(arguments.get("filename", "")), f"crop_{path.stem}.png")
            if not requested.lower().endswith(".png"):
                requested += ".png"
            out = self.output_root / "crops" / requested
            counter = 2
            while out.exists():
                out = self.output_root / "crops" / f"{Path(requested).stem}-{counter}.png"
                counter += 1
            image.save(out, format="PNG")
            return {
                "path": str(out),
                "source_path": str(path),
                "source_region_xyxy": list(box),
                "output_size_px": [image.width, image.height],
                "scale": scale,
                "preprocess": preprocess,
                "note": "This is a pixel crop/enlargement only; it does not establish the identity or meaning of visible features.",
            }

    def ocr(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        if shutil.which("tesseract") is None:
            raise VisionToolError("Tesseract executable is not installed. Run install.sh or install tesseract-ocr.")
        import pytesseract
        from pytesseract import Output

        path = self._image_path(str(arguments.get("path", "")))
        with Image.open(path) as source:
            source.load()
            image, box = self._region(source, dict(arguments.get("region") or {}))
            scale = float(arguments.get("scale", 2.0))
            if scale < 1.0 or scale > 8.0:
                raise VisionToolError("scale must be between 1 and 8")
            if scale != 1.0:
                image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
            preprocess = str(arguments.get("preprocess", "grayscale")).lower()
            if preprocess != "none":
                image = ImageOps.grayscale(image)
                if preprocess == "contrast":
                    image = ImageEnhance.Contrast(image).enhance(float(arguments.get("contrast", 1.8)))
                elif preprocess == "threshold":
                    threshold = max(0, min(255, int(arguments.get("threshold", 170))))
                    image = image.point(lambda p: 255 if p >= threshold else 0)
                elif preprocess == "sharpen":
                    image = image.filter(ImageFilter.SHARPEN)
                    image = ImageEnhance.Contrast(image).enhance(1.35)
                elif preprocess != "grayscale":
                    raise VisionToolError("preprocess must be none/grayscale/contrast/threshold/sharpen")
            if bool(arguments.get("invert", False)):
                if image.mode not in {"L", "RGB"}:
                    image = image.convert("RGB")
                image = ImageOps.invert(image)

            psm = max(3, min(13, int(arguments.get("psm", 6))))
            language = str(arguments.get("language", "eng"))
            config = f"--psm {psm}"
            text = pytesseract.image_to_string(image, lang=language, config=config).strip()
            data = pytesseract.image_to_data(image, lang=language, config=config, output_type=Output.DICT)
            min_conf = float(arguments.get("min_confidence", 35.0))
            words = []
            for i, token in enumerate(data.get("text", [])):
                token = str(token).strip()
                try:
                    conf = float(data["conf"][i])
                except Exception:
                    conf = -1.0
                if not token or conf < min_conf:
                    continue
                left = int(data["left"][i]); top = int(data["top"][i])
                width = int(data["width"][i]); height = int(data["height"][i])
                # Report coordinates in original source-image coordinates.
                source_left = box[0] + left / scale
                source_top = box[1] + top / scale
                words.append({
                    "text": token,
                    "confidence": conf,
                    "bbox_source_xywh": [round(source_left, 2), round(source_top, 2), round(width / scale, 2), round(height / scale, 2)],
                })
                if len(words) >= 500:
                    break
            return {
                "path": str(path),
                "source_region_xyxy": list(box),
                "text": text[:20_000],
                "words": words,
                "ocr_engine": "tesseract",
                "psm": psm,
                "scale": scale,
                "warning": "OCR output is evidence to cross-check against pixels, not ground truth. Low-confidence or contradictory text must be treated as uncertain.",
            }

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "crop_zoom_image":
                result = self.crop_zoom(arguments)
            elif name == "ocr_image":
                result = self.ocr(arguments)
            else:
                raise VisionToolError(f"Unknown vision tool: {name}")
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as exc:
            return f"Tool error: {type(exc).__name__}: {exc}"


VISION_TOOL_NAMES = {"crop_zoom_image", "ocr_image"}


def vision_tool_schemas() -> list[dict[str, Any]]:
    region = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "minimum": 0},
            "y": {"type": "integer", "minimum": 0},
            "width": {"type": "integer", "minimum": 1},
            "height": {"type": "integer", "minimum": 1},
        },
    }
    return [
        {
            "type": "function",
            "function": {
                "name": "crop_zoom_image",
                "description": "Create a deterministic crop/enlargement of the current selected image (or an explicit local path) when small technical details are hard to see. The returned crop is automatically attached to the next vision-model tool round. Use this instead of guessing unreadable pixels.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"}, "region": region,
                        "scale": {"type": "number", "minimum": 1, "maximum": 8},
                        "preprocess": {"type": "string", "enum": ["none", "grayscale", "contrast", "threshold", "sharpen"]},
                        "invert": {"type": "boolean"}, "filename": {"type": "string"},
                        "contrast": {"type": "number"}, "threshold": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ocr_image",
                "description": "Run local Tesseract OCR on the current selected image (or an explicit local path) or bounded crop and return text plus word confidence/coordinates. Use for dense labels, legends, diagnostic screens, or tiny text; cross-check OCR with pixels and never treat uncertain OCR as ground truth.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"}, "region": region,
                        "scale": {"type": "number", "minimum": 1, "maximum": 8},
                        "preprocess": {"type": "string", "enum": ["none", "grayscale", "contrast", "threshold", "sharpen"]},
                        "invert": {"type": "boolean"}, "language": {"type": "string"},
                        "psm": {"type": "integer", "minimum": 3, "maximum": 13},
                        "min_confidence": {"type": "number", "minimum": -1, "maximum": 100},
                        "contrast": {"type": "number"}, "threshold": {"type": "integer"},
                    },
                },
            },
        },
    ]
