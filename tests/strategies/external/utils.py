"""
Copyright 2018 Grid Singularity
This file is part of D3A.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import json
import uuid
from collections import deque
from typing import Dict
from unittest.mock import MagicMock, Mock

from d3a.models.area import Area
from d3a.models.strategy.external_strategies import IncomingRequest
from d3a_interface.constants_limits import ConstSettings, GlobalConfig
from pendulum import duration


def create_areas_markets_for_strategy_fixture(strategy):
    config = Mock()
    config.slot_length = duration(minutes=15)
    config.tick_length = duration(seconds=15)
    config.ticks_per_slot = 60
    config.start_date = GlobalConfig.start_date
    config.grid_fee_type = ConstSettings.IAASettings.GRID_FEE_TYPE
    config.end_date = GlobalConfig.start_date + duration(days=1)
    config.market_count = 1
    area = Area(name="forecast_pv", config=config, strategy=strategy,
                external_connection_available=True)
    parent = Area(name="parent_area", children=[area], config=config)
    parent.activate()
    strategy.connected = True
    market = MagicMock()
    market.time_slot = GlobalConfig.start_date
    return strategy


def check_external_command_endpoint_with_correct_payload_succeeds(ext_strategy_fixture,
                                                                  command: str,
                                                                  arguments: Dict):
    transaction_id = str(uuid.uuid4())
    arguments.update({"transaction_id": transaction_id})
    payload = {"data": json.dumps(arguments)}
    assert ext_strategy_fixture.pending_requests == deque([])
    getattr(ext_strategy_fixture, command)(payload)
    assert len(ext_strategy_fixture.pending_requests) > 0
    response_channel = f"{ext_strategy_fixture.channel_prefix}/response/{command}"
    assert (ext_strategy_fixture.pending_requests ==
            deque([IncomingRequest(command, arguments, response_channel)]))


def assert_bid_offer_aggregator_commands_return_value(return_value, is_offer):
    command_name = "offer" if is_offer else "bid"
    assert return_value["status"] == "ready"
    assert return_value["command"] == command_name
    return_value[command_name] = json.loads(return_value[command_name])
    assert return_value[command_name]["price"] == 200.0
    assert return_value[command_name]["energy"] == 0.5
    assert return_value[command_name]["energy_rate"] == 400.0
    assert return_value[command_name][
        "seller" if is_offer else "buyer"] == "forecast_pv"
    assert return_value[command_name]["original_price"] == 200.0
    assert return_value[command_name][
        "seller_origin" if is_offer else "buyer_origin"] == "forecast_pv"
    assert return_value[command_name]["replace_existing"] is True
    assert return_value[command_name]["type"] == "Offer" if is_offer else "Bid"
