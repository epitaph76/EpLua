from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.pdfgen import canvas


PAGE_SIZE = (13.333 * inch, 7.5 * inch)
ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "luamts_presentation.md"
OUTPUT = ROOT / "luamts_presentation.pdf"


def register_font(name: str, candidates: list[Path], fallback: str) -> str:
    for candidate in candidates:
        if candidate.exists():
            pdfmetrics.registerFont(TTFont(name, str(candidate)))
            return name
    return fallback


def parse_slides(source: Path) -> list[dict[str, object]]:
    slides: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            current = {"title": line[3:].strip(), "bullets": []}
            slides.append(current)
            continue
        if current is None:
            continue
        if line.startswith("- "):
            casted = current["bullets"]
            assert isinstance(casted, list)
            casted.append(line[2:].strip())
            continue

    return slides


def inline_markup(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(
        r"`([^`]+)`",
        r'<font face="Courier">\1</font>',
        escaped,
    )
    return escaped


def draw_paragraph(
    pdf: canvas.Canvas,
    text: str,
    style: ParagraphStyle,
    x: float,
    y: float,
    width: float,
    *,
    bullet_text: str | None = None,
) -> float:
    paragraph = Paragraph(inline_markup(text), style=style, bulletText=bullet_text)
    _, height = paragraph.wrap(width, 1000)
    paragraph.drawOn(pdf, x, y - height)
    return height


def build_pdf(slides: list[dict[str, object]], output: Path) -> None:
    width, height = PAGE_SIZE
    pdf = canvas.Canvas(str(output), pagesize=PAGE_SIZE)

    regular_font = register_font(
        "PresentationRegular",
        [
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
            Path("C:/Windows/Fonts/calibri.ttf"),
        ],
        "Helvetica",
    )
    bold_font = register_font(
        "PresentationBold",
        [
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/segoeuib.ttf"),
            Path("C:/Windows/Fonts/calibrib.ttf"),
        ],
        "Helvetica-Bold",
    )

    title_style = ParagraphStyle(
        "title",
        fontName=bold_font,
        fontSize=24,
        leading=28,
        textColor=HexColor("#0B132B"),
    )
    bullet_style = ParagraphStyle(
        "bullet",
        fontName=regular_font,
        fontSize=14,
        leading=18,
        textColor=HexColor("#122033"),
        leftIndent=20,
        firstLineIndent=0,
    )
    footer_style = ParagraphStyle(
        "footer",
        fontName=regular_font,
        fontSize=9,
        leading=11,
        textColor=HexColor("#546274"),
    )

    for index, slide in enumerate(slides, start=1):
        pdf.setFillColor(HexColor("#F5F7FB"))
        pdf.rect(0, 0, width, height, fill=1, stroke=0)

        pdf.setFillColor(HexColor("#0B132B"))
        pdf.rect(0, height - 44, width, 44, fill=1, stroke=0)

        pdf.setFillColor(HexColor("#2EC4B6"))
        pdf.rect(36, height - 64, 96, 6, fill=1, stroke=0)

        pdf.setFont(bold_font, 13)
        pdf.setFillColor(HexColor("#FFFFFF"))
        pdf.drawString(36, height - 28, "luaMTS")

        pdf.setFont(regular_font, 10)
        pdf.drawRightString(width - 36, height - 27, f"Slide {index}/{len(slides)}")

        y = height - 88
        title = str(slide["title"])
        y -= draw_paragraph(pdf, title, title_style, 36, y, width - 72)
        y -= 12

        pdf.setFillColor(HexColor("#DCE3EE"))
        pdf.roundRect(36, 54, width - 72, height - 168, 12, fill=0, stroke=1)

        bullets = slide["bullets"]
        assert isinstance(bullets, list)
        for bullet in bullets:
            y -= draw_paragraph(
                pdf,
                str(bullet),
                bullet_style,
                54,
                y,
                width - 108,
                bullet_text="•",
            )
            y -= 8

        draw_paragraph(
            pdf,
            "Local agentic Lua generation for LowCode: quality, iteration, privacy, reproducibility.",
            footer_style,
            36,
            34,
            width - 72,
        )

        pdf.showPage()

    pdf.save()


def main() -> None:
    slides = parse_slides(SOURCE)
    if not slides:
        raise SystemExit("No slides found in source markdown.")
    build_pdf(slides, OUTPUT)
    print(f"Rendered {len(slides)} slides -> {OUTPUT}")


if __name__ == "__main__":
    main()
