"""Acceptance tests written from the design before application code."""

import hashlib
import json
import random
import sqlite3

from tests.support import CliCase, FIXTURES, event, events_in, rejections_in


class ContractTests(CliCase):
    def test_golden_business_report(self):
        self.ingest()
        self.assertEqual(self.report(), self.golden_report())

    def test_first_run_summary_accounts_for_every_line(self):
        self.assertEqual(self.ingest(), {
            "input_lines": 12, "inserted": 5, "duplicates": 2,
            "invalid": 4, "conflicts": 1,
        })

    def test_replay_preserves_events_rejections_and_report(self):
        self.ingest()
        before = (events_in(self.db), rejections_in(self.db), self.report())
        summary = self.ingest()
        self.assertEqual(summary, {
            "input_lines": 12, "inserted": 0, "duplicates": 7,
            "invalid": 4, "conflicts": 1,
        })
        self.assertEqual((events_in(self.db), rejections_in(self.db), self.report()), before)

    def test_error_locations_match_independent_fixture(self):
        source = FIXTURES / "golden-12.jsonl"
        self.ingest(source)
        expected = json.loads((FIXTURES / "expected-rejections.json").read_text(encoding="utf-8"))
        actual = rejections_in(self.db)
        self.assertEqual([{"line_no": x[1], "reason": x[2]} for x in actual], expected)
        self.assertEqual({x[0] for x in actual}, {hashlib.sha256(source.read_bytes()).hexdigest()})
        raw = source.read_text(encoding="utf-8").splitlines()
        for item in actual:
            self.assertEqual(item[4], raw[item[1] - 1])
            self.assertTrue(item[3])

    def test_same_id_in_two_services_is_not_duplicate(self):
        self.ingest(self.input([event(), event(service="mobile")]))
        self.assertEqual(len(events_in(self.db)), 2)

    def test_json_order_and_outside_whitespace_do_not_change_identity(self):
        value = event()
        text = json.dumps(value) + "\n  " + json.dumps(dict(reversed(list(value.items())))) + "  \n"
        self.assertEqual(self.ingest(self.raw_input(text))["duplicates"], 1)
        self.assertEqual(len(events_in(self.db)), 1)

    def test_conflicting_status_keeps_original(self):
        summary = self.ingest(self.input([event(), event(status_code=500)]))
        self.assertEqual(summary["conflicts"], 1)
        self.assertEqual(summary["duplicates"], 0)
        self.assertEqual(events_in(self.db)[0][-1], 200)
        self.assertEqual(rejections_in(self.db)[0][2], "id_conflict")

    def test_conflicting_query_is_not_silently_dropped(self):
        self.ingest(self.input([event(), event(query="다른 검색어")]))
        self.assertEqual(events_in(self.db)[0][3], "날씨")
        self.assertEqual(rejections_in(self.db)[0][2], "id_conflict")

    def test_first_valid_arrival_wins_a_conflict(self):
        self.ingest(self.input([event(status_code=500), event()]))
        self.assertEqual(events_in(self.db)[0][-1], 500)
        self.assertEqual(self.report()["server_error_rate_percent"], 100.0)

    def test_invalid_inputs_are_classified_without_blocking_valid_row(self):
        malformed = "{bad json}"
        missing = event()
        del missing["event_time"]
        duplicate_field = json.dumps(event())[:-1] + ',"service":"web"}'
        cases = [
            (malformed, "malformed_json"),
            (json.dumps(missing), "invalid_schema"),
            (json.dumps(event(extra="x")), "invalid_schema"),
            ("[]", "invalid_schema"), ("null", "invalid_schema"),
            (duplicate_field, "invalid_schema"),
            (json.dumps(event(status_code="200")), "invalid_value"),
            (json.dumps(event(status_code=True)), "invalid_value"),
            (json.dumps(event(status_code=200.0)), "invalid_value"),
            (json.dumps(event(status_code=99)), "invalid_value"),
            (json.dumps(event(status_code=600)), "invalid_value"),
            (json.dumps(event(event_time="2026-02-30T00:00:00Z")), "invalid_value"),
            (json.dumps(event(event_time="2026-10-14T09:00:00+09:00")), "invalid_value"),
            (json.dumps(event(event_time="2026-1-1T00:00:00Z")), "invalid_value"),
            (json.dumps(event(service=" web")), "invalid_value"),
            (json.dumps(event(event_id="")), "invalid_value"),
            (json.dumps(event(query="")), "invalid_value"),
            ("  ", "malformed_json"),
        ]
        text = "\n".join(row for row, _ in cases) + "\n" + json.dumps(event()) + "\n"
        summary = self.ingest(self.raw_input(text))
        self.assertEqual(summary["invalid"], len(cases))
        self.assertEqual(summary["inserted"], 1)
        self.assertEqual([r[2] for r in rejections_in(self.db)], [kind for _, kind in cases])

    def test_blank_line_is_a_rejected_physical_line(self):
        self.ingest(self.raw_input("\n" + json.dumps(event()) + "\n"))
        self.assertEqual(rejections_in(self.db)[0][1], 1)
        self.assertEqual(len(events_in(self.db)), 1)

    def test_empty_file_has_zero_rate_and_empty_groups(self):
        self.assertEqual(self.ingest(self.raw_input(""))["input_lines"], 0)
        self.assertEqual(self.report(), {
            "event_count": 0, "server_error_count": 0,
            "server_error_rate_percent": 0.0, "hourly": [], "services": [],
        })

    def test_last_line_without_newline_is_processed(self):
        self.ingest(self.raw_input(json.dumps(event())))
        self.assertEqual(len(events_in(self.db)), 1)

    def test_status_boundaries_and_rate_denominator(self):
        rows = [event(event_id=str(i), status_code=code) for i, code in enumerate([100, 200, 404, 499, 500, 599])]
        self.ingest(self.input(rows + [rows[0]]))
        result = self.report()
        self.assertEqual(result["event_count"], 6)
        self.assertEqual(result["server_error_count"], 2)
        self.assertEqual(result["server_error_rate_percent"], 33.333333)

    def test_batch_size_does_not_change_final_result(self):
        for size in [1, 4, 100]:
            with self.subTest(batch_size=size):
                db = self.work / (str(size) + ".db")
                self.ingest(db=db, batch=size)
                self.assertEqual(self.report(db=db), self.golden_report())
                self.assertEqual(len(rejections_in(db)), 5)

    def test_collision_free_order_and_duplicate_multiplicity_do_not_change_metrics(self):
        originals = [json.loads(line) for line in (FIXTURES / "golden-12.jsonl").read_text(encoding="utf-8").splitlines()[:5]]
        for seed in range(5):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                rows = originals + rng.choices(originals, k=20)
                rng.shuffle(rows)
                db = self.work / ("permutation-" + str(seed) + ".db")
                self.ingest(self.input(rows, "permutation.jsonl"), db=db, batch=4)
                self.assertEqual(self.report(db=db), self.golden_report())

    def test_same_bytes_under_another_filename_do_not_duplicate_rejections(self):
        self.ingest()
        copy = self.work / "renamed.jsonl"
        copy.write_bytes((FIXTURES / "golden-12.jsonl").read_bytes())
        self.ingest(copy)
        self.assertEqual(len(rejections_in(self.db)), 5)

    def test_query_is_bound_as_data_not_sql(self):
        query = "'); DROP TABLE events; --"
        self.ingest(self.input([event(query=query)]))
        self.assertEqual(events_in(self.db)[0][3], query)

    def test_rejection_export_contains_original_line_and_reason(self):
        self.ingest()
        out = self.work / "rejects.jsonl"
        self.cli("rejects", "--db", self.db, "--out", out)
        rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["line_no"], 8)
        self.assertEqual(rows[0]["reason"], "id_conflict")
        self.assertIn("raw_line", rows[0])

    def test_missing_input_is_failure(self):
        result = self.cli("ingest", self.work / "missing.jsonl", "--db", self.db, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", result.stderr.lower())
        self.assertFalse(result.stdout.strip())

    def test_invalid_batch_sizes_are_rejected(self):
        for size in [0, -1]:
            with self.subTest(size=size):
                result = self.cli("ingest", FIXTURES / "golden-12.jsonl", "--db", self.db, "--batch-size", size, ok=False)
                self.assertNotEqual(result.returncode, 0)

    def test_directory_cannot_be_used_as_database(self):
        result = self.cli("ingest", FIXTURES / "golden-12.jsonl", "--db", self.work, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", result.stderr.lower())

    def test_missing_database_is_not_created_by_report(self):
        result = self.cli("report", "--db", self.db, "--out", self.work / "report.json", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.db.exists())

    def test_invalid_utf8_fails_but_preserves_previously_committed_batch(self):
        path = self.work / "bad-utf8.jsonl"
        path.write_bytes((json.dumps(event()) + "\n").encode("utf-8") + b"\xff\n")
        result = self.cli("ingest", path, "--db", self.db, "--batch-size", 1, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(result.stdout.strip())
        self.assertEqual(len(events_in(self.db)), 1)

    def test_database_is_structurally_valid_after_replay(self):
        self.ingest()
        self.ingest()
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_unwritable_output_path_is_reported_as_failure(self):
        self.ingest()
        parent_file = self.work / "not-a-directory"
        parent_file.write_text("keep", encoding="utf-8")
        result = self.cli("report", "--db", self.db, "--out", parent_file / "report.json", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(parent_file.read_text(encoding="utf-8"), "keep")

    def test_report_cannot_replace_its_database(self):
        self.ingest()
        before = self.db.read_bytes()
        result = self.cli("report", "--db", self.db, "--out", self.db, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.db.read_bytes(), before)

    def test_rejection_export_cannot_replace_its_database(self):
        self.ingest()
        before = self.db.read_bytes()
        result = self.cli("rejects", "--db", self.db, "--out", self.db, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.db.read_bytes(), before)
