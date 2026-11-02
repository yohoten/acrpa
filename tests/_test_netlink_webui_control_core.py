# -*- coding: utf-8 -*-
"""NetLink Phase4-2 batch-1 -- webpanel limited-control kernel + control PIN store.

Run:    python -X utf8 tools/_test_netlink_webui_control_core.py
Exit:   0 = all assertions OK (WARN allowed); 1 = at least one FAIL

Headless: no Tk, no socket, no HTTP. Everything runs in-process.

Coverage:
  A. state: 4 new config keys (defaults) + config.json keeps no PIN/hash
  B. control PIN: set -> verify -> wrong rejected -> clear roundtrip
     (credential store keeps pbkdf2$salt$hash only; config.json clean)
  C. anti brute-force: 5 wrong PINs -> exponential backoff lock,
     correct PIN rejected while locked, audit rows written
  D. submit_local("run"/"stop") drives the injected fake hooks and returns result
  E. submit_local rejects non-whitelisted action ("pause") with stable detail
  F. local panel action does NOT pop the desktop confirm dialog by default
     (replaceable _confirm_action must not be called); strict mode does call it
  G. every local action is written to the AuditLog (no audit bypass / no dupes)
  H. remote handle() path unchanged (same single worker + broadcast/audit)
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# keep console output readable even on a non-UTF8 code page
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

import state                                              # noqa: E402
from netlink import security                              # noqa: E402
from netlink.control import ControlExecutor               # noqa: E402
from netlink.protocol import (                            # noqa: E402
    T_CMD_RUN, T_CMD_STOP, T_CMD_ACK, T_CMD_ERR,
)

TOTAL_BUDGET = 60.0
PIN = "123456"
_ACTOR = "web-panel|127.0.0.1"
_REMOTE = "127.0.0.1:54321"

_fails = 0
_warns = 0
T0 = time.time()


def check(name, cond, detail=""):
    global _fails
    if cond:
        print("[OK]   " + name)
        return True
    _fails += 1
    print("[FAIL] " + name + (" :: " + detail if detail else ""))
    return False


def warn(msg):
    global _warns
    _warns += 1
    print("[WARN] " + msg)


# ── helpers ──────────────────────────────────────────────────────────────
def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def audit_lines(log, n=500):
    try:
        return list(log.tail(n))
    except Exception:
        return []


def has_line(lines, *needles):
    for ln in lines:
        try:
            if all(nd in ln for nd in needles):
                return True
        except Exception:
            continue
    return False


def wait_until(fn, timeout=3.0):
    end = time.time() + float(timeout)
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.01)
    return False


class FakeNode(object):
    """Minimal node stand-in: worker/broadcast only (no socket, no Tk)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._conns = {}
        self.root = None
        self.sent = []
        self.broadcasts = []

    def send(self, conn, msg_dict):
        try:
            self.sent.append((conn, msg_dict))
            return True
        except Exception:
            return False

    def broadcast(self, msg_dict, only_authed=True):
        try:
            self.broadcasts.append(msg_dict)
        except Exception:
            pass
        return 0


class Recorder(object):
    """Fake run/stop hooks: record calls; stop() mimics production state reset."""

    def __init__(self):
        self._lock = threading.Lock()
        self.run_calls = []
        self.stop_calls = 0
        self.order = []

    def reset(self):
        with self._lock:
            self.run_calls = []
            self.stop_calls = 0
            self.order = []

    def run(self, loops=None):
        with self._lock:
            self.run_calls.append(loops)
            self.order.append("run")

    def stop(self):
        with self._lock:
            self.stop_calls += 1
            self.order.append("stop")
        try:
            state.quit3 = True
            state.quit2 = True
            state.running = False
            state.pause_event.set()
        except Exception:
            pass


def main():
    global _fails
    print("=" * 68)
    print("NetLink Phase4-2 b1 -- webpanel control kernel + control PIN self-test")
    print("=" * 68)

    # ── environment isolation ───────────────────────────────────────────
    orig_config_path = state.CONFIG_PATH
    orig_confirm_control = getattr(state, "NETLINK_CONFIRM_CONTROL", True)
    orig_confirmed = list(getattr(state, "NETLINK_CONFIRMED_PEERS", []) or [])
    orig_web = {k: getattr(state, k, None) for k in (
        "NETLINK_WEB_CONTROL", "NETLINK_WEB_CONTROL_TTL",
        "NETLINK_WEB_TLS", "NETLINK_WEB_CONFIRM_CONTROL")}
    orig_api_key = getattr(state, "API_KEY", "")
    orig_cred_write = state._cred_write
    orig_cred_delete = state._cred_delete
    saved_rt = {}
    for name in ("running", "recording", "quit2", "quit3", "has_script",
                 "filename", "script_dir"):
        saved_rt[name] = getattr(state, name, None)
    try:
        saved_rt["exec_state"] = dict(getattr(state, "exec_state", {}) or {})
    except Exception:
        saved_rt["exec_state"] = None
    orig_pin_cred = state.cred_read(security.CONTROL_PIN_TARGET)

    tmpdir = tempfile.mkdtemp(prefix="acrpa_webctl_core_")
    node = FakeNode()
    ex = None
    ex_nostart = None
    try:
        # API key credential must never be touched by state.save_config()
        state._cred_write = lambda secret: True
        state._cred_delete = lambda: None

        state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        state.NETLINK_WEB_CONFIRM_CONTROL = False
        state.NETLINK_CONFIRM_CONTROL = True
        state.NETLINK_CONFIRMED_PEERS = []
        state.API_KEY = orig_api_key
        security.forget_control_pin()
        security.reset_control_pin_guard()

        ex = ControlExecutor(node)
        ex.start()
        check("0a. ControlExecutor started (worker thread)", ex._started is True)
        check("0b. audit log bound to isolated CONFIG_PATH",
              "acrpa_webctl_core_" in ex.audit.path,
              "path={!r}".format(ex.audit.path))

        # ── A. state config keys ────────────────────────────────────────
        print("-" * 68)
        check("A1. netlink_web_control default False",
              state.NETLINK_WEB_CONTROL is False,
              "got={!r}".format(state.NETLINK_WEB_CONTROL))
        check("A2. netlink_web_control_ttl default 300",
              int(state.NETLINK_WEB_CONTROL_TTL) == 300,
              "got={!r}".format(state.NETLINK_WEB_CONTROL_TTL))
        check("A3. netlink_web_tls default False",
              state.NETLINK_WEB_TLS is False,
              "got={!r}".format(state.NETLINK_WEB_TLS))
        check("A4. netlink_web_confirm_control default False",
              state.NETLINK_WEB_CONFIRM_CONTROL is False,
              "got={!r}".format(state.NETLINK_WEB_CONFIRM_CONTROL))
        check("A5. config class exposes the same defaults",
              getattr(state.config, "netlink_web_control", None) is False
              and int(getattr(state.config, "netlink_web_control_ttl", 0)) == 300)
        state.save_config()
        cfg = read_text(state.CONFIG_PATH)
        check("A6. save_config persisted 4 new keys",
              all(k in cfg for k in ("netlink_web_control", "netlink_web_control_ttl",
                                     "netlink_web_tls", "netlink_web_confirm_control")),
              "len={}".format(len(cfg)))

        # ── B. control PIN roundtrip ────────────────────────────────────
        print("-" * 68)
        check("B1. no PIN initially -> has_control_pin False",
              security.has_control_pin() is False)
        check("B2. control_pin_target is the stable credential target",
              security.control_pin_target() == "ACRPA/netlink/web-control-pin",
              "got={!r}".format(security.control_pin_target()))
        check("B3. store_control_pin('123456') -> True",
              security.store_control_pin(PIN) is True)
        check("B4. has_control_pin -> True", security.has_control_pin() is True)
        raw = state.cred_read(security.CONTROL_PIN_TARGET) or ""
        parts = raw.split("$")
        check("B5. credential is 'pbkdf2$salt$hash' (3 parts)",
              raw.startswith("pbkdf2$") and len(parts) == 3,
              "raw={!r}".format(raw[:24]))
        check("B6. credential holds no plaintext PIN", PIN not in raw)
        check("B7. hash == derive_token(pin, salt).hex() (PBKDF2-100k reuse)",
              len(parts) == 3
              and parts[2] == security.derive_token(PIN, parts[1]).hex())
        check("B8. verify_control_pin(correct) -> True",
              security.verify_control_pin(PIN) is True)
        check("B9. verify_control_pin(wrong) -> False",
              security.verify_control_pin("654321") is False)
        security.reset_control_pin_guard()
        check("B10. store_control_pin('abc') rejected (format ^\\d{6}$)",
              security.store_control_pin("abc") is False)
        check("B11. store_control_pin('12345') rejected (length)",
              security.store_control_pin("12345") is False)
        state.save_config()
        cfg = read_text(state.CONFIG_PATH)
        check("B12. config.json never contains PIN plaintext/hash",
              (PIN not in cfg) and ("pbkdf2" not in cfg),
              "len={}".format(len(cfg)))
        calls = []
        security.register_control_session_reset_hook(lambda: calls.append("hit"))
        security.store_control_pin(PIN)
        raw2 = state.cred_read(security.CONTROL_PIN_TARGET) or ""
        check("B13. re-store rotates salt/hash (reset semantics)",
              raw2 != raw and raw2.startswith("pbkdf2$"))
        check("B14. pin set fires session-reset hook (batch-2 hook slot)",
              calls == ["hit"], "calls={}".format(calls))
        security.forget_control_pin()
        check("B15. forget_control_pin clears credential",
              (state.cred_read(security.CONTROL_PIN_TARGET) is None)
              and security.has_control_pin() is False)
        check("B16. pin clear fires session-reset hook",
              calls == ["hit", "hit"], "calls={}".format(calls))

        # ── C. anti brute-force lock ────────────────────────────────────
        print("-" * 68)
        security.store_control_pin(PIN)
        security.reset_control_pin_guard()
        st0 = security.control_pin_lock_state()
        check("C1. lock state idle before attack",
              st0["locked"] is False and st0["fails"] == 0,
              "st={}".format(st0))
        all_rej = True
        for _i in range(security.MAX_PIN_FAILS):
            if security.verify_control_pin("000000") is not False:
                all_rej = False
        check("C2. {} wrong PINs all rejected".format(security.MAX_PIN_FAILS),
              all_rej)
        st = security.control_pin_lock_state()
        check("C3. fails counter reached MAX_PIN_FAILS={}".format(security.MAX_PIN_FAILS),
              st["fails"] >= security.MAX_PIN_FAILS, "st={}".format(st))
        check("C4. backoff lock armed (locked=True)", st["locked"] is True)
        check("C5. first backoff == LOCK_BASE 300s (exponential base)",
              290 <= int(st["retry_after"]) <= 301,
              "retry_after={}".format(st["retry_after"]))
        check("C6. correct PIN also rejected while locked (no bypass)",
              security.verify_control_pin(PIN) is False)
        lines = audit_lines(ex.audit)
        check("C7. audit has WEB_PIN_FAIL rows",
              has_line(lines, "cmd=WEB_PIN_FAIL", "result=err"),
              "n={}".format(len(lines)))
        check("C8. audit has WEB_UNLOCK result=rejected detail locked retry_after",
              has_line(lines, "cmd=WEB_UNLOCK", "result=rejected",
                       "locked retry_after="))
        security.reset_control_pin_guard()
        st2 = security.control_pin_lock_state()
        check("C9. reset_control_pin_guard clears lock",
              st2["locked"] is False and st2["fails"] == 0, "st={}".format(st2))
        check("C10. correct PIN accepted again after lock cleared",
              security.verify_control_pin(PIN) is True)

        # ── D. submit_local(run/stop) via fake hooks ────────────────────
        print("-" * 68)
        rec = Recorder()
        ex.set_hooks(run=rec.run, stop=rec.stop)
        confirm_calls = []

        def fake_confirm(actor, cmd, timeout=30, remote=""):
            confirm_calls.append((actor, cmd))
            return True, False, ""

        ex._confirm_action = fake_confirm
        state.running = False
        state.recording = False
        state.has_script = True
        state.filename = os.path.join(tmpdir, "demo.xls")

        res = ex.submit_local("run", _ACTOR, _REMOTE)
        check("D1. submit_local('run') -> ok/done/script",
              bool(res.get("ok")) and res.get("status") == "done"
              and res.get("mode") == "script" and res.get("action") == "run",
              "res={}".format(res))
        check("D2. fake run hook called exactly once",
              len(rec.run_calls) == 1, "calls={}".format(rec.run_calls))
        check("D3. loops passed to hook is None (web panel has no loop arg)",
              rec.run_calls and rec.run_calls[0] is None,
              "calls={}".format(rec.run_calls))
        check("D4. no desktop confirm dialog for local run by default",
              len(confirm_calls) == 0, "calls={}".format(confirm_calls))

        res2 = ex.submit_local("stop", _ACTOR, _REMOTE)
        check("D5. submit_local('stop') -> ok/done",
              bool(res2.get("ok")) and res2.get("status") == "done"
              and res2.get("action") == "stop",
              "res={}".format(res2))
        check("D6. fake stop hook called", rec.stop_calls >= 1,
              "stop_calls={}".format(rec.stop_calls))
        check("D7. local stop reuses kernel semantics (detail non-empty)",
              bool(res2.get("detail")), "res={}".format(res2))
        res2b = ex.submit_local("stop", _ACTOR, _REMOTE)
        check("D8. local stop is idempotent when not running (still ok)",
              bool(res2b.get("ok")) and res2b.get("status") == "done",
              "res={}".format(res2b))

        # ── E. whitelist + precheck rejections ──────────────────────────
        print("-" * 68)
        before_run, before_stop = len(rec.run_calls), rec.stop_calls
        res3 = ex.submit_local("pause", _ACTOR, _REMOTE)
        check("E1. submit_local('pause') rejected: detail 'action not allowed'",
              (not res3.get("ok")) and res3.get("status") == "rejected"
              and res3.get("detail") == "action not allowed",
              "res={}".format(res3))
        res4 = ex.submit_local("resume", _ACTOR, _REMOTE)
        check("E2. submit_local('resume') rejected likewise",
              (not res4.get("ok")) and res4.get("detail") == "action not allowed",
              "res={}".format(res4))
        res5 = ex.submit_local("nonsense", _ACTOR, _REMOTE)
        check("E3. submit_local('nonsense') rejected",
              (not res5.get("ok")) and res5.get("detail") == "action not allowed",
              "res={}".format(res5))
        check("E4. rejected actions never reach the hooks",
              len(rec.run_calls) == before_run and rec.stop_calls == before_stop,
              "run={} stop={}".format(len(rec.run_calls), rec.stop_calls))

        state.running = True
        rx = ex.submit_local("run", _ACTOR, _REMOTE)
        check("E5. run while running -> rejected 'already running'",
              rx.get("detail") == "already running", "res={}".format(rx))
        state.running = False
        state.recording = True
        rx = ex.submit_local("run", _ACTOR, _REMOTE)
        check("E6. run while recording -> rejected 'recording in progress'",
              rx.get("detail") == "recording in progress", "res={}".format(rx))
        state.recording = False
        state.has_script = False
        rx = ex.submit_local("run", _ACTOR, _REMOTE)
        check("E7. run without script -> rejected 'no script selected'",
              rx.get("detail") == "no script selected", "res={}".format(rx))
        state.has_script = True
        check("E8. precheck rejections did not call the run hook",
              len(rec.run_calls) == before_run,
              "run={}".format(len(rec.run_calls)))

        def bad_run(loops=None):
            raise RuntimeError("boom")

        ex.set_hooks(run=bad_run)
        rx = ex.submit_local("run", _ACTOR, _REMOTE)
        check("E9. hook exception -> status failed",
              (not rx.get("ok")) and rx.get("status") == "failed"
              and "run failed" in rx.get("detail", ""),
              "res={}".format(rx))
        ex.set_hooks(run=rec.run)

        ex_nostart = ControlExecutor(node)
        rx = ex_nostart.submit_local("run", _ACTOR, _REMOTE, timeout=0.3)
        check("E10. no worker -> 'execution timeout' (never blocks forever)",
              rx.get("status") == "rejected" and rx.get("detail") == "execution timeout",
              "res={}".format(rx))

        # ── F. desktop confirm policy for local panel actions ───────────
        print("-" * 68)
        state.running = False
        state.recording = False
        state.has_script = True
        state.NETLINK_WEB_CONFIRM_CONTROL = True
        confirm_calls[:] = []

        def deny_confirm(actor, cmd, timeout=30, remote=""):
            confirm_calls.append((actor, cmd))
            return False, False, "user rejected"

        ex._confirm_action = deny_confirm
        rf = ex.submit_local("run", _ACTOR, _REMOTE)
        check("F1. strict mode -> desktop confirm invoked once",
              len(confirm_calls) == 1, "calls={}".format(len(confirm_calls)))
        check("F2. confirm rejected -> status rejected detail 'user rejected'",
              rf.get("status") == "rejected" and rf.get("detail") == "user rejected",
              "res={}".format(rf))
        check("F3. rejected run did not execute hook",
              len(rec.run_calls) == before_run,
              "run={}".format(len(rec.run_calls)))
        confirm_calls[:] = []

        def allow_confirm(actor, cmd, timeout=30, remote=""):
            confirm_calls.append((actor, cmd))
            return True, False, ""

        ex._confirm_action = allow_confirm
        rf = ex.submit_local("run", _ACTOR, _REMOTE)
        check("F4. strict mode confirmed -> executes (ok/done)",
              bool(rf.get("ok")) and rf.get("status") == "done",
              "res={}".format(rf))
        check("F5. local path never writes NETLINK_CONFIRMED_PEERS (no pollution)",
              list(getattr(state, "NETLINK_CONFIRMED_PEERS", [])) == [],
              "peers={}".format(getattr(state, "NETLINK_CONFIRMED_PEERS", None)))

        state.NETLINK_WEB_CONFIRM_CONTROL = False
        confirm_calls[:] = []

        def record_confirm(actor, cmd, timeout=30, remote=""):
            confirm_calls.append((actor, cmd))
            return True, False, ""

        ex._confirm_action = record_confirm
        state.NETLINK_CONFIRM_CONTROL = True
        rf = ex.submit_local("run", _ACTOR, _REMOTE)
        check("F6. local action ignores netlink_confirm_control (no dialog)",
              bool(rf.get("ok")) and len(confirm_calls) == 0,
              "res={} confirm={}".format(rf, confirm_calls))

        # ── G. audit coverage for local actions ─────────────────────────
        print("-" * 68)
        lines = audit_lines(ex.audit)
        check("G1. audit contains cmd=CMD_RUN for actor=web-panel",
              has_line(lines, "cmd=CMD_RUN", "actor=" + _ACTOR),
              "n={}".format(len(lines)))
        check("G2. audit contains cmd=CMD_STOP for actor=web-panel",
              has_line(lines, "cmd=CMD_STOP", "actor=" + _ACTOR),
              "n={}".format(len(lines)))
        check("G3. audit records whitelist rejection (action not allowed)",
              has_line(lines, "result=rejected", "action not allowed"))
        check("G4. audit records precheck rejection (already running)",
              has_line(lines, "result=rejected", "already running"))
        check("G5. audit records execution timeout",
              has_line(lines, "result=rejected", "execution timeout"))
        check("G6. audit file exists under isolated logs dir",
              os.path.isfile(ex.audit.path)
              and "netlink_audit_" in os.path.basename(ex.audit.path),
              "path={!r}".format(ex.audit.path))
        cfg = read_text(state.CONFIG_PATH)
        check("G7. config.json untouched by audit content",
              ("cmd=CMD_RUN" not in cfg) and ("actor=" not in cfg),
              "len={}".format(len(cfg)))

        # ── H. remote handle() path unchanged ──────────────────────────
        print("-" * 68)
        state.NETLINK_CONFIRM_CONTROL = False
        state.NETLINK_CONFIRMED_PEERS = []
        state.running = False
        state.recording = False
        state.has_script = True
        rec.reset()
        node.sent = []
        node.broadcasts = []
        conn = object()
        ex.handle(conn, {"t": T_CMD_RUN, "data": {}})
        check("H1. remote handle() sends immediate accepted ack to caller",
              any(m.get("t") == T_CMD_ACK
                  and ((m.get("data") or {}).get("status") == "accepted")
                  for _c, m in node.sent),
              "sent={}".format([m.get("t") for _c, m in node.sent]))
        got = wait_until(lambda: len(rec.run_calls) >= 1, 3.0)
        check("H2. remote CMD_RUN still executes through the same worker", got,
              "calls={}".format(len(rec.run_calls)))
        check("H3. remote terminal ack broadcast (status=done)",
              wait_until(lambda: any(
                  m.get("t") == T_CMD_ACK
                  and ((m.get("data") or {}).get("status") == "done")
                  for m in node.broadcasts), 3.0),
              "bcast={}".format([(m.get("data") or {}).get("status")
                                 for m in node.broadcasts]))
        state.running = True
        node.broadcasts = []
        rec.reset()
        ex.handle(conn, {"t": T_CMD_RUN, "data": {}})
        check("H4. remote precheck still rejects (reason 'already running')",
              any(m.get("t") == T_CMD_ERR
                  and ((m.get("data") or {}).get("reason") == "already running")
                  for m in node.broadcasts),
              "bcast={}".format([(m.get("data") or {}).get("reason")
                                 for m in node.broadcasts]))
        check("H5. legacy 6-tuple queue item still accepted by _process",
              _legacy_item_ok(ex, node, rec))
        state.running = False

    except Exception:
        print("[FAIL] uncaught exception:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(tmpdir, ex, ex_nostart, node, orig_config_path,
                      orig_confirm_control, orig_confirmed, orig_web,
                      orig_api_key, orig_cred_write, orig_cred_delete,
                      saved_rt, orig_pin_cred)


def _legacy_item_ok(ex, node, rec):
    """_process must keep working with the old 6-tuple queue item."""
    try:
        state.running = False
        state.recording = False
        state.has_script = True
        state.NETLINK_CONFIRM_CONTROL = False
        rec.reset()
        ex._process((None, T_CMD_STOP, {}, "legacy-actor", None, "legacy-remote"))
        return rec.stop_calls >= 1
    except Exception:
        return False


def finish(tmpdir, ex, ex_nostart, node, orig_config_path, orig_confirm_control,
           orig_confirmed, orig_web, orig_api_key, orig_cred_write,
           orig_cred_delete, saved_rt, orig_pin_cred):
    global _fails
    for e in (ex, ex_nostart):
        try:
            if e is not None:
                e.stop()
        except Exception:
            pass
    try:
        security.forget_control_pin()
        security.reset_control_pin_guard()
    except Exception:
        pass
    try:
        if orig_pin_cred:
            state.cred_write(security.CONTROL_PIN_TARGET, orig_pin_cred)
        else:
            state.cred_delete(security.CONTROL_PIN_TARGET)
    except Exception:
        pass
    try:
        state._cred_write = orig_cred_write
        state._cred_delete = orig_cred_delete
        state.CONFIG_PATH = orig_config_path
        state.API_KEY = orig_api_key
        state.NETLINK_CONFIRM_CONTROL = orig_confirm_control
        state.NETLINK_CONFIRMED_PEERS = orig_confirmed
        for k, v in orig_web.items():
            setattr(state, k, v)
        for k, v in saved_rt.items():
            if k == "exec_state":
                continue
            setattr(state, k, v)
        if saved_rt.get("exec_state") is not None:
            try:
                state.exec_state.clear()
                state.exec_state.update(saved_rt["exec_state"])
            except Exception:
                pass
    except Exception:
        pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    elapsed = time.time() - T0
    print("-" * 68)
    if elapsed >= TOTAL_BUDGET:
        _fails += 1
        print("[FAIL] elapsed {:.1f}s over budget of {}s".format(elapsed, TOTAL_BUDGET))
    else:
        print("[OK]   elapsed {:.1f}s (< {}s)".format(elapsed, TOTAL_BUDGET))
    print("warns={}".format(_warns))
    if _fails == 0:
        print("PASS (all assertions OK)")
        return 0
    print("FAIL ({} assertion(s) failed)".format(_fails))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
