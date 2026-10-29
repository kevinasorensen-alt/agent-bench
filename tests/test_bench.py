"""Run: python agent-bench/tests/test_bench.py"""
import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import bench  # noqa: E402
import scorers  # noqa: E402

KEY = HERE / "tasks" / "invoice-extraction" / "answer_key.csv"


def key_rows():
    with open(KEY, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class InvoiceScorer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, rows):
        out = self.tmp / "output.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        return out

    def test_answer_key_scores_full_marks(self):
        r = scorers.score("invoices", self.write(key_rows()), KEY)
        self.assertEqual((r["correct"], r["total"]), (160, 160))

    def test_missing_output_scores_zero_without_error(self):
        r = scorers.score("invoices", self.tmp / "nope.csv", KEY)
        self.assertEqual(r["correct"], 0)

    def test_formatting_differences_that_are_not_the_test_still_count(self):
        rows = key_rows()
        rows[0]["vendor"] = "  " + rows[0]["vendor"].upper()
        rows[0]["total"] = "$" + f"{float(rows[0]['total']):,.2f}"
        rows[0]["currency"] = rows[0]["currency"].lower()
        self.assertEqual(scorers.score("invoices", self.write(rows), KEY)["correct"], 160)

    def test_wrong_values_and_wrong_date_format_lose_points(self):
        rows = key_rows()
        rows[0]["total"] = "1.00"
        y, m, d = rows[1]["invoice_date"].split("-")
        rows[1]["invoice_date"] = f"{d}/{m}/{y}"
        self.assertEqual(scorers.score("invoices", self.write(rows), KEY)["correct"], 158)

    def test_duplicate_rows_cannot_score_twice(self):
        rows = key_rows()
        rows = rows + [dict(rows[0])]
        r = scorers.score("invoices", self.write(rows), KEY)
        self.assertEqual((r["correct"], r["rows_found"]), (160, 20))


class ClaudeStreamView(unittest.TestCase):
    def test_result_event_carries_cost(self):
        shown, cost = bench.view_line("claude-stream-json", json.dumps(
            {"type": "result", "subtype": "success", "total_cost_usd": 0.42,
             "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5}}))
        self.assertEqual(cost, ("total_usd", 0.42, {"input": 100, "cached_input": 90, "output": 5}))
        self.assertIn("finished", shown)

    def test_tool_use_is_summarised(self):
        shown, _ = bench.view_line("claude-stream-json", json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "/work/output.csv"}}]}}))
        self.assertTrue(shown.startswith("→ Write"))

    def test_non_json_and_plain_setups_pass_through(self):
        self.assertEqual(bench.view_line("claude-stream-json", "not json"), ("not json", None))
        self.assertEqual(bench.view_line("none", "read invoice_01.txt"), ("read invoice_01.txt", None))


class CodexView(unittest.TestCase):
    def test_turn_usage_becomes_a_token_event(self):
        _, cost = bench.view_line("codex-json", json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 50}}))
        self.assertEqual(cost, ("tokens", {"input": 1000, "cached_input": 400, "output": 50}))

    def test_commands_and_messages_are_shown(self):
        shown, _ = bench.view_line("codex-json", json.dumps(
            {"type": "item.completed", "item": {"type": "command_execution", "command": "ls /work/inputs"}}))
        self.assertEqual(shown, "→ $ ls /work/inputs")
        shown, _ = bench.view_line("codex-json", json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}}))
        self.assertEqual(shown, "Done.")

    def test_cached_tokens_bill_at_cached_rate(self):
        usd = bench.token_cost({"input": 1_000_000, "cached_input": 400_000, "output": 100_000},
                               {"input": 2.0, "cached_input": 0.5, "output": 10.0})
        self.assertAlmostEqual(usd, 0.6 * 2.0 + 0.4 * 0.5 + 0.1 * 10.0)


class Decoys(unittest.TestCase):
    KEY2 = HERE / "tasks" / "invoice-extraction-v2" / "answer_key.csv"

    def test_full_key_scores_320_and_each_decoy_costs_8(self):
        with open(self.KEY2, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "output.csv"
            fields = ["file"] + scorers.INVOICE_FIELDS
            invoices = [{k: r[k] for k in fields} for r in rows if r["is_invoice"] == "yes"]
            decoy = next({k: r[k] for k in fields} for r in rows if r["is_invoice"] == "no")
            for extra, expected in [([], 320), ([decoy], 312)]:
                with open(out, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fields)
                    w.writeheader()
                    w.writerows(invoices + extra)
                self.assertEqual(scorers.score("invoices", out, self.KEY2)["correct"], expected)
        finally:
            shutil.rmtree(tmp)


class Summary(unittest.TestCase):
    def test_setup_that_never_ran_is_not_averaged_as_zero(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            base = {"total": 160, "cost_usd": 0.0, "seconds": 1.0, "inputs_modified": False, "extra_files": []}
            results = [[{**base, "setup": "A", "status": "finished", "correct": 100},
                        {**base, "setup": "B", "status": "not run: missing KEY", "correct": 0}]]
            text = bench.summarise({"title": "T", "id": "t"}, results, tmp)
            summary = json.loads((tmp / "results.json").read_text())["summary"]
            self.assertEqual(summary["B"]["trials"], 0)
            self.assertEqual(summary["A"]["correct_mean"], 100)
            self.assertIn("not run", text)
        finally:
            shutil.rmtree(tmp)



class Narration(unittest.TestCase):
    import narrate as N  # noqa: E402  (imported here so the other tests don't need the TTS packages)

    def test_misses_are_listed_per_field_and_skipped_invoices_are_named(self):
        with open(Decoys.KEY2, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["is_invoice"] == "yes"]
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "output.csv"
            fields = ["file"] + scorers.INVOICE_FIELDS
            keep = [{k: r[k] for k in fields} for r in rows[1:]]
            keep[0]["total"] = "0.00"
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(keep)
            r = scorers.score("invoices", out, Decoys.KEY2)
            self.assertEqual(r["missing_rows"], [rows[0]["file"]])
            self.assertEqual(len(r["misses"]), 8 + 1)
            self.assertIn([keep[0]["file"], "total"], r["misses"])
        finally:
            shutil.rmtree(tmp)

    def test_catch_names_unseen_layouts_and_skipped_invoices(self):
        task = {"task_dir": str(HERE / "tasks" / "invoice-extraction-v2"), "answer_key": "answer_key.csv",
                "layout_names": {"letter_eu": "German-format invoices", "ocr_noisy": "scanned invoices"}}
        layouts, seen = self.N.key_layouts(task)
        german = [f for f, l in layouts.items() if l == "letter_eu"][:1]
        scanned = [f for f, l in layouts.items() if l == "ocr_noisy"][:1]
        r = {"correct": 309, "misses": [[german[0], "total"], [german[0], "tax"], [german[0], "subtotal"]]
             + [[scanned[0], f] for f in scorers.INVOICE_FIELDS], "missing_rows": scanned}
        text = self.N.the_catch(task, r, {"name": "Blind script (no AI)", "narration": "Written blind."}, 320)
        self.assertIn("Every point it lost came from layouts it never saw: 8 on scanned invoices and 3 on German-format invoices.", text)
        self.assertIn("skipped a real invoice", text)
        self.assertTrue(text.startswith("Now, the catch with Blind script. Written blind."))

    def test_why_slow_uses_logged_facts_only(self):
        setups = {"Fast": {"name": "Fast", "network": "bridge", "cost": "codex-json"},
                  "Slow": {"name": "Slow", "network": "bridge", "cost": "metered-proxy"}}
        base = {"status": "finished", "total": 320, "correct": 320, "started_at": 1000.0}
        ran = [dict(base, setup="Fast", seconds=60, ended_at=1060.0, tokens={"output": 2500}),
               dict(base, setup="Slow", seconds=800, ended_at=1800.0, tokens={"output": 25000})]
        hist = {(1, "Slow"): [{"t": 0, "correct": 0}, {"t": 520, "correct": 320}]}
        cues, note = self.N.why_slow(None, 1, 0.0, 1000.0, ran, setups, hist, {})
        text = " ".join(c[2] for c in cues)
        self.assertIn("about 25 thousand tokens to write the same file Fast wrote with 2.5 thousand", text)
        self.assertIn("Slow now has 320 out of 320", text)
        self.assertIn("after about 9 minutes", note["Slow"])

    def test_spoken_helpers(self):
        self.assertEqual(self.N.spoken_time(1), "1 second")
        self.assertEqual(self.N.spoken_time(819), "about 14 minutes")
        self.assertEqual(self.N.spoken_cost(0.3, {"cost": "codex-json"}), "for about 30 cents")
        self.assertEqual(self.N.spoken_name({"name": "Codex · GPT-6 Astra"}), "Codex with GPT-6 Astra")


class SpeedUp(unittest.TestCase):
    import narrate as N  # noqa: E402

    def race(self):
        base = {"status": "finished", "started_at": 1000.0, "seconds": 0, "t0": 1000.0}
        return {"trials": [[dict(base, setup="A", ended_at=1060.0), dict(base, setup="Slow", ended_at=1900.0)]]}

    def test_processing_is_sped_up_except_around_spoken_moments(self):
        rec = {"recording_started": 990.0}
        cues = [(500.0, "medium", "now has its final score"), (300.0, "low", "still working")]
        windows = self.N.fast_segments(self.race(), rec, cues, [5.0, 2.0])
        # race runs 13s..911s of the recording; the medium line keeps 498..506.5 at normal speed; low lines don't count
        self.assertEqual(windows, [(13.0, 498.0), (506.5, 911.0)])

    def test_time_map_compresses_only_inside_windows(self):
        f = self.N.time_map(self.N.edit_plan([(100.0, 180.0)], [], 1000.0))
        self.assertEqual(f(50.0), 50.0)
        self.assertEqual(f(180.0), 100.0 + 80.0 / self.N.FAST)
        self.assertEqual(f(200.0), 200.0 - 80.0 * (1 - 1 / self.N.FAST))

    def test_cuts_remove_time_and_win_over_speed_ups(self):
        plan = self.N.edit_plan([(100.0, 200.0)], [(150.0, 160.0)], 300.0)
        self.assertEqual(plan, [(0.0, 100.0, 1), (100.0, 150.0, self.N.FAST), (150.0, 160.0, 0),
                                (160.0, 200.0, self.N.FAST), (200.0, 300.0, 1)])
        f = self.N.time_map(plan)
        self.assertAlmostEqual(f(300.0), 100.0 + 90.0 / self.N.FAST + 100.0)

    def test_capped_run_ends_at_start_plus_seconds(self):
        self.assertEqual(self.N.end_of({"ended_at": None, "started_at": 10.0, "seconds": 1205.0}), 1215.0)


class MonthEnd(unittest.TestCase):
    KEY3 = HERE / "tasks" / "month-end-close-v3" / "answer_key.csv"

    def rows(self):
        with open(self.KEY3, newline="", encoding="utf-8") as f:
            return [r for r in csv.DictReader(f)]

    def score(self, out_rows):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "output.csv"
            fields = ["file", "invoice_number"] + scorers.MONTH_END_FIELDS
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(out_rows)
            return scorers.score("month_end", out, self.KEY3)
        finally:
            shutil.rmtree(tmp)

    def perfect(self):
        return [{"file": r["files"].split(";")[0], "invoice_number": r["invoice_number"],
                 **{f: r[f] for f in scorers.MONTH_END_FIELDS}} for r in self.rows() if r["trap"] != "decoy"]

    def test_answer_key_scores_full_marks(self):
        r = self.score(self.perfect())
        self.assertEqual((r["correct"], r["total"], r["made_up"]), (1116, 1116, []))

    def test_filling_a_blank_due_date_is_a_made_up_value(self):
        rows = self.perfect()
        blank = next(r for r in rows if not r["due_date"])
        blank["due_date"] = "2026-10-01"
        r = self.score(rows)
        self.assertEqual(r["correct"], 1115)
        self.assertEqual(r["made_up"], [[blank["invoice_number"], "due_date"]])

    def test_double_entries_and_decoys_cost_a_document_each(self):
        rows = self.perfect()
        decoy_file = next(r["files"] for r in self.rows() if r["trap"] == "decoy")
        rows += [dict(rows[0]), {"file": decoy_file, "invoice_number": "Q-1234", **{f: "" for f in scorers.MONTH_END_FIELDS}}]
        r = self.score(rows)
        self.assertEqual(r["correct"], 1116 - 18)
        self.assertEqual(len(r["double_entries"]), 1)

    def test_zero_tax_credit_note_accepts_plain_zero(self):
        rows = self.perfect()
        zero = [r for r in rows if r["doc_type"] == "credit_note" and float(r["tax"]) == 0]
        self.assertTrue(zero)
        for r in zero:
            self.assertEqual(r["tax"], "0.00")  # the key never says -0.00
            r["tax"] = "-0.00"  # and a contestant writing -0.00 isn't penalised either
        self.assertEqual(self.score(rows)["correct"], 1116)

    def test_resent_invoice_counts_once_whichever_file_is_named(self):
        rows = self.perfect()
        resent = next(r for r in self.rows() if "resent" in r["trap"])
        for row in rows:
            if row["invoice_number"] == resent["invoice_number"]:
                row["file"] = resent["files"].split(";")[1]
        self.assertEqual(self.score(rows)["correct"], 1116)


class Replays(unittest.TestCase):
    import narrate as N  # noqa: E402

    def test_trials_after_the_first_are_shown_as_fast_replays(self):
        base = {"status": "finished", "seconds": 0}
        results = {"trials": [[dict(base, t0=1000.0, started_at=1000.0, ended_at=1100.0)],
                              [dict(base, t0=1200.0, started_at=1200.0, ended_at=1400.0)]]}
        replays = self.N.replay_windows(results, {"recording_started": 990.0})
        self.assertEqual(replays, [(208.0, 411.5, self.N.REPLAY)])
        plan = self.N.edit_plan([(100.0, 300.0)], [], 500.0, replays)
        self.assertIn((208.0, 411.5, self.N.REPLAY), plan)  # replay rate wins over the 8x speed-up
        self.assertIn((100.0, 208.0, self.N.FAST), plan)


if __name__ == "__main__":
    unittest.main()
