#!/usr/bin/env python3
"""精简打包浏览器 profile，只保留登录必需文件。

从两个源目录精简复制到目标目录，再打包为 tar.gz。
"""

import os
import shutil
import sys
import tarfile

# ── 配置 ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(PROJECT_ROOT, ".runtime")
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "browser_profiles_seed.tar.gz")

# 源目录 → 目标目录映射（相对于 PROJECT_ROOT/.runtime/）
PROFILE_MAPPINGS = [
    ("accounts/robot_notes/toutiao_profile", "toutiao_profile"),
    (".runtime/editor_135_profile", "editor_135_profile"),
]

# 排除的目录名（匹配路径中任意层级）
EXCLUDE_DIR_NAMES = {
    "Cache",
    "Code Cache",
    "Service Worker",
}

# 排除的文件扩展名
EXCLUDE_EXTENSIONS = {
    ".log",
    ".pma",
}

# 必须保留的文件（相对于 profile 根目录，统一用 / 分隔）
KEEP_FILES = {
    "Default/Cookies",
    "Default/Network/Cookies",
    "Default/Preferences",
    "Default/Secure Preferences",
    "Default/Login Data",
    "Default/Web Data",
}

# 必须保留的目录前缀（整个目录递归保留，统一用 / 分隔，以 / 结尾）
KEEP_DIRS = {
    "Default/Local Storage/",
    "Default/Session Storage/",
}

# 保留所有路径中包含 "Cookies" 的文件
KEEP_NAME_PATTERN = "Cookies"


def _is_under_keep_dir(rel_path: str) -> bool:
    """判断 rel_path 是否位于某个 KEEP_DIRS 前缀下。"""
    normalized = rel_path.replace("\\", "/")
    for d in KEEP_DIRS:
        if normalized.startswith(d) or normalized == d.rstrip("/"):
            return True
    return False


def _has_cookies_in_name(rel_path: str) -> bool:
    """判断文件名中是否包含 Cookies。"""
    return KEEP_NAME_PATTERN in os.path.basename(rel_path)


def should_keep(rel_path: str) -> bool:
    """判断一个相对路径是否应该保留。"""
    normalized = rel_path.replace("\\", "/")

    # 1. 精确匹配保留文件
    if normalized in KEEP_FILES:
        return True

    # 2. 位于保留目录下
    if _is_under_keep_dir(normalized):
        return True

    # 3. 文件名包含 Cookies
    if _has_cookies_in_name(rel_path):
        return True

    return False


def _is_in_excluded_dir(rel_path: str) -> bool:
    """判断 rel_path 的路径中是否包含被排除的目录名。"""
    parts = rel_path.replace("\\", "/").split("/")
    return bool(set(parts) & EXCLUDE_DIR_NAMES)


def _has_excluded_extension(rel_path: str) -> bool:
    """判断文件扩展名是否在排除列表中。"""
    _, ext = os.path.splitext(rel_path)
    return ext.lower() in EXCLUDE_EXTENSIONS


def fmt_size(n: int) -> str:
    """格式化字节数。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def copy_profile(src_dir: str, dst_dir: str) -> tuple[int, int]:
    """精简复制 profile 目录，返回 (原始大小, 保留大小)。"""
    original_size = 0
    kept_size = 0

    for root, dirs, files in os.walk(src_dir, topdown=True):
        # 剪枝：跳过排除的目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_NAMES]

        for f in files:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, src_dir)

            # 排除特定扩展名
            if _has_excluded_extension(rel_path):
                continue

            # 排除位于排除目录中的文件（双重保险）
            if _is_in_excluded_dir(rel_path):
                continue

            try:
                fsize = os.path.getsize(full_path)
            except OSError:
                continue

            original_size += fsize

            # 只保留符合条件的文件
            if should_keep(rel_path):
                kept_size += fsize
                dst_path = os.path.join(dst_dir, rel_path)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                try:
                    shutil.copy2(full_path, dst_path)
                except (PermissionError, OSError) as exc:
                    print(f"   [WARN] 跳过锁定文件: {rel_path} ({exc})")

    return original_size, kept_size


def main():
    if not os.path.isdir(RUNTIME_DIR):
        print(f"[ERROR] .runtime 目录不存在: {RUNTIME_DIR}")
        sys.exit(1)

    total_original = 0
    total_kept = 0

    print("=" * 60)
    print("浏览器 Profile 精简打包工具")
    print("=" * 60)

    # 阶段1：精简复制
    import tempfile

    for src_rel, dst_rel in PROFILE_MAPPINGS:
        src_path = os.path.join(RUNTIME_DIR, src_rel)
        dst_path = os.path.join(RUNTIME_DIR, dst_rel)

        if not os.path.isdir(src_path):
            print(f"\n[SKIP] 源目录不存在: .runtime/{src_rel}")
            continue

        # 如果源和目标相同，先复制到临时目录再替换，避免删除源
        if os.path.abspath(src_path) == os.path.abspath(dst_path):
            tmp_dir = tempfile.mkdtemp(prefix="profile_pack_", dir=RUNTIME_DIR)
            try:
                print(f"\n📂 复制: .runtime/{src_rel} → 临时目录 → .runtime/{dst_rel}")
                orig, kept = copy_profile(src_path, tmp_dir)
                total_original += orig
                total_kept += kept
                print(f"   原始大小: {fmt_size(orig)}")
                print(f"   保留大小: {fmt_size(kept)}")
                if orig > 0:
                    ratio = kept / orig * 100
                    print(f"   保留比例: {ratio:.1f}%")
                # 替换目标目录
                if os.path.exists(dst_path):
                    shutil.rmtree(dst_path)
                shutil.move(tmp_dir, dst_path)
            except Exception:
                if os.path.isdir(tmp_dir):
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                raise
        else:
            # 目标目录已存在则先删除
            if os.path.exists(dst_path):
                print(f"\n🗑  删除已有目标: .runtime/{dst_rel}")
                shutil.rmtree(dst_path)

            os.makedirs(dst_path, exist_ok=True)

            print(f"\n📂 复制: .runtime/{src_rel} → .runtime/{dst_rel}")
            orig, kept = copy_profile(src_path, dst_path)
            total_original += orig
            total_kept += kept
            print(f"   原始大小: {fmt_size(orig)}")
            print(f"   保留大小: {fmt_size(kept)}")
            if orig > 0:
                ratio = kept / orig * 100
                print(f"   保留比例: {ratio:.1f}%")

    # 阶段2：打包为 tar.gz
    print(f"\n📦 打包为 tar.gz ...")

    # 删除旧的压缩包
    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)

    with tarfile.open(OUTPUT_FILE, "w:gz") as tar:
        for _, dst_rel in PROFILE_MAPPINGS:
            dst_path = os.path.join(RUNTIME_DIR, dst_rel)
            if not os.path.isdir(dst_path):
                continue
            tar.add(dst_path, arcname=dst_rel)

    # 输出结果
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
