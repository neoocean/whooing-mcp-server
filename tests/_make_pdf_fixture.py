"""Generate synthetic PDF statement fixtures using reportlab.

Run once locally to create tests/fixtures/pdf/{shinhan,hyundai}_sample.pdf.
The generated PDFs are committed; this script is for re-generation only
(reportlab is a dev-only optional dep).

Usage:
    pip install -e .[dev]
    python tests/_make_pdf_fixture.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

OUT = Path(__file__).parent / "fixtures" / "pdf"

# 한글 폰트 — issuer 키워드(신한카드/현대카드) 가 PDF 텍스트로 추출 가능해야
# detect 가 동작. macOS 의 AppleGothic.ttf 우선, 그 외엔 다양한 Noto / DejaVu
# 시도. 모두 실패 시 영문 폰트로 fallback (이때 한글은 □ 으로 렌더되어 detect
# 는 실패할 수 있다 — fixture 재생성은 한글 폰트 있는 머신에서 하라고 안내).
FONT_NAME = "Helvetica"
KOREAN_FONT_CANDIDATES = [
    ("AppleGothic", "/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    ("AppleMyungjo", "/System/Library/Fonts/Supplemental/AppleMyungjo.ttf"),
    ("NanumGothic", "/Library/Fonts/NanumGothic.ttf"),
    ("NotoSansKR", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]

for candidate, path in KOREAN_FONT_CANDIDATES:
    try:
        pdfmetrics.registerFont(TTFont(candidate, path))
        FONT_NAME = candidate
        print(f"  using font: {candidate}")
        break
    except Exception:
        continue
else:
    print("  WARNING: 한글 폰트 등록 실패. issuer 키워드가 □ 으로 렌더될 수 있음.")


def _make_pdf(path: Path, title: str, transactions: list[tuple[str, str, str]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=20*mm)
    styles = getSampleStyleSheet()
    style = ParagraphStyle(
        "ko", parent=styles["Normal"], fontName=FONT_NAME, fontSize=11
    )
    title_style = ParagraphStyle(
        "ko_title", parent=styles["Heading1"], fontName=FONT_NAME, fontSize=14
    )

    story = [
        Paragraph(title, title_style),
        Spacer(1, 6*mm),
        Paragraph("이용내역 명세 (합성 fixture — 실 거래 X)", style),
        Spacer(1, 4*mm),
    ]

    data = [["이용일자", "가맹점명", "이용금액"]] + [list(t) for t in transactions]
    table = Table(data, colWidths=[40*mm, 80*mm, 35*mm])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), FONT_NAME, 10),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(table)

    doc.build(story)
    print(f"  wrote {path} ({path.stat().st_size} bytes)")


def main() -> None:
    print("Generating synthetic PDF fixtures...")
    _make_pdf(
        OUT / "shinhan_sample.pdf",
        "신한카드 이용내역",
        [
            ("2026-05-09", "스타벅스강남점", "6,200"),
            ("2026-05-08", "GS25합정", "3,500"),
            ("2026-05-07", "합성식당", "18,000"),
        ],
    )
    _make_pdf(
        OUT / "hyundai_sample.pdf",
        "현대카드 이용내역",
        [
            ("2026-05-09", "스타벅스강남점", "6,200"),
            ("2026-05-08", "합성마트", "25,000"),
        ],
    )
    print("done.")


if __name__ == "__main__":
    main()
