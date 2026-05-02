"""
sidecar.schemas — REST API 的 Pydantic 输入输出模型。

这一层是 sidecar 与前端 / agent 的契约边界。所有跨进程交互的数据形状都在这里定义。
后续会用 datamodel-code-generator 从这里导出 TypeScript 类型给前端用。
"""

from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field


# ====================================================
#  通用响应
# ====================================================


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str


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


class DurationSummary(BaseModel):
    ok: bool = True
    folder: str
    inconsistent: bool
    summary: Optional[str] = None  # 旧逻辑给的人类可读摘要，无不一致时为 None


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
