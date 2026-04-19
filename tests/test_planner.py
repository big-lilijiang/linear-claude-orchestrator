import unittest

from damon_autocoding.planner import parse_json_output


class PlannerTests(unittest.TestCase):
    def test_parse_json_output_accepts_plain_json(self) -> None:
        payload = parse_json_output('{"ok": true, "message": "hi"}')
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["message"], "hi")

    def test_parse_json_output_extracts_json_from_wrapped_text(self) -> None:
        payload = parse_json_output("Here is the result:\n{\"ok\": true}\nDone.")
        self.assertEqual(payload["ok"], True)


if __name__ == "__main__":
    unittest.main()
