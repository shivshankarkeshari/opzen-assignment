"""Local evaluation of saved, real application predictions."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Must precede DeepEval imports. This suite sends judge requests only to the
# explicitly configured local endpoint and does not publish to Confident AI.
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
os.environ["DEEPEVAL_UPDATE_WARNING_OPT_OUT"] = "YES"
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
