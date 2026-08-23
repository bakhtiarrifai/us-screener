#!/usr/bin/env python3
"""
Universe of US tickers for the screener.

Builds a deduplicated list from:
- S&P 500 constituents (Wikipedia, with local cache fallback)
- Nasdaq-100 constituents (Wikipedia, with local cache fallback)
- Curated US-listed ETFs (broad market, sector, thematic, leveraged, bonds, commodities)

Tickers are returned in Yahoo Finance format (dots replaced by dashes, e.g. BRK.B -> BRK-B).
"""

from __future__ import annotations

import io
import json
import logging
import time
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "data" / "cache"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600  # refresh constituent lists weekly

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 us-screener/1.0"
)

# ---------------------------------------------------------------------------
# Curated ETF universe: broad US-listed ETFs across asset classes.
# ---------------------------------------------------------------------------
ETF_TICKERS = [
    # Broad market
    "SPY", "IVV", "VOO", "VTI", "QQQ", "QQQM", "DIA", "IWM", "IWB", "IWF",
    "IWD", "VTV", "VUG", "SCHD", "SCHG", "SCHX", "MGK", "RSP", "MDY", "IJH",
    # International / global
    "VEA", "VWO", "IEFA", "IEMG", "EFA", "EEM", "VXUS", "ACWI", "VT", "EWJ",
    "FXI", "MCHI", "INDA", "EWZ", "EWG", "EWU", "EWC", "EWA", "EWT", "EWY",
    # Sectors (SPDR + Vanguard + iShares)
    "XLK", "XLF", "XLV", "XLE", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC",
    "VGT", "VFH", "VHT", "VDE", "VIS", "VCR", "VDC", "VAW", "VNQ", "VOX",
    "SMH", "SOXX", "IGV", "FDN", "XBI", "IBB", "XOP", "OIH", "XME", "GDX", "GDXJ",
    "ITA", "XAR", "KBE", "KRE", "IYR", "REM", "XHB", "ITB", "XRT", "XSD",
    # Thematic / growth
    "ARKK", "ARKW", "ARKG", "ARKF", "ARKQ", "ICLN", "TAN", "PBW", "LIT", "BOTZ",
    "ROBO", "HACK", "CIBR", "SKYY", "CLOU", "WCLD", "IGM", "SOCL", "BLOK",
    "DRIV", "IDNA", "IRBO", "AIQ", "THNQ", "ARKX", "UFO", "SNSR", "FINX",
    # Dividend / income
    "VYM", "DVY", "SDY", "NOBL", "DGRO", "HDV", "SPHD", "JEPI", "JEPQ", "QYLD",
    "RYLD", "XYLD", "SCHY", "VIG", "SCHP", "DIV",
    # Bonds / fixed income
    "AGG", "BND", "TLT", "IEF", "SHY", "SHV", "BIL", "SGOV", "TIP", "LQD",
    "HYG", "JNK", "EMB", "MUB", "VCIT", "VCSH", "BSV", "BNDX", "GOVT", "SCHO",
    # Commodities / alternatives
    "GLD", "IAU", "SLV", "USO", "UNG", "DBC", "PDBC", "CPER", "WEAT", "CORN",
    "SOYB", "DBA", "UUP", "FXE", "FXY", "BITO", "IBIT", "FBTC", "ETHA",
    # Volatility & leveraged
    "TQQQ", "SQQQ", "UPRO", "SPXU", "UDOW", "SDOW", "TNA", "TZA", "SOXL", "SOXS",
    "LABU", "LABD", "UVXY", "VXX", "SVXY", "BOIL", "KOLD", "FAS", "FAZ", "NUGT",
    "DUST", "ERX", "ERY", "SPXL", "SPXS", "TECL", "TECS", "DRN", "DRV", "YINN",
    "YANG", "WEBL", "WEBS", "CURE", "WANT", "HIBL", "HIBS",
    # Low-vol / factor
    "USMV", "SPLV", "EFAV", "QUAL", "MTUM", "VLUE", "SIZE", "SPHQ", "DGRW",
    "MOAT", "PKW", "MOM", "SPMO", "AVUV", "AVDV",
]

# ---------------------------------------------------------------------------
# Static fallback snapshots (used if Wikipedia fetch fails and no cache).
# S&P 500 subset of the largest/most liquid names + full Nasdaq-100.
# ---------------------------------------------------------------------------
FALLBACK_SP500 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO",
    "BRK-B", "JPM", "LLY", "V", "UNH", "XOM", "MA", "COST", "HD", "PG",
    "NFLX", "JNJ", "CRM", "AMD", "BAC", "CVX", "KO", "MRK", "WMT", "ORCL",
    "PEP", "ABBV", "ADBE", "TMO", "MCD", "CSCO", "ACN", "ABT", "LIN", "DHR",
    "DIS", "TXN", "PM", "INTC", "VZ", "WFC", "QCOM", "IBM", "GE", "CAT",
    "NOW", "AMGN", "AXP", "SPGI", "ISRG", "RTX", "GS", "UBER", "BKNG", "PLD",
    "MS", "T", "LOW", "BLK", "ELV", "SYK", "PGR", "MDT", "ADP", "LMT",
    "CB", "BSX", "AMAT", "GILD", "DE", "HON", "C", "TJX", "SCHW", "ADI",
    "REGN", "VRTX", "MU", "LRCX", "BMY", "CI", "FI", "PANW", "SO", "DUK",
    "ZTS", "MO", "KLAC", "SNPS", "CDNS", "CMG", "MCK", "PYPL", "ICE", "BDX",
    "COP", "EOG", "USB", "NKE", "SHW", "WM", "APD", "MAR", "PNC", "EMR",
    "AON", "MMC", "GD", "ITW", "CL", "FDX", "HCA", "CSX", "ORLY", "MRVL",
    "AEP", "ECL", "AIG", "TGT", "FCX", "ROP", "NXPI", "CTAS", "AFL", "NSC",
    "SLB", "TRV", "SRE", "AZO", "KMB", "PSA", "O", "SPG", "WELL", "DLR",
    "CCI", "AMT", "PLTR", "HOOD", "COIN", "CRWD", "ANET", "DELL", "MSTR",
]

FALLBACK_NDX100 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO",
    "NFLX", "COST", "ASML", "AMD", "CSCO", "AZN", "LIN", "TMUS", "INTU",
    "ISRG", "QCOM", "TXN", "AMGN", "BKNG", "PEP", "ADBE", "AMAT", "HON",
    "PANW", "ADP", "GILD", "CMCSA", "MU", "ADI", "LRCX", "VRTX", "SBUX",
    "MELI", "KLAC", "INTC", "MDLZ", "SNPS", "CDNS", "PYPL", "REGN", "MAR",
    "ORLY", "CTAS", "CRWD", "CSX", "MRVL", "ADSK", "ABNB", "FTNT", "DASH",
    "WDAY", "MNST", "AEP", "KDP", "CHTR", "ROP", "NXPI", "PCAR", "PAYX",
    "CPRT", "ODFL", "FAST", "KHC", "EXC", "GEHC", "CCEP", "LULU", "VRSK",
    "CSGP", "XEL", "FANG", "IDXX", "BKR", "TTWO", "EA", "DXCM", "ANSS",
    "ON", "BIIB", "CDW", "GFS", "TTD", "WBD", "MDB", "ZM", "ILMN", "DLTR",
    "TEAM", "ZS", "DDOG", "ARM", "SMCI", "APP", "PLTR", "MSTR", "CEG",
    "AXON", "TT", "ROP",
]


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.json"


def _load_cache(name: str) -> list[str] | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        age = time.time() - payload.get("fetched_at", 0)
        tickers = payload.get("tickers", [])
        if tickers and age <= CACHE_MAX_AGE_SECONDS:
            logger.info("Using cached %s list (%d tickers, %.1f days old)",
                        name, len(tickers), age / 86400)
            return tickers
        return None
    except Exception as e:
        logger.warning("Failed reading cache %s: %s", path, e)
        return None


def _save_cache(name: str, tickers: list[str]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(name).write_text(
            json.dumps({"fetched_at": time.time(), "tickers": tickers})
        )
    except Exception as e:
        logger.warning("Failed writing cache for %s: %s", name, e)


def _normalize_ticker(symbol: str) -> str:
    """Convert Wikipedia symbol to Yahoo Finance format (BRK.B -> BRK-B)."""
    return symbol.strip().upper().replace(".", "-")


def _fetch_wikipedia_table(url: str, symbol_column_candidates: tuple[str, ...]) -> list[str]:
    """Fetch a Wikipedia constituent table and extract ticker symbols."""
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(io.StringIO(response.text))
    for table in tables:
        cols = {str(c).lower(): c for c in table.columns}
        for candidate in symbol_column_candidates:
            if candidate in cols:
                col = cols[candidate]
                tickers = [
                    _normalize_ticker(str(s))
                    for s in table[col].tolist()
                    if isinstance(s, str) and s.strip()
                ]
                if tickers:
                    return tickers
    raise ValueError(f"No ticker column found in {url}")


def get_sp500_tickers(force_refresh: bool = False) -> list[str]:
    """S&P 500 constituents in Yahoo format, with cache + static fallback."""
    if not force_refresh:
        cached = _load_cache("sp500")
        if cached:
            return cached
    try:
        tickers = _fetch_wikipedia_table(SP500_URL, ("symbol", "ticker"))
        logger.info("Fetched %d S&P 500 tickers from Wikipedia.", len(tickers))
        _save_cache("sp500", tickers)
        return tickers
    except Exception as e:
        logger.warning("Failed fetching S&P 500 list (%s); using fallback.", e)
        cached = _load_cache("sp500") or FALLBACK_SP500
        return cached


def get_nasdaq100_tickers(force_refresh: bool = False) -> list[str]:
    """Nasdaq-100 constituents in Yahoo format, with cache + static fallback."""
    if not force_refresh:
        cached = _load_cache("ndx100")
        if cached:
            return cached
    try:
        tickers = _fetch_wikipedia_table(NDX_URL, ("ticker", "symbol"))
        logger.info("Fetched %d Nasdaq-100 tickers from Wikipedia.", len(tickers))
        _save_cache("ndx100", tickers)
        return tickers
    except Exception as e:
        logger.warning("Failed fetching Nasdaq-100 list (%s); using fallback.", e)
        cached = _load_cache("ndx100") or FALLBACK_NDX100
        return cached


def get_etf_tickers() -> list[str]:
    """Curated ETF universe."""
    return list(dict.fromkeys(ETF_TICKERS))


def get_universe(
    include_sp500: bool = True,
    include_ndx100: bool = True,
    include_etfs: bool = True,
    force_refresh: bool = False,
) -> list[str]:
    """Full deduplicated universe, preserving order (SP500 -> NDX -> ETFs)."""
    universe: list[str] = []
    if include_sp500:
        universe += get_sp500_tickers(force_refresh=force_refresh)
    if include_ndx100:
        universe += get_nasdaq100_tickers(force_refresh=force_refresh)
    if include_etfs:
        universe += get_etf_tickers()
    deduped = list(dict.fromkeys(universe))
    logger.info("Universe built: %d unique tickers (sp500=%s, ndx100=%s, etfs=%s)",
                len(deduped), include_sp500, include_ndx100, include_etfs)
    return deduped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    u = get_universe()
    print(f"Universe: {len(u)} tickers")
    print("Sample:", u[:10], "...", u[-5:])
