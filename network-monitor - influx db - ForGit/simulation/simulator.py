# simulation/simulator.py
import os
import sys
import time
import random
import yaml
from pathlib import Path

# افزودن مسیر ریشه پروژه به sys.path برای ایمپورت ماژول‌های app
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from app.database import save_check_result, log_event
from app.collector import check_anomaly_and_threshold

SIM_CONFIG_PATH = ROOT_DIR / "simulation" / "simulated_devices.yaml"

# نگهداری وضعیت پیشین هر دیوایس برای ثبت رخدادهای UP/DOWN
sim_last_status = {}

def load_sim_config() -> dict:
    if not SIM_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Configuration file not found: {SIM_CONFIG_PATH}")
    with open(SIM_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

class DeviceSimulator:
    def __init__(self, device_cfg: dict):
        self.cfg = device_cfg
        self.id = device_cfg["id"]
        self.name = device_cfg["name"]
        self.device_type = device_cfg.get("device_type", "router")
        self.ip = device_cfg.get("ip", "127.0.0.1")
        self.down_timer_start = 0
        self.is_chaos_down = False

    def step(self) -> dict:
        """محاسبه یک چرخه پایش شبیه‌سازی‌شده بر اساس نوسانات تصادفی گاوسی"""
        now = time.time()
        chaos_interval = self.cfg.get("chaos_down_interval_sec", 0)

        # مدیریت سناریوی خرابی مصنوعی (Chaos Simulation)
        if chaos_interval > 0:
            if not self.is_chaos_down and (int(now) % chaos_interval < 15):
                self.is_chaos_down = True
            elif self.is_chaos_down and (int(now) % chaos_interval >= 15):
                self.is_chaos_down = False

        if self.is_chaos_down:
            return {
                "id": self.id,
                "name": self.name,
                "type": f"sim-{self.device_type}",
                "status": "DOWN",
                "latency_ms": None,
                "cpu_percent": 0.0,
                "ram_percent": 0.0,
                "packet_loss": 100.0,
                "error": f"SNMP/ICMP connection timeout on {self.ip} (Device Down)",
                "anomaly_cfg": self.cfg
            }

        # تولید نوسان طبیعی (Gaussian / Normal Distribution)
        base_lat = self.cfg.get("base_latency_ms", 15.0)
        jitter = self.cfg.get("latency_jitter", 3.0)
        latency = max(1.0, round(random.gauss(base_lat, jitter), 2))

        # احتمال ۲ درصدی اسپایک لحظه‌ای برای تحریک ناهنجاری
        if random.random() < 0.03 and self.cfg.get("anomaly_detection", False):
            latency = round(latency * random.uniform(2.5, 4.0), 2)

        # نوسان بار پردازشی و رم
        cpu = min(100.0, max(5.0, round(random.gauss(self.cfg.get("base_cpu", 30.0), 5.0), 1)))
        ram = min(100.0, max(10.0, round(random.gauss(self.cfg.get("base_ram", 40.0), 2.0), 1)))
        
        # پکت‌لاس جزئی در لود‌های بالا
        packet_loss = 0.0
        if cpu > 85.0:
            packet_loss = round(random.uniform(2.0, 8.0), 1)

        return {
            "id": self.id,
            "name": self.name,
            "type": f"sim-{self.device_type}",
            "status": "UP",
            "latency_ms": latency,
            "cpu_percent": cpu,
            "ram_percent": ram,
            "packet_loss": packet_loss,
            "error": None,
            "anomaly_cfg": self.cfg
        }

def run_simulation_loop():
    config = load_sim_config()
    interval = config.get("simulation_interval", 10)
    devices = [DeviceSimulator(d) for d in config.get("devices", [])]

    print("\n" + "=" * 70)
    print("🌐 Network Hardware Simulator Running...")
    print(f"Simulating {len(devices)} infrastructure devices every {interval}s.")
    print("Integrated directly with monitor.db and Anomaly/Threshold engine.")
    print("=" * 70)

    try:
        while True:
            for dev in devices:
                metrics = dev.step()
                t_id = metrics["id"]
                t_name = metrics["name"]
                status = metrics["status"]
                latency = metrics["latency_ms"]
                err = metrics["error"]

                # ذخیره در دیتابیس فعلی پروژه
                # توجه: فیلد status_code برای نمایش درصد استفاده CPU تجهیز بهره‌برداری می‌شود
                save_check_result(
                    target_id=t_id,
                    target_name=t_name,
                    check_type=metrics["type"],
                    status=status,
                    response_time_ms=latency,
                    status_code=int(metrics["cpu_percent"]) if status == "UP" else None,
                    error_message=err
                )

                # لاگ رخداد تغییر وضعیت تجهیز (Failover & Recovery)
                prev_status = sim_last_status.get(t_id)
                if prev_status is not None and prev_status != status:
                    if status == "DOWN":
                        log_event(t_id, "DOWN", f"[شبیه‌ساز] تجهیز {t_name} از مدار خارج شد ({err})")
                    elif status == "UP":
                        log_event(t_id, "RECOVERED", f"[شبیه‌ساز] ارتباط با {t_name} مجدداً برقرار گردید.")
                sim_last_status[t_id] = status

                # اتصال به موتور ارزیابی هشدار و ناهنجاری هوشمند موجود در collector.py
                if status == "UP":
                    check_anomaly_and_threshold(metrics["anomaly_cfg"], latency)

                lat_str = f"{latency}ms" if latency is not None else "N/A"
                print(f"[SIM-{status:^4}] {t_name:<25} | CPU: {metrics['cpu_percent']:>4.1f}% | Loss: {metrics['packet_loss']}% | Latency: {lat_str}")

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n⏹️ Simulator stopped by user.")

if __name__ == "__main__":
    run_simulation_loop()
