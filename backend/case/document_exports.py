"""Three exports from one plain-text block model; no interpretation of user HTML/Markdown."""

from io import BytesIO
import hashlib
import html
import os
from pathlib import Path
import re
from threading import RLock
from typing import Literal

from pydantic import Field
from .models import ContractModel

EXPORT_VERSION = "complaint-exports-0.1.0"
_FONT_LOCK = RLock()


class DraftBlock(ContractModel):
    kind: Literal["title", "heading", "paragraph", "bullet"]
    text: str = Field(max_length=20000)


def validate_blocks(blocks):
    if not blocks or len(blocks) > 1000 or sum(len(item.text) for item in blocks) > 200000:
        raise ValueError("document content exceeds configured limits")
    for block in blocks:
        for char in block.text:
            code = ord(char)
            if (code < 32 and char not in "\n\r\t") or 0xD800 <= code <= 0xDFFF or code in (0xFFFE, 0xFFFF):
                raise ValueError("document text contains unsupported control characters")


def markdown(blocks):
    validate_blocks(blocks)
    lines = []
    for block in blocks:
        text = html.escape(block.text, quote=False)
        text = re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "  \n")
        prefix = {"title": "# ", "heading": "## ", "paragraph": "", "bullet": "- "}[block.kind]
        lines.append(prefix + text)
    return ("\n\n".join(lines) + "\n").encode("utf-8")


def word(blocks):
    validate_blocks(blocks)
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.2)
    section.left_margin = section.right_margin = Cm(2.3)
    section.header_distance = section.footer_distance = Cm(1)
    for name, size in (("Normal", 11), ("Title", 17), ("Heading 1", 13)):
        style = document.styles[name]
        style.font.name, style.font.size, style.font.color.rgb = "Times New Roman", Pt(size), RGBColor(0, 0, 0)
        fonts=style.element.get_or_add_rPr().get_or_add_rFonts()
        fonts.set(qn("w:eastAsia"), "SimSun")
        for attribute in list(fonts.attrib):
            if "theme" in attribute.lower():
                del fonts.attrib[attribute]
        for border in style.element.xpath("./w:pPr/w:pBdr"):
            border.getparent().remove(border)
        style.paragraph_format.space_after = Pt(7)
        style.paragraph_format.line_spacing = 1.35
        if name != "Normal":
            style.paragraph_format.keep_with_next = True
    document.styles["Heading 1"].paragraph_format.space_before = Pt(10)
    for block in blocks:
        paragraph = document.add_paragraph(style={"title": "Title", "heading": "Heading 1"}.get(block.kind, "Normal"))
        paragraph.add_run(("• " if block.kind == "bullet" else "") + block.text)
        paragraph.paragraph_format.widow_control = True
        if block.kind == "title":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("填写草稿  第 ")
    field = OxmlElement("w:fldSimple"); field.set(qn("w:instr"), "PAGE")
    footer._p.append(field); footer.add_run(" 页")
    for run in footer.runs:
        run.font.size = Pt(9)
        run.font.name = "SimSun"
        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "SimSun")
    document.core_properties.author = "CivRA"
    document.core_properties.title = blocks[0].text
    document.core_properties.subject = "待用户复核的民事起诉状填写草稿"
    document.core_properties.comments = "Generated from the same content blocks as Markdown and PDF."
    buffer = BytesIO(); document.save(buffer)
    return buffer.getvalue()


def _pdf_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    configured = os.environ.get("CIVRA_PDF_FONT_PATH")
    candidates = [Path(configured)] if configured else [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/simsun.ttc",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/simhei.ttf",
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
    ]
    path = next((path for path in candidates if path.is_file()), None)
    if path is None:
        raise ValueError("PDF export needs an embeddable Chinese TrueType font; set CIVRA_PDF_FONT_PATH")
    key = "CivRA_" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
    if key not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(key, str(path), subfontIndex=0))
    return key, pdfmetrics.getFont(key)


def pdf(blocks):
    validate_blocks(blocks)
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    # ReportLab's font registry and subset state are process-global.
    with _FONT_LOCK:
        name, font = _pdf_font()
        text = "".join(block.text for block in blocks) + "填写草稿第页• 0123456789"
        missing = sorted({ord(char) for char in text if not char.isspace() and ord(char) not in font.face.charWidths})
        if missing:
            raise ValueError("PDF font lacks characters: " + ", ".join(f"U+{code:04X}" for code in missing[:12]))
        styles = {
            "title": ParagraphStyle("title", fontName=name, fontSize=17, leading=24, alignment=TA_CENTER, spaceAfter=14, keepWithNext=True),
            "heading": ParagraphStyle("heading", fontName=name, fontSize=13, leading=19, spaceBefore=10, spaceAfter=7, keepWithNext=True),
            "paragraph": ParagraphStyle("paragraph", fontName=name, fontSize=11, leading=16, spaceAfter=7, wordWrap="CJK", splitLongWords=True),
            "bullet": ParagraphStyle("bullet", fontName=name, fontSize=11, leading=16, spaceAfter=7, wordWrap="CJK", splitLongWords=True),
        }
        flowables = []
        for block in blocks:
            safe = html.escape(block.text, quote=True).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>").replace("\t", "    ")
            flowables.append(Paragraph(("• " if block.kind == "bullet" else "") + safe, styles[block.kind]))
        buffer = BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2.3*cm, rightMargin=2.3*cm,
            topMargin=2.2*cm, bottomMargin=2.2*cm, title=blocks[0].text, author="CivRA")
        def footer(canvas, doc):
            canvas.saveState(); canvas.setFont(name, 9)
            canvas.drawCentredString(A4[0]/2, 1.1*cm, f"填写草稿  第 {doc.page} 页")
            canvas.restoreState()
        document.build(flowables, onFirstPage=footer, onLaterPages=footer)
        return buffer.getvalue()


BUILTIN_EXPORTERS = {"md": markdown, "docx": word, "pdf": pdf}
