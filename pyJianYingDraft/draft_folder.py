"""Manage Jianying draft directories and compose private fork integrations."""

from __future__ import annotations

import os
import shutil
from typing import List, Literal, Optional

from . import assets
from .draft_codec import DraftContentCodec
from .draft_content_loader import FallbackLoader
from .draft_registration import DraftFolderRegistration
from .script_file import ScriptFile

DRAFT_FILE_NAME = "draft_info.json"
"""草稿主文件名

剪映 6.0 及以上版本读取此文件, 已在 11.4.0 上实测确认. 旧文件名`draft_content.json`
仅被"草稿包导入"流程识别(该流程会将其重命名), 直接放入草稿文件夹会被判定为"草稿内容已损坏".
"""

LEGACY_DRAFT_FILE_NAME = "draft_content.json"
"""旧版(剪映 5.9 及以下)的草稿主文件名, 仅用于读取兼容"""

class DraftFolder:
    """Manage drafts rooted in one filesystem directory.

    ``fallback_loader`` is the upstream 0.3 read-only escape hatch.  The private
    ``content_codec`` is a reversible format collaborator and deliberately uses
    a separate keyword-only argument so the two policies cannot be confused.
    """

    folder_path: str
    fallback_loader: Optional[FallbackLoader]
    content_codec: Optional[DraftContentCodec]

    def __init__(
        self,
        folder_path: str,
        fallback_loader: Optional[FallbackLoader] = None,
        *,
        content_codec: Optional[DraftContentCodec] = None,
        user_data_path: Optional[str] = None,
    ) -> None:
        if fallback_loader is not None and content_codec is not None:
            raise ValueError("fallback_loader and content_codec are mutually exclusive")

        """管理一个包含若干草稿的文件夹.

        Args:
            folder_path (`str`): 包含若干草稿的文件夹, 一般取剪映保存草稿的位置即可
            fallback_loader (`Callable`, optional): 当草稿主文件无法按明文 JSON 读取时使用的后备读取器.
                剪映 6.0 及以上版本保存的草稿是密文, 读取它们需要提供本参数.
                其输入为文件原始字节串, 返回值仅支持 JSON 字符串或字典.
            content_codec (`DraftContentCodec`, optional): 主仓提供的密文 codec,
                与`fallback_loader`互斥, 优先使用.
            user_data_path (`str`, optional): 剪映用户数据目录, 用于草稿注册.

        Raises:
            `FileNotFoundError`: 路径不存在
        """
        self.folder_path = folder_path
        self.fallback_loader = fallback_loader
        self.content_codec = content_codec
        if not os.path.exists(self.folder_path):
            raise FileNotFoundError(f"根文件夹 {self.folder_path} 不存在")

        self._registration = DraftFolderRegistration(
            self.folder_path,
            user_data_path=user_data_path,
            content_codec=content_codec,
        )

    def list_drafts(self) -> List[str]:
        """Return child directory names without validating their draft structure."""
        return [
            name
            for name in os.listdir(self.folder_path)
            if os.path.isdir(os.path.join(self.folder_path, name))
        ]

    def has_draft(self, draft_name: str) -> bool:
        """Return whether a child directory with ``draft_name`` exists."""
        return draft_name in self.list_drafts()

    def remove(self, draft_name: str) -> None:
        """Delete a draft and remove its private registration references."""
        self._registration.remove_draft(draft_name)

    def create_folder(self, logical_folder_path: str) -> None:
        """Create a private Jianying logical folder under User Data."""
        self._registration.create_folder(logical_folder_path)

    def remove_folder(
        self,
        logical_folder_path: str,
        on_non_empty: Literal["block", "move_drafts_to_root", "delete_drafts"] = "block",
    ) -> None:
        """Remove one logical folder using the explicitly selected non-empty policy."""
        self._registration.remove_folder(logical_folder_path, on_non_empty)

    def move_draft_to_folder(self, draft_name: str, logical_folder_path: str) -> None:
        """Move a registered draft into a logical Jianying folder."""
        self._registration.move_draft_to_folder(draft_name, logical_folder_path)

    def move_draft_to_root(self, draft_name: str) -> None:
        """Move a registered draft back to the top-level logical view."""
        self._registration.move_draft_to_root(draft_name)

    def create_draft(
        self,
        draft_name: str,
        width: int,
        height: int,
        fps: int = 30,
        *,
        maintrack_adsorb: bool = True,
        allow_replace: bool = False,
    ) -> ScriptFile:
        """Create a new plaintext draft and register it on its first save."""
        draft_path = os.path.join(self.folder_path, draft_name)
        if os.path.exists(draft_path):
            if not allow_replace:
                raise FileExistsError(f"草稿文件夹 {draft_name} 已存在且不允许覆盖")
            self.remove(draft_name)

        os.makedirs(draft_path)
        shutil.copy(assets.get_asset_path("DRAFT_META_TEMPLATE"), os.path.join(draft_path, "draft_meta_info.json"))

        script_file = ScriptFile(width, height, fps, maintrack_adsorb)
        script_file.save_path = os.path.join(draft_path, DRAFT_FILE_NAME)
        return self._registration.configure_script_file(script_file, draft_name, is_new_draft=True)

    @staticmethod
    def _draft_file_path(draft_path: str) -> str:
        """草稿主文件的路径, 优先新版文件名, 回退旧版以兼容 5.9 及以下产出的草稿

        Args:
            draft_path (`str`): 草稿文件夹路径
        """
        new_path = os.path.join(draft_path, DRAFT_FILE_NAME)
        if os.path.exists(new_path):
            return new_path

        legacy_path = os.path.join(draft_path, LEGACY_DRAFT_FILE_NAME)
        if os.path.exists(legacy_path):
            return legacy_path

        # 两者均不存在时指向新版文件名, 使报错信息落在当前应有的路径上
        return new_path

    def inspect_material(self, draft_name: str) -> None:
        """Print sticker-material metadata for the selected draft."""
        draft_path = os.path.join(self.folder_path, draft_name)
        if not os.path.exists(draft_path):
            raise FileNotFoundError(f"草稿文件夹 {draft_name} 不存在")
        self.load_template(draft_name).inspect_material()

    def load_template(self, draft_name: str) -> ScriptFile:
        """Open one existing draft as an editable template."""
        draft_path = os.path.join(self.folder_path, draft_name)
        if not os.path.exists(draft_path):
            raise FileNotFoundError(f"草稿文件夹 {draft_name} 不存在")
        script_file = ScriptFile._load_template(
            self._draft_file_path(draft_path),
            fallback_loader=self.fallback_loader,
            content_codec=self.content_codec,
        )
        return self._registration.configure_script_file(script_file, draft_name, is_new_draft=False)

    def duplicate_as_template(
        self,
        template_name: str,
        new_draft_name: str,
        allow_replace: bool = False,
    ) -> ScriptFile:
        """Copy a draft and return the copy as a newly registered template."""
        template_path = os.path.join(self.folder_path, template_name)
        new_draft_path = os.path.join(self.folder_path, new_draft_name)
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"模板草稿 {template_name} 不存在")
        if os.path.normcase(os.path.abspath(template_path)) == os.path.normcase(os.path.abspath(new_draft_path)):
            raise ValueError("复制后的新草稿不能与模板草稿同名")
        if os.path.exists(new_draft_path):
            if not allow_replace:
                raise FileExistsError(f"新草稿 {new_draft_name} 已存在且不允许覆盖")
            self.remove(new_draft_name)

        shutil.copytree(template_path, new_draft_path, dirs_exist_ok=allow_replace)
        script_file = ScriptFile._load_template(
            self._draft_file_path(new_draft_path),
            fallback_loader=self.fallback_loader,
            content_codec=self.content_codec,
        )
        return self._registration.configure_script_file(script_file, new_draft_name, is_new_draft=True)
