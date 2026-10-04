import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_event_contract import parse_contract, validate  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
