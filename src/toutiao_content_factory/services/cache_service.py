from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from toutiao_content_factory.models import CacheContext, WorkflowResult


class CacheService:
    # 常量定义
    _MAX_FINGERPRINT_LENGTH = 2400
    _MAX_NORMALIZED_LENGTH = 5000
    CACHE_TTL_SECONDS = 6 * 3600  # 缓存有效期 6 小时
    # 预编译正则表达式
    _WHITESPACE_PATTERN = re.compile(r"\s+")
    _INVALID_CHAR_PATTERN = re.compile(r"[^\w\u4e00-\u9fff%]")

    def __init__(self, cache_dir: Path, similarity_threshold: float, logger: logging.Logger) -> None:
        self.cache_dir = cache_dir
        self.index_path = cache_dir / "index.json"
        self.similarity_threshold = similarity_threshold
        self.logger = logger

    def inspect(self, source_text: str, style_key: str, refresh_cache: bool = False) -> CacheContext:
        normalized = self._normalize_text(source_text)
        cache_key = self._build_cache_key(normalized, style_key)

        if refresh_cache:
            return CacheContext(
                status="miss",
                cache_key=cache_key,
                note="已跳过缓存，强制重新生成。",
            )

        exact_path = self._result_path(cache_key)
        if exact_path.exists():
            if self._is_cache_expired(cache_key):
                self.logger.info("缓存已过期（超过 %d 秒），视为 miss: %s", self.CACHE_TTL_SECONDS, cache_key)
                return CacheContext(
                    status="miss",
                    cache_key=cache_key,
                    note=f"缓存已过期（超过 {self.CACHE_TTL_SECONDS // 3600} 小时），将重新生成。",
                )
            payload = json.loads(exact_path.read_text(encoding="utf-8"))
            cached_result = WorkflowResult.from_dict(payload)
            if cached_result.model_used == "local-draft":
                return CacheContext(
                    status="miss",
                    cache_key=cache_key,
                    note="Detected local-draft cache entry. Regenerating to improve article quality.",
                )
            return CacheContext(
                status="exact_hit",
                cache_key=cache_key,
                matched_key=cache_key,
                similarity=1.0,
                avoid_titles=cached_result.content.recommended_titles or cached_result.content.titles[:5],
                avoid_angle=cached_result.content.article_angle,
                cached_result=cached_result,
                note="命中精确缓存，直接复用历史结果。",
            )

        best_record = self._find_similar_record(normalized, style_key, cache_key)
        if best_record:
            payload = json.loads(self._result_path(best_record["key"]).read_text(encoding="utf-8"))
            cached_result = WorkflowResult.from_dict(payload)
            return CacheContext(
                status="similar_hit",
                cache_key=cache_key,
                matched_key=str(best_record["key"]),
                similarity=float(best_record["similarity"]),
                avoid_titles=cached_result.content.titles[:8],
                avoid_angle=cached_result.content.article_angle,
                note="发现相似历史内容，本次生成会尽量避开重复角度和标题。",
            )

        return CacheContext(
            status="miss",
            cache_key=cache_key,
            note="未命中缓存，将按当前材料生成新内容。",
        )

    def save(self, result: WorkflowResult, source_text: str) -> None:
        payload = result.to_dict()
        self._result_path(result.cache_key).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        normalized = self._normalize_text(source_text)
        index = self._load_index()
        record = {
            "key": result.cache_key,
            "style_key": result.style_key,
            "created_at": result.created_at,
            "created_at_ts": time.time(),
            "fingerprint": normalized[:self._MAX_FINGERPRINT_LENGTH],
            "article_angle": result.content.article_angle,
            "titles": result.content.recommended_titles,
        }

        filtered = [item for item in index if item.get("key") != result.cache_key]
        filtered.append(record)
        self.index_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.info("缓存已写入: %s", self._result_path(result.cache_key))

    def lexical_similarity(self, left_text: str, right_text: str) -> float:
        left = self._normalize_text(left_text)
        right = self._normalize_text(right_text)
        if not left or not right:
            return 0.0
        return SequenceMatcher(None, left[:self._MAX_FINGERPRINT_LENGTH], right[:self._MAX_FINGERPRINT_LENGTH]).ratio()

    def _find_similar_record(self, normalized: str, style_key: str, cache_key: str) -> dict | None:
        index = self._load_index()
        best_record: dict | None = None
        best_similarity = 0.0
        normalized_truncated = normalized[:self._MAX_FINGERPRINT_LENGTH]
        for record in index:
            if record.get("style_key") != style_key or record.get("key") == cache_key:
                continue
            fingerprint = str(record.get("fingerprint", ""))
            if not fingerprint:
                continue
            similarity = SequenceMatcher(None, normalized_truncated, fingerprint[:self._MAX_FINGERPRINT_LENGTH]).ratio()
            if similarity > best_similarity:
                best_similarity = similarity
                best_record = record

        if best_record and best_similarity >= self.similarity_threshold and self._result_path(best_record["key"]).exists():
            best_record["similarity"] = round(best_similarity, 4)
            return best_record
        return None

    def _load_index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.logger.warning("缓存索引损坏，已忽略旧索引。")
            return []
        return payload if isinstance(payload, list) else []

    def _is_cache_expired(self, cache_key: str) -> bool:
        index = self._load_index()
        for record in index:
            if record.get("key") == cache_key:
                created_at_ts = record.get("created_at_ts")
                if created_at_ts is not None:
                    try:
                        age = time.time() - float(created_at_ts)
                        return age > self.CACHE_TTL_SECONDS
                    except (TypeError, ValueError):
                        pass
                # fallback: 从 ISO 格式 created_at 解析
                created_at_str = record.get("created_at", "")
                if created_at_str:
                    try:
                        from datetime import datetime as _dt
                        created_dt = _dt.fromisoformat(str(created_at_str))
                        age = time.time() - created_dt.timestamp()
                        return age > self.CACHE_TTL_SECONDS
                    except (ValueError, TypeError):
                        pass
                # 无时间戳信息，视为未过期
                return False
        # index 中无记录，视为未过期（文件存在即可用）
        return False

    def _result_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.json"

    def _build_cache_key(self, normalized_text: str, style_key: str) -> str:
        digest = hashlib.sha256(f"{style_key}|{normalized_text}".encode("utf-8")).hexdigest()
        return digest[:16]

    def _normalize_text(self, text: str) -> str:
        normalized = self._WHITESPACE_PATTERN.sub("", text)
        normalized = self._INVALID_CHAR_PATTERN.sub("", normalized)
        return normalized[:self._MAX_NORMALIZED_LENGTH]
