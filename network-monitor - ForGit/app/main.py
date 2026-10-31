import asyncio
import os
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db, get_connection
from app.collector import load_config, run_single_check
from simulation.simulator import run_simulation_loop
from snmp_mock_agent import run_agent, stop_agent


async def background_collector():
    """اجرای دوره‌ای بررسی مقصدها."""
    await asyncio.sleep(2)

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
    init_db()

    # Agent را در ترد جدا اجرا می‌کنیم تا حلقهٔ asyncio آن سرور را متوقف نکند.
    agent_task = asyncio.create_task(asyncio.to_thread(run_agent))

    # اجرای دوره‌ای Collector و شبیه‌ساز
    collector_task = asyncio.create_task(background_collector())
    simulation_task = asyncio.create_task(
        asyncio.to_thread(run_simulation_loop)
    )

    # فرصت کوتاه برای راه‌اندازی Agent و سرویس‌های پس‌زمینه
    await asyncio.sleep(0.5)

    print("Network Monitoring API started.")

    try:
        yield
    finally:
        print("Stopping background services...")

        # توقف Collector
        collector_task.cancel()

        # درخواست توقف حلقهٔ Agent
        stop_agent()

        # منتظر پایان taskها می‌مانیم تا منابع آزاد شوند
        await asyncio.gather(
            collector_task,
            simulation_task,
            agent_task,
            return_exceptions=True,
        )

        print("Network Monitoring API stopped.")


app = FastAPI(
    title="Network Monitoring API",
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
        "message": "Monitoring API is alive and healthy",
    }


@app.get("/api/targets/status")
def get_targets_status():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT t1.*
        FROM check_history t1
        INNER JOIN (
            SELECT target_id, MAX(id) AS max_id
            FROM check_history
            GROUP BY target_id
        ) t2 ON t1.id = t2.max_id
        """
    )

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@app.get("/api/history")
def get_history(limit: int = 30):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM check_history ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]


@app.get("/api/events")
def get_events(limit: int = 20):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


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

    return {
        "message": "Dashboard not found. Please create dashboard/index.html"
    }
