"""回归测试：互动评论的 API 端点拼接。

背景（2026-09-18）：`engagement_agent` 原来在 base 不以 `/v1` 结尾时**硬拼**
`/v1/chat/completions`。这在 DeepSeek 的 base 下是对的，在智谱的
`https://open.bigmodel.cn/api/paas/v4` 下就拼成了
`/api/paas/v4/v1/chat/completions` → 404，云端互动评论全线失败。
修好后主选端点与仓库其余部分一致（`${base}/chat/completions`），
仅在 404 时回落到 `/v1` 变体。

这些用例不需要网络，也不读真实 `.env`。
"""

from __future__ import annotations

from toutiao_content_factory.services.engagement_agent import _candidate_endpoints


def test_zhipu_base_does_not_get_a_duplicated_version_segment() -> None:
    """智谱 base 自带 /v4，主选端点绝不能变成 /v4/v1/...（这正是 404 的成因）。

    注意：回落端点里出现 `/v4/v1/` 是**允许**的——它只在主选 404 时才会用到。
    出问题的是「唯一且必然使用」的那个端点，所以这里断言的是 endpoints[0]。
    """
    endpoints = _candidate_endpoints("https://open.bigmodel.cn/api/paas/v4")
    assert endpoints[0] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert "/v1/" not in endpoints[0]


def test_deepseek_base_keeps_the_v1_path() -> None:
    """DeepSeek 的 base 带 /v1，只有一种正确写法，不该再回落。"""
    endpoints = _candidate_endpoints("https://api.deepseek.com/v1")
    assert endpoints == ["https://api.deepseek.com/v1/chat/completions"]


def test_bare_base_falls_back_to_v1_after_the_primary() -> None:
    """无版本段的 base（如 https://api.deepseek.com）主选在前、/v1 变体在后。"""
    endpoints = _candidate_endpoints("https://api.deepseek.com")
    assert endpoints == [
        "https://api.deepseek.com/chat/completions",
        "https://api.deepseek.com/v1/chat/completions",
    ]


def test_trailing_slash_is_normalized() -> None:
    endpoints = _candidate_endpoints("https://open.bigmodel.cn/api/paas/v4/")
    assert endpoints[0] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def test_primary_endpoint_matches_the_rest_of_the_repo_convention() -> None:
    """主选端点必须是 `${base}/chat/completions`，与 title_agent 等处一致。"""
    for base in (
        "https://open.bigmodel.cn/api/paas/v4",
        "https://api.siliconflow.cn/v1",
        "https://api.deepseek.com",
    ):
        assert _candidate_endpoints(base)[0] == base + "/chat/completions"
