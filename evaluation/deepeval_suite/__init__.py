"""Local evaluation of saved, real application predictions."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Must precede DeepEval imports. This suite sends judge requests only to the
# explicitly configured local endpoint and does not publish to Confident AI.
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
os.environ["DEEPEVAL_UPDATE_WARNING_OPT_OUT"] = "YES"
# Load exactly the repository-root file below, not DeepEval's cwd-based
# .env/.env.local discovery or its legacy credential/configuration file.
os.environ["DEEPEVAL_DISABLE_DOTENV"] = "1"
os.environ["DEEPEVAL_DISABLE_LEGACY_KEYFILE"] = "1"
# Evaluations stay local even if a shell or the root file contains a cloud key.
# This only affects this process; saved logins and env files are not modified.
os.environ["CONFIDENT_API_KEY"] = ""
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
# Retry ownership stays with durable metric checkpoints, not hidden SDK loops.
os.environ["DEEPEVAL_RETRY_MAX_ATTEMPTS"] = "1"
