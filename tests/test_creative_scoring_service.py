from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from toutiao_content_factory.services.creative_scoring_service import CreativeScoringService


def test_specific_story_title_scores_above_generic_title() -> None:
    scorer = CreativeScoringService()

    specific = scorer.score_title(
        "老人拒交物业费，法院最后怎么判的？",
        cover_variant="story_scene",
        content_domain="narrative",
    )
    generic = scorer.score_title(
        "关于生活中一些事情的几点思考",
        cover_variant="story_scene",
        content_domain="narrative",
    )

    assert specific["score"] > generic["score"]
    assert specific["title_cover_fit_score"] > generic["title_cover_fit_score"]


def test_cover_score_reports_mobile_dimensions_and_visual_signals(tmp_path: Path) -> None:
    path = tmp_path / "cover.png"
    image = Image.new("RGB", (1200, 675), (28, 45, 72))
    draw = ImageDraw.Draw(image)
    draw.rectangle((620, 80, 1120, 590), fill=(225, 170, 70))
    draw.line((40, 580, 580, 120), fill=(240, 245, 250), width=18)
    image.save(path)

    result = CreativeScoringService().score_cover(
        path,
        title_score=82,
        title="老人拒交物业费，法院最后怎么判的？",
        cover_variant="story_scene",
        content_domain="narrative",
    )

    assert result["width"] == 1200
    assert result["height"] == 675
    assert result["aspect_ratio"] == 1.778
    assert 0 <= result["score"] <= 100
    assert 0 <= result["combined_score"] <= 100
