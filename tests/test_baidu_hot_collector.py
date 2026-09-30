from __future__ import annotations

import json
import logging

from toutiao_content_factory.services.topic_collection import (
    TopicCollectionService,
    _BaiduHotTopicCollector,
)


def test_baidu_hot_collector_keeps_embedded_summary_and_search_url(app_settings) -> None:
    service = TopicCollectionService(app_settings, logging.getLogger("test.baidu_hot"))
    collector = _BaiduHotTopicCollector("baidu_hot", {}, service)
    content = [
        {
            "query": "女子进矿山工作六年被亲戚羡慕",
            "desc": "9月29日，河南长垣一女子讲述自己在矿山工作6年的经历，公司包吃包住，工资按时发放。",
            "hotScore": "5792291",
            "rawUrl": "https://www.baidu.com/s?wd=story",
        }
    ]
    payload = "<!--s-data:" + json.dumps(
        {
            "data": {
                "cards": [
                    {"component": "other", "content": []},
                    {"component": "hotList", "content": content},
                ]
            }
        },
        ensure_ascii=True,
    ) + "-->"

    topics = collector._parse_hot_page(payload)

    assert len(topics) == 1
    assert topics[0].title == content[0]["query"]
    assert topics[0].snippet == content[0]["desc"]
    assert topics[0].url == content[0]["rawUrl"]
    assert topics[0].heat_value == 5_792_291
