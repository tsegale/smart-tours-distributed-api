"""Issue #10: int8 candidates built from the float model, using ONNX Runtime's documented dynamic quantisation options."""

import sys
from pathlib import Path

from huggingface_hub import hf_hub_download
from onnxruntime.quantization import QuantType, quantize_dynamic

REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CANDIDATES = {
    "qint8-rr": {"weight_type": QuantType.QInt8, "reduce_range": True},
    "qint8-rr-pc": {"weight_type": QuantType.QInt8, "reduce_range": True, "per_channel": True},
    "quint8": {"weight_type": QuantType.QUInt8},
    "quint8-pc": {"weight_type": QuantType.QUInt8, "per_channel": True},
}


def main(directory: str) -> None:
    source = hf_hub_download(REPO, "onnx/model.onnx")
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    for name, options in CANDIDATES.items():
        target = out / f"{name}.onnx"
        quantize_dynamic(source, target, **options)
        print(name, round(target.stat().st_size / 1e6), "MB", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
