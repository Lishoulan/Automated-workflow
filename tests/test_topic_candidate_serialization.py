from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate


def test_topic_candidate_ignores_workflow_only_editorial_council_field() -> None:
    payload = {
        "title": "机器人产线验证加速",
        "url": "https://example.com/topic",
        "source": "test",
        "source_type": "html",
        "editorial_council": {
            "editorial_thesis": "该字段属于内容工作流，不属于选题候选。",
        },
    }

    candidate = TopicCandidate.from_dict(payload)

    assert candidate.title == "机器人产线验证加速"
    assert not hasattr(candidate, "editorial_council")


def test_daily_topics_report_loads_candidates_with_extra_fields() -> None:
    report = DailyTopicsReport.from_dict(
        {
            "generated_at": "2026-08-29T18:00:00",
            "lookback_days": 2,
            "max_topic_age_hours": 12,
            "focus_industries": ["humanoid_robot"],
            "total_raw_items": 1,
            "total_selected_items": 1,
            "topics": [
                {
                    "title": "机器人产线验证加速",
                    "url": "https://example.com/topic",
                    "source": "test",
                    "source_type": "html",
                    "editorial_council": {"confidence_score": 0.8},
                }
            ],
        }
    )

    assert len(report.topics) == 1
    assert isinstance(report.topics[0], TopicCandidate)
