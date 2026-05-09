"""PDF 추출 공통 헬퍼.

pdfplumber 기반. 텍스트 추출 가능한 PDF 만 지원 (이미지/스캔 PDF 는 OCR 필요
— deferred). 비밀번호 PDF 는 raise → tools 가 ToolError 로 변환.
"""

from __future__ import annotations

from dataclasses import dataclass

import pdfplumber


@dataclass
class PDFDetectResult:
    detected_issuer: str | None
    confidence: float
    first_page_excerpt: str


def extract_first_page_text(pdf_path: str) -> str:
    """첫 페이지의 추출 가능한 텍스트 전체. 빈 문자열일 수 있음 (이미지 PDF)."""
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return ""
        text = pdf.pages[0].extract_text() or ""
        return text


def extract_all_tables(pdf_path: str) -> list[list[list[str]]]:
    """모든 페이지의 테이블을 [page][row][col] 형태로.

    카드사 명세서는 보통 테이블이 페이지마다 이어진다. 호출자가 row level 에서
    헤더/footer/empty row 정리.
    """
    out: list[list[list[str]]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if table:
                    out.append(table)
    return out


def extract_all_text_lines(pdf_path: str) -> list[str]:
    """모든 페이지의 텍스트를 줄 단위로 합친 리스트. 테이블 추출 실패 시 fallback."""
    out: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            out.extend(line for line in text.splitlines() if line.strip())
    return out
