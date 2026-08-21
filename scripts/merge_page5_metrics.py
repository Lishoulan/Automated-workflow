"""Merge page 5 data into performance_metrics.json and recompute summary."""
import json
from pathlib import Path

metrics_path = Path("f:/Automated-workflow-main/output/performance_metrics.json")
data = json.loads(metrics_path.read_text(encoding="utf-8"))

page5_records = [
    {"page": 5, "title": "225%之后，AI硬件产业链开始进入兑现筛选期", "impressions": 1271, "views": 15, "likes": 0, "comments": 0, "shares": 0},
    {"page": 5, "title": "芯片-机器人交叉点，12个月验证期将重构产业链座次", "impressions": 2534, "views": 4, "likes": 0, "comments": 0, "shares": 0},
    {"page": 5, "title": "英伟达交付端加薪149%，芯片设计利润挤压窗口已至", "impressions": 2307, "views": 8, "likes": 0, "comments": 1, "shares": 0},
    {"page": 5, "title": "HBM接口单颗价值5倍于DDR5，澜起卡位关键二供", "impressions": 84, "views": 0, "likes": 0, "comments": 0, "shares": 0},
    {"page": 5, "title": "(无标题-可能为视频或微头条)", "impressions": 1163, "views": 0, "likes": 0, "comments": 0, "shares": 0},
    {"page": 5, "title": "(无标题-可能为视频或微头条)", "impressions": 88, "views": 0, "likes": 0, "comments": 0, "shares": 0},
    {"page": 5, "title": "(无标题-可能为视频或微头条)", "impressions": 78, "views": 1, "likes": 0, "comments": 0, "shares": 0},
    {"page": 5, "title": "(无标题-可能为视频或微头条)", "impressions": 106, "views": 0, "likes": 0, "comments": 0, "shares": 0},
    {"page": 5, "title": "(无标题-可能为视频或微头条)", "impressions": 1313, "views": 0, "likes": 0, "comments": 1, "shares": 0},
    {"page": 5, "title": "世界模型叙事喧嚣，但订单兑现才是产业链硬通货", "impressions": 107, "views": 3, "likes": 0, "comments": 0, "shares": 0},
]

existing_pages = {r.get("page") for r in data["records"]}
new_records = [r for r in page5_records if r["page"] not in existing_pages]
data["records"].extend(new_records)

# 重新排序：按page排序
data["records"].sort(key=lambda r: (r.get("page", 0), r.get("impressions", 0)))

# 更新元信息
data["total_articles_collected"] = len(data["records"])
data["pages_collected"] = sorted({r.get("page") for r in data["records"] if r.get("page")})
data["pages_missing"] = []

# 重新计算汇总
total_impressions = sum(r.get("impressions", 0) for r in data["records"])
total_views = sum(r.get("views", 0) for r in data["records"])
total_likes = sum(r.get("likes", 0) for r in data["records"])
total_comments = sum(r.get("comments", 0) for r in data["records"])
total_shares = sum(r.get("shares", 0) for r in data["records"])

n = len(data["records"])
avg_impressions = total_impressions / n if n else 0
avg_views = total_views / n if n else 0
avg_ctr = total_views / total_impressions if total_impressions else 0

# 找出爆款文章 (impressions > 10000)
viral = [r for r in data["records"] if r.get("impressions", 0) >= 10000]
viral_impressions = sum(r.get("impressions", 0) for r in viral)
viral_share_pct = viral_impressions / total_impressions * 100 if total_impressions else 0

# 找出TOP10阅读量
top_views = sorted(data["records"], key=lambda r: r.get("views", 0), reverse=True)[:10]
# 找出TOP10展现量
top_impressions = sorted(data["records"], key=lambda r: r.get("impressions", 0), reverse=True)[:10]
# 找出BOTTOM10展现量
bottom_impressions = sorted(data["records"], key=lambda r: r.get("impressions", 0))[:10]

# 标题特征分析
titles_with_data = [r for r in data["records"] if not r["title"].startswith("(无标题")]
titles_no_data = [r for r in data["records"] if r["title"].startswith("(无标题")]

# 关键词频次统计
import re
from collections import Counter
all_titles = " ".join(r["title"] for r in titles_with_data)
# 提取关键词
keywords = re.findall(r"(人形机器人|机器人|AI|芯片|储能|新能源|利润|订单|交付|量产|零部件|兑现|验证|硬件|资本|融资|Optimus|英伟达|具身智能|脑机接口|DDR5|HBM|世界模型|澜起)", all_titles)
keyword_counter = Counter(keywords).most_common(15)

# 爆款标题特征
viral_titles = [r["title"] for r in viral]

# CTR分布
ctr_buckets = {"<1%": 0, "1-3%": 0, "3-5%": 0, "5-10%": 0, ">10%": 0}
for r in data["records"]:
    imp = r.get("impressions", 0)
    vw = r.get("views", 0)
    if imp == 0:
        continue
    ctr = vw / imp * 100
    if ctr < 1:
        ctr_buckets["<1%"] += 1
    elif ctr < 3:
        ctr_buckets["1-3%"] += 1
    elif ctr < 5:
        ctr_buckets["3-5%"] += 1
    elif ctr < 10:
        ctr_buckets["5-10%"] += 1
    else:
        ctr_buckets[">10%"] += 1

data["summary"] = {
    "total_impressions": total_impressions,
    "total_views": total_views,
    "total_likes": total_likes,
    "total_comments": total_comments,
    "total_shares": total_shares,
    "total_articles": n,
    "articles_with_title": len(titles_with_data),
    "articles_without_title": len(titles_no_data),
    "viral_articles_count": len(viral),
    "viral_impressions": viral_impressions,
    "viral_impressions_share_pct": round(viral_share_pct, 2),
    "average_impressions": round(avg_impressions, 2),
    "average_views": round(avg_views, 2),
    "average_ctr": round(avg_ctr * 100, 4),
    "average_likes": round(total_likes / n, 2),
    "average_comments": round(total_comments / n, 4),
    "like_rate": round(total_likes / total_views * 100, 4) if total_views else 0,
    "comment_rate": round(total_comments / total_views * 100, 4) if total_views else 0,
    "ctr_distribution": ctr_buckets,
    "top10_by_views": [{"title": r["title"], "impressions": r.get("impressions", 0), "views": r.get("views", 0), "likes": r.get("likes", 0)} for r in top_views],
    "top10_by_impressions": [{"title": r["title"], "impressions": r.get("impressions", 0), "views": r.get("views", 0)} for r in top_impressions],
    "bottom10_by_impressions": [{"title": r["title"], "impressions": r.get("impressions", 0), "views": r.get("views", 0)} for r in bottom_impressions],
    "viral_articles": [{"title": r["title"], "impressions": r.get("impressions", 0), "views": r.get("views", 0), "likes": r.get("likes", 0), "comments": r.get("comments", 0)} for r in viral],
    "keyword_frequency": [{"keyword": k, "count": v} for k, v in keyword_counter],
    "note": f"采集到{n}篇文章，其中爆款{len(viral)}篇贡献{viral_share_pct:.1f}%展现量"
}

metrics_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Updated {metrics_path}")
print(f"Total articles: {n}")
print(f"Total impressions: {total_impressions:,}")
print(f"Total views: {total_views:,}")
print(f"Total likes: {total_likes}")
print(f"Total comments: {total_comments}")
print(f"Viral articles: {len(viral)} ({viral_share_pct:.1f}% of impressions)")
print(f"Average CTR: {avg_ctr*100:.2f}%")
print(f"Like rate: {total_likes/total_views*100:.2f}%" if total_views else "N/A")
print(f"Top keywords: {keyword_counter[:5]}")
