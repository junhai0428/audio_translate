from setuptools import setup, find_packages

setup(
    name="voice-gender-classification",
    version="0.1.0",
    description="Gender classification pipeline using SpeechBrain ECAPA embeddings and SVM",
    author="Gregory Koushnir",
    author_email="koushgre@post.bgu.ac.il",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "scikit-learn",
        "pandas",
        "soundfile",
        "speechbrain",
        "torch",
        "torchaudio",
        "huggingface_hub",
    ],
    python_requires=">=3.8"
)