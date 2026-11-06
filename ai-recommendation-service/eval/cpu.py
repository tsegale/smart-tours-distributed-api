"""Which instructions this CPU has. An int8 model's vectors may depend on them (issue #10), so evaluations log them."""

from pathlib import Path

CPUINFO = Path("/proc/cpuinfo")


def cpu_features(cpuinfo: Path = CPUINFO) -> dict:
    """The CPU's model and the flags that choose ONNX Runtime's integer kernels. None where they can't be read."""
    try:
        lines = cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"model": None, "vnni": None, "avx512": None, "avx2": None}
    fields = {}
    for line in lines:
        key, _, value = line.partition(":")
        fields.setdefault(key.strip(), value.strip())
    flags = set(fields.get("flags", "").split())
    return {
        "model": fields.get("model name"),
        "vnni": bool(flags & {"avx512_vnni", "avx_vnni"}),
        "avx512": "avx512f" in flags,
        "avx2": "avx2" in flags,
    }
