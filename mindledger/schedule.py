"""Run `mindledger sync` every 2 hours: launchd on macOS, systemd user timer on
Linux, or a printed cron line when neither is available."""
import os
import platform
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape

LABEL = "io.mindledger.sync"
UNIT = "mindledger-sync"
INTERVAL_SECONDS = 2 * 60 * 60


def _pkg_parent():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def command():
    return [sys.executable, "-m", "mindledger", "sync"]


def launchd_plist(log_dir):
    args = "".join("<string>%s</string>" % escape(a) for a in command())
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array>{args}</array>
<key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>{pp}</string></dict>
<key>StartInterval</key><integer>{iv}</integer>
<key>RunAtLoad</key><true/>
<key>StandardOutPath</key><string>{out}</string>
<key>StandardErrorPath</key><string>{err}</string>
</dict></plist>
""".format(label=LABEL, args=args, pp=escape(_pkg_parent()), iv=INTERVAL_SECONDS,
           out=escape(os.path.join(log_dir, "schedule.out.log")),
           err=escape(os.path.join(log_dir, "schedule.err.log")))


def systemd_units():
    exec_line = " ".join('"%s"' % a for a in command())
    service = ("[Unit]\nDescription=MindLedger sync\n\n[Service]\nType=oneshot\n"
               "Environment=PYTHONPATH=%s\nExecStart=%s\n" % (_pkg_parent(), exec_line))
    timer = ("[Unit]\nDescription=Run MindLedger sync every 2 hours\n\n[Timer]\nOnBootSec=10min\n"
             "OnUnitActiveSec=2h\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    return service, timer


def cron_line():
    return "0 */2 * * * PYTHONPATH=%s %s" % (_pkg_parent(), " ".join(command()))


def _run(cmd, dry_run, out):
    out.append("$ " + " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=False)


def install(log_dir, dry_run=False, system=None):
    """Returns a list of human-readable lines describing what was (or would be) done."""
    system = system or platform.system()
    out = []
    home = os.path.expanduser("~")
    if system == "Darwin":
        path = os.path.join(home, "Library", "LaunchAgents", LABEL + ".plist")
        out.append("write %s" % path)
        if not dry_run:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            os.makedirs(log_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(launchd_plist(log_dir))
        uid = str(os.getuid())
        _run(["launchctl", "bootout", "gui/%s/%s" % (uid, LABEL)], dry_run, out)
        _run(["launchctl", "bootstrap", "gui/%s" % uid, path], dry_run, out)
        return out
    if system == "Linux" and shutil.which("systemctl"):
        unit_dir = os.path.join(home, ".config", "systemd", "user")
        service, timer = systemd_units()
        for name, body in ((UNIT + ".service", service), (UNIT + ".timer", timer)):
            p = os.path.join(unit_dir, name)
            out.append("write %s" % p)
            if not dry_run:
                os.makedirs(unit_dir, exist_ok=True)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(body)
        _run(["systemctl", "--user", "daemon-reload"], dry_run, out)
        _run(["systemctl", "--user", "enable", "--now", UNIT + ".timer"], dry_run, out)
        return out
    out.append("No launchd or systemd found. Add this line with `crontab -e`:")
    out.append(cron_line())
    return out


def uninstall(dry_run=False, system=None):
    system = system or platform.system()
    out = []
    home = os.path.expanduser("~")
    if system == "Darwin":
        path = os.path.join(home, "Library", "LaunchAgents", LABEL + ".plist")
        _run(["launchctl", "bootout", "gui/%s/%s" % (os.getuid(), LABEL)], dry_run, out)
        out.append("remove %s" % path)
        if not dry_run and os.path.exists(path):
            os.remove(path)
        return out
    if system == "Linux" and shutil.which("systemctl"):
        _run(["systemctl", "--user", "disable", "--now", UNIT + ".timer"], dry_run, out)
        unit_dir = os.path.join(home, ".config", "systemd", "user")
        for name in (UNIT + ".service", UNIT + ".timer"):
            p = os.path.join(unit_dir, name)
            out.append("remove %s" % p)
            if not dry_run and os.path.exists(p):
                os.remove(p)
        return out
    out.append("Remove the mindledger line from `crontab -e`.")
    return out
