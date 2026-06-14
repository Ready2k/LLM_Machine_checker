# LLM Machine Checker

A lightweight, zero-dependency Python script to profile your hardware, verify runtime readiness (LM Studio, Ollama, llama.cpp), and execute a fast memory/compute benchmark to recommend the ideal local LLM configuration.

## Features

- **Hardware Auto-Detection**: Profiling of hostname, OS, CPU, system RAM, and GPU (with special-casing for Apple Silicon unified memory systems and NVIDIA VRAM capabilities).
- **Runtime Readiness Checks**: Scans for installations of LM Studio, Ollama, and llama.cpp, and checks for hardware acceleration APIs (like Apple Metal).
- **Scientific Performance Benchmarking**:
  - Dynamically runs memory bandwidth and floating-point computation (TFLOPS) tests.
  - Automatically targets PyTorch/MPS/CUDA if available, with robust fallbacks to NumPy or pure Python standard libraries.
- **Tailored Recommendations**: Identifies your ideal machine role, selects a specific model recommendation (e.g. `Qwen Coder 14B Q4_K_M`), and estimates tokens/second and prompt processing latency (time-to-first-token).
- **Active Resource Risk Assessment**: Scans running system processes (using `psutil` if available) to warn about memory pressure risks if heavy browsers, IDEs, or Docker are active.

## Quick Start

You can run the script directly with Python:

```bash
python3 profile_machine.py
```

### Requirements

- Python 3.9+
- *(Optional)* `torch` and `psutil` to enable high-accuracy GPU bandwidth/FLOPS benchmarking and live process-level memory pressure warnings.

## Sample Output

On a typical Apple Silicon workstation, running the script yields a clean summary:

```
Compatible: yes
Recommended model: Qwen Coder 14B Q4_K_M
Expected performance: good
Measured tokens/sec: 13.1
Risk: memory pressure if IDE/Docker/browser are also running
```
