"""Standalone schema-driven KiCad symbol and footprint generator."""

from .library_generation import generate_kicad_libraries

__all__ = ["generate_kicad_libraries"]
