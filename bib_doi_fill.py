#!/usr/bin/env python3
"""
BibTeX到PubMed DOI/PMID查找工具 - metapub版本
使用metapub库简化PubMed API交互
"""

import re
import os
import argparse
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

# 设置NCBI API Key（可选，提高速率限制）
# export NCBI_API_KEY="your_api_key_here"
# 或在代码中设置: os.environ['NCBI_API_KEY'] = "your_key"

from metapub import PubMedFetcher


@dataclass
class BibEntry:
    """BibTeX条目数据结构"""

    key: str
    entry_type: str
    fields: Dict[str, str]
    raw_text: str = ""


class BibTeXParser:
    """BibTeX解析器"""

    @staticmethod
    def parse_file(filepath: Path) -> List[BibEntry]:
        """解析BibTeX文件"""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # 匹配BibTeX条目: @type{key, ...}
        pattern = r"@(\w+)\{([^,]+),([^@]+?)\n\}"

        entries = []
        for match in re.finditer(pattern, content, re.DOTALL):
            entry_type = match.group(1)
            key = match.group(2).strip()
            fields_text = match.group(3)

            fields = BibTeXParser._parse_fields(fields_text)

            entries.append(
                BibEntry(
                    key=key,
                    entry_type=entry_type,
                    fields=fields,
                    raw_text=match.group(0),
                )
            )

        return entries

    @staticmethod
    def _parse_fields(fields_text: str) -> Dict[str, str]:
        """解析BibTeX字段"""
        fields = {}

        # 匹配 field = {value} 或 field = "value"
        field_pattern = r'(\w+)\s*=\s*[{"]([^"}]+)[}"](?=\s*,|\s*$)'

        for match in re.finditer(field_pattern, fields_text):
            field_name = match.group(1).lower()
            field_value = match.group(2).strip()
            fields[field_name] = field_value

        return fields

    @staticmethod
    def write_file(filepath: Path, entries: List[BibEntry]) -> None:
        """将条目写回BibTeX文件"""
        with open(filepath, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(f"@{entry.entry_type}{{{entry.key},\n")

                # 优先显示的重要字段顺序
                field_order = [
                    "title",
                    "author",
                    "journal",
                    "year",
                    "volume",
                    "number",
                    "pages",
                    "doi",
                    "pmid",
                ]

                for field in field_order:
                    if field in entry.fields:
                        value = entry.fields[field]
                        f.write(f"  {field} = {{{value}}},\n")

                # 写入其他字段
                for field, value in entry.fields.items():
                    if field not in field_order:
                        f.write(f"  {field} = {{{value}}},\n")

                f.write("}\n\n")


class PubMedLookup:
    """使用metapub的PubMed查找类"""

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化PubMed查找器

        Args:
            api_key: NCBI API密钥（提高速率限制）
        """
        if api_key:
            os.environ["NCBI_API_KEY"] = api_key

        self.fetch = PubMedFetcher()

        # 用于统计查询次数
        self.query_count = 0

    def _get_journal_abbrev(self, journal: str) -> str:
        """
        尝试将期刊名转换为NLM缩写格式
        metapub的pmids_for_citation方法要求使用NLM标题缩写
        """
        # 常见期刊映射（可扩展）
        journal_map = {
            "diabetologia": "Diabetologia",
            "nature": "Nature",
            "science": "Science",
            "cell": "Cell",
            "new england journal of medicine": "N Engl J Med",
            "the lancet": "Lancet",
            "british medical journal": "BMJ",
            "jama": "JAMA",
            "plos one": "PLoS One",
            "proceedings of the national academy of sciences": "Proc Natl Acad Sci USA",
        }

        journal_lower = journal.lower().strip()
        for key, abbrev in journal_map.items():
            if key in journal_lower:
                return abbrev

        return journal

    def search_pmid_by_citation(
        self,
        title: str,
        journal: str,
        year: str,
        volume: Optional[str] = None,
        first_page: Optional[str] = None,
        author: Optional[str] = None,
    ) -> Optional[str]:
        """
        通过文章信息搜索PMID

        使用metapub的pmids_for_citation方法

        Args:
            title: 文章标题（用于验证）
            journal: 期刊名称
            year: 发表年份
            volume: 卷号（可选）
            first_page: 起始页码（可选）
            author: 作者姓氏（可选）

        Returns:
            PMID字符串，未找到返回None
        """
        # 构建查询参数
        query_params = {"jtitle": self._get_journal_abbrev(journal), "year": year}

        if volume:
            query_params["volume"] = volume
        if first_page:
            query_params["spage"] = first_page
        if author:
            query_params["aulast"] = author

        # 使用metapub的citation查找
        try:
            pmids = self.fetch.pmids_for_citation(**query_params)
            self.query_count += 1

            if pmids:
                # 验证标题是否匹配
                for pmid in pmids[:3]:
                    if self._verify_title_match(pmid, title):
                        return pmid

            # 如果精确查找失败，尝试使用标题搜索
            return self._search_by_title(title, journal, year)

        except Exception as e:
            print(f"  metapub查询失败: {e}")
            return None

    def _search_by_title(self, title: str, journal: str, year: str) -> Optional[str]:
        """使用标题进行搜索（fallback方法）"""
        # 构建查询字符串
        # 取标题前50个字符或第一个句号之前的内容
        short_title = re.split(r"[.:]", title)[0][:60]
        query = f'"{short_title}"[Title] AND {journal}[Journal] AND {year}[dp]'

        try:
            pmids = self.fetch.pmids_for_query(query, retmax=5)
            self.query_count += 1

            if pmids:
                for pmid in pmids[:3]:
                    if self._verify_title_match(pmid, title):
                        return pmid

            # 更宽松的搜索：只使用标题关键词
            keywords = " ".join(title.split()[:5])
            query = f"{keywords}[Title] AND {year}[dp]"
            pmids = self.fetch.pmids_for_query(query, retmax=5)
            self.query_count += 1

            if pmids:
                for pmid in pmids[:3]:
                    if self._verify_title_match(pmid, title):
                        return pmid

        except Exception as e:
            print(f"  标题搜索失败: {e}")

        return None

    def _verify_title_match(self, pmid: str, expected_title: str) -> bool:
        """验证PMID对应的标题是否匹配"""
        try:
            article = self.fetch.article_by_pmid(pmid)
            self.query_count += 1

            actual_title = article.title
            # 清理标点符号后比较
            clean_expected = re.sub(r"[^\w\s]", "", expected_title.lower())
            clean_actual = re.sub(r"[^\w\s]", "", actual_title.lower())

            # 检查标题相似度（前30个字符匹配或包含关系）
            if (
                clean_actual.startswith(clean_expected[:30])
                or clean_expected.startswith(clean_actual[:30])
                or clean_expected in clean_actual
                or clean_actual in clean_expected
            ):
                return True

        except Exception as e:
            print(f"  标题验证失败 (PMID:{pmid}): {e}")

        return False

    def get_article_info_by_pmid(self, pmid: str) -> Optional[Dict]:
        """通过PMID获取完整的文章信息（包括DOI）"""
        try:
            article = self.fetch.article_by_pmid(pmid)
            self.query_count += 1

            return {
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
        except Exception as e:
            print(f"  获取文章信息失败 (PMID:{pmid}): {e}")
            return None


def main():
    parser = argparse.ArgumentParser(
        description="使用metapub从BibTeX文件中查找并添加PubMed DOI和PMID"
    )
    parser.add_argument("input", type=str, help="输入的BibTeX文件路径")
    parser.add_argument(
        "-o", "--output", type=str, help="输出文件路径（默认覆盖原文件）"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="NCBI API密钥（提高速率限制，设置环境变量NCBI_API_KEY也可）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="仅显示将进行的修改，不写入文件"
    )

    args = parser.parse_args()

    # 读取BibTeX文件
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误：文件 {input_path} 不存在")
        return

    print(f"正在解析BibTeX文件: {input_path}")
    entries = BibTeXParser.parse_file(input_path)
    print(f"找到 {len(entries)} 个条目\n")

    # 初始化PubMed查找器
    searcher = PubMedLookup(
        api_key=args.api_key,
    )

    # 处理每个条目
    stats = {"total": len(entries), "found_pmid": 0, "found_doi": 0, "failed": 0}

    for i, entry in enumerate(entries, 1):
        print(f"[{i}/{len(entries)}] 处理: {entry.key}")

        # 检查是否已有DOI或PMID
        has_doi = "doi" in entry.fields
        has_pmid = "pmid" in entry.fields

        # 提取字段
        title = entry.fields.get("title", "")
        journal = entry.fields.get("journal", "")
        year = entry.fields.get("year", "")
        volume = entry.fields.get("volume", "")
        pages = entry.fields.get("pages", "")
        author = entry.fields.get("author", "")

        if not title or not journal or not year:
            print("  ⚠ 缺少必要信息 (title/journal/year)，跳过")
            stats["failed"] += 1
            continue

        print(f"  标题: {title[:60]}..." if len(title) > 60 else f"  标题: {title}")
        print(f"  期刊: {journal}, {year}")

        # 提取作者姓氏（用于citation搜索）
        author_last = None
        if author:
            # 提取第一个作者的姓氏（如 "B. M. Shields" -> "Shields"）
            first_author = author.split(" and ")[0]
            name_parts = first_author.split()
            if name_parts:
                author_last = name_parts[-1].strip("{},.")

        # 提取起始页码
        first_page = None
        if pages and "--" in pages:
            first_page = pages.split("--")[0]
        elif pages:
            first_page = pages

        # 搜索PMID
        pmid = None
        if not has_pmid:
            print("  正在搜索PMID...")
            pmid = searcher.search_pmid_by_citation(
                title=title,
                journal=journal,
                year=year,
                volume=volume,
                first_page=first_page,
                author=author_last,
            )

            if pmid:
                print(f"  ✓ 找到PMID: {pmid}")
                entry.fields["pmid"] = pmid
                stats["found_pmid"] += 1
            else:
                print("  ✗ 未找到PMID")
        else:
            pmid = entry.fields.get("pmid")

        # 获取DOI
        if pmid and not has_doi:
            print("  正在获取DOI...")
            article_info = searcher.get_article_info_by_pmid(pmid)
            if article_info and article_info.get("doi"):
                doi = article_info["doi"]
                print(f"  ✓ 找到DOI: {doi}")
                entry.fields["doi"] = doi
                stats["found_doi"] += 1
            else:
                print("  ✗ 未找到DOI")

        print(f"  当前查询次数: {searcher.query_count}\n")

    # 输出统计信息
    print("=" * 50)
    print("统计结果:")
    print(f"  总条目数: {stats['total']}")
    print(f"  找到PMID: {stats['found_pmid']}")
    print(f"  找到DOI: {stats['found_doi']}")
    print(f"  失败: {stats['failed']}")
    print(f"  总API查询次数: {searcher.query_count}")

    # 写回文件
    if not args.dry_run:
        output_path = Path(args.output) if args.output else input_path
        BibTeXParser.write_file(output_path, entries)
        print(f"\n✓ 结果已保存到: {output_path}")
    else:
        print("\n✓ Dry run完成，未写入文件")


if __name__ == "__main__":
    main()
