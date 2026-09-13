"""从录屏视频里提取音效，掐掉首尾静音并做峰值归一化。

用法：
    python tools/extract_audio.py <视频路径> <输出名>

例：
    python tools/extract_audio.py "C:/.../lv_0_20260913175223.mp4" click
产出 assets/sounds/click.wav
"""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "sounds"

# 静音判定：低于峰值这么多 dB 的窗口算静音
SILENCE_DB = -35.0
# 首尾各保留一点余量，避免把起音/尾音削掉
PAD_MS = 40
WIN_MS = 10


def main(video: str, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    raw = OUT_DIR / f"_{name}_raw.wav"

    subprocess.run(
        [ff, "-y", "-i", video, "-vn", "-ac", "1", "-ar", "44100",
         "-c:a", "pcm_s16le", str(raw)],
        check=True, capture_output=True,
    )
    print(f"已提取原始音轨 -> {raw.name}")

    with wave.open(str(raw), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        pcm = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64)

    dur = len(pcm) / sr
    peak = np.abs(pcm).max() / 32768.0
    print(f"  采样率 {sr} Hz  时长 {dur:.3f}s  峰值 {peak:.4f} ({20*np.log10(peak+1e-12):.1f} dBFS)")

    # 逐窗口 RMS 包络，找有声区间
    win = max(1, int(sr * WIN_MS / 1000))
    nwin = len(pcm) // win
    env = np.sqrt((pcm[: nwin * win].reshape(nwin, win) ** 2).mean(axis=1))
    env_db = 20 * np.log10(env / 32768.0 + 1e-12)

    thr = env_db.max() + SILENCE_DB
    voiced = np.nonzero(env_db > thr)[0]
    if len(voiced) == 0:
        print("  ！没找到有声区间，保留原音")
        lo, hi = 0, len(pcm)
    else:
        pad = int(sr * PAD_MS / 1000)
        lo = max(0, voiced[0] * win - pad)
        hi = min(len(pcm), (voiced[-1] + 1) * win + pad)
        print(f"  有声区间 {voiced[0]*win/sr:.3f}s ~ {(voiced[-1]+1)*win/sr:.3f}s"
              f"  -> 掐掉首 {lo/sr:.3f}s / 尾 {(len(pcm)-hi)/sr:.3f}s")

    seg = pcm[lo:hi]

    # 峰值归一化到 -1 dBFS
    p = np.abs(seg).max()
    if p > 0:
        seg = seg * (32768.0 * 0.891 / p)
    seg = np.clip(seg, -32768, 32767).astype(np.int16)

    out = OUT_DIR / f"{name}.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(seg.tobytes())

    new_dur = len(seg) / sr
    print(f"已输出 {out.relative_to(ROOT)}  时长 {new_dur:.3f}s  峰值 "
          f"{20*np.log10(np.abs(seg).max()/32768.0):.1f} dBFS")

    raw.unlink(missing_ok=True)
    print(f"（已删除临时文件 {raw.name}）")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
