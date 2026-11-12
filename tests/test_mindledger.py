"""Tests run only on the synthetic transcripts in tests/fixtures (never real data)."""
import contextlib
import datetime
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from unittest import mock

from mindledger import backup, cli, export, ledger, report, scan, schedule, scrub, search
from mindledger.prices import estimate, load_prices

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
PROJECTS = os.path.join(FIX, "projects")
OTHER = os.path.join(FIX, "other-machine")

# fake secrets are assembled at runtime so the literal never appears in the repo
FAKE_ANTHROPIC = "sk-" + "ant-" + "api03-" + "A" * 40
FAKE_GH = "gh" + "p_" + "B" * 36
FAKE_AWS = "AK" + "IA" + "C" * 16
FAKE_PEM = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIabc\n-----END " + "RSA PRIVATE KEY-----"


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def fixture_stats(roots=None):
    return scan.scan(roots or [PROJECTS], use_utc=True)


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mindledger-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.s = fixture_stats()
        self.t = self.s["totals"]

    def test_token_totals_dedup_by_message_id(self):
        # msg_1 appears twice with the same id; counted once
        self.assertEqual(self.t["input"], 18)
        self.assertEqual(self.t["output"], 153)
        self.assertEqual(self.t["cache_read"], 1304)
        self.assertEqual(self.t["cache_creation"], 65)
        self.assertEqual(self.t["tokens"], 1540)

    def test_counts(self):
        self.assertEqual(self.t["sessions"], 3)
        self.assertEqual(self.t["assistant_messages"], 4)  # synthetic placeholder excluded
        self.assertEqual(self.t["user_messages"], 4)
        self.assertEqual(self.t["prompts"], 3)            # tool_result is not a prompt
        self.assertEqual(self.t["messages"], 8)
        self.assertEqual(self.t["days_active"], 3)
        self.assertEqual(self.t["first_day"], "2026-03-01")
        self.assertEqual(self.t["last_day"], "2026-03-04")

    def test_per_day(self):
        d = self.s["days"]
        self.assertEqual(d["2026-03-01"]["tokens"], 415)
        self.assertEqual(d["2026-03-02"]["tokens"], 1111)
        self.assertEqual(d["2026-03-04"]["tokens"], 14)
        self.assertEqual(d["2026-03-01"]["sessions"], 1)
        self.assertEqual(sum(v["tokens"] for v in d.values()), self.t["tokens"])

    def test_models_and_projects(self):
        m = self.s["models"]
        self.assertEqual(set(m), {"claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5"})
        self.assertEqual(m["claude-opus-4-8"]["tokens"], 1111)
        self.assertEqual(self.s["projects"]["/work/demo-api"]["sessions"], 2)
        self.assertEqual(self.s["projects"]["/work/notes-site"]["tokens"], 14)

    def test_bad_lines_counted_not_fatal(self):
        self.assertEqual(self.s["scan"]["bad_lines"], 1)

    def test_second_machine_copy_is_not_double_counted(self):
        both = fixture_stats([PROJECTS, OTHER])
        self.assertEqual(both["totals"]["tokens"], 1540)
        self.assertEqual(both["totals"]["messages"], 8)
        self.assertGreater(both["scan"]["duplicates_skipped"], self.s["scan"]["duplicates_skipped"])

    def test_empty_root(self):
        with tempfile.TemporaryDirectory() as d:
            s = scan.scan([d])
        self.assertEqual(s["totals"]["tokens"], 0)
        self.assertIsNone(s["totals"]["first_day"])


class CostTests(unittest.TestCase):
    def test_estimate_uses_rules_and_reports_unpriced(self):
        models = {"x-sonnet-1": {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_creation": 0},
                  "mystery": {"input": 5, "output": 5, "cache_read": 0, "cache_creation": 0}}
        est = estimate(models)
        self.assertEqual(est["total_usd"], 3.0)
        self.assertEqual(est["unpriced_tokens"], 10)
        self.assertIn("not actual spend", est["label"])

    def test_custom_price_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump([{"match": ".*", "in": 1, "out": 1, "cache_read": 1, "cache_write": 1}], fh)
        try:
            rules = load_prices(fh.name)
            est = estimate(fixture_stats()["models"], rules)
            self.assertAlmostEqual(est["total_usd"], 1540 / 1e6, places=2)
        finally:
            os.remove(fh.name)

    def test_bad_price_file_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump([{"match": "x"}], fh)
        try:
            with self.assertRaises(ValueError):
                load_prices(fh.name)
        finally:
            os.remove(fh.name)


class LedgerTests(TempCase):
    def _stats(self, total):
        return {"totals": {"tokens": total, "input": 0, "output": 0, "cache_read": total,
                           "cache_creation": 0, "sessions": 1, "messages": 1, "days_active": 1}}

    def test_first_row_and_growth(self):
        p = os.path.join(self.tmp, "ledger.jsonl")
        r1 = ledger.append(self._stats(100), p)
        r2 = ledger.append(self._stats(150), p)
        self.assertEqual((r1["status"], r1["delta"]), ("OK", None))
        self.assertEqual((r2["status"], r2["delta"]), ("OK", 50))
        self.assertEqual(len(ledger.load_rows(p)), 2)

    def test_guard_fires_when_total_goes_down(self):
        p = os.path.join(self.tmp, "ledger.jsonl")
        ledger.append(self._stats(1000), p)
        row = ledger.append(self._stats(400), p)
        self.assertEqual(row["status"], "REGRESSION")
        self.assertEqual(row["regression"]["lost"], 600)
        self.assertIn(ledger.REGRESSION_BANNER, ledger.describe(row))

    def test_guard_uses_high_water_not_just_last_row(self):
        p = os.path.join(self.tmp, "ledger.jsonl")
        ledger.append(self._stats(1000), p)
        ledger.append(self._stats(900), p)
        row = ledger.append(self._stats(950), p)  # up vs last row, still below the high-water mark
        self.assertEqual(row["status"], "REGRESSION")
        self.assertEqual(row["regression"]["high_water"], 1000)

    def test_ledger_is_append_only(self):
        p = os.path.join(self.tmp, "ledger.jsonl")
        ledger.append(self._stats(1), p)
        with open(p, encoding="utf-8") as fh:
            first = fh.read()
        ledger.append(self._stats(2), p)
        with open(p, encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith(first))

    def test_truncated_last_line_tolerated(self):
        p = os.path.join(self.tmp, "ledger.jsonl")
        ledger.append(self._stats(10), p)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write('{"total": 99')
        self.assertEqual(ledger.append(self._stats(20), p)["delta"], 10)

    def test_cli_strict_exit_code(self):
        p = os.path.join(self.tmp, "ledger.jsonl")
        sp = os.path.join(self.tmp, "stats.json")
        scan.write_json(self._stats(500), sp)
        ledger.append(self._stats(9999), p)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = cli.main(["ledger", "--stats", sp, "--ledger", p, "--strict"])
        self.assertEqual(rc, 3)
        self.assertIn("TOTAL WENT DOWN", err.getvalue())


class ScrubTests(unittest.TestCase):
    def test_patterns_redacted(self):
        text = "a=%s b=%s c=%s\n%s" % (FAKE_ANTHROPIC, FAKE_GH, FAKE_AWS, FAKE_PEM)
        out, counts = scrub.scrub_text(text)
        for secret in (FAKE_ANTHROPIC, FAKE_GH, FAKE_AWS, "MIIabc"):
            self.assertNotIn(secret, out)
        self.assertIn("anthropic-key", counts)
        self.assertIn("private-key", counts)

    def test_assignment_keeps_name(self):
        out, _ = scrub.scrub_text("DB_PASSWORD=hunter2hunter2hunter2")
        self.assertIn("DB_PASSWORD=REDACTED", out)

    def test_json_sensitive_keys(self):
        src = json.dumps({"env": {"SERVICE_API_TOKEN": "plainvalue", "EDITOR": "vim"}, "author": "someone"})
        out, counts = scrub.scrub_json_text(src)
        obj = json.loads(out)
        self.assertEqual(obj["env"]["SERVICE_API_TOKEN"], "REDACTED")
        self.assertEqual(obj["env"]["EDITOR"], "vim")
        self.assertEqual(obj["author"], "someone")
        self.assertEqual(counts["sensitive-key"], 1)

    def test_ordinary_text_untouched(self):
        s = "risk-based desk-check of the token budget"
        self.assertEqual(scrub.scrub_text(s)[0], s)


class BackupTests(TempCase):
    def _agent_home(self):
        h = os.path.join(self.tmp, "agent")
        os.makedirs(os.path.join(h, "skills", "demo-skill"))
        os.makedirs(os.path.join(h, "agents"))
        os.makedirs(os.path.join(h, "projects", "-work-demo-api", "memory"))
        with open(os.path.join(h, "CLAUDE.md"), "w") as f:
            f.write("# Rules\nkey: %s\n" % FAKE_GH)
        with open(os.path.join(h, "settings.json"), "w") as f:
            json.dump({"env": {"MY_SERVICE_TOKEN": "abc123"}, "model": "opus"}, f)
        with open(os.path.join(h, "skills", "demo-skill", "SKILL.md"), "w") as f:
            f.write("demo skill\n")
        with open(os.path.join(h, "agents", "reviewer.md"), "w") as f:
            f.write("reviewer agent\n")
        with open(os.path.join(h, "projects", "-work-demo-api", "memory", "MEMORY.md"), "w") as f:
            f.write("remember: tests first\n")
        with open(os.path.join(h, "projects", "-work-demo-api", "s.jsonl"), "w") as f:
            f.write("{}\n")  # transcripts are never backed up
        return h

    def test_mirror_scrubs_and_skips_transcripts(self):
        h = self._agent_home()
        vault = os.path.join(self.tmp, "vault")
        rep = backup.mirror(h, vault)
        m = os.path.join(vault, "mirror")
        self.assertEqual(rep["files"], 5)
        self.assertNotIn(FAKE_GH, _read(os.path.join(m, "CLAUDE.md")))
        self.assertEqual(json.loads(_read(os.path.join(m, "settings.json")))["env"]["MY_SERVICE_TOKEN"], "REDACTED")
        self.assertTrue(os.path.exists(os.path.join(m, "memory", "-work-demo-api", "MEMORY.md")))
        self.assertFalse(any(f.endswith(".jsonl") for _, _, fs in os.walk(m) for f in fs))

    def test_mirror_removes_deleted_files(self):
        h = self._agent_home()
        vault = os.path.join(self.tmp, "vault")
        backup.mirror(h, vault)
        os.remove(os.path.join(h, "agents", "reviewer.md"))
        rep = backup.mirror(h, vault)
        self.assertEqual(rep["removed"], 1)

    def test_symlinks_not_followed(self):
        h = self._agent_home()
        outside = os.path.join(self.tmp, "outside.txt")
        _write(outside, "outside\n")
        os.symlink(outside, os.path.join(h, "skills", "link.md"))
        rep = backup.mirror(h, os.path.join(self.tmp, "vault"))
        self.assertEqual(rep["files"], 5)

    def test_zip_retention(self):
        h = self._agent_home()
        vault = os.path.join(self.tmp, "vault")
        backup.mirror(h, vault)
        base = datetime.datetime(2026, 1, 1)
        for i in range(5):
            backup.make_zip(vault, keep=3, now=base + datetime.timedelta(hours=i))
        snaps = backup.list_snapshots(os.path.join(vault, "snapshots"))
        self.assertEqual(len(snaps), 3)
        self.assertTrue(snaps[-1].endswith("2026-01-01_040000.zip"))

    def test_restore_dry_run_and_real(self):
        h = self._agent_home()
        vault = os.path.join(self.tmp, "vault")
        backup.mirror(h, vault)
        zp, _ = backup.make_zip(vault)
        fresh = os.path.join(self.tmp, "fresh")
        os.makedirs(fresh)
        acts = backup.restore(zp, fresh, dry_run=True)
        self.assertEqual({a for a, _, _ in acts}, {"CREATE"})
        self.assertEqual(os.listdir(fresh), [])
        backup.restore(zp, fresh, dry_run=False)
        self.assertTrue(os.path.exists(os.path.join(fresh, "projects", "-work-demo-api", "memory", "MEMORY.md")))
        self.assertTrue(os.path.exists(os.path.join(fresh, "skills", "demo-skill", "SKILL.md")))
        backup.restore(zp, fresh, dry_run=False)  # second run keeps a .bak of what it overwrites
        self.assertTrue(any(".bak-" in f for f in os.listdir(fresh)))

    def test_restore_rejects_path_traversal(self):
        zp = os.path.join(self.tmp, "evil.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("mirror/../../escape.txt", "x")
            zf.writestr("/abs.txt", "x")
            zf.writestr("mirror/memory/../../../x.txt", "x")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        acts = backup.restore(zp, home, dry_run=False)
        self.assertEqual({a for a, _, _ in acts}, {"REJECT"})
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))

    def test_restore_cli_requires_flag(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["restore", "--vault", self.tmp]), 2)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.s = fixture_stats()

    def test_self_contained(self):
        h = report.render(self.s, today=datetime.date(2026, 3, 4))
        self.assertTrue(h.startswith("<!doctype html>"))
        self.assertNotRegex(h, r"""(src|href)\s*=\s*["']https?:""")
        self.assertNotIn("@import", h)
        self.assertIn("<svg", h)
        self.assertIn("prefers-color-scheme:dark", h)
        self.assertIn("1.54K", h)

    def test_private_mode_embeds_no_names(self):
        h = report.render(self.s, private=True)
        self.assertNotIn("demo-api", h)
        self.assertNotIn("notes-site", h)
        self.assertIn(report.project_hash("/work/demo-api"), h)

    def test_toggle_mode_never_embeds_full_path(self):
        h = report.render(self.s)
        self.assertIn('data-n="demo-api"', h)
        self.assertNotIn("/work/demo-api", h)

    def test_escaping(self):
        s = json.loads(json.dumps(self.s))
        s["models"]["<script>x</script>"] = dict(s["models"]["claude-opus-4-8"])
        self.assertNotIn("<script>x</script>", report.render(s))

    def test_streaks(self):
        days = {d: {"tokens": 1} for d in ("2026-03-01", "2026-03-02", "2026-03-03", "2026-03-05", "2026-03-06")}
        self.assertEqual(report.streaks(days, datetime.date(2026, 3, 6)), (2, 3))
        self.assertEqual(report.streaks(days, datetime.date(2026, 3, 7)), (2, 3))
        self.assertEqual(report.streaks(days, datetime.date(2026, 3, 9)), (0, 3))

    def test_cache_share(self):
        self.assertAlmostEqual(report.cache_share(self.s["totals"]), 100 * 1304 / (18 + 1304 + 65))

    def test_cost_section_optional(self):
        self.assertNotIn("Cost estimate", report.render(self.s))
        self.assertIn("List-price equivalent", report.render(self.s, show_cost=True))


class ExportTests(unittest.TestCase):
    def test_public_fields_only(self):
        pub = export.public_stats(fixture_stats(), today=datetime.date(2026, 3, 4))
        self.assertEqual(set(pub), set(export.PUBLIC_FIELDS))
        self.assertEqual(pub["total_tokens"], 1540)
        self.assertEqual(pub["daily_avg_tokens_30d"], round(1540 / 30))
        blob = json.dumps(pub)
        for leak in ("/", "work", "demo", "claude", "usd", "cost"):
            self.assertNotIn(leak, blob.replace("as_of", "").lower())


class SearchTests(TempCase):
    def test_fts_and_fallback(self):
        db = os.path.join(self.tmp, "s.db")
        added, fts = search.build_index(db, [PROJECTS])
        self.assertGreater(added, 0)
        rows, mode = search.search(db, "cache keys")
        self.assertTrue(rows)
        self.assertIn(mode, ("fts5", "scan"))
        self.assertEqual(rows[0][0], scan._local_day("2026-03-02T12:00:10Z"))
        rows2, mode2 = search.search(db, "healthz route", force_fallback=True)
        self.assertEqual(mode2, "scan")
        self.assertTrue(any("healthz" in r[3] for r in rows2))

    def test_incremental_and_tool_payloads_not_indexed(self):
        db = os.path.join(self.tmp, "s.db")
        search.build_index(db, [PROJECTS])
        self.assertEqual(search.build_index(db, [PROJECTS])[0], 0)  # unchanged files skipped
        con = sqlite3.connect(db)
        txt = " ".join(r[0] for r in con.execute("SELECT text FROM chunks"))
        con.close()
        self.assertNotIn("tool_result", txt)

    def test_query_with_fts_syntax_is_safe(self):
        db = os.path.join(self.tmp, "s.db")
        search.build_index(db, [PROJECTS])
        rows, _ = search.search(db, 'footer" *)')  # stray quote/paren/star must not raise
        self.assertTrue(rows)
        search.search(db, 'NEAR( "unbalanced')

    def test_removed_file_dropped(self):
        root = os.path.join(self.tmp, "p")
        shutil.copytree(PROJECTS, root)
        db = os.path.join(self.tmp, "s.db")
        search.build_index(db, [root])
        os.remove(os.path.join(root, "-work-notes-site", "s3.jsonl"))
        search.build_index(db, [root])
        self.assertFalse(search.search(db, "footer")[0])


class ScheduleTests(unittest.TestCase):
    def test_launchd_plist(self):
        p = schedule.launchd_plist("/tmp/logs")
        self.assertIn("<integer>7200</integer>", p)
        self.assertIn(schedule.LABEL, p)
        self.assertIn("<string>sync</string>", p)

    def test_systemd_units(self):
        service, timer = schedule.systemd_units()
        self.assertIn("OnUnitActiveSec=2h", timer)
        self.assertIn("-m\" \"mindledger\" \"sync\"", service)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"HOME": d}):
            out = schedule.install(os.path.join(d, "logs"), dry_run=True, system="Darwin")
            self.assertTrue(any("launchctl bootstrap" in line for line in out))
            self.assertEqual(os.listdir(d), [])

    def test_cron_fallback(self):
        out = schedule.install("/tmp/logs", dry_run=True, system="Plan9")
        self.assertTrue(out[-1].startswith("0 */2 * * *"))


class CliTests(TempCase):
    def test_sync_end_to_end(self):
        agent = os.path.join(self.tmp, "agent")
        os.makedirs(os.path.join(agent, "skills"))
        _write(os.path.join(agent, "CLAUDE.md"), "hello\n")
        env = {"MINDLEDGER_HOME": os.path.join(self.tmp, "home"), "MINDLEDGER_CLAUDE_HOME": agent,
               "MINDLEDGER_PROJECTS": PROJECTS}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["sync"]), 0)
            self.assertEqual(cli.main(["sync"]), 0)
        home = env["MINDLEDGER_HOME"]
        for f in ("stats.json", "ledger.jsonl", "report.html", "public.json", "sync.log"):
            self.assertTrue(os.path.exists(os.path.join(home, f)), f)
        rows = ledger.load_rows(os.path.join(home, "ledger.jsonl"))
        self.assertEqual([r["delta"] for r in rows], [None, 0])
        self.assertFalse(os.path.exists(os.path.join(home, ".sync.lock")))

    def test_scan_cli_writes_stats(self):
        out = os.path.join(self.tmp, "st.json")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            cli.main(["scan", "--projects", PROJECTS, "--out", out, "--utc", "--cost"])
        self.assertEqual(scan.load_json(out)["totals"]["tokens"], 1540)
        self.assertIn("list-price equivalent", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
