import torch
import torchaudio
import numpy as np
import pandas as pd
import soundfile as sf
from typing import Union, List, Dict
from speechbrain.inference.speaker import EncoderClassifier

class GenderClassificationPipeline:
    def __init__(self, svm_model, scaler, device="cpu"):
        """
        Initialize the pipeline with SVM model and scaler.
        """
        self.device = torch.device(device)
        self.model = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            run_opts={"device": str(self.device)}
        )
        self.svm_model = svm_model
        self.scaler = scaler
        self.labels = ["female", "male"]
        self.feature_names = [f"{i}_speechbrain_embedding" for i in range(192)]
        
    def _process_audio(self, waveform: torch.Tensor, sample_rate: int) -> torch.Tensor:
        """Process audio to 16kHz mono."""
        # Convert to mono if needed
        if len(waveform.shape) > 1:
            waveform = torch.mean(waveform, dim=-1)
    
        # Resample to 16kHz if needed
        if sample_rate != 16000:
            waveform = torchaudio.functional.resample(waveform, sample_rate, 16000)
    
        # Normalize to [-1, 1]
        if waveform.abs().max() > 1:
            waveform = waveform / waveform.abs().max()
    
        return waveform

    def preprocess(self, audio_input: Union[str, List[str], np.ndarray, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """Preprocess audio input."""
        def load_audio(audio_file):
            wave, sr = sf.read(audio_file)
            wave = torch.from_numpy(wave).float()
            return wave, sr

        if isinstance(audio_input, list):
            waveforms = []
            wav_lens = []
            for audio_file in audio_input:
                wave, sr = load_audio(audio_file)
                wave = self._process_audio(wave, sr)
                waveforms.append(wave)
                wav_lens.append(wave.shape[0])

            # Pad waveforms to the same length
            max_len = max(wav_lens)
            padded_waveforms = [torch.nn.functional.pad(wave, (0, max_len - wave.shape[0])) for wave in waveforms]
            inputs = torch.stack(padded_waveforms).to(self.device)
            wav_lens = torch.tensor(wav_lens, dtype=torch.float32) / max_len
            return {"inputs": inputs, "wav_lens": wav_lens.to(self.device)}

        if isinstance(audio_input, str):
            waveform, sr = load_audio(audio_input)
            waveform = self._process_audio(waveform, sr)
            inputs = waveform.unsqueeze(0).to(self.device)
            wav_lens = torch.tensor([1.0], dtype=torch.float32).to(self.device)
            return {"inputs": inputs, "wav_lens": wav_lens}

        elif isinstance(audio_input, np.ndarray):
            waveform = torch.from_numpy(audio_input).float()
            waveform = self._process_audio(waveform, 16000)
            inputs = waveform.unsqueeze(0).to(self.device)
            wav_lens = torch.tensor([1.0], dtype=torch.float32).to(self.device)
            return {"inputs": inputs, "wav_lens": wav_lens}

        elif isinstance(audio_input, torch.Tensor):
            waveform = self._process_audio(audio_input, 16000)
            inputs = waveform.unsqueeze(0).to(self.device)
            wav_lens = torch.tensor([1.0], dtype=torch.float32).to(self.device)
            return {"inputs": inputs, "wav_lens": wav_lens}
        else:
            raise ValueError(f"Unsupported audio input type: {type(audio_input)}")

    def forward(self, inputs: Dict[str, torch.Tensor]) -> torch.Tensor:
        """Extract embeddings."""
        with torch.no_grad():
            embeddings = self.model.encode_batch(inputs["inputs"], inputs["wav_lens"])
            return embeddings.squeeze(1)  # Remove the singleton dimension

    def postprocess(self, model_outputs: torch.Tensor) -> List[str]:
        """Get predictions from embeddings."""
        embeddings = model_outputs.cpu().numpy()
        scaled_features = self.scaler.transform(
            pd.DataFrame(embeddings, columns=self.feature_names)
        )
        predictions = self.svm_model.predict(scaled_features)
        return [self.labels[p] for p in predictions]

    def __call__(self, audio_input: Union[str, List[str], np.ndarray, torch.Tensor]) -> List[str]:
        """Run the pipeline on the input."""
        inputs = self.preprocess(audio_input)
        outputs = self.forward(inputs)
        return self.postprocess(outputs)

    def save_pretrained(self, save_directory: str):
        """Save model components."""
        import os
        import joblib
        import json
        
        os.makedirs(save_directory, exist_ok=True)
        
        # Save the SVM model and scaler
        joblib.dump(self.svm_model, os.path.join(save_directory, "svm_model.joblib"))
        joblib.dump(self.scaler, os.path.join(save_directory, "scaler.joblib"))
        
        # Save the configuration
        with open(os.path.join(save_directory, "config.json"), "w") as f:
            json.dump({
                "labels": self.labels,
                "feature_names": self.feature_names
            }, f)

    @classmethod
    def from_pretrained(cls, model_path: str, device="cpu"):
        """Load model components."""
        import os
        import joblib
        import json
        from huggingface_hub import hf_hub_download

        if os.path.isdir(model_path):
            base_path = model_path
            load_file = lambda f: os.path.join(base_path, f)
        else:
            # Download files from HuggingFace Hub
            load_file = lambda f: hf_hub_download(repo_id=model_path, filename=f)

        # Load configuration
        with open(load_file("config.json"), "r") as f:
            config = json.load(f)

        # Load SVM model and scaler
        svm_model = joblib.load(load_file("svm_model.joblib"))
        scaler = joblib.load(load_file("scaler.joblib"))
        
        # Create pipeline instance
        pipeline = cls(svm_model=svm_model, scaler=scaler, device=device)
        
        # Load labels and feature names from config
        pipeline.labels = config.get("labels", pipeline.labels)
        pipeline.feature_names = config.get("feature_names", pipeline.feature_names)
        
        return pipeline
