"""定义云端音频素材及其相关类

云端素材不依赖本地文件, 而是记录剪映云端曲库中的音乐 id,
在剪映打开草稿时按 id 解析出实际的音乐内容.
"""

import uuid

from typing import Any, Dict, Optional, Union

from .local_materials import AudioMaterial
from .metadata.cloud_music import CloudMusicType
from .time_util import tim


class CloudMusicMaterial(AudioMaterial):
    """云端音乐素材, 对应草稿中 `type` 为 `"music"` 的音频素材

    一份素材可以在多个片段中使用.
    """

    music_id: str
    """云端曲库音乐 id, 剪映按此 id 解析音乐内容"""

    def __init__(self, music: Union[str, CloudMusicType], material_name: Optional[str] = None, duration: Optional[Union[str, int]] = None):
        """以云端音乐 id 或 `CloudMusicType` 枚举成员构造一个云端音乐素材

        传入 `CloudMusicType` 枚举成员时, 素材名称与时长自动取自库内元数据,
        无需手动指定; 也可显式传入 `duration` 覆盖默认时长 (如截短使用).

        Args:
            music (`str` or `CloudMusicType`): 云端曲库音乐 id 或库内枚举成员.
                手动指定 id 时, 可从使用过该音乐的剪映草稿 `draft_content.json`
                的 `materials.audios` 中 `type` 为 `"music"` 的条目的 `music_id`
                字段读取.
            material_name (`str`, optional): 素材名称, 显示在剪映素材列表中;
                传入枚举成员时缺省为库内标题.
            duration (`str` or `int`, optional): 素材时长, 单位为微秒, 若为字符串则会调用
                `tim()`函数进行解析, 如 `"1m30s"`; 传入枚举成员时缺省为库内时长.

        Raises:
            `ValueError`: 时长字符串无法被`tim()`解析, 或手动指定 music id
                时未提供 `material_name` / `duration`.
        """

        default_name = None
        default_duration = None
        if isinstance(music, CloudMusicType):
            meta = music.value
            self.music_id = meta.music_id
            default_name = meta.title
            default_duration = meta.duration
        else:
            self.music_id = music
        if material_name is None:
            material_name = default_name
        if material_name is None:
            raise ValueError("手动指定 music id 时必须提供 material_name")
        if duration is None:
            duration = default_duration
        if duration is None:
            raise ValueError("手动指定 music id 时必须提供 duration")

        self.material_id = uuid.uuid4().hex
        self.material_name = material_name
        self.path = f"<cloud://{self.music_id}>"
        self.duration = tim(duration)

    def export_json(self) -> Dict[str, Any]:
        json_dict = super().export_json()
        json_dict["type"] = "music"
        json_dict["music_id"] = self.music_id
        return json_dict
