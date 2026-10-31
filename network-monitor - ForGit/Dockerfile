# استفاده از نسخه سبک پایتون
FROM python:3.10-slim

# نصب ابزارهای شبکه برای پینگ (ICMP) در لینوکس داکر
RUN apt-get update && apt-get install -y iputils-ping && rm -rf /var/lib/apt/lists/*

# تنظیم پوشه کاری
WORKDIR /app

# کپی فایل نیازمندی‌ها و نصب آن‌ها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کل کدها به داخل کانتینر
COPY . .

# باز کردن پورت‌های مورد نیاز (۸۰۰۰ برای API و ۱۱۶۱ برای SNMP)
EXPOSE 8000
EXPOSE 1161

# دستور اجرای پروژه (اجرای API که کالکتور را هم در پس‌زمینه دارد)
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
