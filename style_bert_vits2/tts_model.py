import warnings
from pathlib import Path
from typing import Any, Optional, Union

import gradio as gr
import numpy as np
import torch
from gradio.processing_utils import convert_to_16_bit_wav
from numpy.typing import NDArray

from style_bert_vits2.constants import (
    DEFAULT_ASSIST_TEXT_WEIGHT,
    DEFAULT_LENGTH,
    DEFAULT_LINE_SPLIT,
    DEFAULT_NOISE,
    DEFAULT_NOISEW,
    DEFAULT_SDP_RATIO,
    DEFAULT_SPLIT_INTERVAL,
    DEFAULT_STYLE,
    DEFAULT_STYLE_WEIGHT,
    Languages,
)
from style_bert_vits2.models.hyper_parameters import HyperParameters
from style_bert_vits2.models.infer import get_net_g, infer
from style_bert_vits2.models.models import SynthesizerTrn
from style_bert_vits2.models.models_jp_extra import SynthesizerTrn as SynthesizerTrnJPExtra
from style_bert_vits2.logging import logger


def adjust_voice(
    fs: int,
    wave: NDArray[Any],
    pitch_scale: float,
    intonation_scale: float,
) -> tuple[int, NDArray[Any]]:

    if pitch_scale == 1.0 and intonation_scale == 1.0:
        # 初期値の場合は、音質劣化を避けるためにそのまま返す
        return fs, wave

    try:
        import pyworld
    except ImportError:
        raise ImportError(
            "pyworld is not installed. Please install it by `pip install pyworld`"
        )

    # pyworld で f0 を加工して合成
    # pyworld よりもよいのがあるかもしれないが……
    ## pyworld は Cython で書かれているが、スタブファイルがないため型補完が全く効かない…

    wave = wave.astype(np.double)

    # 質が高そうだしとりあえずharvestにしておく
    f0, t = pyworld.harvest(wave, fs)  # type: ignore

    sp = pyworld.cheaptrick(wave, f0, t, fs)  # type: ignore
    ap = pyworld.d4c(wave, f0, t, fs)  # type: ignore

    non_zero_f0 = [f for f in f0 if f != 0]
    f0_mean = sum(non_zero_f0) / len(non_zero_f0)

    for i, f in enumerate(f0):
        if f == 0:
            continue
        f0[i] = pitch_scale * f0_mean + intonation_scale * (f - f0_mean)

    wave = pyworld.synthesize(f0, sp, ap, fs)  # type: ignore
    return fs, wave


class Model:
    def __init__(
        self, model_path: Path, config_path: Path, style_vec_path: Path, device: str
    ):
        self.model_path: Path = model_path
        self.config_path: Path = config_path
        self.style_vec_path: Path = style_vec_path
        self.device: str = device
        self.hps: HyperParameters = HyperParameters.load_from_json(self.config_path)
        self.spk2id: dict[str, int] = self.hps.data.spk2id
        self.id2spk: dict[int, str] = {v: k for k, v in self.spk2id.items()}

        self.num_styles: int = self.hps.data.num_styles
        if hasattr(self.hps.data, "style2id"):
            self.style2id: dict[str, int] = self.hps.data.style2id
        else:
            self.style2id: dict[str, int] = {str(i): i for i in range(self.num_styles)}
        if len(self.style2id) != self.num_styles:
            raise ValueError(
                f"Number of styles ({self.num_styles}) does not match the number of style2id ({len(self.style2id)})"
            )

        self.style_vectors: NDArray[Any] = np.load(self.style_vec_path)
        if self.style_vectors.shape[0] != self.num_styles:
            raise ValueError(
                f"The number of styles ({self.num_styles}) does not match the number of style vectors ({self.style_vectors.shape[0]})"
            )

        self.net_g: Union[SynthesizerTrn, SynthesizerTrnJPExtra, None] = None

    def load_net_g(self):
        self.net_g = get_net_g(
            model_path=str(self.model_path),
            version=self.hps.version,
            device=self.device,
            hps=self.hps,
        )

    def get_style_vector(self, style_id: int, weight: float = 1.0) -> NDArray[Any]:
        mean = self.style_vectors[0]
        style_vec = self.style_vectors[style_id]
        style_vec = mean + (style_vec - mean) * weight
        return style_vec

    def get_style_vector_from_audio(
        self, audio_path: str, weight: float = 1.0
    ) -> NDArray[Any]:
        from style_gen import get_style_vector

        xvec = get_style_vector(audio_path)
        mean = self.style_vectors[0]
        xvec = mean + (xvec - mean) * weight
        return xvec

    def infer(
        self,
        text: str,
        language: Languages = Languages.JP,
        sid: int = 0,
        reference_audio_path: Optional[str] = None,
        sdp_ratio: float = DEFAULT_SDP_RATIO,
        noise: float = DEFAULT_NOISE,
        noisew: float = DEFAULT_NOISEW,
        length: float = DEFAULT_LENGTH,
        line_split: bool = DEFAULT_LINE_SPLIT,
        split_interval: float = DEFAULT_SPLIT_INTERVAL,
        assist_text: Optional[str] = None,
        assist_text_weight: float = DEFAULT_ASSIST_TEXT_WEIGHT,
        use_assist_text: bool = False,
        style: str = DEFAULT_STYLE,
        style_weight: float = DEFAULT_STYLE_WEIGHT,
        given_tone: Optional[list[int]] = None,
        pitch_scale: float = 1.0,
        intonation_scale: float = 1.0,
    ) -> tuple[int, NDArray[Any]]:
        logger.info(f"Start generating audio data from text:\n{text}")
        if language != "JP" and self.hps.version.endswith("JP-Extra"):
            raise ValueError(
                "The model is trained with JP-Extra, but the language is not JP"
            )
        if reference_audio_path == "":
            reference_audio_path = None
        if assist_text == "" or not use_assist_text:
            assist_text = None

        if self.net_g is None:
            self.load_net_g()
        assert self.net_g is not None
        if reference_audio_path is None:
            style_id = self.style2id[style]
            style_vector = self.get_style_vector(style_id, style_weight)
        else:
            style_vector = self.get_style_vector_from_audio(
                reference_audio_path, style_weight
            )
        if not line_split:
            with torch.no_grad():
                audio = infer(
                    text=text,
                    sdp_ratio=sdp_ratio,
                    noise_scale=noise,
                    noise_scale_w=noisew,
                    length_scale=length,
                    sid=sid,
                    language=language,
                    hps=self.hps,
                    net_g=self.net_g,
                    device=self.device,
                    assist_text=assist_text,
                    assist_text_weight=assist_text_weight,
                    style_vec=style_vector,
                    given_tone=given_tone,
                )
        else:
            texts = text.split("\n")
            texts = [t for t in texts if t != ""]
            audios = []
            with torch.no_grad():
                for i, t in enumerate(texts):
                    audios.append(
                        infer(
                            text=t,
                            sdp_ratio=sdp_ratio,
                            noise_scale=noise,
                            noise_scale_w=noisew,
                            length_scale=length,
                            sid=sid,
                            language=language,
                            hps=self.hps,
                            net_g=self.net_g,
                            device=self.device,
                            assist_text=assist_text,
                            assist_text_weight=assist_text_weight,
                            style_vec=style_vector,
                        )
                    )
                    if i != len(texts) - 1:
                        audios.append(np.zeros(int(44100 * split_interval)))
                audio = np.concatenate(audios)
        logger.info("Audio data generated successfully")
        if not (pitch_scale == 1.0 and intonation_scale == 1.0):
            _, audio = adjust_voice(
                fs=self.hps.data.sampling_rate,
                wave=audio,
                pitch_scale=pitch_scale,
                intonation_scale=intonation_scale,
            )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            audio = convert_to_16_bit_wav(audio)
        return (self.hps.data.sampling_rate, audio)


class ModelHolder:
    def __init__(self, root_dir: Path, device: str):
        self.root_dir: Path = root_dir
        self.device: str = device
        self.model_files_dict: dict[str, list[Path]] = {}
        self.current_model: Optional[Model] = None
        self.model_names: list[str] = []
        self.models: list[Model] = []
        self.models_info: list[dict[str, Union[str, list[str]]]] = []
        self.refresh()

    def refresh(self):
        self.model_files_dict = {}
        self.model_names = []
        self.current_model = None
        self.models_info = []

        model_dirs = [d for d in self.root_dir.iterdir() if d.is_dir()]
        for model_dir in model_dirs:
            model_files = [
                f
                for f in model_dir.iterdir()
                if f.suffix in [".pth", ".pt", ".safetensors"]
            ]
            if len(model_files) == 0:
                logger.warning(f"No model files found in {model_dir}, so skip it")
                continue
            config_path = model_dir / "config.json"
            if not config_path.exists():
                logger.warning(
                    f"Config file {config_path} not found, so skip {model_dir}"
                )
                continue
            self.model_files_dict[model_dir.name] = model_files
            self.model_names.append(model_dir.name)
            hps = HyperParameters.load_from_json(config_path)
            style2id: dict[str, int] = hps.data.style2id
            styles = list(style2id.keys())
            spk2id: dict[str, int] = hps.data.spk2id
            speakers = list(spk2id.keys())
            self.models_info.append({
                "name": model_dir.name,
                "files": [str(f) for f in model_files],
                "styles": styles,
                "speakers": speakers,
            })

    def load_model(self, model_name: str, model_path_str: str):
        model_path = Path(model_path_str)
        if model_name not in self.model_files_dict:
            raise ValueError(f"Model `{model_name}` is not found")
        if model_path not in self.model_files_dict[model_name]:
            raise ValueError(f"Model file `{model_path}` is not found")
        if self.current_model is None or self.current_model.model_path != model_path:
            self.current_model = Model(
                model_path=model_path,
                config_path=self.root_dir / model_name / "config.json",
                style_vec_path=self.root_dir / model_name / "style_vectors.npy",
                device=self.device,
            )
        return self.current_model

    def load_model_gr(
        self, model_name: str, model_path_str: str
    ) -> tuple[gr.Dropdown, gr.Button, gr.Dropdown]:
        model_path = Path(model_path_str)
        if model_name not in self.model_files_dict:
            raise ValueError(f"Model `{model_name}` is not found")
        if model_path not in self.model_files_dict[model_name]:
            raise ValueError(f"Model file `{model_path}` is not found")
        if (
            self.current_model is not None
            and self.current_model.model_path == model_path
        ):
            # Already loaded
            speakers = list(self.current_model.spk2id.keys())
            styles = list(self.current_model.style2id.keys())
            return (
                gr.Dropdown(choices=styles, value=styles[0]),  # type: ignore
                gr.Button(interactive=True, value="音声合成"),
                gr.Dropdown(choices=speakers, value=speakers[0]),  # type: ignore
            )
        self.current_model = Model(
            model_path=model_path,
            config_path=self.root_dir / model_name / "config.json",
            style_vec_path=self.root_dir / model_name / "style_vectors.npy",
            device=self.device,
        )
        speakers = list(self.current_model.spk2id.keys())
        styles = list(self.current_model.style2id.keys())
        return (
            gr.Dropdown(choices=styles, value=styles[0]),  # type: ignore
            gr.Button(interactive=True, value="音声合成"),
            gr.Dropdown(choices=speakers, value=speakers[0]),  # type: ignore
        )

    def update_model_files_gr(self, model_name: str) -> gr.Dropdown:
        model_files = self.model_files_dict[model_name]
        return gr.Dropdown(choices=model_files, value=model_files[0])  # type: ignore

    def update_model_names_gr(self) -> tuple[gr.Dropdown, gr.Dropdown, gr.Button]:
        self.refresh()
        initial_model_name = self.model_names[0]
        initial_model_files = self.model_files_dict[initial_model_name]
        return (
            gr.Dropdown(choices=self.model_names, value=initial_model_name),  # type: ignore
            gr.Dropdown(choices=initial_model_files, value=initial_model_files[0]),  # type: ignore
            gr.Button(interactive=False),  # For tts_button
        )
