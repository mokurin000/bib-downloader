#!/usr/bin/env python3
"""
Sci-Hub BibTeX Downloader

从BibTeX文件中读取文献条目，根据标题通过Sci-Hub下载PDF论文。

使用方法:
    python scihub_bib_downloader.py input.bib
    python scihub_bib_downloader.py input.bib --output ./papers
    python scihub_bib_downloader.py input.bib --scihub-url https://sci-hub.ru
"""

import sys
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

import typer
import bibtexparser
from loguru import logger
from scidownl import scihub_download

# 默认Sci-Hub域名
DEFAULT_SCI_HUB_URL = "https://sci-hub.box"

# 配置 loguru
logger.remove()  # 移除默认处理器
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    colorize=True,
)

# 可选：同时输出到文件
logger.add(
    "logs/scihub_download_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="7 days",
    compression="zip",
    level="DEBUG",
    encoding="utf-8",
)

app = typer.Typer(help="从BibTeX文件读取文献并通过Sci-Hub下载PDF")

# 命令行选项
DEFAULT_OUTPUT_DIR = "./downloads"


@dataclass
class PaperEntry:
    """论文条目数据类"""

    title: str
    entry_type: str
    authors: Optional[str] = None
    year: Optional[str] = None
    doi: Optional[str] = None
    key: Optional[str] = None


def parse_bib_file(bib_path: Path) -> List[PaperEntry]:
    """
    解析BibTeX文件，提取论文信息

    Args:
        bib_path: BibTeX文件路径

    Returns:
        PaperEntry对象列表
    """
    if not bib_path.exists():
        logger.error(f"文件不存在: {bib_path}")
        return []

    try:
        with open(bib_path, "r", encoding="utf-8") as f:
            bib_database = bibtexparser.load(f)

        papers = []
        for entry in bib_database.entries:
            # 获取标题
            title = entry.get("title", "")
            if not title:
                # 尝试其他可能的标题字段
                title = entry.get("Title", "")

            if not title:
                logger.warning(f"跳过无标题条目: {entry.get('ID', 'unknown')}")
                continue

            # 清理标题中的花括号和多余空格
            title = title.replace("{", "").replace("}", "").strip()

            paper = PaperEntry(
                title=title,
                entry_type=entry.get("ENTRYTYPE", "unknown"),
                authors=entry.get("author", ""),
                year=entry.get("year", ""),
                doi=entry.get("doi", ""),
                key=entry.get("ID", ""),
            )
            papers.append(paper)
            logger.info(
                f"解析文献: {paper.title[:50]}... ({paper.entry_type}, {paper.year or 'unknown'})"
            )

        logger.success(f"成功解析 {len(papers)} 篇文献")
        return papers

    except Exception as e:
        logger.exception(f"解析BibTeX文件失败: {e}")
        return []


def download_paper(
    paper: PaperEntry,
    output_dir: Path,
    scihub_url: str,
    proxies: Optional[dict] = None,
    timeout: int = 30,
) -> bool:
    """
    下载单篇论文

    Args:
        paper: 论文条目
        output_dir: 输出目录
        scihub_url: Sci-Hub网址
        proxies: 代理设置
        timeout: 下载超时时间(秒)

    Returns:
        是否下载成功
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 生成安全的文件名（使用论文标题，但需要处理特殊字符）
    safe_title = sanitize_filename(paper.title)

    output_path = output_dir / f"{safe_title}.pdf"

    # 如果文件已存在，跳过下载
    if output_path.exists():
        logger.info(f"文件已存在，跳过: {output_path.name}")
        return True

    try:
        # 优先使用DOI下载（如果有），否则使用标题
        if paper.doi:
            logger.info(f"使用DOI下载: {paper.doi} (Sci-Hub: {scihub_url})")
            with logger.catch(message=f"下载失败: {paper.doi}"):
                scihub_download(
                    paper.doi,
                    paper_type="doi",
                    out=str(output_path),
                    proxies=proxies,
                    scihub_url=scihub_url,
                )
        else:
            logger.info(f"使用标题下载: {paper.title[:80]}... (Sci-Hub: {scihub_url})")
            with logger.catch(message=f"下载失败: {paper.title[:50]}"):
                scihub_download(
                    paper.title,
                    paper_type="title",
                    out=str(output_path),
                    proxies=proxies,
                    scihub_url=scihub_url,
                )

        if output_path.exists() and output_path.stat().st_size > 0:
            file_size = output_path.stat().st_size / 1024  # KB
            logger.success(f"✓ 下载成功: {output_path.name} ({file_size:.1f} KB)")
            return True
        else:
            logger.warning(f"✗ 下载失败: {paper.title[:50]}... (文件为空或不存在)")
            return False

    except Exception as e:
        logger.error(f"下载出错: {paper.title[:50]}... - {e}")
        return False


@app.command()
def main(
    bib_file: str = typer.Argument(..., help="BibTeX文件路径"),
    output: str = typer.Option(
        DEFAULT_OUTPUT_DIR, "--output", "-o", help="PDF文件输出目录"
    ),
    scihub_url: str = typer.Option(
        DEFAULT_SCI_HUB_URL,
        "--scihub-url",
        "-s",
        help=f"Sci-Hub网址 (默认: {DEFAULT_SCI_HUB_URL})",
    ),
    proxy: Optional[str] = typer.Option(
        None, "--proxy", "-p", help="代理地址，例如: http=socks5://127.0.0.1:7890"
    ),
    skip_existing: bool = typer.Option(
        True, "--skip-existing/--no-skip-existing", help="跳过已存在的PDF文件"
    ),
    timeout: int = typer.Option(30, "--timeout", "-t", help="下载超时时间(秒)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
):
    """
    从BibTeX文件读取文献，根据标题下载PDF

    示例：

    # 基本使用
    python scihub_bib_downloader.py references.bib

    # 指定输出目录
    python scihub_bib_downloader.py references.bib --output ./my_papers

    # 使用自定义Sci-Hub网址
    python scihub_bib_downloader.py references.bib --scihub-url https://sci-hub.st

    # 使用代理下载
    python scihub_bib_downloader.py references.bib --proxy socks5://127.0.0.1:7890

    # 设置超时时间
    python scihub_bib_downloader.py references.bib --timeout 60
    """

    # 设置日志级别
    if verbose:
        logger.remove()
        logger.add(
            sys.stderr,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level="DEBUG",
            colorize=True,
        )
        logger.debug("详细日志模式已启用")

    # 显示配置信息
    logger.info("=" * 60)
    logger.info("Sci-Hub BibTeX 论文下载器")
    logger.info(f"Bib文件: {bib_file}")
    logger.info(f"输出目录: {output}")
    logger.info(f"Sci-Hub网址: {scihub_url}")
    logger.info(f"代理: {proxy or '未设置'}")
    logger.info(f"超时: {timeout}秒")
    logger.info("=" * 60)

    # 解析代理设置
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}
        logger.debug(f"代理配置: {proxies}")

    # 解析Bib文件
    bib_path = Path(bib_file)
    papers = parse_bib_file(bib_path)

    if not papers:
        logger.error("没有找到有效的文献条目，请检查Bib文件格式")
        raise typer.Exit(code=1)

    # 下载论文
    logger.info(f"\n开始下载 {len(papers)} 篇论文...")

    success_count = 0
    fail_count = 0
    skip_count = 0

    with logger.catch(message="下载过程中出现错误"):
        for i, paper in enumerate(papers, 1):
            logger.info(f"\n[{i}/{len(papers)}] 处理: {paper.title[:60]}...")

            output_path = Path(output) / f"{sanitize_filename(paper.title)}.pdf"

            # 检查是否跳过已存在的文件
            if skip_existing and output_path.exists():
                logger.info(f"文件已存在，跳过: {output_path.name}")
                skip_count += 1
                continue

            # 下载
            if download_paper(paper, Path(output), scihub_url, proxies, timeout):
                success_count += 1
            else:
                fail_count += 1

    # 输出统计信息
    logger.info("\n" + "=" * 60)
    logger.info("下载完成统计:")
    logger.info(f"  总计文献: {len(papers)}")
    logger.success(f"  成功下载: {success_count}")
    logger.info(f"  已跳过:   {skip_count}")
    if fail_count > 0:
        logger.error(f"  失败:     {fail_count}")
    else:
        logger.success(f"  失败:     {fail_count}")
    logger.info("=" * 60)

    if fail_count > 0:
        logger.warning("部分论文下载失败，可能原因:")
        logger.warning("  1. Sci-Hub域名不可用，请尝试更换 --scihub-url")
        logger.warning("  2. 网络连接问题，可尝试使用代理")
        logger.warning("  3. 论文在Sci-Hub上不存在")
        logger.warning("  4. 下载超时，可尝试增加 --timeout 参数")

    # 根据结果返回退出码
    if fail_count > 0 and success_count == 0:
        raise typer.Exit(code=1)
    elif fail_count > 0:
        raise typer.Exit(code=2)
    else:
        raise typer.Exit(code=0)


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除非法字符"""
    # 移除或替换Windows文件名中的非法字符
    illegal_chars = '<>:"/\\|?*'
    for char in illegal_chars:
        filename = filename.replace(char, "_")
    # 限制长度
    if len(filename) > 200:
        filename = filename[:200]
    return filename.strip()


def run():
    app()


if __name__ == "__main__":
    run()
