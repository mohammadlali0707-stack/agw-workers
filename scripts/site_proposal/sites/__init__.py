"""Site DOM Adapters for freelance platforms (Ponisha, Karlancer).

Exposes get_site_adapter(site: str) returning the appropriate adapter class.
Each adapter provides `async def run_site_flow(page, context, proposal_data: dict, mode: str) -> dict`.
"""

from typing import Any, Dict, Type, Union
from .ponisha import PonishaAdapter, run_site_flow as ponisha_flow
from .karlancer import KarlancerAdapter, run_site_flow as karlancer_flow
from .parscoders import ParscodersAdapter
from .freelancer import FreelancerAdapter

ADAPTER_MAP: Dict[str, Type[Union[PonishaAdapter, KarlancerAdapter]]] = {
    "ponisha": PonishaAdapter,
    "karlancer": KarlancerAdapter,
    "parscoders": ParscodersAdapter,
    "freelancer": FreelancerAdapter,
}

SUPPORTED_SITES = list(ADAPTER_MAP.keys())


def get_site_adapter(site: str):
    """Return the adapter class or module for the requested site.

    Args:
        site: Name of the site (e.g. 'ponisha', 'karlancer').

    Returns:
        Adapter class with `async def run_site_flow(...)`.

    Raises:
        ValueError: If site is unsupported or empty.
    """
    if not site or not isinstance(site, str):
        raise ValueError("Site name must be a non-empty string.")

    site_key = site.strip().lower()
    if site_key not in ADAPTER_MAP:
        raise ValueError(
            f"Unsupported site: '{site}'. Supported sites are: {SUPPORTED_SITES}"
        )

    return ADAPTER_MAP[site_key]


__all__ = [
    "get_site_adapter",
    "PonishaAdapter",
    "KarlancerAdapter",
    "SUPPORTED_SITES",
]
