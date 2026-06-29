import sys
import asyncio
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime
from typing import Optional, List

import typer
from playwright.async_api import async_playwright, Page
import bibtexparser
from loguru import logger

app = typer.Typer()

# Setup logging
log_path = Path("logs")
log_path.mkdir(exist_ok=True)
logger.add(f"logs/sci-downloader-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log")

DEFAULT_SCI_HUB_URL = "https://sci-hub.box"
DOWNLOAD_LOCATOR = "div.pdf > iframe, div.pdf > object"
POLL_INTERVAL_MS = 200
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"


def normalize_filename(doi: str) -> str:
    """Replace '/' with '_' for filename"""
    return doi.replace("/", "_") + ".pdf"


async def download_pdf(page: Page, url: str, filepath: Path) -> bool:
    """Download PDF from URL using browser's API request context and save to file"""
    try:
        logger.info(f"Downloading PDF from {url}")
        response = await page.request.get(url)
        if not response.ok:
            logger.error(
                f"Failed to download PDF: HTTP {response.status} {response.status_text}"
            )
            return False

        body = await response.body()
        if not body:
            logger.error("Downloaded empty response body")
            return False

        filepath.write_bytes(body)
        await response.dispose()
        logger.success(f"Saved PDF ({len(body)} bytes) to {filepath.name}")
        return True
    except Exception as e:
        logger.error(f"Error downloading PDF: {e}")
        return False


async def get_download_link(page: Page, sci_hub_url: str, doi: str) -> Optional[str]:
    """Navigate to Sci-Hub and extract download link using existing page"""
    full_url = f"{sci_hub_url}/{doi}"
    logger.info(f"Accessing {full_url}")

    await page.goto(full_url)

    # Wait for DDoS-Guard check and download link to appear (no timeout)
    locator = page.locator(DOWNLOAD_LOCATOR)
    missing_locator = page.locator("block-rounded.message")
    download_link = None
    attempt = 0
    while True:
        await asyncio.sleep(POLL_INTERVAL_MS / 1000)
        attempt += 1

        # Check if download element exists
        element = await locator.all()
        if element:
            pdf = element.pop()
            src = await pdf.get_attribute("src") or await pdf.get_attribute("data")
            download_link = src
            logger.success(f"Found download link for {doi} after {attempt} attempts")
            break

        if await missing_locator.all():
            logger.error("Missing article on Sci-Hub, skipping")
            break

        logger.debug(f"Waiting for download link... attempt {attempt}")

    if not download_link:
        logger.error(f"Failed to get download link for {doi}")
        return None

    # Handle relative URLs
    if download_link.startswith("/"):
        # Handle sci media
        domain = urlparse(page.url).hostname
        download_link = f"https://{domain}{download_link}"

    return download_link


def parse_bib_file(bib_path: Path) -> List[str]:
    """Parse bib file and extract DOIs"""
    try:
        with open(bib_path, "r", encoding="utf-8") as f:
            bib_db = bibtexparser.load(f)

        dois = []
        for entry in bib_db.entries:
            if "doi" in entry and entry["doi"]:
                doi = entry["doi"].lower()
                dois.append(doi)
                logger.info(
                    f"Found DOI: {doi} from entry: {entry.get('ID', 'unknown')}"
                )
            else:
                logger.warning(
                    f"Skipping entry {entry.get('ID', 'unknown')} - no DOI field"
                )

        logger.info(f"Total papers with DOI: {len(dois)}")
        return dois

    except Exception as e:
        logger.error(f"Failed to parse bib file: {e}")
        raise typer.Exit(code=1)


@app.command()
def download(
    bib_file: Path = typer.Argument(..., exists=True, help="BibTeX file path"),
    sci_hub_url: str = typer.Option(DEFAULT_SCI_HUB_URL, help="Sci-Hub base URL"),
    skip_existing: bool = typer.Option(True, help="Skip if PDF already exists"),
    pdf_dir: Path = typer.Option(
        Path("downloads"), help="Directory to check for existing PDFs"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
    delay: float = typer.Option(
        2.0, "--delay", "-d", help="Delay in seconds between requests to avoid IP ban"
    ),
    headless: bool = typer.Option(
        False, "--headless", help="Run browser in headless mode"
    ),
):
    """
    Download PDFs from Sci-Hub using DOIs from BibTeX file.
    PDFs are downloaded directly via the browser's network context.

    Usage:
        python script.py download references.bib
    """
    if verbose:
        logger.add(sys.stderr, level="DEBUG")

    # Create downloads directory
    pdf_dir.mkdir(exist_ok=True)
    logger.info(f"Starting PDF download process from {bib_file}")
    logger.info(f"Sci-Hub URL: {sci_hub_url}")
    logger.info(f"Downloads directory: {pdf_dir.absolute()}")
    logger.info(f"Delay between requests: {delay} seconds")
    logger.info(f"Browser headless mode: {headless}")

    # Parse DOIs from bib file
    dois = parse_bib_file(bib_file)

    if not dois:
        logger.warning("No DOIs found in bib file")
        raise typer.Exit(code=0)

    # Asynchronously process DOIs one by one with delay, reusing browser
    async def main():
        downloaded_count = 0

        # Launch browser once
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless, channel="chrome")
            page = await browser.new_page(user_agent=USER_AGENT)

            try:
                for idx, doi in enumerate(dois):
                    filename = normalize_filename(doi)
                    filepath = pdf_dir / filename

                    if skip_existing and filepath.exists():
                        logger.info(
                            f"Skipping {filename} - already exists in {pdf_dir}"
                        )
                        continue

                    # Add delay between requests (except for first one)
                    if idx > 0:
                        logger.info(f"Waiting {delay} seconds before next request...")
                        await asyncio.sleep(delay)

                    logger.info(f"Processing DOI {idx + 1}/{len(dois)}: {doi}")

                    url = await get_download_link(page, sci_hub_url, doi)

                    if url:
                        success = await download_pdf(page, url, filepath)
                        if success:
                            downloaded_count += 1
                    else:
                        logger.error(f"No download link for {doi}")

            finally:
                await browser.close()

        # Summary
        if downloaded_count:
            logger.success(
                f"Downloaded {downloaded_count} PDF(s) to {pdf_dir.absolute()}"
            )
        else:
            logger.warning("No PDFs were downloaded")

    # Run async function
    asyncio.run(main())


if __name__ == "__main__":
    app()
