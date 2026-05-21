#!/usr/bin/env python3
"""
从 https://swarmskills.openjiuwen.com/ 下载所有 skills 到本地。

用法:
    python tools/download.py                  # 下载所有 skills（保留 zip）
    python tools/download.py --extract        # 下载并解压所有 skills
    python tools/download.py --limit 5        # 只下载前 5 个
    python tools/download.py --output ./my_skills  # 指定输出目录
    python tools/download.py --no-download    # 只获取索引，不下载文件
"""

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
import io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import URLError


BASE_URL = "https://swarmskills.openjiuwen.com/api/v1"
DEFAULT_OUTPUT_DIR = "skills"
PAGE_SIZE = 50  # 每页获取的数量


def fetch_json(url: str) -> dict:
    """从 URL 获取 JSON 数据"""
    req = Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "SkillMash-Downloader/1.0")
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_all_skills() -> list[dict]:
    """获取所有 skills 的列表"""
    all_skills = []
    page = 1
    
    print(f"正在获取 skills 列表...")
    
    while True:
        url = f"{BASE_URL}/plugins?page={page}&page_size={PAGE_SIZE}"
        try:
            data = fetch_json(url)
        except (URLError, Exception) as e:
            print(f"  警告: 获取第 {page} 页失败: {e}")
            break
        
        if data.get("code") != 200:
            print(f"  警告: API 返回错误: {data.get('message')}")
            break
        
        items = data.get("data", {}).get("items", [])
        if not items:
            break
        
        all_skills.extend(items)
        total = data.get("data", {}).get("total", 0)
        print(f"  已获取 {len(all_skills)}/{total} 个 skills")
        
        if len(all_skills) >= total:
            break
        
        page += 1
        time.sleep(0.2)  # 避免请求过快
    
    print(f"共获取 {len(all_skills)} 个 skills\n")
    return all_skills


def get_artifact_info(asset_id: str) -> dict | None:
    """获取单个 skill 的下载信息"""
    url = f"{BASE_URL}/artifacts/{asset_id}"
    try:
        data = fetch_json(url)
        if data.get("code") == 200:
            return data.get("data")
    except (URLError, Exception) as e:
        print(f"  警告: 获取 artifact {asset_id} 失败: {e}")
    return None


def download_file(url: str, dest_path: Path, expected_size: int = 0, expected_sha256: str = "") -> bool:
    """下载文件到指定路径"""
    req = Request(url)
    req.add_header("User-Agent", "SkillMash-Downloader/1.0")
    
    try:
        with urlopen(req, timeout=120) as response:
            content = response.read()
            
            # 验证 SHA256
            if expected_sha256:
                actual_sha256 = hashlib.sha256(content).hexdigest()
                if actual_sha256 != expected_sha256:
                    print(f"  SHA256 校验失败!")
                    print(f"    期望: {expected_sha256}")
                    print(f"    实际: {actual_sha256}")
                    return False
            
            # 写入文件
            dest_path.write_bytes(content)
            
            # 验证大小
            if expected_size and len(content) != expected_size:
                print(f"  警告: 文件大小不匹配 (期望 {expected_size}, 实际 {len(content)})")
            
            return True
    except (URLError, Exception) as e:
        print(f"  下载失败: {e}")
        return False


def extract_zip(zip_path: Path, extract_dir: Path) -> bool:
    """解压 zip 文件到指定目录"""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # 获取 zip 中的顶级目录
            top_dirs = set()
            for name in zf.namelist():
                # 获取第一级目录
                parts = name.split('/')
                if parts[0]:
                    top_dirs.add(parts[0])
            
            # 如果 zip 只有一个顶级目录，则解压到该目录
            if len(top_dirs) == 1:
                extract_path = extract_dir / list(top_dirs)[0]
            else:
                # 否则解压到以 skill 名称命名的目录
                skill_name = zip_path.stem.rsplit('_', 1)[0] if '_' in zip_path.stem else zip_path.stem
                extract_path = extract_dir / skill_name
            
            extract_path.mkdir(parents=True, exist_ok=True)
            zf.extractall(extract_path)
        
        # 删除 zip 文件
        zip_path.unlink()
        return True
    except (zipfile.BadZipFile, Exception) as e:
        print(f"  解压失败: {e}")
        return False


def process_skill(skill: dict, output_dir: Path, skip_existing: bool = True, extract: bool = False) -> dict:
    """处理单个 skill: 获取下载链接并下载"""
    name = skill.get("name", "unknown")
    version = skill.get("latest_version", "unknown")
    asset_id = skill.get("asset_id", "")
    display_name = skill.get("display_name", name)
    
    result = {
        "name": name,
        "display_name": display_name,
        "asset_id": asset_id,
        "version": version,
        "category": skill.get("category_name", ""),
        "publisher": skill.get("publisher_name", ""),
        "short_desc": skill.get("short_desc", ""),
        "detail_desc": skill.get("detail_desc", ""),
        "tags": skill.get("tags", []),
        "install_count": skill.get("install_count", 0),
        "view_count": skill.get("view_count", 0),
        "like_count": skill.get("like_count", 0),
        "star_count": skill.get("star_count", 0),
        "average_rating": skill.get("average_rating", 0),
        "file_path": None,
        "extracted_path": None,
        "file_size": 0,
        "checksum_sha256": "",
        "download_url": None,
        "status": "skipped"
    }
    
    # 获取下载信息
    artifact = get_artifact_info(asset_id)
    if not artifact:
        result["status"] = "error_no_artifact"
        return result
    
    download_url = artifact.get("download_url", "")
    file_size = artifact.get("file_size", 0)
    checksum_sha256 = artifact.get("checksum_sha256", "")
    
    result["download_url"] = download_url
    result["file_size"] = file_size
    result["checksum_sha256"] = checksum_sha256
    
    # 确定文件名
    zip_name = f"{name}_{version}.zip"
    zip_path = output_dir / zip_name
    
    # 检查是否已存在（zip 文件或解压目录）
    if skip_existing:
        if zip_path.exists():
            result["file_path"] = str(zip_path)
            result["status"] = "exists"
            return result
        # 检查是否已解压
        extracted_path = output_dir / name
        if extracted_path.exists() and extracted_path.is_dir():
            result["file_path"] = None
            result["extracted_path"] = str(extracted_path)
            result["status"] = "exists_extracted"
            return result
    
    # 下载文件
    print(f"  下载: {name} v{version} ({file_size} bytes)")
    success = download_file(download_url, zip_path, file_size, checksum_sha256)
    
    if not success:
        result["status"] = "error_download"
        return result
    
    result["file_path"] = str(zip_path)
    
    # 解压
    if extract:
        print(f"  解压: {name}")
        extract_success = extract_zip(zip_path, output_dir)
        if extract_success:
            extracted_path = output_dir / name
            result["extracted_path"] = str(extracted_path)
            result["status"] = "extracted"
        else:
            result["status"] = "error_extract"
    else:
        result["status"] = "downloaded"
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description="从 swarmskills.openjiuwen.com 下载所有 skills"
    )
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录 (默认: {DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=0,
        help="限制下载数量 (0 = 不限制)"
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="只获取索引，不下载文件"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新下载已存在的文件"
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=4,
        help="并发下载线程数 (默认: 4)"
    )
    parser.add_argument(
        "--category",
        default=None,
        help="只下载指定分类的 skills"
    )
    parser.add_argument(
        "--extract", "-e",
        action="store_true",
        help="下载后自动解压 zip 文件（解压后删除 zip）"
    )
    
    args = parser.parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 获取所有 skills
    skills = get_all_skills()
    
    if not skills:
        print("未获取到任何 skills")
        sys.exit(1)
    
    # 按分类过滤
    if args.category:
        skills = [s for s in skills if s.get("category_name") == args.category]
        print(f"按分类过滤 '{args.category}': {len(skills)} 个 skills\n")
    
    # 限制数量
    if args.limit > 0:
        skills = skills[:args.limit]
        print(f"限制数量: {len(skills)} 个 skills\n")
    
    # 保存完整索引
    index_path = output_dir / "skills_index.json"
    
    if args.no_download:
        # 只保存索引
        print("只获取索引模式，不下载文件")
        index_data = {
            "total": len(skills),
            "skills": skills
        }
        index_path.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"索引已保存到: {index_path}")
        return
    
    # 下载 skills
    print(f"输出目录: {output_dir.absolute()}")
    print(f"并发线程: {args.workers}")
    print(f"跳过已存在: {'是' if not args.force else '否'}")
    print(f"自动解压: {'是' if args.extract else '否'}")
    print(f"开始下载 {len(skills)} 个 skills...\n")
    
    results = []
    stats = {"downloaded": 0, "extracted": 0, "exists": 0, "exists_extracted": 0, "error": 0, "skipped": 0}
    
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_skill, skill, output_dir, not args.force, args.extract): skill
            for skill in skills
        }
        
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            
            status = result["status"]
            if status == "downloaded":
                stats["downloaded"] += 1
            elif status == "extracted":
                stats["extracted"] += 1
            elif status == "exists":
                stats["exists"] += 1
            elif status == "exists_extracted":
                stats["exists_extracted"] += 1
            elif status.startswith("error"):
                stats["error"] += 1
            else:
                stats["skipped"] += 1
            
            if i % 10 == 0 or i == len(skills):
                print(f"  进度: {i}/{len(skills)} (成功: {stats['downloaded'] + stats['extracted']}, 已存在: {stats['exists'] + stats['exists_extracted']}, 失败: {stats['error']})")
    
    # 保存索引
    index_data = {
        "total": len(skills),
        "stats": stats,
        "skills": results
    }
    index_path.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # 打印统计
    print(f"\n{'='*50}")
    print(f"下载完成!")
    print(f"  总计: {len(skills)} 个 skills")
    print(f"  新下载: {stats['downloaded']}")
    print(f"  新解压: {stats['extracted']}")
    print(f"  已存在 (zip): {stats['exists']}")
    print(f"  已存在 (已解压): {stats['exists_extracted']}")
    print(f"  失败: {stats['error']}")
    print(f"  索引: {index_path}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
