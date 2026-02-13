import os
import json
import requests

BASE_URL = "https://storage.googleapis.com/magentadata/js/soundfonts/sgm_plus"
OUTPUT_DIR = "sgm_plus"


def download_file(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = requests.get(url)
    if r.status_code == 200:
        with open(path, "wb") as f:
            f.write(r.content)
        print("✔", path)
    else:
        print("✘ Failed:", url)


def main():
    instrument = "acoustic_grand_piano"
    keep_velocity = 79

    instrument_base_url = f"{BASE_URL}/{instrument}"
    instrument_dir = os.path.join(OUTPUT_DIR, instrument)

    print(f"\nDownloading instrument: {instrument} (single velocity)")

    # 下载 instrument.json
    inst_json_url = f"{instrument_base_url}/instrument.json"
    inst_json_path = os.path.join(instrument_dir, "instrument.json")
    download_file(inst_json_url, inst_json_path)

    if os.path.exists(inst_json_path):
        with open(inst_json_path, "r", encoding="utf-8") as f:
            inst_data = json.load(f)

        min_pitch = inst_data["minPitch"]
        max_pitch = inst_data["maxPitch"]

        # ⚠ 修改 velocities 为单层
        inst_data["velocities"] = [keep_velocity]

        # 覆盖写回 instrument.json
        with open(inst_json_path, "w", encoding="utf-8") as f:
            json.dump(inst_data, f, indent=2)

        # 只下载一个 velocity
        for pitch in range(min_pitch, max_pitch + 1):
            sample_file = f"p{pitch}_v{keep_velocity}.mp3"
            sample_url = f"{instrument_base_url}/{sample_file}"
            sample_path = os.path.join(instrument_dir, sample_file)
            download_file(sample_url, sample_path)

    # 生成一个极简 soundfont.json
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, "soundfont.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"name": "sgm_plus", "instruments": {"0": "acoustic_grand_piano"}},
            f,
            indent=2,
        )


if __name__ == "__main__":
    main()
