import json
import os
import shutil
import uuid
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Set

from . import assets
from . import util
from ._script_file_segments import _ScriptFileSegmentOps
from ._script_file_template import _ScriptFileTemplateOps
from ._script_file_tracks import _ScriptFileTrackOps
from .draft_codec import DraftContentCodec, load_json_object_with_codec, write_json_text_with_codec
from .draft_content_loader import FallbackLoader, load_draft_content
from .draft_file_paths import map_material_file_paths
from .script_material import ScriptMaterial
from .template_mode import ImportedTrack, import_track
from .track import BaseTrack, Track

MATERIALS_DIR_NAME = "materials"
"""草稿文件夹内存放内联素材的子目录名"""


class ScriptFile(_ScriptFileTrackOps, _ScriptFileSegmentOps, _ScriptFileTemplateOps):
    """剪映草稿文件, 大部分接口定义在此"""

    save_path: Optional[str]
    """草稿文件保存路径, 仅在模板模式下有效"""
    content: Dict[str, Any]
    """草稿文件内容"""

    width: int
    """视频的宽度, 单位为像素"""
    height: int
    """视频的高度, 单位为像素"""
    fps: int
    """视频的帧率"""
    duration: int
    """视频的总时长, 单位为微秒"""

    maintrack_adsorb: bool
    """是否启用主轨道吸附（主轨磁吸）"""

    materials: ScriptMaterial
    """草稿文件中的素材信息部分"""
    tracks: Dict[str, Track]
    """轨道信息"""

    imported_materials: Dict[str, List[Dict[str, Any]]]
    """导入的素材原始信息, 读取时推荐走带自动补空的`_get_imported_material_list`方法"""
    imported_tracks: List[ImportedTrack]
    """导入的轨道信息"""
    _track_ref_owner_id: str
    """用于校验 TrackRef 归属的内部标识"""
    _loaded_content_codec: Optional[DraftContentCodec]
    """实际解码当前 draft_content.json 的私有 codec；明文与 fallback 加载均为空。"""
    _before_save_hook: Optional[Callable[["ScriptFile"], None]]
    """由 DraftFolder 注入的私有保存前钩子。"""
    _after_save_hook: Optional[Callable[["ScriptFile"], None]]
    """由 DraftFolder 注入的私有保存后钩子。"""
    _draft_registration_context: Optional[Dict[str, Any]]
    """仅供私有 DraftFolder 注册流程使用的保存上下文。"""

    def __init__(self, width: int, height: int, fps: int, maintrack_adsorb: bool):
        """**创建剪映草稿推荐使用`DraftFolder.create_draft()`而非此方法**

        Args:
            width (int): 视频宽度, 单位为像素
            height (int): 视频高度, 单位为像素
            fps (int): 视频帧率
            maintrack_adsorb (bool): 是否启用主轨道吸附（主轨磁吸）

        Raises:
            无
        """
        self.save_path = None

        self.width = width
        self.height = height
        self.fps = fps
        self.duration = 0
        self.maintrack_adsorb = maintrack_adsorb

        self.materials = ScriptMaterial()
        self.tracks = {}

        self.imported_materials = {}
        self.imported_tracks = []
        self._track_ref_owner_id = uuid.uuid4().hex
        self._loaded_content_codec = None
        self._before_save_hook = None
        self._after_save_hook = None
        self._draft_registration_context = None

        with open(assets.get_asset_path("DRAFT_CONTENT_TEMPLATE"), "r", encoding="utf-8") as f:
            self.content = json.load(f)

    @classmethod
    def _load_template(
        cls,
        json_path: str,
        fallback_loader: Optional[FallbackLoader] = None,
        *,
        content_codec: Optional[DraftContentCodec] = None,
    ) -> "ScriptFile":
        if fallback_loader is not None and content_codec is not None:
            raise ValueError("fallback_loader and content_codec are mutually exclusive")

        obj = cls(**util.provide_ctor_defaults(cls))
        obj.save_path = json_path
        if content_codec is None:
            obj.content = load_draft_content(json_path, fallback_loader=fallback_loader)
            loaded_with_codec = False
        else:
            # Keep the upstream fallback contract separate from the private
            # reversible codec path so codec diagnostics retain their cause.
            obj.content, loaded_with_codec = load_json_object_with_codec(
                json_path,
                content_codec=content_codec,
            )
        if loaded_with_codec:
            obj._loaded_content_codec = content_codec
        obj.content.setdefault("fps", 30.0)
        obj.content.setdefault("config", {})
        obj.content["config"].setdefault("maintrack_adsorb", True)
        obj.content.setdefault("tracks", [])
        obj.content.setdefault("materials", {})

        for track in obj.content["tracks"]:
            track.setdefault("segments", [])

        util.assign_attr_with_json(obj, ["fps", "duration"], obj.content)
        util.assign_attr_with_json(obj, ["maintrack_adsorb"], obj.content["config"])
        util.assign_attr_with_json(obj, ["width", "height"], obj.content["canvas_config"])

        obj.imported_materials = deepcopy(obj.content["materials"])
        obj.imported_tracks = [
            import_track(track_data, track_order)
            for track_order, track_data in enumerate(obj.content["tracks"])
        ]

        return obj

    def dumps(self) -> str:
        """将草稿文件内容导出为JSON字符串"""
        self.content["fps"] = self.fps
        self.content["duration"] = self.duration
        self.content["config"]["maintrack_adsorb"] = self.maintrack_adsorb
        canvas_config = {"width": self.width, "height": self.height, "ratio": "original"}
        existing_canvas = self.content.get("canvas_config")
        if isinstance(existing_canvas, dict) and "background" in existing_canvas:
            canvas_config["background"] = existing_canvas["background"]
        self.content["canvas_config"] = canvas_config
        self.content["materials"] = self.materials.export_json()

        for material_type, material_list in self.imported_materials.items():
            if material_type not in self.content["materials"]:
                self.content["materials"][material_type] = material_list
            else:
                self.content["materials"][material_type].extend(material_list)

        track_list: List[BaseTrack] = []
        track_list.extend(self.imported_tracks)
        track_list.extend(self.tracks.values())
        track_list.sort(key=lambda track: track.track_order)
        track_exports = [track.export_json() for track in track_list]
        for export_index, track_json in enumerate(track_exports):
            for segment_json in track_json["segments"]:
                segment_json["render_index"] = export_index
                segment_json["track_render_index"] = 0

        self.content["tracks"] = track_exports

        return json.dumps(self.content, ensure_ascii=False, indent=4)

    def dump(
        self,
        file_path: str,
        *,
        content_codec: Optional[DraftContentCodec] = None,
    ) -> None:
        """将草稿文件写入文件，默认保持实际加载时使用的私有编码格式。"""
        effective_codec = content_codec if content_codec is not None else self._loaded_content_codec
        write_json_text_with_codec(file_path, self.dumps(), content_codec=effective_codec)

    @staticmethod
    def _unique_material_name(name: str, used_names: Set[str]) -> str:
        """避开`used_names`中已占用的文件名, 必要时追加序号"""
        if os.path.normcase(name) not in used_names:
            return name

        stem, suffix = os.path.splitext(name)
        index = 1
        while os.path.normcase(f"{stem}_{index}{suffix}") in used_names:
            index += 1
        return f"{stem}_{index}{suffix}"

    def map_material_paths(self, transform: Callable[[str], str]) -> None:
        """Map known local media and font references before serialization."""
        def map_path(value: str) -> str:
            mapped = transform(value)
            if mapped != value and self._draft_registration_context is not None:
                key = os.path.normcase(os.path.normpath(value.replace("\\", os.sep).replace("/", os.sep)))
                self._draft_registration_context.setdefault("material_path_mapping", {})[key] = mapped
            return mapped

        for material in [*self.materials.videos, *self.materials.audios]:
            if material.path and not material.path.startswith("<cloud://"):
                material.path = map_path(material.path)
        map_material_file_paths(
            {"texts": self.materials.texts, "masks": self.materials.masks,
             "stickers": self.materials.stickers}, map_path,
        )
        map_material_file_paths(self.imported_materials, map_path)

    def _inline_materials(self, target_dir: str, *, only_from: Optional[str] = None) -> None:
        """Copy local media and fonts, optionally only from one managed source root."""
        target_dir = os.path.abspath(target_dir)
        os.makedirs(target_dir, exist_ok=True)
        copied: Dict[str, str] = {}
        used_names: Set[str] = {os.path.normcase(name) for name in os.listdir(target_dir)}
        source_root = os.path.normcase(os.path.abspath(only_from)) if only_from is not None else None

        def copy_material(raw_path: str) -> str:
            source = os.path.abspath(raw_path)
            source_key = os.path.normcase(source)
            if source_root is not None:
                try:
                    if os.path.commonpath([source_root, source_key]) != source_root:
                        return raw_path
                except ValueError:
                    return raw_path
            if not os.path.isfile(source):
                raise FileNotFoundError(f"内联素材不存在: {source}")
            if source_key in copied:
                return copied[source_key]
            if os.path.normcase(os.path.dirname(source)) == os.path.normcase(target_dir):
                target = source
            else:
                name = self._unique_material_name(os.path.basename(source), used_names)
                used_names.add(os.path.normcase(name))
                target = os.path.join(target_dir, name)
                shutil.copy2(source, target)
            copied[source_key] = target
            return target

        self.map_material_paths(copy_material)

    def save(self, *, inline_materials: bool = False) -> None:
        """保存草稿文件至打开时的路径

        Args:
            inline_materials (`bool`, optional): 是否将新建及模板导入的本地素材复制进草稿文件夹的
                `materials`子目录并改写素材路径, 使草稿可连同素材整体迁移.
                默认为否.

        Raises:
            `ValueError`: 没有设置保存路径
        """
        if self.save_path is None:
            raise ValueError("没有设置保存路径, 可能不在模板模式下")
        if self._before_save_hook is not None:
            self._before_save_hook(self)

        if inline_materials:
            self._inline_materials(os.path.join(os.path.dirname(self.save_path), MATERIALS_DIR_NAME))

        self.dump(self.save_path)
        if self._after_save_hook is not None:
            self._after_save_hook(self)
