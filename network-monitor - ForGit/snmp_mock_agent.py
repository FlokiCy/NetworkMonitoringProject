# snmp_mock_agent.py
import asyncio
import threading

import psutil

from pysnmp.entity import engine, config
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.proto.api import v2c


AGENT_HOST = "127.0.0.1"
AGENT_PORT = 1161
COMMUNITY = "public"

# در MIB، OID پایه بدون instance-id تعریف می‌شود.
# مقدار واقعی برای GET روی این OID خواهد بود:
# 1.3.6.1.4.1.2021.11.9.0
SYS_DESCR_OID = (1, 3, 6, 1, 2, 1, 1, 1)
CPU_USAGE_OID = (1, 3, 6, 1, 4, 1, 2021, 11, 9)

_agent_loop = None
_agent_thread_id = None
_agent_engine = None
_agent_ready = threading.Event()


def run_agent():
    """راه‌اندازی SNMP Mock Agent در یک حلقه asyncio مستقل."""

    global _agent_loop, _agent_thread_id, _agent_engine

    loop = asyncio.new_event_loop()
    _agent_loop = loop
    _agent_thread_id = threading.get_ident()
    asyncio.set_event_loop(loop)

    try:
        snmp_engine = engine.SnmpEngine()
        _agent_engine = snmp_engine

        # API جدید pysnmp 7
        config.add_transport(
            snmp_engine,
            udp.DOMAIN_NAME,
            udp.UdpTransport().open_server_mode(
                (AGENT_HOST, AGENT_PORT)
            ),
        )

        # SNMPv2c community
        config.add_v1_system(
            snmp_engine,
            "my-area",
            COMMUNITY,
        )
        # دسترسی SNMPv1
        config.add_vacm_user(
            snmp_engine,
            1,
            "my-area",
            "noAuthNoPriv",
            readSubTree=(1, 3, 6),
        )
        # مجوز خواندن برای تمام OIDهای زیر 1.3.6
        config.add_vacm_user(
            snmp_engine,
            2,
            "my-area",
            "noAuthNoPriv",
            readSubTree=(1, 3, 6),
        )

        snmp_context = context.SnmpContext(snmp_engine)

        mib_instrum = snmp_context.get_mib_instrum()
        mib_builder = mib_instrum.get_mib_builder()

        MibScalar, MibScalarInstance = mib_builder.import_symbols(
            "SNMPv2-SMI",
            "MibScalar",
            "MibScalarInstance",
        )

        # -----------------------------
        # توضیحات تجهیز
        # -----------------------------
        sys_descr = MibScalar(
            SYS_DESCR_OID,
            v2c.OctetString(),
        ).setMaxAccess("read-only")

        class SysDescrInstance(MibScalarInstance):
            def getValue(self, name, *args, **context):
                return self.syntax.clone("Simulated Core Router (Cisco IOS-XE)")

        sys_descr_instance = SysDescrInstance(
            SYS_DESCR_OID,
            (0,),
            v2c.OctetString("Simulated Core Router (Cisco IOS-XE)"),
        )

        # -----------------------------
        # مصرف CPU
        # -----------------------------
        cpu_usage = MibScalar(
            CPU_USAGE_OID,
            v2c.Integer32(),
        ).setMaxAccess("read-only")

        class CpuUsageInstance(MibScalarInstance):
            def getValue(self, name, *args, **context):
                try:
                    cpu_value = int(psutil.cpu_percent(interval=None))
                    cpu_value = max(0, min(cpu_value, 100))
                    return self.syntax.clone(cpu_value)
                except Exception:
                    import traceback
                    print("[SNMP getValue ERROR]", flush=True)
                    traceback.print_exc()
                    raise

        cpu_usage_instance = CpuUsageInstance(
            CPU_USAGE_OID,
            (0,),
            v2c.Integer32(0),
        )

        mib_builder.export_symbols(
            "__MY_MIB",
            sys_descr,
            sys_descr_instance,
            cpu_usage,
            cpu_usage_instance,
        )

        # پاسخ‌گویی به GET
        cmdrsp.GetCommandResponder(
            snmp_engine,
            snmp_context,
        )

        # برای کامل‌تر شدن Agent و پشتیبانی از walk
        cmdrsp.NextCommandResponder(
            snmp_engine,
            snmp_context,
        )

        cmdrsp.BulkCommandResponder(
            snmp_engine,
            snmp_context,
        )

        print(
            f"SNMP Mock Agent listening on "
            f"{AGENT_HOST}:{AGENT_PORT}",
            flush=True,
        )

        _agent_ready.set()

        # API جدید dispatcher
        snmp_engine.transport_dispatcher.job_started(1)

        try:
            snmp_engine.open_dispatcher()
        finally:
            snmp_engine.close_dispatcher()

    except Exception as exc:
        _agent_ready.set()
        print(f"SNMP Agent startup failed: {exc}", flush=True)
        raise

    finally:
        _agent_ready.clear()
        _agent_loop = None
        _agent_thread_id = None
        _agent_engine = None

        if not loop.is_closed():
            loop.close()

        print("SNMP Mock Agent stopped.", flush=True)


def stop_agent():
    """توقف امن Agent از ترد اصلی FastAPI."""

    global _agent_loop, _agent_engine

    loop = _agent_loop
    snmp_engine = _agent_engine

    if loop is not None and loop.is_running():
        if snmp_engine is not None:
            try:
                loop.call_soon_threadsafe(
                    snmp_engine.close_dispatcher
                )
            except Exception:
                loop.call_soon_threadsafe(loop.stop)
        else:
            loop.call_soon_threadsafe(loop.stop)
