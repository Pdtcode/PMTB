"""
Scanner package for PMTB market scanning pipeline.

Public API:
    MarketCandidate — a market that passed all filters
    ScanResult — output of a complete scan cycle
"""
from pmtb.scanner.models import MarketCandidate, ScanResult

__all__ = ["MarketCandidate", "ScanResult"]
