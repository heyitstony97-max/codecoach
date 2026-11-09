"""Importer: materials or an outline -> a course map with coverage. Hermes-free."""

from codecoach.importer.build import (ImportFailed, get_import_report, import_materials, import_outline,
                                   import_with_syllabus, OUTLINE_MODE_REASON)
from codecoach.importer.syllabus import parse_syllabus
from codecoach.importer.lang import infer_code_language
from codecoach.importer.subject import SUBJECTS, clean_subject, infer_subject
from codecoach.importer.mapio import export_map, import_map
from codecoach.importer.verbs import VERB_FORMATS, VERBS, allowed_formats

__all__ = ["ImportFailed", "get_import_report", "import_materials", "import_outline", "import_with_syllabus", "parse_syllabus", "export_map",
           "import_map", "VERBS", "VERB_FORMATS", "allowed_formats", "OUTLINE_MODE_REASON", "infer_code_language",
           "SUBJECTS", "clean_subject", "infer_subject"]
