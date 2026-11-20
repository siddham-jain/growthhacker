from typing import Any

from app.core.registry import Registry

# each registry maps a key to a class; services build instances from settings and ports
candidate_generator_registry: Registry[type[Any]] = Registry("competitor_candidate_generators")
web_query_template_registry: Registry[Any] = Registry("competitor_web_query_templates")
channel_resolver_registry: Registry[type[Any]] = Registry("competitor_channel_resolvers")
ownership_signal_registry: Registry[type[Any]] = Registry("competitor_ownership_signals")
business_scoring_registry: Registry[Any] = Registry("competitor_business_scoring_profiles")
drift_rule_registry: Registry[type[Any]] = Registry("competitor_drift_rules")
