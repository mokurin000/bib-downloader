import shutil
from datetime import datetime
from pathlib import Path

import bibtexparser
import typer
from bibtexparser.bparser import BibTexParser
from loguru import logger

from . import __version__
from .bibtex import enrich_bibtex_entry
from .pubmed import PubMedLookup

# 创建CLI应用 — 使用callback模式（无子命令，--version为eager选项）
app = typer.Typer(
    name="bibtex-pubmed-enricher",
    help="从BibTeX文件中查找并添加PubMed DOI和PMID",
    add_completion=True,
    pretty_exceptions_show_locals=False,
)

# 全局日志配置（在callback中按需调整级别）
logger.add(
    "logs/bibtex_enrich_{time:YYYY-MM-DD}.log",
    rotation="1 week",
    retention="30 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | {message}",
)


def _version_callback(value: bool):
    """--version 回调"""
    if value:
        typer.echo(f"BibTeX PubMed Enricher v{__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    input_file: str = typer.Argument(
        ..., help="输入的BibTeX文件路径", show_default=False
    ),
    output_file: str | None = typer.Option(
        None, "--output", "-o", help="输出文件路径（默认覆盖原文件）"
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", envvar="NCBI_API_KEY", help="NCBI API密钥"
    ),
    email: str | None = typer.Option(
        None, "--email", envvar="NCBI_EMAIL", help="联系邮箱（NCBI建议提供）"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="强制更新已有DOI/PMID的条目"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="仅显示将进行的修改，不写入文件"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
    version: bool = typer.Option(
        False,
        "--version",
        help="显示版本信息",
        callback=_version_callback,
        is_eager=True,
    ),
):
    """
    从BibTeX文件中查找并添加PubMed DOI和PMID

    示例:

      bibtex-enrich references.bib

      bibtex-enrich references.bib -o enriched.bib --api-key YOUR_KEY

      bibtex-enrich references.bib --dry-run --verbose
    """
    # 如果用户只输入 --version，callback会提前退出，不会执行到这里
    # 处理输入文件路径验证（typer的Argument的exists=True在callback中不生效）
    input_path = Path(input_file)
    if not input_path.exists():
        typer.echo(f"错误: 文件不存在: {input_file}", err=True)
        raise typer.Exit(code=1)

    # 设置日志级别
    if verbose:
        logger.remove()
        logger.add(lambda msg: print(msg, end=""), level="DEBUG")
        logger.add(
            "logs/bibtex_enrich_{time:YYYY-MM-DD}.log",
            level="DEBUG",
            rotation="1 week",
            retention="30 days",
        )

    logger.info(f"BibTeX PubMed Enricher v{__version__}")
    logger.info(f"开始处理文件: {input_file}")

    # 读取BibTeX文件
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            bib_database = bibtexparser.load(
                f, parser=BibTexParser(common_strings=True)
            )
    except Exception as e:
        logger.error(f"读取BibTeX文件失败: {e}")
        raise typer.Exit(code=1)

    entries = bib_database.entries
    logger.info(f"找到 {len(entries)} 个BibTeX条目")

    # 初始化PubMed查找器
    searcher = PubMedLookup(api_key=api_key, email=email)

    # 处理每个条目
    stats = {
        "total": len(entries),
        "found_pmid": 0,
        "found_doi": 0,
        "skipped": 0,
        "failed": 0,
    }

    for i, entry in enumerate(entries, 1):
        logger.info(f"\n[{i}/{len(entries)}] 处理条目: {entry.get('ID', 'unknown')}")

        entry_key = entry.get("ID", f"entry_{i}")

        original_has_pmid = "pmid" in entry
        original_has_doi = "doi" in entry

        enriched_fields = enrich_bibtex_entry(
            entry, entry_key, searcher, force, dry_run
        )

        # 更新统计
        if not original_has_pmid and "pmid" in enriched_fields:
            stats["found_pmid"] += 1
        if not original_has_doi and "doi" in enriched_fields:
            stats["found_doi"] += 1

        # 更新原始条目（跳过特殊字段）
        for key, value in enriched_fields.items():
            if key not in ["id", "entrytype"]:
                entry[key] = value

    # 输出统计信息
    logger.info("\n" + "=" * 50)
    logger.info("统计结果:")
    logger.info(f"  总条目数: {stats['total']}")
    logger.info(f"  新增PMID: {stats['found_pmid']}")
    logger.info(f"  新增DOI: {stats['found_doi']}")
    logger.info(f"  失败/跳过: {stats['failed']}")
    logger.info(f"  总API查询次数: {searcher.query_count}")

    # 写回文件
    if not dry_run:
        output_path = Path(output_file) if output_file else input_path

        # 创建备份（如果覆盖原文件）
        if output_path == input_path and input_path.exists():
            backup_path = input_path.with_suffix(
                f".backup{datetime.now():%Y%m%d_%H%M%S}.bib"
            )
            shutil.copy2(input_path, backup_path)
            logger.info(f"已创建备份: {backup_path}")

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                bibtexparser.dump(bib_database, f)
            logger.success(f"✓ 结果已保存到: {output_path}")
        except Exception as e:
            logger.error(f"保存文件失败: {e}")
            raise typer.Exit(code=1)
    else:
        logger.info("\n✓ Dry run完成，未写入文件")


def entry_point():
    """Console_scripts入口点"""
    app()
