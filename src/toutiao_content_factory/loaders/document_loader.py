from __future__ import annotations

import re
from pathlib import Path

try:
    from pypdf import PdfReader
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    PdfReader = None

from toutiao_content_factory.exceptions import InputNotFoundError, UnsupportedFileTypeError
from toutiao_content_factory.models import SourceDocument


class DocumentLoader:
    SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf"}
    TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030")

    def __init__(self, input_dir: Path, max_input_chars: int) -> None:
        self.input_dir = input_dir
        self.max_input_chars = max_input_chars

    def load(self, paste_text: str | None = None, include_input_dir: bool = True) -> list[SourceDocument]:
        documents: list[SourceDocument] = []
        if paste_text and paste_text.strip():
            metadata = self._extract_inline_metadata(paste_text)
            documents.append(
                SourceDocument(
                    source_type="paste",
                    content=self._trim_text(paste_text),
                    title="manual_paste",
                    metadata=metadata,
                )
            )

        if include_input_dir:
            for path in sorted(self.input_dir.iterdir()):
                if not path.is_file() or path.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                    continue
                documents.append(self._load_file(path))

        if not documents:
            raise InputNotFoundError(
                f"未在 {self.input_dir} 中找到可处理文件，且没有传入 --paste 文本。"
            )

        return documents

    def _load_file(self, path: Path) -> SourceDocument:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            content = self._read_pdf(path)
            source_type = "pdf"
        else:
            content = self._read_text(path)
            source_type = "text" if suffix == ".txt" else "markdown"

        return SourceDocument(
            source_type=source_type,
            content=self._trim_text(content),
            path=path,
            title=path.stem,
            metadata=self._extract_inline_metadata(content),
        )

    def _read_text(self, path: Path) -> str:
        for encoding in self.TEXT_ENCODINGS:
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="ignore")

    def _read_pdf(self, path: Path) -> str:
        if PdfReader is None:
            raise UnsupportedFileTypeError("当前环境未安装 pypdf，暂时无法读取 PDF 文件。")
        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n".join(pages)

    def _trim_text(self, text: str) -> str:
        normalized_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Normalize markdown headings and bullet prefixes before extraction.
            line = re.sub(r"^(#+|\-|\*|\d+\.)\s*", "", line)
            if line:
                normalized_lines.append(line)

        cleaned = "\n".join(normalized_lines)
        return cleaned[: self.max_input_chars]

    def _extract_inline_metadata(self, text: str) -> dict[str, str]:
        metadata: dict[str, str] = {}
        patterns = {
            "topic_title": r"标题：([^\n]+)",
            "selection_reason": r"入选理由：([^\n]+)",
            "suggested_angle": r"推荐主结论：([^\n]+)",
            "follow_up_focus": r"跟踪变量：([^\n]+)",
            "writing_brief": r"固定写法：([^\n]+)",
            "primary_industry": r"主赛道：([^\n]+)",
            "primary_keywords": r"主关键词：([^\n]+)",
            "topic_type": r"选题类型：([^\n]+)",
            "content_format": r"内容体裁：([^\n]+)",
            "format_brief": r"体裁要求：([^\n]+)",
            "paragraph_instruction": r"段落要求：([^\n]+)",
            "evidence_strength": r"证据强度：([^\n]+)",
            "topic_quality_score": r"选题质量分：([^\n]+)",
            "topic_quality_flags": r"质量标记：([^\n]+)",
            "word_target": r"建议字数：([^\n]+)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, text)
            if match:
                metadata[key] = match.group(1).strip()
        return metadata
