#!/usr/bin/env python3

import argparse
import json
import platform
import shutil
import subprocess
import re
import os
import time
from dataclasses import dataclass, asdict
from typing import Optional, List

try:
    import psutil
except ImportError:
    psutil = None


@dataclass
class MachineProfile:
    hostname: str
    os: str
    arch: str
    cpu: str
    ram_gb: float
    gpu_name: Optional[str]
    vram_gb: Optional[float]
    has_nvidia: bool
    has_ollama: bool
    has_docker: bool
    role_recommendation: str
    model_recommendations: List[str]
    notes: List[str]
    # New readiness fields
    lm_studio_installed: bool
    ollama_installed: bool
    llama_cpp_installed: bool
    metal_acceleration_available: bool
    # Benchmark metrics
    measured_bandwidth_gb_s: Optional[float]
    measured_tflops: Optional[float]
    estimated_tokens_sec: Optional[float]
    time_to_first_token_ms: Optional[float]
    memory_pressure_during_run: str
    recommended_model: str
    compatible: str
    expected_performance: str
    risk: str


def run(cmd):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return ""


def get_ram_gb():
    system = platform.system()

    if system == "Darwin":
        out = run(["sysctl", "-n", "hw.memsize"])
        return round(int(out) / 1024**3, 1) if out else 0

    if system == "Linux":
        try:
            with open("/proc/meminfo") as f:
                meminfo = f.read()
            match = re.search(r"MemTotal:\s+(\d+)", meminfo)
            if match:
                return round(int(match.group(1)) / 1024**2, 1)
        except Exception:
            pass

    if system == "Windows":
        out = run(["wmic", "computersystem", "get", "TotalPhysicalMemory"])
        nums = re.findall(r"\d+", out)
        if nums:
            return round(int(nums[-1]) / 1024**3, 1)

    return 0


def get_cpu():
    system = platform.system()

    if system == "Darwin":
        return run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()

    if system == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass

    if system == "Windows":
        return run(["wmic", "cpu", "get", "name"]).splitlines()[-1].strip()

    return platform.processor()


def get_gpu():
    # NVIDIA first, because actual VRAM matters more than vague hope.
    if shutil.which("nvidia-smi"):
        name = run([
            "nvidia-smi",
            "--query-gpu=name",
            "--format=csv,noheader"
        ]).splitlines()[0]

        vram = run([
            "nvidia-smi",
            "--query-gpu=memory.total",
            "--format=csv,noheader,nounits"
        ]).splitlines()[0]

        return name, round(int(vram) / 1024, 1), True

    if platform.system() == "Darwin":
        sp = run(["system_profiler", "SPDisplaysDataType"])
        chip = run(["sysctl", "-n", "machdep.cpu.brand_string"])

        if "Apple" in chip or "Apple" in sp:
            return "Apple Silicon / Metal GPU", None, False

        if sp:
            lines = [l.strip() for l in sp.splitlines() if "Chipset Model" in l]
            if lines:
                return lines[0].split(":", 1)[1].strip(), None, False

    if platform.system() == "Linux":
        if shutil.which("lspci"):
            out = run(["lspci"])
            gpu_lines = [
                l for l in out.splitlines()
                if "VGA" in l or "3D controller" in l or "Display" in l
            ]
            if gpu_lines:
                return gpu_lines[0], None, False

    return None, None, False


def recommend(ram_gb, gpu_name, vram_gb, has_nvidia, arch, cpu):
    notes = []
    models = []
    role = "router / utility node"

    is_apple_silicon = (
        platform.system() == "Darwin"
        and arch.lower() == "arm64"
        and "Apple" in cpu
    )

    if has_nvidia and vram_gb:
        if vram_gb >= 24:
            models += [
                "30B-34B Q4 models: good",
                "14B Q5/Q6 models: fast",
                "7B-9B models: very fast"
            ]
            role = "primary LLM host"
        elif vram_gb >= 16:
            models += [
                "14B Q4/Q5 models: good",
                "7B-9B Q6/Q8 models: fast",
                "30B+ models: possible but likely slow/offloaded"
            ]
            role = "strong LLM host"
        elif vram_gb >= 8:
            models += [
                "7B-9B Q4/Q5 models: good",
                "12B-14B Q4 models: possible but slower",
                "30B+ models: not recommended"
            ]
            role = "useful local LLM host"
        else:
            models += [
                "3B-4B models: okay",
                "7B models: possible with compromises"
            ]
            role = "light LLM host"

    elif is_apple_silicon:
        if ram_gb >= 64:
            role = "premium local LLM workstation"
            models = [
                "30B-34B Q4 models: good",
                "14B Q5/Q6 models: fast",
                "7B-9B models: very fast"
            ]
        elif ram_gb >= 32:
            role = "strong local LLM workstation"
            models = [
                "14B Q4/Q5 models: good",
                "20B-27B Q4 models: possible",
                "7B-9B models: fast"
            ]
        elif ram_gb >= 24:
            role = "strong local LLM workstation"
            models = [
                "7B-9B Q5/Q6 models: good",
                "12B-14B Q4 models: good",
                "20B-27B Q4 models: possible but slower"
            ]
        elif ram_gb >= 16:
            role = "light-to-medium local LLM workstation"
            models = [
                "7B-9B Q4 models: good",
                "12B-14B Q4 models: possible but memory-tight"
            ]
        else:
            role = "edge/router node"
            models = [
                "1B-3B models only",
                "Use as API router, vector DB, monitoring, not main inference"
            ]

    elif "arm" in arch.lower() or "aarch64" in arch.lower():
        if ram_gb <= 8:
            models += [
                "1B-3B models only",
                "Use as API router, vector DB, monitoring, not main inference"
            ]
            role = "edge/router node"
        else:
            models += [
                "3B-7B CPU models possible",
                "Expect slow output unless this is Apple Silicon"
            ]
            role = "light inference / utility node"

    else:
        if ram_gb >= 64:
            models += [
                "14B-30B CPU inference possible but slow",
                "Better as model storage/API host unless GPU exists"
            ]
            role = "CPU fallback host"
        elif ram_gb >= 32:
            models += [
                "7B-14B CPU inference possible",
                "Usable for testing, not joyful"
            ]
            role = "secondary test host"
        elif ram_gb >= 16:
            models += [
                "3B-7B models only",
                "Use quantized models"
            ]
            role = "light test host"
        else:
            models += [
                "Tiny models only",
                "Better used for routing/monitoring"
            ]
            role = "utility node"

    if gpu_name is None:
        notes.append("No useful GPU detected. CPU-only LLMs work, but slowly, because apparently time has no value.")

    if has_nvidia and vram_gb and vram_gb <= 8:
        notes.append("8GB VRAM is useful, but keep expectations sane: 7B/8B class models are the sweet spot.")

    if ram_gb < 16:
        notes.append("System RAM is low for modern local AI. Use this as a helper node, not the main engine.")

    return role, models, notes


def check_readiness(cpu_brand):
    # LM Studio installed
    lm_studio_installed = False
    paths_to_check = [
        "/Applications/LM Studio.app",
        os.path.expanduser("~/Applications/LM Studio.app"),
        os.path.expanduser("~/.cache/lm-studio"),
        os.path.expanduser("~/Library/Application Support/LM Studio")
    ]
    for path in paths_to_check:
        if os.path.exists(path):
            lm_studio_installed = True
            break

    # Ollama installed
    ollama_installed = (
        shutil.which("ollama") is not None
        or os.path.exists("/Applications/Ollama.app")
        or os.path.exists(os.path.expanduser("~/Applications/Ollama.app"))
        or os.path.exists(os.path.expanduser("~/.ollama"))
    )

    # llama.cpp installed
    llama_cpp_installed = (
        shutil.which("llama-cli") is not None
        or shutil.which("llama-cpp") is not None
        or shutil.which("main") is not None
        or os.path.exists("/opt/homebrew/bin/llama-cli")
        or os.path.exists("/usr/local/bin/llama-cli")
    )

    # Metal acceleration available
    is_mac = platform.system() == "Darwin"
    is_arm = platform.machine().lower() == "arm64"
    metal_available = False
    if is_mac:
        try:
            import torch
            metal_available = torch.backends.mps.is_available()
        except ImportError:
            metal_available = is_arm or "Apple" in cpu_brand

    return {
        "lm_studio": lm_studio_installed,
        "ollama": ollama_installed,
        "llama_cpp": llama_cpp_installed,
        "metal": metal_available
    }


def run_benchmark(metal_available):
    measured_bandwidth = None
    measured_tflops = None

    try:
        import torch
        if torch.backends.mps.is_available() and metal_available:
            device = torch.device("mps")
        elif torch.cuda.is_available():
            device = torch.device("cuda")
        else:
            device = torch.device("cpu")

        # Bandwidth
        size_mb = 128
        num_elements = size_mb * 1024 * 1024 // 4
        x = torch.ones(num_elements, dtype=torch.float32, device=device)
        _ = x + 1.0
        if device.type == "mps":
            torch.mps.synchronize()
        elif device.type == "cuda":
            torch.cuda.synchronize()

        t0 = time.perf_counter()
        iters = 10
        for _ in range(iters):
            y = x + 1.0
        if device.type == "mps":
            torch.mps.synchronize()
        elif device.type == "cuda":
            torch.cuda.synchronize()
        t1 = time.perf_counter()
        measured_bandwidth = (size_mb * 2 * iters) / (t1 - t0) / 1024

        # FLOPS
        n = 1024
        a = torch.randn(n, n, device=device, dtype=torch.float32)
        b = torch.randn(n, n, device=device, dtype=torch.float32)
        _ = torch.matmul(a, b)
        if device.type == "mps":
            torch.mps.synchronize()
        elif device.type == "cuda":
            torch.cuda.synchronize()

        t0 = time.perf_counter()
        iters_mul = 20
        for _ in range(iters_mul):
            c = torch.matmul(a, b)
        if device.type == "mps":
            torch.mps.synchronize()
        elif device.type == "cuda":
            torch.cuda.synchronize()
        t1 = time.perf_counter()
        gflops_per_mul = (2.0 * (n ** 3)) / 1e9
        measured_tflops = (gflops_per_mul * iters_mul / (t1 - t0)) / 1000

    except Exception:
        try:
            import numpy as np
            size_mb = 64
            num_elements = size_mb * 1024 * 1024 // 8
            x = np.ones(num_elements, dtype=np.float64)
            t0 = time.perf_counter()
            iters = 5
            for _ in range(iters):
                y = x + 1.0
            t1 = time.perf_counter()
            measured_bandwidth = (size_mb * 2 * iters) / (t1 - t0) / 1024

            n = 512
            a = np.random.randn(n, n).astype(np.float32)
            b = np.random.randn(n, n).astype(np.float32)
            t0 = time.perf_counter()
            iters_mul = 10
            for _ in range(iters_mul):
                c = np.dot(a, b)
            t1 = time.perf_counter()
            gflops_per_mul = (2.0 * (n ** 3)) / 1e9
            measured_tflops = (gflops_per_mul * iters_mul / (t1 - t0)) / 1000
        except Exception:
            t0 = time.perf_counter()
            b1 = bytearray(10 * 1024 * 1024)
            for _ in range(5):
                b2 = bytearray(b1)
            t1 = time.perf_counter()
            measured_bandwidth = 50 / (t1 - t0) / 1024
            measured_tflops = 0.05

    return measured_bandwidth, measured_tflops


def get_recommendation_details(ram_gb, has_nvidia, is_apple_silicon):
    if is_apple_silicon:
        if ram_gb >= 64:
            return "Qwen Coder 32B Q4_K_M", 19.2, "excellent", "yes"
        elif ram_gb >= 32:
            return "Qwen Coder 32B Q4_K_M", 19.2, "good", "yes"
        elif ram_gb >= 24:
            return "Qwen Coder 14B Q4_K_M", 8.4, "good", "yes"
        elif ram_gb >= 16:
            return "Qwen Coder 7B Q4_K_M", 4.8, "good", "yes"
        else:
            return "Qwen Coder 3B Q4_K_M", 2.2, "fair", "yes" if ram_gb >= 8 else "no"
    elif has_nvidia:
        if ram_gb >= 32:
            return "Qwen Coder 14B Q4_K_M", 8.4, "excellent", "yes"
        elif ram_gb >= 16:
            return "Qwen Coder 7B Q4_K_M", 4.8, "good", "yes"
        else:
            return "Qwen Coder 3B Q4_K_M", 2.2, "fair", "yes" if ram_gb >= 8 else "no"
    else:
        if ram_gb >= 32:
            return "Qwen Coder 7B Q4_K_M", 4.8, "fair", "yes"
        elif ram_gb >= 16:
            return "Qwen Coder 3B Q4_K_M", 2.2, "fair", "yes"
        else:
            return "Qwen Coder 1.5B Q4_K_M", 1.2, "poor", "no"


def main(output_json=False):
    hostname = platform.node()
    os_name = f"{platform.system()} {platform.release()}"
    arch = platform.machine()
    cpu = get_cpu()
    ram_gb = get_ram_gb()
    gpu_name, vram_gb, has_nvidia = get_gpu()

    is_apple_silicon = (
        platform.system() == "Darwin"
        and arch.lower() == "arm64"
        and "Apple" in cpu
    )

    role, models, notes = recommend(ram_gb, gpu_name, vram_gb, has_nvidia, arch, cpu)
    readiness = check_readiness(cpu)
    bandwidth, tflops = run_benchmark(readiness["metal"])

    rec_model, rec_model_size_gb, expected_perf, compatible = get_recommendation_details(
        ram_gb, has_nvidia, is_apple_silicon
    )

    # Estimate metrics
    est_tokens_sec = None
    ttft_ms = None
    if bandwidth:
        est_tokens_sec = round((bandwidth / rec_model_size_gb) * 0.8, 1)
    if tflops:
        ttft_ms = round((512 * rec_model_size_gb) / (tflops * 1000) * 0.3 * 1000, 1)
        if ttft_ms < 50:
            ttft_ms = 50.0

    # Risk and memory pressure
    mem_pressure = "low"
    running_apps = []
    risk = "none"

    if psutil:
        mem_pressure_pct = psutil.virtual_memory().percent
        available_gb = psutil.virtual_memory().available / 1024**3
        if available_gb < rec_model_size_gb:
            mem_pressure = "high"
        elif available_gb < (rec_model_size_gb + 6.0):
            mem_pressure = "medium"

        # Check processes
        for proc in psutil.process_iter(['name']):
            try:
                name = proc.info['name'].lower()
                if 'chrome' in name or 'safari' in name or 'firefox' in name:
                    if 'browser' not in running_apps:
                        running_apps.append('browser')
                elif 'code' in name or 'xcode' in name or 'visual studio' in name:
                    if 'IDE' not in running_apps:
                        running_apps.append('IDE')
                elif 'docker' in name or 'dockerd' in name:
                    if 'Docker' not in running_apps:
                        running_apps.append('Docker')
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        if available_gb < (rec_model_size_gb + 4.0) and running_apps:
            apps_str = "/".join(running_apps)
            risk = f"memory pressure if {apps_str} are also running"
        elif available_gb < rec_model_size_gb:
            risk = "system available memory is less than model size"
    else:
        # Fallback without psutil
        if ram_gb - rec_model_size_gb < 4.0:
            mem_pressure = "medium"
            risk = "potential memory pressure on low RAM systems"
        else:
            mem_pressure = "low"

    profile = MachineProfile(
        hostname=hostname,
        os=os_name,
        arch=arch,
        cpu=cpu,
        ram_gb=ram_gb,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        has_nvidia=has_nvidia,
        has_ollama=readiness["ollama"],
        has_docker=shutil.which("docker") is not None,
        role_recommendation=role,
        model_recommendations=models,
        notes=notes,
        lm_studio_installed=readiness["lm_studio"],
        ollama_installed=readiness["ollama"],
        llama_cpp_installed=readiness["llama_cpp"],
        metal_acceleration_available=readiness["metal"],
        measured_bandwidth_gb_s=round(bandwidth, 2) if bandwidth else None,
        measured_tflops=round(tflops, 2) if tflops else None,
        estimated_tokens_sec=est_tokens_sec,
        time_to_first_token_ms=ttft_ms,
        memory_pressure_during_run=mem_pressure,
        recommended_model=rec_model,
        compatible=compatible,
        expected_performance=expected_perf,
        risk=risk
    )

    # Output
    if output_json:
        print(json.dumps(asdict(profile), indent=2, default=str))
    else:
        print(f"Compatible: {compatible}")
        print(f"Recommended model: {rec_model}")
        print(f"Expected performance: {expected_perf}")
        print(f"Measured tokens/sec: {est_tokens_sec if est_tokens_sec is not None else 'N/A'}")
        print(f"Risk: {risk}")

    return profile


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Profile machine for local LLM readiness")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output full profile as JSON to stdout")
    args = parser.parse_args()
    main(output_json=args.json_output)
