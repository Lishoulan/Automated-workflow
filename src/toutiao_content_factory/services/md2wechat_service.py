from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from toutiao_content_factory.config import AppSettings


class Md2WechatService:
    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

    @property
    def enabled(self) -> bool:
        wechat = self.settings.wechat
        backend = str(wechat.layout_backend or "").strip().lower()
        return bool(wechat.md2wechat_enabled or backend == "md2wechat")

    @property
    def available(self) -> bool:
        wechat = self.settings.wechat
        return self.enabled and (bool(str(wechat.md2wechat_api_key).strip()) or bool(self._resolve_cli_path()))

    def convert(self, markdown: str) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("md2wechat is not enabled. Set WECHAT_MD2WECHAT_ENABLED=1 or WECHAT_LAYOUT_BACKEND=md2wechat.")

        cleaned_markdown = str(markdown or "").strip()
        if not cleaned_markdown:
            raise RuntimeError("md2wechat conversion received empty markdown.")

        errors: list[str] = []
        wechat = self.settings.wechat
        api_key = str(wechat.md2wechat_api_key or "").strip()
        cli_path = self._resolve_cli_path()

        if api_key:
            try:
                return self._convert_via_api(cleaned_markdown)
            except Exception as exc:
                errors.append(f"api: {exc}")
                self.logger.warning("md2wechat API conversion failed, will try local CLI if available: %s", exc)

        if cli_path:
            try:
                return self._convert_via_cli(cleaned_markdown, cli_path=cli_path)
            except Exception as exc:
                errors.append(f"cli: {exc}")
                self.logger.warning("md2wechat CLI conversion failed: %s", exc)

        if errors:
            raise RuntimeError(" | ".join(errors))
        raise RuntimeError("md2wechat is enabled but neither API key nor local CLI is available.")

    def _convert_via_api(self, cleaned_markdown: str) -> dict[str, Any]:
        wechat = self.settings.wechat
        base_url = str(wechat.md2wechat_base_url or "").rstrip("/")
        if not base_url:
            raise RuntimeError("MD2WECHAT_BASE_URL is empty.")

        payload = {
            "markdown": cleaned_markdown,
            "theme": wechat.md2wechat_theme,
            "fontSize": wechat.md2wechat_font_size,
            "convertVersion": wechat.md2wechat_convert_version,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{base_url}/convert",
            data=data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Md2wechat-API-Key": wechat.md2wechat_api_key,
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=90) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"md2wechat API request failed: {exc.code} {raw}") from exc
        except URLError as exc:
            raise RuntimeError(f"md2wechat API request failed: {exc.reason}") from exc

        response_payload = json.loads(raw or "{}")
        html = self._extract_html(response_payload)
        if not html:
            raise RuntimeError(f"md2wechat returned no html content: {response_payload}")

        return {
            "html": html,
            "provider": "md2wechat-api",
            "request": payload,
            "response": response_payload,
        }

    def _convert_via_cli(self, cleaned_markdown: str, *, cli_path: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="md2wechat_") as temp_dir:
            temp_root = Path(temp_dir)
            markdown_path = temp_root / "article.md"
            markdown_path.write_text(cleaned_markdown, encoding="utf-8")

            attempts = [
                ["convert", str(markdown_path), "--preview", "--json", "--theme", self.settings.wechat.md2wechat_theme],
                ["preview", str(markdown_path), "--json", "--theme", self.settings.wechat.md2wechat_theme],
                ["convert", str(markdown_path), "--preview", "--theme", self.settings.wechat.md2wechat_theme],
                ["preview", str(markdown_path), "--theme", self.settings.wechat.md2wechat_theme],
            ]
            failures: list[str] = []
            for args in attempts:
                command = self._wrap_cli_command(cli_path, args)
                try:
                    completed = subprocess.run(
                        command,
                        cwd=str(temp_root),
                        capture_output=True,
                        text=True,
                        timeout=90,
                        check=False,
                    )
                except Exception as exc:
                    failures.append(f"{args[0]} launch failed: {exc}")
                    continue

                stdout = str(completed.stdout or "")
                stderr = str(completed.stderr or "")
                if completed.returncode != 0:
                    failures.append(f"{' '.join(args)} -> {completed.returncode}: {(stderr or stdout).strip()}")
                    continue

                html = self._extract_cli_html(stdout=stdout, stderr=stderr, temp_root=temp_root)
                if html:
                    return {
                        "html": html,
                        "provider": "md2wechat-cli",
                        "request": {"command": command, "theme": self.settings.wechat.md2wechat_theme},
                        "response": {"stdout": stdout, "stderr": stderr},
                    }
                failures.append(f"{' '.join(args)} returned no html.")

            raise RuntimeError(" ; ".join(failures) if failures else "md2wechat CLI returned no usable output.")

    def _resolve_cli_path(self) -> str:
        configured = str(self.settings.wechat.md2wechat_cli_path or "").strip()
        candidates = [
            configured,
            shutil.which("md2wechat"),
            shutil.which("md2wechat.cmd"),
            str(Path.home() / "AppData" / "Roaming" / "npm" / "md2wechat.cmd"),
            str(Path.home() / "AppData" / "Roaming" / "npm" / "md2wechat"),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            candidate_path = Path(candidate)
            if candidate_path.exists():
                return str(candidate_path)
        return ""

    def _wrap_cli_command(self, cli_path: str, args: list[str]) -> list[str]:
        lowered = cli_path.lower()
        if lowered.endswith(".cmd") or lowered.endswith(".bat"):
            return ["cmd", "/c", cli_path, *args]
        return [cli_path, *args]

    def _extract_cli_html(self, *, stdout: str, stderr: str, temp_root: Path) -> str:
        for blob in (stdout, stderr):
            direct_html = self._extract_direct_html(blob)
            if direct_html:
                return direct_html

        for blob in (stdout, stderr):
            payload = self._extract_json_blob(blob)
            if payload:
                html = self._extract_html(payload)
                if html:
                    return html
                linked_html = self._extract_linked_html(payload, temp_root)
                if linked_html:
                    return linked_html

        linked_html = self._extract_linked_html({"stdout": stdout, "stderr": stderr}, temp_root)
        if linked_html:
            return linked_html

        html_files = sorted(temp_root.glob("*.html"))
        for html_file in html_files:
            try:
                content = html_file.read_text(encoding="utf-8")
            except Exception:
                continue
            if self._extract_direct_html(content):
                return content
        return ""

    def _extract_direct_html(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "<html" in text.lower() or "<section" in text.lower() or "<article" in text.lower():
            return text
        return ""

    def _extract_json_blob(self, value: str) -> dict[str, Any]:
        text = str(value or "").strip()
        if not text:
            return {}
        candidates = [text]
        match = re.search(r"(\{.*\})", text, flags=re.S)
        if match:
            candidates.append(match.group(1))
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    def _extract_linked_html(self, payload: dict[str, Any], temp_root: Path) -> str:
        links: list[str] = []
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, str):
                    links.extend(self._find_html_like_targets(value))
                elif isinstance(value, dict):
                    links.extend(self._find_html_like_targets(json.dumps(value, ensure_ascii=False)))
        for target in links:
            if target.startswith(("http://", "https://")):
                try:
                    with urlopen(target, timeout=20) as response:
                        content = response.read().decode("utf-8", errors="replace")
                except Exception:
                    continue
                if self._extract_direct_html(content):
                    return content
                continue
            file_target = target.removeprefix("file://")
            candidate = Path(file_target)
            if not candidate.is_absolute():
                candidate = temp_root / file_target
            if not candidate.exists():
                continue
            try:
                content = candidate.read_text(encoding="utf-8")
            except Exception:
                continue
            if self._extract_direct_html(content):
                return content
        return ""

    def _find_html_like_targets(self, text: str) -> list[str]:
        value = str(text or "")
        patterns = [
            r"https?://[^\s\"']+",
            r"file://[^\s\"']+",
            r"[A-Za-z]:\\[^\r\n\"']+?\.html",
            r"[^\s\"']+?\.html",
        ]
        matches: list[str] = []
        for pattern in patterns:
            matches.extend(re.findall(pattern, value))
        deduped: list[str] = []
        seen: set[str] = set()
        for item in matches:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _extract_html(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""

        direct_candidates = [
            payload.get("html"),
            payload.get("content"),
            payload.get("data"),
        ]
        for candidate in direct_candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        nested_data = payload.get("data")
        if isinstance(nested_data, dict):
            for key in ("html", "content"):
                candidate = nested_data.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()

        result = payload.get("result")
        if isinstance(result, dict):
            for key in ("html", "content"):
                candidate = result.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return ""
