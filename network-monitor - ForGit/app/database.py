import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_NAME = PROJECT_ROOT / "monitor.db"


def get_connection():
    """ایجاد اتصال به دیتابیس SQLite"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # خروجی‌ها را مثل دیکشنری برمی‌گرداند
    return conn


def init_db():
    """ایجاد جداول مورد نیاز در صورت عدم وجود"""
    conn = get_connection()
    cursor = conn.cursor()

    # جدول لاگ و تاریخچه نتایج مانیتورینگ
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS check_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id TEXT NOT NULL,
        target_name TEXT NOT NULL,
        check_type TEXT NOT NULL,
        status TEXT NOT NULL,           -- 'UP' یا 'DOWN'
        response_time_ms REAL,          -- زمان پاسخ به میلی‌ثانیه
        status_code INTEGER,           -- کد وضعیت برای HTTP (مثلاً 200)
        error_message TEXT,
        checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # جدول رخدادها (فقط زمان‌هایی که وضعیتی تغییر می‌کند یا خطایی رخ می‌دهد)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id TEXT NOT NULL,
        event_type TEXT NOT NULL,       -- 'DOWN', 'RECOVERED'
        description TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


def save_check_result(
    target_id: str,
    target_name: str,
    check_type: str,
    status: str,
    response_time_ms: float = None,
    status_code: int = None,
    error_message: str = None,
):
    """ذخیره نتیجه هر دور بررسی در دیتابیس"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO check_history (
            target_id, target_name, check_type, status,
            response_time_ms, status_code, error_message, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            target_id,
            target_name,
            check_type,
            status,
            response_time_ms,
            status_code,
            error_message,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def log_event(target_id: str, event_type: str, description: str):
    """ثبت رخداد بحرانی (قطع شدن یا بازیابی)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO events (target_id, event_type, description, created_at)
        VALUES (?, ?, ?, ?)
    """,
        (
            target_id,
            event_type,
            description,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database and tables initialized successfully!")
