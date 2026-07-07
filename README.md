# Bib Downloader

A collection of tools for downloading academic papers and enriching BibTeX metadata.

## Tools

### `sci-downloader.py` — Download PDFs from Sci-Hub

Scrapes Sci-Hub using DOIs from a BibTeX file and downloads PDFs directly via the browser's network context.

#### Usage

```bash
uv run sci-downloader.py input.bib
```

#### Options

| Flag               | Default               | Description                                      |
| ------------------ | --------------------- | ------------------------------------------------ |
| `bib_file`         | (required)            | Path to BibTeX file                              |
| `--sci-hub-url`    | `https://sci-hub.box` | Sci-Hub base URL                                 |
| `--skip-existing`  | `True`                | Skip if PDF already exists in download directory |
| `--pdf-dir`        | `downloads/`          | Directory to save PDFs                           |
| `--delay` / `-d`   | `2.0`                 | Seconds between requests to avoid IP ban         |
| `--headless`       | `False`               | Run browser in headless mode                     |
| `--verbose` / `-v` | `False`               | Enable debug logging                             |

#### How it works

1. Parses a BibTeX file and extracts all DOIs
2. Launches a Chrome browser via Playwright
3. For each DOI, navigates to Sci-Hub and waits for the PDF iframe/object to load
4. Extracts the PDF download URL and fetches it using the browser's API request context (sharing cookies/session for DDoS-Guard bypass)
5. Saves the PDF directly to `downloads/<doi>.pdf`
6. Respects `--delay` between requests and `--skip-existing` for already-downloaded files

#### Examples

```bash
# Basic usage
uv run sci-downloader.py references.bib

# Use a different Sci-Hub mirror, no delay between requests
uv run sci-downloader.py references.bib --sci-hub-url https://sci-hub.ru --delay 0

# Headless mode (no visible browser window)
uv run sci-downloader.py references.bib --headless

# Custom output directory
uv run sci-downloader.py references.bib --pdf-dir my_papers
```

### `bib_doi_fill` — Enrich BibTeX with DOIs & PMIDs

Looks up DOIs and PMIDs from PubMed for BibTeX entries missing this information.
Refactored into a modular package (`bib_doi_fill/`) — no subcommand needed.

#### Usage

```bash
python -m bib_doi_fill input.bib --dry-run
# Or via installed entry point:
bibtex-enrich input.bib
```

#### Options

| Flag               | Default    | Description                                            |
| ------------------ | ---------- | ------------------------------------------------------ |
| `input_file`       | (required) | Path to input BibTeX file                              |
| `--output` / `-o`  | —          | Output file path (defaults to overwriting input file)  |
| `--api-key`        | —          | NCBI API key (increases rate limit from 3 to 10 req/s) |
| `--email`          | —          | Contact email (recommended by NCBI)                    |
| `--force` / `-f`   | `False`    | Force re-fetch for entries that already have DOI/PMID  |
| `--dry-run`        | `False`    | Show what would be changed without writing             |
| `--verbose` / `-v` | `False`    | Enable debug logging                                   |
| `--version`        | —          | Show version information and exit                      |

#### DOI-first enrichment

When a BibTeX entry already has a DOI but no PMID, the tool now uses
`PubMedFetcher.article_by_doi(doi)` to resolve the PMID directly — skipping
the slower citation-based search. This applies automatically.

#### Module structure

```
bib_doi_fill.py        Thin wrapper (backwards compatible)
bib_doi_fill/
  __init__.py          Version info
  __main__.py          python -m support
  cli.py               Typer CLI (callback pattern, no subcommand)
  pubmed.py            PubMedLookup class (+ article_by_doi support)
  bibtex.py            BibTeX field cleaning & enrichment logic
```

## Installation

```bash
# Clone the repo
git clone <repo-url>
cd bib-downloader

# Install dependencies (uses uv)
uv sync

# Install Playwright browsers (first time only)
uv run playwright install chrome
```
