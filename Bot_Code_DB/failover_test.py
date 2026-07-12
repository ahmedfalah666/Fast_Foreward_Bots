"""Failover test harness.
Usage:
  python failover_test.py start-pc     # Start PC bot in background
  python failover_test.py start-ipad   # Start iPad bot via SSH
  python failover_test.py monitor      # Poll PG lock every 3s
  python failover_test.py kill-pc      # Kill PC bot
  python failover_test.py kill-ipad    # Kill iPad bot via SSH
  python failover_test.py check        # Check PG lock status
"""
import sys, os, subprocess, time, datetime, json

sys.path.insert(0, '.')
from db import _sync_lock_dsn, LOCK_STALE_SECONDS, LOCK_INSTANCE_NAME
import psycopg

DSN = _sync_lock_dsn()
PC_DIR = os.path.dirname(os.path.abspath(__file__))

def check_lock():
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT instance_name, last_heartbeat FROM bot_lock WHERE id = 1')
            row = cur.fetchone()
            if row:
                age = (datetime.datetime.utcnow() - row[1]).total_seconds()
                return row[0], round(age, 1)
    return None, None

def print_status(label=""):
    owner, age = check_lock()
    stale = age is not None and age > LOCK_STALE_SECONDS
    print(f"[{label}] Lock: owner={owner}, age={age}s, stale={stale}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"

    if cmd == "start-pc":
        log = os.path.join(PC_DIR, "pc_bot.log")
        with open(log, "w") as f:
            f.write(f"=== START at {datetime.datetime.now()} ===\n")
        proc = subprocess.Popen(
            [sys.executable, "-u", "main.py"],
            cwd=PC_DIR,
            stdout=open(log, "a"), stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
        print(f"PC bot PID: {proc.pid}")
        with open(os.path.join(PC_DIR, "pc_bot.pid"), "w") as f:
            f.write(str(proc.pid))

    elif cmd == "kill-pc":
        pidfile = os.path.join(PC_DIR, "pc_bot.pid")
        if os.path.exists(pidfile):
            with open(pidfile) as f:
                pid = int(f.read().strip())
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            os.remove(pidfile)
            print(f"Killed PC bot (PID {pid})")
        else:
            # Try harder
            subprocess.run(["taskkill", "/F", "/IM", "python.exe"], capture_output=True)
            print("Killed all python.exe")

    elif cmd == "start-ipad":
        ssh_key = os.path.expanduser("~/.ssh/id_rsa")
        cmd_ssh = [
            "ssh", "-i", ssh_key, "-p", "8022",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            "u0_a223@192.168.0.103",
            "cd ~/Fast_Foreward_Bots/Bot_Code_DB && nohup python -u main.py > ipad_bot.log 2>&1 &"
        ]
        result = subprocess.run(cmd_ssh, capture_output=True, text=True, timeout=15)
        print(f"SSH stdout: {result.stdout}")
        print(f"SSH stderr: {result.stderr}")
        print(f"SSH return: {result.returncode}")

    elif cmd == "kill-ipad":
        ssh_key = os.path.expanduser("~/.ssh/id_rsa")
        result = subprocess.run(
            ["ssh", "-i", ssh_key, "-p", "8022",
             "-o", "ConnectTimeout=10",
             "u0_a223@192.168.0.103",
             "pkill -f 'python.*main.py' 2>/dev/null; echo 'killed'"],
            capture_output=True, text=True, timeout=15
        )
        print(f"Kill result: {result.stdout.strip()}")

    elif cmd == "check":
        owner, age = check_lock()
        print(f"current PG lock: owner={owner}, age={age}s")

    elif cmd == "monitor":
        print(f"Monitoring PG lock every 3s (stale > {LOCK_STALE_SECONDS}s)...")
        print(f"Local instance: {LOCK_INSTANCE_NAME}")
        print()
        while True:
            owner, age = check_lock()
            stale = age is not None and age > LOCK_STALE_SECONDS
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            stealable = "STEALABLE" if stale else "fresh"
            print(f"[{ts}] owner={owner}  age={age}s  ({stealable})")
            time.sleep(3)
