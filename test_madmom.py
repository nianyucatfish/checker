from madmom.features.downbeats import RNNDownBeatProcessor, DBNDownBeatTrackingProcessor

FPS = 100

# 1️⃣ Downbeat RNN（不是 Beat RNN）
act = RNNDownBeatProcessor(fps=FPS)(r"C:\Users\YH\Desktop\演员_Mix_A.wav")

# 2️⃣ Downbeat DBN（指定拍号）
processor = DBNDownBeatTrackingProcessor(
    beats_per_bar=[4], fps=FPS, min_bpm=55, max_bpm=190  # 4/4 拍
)

downbeats = processor(act)

print(downbeats[:20])
