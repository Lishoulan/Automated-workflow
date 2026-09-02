#!/usr/bin/env python3
"""Pack minimal browser profiles for cloud restore.

This script trims login-critical files from local browser profiles and packs them
into ``browser_profiles_seed.tar.gz`` for GitHub Actions / cloud runners.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
STAGING_DIR = RUNTIME_DIR / "_pack_staging"
DEFAULT_OUTPUT = PROJECT_ROOT / "browser_profiles_seed.tar.gz"

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from toutiao_content_factory.config import load_settings

EXCLUDE_DIR_NAMES = {
    "Cache",
    "Code Cache",
    "Service Worker",
}

EXCLUDE_EXTENSIONS = {
    ".log",
    ".pma",
}

KEEP_FILES = {
    "Default/Cookies",
    "Default/Network/Cookies",
    "Default/Preferences",
    "Default/Secure Preferences",
    "Default/Login Data",
    "Default/Web Data",
}

KEEP_DIR_PREFIXES = {
    "Default/Local Storage/",
    "Default/Session Storage/",
}

KEEP_NAME_PATTERN = "Cookies"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pack minimal browser profiles for cloud restore.")
    parser.add_argument("--account", default="default", help="Account key defined in config.yaml.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output tar.gz path. Default: browser_profiles_seed.tar.gz in project root.",
    )
    return parser.parse_args()


def normalize_rel_path(path: str) -> str:
    return path.replace("\\", "/")


def is_kept_path(rel_path: str) -> bool:
    normalized = normalize_rel_path(rel_path)
    if normalized in KEEP_FILES:
        return True
    if any(normalized.startswith(prefix) for prefix in KEEP_DIR_PREFIXES):
        return True
    return KEEP_NAME_PATTERN in os.path.basename(rel_path)


def is_excluded_path(rel_path: str) -> bool:
    normalized = normalize_rel_path(rel_path)
    if any(part in EXCLUDE_DIR_NAMES for part in normalized.split("/")):
        return True
    return Path(rel_path).suffix.lower() in EXCLUDE_EXTENSIONS


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def trim_profile(src_dir: Path, dst_dir: Path) -> tuple[int, int]:
    original_size = 0
    kept_size = 0

    for root, dirs, files in os.walk(src_dir, topdown=True):
        dirs[:] = [name for name in dirs if name not in EXCLUDE_DIR_NAMES]
        root_path = Path(root)

        for file_name in files:
            src_file = root_path / file_name
            rel_path = os.path.relpath(src_file, src_dir)
            if is_excluded_path(rel_path):
                continue

            try:
                file_size = src_file.stat().st_size
            except OSError:
                continue

            original_size += file_size
            if not is_kept_path(rel_path):
                continue

            kept_size += file_size
            dst_file = dst_dir / rel_path
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_file, dst_file)
            except (PermissionError, OSError) as exc:
                print(f"[WARN] Skip locked file: {rel_path} ({exc})")

    return original_size, kept_size


def resolve_profile_mappings(account: str) -> list[tuple[Path, Path]]:
    settings = load_settings(PROJECT_ROOT, account_key=account)

    toutiao_src = Path(settings.publishing.user_data_dir)
    if not toutiao_src.is_absolute():
        toutiao_src = PROJECT_ROOT / toutiao_src

    wechat_src = settings.account_runtime_dir / "wechat_profile"

    def _mirror_dst(src: Path) -> Path:
        """暂存目录镜像源路径结构,保留账号子目录。

        暂存到 .runtime/_pack_staging/<相对路径>,避免修改本地原始 profile。
        tar arcname 用相对暂存目录的路径,解压后自然回到 .runtime/accounts/{account}/toutiao_profile。
        """
        try:
            rel = src.resolve().relative_to(RUNTIME_DIR.resolve())
            return STAGING_DIR / rel
        except ValueError:
            # 源不在 .runtime 下,回退到扁平命名(default 账号兼容)
            return STAGING_DIR / src.name

    mappings: list[tuple[Path, Path]] = [
        (toutiao_src, _mirror_dst(toutiao_src)),
        (wechat_src, _mirror_dst(wechat_src)),
    ]

    legacy_wechat_src = PROJECT_ROOT / ".runtime" / "wechat_profile"
    if legacy_wechat_src.exists():
        mappings.append((legacy_wechat_src, _mirror_dst(legacy_wechat_src)))

    unique: list[tuple[Path, Path]] = []
    seen_targets: set[Path] = set()
    for src, dst in mappings:
        if dst in seen_targets:
            continue
        seen_targets.add(dst)
        unique.append((src, dst))
    return unique


def trim_and_stage_profile(src_dir: Path, dst_dir: Path) -> tuple[int, int]:
    if src_dir.resolve() == dst_dir.resolve():
        temp_dir = Path(tempfile.mkdtemp(prefix="profile_pack_", dir=RUNTIME_DIR))
        try:
            original_size, kept_size = trim_profile(src_dir, temp_dir)
            if dst_dir.exists():
                shutil.rmtree(dst_dir)
            shutil.move(str(temp_dir), str(dst_dir))
            return original_size, kept_size
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    return trim_profile(src_dir, dst_dir)


def pack_profiles(mappings: list[tuple[Path, Path]], output_file: Path) -> None:
    if output_file.exists():
        output_file.unlink()

    with tarfile.open(output_file, "w:gz") as tar:
        for _, dst_dir in mappings:
            if dst_dir.exists():
                # arcname 保留相对 STAGING_DIR 的路径,确保解压后账号子目录结构正确
                try:
                    arcname = dst_dir.resolve().relative_to(STAGING_DIR.resolve())
                    arcname_str = str(arcname)
                except ValueError:
                    arcname_str = dst_dir.name
                tar.add(dst_dir, arcname=arcname_str)


def main() -> int:
    args = parse_args()
    output_file = Path(args.output)
    if not output_file.is_absolute():
        output_file = PROJECT_ROOT / output_file

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    mappings = resolve_profile_mappings(args.account)
    total_original = 0
    total_kept = 0
    packed_targets: list[tuple[Path, Path]] = []

    print("=" * 60)
    print("Minimal browser profile packer")
    print("=" * 60)
    print(f"Account: {args.account}")

    for src_dir, dst_dir in mappings:
        rel_src = src_dir.relative_to(PROJECT_ROOT) if src_dir.is_relative_to(PROJECT_ROOT) else src_dir
        rel_dst = dst_dir.relative_to(PROJECT_ROOT) if dst_dir.is_relative_to(PROJECT_ROOT) else dst_dir

        if not src_dir.exists():
            print(f"[SKIP] Missing source profile: {rel_src}")
            continue

        print(f"\n[PACK] {rel_src} -> {rel_dst}")
        original_size, kept_size = trim_and_stage_profile(src_dir, dst_dir)
        total_original += original_size
        total_kept += kept_size
        packed_targets.append((src_dir, dst_dir))

        print(f"  original: {format_size(original_size)}")
        print(f"  kept:     {format_size(kept_size)}")
        if original_size > 0:
            print(f"  ratio:    {kept_size / original_size * 100:.1f}%")

    if not packed_targets:
        print("[ERROR] No valid profiles were found to pack.")
        return 1

    print(f"\n[ARCHIVE] {output_file}")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    pack_profiles(packed_targets, output_file)

    archive_size = output_file.stat().st_size if output_file.exists() else 0
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Original total: {format_size(total_original)}")
    print(f"Kept total:     {format_size(total_kept)}")
    print(f"Archive size:   {format_size(archive_size)}")
    print(f"Saved to:       {output_file}")

    # 清理暂存目录
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR, ignore_errors=True)
        print(f"[CLEAN] Removed staging dir: {STAGING_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
