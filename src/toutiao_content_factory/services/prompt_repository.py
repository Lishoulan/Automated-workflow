from __future__ import annotations

from pathlib import Path
import re
from string import Template


class PromptRepository:
    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir

    def render(self, template_name: str, **kwargs: str) -> str:
        template_path = self.prompts_dir / template_name
        content = template_path.read_text(encoding="utf-8")
        rendered = Template(content).safe_substitute(**kwargs)
        return self._normalize_prompt(rendered)

    def _normalize_prompt(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = re.sub(r"^账号：.*$\n?", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^账号名：.*$\n?", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"服务账号是[“\"].*?[”\"]", "服务对象是该科技内容项目", cleaned)
        cleaned = re.sub(r"负责账号[“\"].*?[”\"]的头条内容生产", "负责头条科技内容生产", cleaned)
        cleaned = re.sub(r"按.*?的写法来写", "按既定产业分析写法来写", cleaned)
        return cleaned
