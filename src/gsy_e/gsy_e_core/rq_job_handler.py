import ast
import logging
import traceback
from datetime import datetime, date
from typing import Dict, Optional

import pendulum
from gsy_framework.constants_limits import GlobalConfig, ConstSettings
from gsy_framework.enums import ConfigurationType, SpotMarketTypeEnum
from gsy_framework.settings_validators import validate_global_settings
from pendulum import duration, instance, now

import gsy_e.constants
from gsy_e.gsy_e_core.simulation import run_simulation
from gsy_e.gsy_e_core.util import update_advanced_settings
from gsy_e.models.config import SimulationConfig

logging.getLogger().setLevel(logging.ERROR)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# pylint: disable=too-many-branches, too-many-statements
def launch_simulation_from_rq_job(scenario: Dict,
                                  settings: Optional[Dict],
                                  events: Optional[str],
                                  aggregator_device_mapping: Dict,
                                  saved_state: Dict,
                                  job_id: str,
                                  connect_to_profiles_db: bool = True):
    # pylint: disable=too-many-arguments, too-many-locals
    """Launch simulation from rq job."""

    gsy_e.constants.CONFIGURATION_ID = scenario.pop("configuration_uuid", None)
    try:
        if not gsy_e.constants.CONFIGURATION_ID:
            raise Exception("configuration_uuid was not provided")

        logger.error("Starting simulation with job_id: %s and configuration id: %s",
                     job_id, gsy_e.constants.CONFIGURATION_ID)

        settings = _adapt_settings(settings)

        if events is not None:
            events = ast.literal_eval(events)

        _configure_constants_constsettings(scenario, settings, connect_to_profiles_db)

        slot_length_realtime = (
            duration(seconds=settings["slot_length_realtime"].seconds)
            if "slot_length_realtime" in settings else None)

        scenario_name = "json_arg"

        kwargs = {"no_export": True,
                  "seed": settings.get("random_seed", 0)}

        _handle_scm_past_slots_simulation_run(
            scenario, settings, events, aggregator_device_mapping, saved_state, job_id,
            scenario_name, slot_length_realtime, kwargs)

        config = _create_config_settings_object(
            scenario, settings, aggregator_device_mapping)
        if GlobalConfig.IS_CANARY_NETWORK:
            config.start_date = (
                instance((datetime.combine(date.today(), datetime.min.time()))))
            if ConstSettings.MASettings.MARKET_TYPE == SpotMarketTypeEnum.COEFFICIENTS.value:
                # For SCM CNs, run with SCM_CN_DAYS_OF_DELAY in order to be able to get results.
                config.start_date = config.start_date.subtract(
                    days=gsy_e.constants.SCM_CN_DAYS_OF_DELAY)

        run_simulation(setup_module_name=scenario_name,
                       simulation_config=config,
                       simulation_events=events,
                       redis_job_id=job_id,
                       saved_sim_state=saved_state,
                       slot_length_realtime=slot_length_realtime,
                       kwargs=kwargs)

        logger.info("Finishing simulation with job_id: %s and configuration id: %s",
                    job_id, gsy_e.constants.CONFIGURATION_ID)

    # pylint: disable=broad-except
    except Exception:
        # pylint: disable=import-outside-toplevel
        from gsy_e.gsy_e_core.redis_connections.simulation import publish_job_error_output
        logger.error("Error on jobId, %s, configuration id: %s",
                     job_id, gsy_e.constants.CONFIGURATION_ID)
        publish_job_error_output(job_id, traceback.format_exc())
        logger.error("Error on jobId, %s, configuration id: %s: error sent to gsy-web",
                     job_id, gsy_e.constants.CONFIGURATION_ID)
        raise


def _adapt_settings(settings: Dict) -> Dict:
    if settings is None:
        settings = {}
    else:
        settings = {k: v for k, v in settings.items() if v is not None and v != "None"}

    advanced_settings = settings.get("advanced_settings", None)
    if advanced_settings is not None:
        update_advanced_settings(ast.literal_eval(advanced_settings))

    return settings


def _configure_constants_constsettings(
        scenario: Dict, settings: Dict, connect_to_profiles_db: bool):
    assert isinstance(scenario, dict)
    if "collaboration_uuid" in scenario or settings.get("type") in [
            ConfigurationType.CANARY_NETWORK.value, ConfigurationType.B2B.value]:
        gsy_e.constants.EXTERNAL_CONNECTION_WEB = True
        GlobalConfig.IS_CANARY_NETWORK = scenario.pop("is_canary_network", False)
        gsy_e.constants.RUN_IN_REALTIME = GlobalConfig.IS_CANARY_NETWORK

        if settings.get("type") == ConfigurationType.B2B.value:
            ConstSettings.ForwardMarketSettings.ENABLE_FORWARD_MARKETS = True
            # Disable fully automatic trading mode for the template strategies in favor of
            # UI manual and auto modes.
            ConstSettings.ForwardMarketSettings.FULLY_AUTO_TRADING = False

    gsy_e.constants.SEND_EVENTS_RESPONSES_TO_SDK_VIA_RQ = True

    spot_market_type = settings.get("spot_market_type")
    bid_offer_match_algo = settings.get("bid_offer_match_algo")

    if spot_market_type:
        ConstSettings.MASettings.MARKET_TYPE = spot_market_type
    if bid_offer_match_algo:
        ConstSettings.MASettings.BID_OFFER_MATCH_TYPE = bid_offer_match_algo

    ConstSettings.SettlementMarketSettings.RELATIVE_STD_FROM_FORECAST_FLOAT = (
        settings.get(
            "relative_std_from_forecast_percent",
            ConstSettings.SettlementMarketSettings.RELATIVE_STD_FROM_FORECAST_FLOAT
        ))

    ConstSettings.SettlementMarketSettings.ENABLE_SETTLEMENT_MARKETS = settings.get(
        "settlement_market_enabled",
        ConstSettings.SettlementMarketSettings.ENABLE_SETTLEMENT_MARKETS
    )
    gsy_e.constants.CONNECT_TO_PROFILES_DB = connect_to_profiles_db


def _create_config_settings_object(
        scenario: Dict, settings: Dict, aggregator_device_mapping: Dict
) -> SimulationConfig:
    config_settings = {
        "start_date":
            instance(datetime.combine(settings.get("start_date"), datetime.min.time()))
            if "start_date" in settings else GlobalConfig.start_date,
        "sim_duration":
            duration(days=settings["duration"].days)
            if "duration" in settings else GlobalConfig.sim_duration,
        "slot_length":
            duration(seconds=settings["slot_length"].seconds)
            if "slot_length" in settings else GlobalConfig.slot_length,
        "tick_length":
            duration(seconds=settings["tick_length"].seconds)
            if "tick_length" in settings else GlobalConfig.tick_length,
        "market_maker_rate":
            settings.get("market_maker_rate",
                         ConstSettings.GeneralSettings.DEFAULT_MARKET_MAKER_RATE),
        "cloud_coverage": settings.get("cloud_coverage", GlobalConfig.cloud_coverage),
        "pv_user_profile": settings.get("pv_user_profile", None),
        "capacity_kW": settings.get("capacity_kW",
                                    ConstSettings.PVSettings.DEFAULT_CAPACITY_KW),
        "grid_fee_type": settings.get("grid_fee_type", GlobalConfig.grid_fee_type),
        "external_connection_enabled": settings.get("external_connection_enabled", False),
        "aggregator_device_mapping": aggregator_device_mapping
    }

    validate_global_settings(config_settings)
    config = SimulationConfig(**config_settings)
    config.area = scenario
    return config


def _handle_scm_past_slots_simulation_run(
    scenario: Dict, settings: Optional[Dict], events: Optional[str],
    aggregator_device_mapping: Dict, saved_state: Dict, job_id: str,
    scenario_name: str, slot_length_realtime: Optional[duration],
    kwargs: Dict
):
    # pylint: disable=too-many-arguments
    """
    Run an extra simulation before running a CN, in case the scm_past_slots parameter is set.
    Used to pre-populate simulation results from past market slots before starting the CN.
    """
    scm_past_slots = saved_state.pop("scm_past_slots", False)
    if GlobalConfig.IS_CANARY_NETWORK and scm_past_slots:
        config = _create_config_settings_object(
            scenario, settings, aggregator_device_mapping)
        config.end_date = now(tz=pendulum.UTC).subtract(
            days=gsy_e.constants.SCM_CN_DAYS_OF_DELAY)
        config.sim_duration = config.end_date - config.start_date
        GlobalConfig.sim_duration = config.sim_duration
        GlobalConfig.IS_CANARY_NETWORK = False
        gsy_e.constants.RUN_IN_REALTIME = False
        run_simulation(setup_module_name=scenario_name,
                       simulation_config=config,
                       simulation_events=events,
                       redis_job_id=job_id,
                       saved_sim_state=saved_state,
                       slot_length_realtime=slot_length_realtime,
                       kwargs=kwargs)
        GlobalConfig.IS_CANARY_NETWORK = True
        gsy_e.constants.RUN_IN_REALTIME = True
