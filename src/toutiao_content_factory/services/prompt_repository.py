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

    def render(self, template_name: str, **kwargs: str) -> str:
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
        return self._normalize_prompt(rendered)

    def _normalize_prompt(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = self._ACCOUNT_PATTERN.sub("", cleaned)
        cleaned = self._ACCOUNT_NAME_PATTERN.sub("", cleaned)
        cleaned = self._SERVICE_ACCOUNT_PATTERN.sub("服务对象是该科技内容项目", cleaned)
        cleaned = self._RESPONSIBLE_ACCOUNT_PATTERN.sub("负责头条科技内容生产", cleaned)
        cleaned = self._WRITING_STYLE_PATTERN.sub("按既定产业分析写法来写", cleaned)
        return cleaned
