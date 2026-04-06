from __future__ import annotations

import base64
import io
import re
import zipfile

from app.schemas.document import TemplateImportRequest


def _safe_decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "utf-16le", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def _collapse_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_docx_text(raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            xml = zf.read("word/document.xml")
    except Exception:
        return ""

    text = _safe_decode_text(xml)
    text = re.sub(r"<w:tab[^>]*/>", "\t", text)
    text = re.sub(r"</w:p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return _collapse_text(text)


def _extract_doc_text(raw: bytes) -> str:
    candidates: list[str] = []
    for encoding in ("utf-16le", "utf-16", "gb18030", "utf-8", "latin-1"):
        try:
            candidates.append(raw.decode(encoding, errors="ignore"))
        except Exception:
            continue

    best_text = ""
    best_score = -1
    for decoded in candidates:
        chunks = re.split(r"[\x00-\x1f]+", decoded)
        lines: list[str] = []
        for chunk in chunks:
            line = chunk.strip()
            if not line:
                continue
            if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", line) is None:
                continue
            if len(line) == 1 and not re.search(r"[\u4e00-\u9fff]", line):
                continue
            lines.append(line)

        text = _collapse_text("\n".join(lines))
        score = len(re.findall(r"[\u4e00-\u9fff]", text)) * 3 + len(re.findall(r"[A-Za-z0-9]", text))
        if score > best_score:
            best_text = text
            best_score = score

    return best_text


def parse_template_import(payload: TemplateImportRequest) -> tuple[str, str]:
    name = (payload.name or "").strip()

    if payload.template_text and payload.template_text.strip():
        return name or "导入模板", payload.template_text.strip()

    if not payload.template_base64:
        raise ValueError("template_content_missing")

    try:
        raw = base64.b64decode(payload.template_base64)
    except Exception as exc:
        raise ValueError("template_base64_invalid") from exc

    file_name = (payload.file_name or "").lower()
    mime_type = (payload.mime_type or "").lower()

    if file_name.endswith(".docx") or "officedocument.wordprocessingml.document" in mime_type:
        extracted = _extract_docx_text(raw)
    elif file_name.endswith(".doc") or "msword" in mime_type:
        extracted = _extract_doc_text(raw)
    elif (
        file_name.endswith(".txt")
        or file_name.endswith(".md")
        or file_name.endswith(".json")
        or file_name.endswith(".xml")
        or "text/" in mime_type
    ):
        extracted = _collapse_text(_safe_decode_text(raw))
    else:
        extracted = _collapse_text(_safe_decode_text(raw))

    if not extracted:
        raise ValueError("template_parse_failed")

    return name or payload.file_name or "导入模板", extracted
