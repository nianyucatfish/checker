"""
sidecar.schemas — REST API 的 Pydantic 输入输出模型。

这一层是 sidecar 与前端 / agent 的契约边界。所有跨进程交互的数据形状都在这里定义。
后续会用 datamodel-code-generator 从这里导出 TypeScript 类型给前端用。
"""

from typing import List, Optional, Dict, Any, Literal

from pydantic import BaseModel, Field


# ====================================================
#  通用响应
# ====================================================


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatIn(BaseModel):
    messages: List[ChatMessage]


class ChatOut(BaseModel):
    ok: bool = True
    message: ChatMessage
    model: str


# ====================================================
#  CheckError 投影
# ====================================================


class CheckErrorOut(BaseModel):
    """对 sidecar.errors.CheckError 的可序列化投影。"""

    code: str
    severity: str
    path: str
    message: str
    expected: Dict[str, Any] = Field(default_factory=dict)
    fix_hints: List[str] = Field(default_factory=list)
    machine_fixable: bool = False


# ====================================================
#  list_workspace
# ====================================================


class ListWorkspaceOut(BaseModel):
    ok: bool = True
    songs: List[str]


# ====================================================
#  check_song / check_workspace
# ====================================================


class CheckResult(BaseModel):
    ok: bool = True
    scope: str  # 'song' | 'workspace'
    errors: Dict[str, List[CheckErrorOut]]
    paths_with_errors: int
    total_errors: int


# ====================================================
#  get_audio_metadata
# ====================================================


class AudioMetadata(BaseModel):
    ok: bool = True
    path: str
    samplerate: int
    channels: int
    subtype: str
    frames: int
    duration_seconds: float


# ====================================================
#  get_duration_summary
# ====================================================


# ====================================================
#  list_song_files
# ====================================================


class FileEntry(BaseModel):
    path: str
    name: str
    rel_path: str           # 相对 song_path 的路径
    size_bytes: int
    is_audio: bool
    audio_meta: Optional[AudioMetadata] = None  # 仅在 is_audio 且能成功读取时填


class ListSongFilesOut(BaseModel):
    ok: bool = True
    song_path: str
    files: List[FileEntry]


# ====================================================
#  list_dir（一级目录列举，供前端文件树懒加载）
# ====================================================


class DirEntry(BaseModel):
    path: str          # 绝对路径
    name: str
    is_dir: bool
    size_bytes: int = 0  # 文件夹固定 0
    ext: str = ""        # 不带点的小写扩展名；目录为空


class ListDirOut(BaseModel):
    ok: bool = True
    path: str
    entries: List[DirEntry]


# ====================================================
#  propose_renames / apply_renames
# ====================================================


class RenameOpModel(BaseModel):
    src: str
    dst: str
    kind: str  # song_folder | managed_dir | file


class ProposeRenamesIn(BaseModel):
    song_paths: List[str]


class ProposeRenamesOut(BaseModel):
    ok: bool = True
    ops: List[RenameOpModel]
    conflicts: List[str]


class ApplyRenamesIn(BaseModel):
    ops: List[RenameOpModel]


class ApplyRenamesOut(BaseModel):
    ok: bool = True
    executed: List[RenameOpModel]
    errors: List[str]
    path_updates: Dict[str, str]


# ====================================================
#  pad_song_to_longest
# ====================================================


class PadSongIn(BaseModel):
    song_path: str


class PadResultOut(BaseModel):
    ok: bool
    padded: int
    max_duration: Optional[float] = None
    error: Optional[str] = None


# ====================================================
#  read_csv / read_text
# ====================================================


class ReadCsvOut(BaseModel):
    ok: bool = True
    path: str
    rows: List[List[str]]
    total_rows: int
    truncated: bool


class ReadTextOut(BaseModel):
    ok: bool = True
    path: str
    content: str
    truncated: bool


# ====================================================
#  write_csv / write_text（原子写）
# ====================================================


class WriteCsvIn(BaseModel):
    path: str
    rows: List[List[str]]


class WriteTextIn(BaseModel):
    path: str
    content: str


class WriteResultOut(BaseModel):
    ok: bool = True
    path: str
    bytes_written: int


# ====================================================
#  get_audio_peaks（服务端预算波形包络，避免渲染端 decodeAudioData OOM）
# ====================================================


class AudioPeaksOut(BaseModel):
    ok: bool = True
    path: str
    samplerate: int
    channels: int
    frames: int
    duration_seconds: float
    columns: int
    mins: List[float]
    maxs: List[float]


# ====================================================
#  get_audio_durations（批量取音频时长，供文件树渲染）
# ====================================================


class AudioDurationItem(BaseModel):
    frames: int
    samplerate: int
    duration_seconds: float


class GetAudioDurationsIn(BaseModel):
    paths: List[str]


class GetAudioDurationsOut(BaseModel):
    ok: bool = True
    # 读不出 / 非音频值为 None;前端用 frames+samplerate 做精确同帧对比
    durations: Dict[str, Optional[AudioDurationItem]]


# ====================================================
#  rename_path / delete_paths / copy_paths / move_paths
# ====================================================


class RenamePathIn(BaseModel):
    src: str
    dst: str  # 完整目标绝对路径(同目录改名也走这条)


class DeletePathsIn(BaseModel):
    paths: List[str]


class CopyPathsIn(BaseModel):
    srcs: List[str]
    dst_dir: str  # 目标父目录;源文件名保留


class MovePathsIn(BaseModel):
    srcs: List[str]
    dst_dir: str


class FileOpResultOut(BaseModel):
    """通用文件操作结果。executed/errors 一一对应输入的 srcs 顺序。"""

    ok: bool = True
    executed: List[str] = Field(default_factory=list)  # 成功后的目标路径
    errors: List[str] = Field(default_factory=list)    # "src: <error>" 形式

