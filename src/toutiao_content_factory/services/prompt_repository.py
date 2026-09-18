from __future__ import annotations

from pathlib import Path
import re
from string import Template


class PromptRepository:
    # 预编译正则表达式以提高性能
    _ACCOUNT_PATTERN = re.compile(r"^账号：.*$\n?", re.MULTILINE)
    _ACCOUNT_NAME_PATTERN = re.compile(r"^账号名：.*$\n?", re.MULTILINE)
    _SERVICE_ACCOUNT_PATTERN = re.compile(r"服务账号是[“\"].*?[”\"]")
    _RESPONSIBLE_ACCOUNT_PATTERN = re.compile(r"负责账号[“\"].*?[”\"]的头条内容生产")
    _WRITING_STYLE_PATTERN = re.compile(r"按.*?的写法来写")
    _BRACED_VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir
        self._template_cache: dict[str, str] = {}  # 模板缓存

    def render(
        self,
        template_name: str,
        *,
        content_domain: str = "industry",
        **kwargs: str,
    ) -> str:
        # 尝试从缓存中获取模板
        if template_name not in self._template_cache:
            template_path = self.prompts_dir / template_name
            content = template_path.read_text(encoding="utf-8")
            self._template_cache[template_name] = content
        content = self._template_cache[template_name]
        rendered = Template(content).safe_substitute(**kwargs)
        # Some legacy prompts use Jinja-style braces. Support them while
        # keeping string.Template as the canonical syntax for new prompts.
        rendered = self._BRACED_VARIABLE_PATTERN.sub(
            lambda match: str(kwargs.get(match.group(1), match.group(0))),
            rendered,
        )
        return self._normalize_prompt(rendered, content_domain=content_domain)

    def _normalize_prompt(self, text: str, *, content_domain: str = "industry") -> str:
        """清理提示词里的账号信息与写死的领域措辞。

        ⚠️ 2026-09-18：这里原来把所有提示词里的写法都改写为
        「按既定**产业分析**写法来写」，并且把服务对象一律写成「该科技内容项目」。
        对科技/产业账号没问题，但社会故事类账号会被反写成产业分析 ——
        哪怕 style 和 system prompt 都已经换成了叙事写法。
        现在按 `content_domain` 取用对应的措辞。
        """
        narrative = str(content_domain or "industry").strip().lower() == "narrative"
        service_subject = "该内容项目" if narrative else "该科技内容项目"
        responsible_line = "负责头条内容生产" if narrative else "负责头条科技内容生产"
        writing_style_line = (
            "按既定人情故事写法来写" if narrative else "按既定产业分析写法来写"
        )
        cleaned = str(text or "")
        cleaned = self._ACCOUNT_PATTERN.sub("", cleaned)
        cleaned = self._ACCOUNT_NAME_PATTERN.sub("", cleaned)
        cleaned = self._SERVICE_ACCOUNT_PATTERN.sub(f"服务对象是{service_subject}", cleaned)
        cleaned = self._RESPONSIBLE_ACCOUNT_PATTERN.sub(responsible_line, cleaned)
        cleaned = self._WRITING_STYLE_PATTERN.sub(writing_style_line, cleaned)
        return cleaned
