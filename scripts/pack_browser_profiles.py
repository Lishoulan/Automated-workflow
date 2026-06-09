#!/usr/bin/env python3
"""精简打包浏览器 profile，只保留登录必需文件。

扫描 .runtime/toutiao_profile 和 .runtime/wechat_profile，
过滤出登录必需的文件/目录，打包为 browser_profiles_seed.tar.gz。
"""

import os
import tarfile
import sys

# ── 配置 ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(PROJECT_ROOT, ".runtime")
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "browser_profiles_seed.tar.gz")

PROFILE_DIRS = [
    "toutiao_profile",
    "wechat_profile",
    "accounts/robot_notes/toutiao_profile",
    "accounts/robot-notes/toutiao_profile",
    "accounts/robot-notes/wechat_profile",
    "accounts/robot-notes/editor_135_profile",
]

# 登录必需的文件（相对于 profile 根目录）
KEEP_FILES = {
    "Local State",
    "FirstLaunchAfterInstallation",
    "Last Version",
    "Variations",
    "Breadcrumbs",
    "Default/Cookies",
    "Default/Cookies-journal",
    "Default/Preferences",
    "Default/Secure Preferences",
    "Default/Login Data",
    "Default/Login Data For Account",
    "Default/Login Data-journal",
    "Default/Web Data",
    "Default/Web Data-journal",
    "Default/LOCK",
    "Default/LOG",
}

# 登录必需的目录前缀（整个目录递归保留）
KEEP_DIRS = {
    "Default/Session Storage/",
    "Default/Network/",
}

# 需要排除的缓存目录名（匹配路径中任意层级）
EXCLUDE_DIR_NAMES = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "GrShaderCache",
    "GraphiteDawnCache",
    "ShaderCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "Crashpad",
    "SmartScreen",
}


def _is_under_keep_dir(rel_path: str) -> bool:
    """判断 rel_path 是否位于某个 KEEP_DIRS 前缀下。"""
    # 统一用 / 分隔
    normalized = rel_path.replace("\\", "/")
    for d in KEEP_DIRS:
        if normalized.startswith(d) or normalized == d.rstrip("/"):
            return True
    return False


def _is_in_excluded_dir(rel_path: str) -> bool:
    """判断 rel_path 的路径中是否包含被排除的目录名。"""
    parts = rel_path.replace("\\", "/").split("/")
    return bool(set(parts) & EXCLUDE_DIR_NAMES)


def should_keep(rel_path: str) -> bool:
    """判断一个相对路径是否应该保留。"""
    normalized = rel_path.replace("\\", "/")

    # 1. 精确匹配文件
    if normalized in KEEP_FILES:
        return True

    # 2. 位于保留目录下
    if _is_under_keep_dir(normalized):
        return True

    return False


def get_dir_size(path: str) -> int:
    """递归计算目录总大小。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def pack_profile(profile_path: str, tar: tarfile.TarFile, prefix: str) -> tuple[int, int]:
    """扫描并打包单个 profile 目录，返回 (原始大小, 保留大小)。"""
    original_size = 0
    kept_size = 0

    for root, dirs, files in os.walk(profile_path, topdown=True):
        # 剪枝：跳过排除的目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_NAMES]

        for f in files:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, profile_path)

            try:
                fsize = os.path.getsize(full_path)
            except OSError:
                continue

            original_size += fsize

            if should_keep(rel_path):
                kept_size += fsize
                arcname = os.path.join(prefix, rel_path).replace("\\", "/")
                try:
                    tar.add(full_path, arcname=arcname)
                except (PermissionError, OSError) as exc:
                    print(f"   [WARN] 跳过锁定文件: {rel_path} ({exc})")

    # 也处理保留目录下的空子目录（Session Storage / Network 可能含子目录）
    for root, dirs, _files in os.walk(profile_path, topdown=True):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_NAMES]
        for d in dirs:
            full_dir = os.path.join(root, d)
            rel_dir = os.path.relpath(full_dir, profile_path)
            if _is_under_keep_dir(rel_dir):
                # 目录本身会被 tar.add 文件时自动包含，无需额外处理
                pass

    return original_size, kept_size


def fmt_size(n: int) -> str:
    """格式化字节数。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main():
    if not os.path.isdir(RUNTIME_DIR):
        print(f"[ERROR] .runtime 目录不存在: {RUNTIME_DIR}")
        sys.exit(1)

    total_original = 0
    total_kept = 0

    print("=" * 60)
    print("浏览器 Profile 精简打包工具")
    print("=" * 60)

    with tarfile.open(OUTPUT_FILE, "w:gz") as tar:
        for profile_name in PROFILE_DIRS:
            profile_path = os.path.join(RUNTIME_DIR, profile_name)
            if not os.path.isdir(profile_path):
                print(f"\n[SKIP] {profile_name} 目录不存在，跳过")
                continue

            print(f"\n📂 扫描: .runtime/{profile_name}")
            orig, kept = pack_profile(profile_path, tar, prefix=profile_name)
            total_original += orig
            total_kept += kept
            print(f"   原始大小: {fmt_size(orig)}")
            print(f"   保留大小: {fmt_size(kept)}")
            if orig > 0:
                ratio = kept / orig * 100
                print(f"   保留比例: {ratio:.1f}%")

    # 输出压缩包大小
    gz_size = os.path.getsize(OUTPUT_FILE) if os.path.exists(OUTPUT_FILE) else 0

    print("\n" + "=" * 60)
    print("📊 汇总")
    print("=" * 60)
    print(f"   原始总大小:   {fmt_size(total_original)}")
    print(f"   保留总大小:   {fmt_size(total_kept)}")
    print(f"   压缩包大小:   {fmt_size(gz_size)}")
    if total_original > 0:
        print(f"   精简比例:     {total_kept / total_original * 100:.1f}%")
        print(f"   压缩比例:     {gz_size / total_original * 100:.1f}%")
    print(f"\n✅ 已保存到: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
