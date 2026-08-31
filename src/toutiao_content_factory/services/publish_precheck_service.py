"""头条公域发布前最终合规预检服务。

在文章发布到头条公域之前，对正文进行最后一道硬拦截校验，
防止 CTA 引流段落、内部数据源标识、重复指标短语、内部 URL 等泄漏到公域。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

# CTA 段落标记（与 publish_service._strip_cta_for_toutiao 保持一致）。
# 必须同时包含带 ## 前缀和不带前缀的版本，因为 clean_article_text 会剥离行首 ## 。
CTA_MARKERS: tuple[str, ...] = (
    # 带 ## 前缀（clean_article_text 调用前的原始格式）
    "## 延伸服务",
    "## 延伸阅读",
    "## 进一步交流",
    # 不带 ## 前缀（clean_article_text 调用后被改写的格式）
    "延伸服务",
    "延伸阅读",
    "进一步交流",
    # 内容型标记（段落首句，作为兜底）
    "如果你想把信息差变成结果",
    "做内容的目的不是多发几篇",
)

# 内部数据源标识黑名单（参考 article_sanitizer.META_NOISE_PREFIXES）。
INTERNAL_IDENTIFIERS: tuple[str, ...] = (
    "evergreen_pool",
    "evergreen://",
    "benchmark_hotspot",
    "对标账号高热内容",
)

# These are copied-account metadata, not article evidence.  They are checked
# again here because this service sees the exact text about to cross into the
# public platform boundary.
EXTERNAL_SOURCE_METADATA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?m)^\s*(?:作者|编辑|责编|责任编辑|撰文|审校|整理)\s*[|｜：:]"),
    re.compile(r"(?:公众号|微信号|微博|抖音号)\s*[：:]"),
    re.compile(r"(?m)^\s*(?:关注我|欢迎关注|关注本号|关注本账号|关注公众号|扫码关注)"),
    re.compile(r"(?m)^\s*#[^\s#]+(?:\s+#[^\s#]+)*\s*$"),
)

# 内部 URL 正则：evergreen://xxx 与 .runtime/xxx
INTERNAL_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"evergreen://[^\s]*"),
    re.compile(r"\.runtime/[^\s]*"),
)

# 指标短语正则：至少 3 个中文词用顿号连接，如"订单量、量产节奏、单机成本"。
_METRIC_PHRASE_RE = re.compile(r"[\u4e00-\u9fa5]+(?:、[\u4e00-\u9fa5]+){2,}")

# 同一指标短语重复出现次数阈值，达到即判定为重复。
_REPEAT_THRESHOLD = 3


class PublishPrecheckService:
    """头条公域发布前最终合规预检器。

    在发布流水线末端执行硬拦截校验，命中任一规则即返回 ``status="blocked"``，
    并将详细违规项写入 ``precheck_report.json`` 供主流程审计。
    """

    def __init__(self, account: str = "default", logger: logging.Logger | None = None) -> None:
        """初始化预检服务。

        Args:
            account: 账号标识，用于隔离报告输出目录。
            logger: 可选日志记录器，未提供时使用本模块默认 logger。
        """
        self.account = account
        self.logger = logger or logging.getLogger(__name__)

    def run_precheck(self, article: str, project_root: Path | None = None) -> dict:
        """对文章执行全部硬拦截校验，并输出 precheck_report.json。

        Args:
            article: 待发布的文章正文。
            project_root: 项目根目录，用于定位 ``.runtime/accounts/{account}`` 输出目录。
                未提供时默认使用当前工作目录。

        Returns:
            包含 ``status`` / ``violations`` / ``report_path`` /
            ``article_length`` / ``checked_at`` 字段的预检结果字典。
        """
        text = str(article or "")
        violations: list[dict] = []

        violations.extend(self._check_cta_markers(text))
        violations.extend(self._check_internal_identifiers(text))
        violations.extend(self._check_external_source_metadata(text))
        violations.extend(self._check_repeated_phrases(text))
        violations.extend(self._check_internal_urls(text))

        status = "blocked" if violations else "passed"
        checked_at = datetime.now().isoformat()
        report_path = self._write_report(
            status=status,
            violations=violations,
            article_length=len(text),
            checked_at=checked_at,
            project_root=project_root,
        )

        self.logger.info(
            "发布预检完成：status=%s，命中 %d 项违规，报告=%s",
            status,
            len(violations),
            report_path,
        )

        return {
            "status": status,
            "violations": violations,
            "report_path": str(report_path),
            "article_length": len(text),
            "checked_at": checked_at,
        }

    # ------------------------------------------------------------------
    # 硬拦截项 1：CTA 段落残留检测
    # ------------------------------------------------------------------
    def _check_cta_markers(self, article: str) -> list[dict]:
        """检测 CTA 引流段落标记是否残留。"""
        violations: list[dict] = []
        for marker in CTA_MARKERS:
            if marker in article:
                violations.append({"rule": "cta_marker", "matched": marker})
        return violations

    # ------------------------------------------------------------------
    # 硬拦截项 2：内部数据源标识残留检测
    # ------------------------------------------------------------------
    def _check_internal_identifiers(self, article: str) -> list[dict]:
        """检测内部数据源标识是否残留。"""
        violations: list[dict] = []
        for identifier in INTERNAL_IDENTIFIERS:
            if identifier in article:
                violations.append(
                    {"rule": "internal_identifier", "matched": identifier}
                )
        return violations

    def _check_external_source_metadata(self, article: str) -> list[dict]:
        """Block copied bylines, account handles, CTAs, and hashtag rows."""
        violations: list[dict] = []
        for pattern in EXTERNAL_SOURCE_METADATA_PATTERNS:
            match = pattern.search(article)
            if match:
                violations.append(
                    {
                        "rule": "external_source_metadata",
                        "matched": match.group(0).strip(),
                    }
                )
        return violations

    # ------------------------------------------------------------------
    # 硬拦截项 3：段落重复率检测（段落级，跨段落提及不算违规）
    # ------------------------------------------------------------------
    def _check_repeated_phrases(self, article: str) -> list[dict]:
        """检测指标短语是否在同一自然段内重复出现 ≥3 次。

        跨段落合理提及产业链关键指标（如"样机验证、量产节奏、单机成本"）
        是深度行业分析的正常写法，不算违规；仅当同一自然段内反复堆砌
        同一指标短语时才判定为重复率过高。
        """
        violations: list[dict] = []
        seen: set[str] = set()
        # 按空行分割自然段（兼容 \\n\\n 与 \\r\\n\\r\\n）
        paragraphs = re.split(r"\n\s*\n", article)
        for match in _METRIC_PHRASE_RE.finditer(article):
            phrase = match.group(0)
            if phrase in seen:
                continue
            seen.add(phrase)
            # 只统计同一自然段内的重复次数，取最大值
            max_in_paragraph = max(
                (para.count(phrase) for para in paragraphs),
                default=0,
            )
            if max_in_paragraph >= _REPEAT_THRESHOLD:
                violations.append(
                    {
                        "rule": "repeated_phrase",
                        "matched": phrase,
                        "count": max_in_paragraph,
                    }
                )
        return violations

    # ------------------------------------------------------------------
    # 硬拦截项 4：内部 URL 泄漏检测
    # ------------------------------------------------------------------
    def _check_internal_urls(self, article: str) -> list[dict]:
        """检测内部 URL（evergreen://、.runtime/）是否泄漏。"""
        violations: list[dict] = []
        for pattern in INTERNAL_URL_PATTERNS:
            for match in pattern.findall(article):
                violations.append({"rule": "internal_url", "matched": match})
        return violations

    # ------------------------------------------------------------------
    # 报告输出
    # ------------------------------------------------------------------
    def _write_report(
        self,
        *,
        status: str,
        violations: list[dict],
        article_length: int,
        checked_at: str,
        project_root: Path | None,
    ) -> Path:
        """将预检结果写入 precheck_report.json，返回报告路径。"""
        root = Path(project_root) if project_root is not None else Path.cwd()
        report_dir = root / ".runtime" / "accounts" / self.account
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "precheck_report.json"

        payload = {
            "account": self.account,
            "status": status,
            "violations": violations,
            "article_length": article_length,
            "checked_at": checked_at,
        }
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report_path
