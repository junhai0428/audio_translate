# Gender Classification from Audio

A HuggingFace pipeline for gender classification from audio using SpeechBrain ECAPA embeddings and SVM.

## Model Details
- **Architecture**: SpeechBrain ECAPA-TDNN embeddings (192-dim) + SVM classifier
- **Training Data**: VoxCeleb2 dataset
  - Training set: 1,691 speakers (845 females, 846 males)
  - Validation set: 785 speakers (396 females, 389 males)
  - Test set: 1,647 speakers (828 females, 819 males)
- **Performance**:
  - VoxCeleb2 test set: 98.9% accuracy, 0.9885 F1-score
  - Mozilla Common Voice v10.0 English validated test set: 92.3% accuracy
  - TIMIT test set: 99.6% accuracy
- **Audio Processing**:
  - Input format: Any audio file format supported by soundfile
  - Automatically converted to: 16kHz, mono, single channel, 256 Kbps

## Installation

You can install the package directly from GitHub:

```bash
pip install git+https://github.com/griko/voice-gender-classification.git
```

## Usage

```python
from voice_gender_classification import GenderClassificationPipeline

# Load the pipeline
classifier = GenderClassificationPipeline.from_pretrained(
    "griko/gender_cls_svm_ecapa_voxceleb"
)

# Single file prediction
result = classifier("path/to/audio.wav")
print(result)  # ["female"] or ["male"]

# Batch prediction
results = classifier(["audio1.wav", "audio2.wav"])
print(results)  # ["female", "male", "female"]
```

## Limitations
- Designed for binary gender classification only
- Model was trained on celebrity voices from YouTube interviews
- Performance may vary on:
  - Different audio qualities
  - Different recording conditions
  - Multiple simultaneous speakers

## Citation
If you use this model in your research, please cite:
```bibtex
@misc{koushnir2025vanpyvoiceanalysisframework,
      title={VANPY: Voice Analysis Framework}, 
      author={Gregory Koushnir and Michael Fire and Galit Fuhrmann Alpert and Dima Kagan},
      year={2025},
      eprint={2502.17579},
      archivePrefix={arXiv},
      primaryClass={cs.SD},
      url={https://arxiv.org/abs/2502.17579}, 
}
```

## License
This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments
- VoxCeleb2 dataset for providing the training data
- SpeechBrain team for their excellent speech processing toolkit
