import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_event_contract import (  # noqa: E402
    BASELINE_PATH, COMPATIBILITY_PATH, PROTO_PATH, V2, parse_contract,
    validate, validate_compatibility,
)
import json


class EventContractValidatorTests(unittest.TestCase):
    def test_checked_in_contract_matches_compatibility_manifest(self):
        validate()

    def test_parser_rejects_duplicate_field_numbers(self):
        source = """
        message EventEnvelope {
          string first = 1;
          string second = 1;
        }
        message FlowEvent {
          string value = 1;
        }
        message FeatureVector {
          string value = 1;
        }
        message Detection {
          string value = 1;
        }
        message CaptureObject {
          string value = 1;
        }
        """
        with self.assertRaises(ValueError):
            parse_contract(source)

    def test_additive_fields_preserve_v1_contract(self):
        baseline = BASELINE_PATH.read_text()
        expected = json.loads(COMPATIBILITY_PATH.read_text())
        validate_compatibility(PROTO_PATH.read_text(), baseline, expected)

    def test_renumbered_or_retyped_v1_field_is_rejected(self):
        baseline = BASELINE_PATH.read_text()
        expected = json.loads(COMPATIBILITY_PATH.read_text())
        source = PROTO_PATH.read_text()
        for changed in (
            source.replace("string event_id = 1;", "string event_id = 90;"),
            source.replace("string event_id = 1;", "bytes event_id = 1;"),
        ):
            with self.subTest(changed=changed != source), self.assertRaises(ValueError):
                validate_compatibility(changed, baseline, expected)


class EventContractV2Tests(unittest.TestCase):
    def setUp(self):
        self.source = V2.proto_path.read_text()
        self.baseline = V2.baseline_path.read_text()
        self.expected = json.loads(V2.compatibility_path.read_text())

    def test_v2_contract_matches_compatibility_manifest(self):
        validate(V2)

    def test_v2_manifest_covers_enums(self):
        self.assertEqual(self.expected["TransitionStatus"]["TRANSITION_STATUS_RESOLVE"], 4)
        self.assertEqual(self.expected["AttemptOutcome"]["ATTEMPT_OUTCOME_UNANSWERED"], 4)

    def test_additive_v2_field_and_enum_value_are_accepted(self):
        source = self.source.replace(
            "  bool continuation = 22;\n",
            "  bool continuation = 22;\n  uint32 vlan = 23;\n",
        ).replace(
            "  THREAT_FAMILY_PORT_SCAN = 1;\n",
            "  THREAT_FAMILY_PORT_SCAN = 1;\n  THREAT_FAMILY_BEACONING = 2;\n",
        )
        self.assertNotEqual(source, self.source)
        validate_compatibility(source, self.baseline, self.expected, V2)

    def test_breaking_v2_changes_are_rejected(self):
        for changed in (
            self.source.replace("string incident_id = 3;", "string incident_id = 30;"),
            self.source.replace("uint64 revision = 4;", "uint32 revision = 4;"),
            self.source.replace("TRANSITION_STATUS_OPEN = 1;", "TRANSITION_STATUS_OPEN = 5;"),
        ):
            with self.subTest(), self.assertRaises(ValueError):
                self.assertNotEqual(changed, self.source)
                validate_compatibility(changed, self.baseline, self.expected, V2)

    def test_enum_without_zero_value_is_rejected(self):
        source = "message M {\n  string a = 1;\n}\nenum E {\n  E_FIRST = 1;\n}\n"
        with self.assertRaises(ValueError):
            parse_contract(source, ("M",), ("E",))


if __name__ == "__main__":
    unittest.main()
