from src.parsers.archive import ArchiveParser
from src.parsers.base import DiveLogParser
from src.parsers.garmin_fit import GarminFitParser
from src.parsers.subsurface import SubsurfaceParser
from src.parsers.uddf import UddfParser

PARSER_REGISTRY: dict[str, type[DiveLogParser]] = {
    ".ssrf": SubsurfaceParser,
    ".xml": SubsurfaceParser,
    ".uddf": UddfParser,
    ".fit": GarminFitParser,
    ".zip": ArchiveParser,
}


def get_parser(filename: str) -> DiveLogParser:
    """Return the appropriate parser for a given filename."""
    for ext, parser_cls in PARSER_REGISTRY.items():
        if filename.lower().endswith(ext):
            return parser_cls()
    supported = ", ".join(PARSER_REGISTRY.keys())
    raise ValueError(f"Unsupported file type: {filename}. Supported: {supported}")
