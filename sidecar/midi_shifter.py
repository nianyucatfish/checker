"""
Tick-level MIDI 平移,绕过 magenta-music 1.x 的 round-trip bug。

magenta 1.23.1 的 sequenceProtoToMidi 在原 midi 含多声部 overlap(legato/复音/和声)时,
重写出来的 note_on/note_off 顺序会被打乱(实测多处非法的同 channel 同 pitch 双 note_on)。
这里用 mido 直接在 tick 层加偏移,保证 raw event 顺序、velocity、channel、tempo 全部不变。
"""
from io import BytesIO
from typing import Dict, Mapping

import mido


def _detect_tempo(mf: mido.MidiFile) -> int:
    """找第一个 set_tempo,没有则用 MIDI 默认 500000 (120 BPM)。"""
    for track in mf.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                return msg.tempo
    return 500000


def shift_midi_bytes_per_track_index(
    midi_bytes: bytes, shifts: Mapping[int, float]
) -> bytes:
    """按 mido tracks 数组的 index 分别平移 — 适用于 type 1 多 track midi。

    shifts: {track_index: shift_seconds_float}。未列出的 track 不动。

    平移时 track 内的 meta 事件(set_tempo / time_signature 等)也跟随平移,
    保持它们和 channel events 的相对顺序 — 否则 set_tempo 可能排到 note_on 之后,
    播放器对前段 note 用默认 tempo,造成节奏错乱(实测 type 0 拖到最左的 bug)。
    """
    mf = mido.MidiFile(file=BytesIO(midi_bytes))
    spt = (_detect_tempo(mf) / 1_000_000.0) / mf.ticks_per_beat
    ticks_by_track: Dict[int, int] = {
        int(ti): int(round(float(s) / spt)) for ti, s in shifts.items()
    }

    for ti, track in enumerate(mf.tracks):
        track_shift = ticks_by_track.get(ti, 0)
        events = []
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            shifted = max(0, abs_tick + track_shift) if track_shift else abs_tick
            events.append((shifted, msg))
        events.sort(key=lambda e: e[0])

        new_track = mido.MidiTrack()
        prev_abs = 0
        for abs_t, msg in events:
            if msg.type == "end_of_track":
                continue  # mido 在 save 时自动追加
            new_msg = msg.copy()
            new_msg.time = abs_t - prev_abs
            new_track.append(new_msg)
            prev_abs = abs_t
        mf.tracks[ti] = new_track

    out = BytesIO()
    mf.save(file=out)
    return out.getvalue()


def magenta_instrument_to_track_index(midi_bytes: bytes) -> Dict[int, int]:
    """计算 magenta 1.x NoteSequence.note.instrument → mido track index 的映射。

    magenta 1.x 在读取 type 1 midi 时,把每个**有 note 的** mido track 按出现顺序
    赋一个连续 0-based instrument id(跳过 conductor track 等无 note 的 track)。
    """
    mf = mido.MidiFile(file=BytesIO(midi_bytes))
    mapping: Dict[int, int] = {}
    next_id = 0
    for ti, tr in enumerate(mf.tracks):
        if any(m.type == "note_on" and m.velocity > 0 for m in tr):
            mapping[next_id] = ti
            next_id += 1
    return mapping


def shift_midi_bytes_per_magenta_instrument(
    midi_bytes: bytes, shifts: Mapping[int, float]
) -> bytes:
    """按 magenta NoteSequence 的 instrument 编号平移。前端可以直接传 magenta instId。

    内部把 magenta instId 映射到 mido track index 后,委托给 shift_midi_bytes_per_track_index。
    """
    inst_to_track = magenta_instrument_to_track_index(midi_bytes)
    track_shifts: Dict[int, float] = {}
    for inst, sec in shifts.items():
        i = int(inst)
        if i in inst_to_track:
            track_shifts[inst_to_track[i]] = float(sec)
    return shift_midi_bytes_per_track_index(midi_bytes, track_shifts)
