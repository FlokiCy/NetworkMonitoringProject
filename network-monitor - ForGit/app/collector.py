# app/collector.py
import os
import platform
import re
import socket
import subprocess
import time
import math
from typing import Dict, Any
import httpx
import yaml
import asyncio
# ایمپورت ماژول SNMP با سازگاری دوگانه (نسخه‌های جدید v6+ و نسخه‌های قدیمی‌تر)
HAS_PYSNMP = False

try:
    from pysnmp.hlapi.v3arch.asyncio import (
        SnmpEngine,
        CommunityData,
        UdpTransportTarget,
        ContextData,
        ObjectType,
        ObjectIdentity,
        get_cmd,
    )

    HAS_PYSNMP = True

except ImportError as exc:
    print(f"pysnmp import failed: {exc}")

from app.database import log_event, save_check_result, get_connection

CONFIG_PATH = os.path.join("config", "targets.yaml")

# نگهداری آخرین وضعیت هر مقصد در حافظه برای جلوگیری از هشدارهای تکراری
last_target_status: Dict[str, str] = {}
last_threshold_state: Dict[str, bool] = {}


def load_config() -> dict:
    """خواندن فایل تنظیمات YAML"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_anomaly_and_threshold(target: dict, current_latency: float | None):
    """
    بررسی آستانه ثابت (Rule-Based) و ناهنجاری آماری (Moving Average & Z-Score)
    """
    if current_latency is None:
        return

    target_id = target.get("id")
    target_name = target.get("name", target_id)
    threshold = target.get("latency_threshold_ms")
    anomaly_enabled = target.get("anomaly_detection", False)

    # ۱. بررسی آستانه‌گذاری ثابت (Threshold Alerting)
    if threshold:
        is_exceeded = current_latency > threshold
        was_exceeded = last_threshold_state.get(target_id, False)

        if is_exceeded and not was_exceeded:
            log_event(
                target_id=target_id,
                event_type="THRESHOLD_ALERT",
                description=f"هشدار آستانه: تاخیر {target_name} ({current_latency}ms) از حد تعیین‌شده ({threshold}ms) فراتر رفت.",
            )
            last_threshold_state[target_id] = True
        elif not is_exceeded and was_exceeded:
            log_event(
                target_id=target_id,
                event_type="THRESHOLD_NORMAL",
                description=f"وضعیت عادی: تاخیر {target_name} ({current_latency}ms) به زیر حد مجاز بازگشت.",
            )
            last_threshold_state[target_id] = False

    # ۲. تشخیص ناهنجاری آماری (Anomaly Detection via Moving Window)
    if anomaly_enabled:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT response_time_ms FROM check_history
            WHERE target_id = ? AND status = 'UP' AND response_time_ms IS NOT NULL
            ORDER BY id DESC LIMIT 10
        """,
            (target_id,),
        )
        rows = cursor.fetchall()
        conn.close()

        if len(rows) >= 5:
            latencies = [r["response_time_ms"] for r in rows]
            mean = sum(latencies) / len(latencies)
            variance = sum((x - mean) ** 2 for x in latencies) / len(latencies)
            std_dev = math.sqrt(variance)

            # اگر لتنسی فعلی از میانگین + ۲ برابر انحراف معیار بیشتر باشد
            if std_dev > 0 and current_latency > (mean + 2 * std_dev) and (current_latency - mean) > 25:
                log_event(
                    target_id=target_id,
                    event_type="ANOMALY_DETECTED",
                    description=f"ناهنجاری تاخیر: جهش ناگهانی به {current_latency}ms. میانگین ۱۰ تست قبلی: {mean:.1f}ms (انحراف معیار: {std_dev:.1f})",
                )


def check_icmp(host: str, timeout_sec: int = 2) -> tuple[str, float | None, str | None]:
    """بررسی اتصال از طریق پینگ (ICMP)"""
    param = "-n" if platform.system().lower() == "windows" else "-c"
    timeout_param = "-w" if platform.system().lower() == "windows" else "-W"
    timeout_val = str(timeout_sec * 1000) if platform.system().lower() == "windows" else str(timeout_sec)

    cmd = ["ping", param, "1", timeout_param, timeout_val, host]
    try:
        start_time = time.time()
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, universal_newlines=True)
        latency = round((time.time() - start_time) * 1000, 2)

        match = re.search(r"time[<=]([0-9]+(?:\.[0-9]+)?)ms", output, re.IGNORECASE)
        if match:
            latency = float(match.group(1))

        return "UP", latency, None
    except subprocess.CalledProcessError:
        return "DOWN", None, "Host unreachable / Request timed out"
    except Exception as e:
        return "DOWN", None, str(e)


def check_tcp(host: str, port: int, timeout_sec: int = 2) -> tuple[str, float | None, str | None]:
    """بررسی وضعیت پورت با سوکت TCP"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    start_time = time.time()
    try:
        result = sock.connect_ex((host, port))
        latency = round((time.time() - start_time) * 1000, 2)
        if result == 0:
            return "UP", latency, None
        return "DOWN", None, f"Port {port} closed or filtered (code: {result})"
    except Exception as e:
        return "DOWN", None, str(e)
    finally:
        sock.close()


def check_http(url: str, timeout_sec: int = 2) -> tuple[str, float | None, int | None, str | None]:
    """ارسال درخواست HTTP GET و بررسی پاسخ"""
    try:
        start_time = time.time()
        response = httpx.get(url, timeout=timeout_sec, follow_redirects=True)
        latency = round((time.time() - start_time) * 1000, 2)

        if 200 <= response.status_code < 400:
            return "UP", latency, response.status_code, None
        return "DOWN", latency, response.status_code, f"HTTP Error Status {response.status_code}"
    except httpx.RequestError as e:
        return "DOWN", None, None, f"Connection failed: {e}"
    except Exception as e:
        return "DOWN", None, None, str(e)


async def _check_snmp_async(
    host: str,
    port: int,
    community: str,
    oid: str,
    timeout_sec: int,
) -> tuple[str, float | None, int | None, str | None]:
    """اجرای درخواست SNMP با API نسخه 7.x."""

    start_time = time.time()
    snmp_engine = None

    try:
        snmp_engine = SnmpEngine()

        transport_target = await UdpTransportTarget.create(
            (host, port),
            timeout=timeout_sec,
            retries=1,
        )

        result = await get_cmd(
            snmp_engine,
            CommunityData(
                community,
                mpModel=1,
            ),
            transport_target,
            ContextData(),
            ObjectType(
                ObjectIdentity(oid)
            ),
        )

        (
            error_indication,
            error_status,
            error_index,
            var_binds,
        ) = result

        latency = round(
            (time.time() - start_time) * 1000,
            2,
        )

        if error_indication:
            return (
                "DOWN",
                None,
                None,
                str(error_indication),
            )

        if error_status:
            return (
                "DOWN",
                latency,
                None,
                error_status.prettyPrint(),
            )

        metric_value = None

        for var_bind in var_binds:
            raw_value = var_bind[1].prettyPrint()

            try:
                metric_value = int(raw_value)
            except (TypeError, ValueError):
                metric_value = None

        return (
            "UP",
            latency,
            metric_value,
            None,
        )

    except Exception as exc:
        return (
            "DOWN",
            None,
            None,
            str(exc),
        )

    finally:
        if snmp_engine is not None:
            try:
                snmp_engine.close_dispatcher()
            except Exception:
                pass


def check_snmp(
    host: str,
    port: int = 1161,
    community: str = "public",
    oid: str = "1.3.6.1.4.1.2021.11.9.0",
    timeout_sec: int = 2,
) -> tuple[str, float | None, int | None, str | None]:
    """
    بررسی SNMP با پشتیبانی از API غیرهمزمان pysnmp.
    
    چون run_single_check در ترد جدا اجرا می‌شود،
    asyncio.run در اینجا مشکلی برای حلقه اصلی FastAPI ایجاد نمی‌کند.
    """

    if not HAS_PYSNMP:
        return (
            "DOWN",
            None,
            None,
            "Library pysnmp is not installed",
        )

    try:
        return asyncio.run(
            _check_snmp_async(
                host=host,
                port=port,
                community=community,
                oid=oid,
                timeout_sec=timeout_sec,
            )
        )
    except Exception as exc:
        return "DOWN", None, None, str(exc)

def run_single_check(target: dict):
    """اجرای یک دور مانیتورینگ برای یک مقصد مشخص"""
    t_id = target["id"]
    t_name = target["name"]
    t_type = target["type"].lower()

    status = "DOWN"
    latency = None
    status_code = None
    error_msg = None

    if t_type == "icmp":
        status, latency, error_msg = check_icmp(target["host"])
    elif t_type == "tcp":
        status, latency, error_msg = check_tcp(target["host"], int(target["port"]))
    elif t_type == "http":
        status, latency, status_code, error_msg = check_http(target["url"])
    elif t_type == "snmp":
        port = int(target.get("port", 1161))
        community = target.get("community", "public")
        oid = target.get("oid", "1.3.6.1.4.1.2021.11.9.0")
        status, latency, status_code, error_msg = check_snmp(
            host=target["host"],
            port=port,
            community=community,
            oid=oid,
        )

    # ذخیره در دیتابیس
    save_check_result(
        target_id=t_id,
        target_name=t_name,
        check_type=t_type,
        status=status,
        response_time_ms=latency,
        status_code=status_code,
        error_message=error_msg,
    )

    # لاگ تغییر وضعیت UP / DOWN
    prev_status = last_target_status.get(t_id)
    if prev_status is not None and prev_status != status:
        if status == "DOWN":
            log_event(t_id, "DOWN", f"{t_name} از دسترس خارج شد. دلیل: {error_msg}")
        elif status == "UP":
            log_event(t_id, "RECOVERED", f"{t_name} مجدداً در دسترس قرار گرفت.")
    last_target_status[t_id] = status

    # ارزیابی آستانه و ناهنجاری (در صورتی که سرویس UP باشد)
    if status == "UP":
        check_anomaly_and_threshold(target, latency)

    # نمایش زنده در کنسول
    latency_str = f"{latency}ms" if latency is not None else "N/A"
    metric_str = f" | Val: {status_code}" if status_code is not None else ""
    print(f"[{status:^4}] {t_name:<24} | Type: {t_type:<4} | Latency: {latency_str:<9}{metric_str} | Error: {error_msg or 'None'}")


def start_collector(loop: bool = False):
    config = load_config()
    interval = config.get("check_interval", 10)
    targets = config.get("targets", [])

    print("\n" + "=" * 65)
    print("🚀 Network Monitoring Collector Started...")
    print(f"Monitoring {len(targets)} targets every {interval} seconds.")
    print("=" * 65)

    try:
        while True:
            for target in targets:
                run_single_check(target)
            if not loop:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n⏹️ Collector stopped by user.")


if __name__ == "__main__":
    start_collector(loop=False)
