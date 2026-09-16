from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import TopicCandidate, WorkflowResult
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.automation import _humanize_source
from toutiao_content_factory.services.schema_guard_service import SchemaGuardService
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
from toutiao_content_factory.services.monetization_service import MonetizationService
from toutiao_content_factory.services.publish_service import PublishService
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.agents.base import BaseAgent


@dataclass
class ReviewBundle:
    created_at: str = ""
    style_key: str = ""
    style_label: str = ""
    titles: list[str] = field(default_factory=list)
    recommended_titles: list[str] = field(default_factory=list)
    summary: str = ""
    article: str = ""
    comment_prompt: str = ""
    tags: list[str] = field(default_factory=list)
    recommended_topics: list[str] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)
    validation_notes: list[str] = field(default_factory=list)
    revision_suggestions: list[str] = field(default_factory=list)
    score_snapshot: dict[str, int | str] = field(default_factory=dict)


class ReviewWorkflowService:
    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.publish_service = PublishService(settings, logger)
        self.growth_ops_service = GrowthOpsService(settings, logger)
        self.monetization_service = MonetizationService(settings, logger)
        self._text_cleaner = BaseAgent.__new__(BaseAgent)
        self._text_cleaner.settings = settings

    def resolve_run_dir(self, run_dir: str | None = None) -> Path:
        if run_dir:
            path = Path(run_dir).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Run directory not found: {path}")
            return path

        runs_root = self.settings.output_dir / self.settings.automation.archive_dirname
        if not runs_root.exists():
            raise FileNotFoundError(f"Runs root not found: {runs_root}")

        candidates = [item for item in runs_root.iterdir() if item.is_dir()]
        if not candidates:
            raise FileNotFoundError(f"No archived runs found under: {runs_root}")
        return max(candidates, key=lambda item: item.stat().st_mtime)

    def resolve_latest_approved_run(self, unpublished_only: bool = False, today_only: bool = False) -> Path:
        runs_root = self.settings.output_dir / self.settings.automation.archive_dirname
        if not runs_root.exists():
            raise FileNotFoundError(f"Runs root not found: {runs_root}")

        published_run_dirs = self._load_published_run_dirs() if unpublished_only else set()
        candidates: list[Path] = []
        today_prefix = datetime.now().strftime("%Y%m%d")
        for item in runs_root.iterdir():
            if not item.is_dir():
                continue
            if today_only and not item.name.startswith(today_prefix):
                continue
            status = self._load_status(item)
            if str(status.get("status", "")) != "approved":
                continue
            if unpublished_only and str(item.resolve()) in published_run_dirs:
                continue
            candidates.append(item)

        if not candidates:
            suffix = " unpublished approved" if unpublished_only else " approved"
            if today_only:
                suffix = " today's" + suffix
            raise FileNotFoundError(f"No{suffix} runs found under: {runs_root}")
        return max(candidates, key=lambda item: item.stat().st_mtime)

    def resolve_latest_pending_run(self) -> Path:
        runs_root = self.settings.output_dir / self.settings.automation.archive_dirname
        if not runs_root.exists():
            raise FileNotFoundError(f"Runs root not found: {runs_root}")

        candidates: list[Path] = []
        for item in runs_root.iterdir():
            if not item.is_dir():
                continue
            status = self._load_status(item)
            if str(status.get("status", "")) == "pending":
                candidates.append(item)

        if not candidates:
            raise FileNotFoundError(f"No pending runs found under: {runs_root}")
        return max(candidates, key=lambda item: item.stat().st_mtime)

    def prepare_review(self, run_dir: Path) -> dict[str, Path]:
        bundle, selected_topic = self._load_bundle(run_dir)
        status = self._load_status(run_dir)
        now = datetime.now().isoformat(timespec="seconds")
        recommended_action = self._recommended_action(bundle)

        if not status:
            status = {
                "status": "pending",
                "recommended_action": recommended_action,
                "selected_title": "",
                "selected_title_index": 0,
                "reviewer_notes": "",
                "created_at": now,
            }

        status["updated_at"] = now
        status["recommended_action"] = recommended_action
        status["selected_topic_title"] = selected_topic.title if selected_topic is not None else ""
        status["score_snapshot"] = bundle.score_snapshot
        status["publish_recommendation"] = bundle.score_snapshot.get("publish_recommendation", "manual_review")
        status["source_run_dir"] = str(run_dir)
        status["review_packet"] = str(run_dir / "review_packet.md")

        packet_path = run_dir / "review_packet.md"
        status_path = run_dir / "review_status.json"
        packet_path.write_text(self._build_review_packet(run_dir, bundle, selected_topic, status), encoding="utf-8")
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.info("Review packet prepared: %s", run_dir)
        return {
            "review_packet": packet_path,
            "review_status": status_path,
        }

    def approve_review(
        self,
        run_dir: Path,
        selected_title: str | None = None,
        title_index: int | None = None,
        reviewer_notes: str | None = None,
        auto_publish: bool = False,
    ) -> dict[str, Path]:
        bundle, selected_topic = self._load_bundle(run_dir)
        status = self._load_status(run_dir)
        if not status:
            self.prepare_review(run_dir)
            status = self._load_status(run_dir)

        title_candidates = self._title_candidates(bundle)
        chosen_title, chosen_index = self._resolve_title(title_candidates, selected_title, title_index)
        publish_dir = run_dir / self.settings.review.publish_dirname
        publish_dir.mkdir(parents=True, exist_ok=True)

        package_path = publish_dir / "toutiao_publish_package.md"
        article_path = publish_dir / "article.md"
        title_path = publish_dir / "title.txt"
        summary_path = publish_dir / "summary.txt"
        comment_path = publish_dir / "comment_prompt.txt"
        tags_path = publish_dir / "tags.txt"
        meta_path = publish_dir / "publish_meta.json"

        clean_article = clean_article_text(
            self._text_cleaner.strip_prompt_leakage(bundle.article).strip(),
            title_candidates=[chosen_title, *bundle.recommended_titles, *bundle.titles],
        )
        clean_article = self.monetization_service.append_cta(clean_article, platform_key="default").strip()
        # 头条公域发布包不含私域引流CTA；微信渠道在wechat_layout_engine._strip_trailing_cta中也会剥离CTA
        # 此处剥离确保文件层面不泄漏CTA，即使止血模式下不经过_publish_to_toutiao
        clean_article = self.publish_service._strip_cta_for_toutiao(clean_article).strip()
        clean_title = self.publish_service._clip_title(self._text_cleaner.strip_prompt_leakage(chosen_title).strip())
        clean_summary = self._text_cleaner.strip_prompt_leakage(bundle.summary).strip()
        clean_comment = self._text_cleaner.strip_prompt_leakage(bundle.comment_prompt).strip()

        article_path.write_text(clean_article + "\n", encoding="utf-8")
        title_path.write_text(clean_title + "\n", encoding="utf-8")
        summary_path.write_text(clean_summary + "\n", encoding="utf-8")
        comment_path.write_text(clean_comment + "\n", encoding="utf-8")
        tags_path.write_text(" ".join(bundle.tags).strip() + "\n", encoding="utf-8")
        package_path.write_text(self._build_publish_package(bundle, clean_title), encoding="utf-8")

        meta_payload = {
            "approved_at": datetime.now().isoformat(timespec="seconds"),
            "selected_title": clean_title,
            "selected_title_index": chosen_index,
            "summary": clean_summary,
            "tags": bundle.tags,
            "recommended_topics": bundle.recommended_topics,
            "comment_prompt": clean_comment,
            "style_key": bundle.style_key,
            "style_label": bundle.style_label,
            "score_snapshot": bundle.score_snapshot,
            "publish_recommendation": bundle.score_snapshot.get("publish_recommendation", "manual_review"),
            "selected_topic": selected_topic.__dict__ if selected_topic is not None else None,
            "source_run_dir": str(run_dir),
            "validation_notes": bundle.validation_notes,
        }
        selected_topic_payload = selected_topic.__dict__ if selected_topic is not None else {}
        topic_title_text = str(selected_topic_payload.get("title", ""))
        content_intent = str(selected_topic_payload.get("commercial_intent", "")).strip() or "medium"
        content_mix_value = str(selected_topic_payload.get("content_mix", "")).strip() or "professional"
        cta_variant_value = str(selected_topic_payload.get("cta_variant", "")).strip()
        cta_source_code_value = str(selected_topic_payload.get("cta_source_code", "")).strip()
        title_variant_value = str(selected_topic_payload.get("title_variant", "")).strip()
        cover_variant_value = str(selected_topic_payload.get("cover_variant", "")).strip()
        platform_variant_value = str(selected_topic_payload.get("platform_variant", "")).strip()

        experiment_tags = self.growth_ops_service.build_experiment_tags(
            title=clean_title,
            article=clean_article,
            summary=clean_summary,
            topic_title=topic_title_text,
            platform=self.settings.platform,
            recommended_topics=bundle.recommended_topics,
            raw_tags=bundle.tags,
            commercial_intent=content_intent,
            content_mix=content_mix_value,
            cta_variant=cta_variant_value,
            cta_source_code=cta_source_code_value,
            title_variant=title_variant_value,
            cover_variant=cover_variant_value,
            platform_variant=platform_variant_value,
        )
        experiment_tags_path = publish_dir / "experiment_tags.json"
        experiment_tags_path.write_text(json.dumps(experiment_tags, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_payload["experiment_tags"] = experiment_tags
        SchemaGuardService.validate_publish_meta(meta_payload)
        meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        status.update(
            {
                "status": "approved",
                "recommended_action": self._recommended_action(bundle),
                "selected_title": clean_title,
                "selected_title_index": chosen_index,
                "reviewer_notes": reviewer_notes or status.get("reviewer_notes", ""),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "publish_dir": str(publish_dir),
                "publish_recommendation": bundle.score_snapshot.get("publish_recommendation", "manual_review"),
                "experiment_tags_path": str(experiment_tags_path),
            }
        )
        status_path = run_dir / "review_status.json"
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.info("Review approved and publish package exported: %s", publish_dir)

        result = {
            "review_status": status_path,
            "publish_package": package_path,
            "publish_article": article_path,
            "publish_title": title_path,
            "publish_summary": summary_path,
            "publish_comment": comment_path,
            "publish_tags": tags_path,
            "publish_meta": meta_path,
            "experiment_tags": experiment_tags_path,
        }
        if auto_publish:
            result.update(self.publish_service.publish_run(run_dir))
        return result

    def select_auto_title(self, run_dir: Path) -> dict[str, object]:
        """Pick the best title that passes the final title-level quality gate.

        The score model ranks candidates, but it does not guarantee that the
        selected title is compliant after generation. Keep this gate next to
        title resolution so both scheduled and manual callers can inspect the
        same candidate list and diagnostics.

        ⚠️ 2026-09-16：以前这里取的是**第一个**通过闸门的候选。配上
        `title_agent` 把硬编码兜底模板**塞在候选列表最前面**，等于让模板必然胜出
        （云端 9-16 发布的正是兜底模板「工信部推动机器人80%：具身智能如何实测」）。
        现在改为：先按闸门筛掉不合格的（语义不变），再在**通过的**里面按
        `title_click_preference` 选点击吸引力最高的，同分时保持原顺序。
        排序键来自实测 CTR 分布，绝不会让不合格标题复活。
        """
        bundle, selected_topic = self._load_bundle(run_dir)
        candidates = self._title_candidates(bundle)
        checker = ArticleQualityChecker()
        evaluations: list[dict[str, object]] = []
        publishing = self.settings.publishing
        duplicate_guard_enabled = (
            bool(getattr(publishing, "enabled", False))
            and not bool(getattr(publishing, "draft_only", False))
            and not bool(getattr(publishing, "allow_duplicate_topics", False))
        )
        topic_duplicate = False
        if duplicate_guard_enabled and selected_topic is not None:
            topic_duplicate = self.publish_service._was_topic_published(
                str(getattr(selected_topic, "title", "")),
                str(getattr(selected_topic, "url", "")),
            )

        passed: list[tuple[int, int, str]] = []
        for index, candidate in enumerate(candidates, start=1):
            title = self.publish_service._clip_title(
                self._text_cleaner.strip_prompt_leakage(candidate).strip()
            )
            result = checker._check_title(title, bundle.article)
            issues = [str(item) for item in result.get("issues", []) if str(item).strip()]
            if duplicate_guard_enabled:
                if topic_duplicate:
                    issues.append("选题与近7天已发布选题重复")
                if self.publish_service._was_article_title_published_recently(title):
                    issues.append("标题与近7天已发布文章标题重复或高度相似")
                if self.publish_service._blocked_by_template_dedup(title):
                    issues.append("标题复用了近7天已发布的高频模板")
            evaluation = {
                "index": index,
                "title": title,
                "passed": not issues,
                "issues": issues,
                "suggestions": [str(item) for item in result.get("suggestions", [])],
            }
            evaluations.append(evaluation)
            if not issues:
                passed.append((-checker.title_click_preference(title), index, title))

        if passed:
            passed.sort(key=lambda item: (item[0], item[1]))
            best_preference, best_index, best_title = passed[0]
            self.logger.info(
                "Auto title selection: %d/%d candidates passed the gate; picked index=%s "
                "(preference=%s) %s",
                len(passed),
                len(evaluations),
                best_index,
                -best_preference,
                best_title,
            )
            self._log_title_skeleton_collapse(candidates)
            return {
                "selected_title": best_title,
                "selected_title_index": best_index,
                "evaluations": evaluations,
                "passed_count": len(passed),
            }

        return {
            "selected_title": "",
            "selected_title_index": 0,
            "evaluations": evaluations,
            "passed_count": 0,
            "reason": "No candidate title passed the final title quality gate.",
        }

    def reject_review(self, run_dir: Path, reviewer_notes: str | None = None) -> dict[str, Path]:
        bundle, _selected_topic = self._load_bundle(run_dir)
        status = self._load_status(run_dir)
        if not status:
            self.prepare_review(run_dir)
            status = self._load_status(run_dir)

        status.update(
            {
                "status": "rejected",
                "recommended_action": self._recommended_action(bundle),
                "reviewer_notes": reviewer_notes or "Manual review rejected this run. Revise before publishing.",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        status_path = run_dir / "review_status.json"
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.info("Review rejected: %s", run_dir)
        return {"review_status": status_path}

    def mark_skipped_review(self, run_dir: Path, reviewer_notes: str | None = None) -> dict[str, Path]:
        bundle, _selected_topic = self._load_bundle(run_dir)
        status = self._load_status(run_dir)
        if not status:
            self.prepare_review(run_dir)
            status = self._load_status(run_dir)

        status.update(
            {
                "status": "skipped",
                "recommended_action": self._recommended_action(bundle),
                "reviewer_notes": reviewer_notes or "Skipped by the daily automation fallback logic.",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        status_path = run_dir / "review_status.json"
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.info("Review skipped by automation: %s", run_dir)
        return {"review_status": status_path}

    def publish_run(self, run_dir: Path, *, declare_first_release: bool = True) -> dict[str, Path]:
        return self.publish_service.publish_run(run_dir, declare_first_release=declare_first_release)

    def publish_weitoutiao(self, run_dir: Path, *, draft_only: bool = False) -> dict[str, Path]:
        return self.publish_service.publish_weitoutiao(run_dir, draft_only=draft_only)

    def _load_published_run_dirs(self) -> set[str]:
        history_path = self.settings.output_dir / self.settings.publishing.history_filename
        if not history_path.exists():
            return set()
        try:
            payload = json.loads(history_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return set()

        result: set[str] = set()
        if not isinstance(payload, list):
            return result
        for item in payload:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip()
            if status not in PublishService.SUCCESS_HISTORY_STATUSES:
                continue
            run_dir = str(item.get("run_dir", "")).strip()
            if not run_dir:
                continue
            try:
                result.add(str(Path(run_dir).resolve()))
            except Exception:
                result.add(run_dir)
        return result

    def _load_workflow(self, run_dir: Path) -> WorkflowResult:
        workflow_path = run_dir / "workflow_result.json"
        if not workflow_path.exists():
            raise FileNotFoundError(f"Structured workflow result not found: {workflow_path}")
        payload = json.loads(workflow_path.read_text(encoding="utf-8"))
        return WorkflowResult.from_dict(payload)

    def _load_workflow_optional(self, run_dir: Path) -> WorkflowResult | None:
        try:
            return self._load_workflow(run_dir)
        except FileNotFoundError:
            return None

    def _log_title_skeleton_collapse(self, candidates: list[str]) -> None:
        """记录「模型标题塌缩成同一个骨架」的情况。

        2026-09-16 实测：13 个候选里 11 个都是「灵巧手产业：…」。提示词里
        第 19 条明令禁止填空式模板，模型仍然照做 —— 这类塌缩是「标题看起来
        有 20 个候选、实际只有一个思路」的根因，也解释了为什么排序层最后只能
        在两个都不理想的形态里挑。这里只做**观测**（不改行为），
        方便按真实数据迭代提示词，而不是凭感觉改。
        """
        if not candidates:
            return
        skeletons: dict[str, int] = {}
        for title in candidates:
            text = (title or "").strip()
            if not text:
                continue
            head = text.split("：", 1)[0].split("，", 1)[0].split(",", 1)[0][:12]
            if head:
                skeletons[head] = skeletons.get(head, 0) + 1
        if not skeletons:
            return
        head, count = max(skeletons.items(), key=lambda item: item[1])
        if count >= 3 and count * 2 >= len(candidates):
            self.logger.warning(
                "Title skeleton collapse: %d/%d candidates share the leading skeleton 「%s」. "
                "The candidate list looks diverse but carries one idea; consider tightening the "
                "title prompt before tuning the gate.",
                count,
                len(candidates),
                head,
            )

    def _load_bundle(self, run_dir: Path) -> tuple[ReviewBundle, TopicCandidate | None]:
        workflow = self._load_workflow_optional(run_dir)
        selected_topic = self._load_selected_topic(run_dir)
        if workflow is not None:
            return self._bundle_from_workflow(workflow), selected_topic
        return self._bundle_from_legacy_files(run_dir), selected_topic

    def _load_selected_topic(self, run_dir: Path) -> TopicCandidate | None:
        topic_path = run_dir / "selected_topic.json"
        if not topic_path.exists():
            return None
        payload = json.loads(topic_path.read_text(encoding="utf-8"))
        topic_payload = payload.get("selected_topic")
        if not isinstance(topic_payload, dict):
            return None
        return TopicCandidate.from_dict(topic_payload)

    def _load_status(self, run_dir: Path) -> dict[str, object]:
        status_path = run_dir / "review_status.json"
        if not status_path.exists():
            return {}
        return json.loads(status_path.read_text(encoding="utf-8"))

    def _recommended_action(self, bundle: ReviewBundle) -> str:
        overall_score = int(bundle.score_snapshot.get("overall_score", 0))
        ai_risk = int(bundle.score_snapshot.get("ai_generated_risk", 100))
        validation_score = int(bundle.score_snapshot.get("validation_score", 0))
        publish_readiness = int(bundle.score_snapshot.get("publish_readiness_score", 0))
        is_hot_topic = bool(bundle.score_snapshot.get("is_hot_topic", 0))
        if self.settings.review.qualifies_for_auto_publish(
            overall_score=overall_score,
            ai_risk=ai_risk,
            validation_score=validation_score,
            publish_readiness_score=publish_readiness,
            is_hot_topic=is_hot_topic,
        ):
            return "approve_after_light_check"
        return "revise_before_publish"

    def _bundle_from_workflow(self, workflow: WorkflowResult) -> ReviewBundle:
        return ReviewBundle(
            created_at=workflow.created_at,
            style_key=workflow.style_key,
            style_label=workflow.style_label,
            titles=list(workflow.content.titles),
            recommended_titles=list(workflow.content.recommended_titles),
            summary=workflow.content.summary,
            article=clean_article_text(
                workflow.content.polished_article or workflow.content.article,
                title_candidates=list(workflow.content.recommended_titles) + list(workflow.content.titles),
            ),
            comment_prompt=workflow.content.comment_prompt,
            tags=list(workflow.content.tags),
            recommended_topics=list(workflow.content.recommended_topics),
            review_notes=list(workflow.content.review_notes),
            validation_notes=list(workflow.content.validation_notes),
            revision_suggestions=list(workflow.content_score.revision_suggestions),
            score_snapshot={
                "overall_score": workflow.content_score.overall_score,
                "title_attraction": workflow.content_score.title_attraction,
                "information_density": workflow.content_score.information_density,
                "save_potential": workflow.content_score.save_potential,
                "completion_rate_potential": workflow.content_score.completion_rate_potential,
                "ai_generated_risk": workflow.content_score.ai_generated_risk,
                "publish_readiness_score": workflow.content_score.publish_readiness_score,
                "validation_score": workflow.content.validation_score,
                "publish_recommendation": workflow.content_score.publish_recommendation,
                "is_hot_topic": 1 if bool(getattr(workflow.data_report, "is_hot_topic", False)) else 0,
            },
        )

    def _bundle_from_legacy_files(self, run_dir: Path) -> ReviewBundle:
        score_payload = self._load_score_payload(run_dir)
        article_sections = self._parse_article_sections(run_dir / "editorial_packet.md")
        title_payload = self._parse_titles_file(run_dir / "titles.txt")
        tags = article_sections.get("头条标签", "").split()
        recommended_topics = self._parse_bullet_lines(article_sections.get("推荐话题", ""))
        review_notes = self._parse_bullet_lines(article_sections.get("发布前检查", ""))
        validation_notes = list(score_payload.get("validation_notes", []))
        revision_suggestions = list(score_payload.get("revision_suggestions", []))
        scores = score_payload.get("scores", {})

        return ReviewBundle(
            created_at=str(score_payload.get("generated_at", "")),
            style_key=str(score_payload.get("style_key", "")),
            style_label=str(score_payload.get("style_label", "")),
            titles=title_payload["all_titles"],
            recommended_titles=title_payload["recommended_titles"],
            summary=article_sections.get("摘要", "").strip(),
            article=clean_article_text(self._read_text(run_dir / "article.md").strip()),
            comment_prompt=article_sections.get("评论区互动", "").strip(),
            tags=tags,
            recommended_topics=recommended_topics,
            review_notes=review_notes,
            validation_notes=validation_notes,
            revision_suggestions=revision_suggestions,
            score_snapshot={
                "overall_score": int(scores.get("overall_score", 0)),
                "title_attraction": int(scores.get("title_attraction", 0)),
                "information_density": int(scores.get("information_density", 0)),
                "save_potential": int(scores.get("save_potential", 0)),
                "completion_rate_potential": int(scores.get("completion_rate_potential", 0)),
                "ai_generated_risk": int(scores.get("ai_generated_risk", 0)),
                "publish_readiness_score": int(scores.get("publish_readiness_score", 0)),
                "validation_score": int(scores.get("validation_score", 0)),
                "publish_recommendation": str(score_payload.get("publish_recommendation", "manual_review")),
            },
        )

    def _load_score_payload(self, run_dir: Path) -> dict[str, object]:
        path = run_dir / "content_score.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def _parse_article_sections(self, path: Path) -> dict[str, str]:
        text = self._read_text(path)
        if not text:
            return {}
        result: dict[str, str] = {}
        current_title: str | None = None
        buffer: list[str] = []
        for line in text.splitlines():
            if line.startswith("# "):
                if current_title is not None:
                    result[current_title] = "\n".join(buffer).strip()
                current_title = line[2:].strip()
                buffer = []
            else:
                buffer.append(line)
        if current_title is not None:
            result[current_title] = "\n".join(buffer).strip()
        return result

    def _parse_titles_file(self, path: Path) -> dict[str, list[str]]:
        text = self._read_text(path)
        if not text:
            return {"all_titles": [], "recommended_titles": []}
        recommended_marker = "推荐标题："
        if recommended_marker in text:
            all_part, recommended_part = text.split(recommended_marker, 1)
        else:
            all_part, recommended_part = text, ""
        all_part = all_part.replace("全部标题：", "", 1).strip()
        return {
            "all_titles": self._parse_numbered_lines(all_part),
            "recommended_titles": self._parse_numbered_lines(recommended_part),
        }

    def _parse_numbered_lines(self, text: str) -> list[str]:
        result: list[str] = []
        for line in text.splitlines():
            match = re.match(r"^\d+\.\s*(.+)$", line.strip())
            if match:
                result.append(match.group(1).strip())
        return result

    def _parse_bullet_lines(self, text: str) -> list[str]:
        result: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                result.append(stripped[2:].strip())
        return result

    def _build_review_packet(
        self,
        run_dir: Path,
        bundle: ReviewBundle,
        selected_topic: TopicCandidate | None,
        status: dict[str, object],
    ) -> str:
        topic_block = ""
        if selected_topic is not None:
            topic_block = (
                "## 选题信息\n\n"
                f"- 标题：{selected_topic.title}\n"
                f"- 来源：{_humanize_source(selected_topic.source)}\n"
                f"- 发布时间：{selected_topic.published_at or '未知'}\n"
                f"- 原文链接：{selected_topic.url}\n"
                f"- 热度总分：{selected_topic.total_score}\n"
                f"- 标题潜力分：{selected_topic.title_potential_score}\n"
                f"- 标题打法赛道：{selected_topic.title_playbook_track or '未识别'}\n"
                f"- 标题抓手：{'、'.join(selected_topic.title_potential_reasons) or '无'}\n\n"
            )

        recommended_titles = "\n".join(
            f"{index}. {title}" for index, title in enumerate(self._title_candidates(bundle), start=1)
        ) or "1. 暂无标题"
        review_notes = "\n".join(f"- {item}" for item in bundle.review_notes) or "- 无"
        validation_notes = "\n".join(f"- {item}" for item in bundle.validation_notes) or "- 无"
        revision_suggestions = "\n".join(f"- {item}" for item in bundle.revision_suggestions) or "- 无"

        return (
            "# 发布前审核包\n\n"
            f"- 归档目录：{run_dir}\n"
            f"- 当前状态：{status.get('status', 'pending')}\n"
            f"- 建议动作：{status.get('recommended_action', 'manual_review')}\n"
            f"- 内容生成时间：{bundle.created_at}\n"
            f"- 审核更新时间：{status.get('updated_at', '')}\n\n"
            f"{topic_block}"
            "## 推荐标题候选\n\n"
            f"{recommended_titles}\n\n"
            "## 评分概览\n\n"
            f"- 综合得分：{bundle.score_snapshot.get('overall_score', 0)}\n"
            f"- 标题吸引力：{bundle.score_snapshot.get('title_attraction', 0)}\n"
            f"- 信息密度：{bundle.score_snapshot.get('information_density', 0)}\n"
            f"- 收藏潜力：{bundle.score_snapshot.get('save_potential', 0)}\n"
            f"- 完读率潜力：{bundle.score_snapshot.get('completion_rate_potential', 0)}\n"
            f"- AI 痕迹风险：{bundle.score_snapshot.get('ai_generated_risk', 0)}\n"
            f"- 正文校验分：{bundle.score_snapshot.get('validation_score', 0)}\n"
            f"- 发布就绪分：{bundle.score_snapshot.get('publish_readiness_score', 0)}\n"
            f"- 发布建议：{bundle.score_snapshot.get('publish_recommendation', 'manual_review')}\n\n"
            "## 审核关注点\n\n"
            f"{review_notes}\n\n"
            "## 正文校验备注\n\n"
            f"{validation_notes}\n\n"
            "## 建议修改项\n\n"
            f"{revision_suggestions}\n\n"
            "## 审核操作命令\n\n"
            "```powershell\n"
            f'python main.py --approve-run --run-dir "{run_dir}" --selected-title-index 1\n'
            f'python main.py --approve-run --run-dir "{run_dir}" --selected-title-index 1 --auto-publish\n'
            f'python main.py --reject-run --run-dir "{run_dir}" --review-notes "说明驳回原因"\n'
            "```\n"
        )

    def _title_candidates(self, bundle: ReviewBundle) -> list[str]:
        merged = bundle.recommended_titles + bundle.titles
        result: list[str] = []
        seen: set[str] = set()
        for item in merged:
            title = item.strip()
            if not title or title in seen:
                continue
            seen.add(title)
            result.append(title)
        return result

    def _resolve_title(
        self,
        candidates: list[str],
        selected_title: str | None,
        title_index: int | None,
    ) -> tuple[str, int]:
        if selected_title:
            normalized = selected_title.strip()
            if normalized not in candidates:
                candidates = [normalized] + candidates
                return normalized, 1
            return normalized, candidates.index(normalized) + 1

        if title_index is not None:
            if title_index < 1 or title_index > len(candidates):
                raise ValueError(f"--selected-title-index must be between 1 and {len(candidates)}.")
            return candidates[title_index - 1], title_index

        if not candidates:
            raise ValueError("No candidate titles available for this run.")
        return candidates[0], 1

    def _build_publish_package(self, bundle: ReviewBundle, title: str) -> str:
        tags = " ".join(bundle.tags) or "无"
        topics = "\n".join(f"- {item}" for item in bundle.recommended_topics) or "- 无"
        validation_notes = "\n".join(f"- {item}" for item in bundle.validation_notes) or "- 无"
        return (
            f"# {title}\n\n"
            "## 摘要\n\n"
            f"{bundle.summary.strip()}\n\n"
            "## 标签\n\n"
            f"{tags}\n\n"
            "## 推荐话题\n\n"
            f"{topics}\n\n"
            "## 正文\n\n"
            f"{bundle.article.strip()}\n\n"
            "## 评论区互动\n\n"
            f"{bundle.comment_prompt.strip()}\n\n"
            "## 正文校验备注\n\n"
            f"{validation_notes}\n"
        )
