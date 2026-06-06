"""深度去 AI 味模块 — 信息密度检查 + AI陈词过滤 + 长短句打散 + 图片指纹"""
from __future__ import annotations

import hashlib
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# 1) AI 陈词列表（可扩展 + 从文件加载）
# ============================================================================

DEFAULT_AI_CLICHE_WORDS = r"""
综上所述
总而言之
不可否认
不仅如此
而且更重要的是
这不只是……更是
我们要看到的是
值得注意的是
令人惊讶的是
毫无疑问
在这一背景下
随着……的发展
在当今……时代
可以说
由此可见
众所周知
需要指出的是
不言而喻
诚然
毋庸置疑
必须承认
值得一提的是
从某种程度上说
从某种意义上讲
事实上
很显然
这意味着
这说明了
从中可以看出
正如我们所知
在当今快节奏的世界里
如前所述
显而易见的是
"""


@dataclass
class AIClicheFilter:
    """AI 陈词过滤器"""

    cliche_words: list[str] = field(default_factory=list)
    replacement_hints: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load_from_file(cls, path: Path | None = None) -> "AIClicheFilter":
        """从文件加载陈词列表"""
        filter_obj = cls()
        words_text = DEFAULT_AI_CLICHE_WORDS

        if path and path.exists():
            try:
                words_text = path.read_text(encoding="utf-8")
            except Exception:
                pass

        for line in words_text.strip().split("\n"):
            word = line.strip()
            if word and not word.startswith("#"):
                filter_obj.cliche_words.append(word)

        return filter_obj

    def scan(self, text: str) -> dict[str, Any]:
        """扫描文本中的 AI 陈词"""
        found: list[str] = []
        cleaned = text
        count = 0

        for cliche in self.cliche_words:
            if cliche in cleaned:
                found.append(cliche)
                count += len(re.findall(re.escape(cliche), cleaned))

        return {
            "found_count": len(found),
            "total_occurrences": count,
            "matched_cliches": found,
            "density": round(count / max(len(text), 1) * 1000, 2),
        }

    def clean(self, text: str) -> str:
        """自动替换/删除 AI 陈词"""
        result = text
        for cliche in sorted(self.cliche_words, key=len, reverse=True):
            # 替换为更自然的短表述
            replacement_map = {
                "综上所述": "",
                "总而言之": "总的来说",
                "不可否认": "",
                "不仅如此": "",
                "而且更重要的是": "更关键的是",
                "值得注意的是": "",
                "毫无疑问": "",
                "可以说": "",
                "众所周知": "业内公认",
                "需要指出的是": "需要注意",
                "不言而喻": "",
                "诚然": "",
                "毋庸置疑": "",
                "必须承认": "",
                "值得一提的是": "值得关注的是",
                "这意味着": "背后是",
                "这说明了": "反映出",
                "从中可以看出": "可见",
            }
            replacement = replacement_map.get(cliche, "")
            if replacement:
                result = result.replace(cliche, replacement)
            else:
                # 删除（前后保留一个空格）
                result = re.sub(rf"\s*{re.escape(cliche)}\s*", " ", result)

        # 清理多余空格
        result = re.sub(r"\s{2,}", " ", result)
        result = result.strip()
        return result


# ============================================================================
# 2) 信息密度检查
# ============================================================================

@dataclass
class InfoDensityResult:
    """信息密度分析结果"""
    noun_ratio: float = 0.0
    verb_ratio: float = 0.0
    total_chars: int = 0
    total_words: int = 0
    sentence_count: int = 0
    avg_sentence_length: float = 0.0
    density_score: int = 0  # 0-100
    is_too_verbose: bool = False
    suggestions: list[str] = field(default_factory=list)


class InfoDensityChecker:
    """信息密度检查器 — 统计名词/动词比例，剔除车轱辘话"""

    # 中文常见名词后缀
    NOUN_MARKERS = [
        "机", "器", "源", "能", "站", "厂", "链", "网", "端", "芯",
        "储", "电", "池", "车", "人", "企", "业", "商", "模", "式",
        "系", "统", "数", "据", "报", "告", "方", "案", "协", "议",
        "公司", "技术", "产品", "市场", "行业", "领域", "赛道",
    ]

    # 中文常见动词标记
    VERB_MARKERS = [
        "发布", "推出", "宣布", "完成", "实现", "达到", "突破",
        "增长", "下降", "提升", "降低", "进入", "退出", "布局",
        "投资", "收购", "合作", "签约", "交付", "量产", "落地",
    ]

    # AI 车轱辘话特征（功能词/连接词过多）
    FILLER_PATTERNS = [
        r"不仅.{1,10}而且",
        r"一方面.{1,10}另一方面",
        r"既.{1,8}又",
        r"首先.{1,10}其次.{1,10}最后",
        r"总而言之",
        r"综上所述",
        r"基于以上",
        r"通过以上",
    ]

    @classmethod
    def check(cls, text: str) -> InfoDensityResult:
        """分析文本信息密度"""
        result = InfoDensityResult()
        result.total_chars = len(text)

        # 简单"词"计数（按标点和空格分割）
        words = [w for w in re.split(r"[\s，。！？、：；""''（）\n]", text) if len(w) >= 2]
        result.total_words = len(words)

        # 句子计数
        sentences = [s.strip() for s in re.split(r"[。！？!?；;]", text) if s.strip()]
        result.sentence_count = len(sentences)

        if result.sentence_count > 0:
            result.avg_sentence_length = result.total_chars / result.sentence_count

        # 名词比例
        noun_count = 0
        for marker in cls.NOUN_MARKERS:
            noun_count += text.count(marker)
        result.noun_ratio = noun_count / max(result.total_words, 1)

        # 动词比例
        verb_count = 0
        for marker in cls.VERB_MARKERS:
            verb_count += text.count(marker)
        result.verb_ratio = verb_count / max(result.total_words, 1)

        # 车轱辘话检测
        filler_count = 0
        for pattern in cls.FILLER_PATTERNS:
            filler_count += len(re.findall(pattern, text))
        result.is_too_verbose = filler_count >= 3

        # 密度评分
        result.density_score = int(
            min(100, (result.noun_ratio * 40 + result.verb_ratio * 30 + (1 - filler_count * 0.1) * 30))
        )

        # 建议
        if result.density_score < 50:
            result.suggestions.append("信息密度偏低，建议增加具体数据/名词/动词，减少连接词和套话")
        if result.is_too_verbose:
            result.suggestions.append("检测到车轱辘话模式，建议精炼表达，删减冗余连接词")
        if result.avg_sentence_length > 80:
            result.suggestions.append("平均句子过长（{}字），建议拆分长句".format(int(result.avg_sentence_length)))

        return result


# ============================================================================
# 3) 长短句交叉打散
# ============================================================================

class SentenceVariationAdjuster:
    """长短短句检测 + 强制打散"""

    @classmethod
    def analyze(cls, text: str) -> dict[str, Any]:
        """分析句子长度分布"""
        sentences = [s.strip() for s in re.split(r"[。！？!?；;]", text) if s.strip()]
        lengths = [len(s) for s in sentences]

        if not lengths:
            return {"status": "empty"}

        return {
            "total": len(lengths),
            "max_length": max(lengths),
            "min_length": min(lengths),
            "avg_length": round(sum(lengths) / len(lengths), 1),
            "similarity_risk": cls._similarity_risk(lengths),
            "too_uniform": cls._is_too_uniform(lengths),
        }

    @classmethod
    def adjust(cls, text: str) -> str:
        """强制打散连续的长句或短句"""
        sentences = re.split(r"(?<=[。！？!?；;])", text)
        adjusted: list[str] = []
        consecutive_long = 0
        consecutive_short = 0

        for i, sent in enumerate(sentences):
            stripped = sent.strip()
            if not stripped:
                adjusted.append(sent)
                continue

            length = len(stripped)

            if length > 60:
                consecutive_long += 1
                consecutive_short = 0
                if consecutive_long >= 3:
                    # 第 3 个连续长句：尝试在逗号处拆分
                    parts = re.split(r"(?<=[，,])", stripped)
                    if len(parts) >= 3:
                        mid = len(parts) // 2
                        # 将后半部分另起
                        adjusted.extend(parts[:mid])
                        adjusted.append("\n")
                        adjusted.extend(parts[mid:])
                    else:
                        adjusted.append(sent)
                    consecutive_long = 0
                else:
                    adjusted.append(sent)
            elif length < 10:
                consecutive_short += 1
                consecutive_long = 0
                if consecutive_short >= 3:
                    # 合并连续短句
                    if adjusted:
                        adjusted[-1] = adjusted[-1].rstrip() + "，" + stripped
                        consecutive_short -= 1
                    else:
                        adjusted.append(sent)
                else:
                    adjusted.append(sent)
            else:
                consecutive_long = 0
                consecutive_short = 0
                adjusted.append(sent)

        return "".join(adjusted)

    @classmethod
    def _similarity_risk(cls, lengths: list[int]) -> float:
        """连续句子长度差异过小的风险"""
        if len(lengths) < 3:
            return 0.0
        similar_count = 0
        for i in range(len(lengths) - 2):
            diffs = [abs(lengths[i] - lengths[i+1]), abs(lengths[i+1] - lengths[i+2])]
            if all(d < 10 for d in diffs):
                similar_count += 1
        return round(similar_count / max(len(lengths) - 2, 1), 2)

    @classmethod
    def _is_too_uniform(cls, lengths: list[int]) -> bool:
        return cls._similarity_risk(lengths) > 0.3


# ============================================================================
# 4) 图片指纹去重（轻微噪点 + 色相偏移）
# ============================================================================

class ImageFingerprintObfuscator:
    """在图片渲染时加入极轻微噪点和色相偏移，防止平台 MD5/感知哈希判定"""

    @classmethod
    def add_noise_to_image(
        cls,
        image_path: Path | str,
        noise_level: float = 0.005,
        hue_shift: float = 1.0,
    ) -> Path:
        """给图片添加微量噪点和色相偏移，返回新路径"""
        try:
            from PIL import Image, ImageEnhance
            import numpy as np
        except ModuleNotFoundError:
            logger.debug("Pillow or numpy not available, skipping image fingerprint obfuscation.")
            return Path(image_path)

        image_path = Path(image_path)
        if not image_path.exists():
            return image_path

        img = Image.open(image_path).convert("RGB")
        arr = np.array(img, dtype=np.float32)

        # 添加轻微高斯噪点
        noise = np.random.normal(0, noise_level * 255, arr.shape)
        arr = arr + noise
        arr = np.clip(arr, 0, 255).astype(np.uint8)

        # 轻微色相偏移（HSV 空间）
        try:
            img_noised = Image.fromarray(arr)
            if hue_shift != 0:
                hsv = img_noised.convert("HSV")
                hsv_arr = np.array(hsv, dtype=np.float32)
                hsv_arr[:, :, 0] = (hsv_arr[:, :, 0] + hue_shift) % 256
                hsv_arr = np.clip(hsv_arr, 0, 255).astype(np.uint8)
                img_out = Image.fromarray(hsv_arr, "HSV").convert("RGB")
            else:
                img_out = img_noised
        except Exception:
            img_out = Image.fromarray(arr)

        # 保存为新文件
        output_path = image_path.parent / f"f_{image_path.stem}{image_path.suffix}"
        img_out.save(output_path, quality=98)
        logger.debug("Fingerprint-obfuscated image saved: %s", output_path)
        return output_path

    @classmethod
    def obfuscate_batch(cls, paths: list[Path | str]) -> list[Path]:
        """批量处理"""
        results: list[Path] = []
        for p in paths:
            try:
                results.append(cls.add_noise_to_image(p))
            except Exception as exc:
                logger.warning("Failed to obfuscate image %s: %s", p, exc)
                results.append(Path(p))
        return results


# ============================================================================
# 5) 顶层便捷：一步去 AI 味
# ============================================================================

class AntiAIFlavorPipeline:
    """一站式去 AI 味流水线"""

    def __init__(self, cliche_words_path: Path | None = None):
        self.cliche_filter = AIClicheFilter.load_from_file(cliche_words_path)
        self.density_checker = InfoDensityChecker()
        self.sentence_adjuster = SentenceVariationAdjuster()

    def process(self, text: str) -> dict[str, Any]:
        """处理文本：清除 AI 陈词 → 信息密度检查 → 长短句打散"""
        # Step 1: 清除 AI 陈词
        scan = self.cliche_filter.scan(text)
        cleaned = self.cliche_filter.clean(text)

        # Step 2: 信息密度分析
        density = self.density_checker.check(cleaned)

        # Step 3: 长短句打散
        variation = self.sentence_adjuster.analyze(cleaned)
        if variation.get("too_uniform"):
            cleaned = self.sentence_adjuster.adjust(cleaned)

        return {
            "processed_text": cleaned,
            "cliche_scan": scan,
            "density": {
                "score": density.density_score,
                "noun_ratio": round(density.noun_ratio, 3),
                "verb_ratio": round(density.verb_ratio, 3),
                "is_too_verbose": density.is_too_verbose,
                "avg_sentence_length": round(density.avg_sentence_length, 1),
                "suggestions": density.suggestions,
            },
            "sentence_variation": variation,
        }
