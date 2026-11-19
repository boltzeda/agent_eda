"""Standalone finder for installed KiCad symbol and footprint libraries."""

from .library_finder import find_installed_libraries

__all__ = ["find_installed_libraries"]
