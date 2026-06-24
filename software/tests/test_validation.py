from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.validation import validate_and_normalize_config


class ValidationTests(unittest.TestCase):
    def test_participant_id_text_fields_are_preserved(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Participant Text Test",
                "questions": [
                    {
                        "type": "participant-id",
                        "prompt": "Please identify yourself anonymously.",
                        "code_label": "Your code",
                        "code_hint": "The raw inputs are hashed locally.",
                    },
                    {
                        "type": "finish",
                        "title": "Done",
                        "prompt": "Saved.",
                    },
                ],
            }
        )

        participant_card = config["questions"][0]
        self.assertEqual(participant_card["code_label"], "Your code")
        self.assertEqual(participant_card["code_hint"], "The raw inputs are hashed locally.")


if __name__ == "__main__":
    unittest.main()
