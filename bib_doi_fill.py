#!/usr/bin/env python3
"""
BibTeX到PubMed DOI/PMID查找工具 - metapub版本
使用metapub库简化PubMed API交互
"""

import re
import os
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

import typer
import bibtexparser
from bibtexparser.bparser import BibTexParser
from loguru import logger
from metapub import PubMedFetcher

# 创建CLI应用
app = typer.Typer(
    name="bibtex-pubmed-enricher",
    help="从BibTeX文件中查找并添加PubMed DOI和PMID",
    add_completion=True,
)

# 配置日志
logger.add(
    "logs/bibtex_enrich_{time:YYYY-MM-DD}.log",
    rotation="1 week",
    retention="30 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | {message}",
)


@dataclass
class BibEntry:
    """BibTeX条目数据结构（简化版，使用bibtexparser后不再需要完整解析逻辑）"""

    key: str
    entry_type: str
    fields: dict[str, str]


class PubMedLookup:
    """使用metapub的PubMed查找类"""

    def __init__(self, api_key: str | None = None, email: str | None = None):
        """
        初始化PubMed查找器

        Args:
            api_key: NCBI API密钥（提高速率限制）
            email: 联系邮箱（NCBI建议提供）
        """
        if api_key:
            os.environ["NCBI_API_KEY"] = api_key
            logger.info(f"使用NCBI API Key (前缀: {api_key[:8]}...)")

        if email:
            os.environ["NCBI_EMAIL"] = email
            logger.info(f"设置联系邮箱: {email}")

        self.fetch = PubMedFetcher()
        self.query_count = 0
        self.cache_pmid_info: dict[str, dict] = {}

    def _get_journal_abbrev(self, journal: str) -> str:
        """尝试将期刊名转换为NLM缩写格式"""
        # 常见期刊映射
        journal_map = {
            "diabetologia": "Diabetologia",
            "nature": "Nature",
            "science": "Science",
            "cell": "Cell",
            "new england journal of medicine": "N Engl J Med",
            "the lancet": "Lancet",
            "lancet": "Lancet",
            "british medical journal": "BMJ",
            "bmj": "BMJ",
            "jama": "JAMA",
            "plos one": "PLoS One",
            "plos medicine": "PLoS Med",
            "proceedings of the national academy of sciences": "Proc Natl Acad Sci USA",
            "pnas": "Proc Natl Acad Sci USA",
            "nature communications": "Nat Commun",
            "scientific reports": "Sci Rep",
            "cell reports": "Cell Rep",
        }

        journal_lower = journal.lower().strip()
        for key, abbrev in journal_map.items():
            if key in journal_lower:
                logger.debug(f"期刊映射: {journal} -> {abbrev}")
                return abbrev

        return journal

    def search_pmid_by_citation(
        self,
        title: str,
        journal: str,
        year: str,
        volume: str | None = None,
        first_page: str | None = None,
        author: str | None = None,
    ) -> str | None:
        """通过文章信息搜索PMID"""
        query_params = {"jtitle": self._get_journal_abbrev(journal), "year": year}

        if volume:
            query_params["volume"] = volume
        if first_page:
            query_params["spage"] = first_page
        if author:
            query_params["aulast"] = author

        try:
            pmids = self.fetch.pmids_for_citation(**query_params)
            self.query_count += 1
            logger.debug(f"pmids_for_citation查询: {query_params} -> {pmids}")

            if pmids:
                for pmid in pmids[:3]:
                    if self._verify_title_match(pmid, title):
                        logger.info(f"通过citation找到PMID: {pmid}")
                        return pmid

            # Fallback到标题搜索
            return self._search_by_title(title, journal, year)

        except Exception as e:
            logger.warning(f"citation查询失败 {query_params}: {e}")
            return None

    def _search_by_title(self, title: str, journal: str, year: str) -> str | None:
        """使用标题进行搜索"""
        short_title = re.split(r"[.:]", title)[0][:60].strip()
        query = f'"{short_title}"[Title] AND {journal}[Journal] AND {year}[dp]'

        try:
            pmids = self.fetch.pmids_for_query(query, retmax=5)
            self.query_count += 1
            logger.debug(f"标题搜索查询: {query} -> {pmids}")

            if pmids:
                for pmid in pmids[:3]:
                    if self._verify_title_match(pmid, title):
                        logger.info(f"通过标题找到PMID: {pmid}")
                        return pmid

            # 更宽松的搜索
            keywords = " ".join(title.split()[:5])
            query = f"{keywords}[Title] AND {year}[dp]"
            pmids = self.fetch.pmids_for_query(query, retmax=5)
            self.query_count += 1
            logger.debug(f"宽松搜索查询: {query} -> {pmids}")

            if pmids:
                for pmid in pmids[:3]:
                    if self._verify_title_match(pmid, title):
                        logger.info(f"通过宽松搜索找到PMID: {pmid}")
                        return pmid

        except Exception as e:
            logger.warning(f"标题搜索失败: {e}")

        return None

    def _verify_title_match(self, pmid: str, expected_title: str) -> bool:
        """验证PMID对应的标题是否匹配"""
        try:
            article = self.fetch.article_by_pmid(pmid)
            self.query_count += 1

            actual_title = article.title
            clean_expected = re.sub(r"[^\w\s]", "", expected_title.lower())
            clean_actual = re.sub(r"[^\w\s]", "", actual_title.lower())

            # 缓存文章信息供后续使用
            if pmid not in self.cache_pmid_info:
                self.cache_pmid_info[pmid] = {
                    "doi": article.doi,
                    "title": actual_title,
                    "journal": article.journal,
                    "year": article.year,
                    "volume": getattr(article, "volume", ""),
                    "issue": getattr(article, "issue", ""),
                    "pages": getattr(article, "pages", ""),
                }

            # 标题匹配检查
            match = (
                clean_actual.startswith(clean_expected[:30])
                or clean_expected.startswith(clean_actual[:30])
                or clean_expected in clean_actual
                or clean_actual in clean_expected
            )

            if match:
                logger.debug(f"标题匹配成功: {actual_title[:50]}...")
            else:
                logger.debug(
                    f"标题不匹配: 期望={expected_title[:50]}... 实际={actual_title[:50]}..."
                )

            return match

        except Exception as e:
            logger.warning(f"标题验证失败 (PMID:{pmid}): {e}")
            return False

    def get_article_info_by_pmid(self, pmid: str) -> dict | None:
        """通过PMID获取完整的文章信息（从缓存或API）"""
        if pmid in self.cache_pmid_info:
            return self.cache_pmid_info[pmid]

        try:
            article = self.fetch.article_by_pmid(pmid)
            self.query_count += 1

            info = {
                "pmid": article.pmid,
                "doi": article.doi,
                "title": article.title,
                "journal": article.journal,
                "year": article.year,
                "volume": getattr(article, "volume", ""),
                "issue": getattr(article, "issue", ""),
                "pages": getattr(article, "pages", ""),
                "authors": article.authors,
                "abstract": getattr(article, "abstract", ""),
            }

            self.cache_pmid_info[pmid] = info
            return info

        except Exception as e:
            logger.error(f"获取文章信息失败 (PMID:{pmid}): {e}")
            return None


def clean_bibtex_fields(entry: dict) -> dict:
    """清理和标准化BibTeX字段"""
    cleaned = {}
    for key, value in entry.items():
        # 移除多余的换行和空格
        if isinstance(value, str):
            value = " ".join(value.split())
            # 移除字段值外层的花括号
            value = value.strip("{}")
        cleaned[key.lower()] = value
    return cleaned


def enrich_bibtex_entry(
    entry_dict: dict,
    entry_key: str,
    searcher: PubMedLookup,
    force_update: bool = False,
    dry_run: bool = False,
) -> dict:
    """丰富单个BibTeX条目"""
    fields = clean_bibtex_fields(entry_dict)

    # 检查是否需要处理
    has_doi = "doi" in fields and fields["doi"]
    has_pmid = "pmid" in fields and fields["pmid"]

    if has_doi and has_pmid and not force_update:
        logger.debug(f"跳过 {entry_key}: 已有DOI和PMID")
        return fields

    # 提取必要字段
    title = fields.get("title", "")
    journal = fields.get("journal", "")
    year = fields.get("year", "")
    volume = fields.get("volume", "")
    pages = fields.get("pages", "")
    author = fields.get("author", "")

    if not title or not journal or not year:
        logger.warning(f"{entry_key}: 缺少必要信息 (title/journal/year)，跳过")
        return fields

    logger.info(f"处理: {entry_key}")
    logger.debug(f"  标题: {title[:80]}...")
    logger.debug(f"  期刊: {journal}, {year}")

    # 提取作者姓氏
    author_last = None
    if author:
        first_author = author.split(" and ")[0]
        name_parts = first_author.split()
        if name_parts:
            author_last = name_parts[-1].strip("{},.")
            logger.debug(f"  第一作者: {author_last}")

    # 提取起始页码
    first_page = None
    if pages:
        if "--" in pages:
            first_page = pages.split("--")[0]
        elif "-" in pages:
            first_page = pages.split("-")[0]
        else:
            first_page = pages
        logger.debug(f"  起始页码: {first_page}")

    # 查找PMID
    if not has_pmid or force_update:
        logger.info("  搜索PMID...")
        pmid = searcher.search_pmid_by_citation(
            title=title,
            journal=journal,
            year=year,
            volume=volume,
            first_page=first_page,
            author=author_last,
        )

        if pmid and not dry_run:
            fields["pmid"] = pmid
            logger.success(f"  ✓ 找到PMID: {pmid}")
        elif pmid:
            logger.success(f"  ✓ 找到PMID: {pmid} (dry-run)")
        else:
            logger.warning("  ✗ 未找到PMID")
    else:
        pmid = fields.get("pmid")
        logger.debug(f"  已有PMID: {pmid}")

    # 获取DOI
    if pmid and (not has_doi or force_update):
        logger.info("  获取DOI...")
        article_info = searcher.get_article_info_by_pmid(pmid)
        if article_info and article_info.get("doi"):
            doi = article_info["doi"]
            if not dry_run:
                fields["doi"] = doi
            logger.success(f"  ✓ 找到DOI: {doi}")
        else:
            logger.warning("  ✗ 未找到DOI")

    logger.debug(f"  当前API查询次数: {searcher.query_count}")
    return fields


@app.command()
def enrich(
    input_file: str = typer.Argument(..., help="输入的BibTeX文件路径", exists=True),
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
):
    """
    从BibTeX文件中查找并添加PubMed DOI和PMID

    示例:
      bibtex-enrich references.bib
      bibtex-enrich references.bib -o enriched.bib --api-key YOUR_KEY
      bibtex-enrich references.bib --dry-run --verbose
    """
    # 设置日志级别
    if verbose:
        logger.remove()
        logger.add(lambda msg: print(msg, end=""), level="DEBUG")
        logger.add("bibtex_enrich_{time:YYYY-MM-DD}.log", level="DEBUG")
    else:
        logger.info("使用 --verbose 查看详细日志")

    logger.info(f"开始处理文件: {input_file}")

    # 读取BibTeX文件
    input_path = Path(input_file)
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

        # 保存原始的ID和ENTRYTYPE
        entry_key = entry.get("ID", f"entry_{i}")

        # 丰富条目
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
            import shutil

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


@app.command()
def version():
    """显示版本信息"""
    typer.echo("BibTeX PubMed Enricher v2.0.0")
    typer.echo("使用 metapub, bibtexparser, typer, loguru")


def main():
    app()


if __name__ == "__main__":
    main()
