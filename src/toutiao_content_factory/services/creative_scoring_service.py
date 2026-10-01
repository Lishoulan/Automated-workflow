from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageStat

from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.services.growth_feedback_service import title_signals


class CreativeScoringService:
    """Score article titles and the generated cover with explainable signals."""

    AUTO_TITLE_SCORE_MIN = 70
    AUTO_COVER_SCORE_MIN = 70
    AUTO_COMBINED_SCORE_MIN = 70

    _CONCRETE_TOKENS = (
        "老人", "夫妻", "妻子", "丈夫", "儿子", "女儿", "邻居", "物业", "法院",
        "公司", "工厂", "机器人", "电池", "订单", "收入", "利润", "判决", "赔偿",
    )
    _OUTCOME_TOKENS = (
        "结果", "最后", "后来", "为何", "为什么", "怎么办", "解决", "提醒", "真相",
        "变化", "关键", "背后", "值得", "如何", "怎么", "哪些",
    )
    _SCENE_TOKENS = (
        "家里", "小区", "医院", "婚礼", "饭桌", "车站", "公司", "工厂", "现场",
        "相亲", "养老", "退休", "物业", "邻居",
    )
    _WEAK_TOKENS = ("震惊", "惊呆", "速看", "必看", "重磅", "彻底炸了", "你绝对想不到")

    def __init__(self, calibration_path: Path | None = None) -> None:
        self.quality_checker = ArticleQualityChecker()
        self.calibration = self._load_calibration(calibration_path)

    @staticmethod
    def _load_calibration(path: Path | None) -> dict[str, Any]:
        if path is None:
            return {}
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def score_title(
        self,
        title: str,
        *,
        cover_variant: str = "",
        content_domain: str = "industry",
    ) -> dict[str, Any]:
        text = str(title or "").strip()
        score = 50
        reasons: list[str] = []
        length = len(text)

        if 14 <= length <= 24:
            score += 14
            reasons.append("标题长度适合移动端完整展示")
        elif 10 <= length <= 28:
            score += 8
            reasons.append("标题长度处于可用区间")
        else:
            score -= 12
            reasons.append("标题长度偏离移动端推荐区间")

        click_preference = self.quality_checker.title_click_preference(text)
        score += max(-8, min(12, click_preference * 3))
        if click_preference > 0:
            reasons.append("标题结构具备点击偏好信号")

        concrete = bool(re.search(r"\d", text) or any(token in text for token in self._CONCRETE_TOKENS))
        if concrete:
            score += 10
            reasons.append("包含具体人物、场景或数字")
        else:
            score -= 6
            reasons.append("缺少可快速识别的具体锚点")

        if any(token in text for token in self._OUTCOME_TOKENS) or "？" in text or "?" in text:
            score += 9
            reasons.append("给出结果、问题或信息缺口")
        if any(token in text for token in self._WEAK_TOKENS):
            score -= 18
            reasons.append("含低可信夸张词")
        if text.count("、") >= 2 or text.count("：") + text.count(":") >= 2:
            score -= 8
            reasons.append("并列信息较多，移动端扫读负担偏高")

        variant = str(cover_variant or "").strip().lower()
        domain = str(content_domain or "industry").strip().lower()
        pair_score = 70
        pair_reasons: list[str] = []
        if "data" in variant:
            if re.search(r"\d", text):
                pair_score += 20
                pair_reasons.append("数据型封面与数字标题一致")
            else:
                pair_score -= 15
                pair_reasons.append("数据型封面缺少标题数字锚点")
        elif domain == "narrative" or any(token in variant for token in ("scene", "story", "emotion")):
            if any(token in text for token in self._SCENE_TOKENS):
                pair_score += 20
                pair_reasons.append("故事标题包含可视化生活场景")
            elif concrete:
                pair_score += 10
                pair_reasons.append("故事标题包含可视化主体")
            else:
                pair_score -= 10
                pair_reasons.append("故事封面缺少明确场景或人物")
        else:
            pair_reasons.append("封面类型与标题没有明显冲突")

        adjustments = self.calibration.get("signal_adjustments")
        adjustments = adjustments if isinstance(adjustments, dict) else {}
        matched_signals = title_signals(text)
        calibration_adjustment = max(
            -10,
            min(10, sum(int(adjustments.get(signal) or 0) for signal in matched_signals)),
        )
        if calibration_adjustment:
            score += calibration_adjustment
            reasons.append(f"历史24/72小时表现校准 {calibration_adjustment:+d}")

        title_score = max(0, min(100, score))
        pair_score = max(0, min(100, pair_score))
        total = round(title_score * 0.8 + pair_score * 0.2)
        return {
            "title": text,
            "score": total,
            "title_score": title_score,
            "title_cover_fit_score": pair_score,
            "click_preference": click_preference,
            "cover_variant": variant or "unspecified",
            "calibration_adjustment": calibration_adjustment,
            "calibration_signals": matched_signals,
            "reasons": reasons,
            "pair_reasons": pair_reasons,
        }

    def score_cover(
        self,
        cover_path: Path,
        *,
        title_score: int,
        title: str,
        cover_variant: str = "",
        content_domain: str = "industry",
    ) -> dict[str, Any]:
        path = Path(cover_path)
        if not path.exists():
            return {
                "score": 0,
                "combined_score": round(max(0, min(100, title_score)) * 0.65),
                "status": "missing",
                "reasons": ["封面文件不存在"],
            }

        with Image.open(path) as source:
            image = source.convert("RGB")
            width, height = image.size
            gray = image.convert("L")
            stats = ImageStat.Stat(gray)
            mean = float(stats.mean[0])
            contrast = float(stats.stddev[0])
            extrema = gray.getextrema()
            dynamic_range = int(extrema[1] - extrema[0])
            edges = gray.filter(ImageFilter.FIND_EDGES)
            sharpness = float(ImageStat.Stat(edges).mean[0])

        reasons: list[str] = []
        ratio = width / height if height else 0.0
        ratio_score = 25 if (1.72 <= ratio <= 1.82 or 2.25 <= ratio <= 2.42) else 12 if 1.6 <= ratio <= 2.5 else 0
        reasons.append("封面比例适配头条图文" if ratio_score == 25 else "封面比例需要复核")

        resolution_score = 20 if width >= 900 and height >= 383 else 12 if width >= 640 else 0
        reasons.append("封面分辨率达标" if resolution_score == 20 else "封面分辨率偏低")

        contrast_score = 20 if 38 <= contrast <= 90 else 12 if 25 <= contrast <= 105 else 5
        exposure_score = 15 if 55 <= mean <= 205 else 8 if 35 <= mean <= 225 else 2
        detail_score = 10 if sharpness >= 12 else 6 if sharpness >= 6 else 2
        range_score = 10 if dynamic_range >= 170 else 6 if dynamic_range >= 110 else 2
        if contrast_score < 20:
            reasons.append("封面对比度可能影响缩略图识别")
        if exposure_score < 15:
            reasons.append("封面整体亮度偏暗或偏亮")
        if detail_score < 10:
            reasons.append("封面细节锐度偏低")

        technical_score = ratio_score + resolution_score + contrast_score + exposure_score + detail_score + range_score
        title_payload = self.score_title(
            title,
            cover_variant=cover_variant,
            content_domain=content_domain,
        )
        resolved_title_score = max(0, min(100, int(title_score or title_payload["score"])))
        combined = round(resolved_title_score * 0.65 + technical_score * 0.35)
        return {
            "score": technical_score,
            "combined_score": combined,
            "status": "ready" if technical_score >= 70 else "review",
            "width": width,
            "height": height,
            "aspect_ratio": round(ratio, 3),
            "mean_brightness": round(mean, 2),
            "contrast": round(contrast, 2),
            "dynamic_range": dynamic_range,
            "edge_strength": round(sharpness, 2),
            "reasons": reasons,
        }
