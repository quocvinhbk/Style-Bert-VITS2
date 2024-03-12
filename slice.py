import argparse
import shutil
from pathlib import Path

import soundfile as sf
import torch
import yaml
from tqdm import tqdm

from style_bert_vits2.logging import logger
from style_bert_vits2.utils.stdout_wrapper import SAFE_STDOUT


# TODO: 並列処理による高速化

vad_model, utils = torch.hub.load(
    repo_or_dir="snakers4/silero-vad",
    model="silero_vad",
    onnx=True,
    trust_repo=True,
)

(get_speech_timestamps, _, read_audio, *_) = utils


def get_stamps(
    audio_file: Path,
    min_silence_dur_ms: int = 700,
    min_sec: float = 2,
    max_sec: float = 12,
):
    """
    min_silence_dur_ms: int (ミリ秒):
        このミリ秒数以上を無音だと判断する。
        逆に、この秒数以下の無音区間では区切られない。
        小さくすると、音声がぶつ切りに小さくなりすぎ、
        大きくすると音声一つ一つが長くなりすぎる。
        データセットによってたぶん要調整。
    min_sec: float (秒):
        この秒数より小さい発話は無視する。
    max_sec: float (秒):
        この秒数より大きい発話は無視する。
    """

    sampling_rate = 16000  # 16kHzか8kHzのみ対応

    min_ms = int(min_sec * 1000)

    wav = read_audio(str(audio_file), sampling_rate=sampling_rate)
    speech_timestamps = get_speech_timestamps(
        wav,
        vad_model,
        sampling_rate=sampling_rate,
        min_silence_duration_ms=min_silence_dur_ms,
        min_speech_duration_ms=min_ms,
        max_speech_duration_s=max_sec,
    )

    return speech_timestamps


def split_wav(
    audio_file: Path,
    target_dir: Path,
    min_sec: float = 2,
    max_sec: float = 12,
    min_silence_dur_ms: int = 700,
) -> tuple[float, int]:
    margin: int = 200  # ミリ秒単位で、音声の前後に余裕を持たせる
    speech_timestamps = get_stamps(
        audio_file,
        min_silence_dur_ms=min_silence_dur_ms,
        min_sec=min_sec,
        max_sec=max_sec,
    )

    data, sr = sf.read(audio_file)

    total_ms = len(data) / sr * 1000

    file_name = audio_file.stem
    target_dir.mkdir(parents=True, exist_ok=True)

    total_time_ms: float = 0
    count = 0

    # タイムスタンプに従って分割し、ファイルに保存
    for i, ts in enumerate(speech_timestamps):
        start_ms = max(ts["start"] / 16 - margin, 0)
        end_ms = min(ts["end"] / 16 + margin, total_ms)

        start_sample = int(start_ms / 1000 * sr)
        end_sample = int(end_ms / 1000 * sr)
        segment = data[start_sample:end_sample]

        sf.write(str(target_dir / f"{file_name}-{i}.wav"), segment, sr)
        total_time_ms += end_ms - start_ms
        count += 1

    return total_time_ms / 1000, count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--min_sec", "-m", type=float, default=2, help="Minimum seconds of a slice"
    )
    parser.add_argument(
        "--max_sec", "-M", type=float, default=12, help="Maximum seconds of a slice"
    )
    parser.add_argument(
        "--input_dir",
        "-i",
        type=str,
        default="inputs",
        help="Directory of input wav files",
    )
    parser.add_argument(
        "--model_name",
        type=str,
        required=True,
        help="The result will be in Data/{model_name}/raw/ (if Data is dataset_root in configs/paths.yml)",
    )
    parser.add_argument(
        "--min_silence_dur_ms",
        "-s",
        type=int,
        default=700,
        help="Silence above this duration (ms) is considered as a split point.",
    )
    args = parser.parse_args()

    with open(Path("configs/paths.yml"), "r", encoding="utf-8") as f:
        path_config: dict[str, str] = yaml.safe_load(f.read())
        dataset_root = path_config["dataset_root"]

    model_name = str(args.model_name)
    input_dir = Path(args.input_dir)
    output_dir = Path(dataset_root) / model_name / "raw"
    min_sec: float = args.min_sec
    max_sec: float = args.max_sec
    min_silence_dur_ms: int = args.min_silence_dur_ms

    wav_files = Path(input_dir).glob("**/*.wav")
    wav_files = list(wav_files)
    logger.info(f"Found {len(wav_files)} wav files.")
    if output_dir.exists():
        logger.warning(f"Output directory {output_dir} already exists, deleting...")
        shutil.rmtree(output_dir)

    total_sec = 0
    total_count = 0
    for wav_file in tqdm(wav_files, file=SAFE_STDOUT):
        time_sec, count = split_wav(
            audio_file=wav_file,
            target_dir=output_dir,
            min_sec=min_sec,
            max_sec=max_sec,
            min_silence_dur_ms=min_silence_dur_ms,
        )
        total_sec += time_sec
        total_count += count

    logger.info(
        f"Slice done! Total time: {total_sec / 60:.2f} min, {total_count} files."
    )
