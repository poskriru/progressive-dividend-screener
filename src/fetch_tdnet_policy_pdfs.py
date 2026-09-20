"""
TDnetのPDFを安全に取得し、ページ単位で本文を抽出する。

取得先をTDnet公式ホストのHTTPS URLへ限定し、
Content-Type、ファイルサイズ、PDFシグネチャ、
ページ数を検証する。

画像PDFに対するOCRは実施しない。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from urllib.parse import urlparse


# ============================================================
# 外部ライブラリ
# ============================================================

import pdfplumber
import requests


# ============================================================
# 定数
# ============================================================

TDNET_PDF_HOST = "www.release.tdnet.info"
TDNET_PDF_PATH_PREFIX = "/inbs/"

REQUEST_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_SIZE = 64 * 1024
MAXIMUM_PDF_SIZE_BYTES = 20 * 1024 * 1024
MAXIMUM_PDF_PAGE_COUNT = 200

ALLOWED_PDF_CONTENT_TYPES = {
    "application/pdf",
    "application/octet-stream",
}

PDF_HEADER_SIGNATURE = b"%PDF-"

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; progressive-dividend-screener/0.5; "
    "+https://github.com/poskriru/"
    "progressive-dividend-screener)"
)


# ============================================================
# 例外
# ============================================================

class TdnetPdfFetchError(RuntimeError):
    """TDnet PDFの取得または取得内容検証に失敗した場合。"""


class TdnetPdfTextExtractionError(RuntimeError):
    """PDF本文をテキストとして抽出できない場合。"""


# ============================================================
# データモデル
# ============================================================

@dataclass(frozen=True)
class FetchedPdf:
    """取得済みPDFとHTTP・ファイル情報。"""

    content: bytes
    content_sha256: str
    content_type: str
    content_length_bytes: int
    http_status_code: int
    final_url: str


@dataclass(frozen=True)
class ExtractedPdfText:
    """PDFから抽出したページ単位の本文。"""

    page_texts: tuple[str, ...]
    page_count: int
    extracted_text_length: int


# ============================================================
# URL・HTTP設定
# ============================================================

def validate_tdnet_pdf_url(url: str) -> str:
    """TDnet公式のPDF URLだけを許可する。"""

    normalized_url = str(url or "").strip()

    if not normalized_url:
        raise TdnetPdfFetchError(
            "TDnet PDF URLが空です。"
        )

    try:
        parsed = urlparse(normalized_url)
        port = parsed.port
    except ValueError as error:
        raise TdnetPdfFetchError(
            "TDnet PDF URLのポート指定が不正です。"
        ) from error

    if parsed.scheme.lower() != "https":
        raise TdnetPdfFetchError(
            "TDnet PDF URLはHTTPSである必要があります。"
        )

    if (
        parsed.username is not None
        or parsed.password is not None
    ):
        raise TdnetPdfFetchError(
            "TDnet PDF URLに認証情報は指定できません。"
        )

    hostname = (
        parsed.hostname or ""
    ).lower()

    if hostname != TDNET_PDF_HOST:
        raise TdnetPdfFetchError(
            "TDnet公式ホスト以外のPDF URLは取得できません。"
            f"ホスト: {hostname or '(empty)'}"
        )

    if port not in {None, 443}:
        raise TdnetPdfFetchError(
            "TDnet PDF URLのポートは443だけを許可します。"
        )

    if not parsed.path.startswith(
        TDNET_PDF_PATH_PREFIX
    ):
        raise TdnetPdfFetchError(
            "TDnet PDF URLのパスが不正です。"
        )

    if not parsed.path.lower().endswith(".pdf"):
        raise TdnetPdfFetchError(
            "TDnet PDF URLが.pdfで終了していません。"
        )

    return normalized_url


def create_pdf_http_session() -> requests.Session:
    """TDnet PDF取得用のHTTPセッションを作成する。"""

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/pdf,"
                "application/octet-stream;q=0.8"
            ),
            "Accept-Language": (
                "ja,en-US;q=0.8,en;q=0.6"
            ),
        }
    )
    return session


def normalize_content_type(value: Any) -> str:
    """Content-Typeからメディアタイプ部分を取得する。"""

    return (
        str(value or "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )


def parse_content_length(value: Any) -> int | None:
    """Content-Lengthを非負整数として取得する。"""

    raw_value = str(value or "").strip()

    if not raw_value:
        return None

    try:
        content_length = int(raw_value)
    except ValueError as error:
        raise TdnetPdfFetchError(
            "TDnet PDFのContent-Lengthが整数ではありません。"
        ) from error

    if content_length < 0:
        raise TdnetPdfFetchError(
            "TDnet PDFのContent-Lengthが負数です。"
        )

    return content_length


def has_pdf_signature(content: bytes) -> bool:
    """
    PDFヘッダーが先頭1024バイト以内に存在するか確認する。

    PDF仕様ではファイル先頭付近に%PDF-ヘッダーが必要だが、
    一部ファイルの先頭に短い余分なデータが付く場合を考慮する。
    """

    return PDF_HEADER_SIGNATURE in content[:1024]


# ============================================================
# PDF取得
# ============================================================

def fetch_tdnet_pdf(
    session: requests.Session,
    url: str,
    *,
    maximum_size_bytes: int = MAXIMUM_PDF_SIZE_BYTES,
) -> FetchedPdf:
    """TDnet PDFをサイズ制限付きで取得する。"""

    validated_url = validate_tdnet_pdf_url(url)

    if maximum_size_bytes <= 0:
        raise ValueError(
            "maximum_size_bytesは1以上で指定してください。"
        )

    response = None

    try:
        try:
            response = session.get(
                validated_url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                stream=True,
                allow_redirects=True,
            )
        except requests.RequestException as error:
            raise TdnetPdfFetchError(
                "TDnet PDFのHTTP取得に失敗しました。"
                f"エラー種別: {type(error).__name__}"
            ) from error

        final_url = validate_tdnet_pdf_url(
            str(
                getattr(
                    response,
                    "url",
                    validated_url,
                )
                or validated_url
            )
        )
        status_code = int(
            getattr(
                response,
                "status_code",
                0,
            )
        )

        if status_code != 200:
            raise TdnetPdfFetchError(
                "TDnet PDFのHTTPステータスが"
                "200ではありません。"
                f"ステータス: {status_code}"
            )

        content_type = normalize_content_type(
            response.headers.get(
                "Content-Type",
                "",
            )
        )

        if content_type not in ALLOWED_PDF_CONTENT_TYPES:
            raise TdnetPdfFetchError(
                "TDnet PDFのContent-Typeが"
                "許可されていません。"
                f"Content-Type: {content_type or '(empty)'}"
            )

        declared_content_length = parse_content_length(
            response.headers.get(
                "Content-Length",
                "",
            )
        )

        if (
            declared_content_length is not None
            and declared_content_length
            > maximum_size_bytes
        ):
            raise TdnetPdfFetchError(
                "TDnet PDFのContent-Lengthが"
                "上限を超えています。"
                f"上限: {maximum_size_bytes:,}, "
                f"Content-Length: "
                f"{declared_content_length:,}"
            )

        content_buffer = bytearray()
        content_hash = hashlib.sha256()

        try:
            chunks = response.iter_content(
                chunk_size=DOWNLOAD_CHUNK_SIZE
            )

            for chunk in chunks:
                if not chunk:
                    continue

                content_buffer.extend(chunk)
                content_hash.update(chunk)

                if (
                    len(content_buffer)
                    > maximum_size_bytes
                ):
                    raise TdnetPdfFetchError(
                        "TDnet PDFの取得サイズが"
                        "上限を超えました。"
                        f"上限: {maximum_size_bytes:,}"
                    )
        except requests.RequestException as error:
            raise TdnetPdfFetchError(
                "TDnet PDFのダウンロード中に"
                "通信エラーが発生しました。"
                f"エラー種別: {type(error).__name__}"
            ) from error

        content = bytes(content_buffer)

        if not content:
            raise TdnetPdfFetchError(
                "取得したTDnet PDFが空です。"
            )

        if not has_pdf_signature(content):
            raise TdnetPdfFetchError(
                "取得内容にPDFヘッダーがありません。"
            )

        return FetchedPdf(
            content=content,
            content_sha256=content_hash.hexdigest(),
            content_type=content_type,
            content_length_bytes=len(content),
            http_status_code=status_code,
            final_url=final_url,
        )

    finally:
        if response is not None:
            close = getattr(
                response,
                "close",
                None,
            )

            if callable(close):
                close()


# ============================================================
# PDF本文抽出
# ============================================================

def extract_pdf_text(
    content: bytes,
    *,
    maximum_page_count: int = MAXIMUM_PDF_PAGE_COUNT,
) -> ExtractedPdfText:
    """PDFからページ単位の本文を抽出する。"""

    if maximum_page_count <= 0:
        raise ValueError(
            "maximum_page_countは1以上で指定してください。"
        )

    if not content:
        raise TdnetPdfTextExtractionError(
            "PDF本文抽出対象が空です。"
        )

    if not has_pdf_signature(content):
        raise TdnetPdfTextExtractionError(
            "PDF本文抽出対象にPDFヘッダーがありません。"
        )

    try:
        with pdfplumber.open(
            BytesIO(content)
        ) as pdf:
            page_count = len(pdf.pages)

            if page_count == 0:
                raise TdnetPdfTextExtractionError(
                    "PDFにページがありません。"
                )

            if page_count > maximum_page_count:
                raise TdnetPdfTextExtractionError(
                    "PDFのページ数が上限を超えています。"
                    f"上限: {maximum_page_count}, "
                    f"ページ数: {page_count}"
                )

            page_texts = []

            for page_number, page in enumerate(
                pdf.pages,
                start=1,
            ):
                try:
                    extracted_text = (
                        page.extract_text() or ""
                    )
                except Exception as error:
                    raise TdnetPdfTextExtractionError(
                        "PDFページの本文抽出に失敗しました。"
                        f"ページ: {page_number}, "
                        f"エラー種別: "
                        f"{type(error).__name__}"
                    ) from error

                page_texts.append(extracted_text)

    except TdnetPdfTextExtractionError:
        raise
    except Exception as error:
        raise TdnetPdfTextExtractionError(
            "PDFファイルを開けませんでした。"
            f"エラー種別: {type(error).__name__}"
        ) from error

    normalized_page_texts = tuple(
        str(page_text or "")
        for page_text in page_texts
    )
    extracted_text_length = sum(
        len(page_text)
        for page_text in normalized_page_texts
    )

    if not any(
        page_text.strip()
        for page_text in normalized_page_texts
    ):
        raise TdnetPdfTextExtractionError(
            "PDFから文字を抽出できませんでした。"
            "画像PDFの可能性があります。"
        )

    return ExtractedPdfText(
        page_texts=normalized_page_texts,
        page_count=len(normalized_page_texts),
        extracted_text_length=(
            extracted_text_length
        ),
    )
