from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

MAX_PAGES = 30
MAX_CHARS = 60_000
MIN_TEXT_CHARS = 20


class PdfTextExtractor:
    """text layer of the first pages only; image-only or unreadable pdfs yield none (no ocr in v1)."""

    def extract(self, data: bytes) -> str | None:
        try:
            reader = PdfReader(BytesIO(data), strict=False)
            parts: list[str] = []
            total = 0
            for page in reader.pages[:MAX_PAGES]:
                text = page.extract_text() or ""
                parts.append(text)
                total += len(text)
                if total >= MAX_CHARS:
                    break
        except (PyPdfError, ValueError, KeyError) as error:
            raise PdfUnreadableError(str(error)) from error
        joined = "\n".join(part.strip() for part in parts if part.strip())[:MAX_CHARS]
        return joined if len(joined) >= MIN_TEXT_CHARS else None


class PdfUnreadableError(ValueError):
    pass
