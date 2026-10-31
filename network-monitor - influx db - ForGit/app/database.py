# app/database.py
import os
import time
from datetime import datetime, timezone
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

INFLUX_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUXDB_TOKEN", "my-super-secret-admin-token-123456789")
INFLUX_ORG = os.getenv("INFLUXDB_ORG", "network_org")
INFLUX_BUCKET = os.getenv("INFLUXDB_BUCKET", "network_bucket")


def get_influx_client():
    """ایجاد اتصال به سرور InfluxDB"""
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, timeout=10_000)


def init_db():
    """بررسی در دسترس بودن سرویس InfluxDB هنگام بوت اپلیکیشن"""
    client = get_influx_client()
    try:
        health = client.health()
        print(f"InfluxDB Connection Status: {health.status}")
    except Exception as exc:
        print(f"Waiting for InfluxDB to become ready: {exc}")
    finally:
        client.close()


def save_check_result(
    target_id: str,
    target_name: str,
    check_type: str,
    status: str,
    response_time_ms: float = None,
    status_code: int = None,
    error_message: str = None,
):
    """
    ذخیره متریک‌های دوره‌ای پایش در Measurement با عنوان check_history.
    فیلدهای تگ جهت فیلتر و کوئری سریع و فیلدها برای مقادیر عددی هستند.
    """
    client = get_influx_client()
    try:
        write_api = client.write_api(write_options=SYNCHRONOUS)

        point = (
            Point("check_history")
            .tag("target_id", str(target_id))
            .tag("target_name", str(target_name))
            .tag("check_type", str(check_type))
            .tag("status", str(status))
            .time(datetime.now(timezone.utc), WritePrecision.NS)
        )

        if response_time_ms is not None:
            point.field("response_time_ms", float(response_time_ms))
        if status_code is not None:
            point.field("status_code", int(status_code))
        if error_message:
            point.field("error_message", str(error_message))
        else:
            point.field("error_message", "")

        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    except Exception as exc:
        print(f"[InfluxDB Write Error - check_result]: {exc}")
    finally:
        client.close()


def log_event(target_id: str, event_type: str, description: str):
    """
    ثبت رویدادهای بحرانی و تغییر وضعیت در Measurement با عنوان events
    """
    client = get_influx_client()
    try:
        write_api = client.write_api(write_options=SYNCHRONOUS)

        point = (
            Point("events")
            .tag("target_id", str(target_id))
            .tag("event_type", str(event_type))
            .field("description", str(description))
            .time(datetime.now(timezone.utc), WritePrecision.NS)
        )

        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    except Exception as exc:
        print(f"[InfluxDB Write Error - log_event]: {exc}")
    finally:
        client.close()


def get_recent_latencies(target_id: str, limit: int = 10) -> list[float]:
    """
    دریافت ۱۰ تاخیر اخیر با کوئری زبان Flux برای محاسبه ناهنجاری آماری (Z-Score)
    """
    client = get_influx_client()
    latencies = []
    try:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "check_history")
          |> filter(fn: (r) => r["target_id"] == "{target_id}")
          |> filter(fn: (r) => r["status"] == "UP")
          |> filter(fn: (r) => r["_field"] == "response_time_ms")
          |> sort(columns: ["_time"], desc: true)
          |> limit(n: {limit})
        '''
        tables = query_api.query(query, org=INFLUX_ORG)
        for table in tables:
            for record in table.records:
                val = record.get_value()
                if val is not None:
                    latencies.append(float(val))
    except Exception as exc:
        print(f"[InfluxDB Query Error - get_recent_latencies]: {exc}")
    finally:
        client.close()

    return latencies


if __name__ == "__main__":
    init_db()
