"""Apex Pro -- cloud voice CLONING, on Modal. Replaces the fixed-voice-menu
approach in app.py: instead of picking from a small preset list, the customer
records/uploads ~1-2 minutes of ANY voice, and this service clones it for
real-time conversion, with no training step -- a fundamentally different
kind of model (zero-shot voice conversion) than the RVC engine in app.py,
which needs a real training run per voice.

Model: Seed-VC (https://github.com/Plachtaa/seed-vc), MIT-licensed inference
code wrapping GPL-3.0-licensed model weights/training code from the same
project -- see LICENSE_NOTE below before ever shipping this to run locally.

Pipeline per session:
  1. Customer's reference clip (~1-2 min, any voice) -> one-time "prompt
     conditioning" pass (semantic features + speaker embedding + mel of the
     reference) -- computed ONCE per session, cached, reused for every
     subsequent chunk. This is what makes it "no training": there's no
     gradient update, just a forward pass over the reference audio.
  2. Each live mic chunk -> semantic features (with ~2s of rolling context,
     see _CE_DIT_CONTEXT_S below) -> the diffusion model (conditioned on the
     cached reference) -> vocoder -> converted waveform.

LICENSE_NOTE: Seed-VC's model weights and training code are GPL-3.0. Running
it as OUR OWN cloud service (customers only ever talk to it over a network
API, never receive the code or weights) does not trigger GPL's copyleft
distribution requirements. It must NOT be bundled into the downloadable
desktop app to run locally -- that would be "distribution" and would carry
real obligations. Cloud-only, by design, same as the RVC cloud voice.

Simplification vs. the upstream real-time-gui.py: that script uses a
sophisticated SOLA (Synchronized OverLap-Add) crossfade to hide the seams
between chunks, plus voice-activity detection and RMS-based silence
handling. This first version uses a simpler rolling-context buffer with a
linear crossfade -- the same "buffer into windows, hard-but-smoothed
boundary" philosophy already used by the RVC engine (rvc.py /
cloud-voice/app.py) -- correct and functional, not a byte-for-byte port of
their more polished blending. Quality can be tuned once there's real
listening feedback.

Deploy:  modal deploy cloud-voice/clone_app.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import modal

app = modal.App("apexcam-voice-clone")


# --- token verification -- identical scheme to app.py's (same shared secret,
# same "cloud_voice" scope) so the existing server-side minting route
# (server/app/security.py's make_voice_token) works unchanged for this
# service too. Duplicated rather than imported for the same reason as
# app.py: this container doesn't have the server's codebase. -------------
def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify_voice_token(token: str) -> int | None:
    secret = os.environ.get("APEXCAM_VOICE_SECRET", "").encode()
    if not secret:
        return None
    try:
        payload, sig = token.split(".")
        expected = base64.urlsafe_b64encode(
            hmac.new(secret, payload.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64d(payload))
        if data.get("scope") != "cloud_voice":
            return None
        if data.get("exp", 0) < time.time():
            return None
        return int(data["uid"])
    except Exception:
        return None


# --- image: CUDA runtime (same reasoning as app.py -- onnxruntime isn't used
# here, but torch's CUDA wheels need the same real CUDA 12.x runtime libs)
# plus seed-vc's own dependencies, plus a git clone of seed-vc itself so we
# can import its model-definition code (modules/, hf_utils.py) without
# vendoring it into this repo. --------------------------------------------
image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.11")
    .apt_install("git")
    .pip_install(
        "torch==2.4.0", "torchaudio==2.4.0",
        extra_index_url="https://download.pytorch.org/whl/cu121",
    )
    .pip_install(
        "fastapi",
        "scipy==1.13.1",
        "librosa==0.10.2",
        "huggingface-hub>=0.28.1",
        "munch==4.0.0",
        "einops==0.8.0",
        "transformers==4.46.3",
        "soundfile==0.12.1",
        "numpy==1.26.4",
        "hydra-core==1.3.2",
        "pyyaml",
        "python-dotenv",
    )
    .run_commands(
        "git clone --depth 1 https://github.com/Plachtaa/seed-vc.git /seedvc",
        # modules/length_regulator.py does `from dac.nn.quantize import
        # VectorQuantize` at module level, but InterpolateRegulator only
        # ever instantiates it when vector_quantize=True (see its __init__)
        # -- our config (config_dit_mel_seed_uvit_xlsr_tiny.yml) never sets
        # that, so it's purely an unused import for us. The REAL package
        # (descript-audio-codec) drags in descript-audiotools, which pins
        # protobuf<3.20 -- incompatible with the protobuf version Modal's
        # OWN generated stubs need (confirmed: forcing protobuf down into
        # that old range breaks "import modal" itself inside the container
        # with "Enum VolumeFsVersion has no value defined for name
        # 'ValueType'"). A tiny local stub sidesteps that whole dependency
        # fight instead of fighting a genuinely unresolvable pip conflict.
        "mkdir -p /stubs/dac/nn",
        "touch /stubs/dac/__init__.py",
        "touch /stubs/dac/nn/__init__.py",
        "printf 'import torch.nn as nn\\n\\n\\nclass VectorQuantize(nn.Module):\\n"
        "    \"\"\"Stub -- real seed-vc configs never instantiate this (see\\n"
        "    modules/length_regulator.py: only built when vector_quantize=True,\\n"
        "    which our config never sets). Exists purely so the unconditional\\n"
        "    module-level import succeeds.\"\"\"\\n\\n"
        "    def __init__(self, *a, **kw):\\n"
        "        super().__init__()\\n' > /stubs/dac/nn/quantize.py",
    )
)

# No modal.Volume for model-weight caching, matching app.py (the RVC
# service) -- keeps this simple; a cold container re-downloads weights from
# HuggingFace instead of reusing a cache, but warm containers (the common
# case once traffic starts) are unaffected. (Earlier debugging found that
# Volume support requires a newer protobuf than descript-audiotools could
# tolerate in the same environment -- moot now that the dac dependency is
# stubbed out below instead of pip-installed for real, but a Volume was
# never actually needed here regardless.)

SR_MODEL = None          # set from the loaded config, model's native sample rate
_CE_DIT_CONTEXT_S = 2.0   # seconds of rolling context the model needs before each block (matches upstream default ce_dit_difference)
_BLOCK_S = 1.0            # seconds of NEW audio converted per call -- a tradeoff: bigger = fewer, slower model calls with more context per call; smaller = choppier but lower per-call latency
_DIFFUSION_STEPS = 10     # upstream's default; fewer = faster/lower quality, more = slower/better
_MAX_REFERENCE_S = 120    # 2 minutes -- matches what we're telling customers to record


class CloneSession:
    """One connected customer's state: the shared (big, slow-to-load) model
    is loaded ONCE per container via VoiceCloneServer.load(); this class
    holds only the lightweight PER-CUSTOMER reference conditioning, computed
    once from their uploaded clip and reused for every audio chunk after."""

    def __init__(self, model_set, device) -> None:
        (self.model, self.semantic_fn, self.vocoder_fn, self.campplus_model,
         self.to_mel, self.mel_fn_args) = model_set
        # `self.model` is a Munch of named submodules (length_regulator, cfm,
        # ...), not itself an nn.Module -- it has no .parameters()/.device of
        # its own, so the device is threaded in explicitly from load()
        # rather than guessed from the model object.
        self.device = device
        self.sr = self.mel_fn_args["sampling_rate"]
        self.hop_length = self.mel_fn_args["hop_size"]
        self.prompt_condition = None
        self.mel2 = None
        self.style2 = None
        self._in_buf = np.zeros(0, np.float32)   # rolling buffer at self.sr
        self._ctx_tail = None   # torch tensor, previous block's tail audio, used as context for the next call

    def set_reference(self, reference_wav: np.ndarray) -> None:
        """One-time pass over the customer's reference clip -- this is the
        entire "cloning" step. No gradient update, no training; just a
        forward pass whose output (prompt_condition/mel2/style2) is cached
        and reused for every chunk in this session."""
        import time as _time

        import torch
        import torchaudio

        def _t(label, t0):
            print(f"  set_reference: {label} took {_time.time()-t0:.2f}s", flush=True)
            return _time.time()

        t0 = _time.time()
        reference_wav = reference_wav[: int(self.sr * _MAX_REFERENCE_S)]
        device = self.device
        ref_t = torch.from_numpy(reference_wav).to(device)
        ori_16k = torchaudio.functional.resample(ref_t, self.sr, 16000)
        t0 = _t("setup/resample", t0)
        with torch.no_grad():
            S_ori = self.semantic_fn(ori_16k.unsqueeze(0))
            t0 = _t("semantic_fn", t0)
            feat2 = torchaudio.compliance.kaldi.fbank(
                ori_16k.unsqueeze(0), num_mel_bins=80, dither=0, sample_frequency=16000)
            feat2 = feat2 - feat2.mean(dim=0, keepdim=True)
            style2 = self.campplus_model(feat2.unsqueeze(0))
            t0 = _t("campplus", t0)
            mel2 = self.to_mel(ref_t.unsqueeze(0))
            t0 = _t("to_mel", t0)
            target2_lengths = torch.LongTensor([mel2.size(2)]).to(device)
            prompt_condition = self.model.length_regulator(
                S_ori, ylens=target2_lengths, n_quantizers=3, f0=None)[0]
            t0 = _t("length_regulator", t0)
        self.prompt_condition, self.mel2, self.style2 = prompt_condition, mel2, style2
        self._in_buf = np.zeros(0, np.float32)

    def convert(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Streaming entry point -- buffers into ~_BLOCK_S windows (each with
        ~_CE_DIT_CONTEXT_S of rolling context prepended), runs one
        model call per full window, and returns exactly as many samples as
        were sent (matching real-time playback), same contract as
        RVCVoiceEngine.convert(). Passes raw audio through while priming."""
        if self.prompt_condition is None:
            return samples

        block_len = int(round(_BLOCK_S * sample_rate))
        self._in_buf = np.concatenate([self._in_buf, samples.astype(np.float32)])
        out_chunks = []
        while len(self._in_buf) >= block_len:
            # Context = whatever came before this block, capped at ~_CE_DIT_CONTEXT_S.
            ctx_len = int(round(_CE_DIT_CONTEXT_S * sample_rate))
            window = self._in_buf[:block_len]
            self._in_buf = self._in_buf[block_len:]
            out_chunks.append(self._convert_block(window, sample_rate, ctx_len))
        if not out_chunks:
            return samples[:0]   # still buffering -- nothing to emit yet this call
        out = np.concatenate(out_chunks)
        # Keep the stream in sync with what was actually requested.
        if len(out) >= len(samples):
            return out[-len(samples):]
        return np.pad(out, (len(samples) - len(out), 0))

    def _convert_block(self, block: np.ndarray, sample_rate: int, ctx_len: int) -> np.ndarray:
        import torch
        import torchaudio

        device = self.device
        block_t = torch.from_numpy(block).to(device)
        if self._ctx_tail is None:
            self._ctx_tail = torch.zeros(ctx_len, device=device, dtype=torch.float32)
        windowed = torch.cat([self._ctx_tail, block_t])
        self._ctx_tail = windowed[-ctx_len:]

        waves_16k = torchaudio.functional.resample(windowed, sample_rate, 16000)
        ce_dit_frame_difference = int(_CE_DIT_CONTEXT_S * 50)
        with torch.no_grad():
            S_alt = self.semantic_fn(waves_16k.unsqueeze(0))
            S_alt = S_alt[:, ce_dit_frame_difference:]
            # length_regulator's `ylens` is a MEL-frame count (matches how the
            # reference conditioning above builds target2_lengths from
            # mel2.size(2)), not a semantic-token count -- S_alt is at the
            # semantic tokenizer's 50fps, so convert: (frames / 50fps) is the
            # duration in seconds, times the mel frame rate (sr/hop_length).
            target_frames = max(1, int(round(S_alt.size(1) / 50.0 * self.sr / self.hop_length)))
            target_lengths = torch.LongTensor([target_frames]).to(device)
            cond = self.model.length_regulator(S_alt, ylens=target_lengths, n_quantizers=3, f0=None)[0]
            cat_condition = torch.cat([self.prompt_condition, cond], dim=1)
            vc_target = self.model.cfm.inference(
                cat_condition,
                torch.LongTensor([cat_condition.size(1)]).to(device),
                self.mel2, self.style2, None,
                n_timesteps=_DIFFUSION_STEPS, inference_cfg_rate=0.7,
            )
            vc_target = vc_target[:, :, self.mel2.size(-1):]
            vc_wave = self.vocoder_fn(vc_target).squeeze()
        out = vc_wave.detach().cpu().numpy().astype(np.float32)
        # Model runs at its own native sample rate -- resample back to what
        # the caller (the local audio pipeline, device-rate) is expecting.
        if sample_rate != self.sr:
            out_t = torch.from_numpy(out)
            out_t = torchaudio.functional.resample(out_t, self.sr, sample_rate)
            out = out_t.numpy()
        target_n = int(round(_BLOCK_S * sample_rate))
        if len(out) >= target_n:
            return out[-target_n:]
        return np.pad(out, (target_n - len(out), 0))


@app.cls(image=image, gpu="L4", secrets=[modal.Secret.from_name("apexcam-voice-secret")])
@modal.concurrent(max_inputs=2)   # diffusion model -- heavier per-session than RVC's net_g, keep this modest
class VoiceCloneServer:
    @modal.enter()
    def load(self) -> None:
        """Loads the shared, slow-to-load model ONCE per container (not per
        customer session) -- reused across every WebSocket connection this
        container handles. No Volume caching (see the module-level note on
        why) -- a cold container re-downloads weights from HuggingFace into
        its own ephemeral filesystem; warm containers pay this only once."""
        import sys
        sys.path.insert(0, "/stubs")   # our stub `dac` package -- must resolve before the real one would
        sys.path.insert(0, "/seedvc")
        os.chdir("/seedvc")   # some of seed-vc's modules assume cwd-relative config paths

        import torch
        import yaml
        from hf_utils import load_custom_model_from_hf
        from modules.commons import build_model, load_checkpoint, recursive_munch

        device = torch.device("cuda")
        dit_checkpoint_path, dit_config_path = load_custom_model_from_hf(
            "Plachta/Seed-VC", "DiT_uvit_tat_xlsr_ema.pth",
            "config_dit_mel_seed_uvit_xlsr_tiny.yml")
        config = yaml.safe_load(open(dit_config_path, "r"))
        model_params = recursive_munch(config["model_params"])
        model_params.dit_type = "DiT"
        model = build_model(model_params, stage="DiT")
        hop_length = config["preprocess_params"]["spect_params"]["hop_length"]
        sr = config["preprocess_params"]["sr"]
        model, _, _, _ = load_checkpoint(
            model, None, dit_checkpoint_path, load_only_params=True,
            ignore_modules=[], is_distributed=False)
        for key in model:
            model[key].eval()
            model[key].to(device)
        model.cfm.estimator.setup_caches(max_batch_size=1, max_seq_length=8192)

        from modules.campplus.DTDNN import CAMPPlus
        campplus_ckpt_path = load_custom_model_from_hf(
            "funasr/campplus", "campplus_cn_common.bin", config_filename=None)
        campplus_model = CAMPPlus(feat_dim=80, embedding_size=192)
        campplus_model.load_state_dict(torch.load(campplus_ckpt_path, map_location="cpu"))
        campplus_model.eval().to(device)

        vocoder_type = model_params.vocoder.type
        if vocoder_type == "bigvgan":
            from modules.bigvgan import bigvgan
            bigvgan_model = bigvgan.BigVGAN.from_pretrained(
                model_params.vocoder.name, use_cuda_kernel=False)
            bigvgan_model.remove_weight_norm()
            vocoder_fn = bigvgan_model.eval().to(device)
        elif vocoder_type == "hifigan":
            from modules.hifigan.f0_predictor import ConvRNNF0Predictor
            from modules.hifigan.generator import HiFTGenerator
            hift_config = yaml.safe_load(open("configs/hifigan.yml", "r"))
            hift_gen = HiFTGenerator(**hift_config["hift"],
                                     f0_predictor=ConvRNNF0Predictor(**hift_config["f0_predictor"]))
            hift_path = load_custom_model_from_hf("FunAudioLLM/CosyVoice-300M", "hift.pt", None)
            hift_gen.load_state_dict(torch.load(hift_path, map_location="cpu"))
            vocoder_fn = hift_gen.eval().to(device)
        else:
            raise RuntimeError(f"Unsupported vocoder type from config: {vocoder_type}")

        tok_type = model_params.speech_tokenizer.type
        if tok_type == "xlsr":
            from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2Model
            name = model_params.speech_tokenizer.name
            output_layer = model_params.speech_tokenizer.output_layer
            extractor = Wav2Vec2FeatureExtractor.from_pretrained(name)
            wav2vec = Wav2Vec2Model.from_pretrained(name)
            wav2vec.encoder.layers = wav2vec.encoder.layers[:output_layer]
            wav2vec = wav2vec.eval().to(device).half()

            def semantic_fn(waves_16k):
                inputs = extractor(
                    [waves_16k[b].cpu().numpy() for b in range(len(waves_16k))],
                    return_tensors="pt", return_attention_mask=True,
                    padding=True, sampling_rate=16000).to(device)
                with torch.no_grad():
                    out = wav2vec(inputs.input_values.half())
                return out.last_hidden_state.float()
        else:
            raise RuntimeError(f"Unsupported speech tokenizer type from config: {tok_type}")

        mel_fn_args = {
            "n_fft": config["preprocess_params"]["spect_params"]["n_fft"],
            "win_size": config["preprocess_params"]["spect_params"]["win_length"],
            "hop_size": config["preprocess_params"]["spect_params"]["hop_length"],
            "num_mels": config["preprocess_params"]["spect_params"]["n_mels"],
            "sampling_rate": sr,
            "fmin": config["preprocess_params"]["spect_params"].get("fmin", 0),
            "fmax": None if config["preprocess_params"]["spect_params"].get("fmax", "None") == "None" else 8000,
            "center": False,
        }
        from modules.audio import mel_spectrogram
        to_mel = lambda x: mel_spectrogram(x, **mel_fn_args)

        self.model_set = (model, semantic_fn, vocoder_fn, campplus_model, to_mel, mel_fn_args)
        self.device = device
        print(f"VoiceCloneServer loaded (sr={sr}, hop={hop_length}, vocoder={vocoder_type})", flush=True)

    @modal.asgi_app()
    def web(self):
        web_app = FastAPI()

        @web_app.get("/health")
        def health():
            return {"status": "ok"}

        @web_app.websocket("/ws")
        async def ws_clone(ws: WebSocket):
            """Protocol:
              1. text JSON {"token": "...", "sample_rate": 48000}
              2. ONE binary message: the customer's reference clip -- raw
                 float32 PCM mono at `sample_rate`, up to ~2 minutes. This is
                 the "cloning" step -- no training, just a forward pass.
              3. server sends {"type": "ready"} once conditioning is done.
              4. binary frames in (raw float32 PCM mono, `sample_rate`) ->
                 binary frames out (converted, same size)."""
            import asyncio

            await ws.accept()
            print("WS accepted", flush=True)
            try:
                cfg = await ws.receive_json()
                uid = _verify_voice_token(cfg.get("token", ""))
                if uid is None:
                    await ws.close(code=4401, reason="invalid or expired token")
                    return
                sample_rate = int(cfg.get("sample_rate", 48000))

                ref_bytes = await ws.receive_bytes()
                reference = np.frombuffer(ref_bytes, dtype=np.float32)
                if reference.size < sample_rate:   # need at least ~1s to be usable
                    await ws.close(code=4400, reason="reference clip too short")
                    return

                session = CloneSession(self.model_set, self.device)
                t0 = time.time()
                # Resample the reference to the model's native rate before conditioning.
                import torch
                import torchaudio
                ref_t = torch.from_numpy(reference)
                ref_resampled = torchaudio.functional.resample(
                    ref_t, sample_rate, session.sr).numpy()
                await asyncio.to_thread(session.set_reference, ref_resampled)
                print(f"Reference conditioned in {time.time()-t0:.1f}s "
                     f"({reference.size/sample_rate:.1f}s of audio)", flush=True)
                await ws.send_json({"type": "ready"})

                while True:
                    data = await ws.receive_bytes()
                    samples = np.frombuffer(data, dtype=np.float32)
                    out = await asyncio.to_thread(session.convert, samples, sample_rate)
                    await ws.send_bytes(out.tobytes())
            except WebSocketDisconnect:
                print("client disconnected", flush=True)
            except Exception as exc:
                print(f"HANDLER EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
                try:
                    await ws.close(code=1011, reason=str(exc)[:120])
                except Exception as close_exc:
                    print(f"close failed too: {close_exc}", flush=True)

        return web_app
