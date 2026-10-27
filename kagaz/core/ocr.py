"""Read the text out of a photo or screenshot of a notice.

Most scams reach people as SMS or WhatsApp screenshots, so the Reader step
accepts images too. Tesseract OCR runs locally (English + Hindi), with no
network call. If it finds too little text, ASI:One gets one try at reading
the image. Either way the result is plain text that goes through the normal
Reader -> Verify -> Rules -> Action flow.
"""

import base64
import io
import logging
import re

from kagaz import llm

log = logging.getLogger("kagaz.ocr")

MIN_USEFUL_CHARS = 25
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000  # refuse "decompression bomb" images
IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif",
               "image/bmp", "image/tiff", "image/heic"}


def _languages() -> str:
    try:
        import pytesseract

        have = set(pytesseract.get_languages(config=""))
    except Exception:
        return "eng"
    return "+".join(lang for lang in ("eng", "hin") if lang in have) or "eng"


def _prepare(img):
    """Grayscale and upscale small screenshots; OCR is much better on big text."""
    from PIL import ImageOps

    img = ImageOps.exif_transpose(img).convert("L")
    if img.width < 1200:
        scale = 1200 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    # Dark-mode screenshots (light text on dark) read better inverted.
    hist = img.histogram()
    dark = sum(hist[:96])
    if dark > sum(hist) * 0.5:
        img = ImageOps.invert(img)
    return img


def clean(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def tesseract_text(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_IMAGE_PIXELS:
            raise ValueError("image too large")
        img = _prepare(img)
        return clean(pytesseract.image_to_string(img, lang=_languages()))
    except Exception as err:  # tesseract missing, unreadable image
        log.warning("OCR failed: %s", err)
        return ""


def llm_text(data: bytes, mime_type: str) -> str:
    b64 = base64.b64encode(data).decode()
    text = llm.complete_vision(
        "Transcribe all text in this image exactly as written (it is a notice, "
        "SMS or letter). Output only the text, no commentary.",
        f"data:{mime_type};base64,{b64}",
    )
    return clean(text or "")


def image_to_text(data: bytes, mime_type: str = "image/png") -> tuple[str, str]:
    """Return (text, method). method is 'ocr', 'asi1' or '' when nothing was read."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        return "", ""
    text = tesseract_text(data)
    if len(text) >= MIN_USEFUL_CHARS:
        return text, "ocr"
    fallback = llm_text(data, mime_type)
    if len(fallback) > len(text):
        return fallback, "asi1"
    return text, "ocr" if text else ""
