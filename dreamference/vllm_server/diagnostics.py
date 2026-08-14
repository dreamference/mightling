import os
import shutil
import subprocess
from typing import Optional

class ContainerDiagnostics:
    """
    Gathers detailed diagnostics including Docker container memory, CPU, block I/O, 
    as well as system-wide I/O wait, swap metrics, and available RAM.
    """
    def __init__(self, host: str):
        self.host = host
        self._last_iowait_ticks: int = 0
        self._last_total_ticks: int = 0

    def get_diagnostics_line(self) -> Optional[str]:
        if not shutil.which("docker"):
            return None
        try:
            # Extract port from host for container name
            port = self.host.split(":")[-1] if ":" in self.host else "8000"
            container_name = f"dreamference-vllm-{port}"
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}|{{.CPUPerc}}|{{.BlockIO}}", container_name],
                capture_output=True, text=True, timeout=2
            )
            swap_info = ""
            try:
                cg_result = subprocess.run(
                    ["docker", "exec", container_name, "cat", "/sys/fs/cgroup/memory.stat"],
                    capture_output=True, text=True, timeout=1
                )
                if cg_result.returncode == 0:
                    majfault, pswpin, pswpout = "?", "?", "?"
                    for line in cg_result.stdout.splitlines():
                        if line.startswith("pgmajfault"): majfault = line.split()[1]
                        elif line.startswith("pswpin"): pswpin = line.split()[1]
                        elif line.startswith("pswpout"): pswpout = line.split()[1]
                    swap_info = f" | SwapIO: {pswpin}in {pswpout}out | MajFaults: {majfault}"
            except Exception:
                pass
                
            # Host RAM
            host_ram_gb = -1.0
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemAvailable:"):
                            kb = int(line.split()[1])
                            host_ram_gb = kb / (1024 * 1024)
                            break
            except Exception:
                pass
            
            if host_ram_gb > 5.0:
                ram_status = "Healthy"
            elif host_ram_gb > 2.0:
                ram_status = "Unhealthy"
            elif host_ram_gb >= 0:
                ram_status = "Dangerous"
            else:
                ram_status = "Unknown"
                
            ram_str = f"{host_ram_gb:.1f}GB ({ram_status})" if host_ram_gb >= 0 else "Unknown"
            
            # IOWait
            iowait_pct = -1.0
            try:
                with open("/proc/stat", "r") as f:
                    cpu_line = f.readline()
                parts = cpu_line.split()
                if len(parts) >= 6:
                    total_ticks = sum(int(p) for p in parts[1:])
                    iowait_ticks = int(parts[5])
                    if self._last_total_ticks > 0:
                        dt = total_ticks - self._last_total_ticks
                        di = iowait_ticks - self._last_iowait_ticks
                        if dt > 0:
                            iowait_pct = (di / dt) * 100.0
                    self._last_total_ticks = total_ticks
                    self._last_iowait_ticks = iowait_ticks
            except Exception:
                pass
                
            if iowait_pct < 0:
                io_status = "Unknown"
            elif iowait_pct < 10.0:
                io_status = "Healthy"
            elif iowait_pct < 25.0:
                io_status = "Unhealthy"
            else:
                io_status = "Dangerous"
                
            io_str = f"{iowait_pct:.1f}% ({io_status})" if iowait_pct >= 0 else "Unknown"

            if result.returncode == 0:
                stats = result.stdout.strip()
                if stats.count("|") >= 2:
                    mem, cpu, blkio = stats.split("|", 2)
                    
                    total_cpus = os.cpu_count() or 1
                    cpus_limit = max(1.0, total_cpus * 0.7)
                    cpu_limit_pct = cpus_limit * 100.0
                    
                    try:
                        cpu_val = float(cpu.replace("%", "").strip())
                        status = "Healthy" if cpu_val <= cpu_limit_pct + 0.1 else "Dangerous"
                    except ValueError:
                        status = "Unknown"
                        
                    return f"{mem.strip()} | CPU: {cpu.strip()} / {cpu_limit_pct:.1f}% ({status}) | BlkIO: {blkio.strip()} | Host RAM Free: {ram_str} | IOWait: {io_str}{swap_info}"
                return stats + swap_info
        except Exception:
            pass
        return None
