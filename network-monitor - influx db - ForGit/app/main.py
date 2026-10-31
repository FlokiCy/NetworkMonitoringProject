# app/main.py
import asyncio
import os
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import (
    init_db,
    get_influx_client,
    INFLUX_ORG,
    INFLUX_BUCKET,
)
from app.collector import load_config, run_single_check
from simulation.simulator import run_simulation_loop
from snmp_mock_agent import run_agent, stop_agent


async def background_collector():
    """اجرای دوره‌ای پایش مقاصد"""
    await asyncio.sleep(3)

    while True:
        try:
            config = load_config()
            interval = config.get("check_interval", 10)
            targets = config.get("targets", [])

            for target in targets:
                await asyncio.to_thread(run_single_check, target)

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"Error in background collector: {exc}")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # مقداردهی و بررسی اتصال به InfluxDB
    init_db()

    # اجرای Mock Agent و شبیه‌ساز و کالکتور
    agent_task = asyncio.create_task(asyncio.to_thread(run_agent))
    collector_task = asyncio.create_task(background_collector())
    simulation_task = asyncio.create_task(asyncio.to_thread(run_simulation_loop))

    await asyncio.sleep(0.5)
    print("Network Monitoring API started (InfluxDB Backend).")

    try:
        yield
    finally:
        print("Stopping background services...")
        collector_task.cancel()
        stop_agent()
        await asyncio.gather(
            collector_task,
            simulation_task,
            agent_task,
            return_exceptions=True,
        )
        print("Network Monitoring API stopped.")


app = FastAPI(
    title="Network Monitoring API (InfluxDB)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "database": "InfluxDB 2.x",
        "message": "Monitoring API is alive and healthy",
    }


@app.get("/api/targets/status")
def get_targets_status():
    """
    دریافت آخرین رکورد هر تارگت با تابع pivot در Flux (معادل MAX(id) در SQL)
    """
    client = get_influx_client()
    results = {}
    try:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -2h)
          |> filter(fn: (r) => r["_measurement"] == "check_history")
          |> last()
          |> pivot(rowKey:["_time", "target_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        tables = query_api.query(query, org=INFLUX_ORG)
        for table in tables:
            for r in table.records:
                tid = r.values.get("target_id")
                if tid:
                    results[tid] = {
                        "target_id": tid,
                        "target_name": r.values.get("target_name", tid),
                        "check_type": r.values.get("check_type", "N/A"),
                        "status": r.values.get("status", "DOWN"),
                        "response_time_ms": r.values.get("response_time_ms"),
                        "status_code": r.values.get("status_code"),
                        "error_message": r.values.get("error_message") or "",
                        "checked_at": str(r.get_time()),
                    }
    except Exception as exc:
        print(f"Error in /api/targets/status: {exc}")
    finally:
        client.close()

    return list(results.values())


@app.get("/api/history")
def get_history(limit: int = 30):
    """
    دریافت سوابق زمانی برای رسم نمودار Latency در Chart.js
    """
    client = get_influx_client()
    history = []
    try:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "check_history")
          |> filter(fn: (r) => r["_field"] == "response_time_ms")
          |> sort(columns: ["_time"], desc: true)
          |> limit(n: {limit})
        '''
        tables = query_api.query(query, org=INFLUX_ORG)
        for table in tables:
            for r in table.records:
                # تبدیل ساختار زمان به قالب خوانا برای چارت
                t = r.get_time()
                time_str = t.strftime("%Y-%m-%d %H:%M:%S") if t else ""
                history.append({
                    "target_id": r.values.get("target_id"),
                    "response_time_ms": r.get_value(),
                    "checked_at": time_str,
                })
    except Exception as exc:
        print(f"Error in /api/history: {exc}")
    finally:
        client.close()

    # ارسال برعکس جهت نمایش از چپ به راست در نمودار خطی
    return list(reversed(history))


@app.get("/api/events")
def get_events(limit: int = 20):
    """
    دریافت لاگ هشدارهای بحرانی و تغییر وضعیت از اینفلاکس
    """
    client = get_influx_client()
    events = []
    try:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -24h)
          |> filter(fn: (r) => r["_measurement"] == "events")
          |> filter(fn: (r) => r["_field"] == "description")
          |> sort(columns: ["_time"], desc: true)
          |> limit(n: {limit})
        '''
        tables = query_api.query(query, org=INFLUX_ORG)
        for table in tables:
            for r in table.records:
                t = r.get_time()
                time_str = t.strftime("%Y-%m-%d %H:%M:%S") if t else ""
                events.append({
                    "target_id": r.values.get("target_id"),
                    "event_type": r.values.get("event_type"),
                    "description": r.get_value(),
                    "created_at": time_str,
                })
    except Exception as exc:
        print(f"Error in /api/events: {exc}")
    finally:
        client.close()

    return events


if os.path.exists("dashboard"):
    app.mount(
        "/static",
        StaticFiles(directory="dashboard"),
        name="static",
    )


@app.get("/")
def serve_dashboard():
    dashboard_index = os.path.join("dashboard", "index.html")
    if os.path.exists(dashboard_index):
        return FileResponse(dashboard_index)
    return {"message": "Dashboard not found. Please create dashboard/index.html"}
