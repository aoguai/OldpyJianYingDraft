"""Rewrite file references in known native draft fields, never arbitrary text."""

from __future__ import annotations

import json
from pathlib import PureWindowsPath
from typing import Any, Callable, Dict


PathMapper = Callable[[str], str]


def map_file_field(owner: Dict[str, Any], key: str, transform: PathMapper) -> None:
    value = owner.get(key)
    if not isinstance(value, str) or not value or value.startswith("<cloud://"):
        return
    # Native rich-text fonts use drive-only placeholders alongside resource IDs.
    if PureWindowsPath(value).drive == value:
        return
    owner[key] = transform(value)


def map_font_paths(font: Dict[str, Any], transform: PathMapper, *, clear_cloud_paths: bool) -> None:
    cloud_font = bool(font.get("font_resource_id") or font.get("font_id") or font.get("id"))
    for key in ("font_path", "path"):
        if cloud_font:
            if clear_cloud_paths and key in font:
                font[key] = ""
        else:
            map_file_field(font, key, transform)


def map_material_file_paths(
    materials: Dict[str, Any], transform: PathMapper, *, clear_cloud_paths: bool = False,
) -> None:
    for kind in ("videos", "audios", "images", "stickers"):
        for material in materials.get(kind, []) or []:
            if not isinstance(material, dict):
                continue
            if kind == "audios" and material.get("type") == "music":
                if clear_cloud_paths and not str(material.get("path", "")).startswith("<cloud://"):
                    material["path"] = ""
                continue
            if kind == "stickers" and material.get("resource_id"):
                if clear_cloud_paths and "path" in material:
                    material["path"] = ""
                continue
            map_file_field(material, "path", transform)

    for text in materials.get("texts", []) or []:
        if not isinstance(text, dict):
            continue
        # A text's id identifies the material, not a downloadable font.
        if text.get("font_resource_id") or text.get("font_id"):
            if clear_cloud_paths and "font_path" in text:
                text["font_path"] = ""
        else:
            map_file_field(text, "font_path", transform)
        for font in text.get("fonts", []) or []:
            if isinstance(font, dict):
                map_font_paths(font, transform, clear_cloud_paths=clear_cloud_paths)
        content = text.get("content")
        if isinstance(content, str) and content:
            embedded = json.loads(content)
            if isinstance(embedded, dict):
                for style in embedded.get("styles", []) or []:
                    if isinstance(style, dict) and isinstance(style.get("font"), dict):
                        map_font_paths(style["font"], transform, clear_cloud_paths=clear_cloud_paths)
                text["content"] = json.dumps(embedded, ensure_ascii=False)

    for mask in materials.get("masks", []) or []:
        if isinstance(mask, dict) and isinstance(mask.get("text_config"), dict):
            map_font_paths(mask["text_config"], transform, clear_cloud_paths=clear_cloud_paths)


def map_draft_file_paths(
    document: Dict[str, Any], transform: PathMapper, *, clear_cloud_paths: bool = False,
) -> None:
    materials = document.get("materials")
    if isinstance(materials, dict):
        map_material_file_paths(materials, transform, clear_cloud_paths=clear_cloud_paths)
    for bucket in document.get("draft_materials", []) or []:
        if not isinstance(bucket, dict):
            continue
        for material in bucket.get("value", []) or []:
            if isinstance(material, dict):
                map_file_field(material, "file_Path", transform)
