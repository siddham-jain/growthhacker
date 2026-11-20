"""import side effects of component 12: cron tasks, the nightly freeze purge, api services and policy rows."""

from app.evaluation import api  # noqa: F401 - evaluation api service
from app.evaluation.freeze import purge  # noqa: F401 - nightly freeze-bundle purge hook
from app.evaluation.ops import api as ops_api  # noqa: F401 - ops api service
from app.evaluation.ops import tasks  # noqa: F401 - ops_rollup and ops_alerts maintenance tasks
from app.evaluation.policy import features  # noqa: F401 - the feature-policy registry rows
