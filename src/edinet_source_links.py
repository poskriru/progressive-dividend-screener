"""EDINET書類のPDF直リンクを扱う共通処理。

WZEK0040.aspx?S100=... の旧形式URLは外部から開くと
「規定外操作」となるため、searchdocument/pdf 形式の
直リンクへ統一して表示・保存する。
"""

from typing import Any
from urllib.parse import parse_qs, urlsplit

EDINET_PDF_BASE_URL = (
    "https://disclosure2dl.edinet-fsa.go.jp/"
    "searchdocument/pdf"
)


def extract_edinet_document_id(value: Any) -> str | None:
    """保存済みURLからEDINET書類IDを取り出す。"""

    if not isinstance(value, str):
        return None

    try:
        parsed_url = urlsplit(value.strip())
    except ValueError:
        return None

    netloc = parsed_url.netloc.lower()
    document_id: str | None = None

    if (
        parsed_url.scheme == "https"
        and netloc == "disclosure2dl.edinet-fsa.go.jp"
        and parsed_url.path.startswith(
            "/searchdocument/pdf/"
        )
        and not parsed_url.fragment
        and not parsed_url.query
    ):
        filename = parsed_url.path[
            len("/searchdocument/pdf/") :
        ]

        if filename.endswith(".pdf"):
            document_id = filename[: -len(".pdf")]
    elif (
        parsed_url.scheme == "https"
        and netloc == "disclosure2.edinet-fsa.go.jp"
        and parsed_url.path == "/WZEK0040.aspx"
        and not parsed_url.fragment
    ):
        try:
            query = parse_qs(
                parsed_url.query,
                keep_blank_values=False,
            )
        except ValueError:
            return None

        if (
            set(query) == {"S100"}
            and len(query["S100"]) == 1
        ):
            document_id = query["S100"][0]

    if (
        not document_id
        or len(document_id) > 32
        or not document_id.isascii()
        or not document_id.isalnum()
    ):
        return None

    return document_id


def build_edinet_pdf_url(document_id: str) -> str:
    """書類IDからPDF直リンクURLを作る。"""

    return f"{EDINET_PDF_BASE_URL}/{document_id}.pdf"


def to_edinet_sheet_url(value: Any) -> str:
    """DB保存URLをシート表示用のPDF直リンクへ変換する。

    変換できない形式は元の文字列をそのまま返す。
    """

    document_id = extract_edinet_document_id(value)

    if document_id is None:
        return str(value) if value else ""

    return build_edinet_pdf_url(document_id)
