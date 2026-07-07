from loguru import logger

from .pubmed import PubMedLookup


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


def _extract_author_last(author: str) -> str | None:
    """从BibTeX作者字段中提取第一作者的姓氏"""
    if not author:
        return None
    first_author = author.split(" and ")[0]
    name_parts = first_author.split()
    if name_parts:
        return name_parts[-1].strip("{},.")
    return None


def _extract_first_page(pages: str) -> str | None:
    """从页码字符串中提取起始页码"""
    if not pages:
        return None
    if "--" in pages:
        return pages.split("--")[0]
    if "-" in pages:
        return pages.split("-")[0]
    return pages


def enrich_bibtex_entry(
    entry_dict: dict,
    entry_key: str,
    searcher: PubMedLookup,
    force_update: bool = False,
    dry_run: bool = False,
) -> dict:
    """丰富单个BibTeX条目 — 支持DOI-first路径

    决策矩阵:
      Has DOI | Has PMID | Action (no force)            | Action (force)
      --------|----------|------------------------------|-------------------------
      Yes     | Yes      | Skip                         | DOI→PMID + PMID→DOI re-fetch
      Yes     | No       | article_by_doi(doi) → PMID   | Same
      No      | Yes      | article_by_pmid(pmid) → DOI  | Same
      No      | No       | Citation search → PMID → DOI | Same
    """
    fields = clean_bibtex_fields(entry_dict)

    # 检查是否需要处理
    has_doi = bool(fields.get("doi"))
    has_pmid = bool(fields.get("pmid"))

    if has_doi and has_pmid and not force_update:
        logger.debug(f"跳过 {entry_key}: 已有DOI和PMID")
        return fields

    # 提取必要字段（用于citation search fallback）
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

    author_last = _extract_author_last(author)
    first_page = _extract_first_page(pages)
    if author_last:
        logger.debug(f"  第一作者: {author_last}")
    if first_page:
        logger.debug(f"  起始页码: {first_page}")

    pmid = fields.get("pmid")

    # ── Phase 1: 获取PMID ──────────────────────────────────
    if not pmid or force_update:
        # DOI-first path: 如果已有DOI，优先使用article_by_doi直接获取PMID
        if has_doi and (not pmid or force_update):
            logger.info("  通过DOI直接获取文章信息...")
            article_info = searcher.get_article_info_by_doi(fields["doi"])
            if article_info and article_info.get("pmid"):
                pmid = article_info["pmid"]
                if not dry_run:
                    fields["pmid"] = pmid
                logger.success(f"  ✓ 通过DOI找到PMID: {pmid}")
            else:
                logger.warning("  ✗ 通过DOI未找到PMID，尝试citation搜索")

        # Citation search fallback: 仍然没有PMID时使用citation搜索
        if not pmid:
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
        logger.debug(f"  已有PMID: {pmid}")

    # ── Phase 2: 获取DOI ──────────────────────────────────
    if pmid and (not has_doi or force_update):
        logger.info("  获取DOI...")

        # Try DOI cache first (if we fetched via DOI in Phase 1)
        article_info = None
        if has_doi and fields["doi"] in searcher.cache_doi_info:
            article_info = searcher.cache_doi_info[fields["doi"]]
        else:
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
