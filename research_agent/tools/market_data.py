"""Describe market-data coverage without pretending a provider is configured."""


def market_data_coverage() -> dict:
    """Report available acquisition and unsupported historical-market capabilities."""
    return {
        "public_snapshot": "Use the shared public Web reader and record dated, defined MetricObservations.",
        "dedicated_provider": "not_configured",
        "historical_price_series": "not_supported",
        "fund_nav_series": "not_supported",
        "gap_policy": "Unavailable provider series remain an explicit acquisition gap.",
    }
