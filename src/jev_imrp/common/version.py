"""Version constants.

Every persisted dataset, feature snapshot, label set, simulator run and config
file must carry one of these so a result can be tied back to what produced it
(plan sections 6.2 and 14.3).
"""

from __future__ import annotations

__version__ = "0.1.0"

#: Shape of the append-only raw event table (plan section 5.2).
RAW_EVENT_SCHEMA_VERSION = "raw_events_v1"
#: Quote-state reconstruction output (plan section 6.1).
QUOTE_STATE_SCHEMA_VERSION = "quote_states_v1"
#: Normalizer/parser code that produced a state.
PARSER_VERSION = "parser_v0"
#: Feature set registry version (config/feature_config.yaml).
FEATURE_CONFIG_VERSION = "feature_config_v1"
#: Forward label generator version.
LABEL_VERSION = "labels_v0"
#: Fill/execution simulator version.
SIMULATION_VERSION = "simulation_v0"
#: Cost schedule version in force.
COST_SCHEDULE_VERSION = "cost_schedule_v1"
#: Universe membership version.
UNIVERSE_VERSION = "universe_v1"
#: Schema of the YAML config files themselves.
CONFIG_SCHEMA_VERSION = "config_v1"
#: Version of the frozen research contract (PREREGISTRATION.md).
EXPERIMENT_CONTRACT_VERSION = "contract_v1"
