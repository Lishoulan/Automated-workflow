from __future__ import annotations

from toutiao_content_factory.services import material_search_service as material


TITLE = "苹果 iPhone Duo 耐用性测试：反向弯折内屏开裂"
PRIMARY_URL = "https://phone.example/original"
RELATED_URL = "https://review.example/durability"
FRUIT_URL = "https://encyclopedia.example/apple-fruit"
PHONE_BODY = "\n".join([
    "苹果 iPhone Duo 耐用性测试于2026年10月25日公布，测试包含反向弯折。",
    "这次测试使用1台手机，机身展开后进行第2项反向弯折测试，内屏出现开裂。",
    "测试者记录了3次屏幕操作，测试环境为25摄氏度，结果仅对应这一台设备。",
    "测试还展示4处机身位置，测试者说明视频没有提供全部用户场景的数据。",
    "手机内屏尺寸为6.8英寸，测试中第5次操作的状态可从公开画面核对。",
])
FRUIT_BODY = "\n".join([
    "此条目介绍水果和植物苹果，关于美国科技公司请见苹果公司。",
    "果树在2026年种植，试验地有200株苹果树，每亩种植40株。",
    "果园2025年收获3000公斤，2026年有5个苹果品种，6月开始采样。",
]) * 14


def source(title, url, body):
    return material.MaterialSource(title=title, url=url, body=body, ok=True)


def test_short_search_phrase_cannot_authorize_an_unrelated_article(monkeypatch):
    pages = {PRIMARY_URL: PHONE_BODY * 3, RELATED_URL: PHONE_BODY * 5, FRUIT_URL: FRUIT_BODY}
    assert 400 <= len(pages[PRIMARY_URL]) < 900
    fetched = []

    def search(query, **kwargs):
        if query == "苹果 iPhone":
            return [
                {"title": "苹果 - 水果和植物百科", "url": FRUIT_URL},
                {"title": TITLE, "url": RELATED_URL},
            ]
        return []

    def fetch(url, **kwargs):
        fetched.append(url)
        return pages[url], url, "http"

    monkeypatch.setattr(material, "search_web", search)
    monkeypatch.setattr(material, "fetch_text_smart", fetch)
    monkeypatch.setattr(material, "extract_article_body", lambda page, **kwargs: page)
    bundle = material.collect_material(TITLE, source_url=PRIMARY_URL, min_body_chars=400)

    assert material.assess_material_evidence(bundle).passed
    assert {item.url for item in bundle.usable} == {PRIMARY_URL, RELATED_URL}
    assert FRUIT_URL not in fetched


def test_cached_unrelated_article_cannot_supply_missing_evidence(tmp_path):
    bundle = material.MaterialBundle(TITLE, [
        source(TITLE, PRIMARY_URL, PHONE_BODY * 3),
        source("苹果 - 水果和植物百科", FRUIT_URL, FRUIT_BODY),
    ])
    assert material.assess_material_evidence(bundle).passed
    material._save_cached_material(tmp_path, bundle)
    cache_file = material._material_cache_path(tmp_path, TITLE)
    previous = cache_file.read_bytes()

    assert material._load_cached_material(tmp_path, TITLE) is None
    assert cache_file.read_bytes() == previous


def test_relevant_cached_sources_keep_provenance(tmp_path):
    first = source(TITLE, PRIMARY_URL, PHONE_BODY * 2)
    first.published_at, first.date_verified, first.publisher = "2026-10-25T10:00:00+08:00", True, "原始报道"
    bundle = material.MaterialBundle(TITLE, [first, source(TITLE, RELATED_URL, PHONE_BODY * 5)])
    material._save_cached_material(tmp_path, bundle)

    restored = material._load_cached_material(tmp_path, TITLE)
    assert restored is not None
    assert {item.url for item in restored.usable} == {PRIMARY_URL, RELATED_URL}
    assert restored.sources[0].published_at == "2026-10-25T10:00:00+08:00"
    assert restored.sources[0].date_verified is True
    assert restored.sources[0].publisher == "原始报道"
