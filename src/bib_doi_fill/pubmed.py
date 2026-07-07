import os
import re

from loguru import logger
from metapub import PubMedFetcher


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
        self.cache_doi_info: dict[str, dict] = {}

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
                self.cache_pmid_info[pmid] = self._build_article_info(article)

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

    @staticmethod
    def _build_article_info(article) -> dict:
        """从PubMedArticle对象构建标准信息字典"""
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

    def get_article_info_by_pmid(self, pmid: str) -> dict | None:
        """通过PMID获取完整的文章信息（从缓存或API）"""
        if pmid in self.cache_pmid_info:
            return self.cache_pmid_info[pmid]

        try:
            article = self.fetch.article_by_pmid(pmid)
            self.query_count += 1

            info = self._build_article_info(article)
            self.cache_pmid_info[pmid] = info

            # Also cache by DOI if available
            if info.get("doi"):
                self.cache_doi_info[info["doi"]] = info

            return info

        except Exception as e:
            logger.error(f"获取文章信息失败 (PMID:{pmid}): {e}")
            return None

    def get_article_info_by_doi(self, doi: str) -> dict | None:
        """通过DOI获取完整的文章信息（从缓存或API）

        使用metapub的 article_by_doi 方法，内部自动解析 DOI → PMID → article。
        结果会同时缓存到 pmid 和 doi 索引中。
        """
        # 检查DOI缓存
        if doi in self.cache_doi_info:
            return self.cache_doi_info[doi]

        try:
            article = self.fetch.article_by_doi(doi)
            self.query_count += 1

            info = self._build_article_info(article)
            self.cache_doi_info[doi] = info

            # Also cache by PMID if available
            if info.get("pmid"):
                self.cache_pmid_info[info["pmid"]] = info

            return info

        except Exception as e:
            logger.error(f"通过DOI获取文章信息失败 (DOI:{doi}): {e}")
            return None

    def get_article_info(
        self, doi: str | None = None, pmid: str | None = None
    ) -> dict | None:
        """统一的文章信息获取接口

        优先使用DOI查找，其次使用PMID查找。
        """
        if doi:
            result = self.get_article_info_by_doi(doi)
            if result:
                return result
            # DOI查找失败，回退到PMID
        if pmid:
            return self.get_article_info_by_pmid(pmid)
        return None
