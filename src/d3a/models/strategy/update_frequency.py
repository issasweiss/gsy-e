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
from typing import TYPE_CHECKING, Callable, List

from d3a_interface.constants_limits import ConstSettings, GlobalConfig
from d3a_interface.read_user_profile import InputProfileTypes
from d3a_interface.utils import find_object_of_same_weekday_and_time
from pendulum import duration, DateTime, Duration

from d3a.d3a_core.global_objects_singleton import global_objects
from d3a.d3a_core.util import write_default_to_dict, is_time_slot_in_past_markets

if TYPE_CHECKING:
    from d3a.models.area import Area
    from d3a.models.market.one_sided import OneSidedMarket
    from d3a.models.market.two_sided import TwoSidedMarket
    from d3a.models.strategy import BidEnabledStrategy, BaseStrategy


class TemplateStrategyUpdaterBase:
    """Manage template strategy bid / offer posting. Updates periodically the energy rate
    of the posted bids or offers. Base class"""
    def __init__(self, initial_rate: float, final_rate: float, fit_to_limit: bool = True,
                 energy_rate_change_per_update: float = None,
                 update_interval: Duration = duration(
                    minutes=ConstSettings.GeneralSettings.DEFAULT_UPDATE_INTERVAL),
                 rate_limit_object: Callable = max):
        self.fit_to_limit = fit_to_limit

        # initial input values (currently of type float)
        self.initial_rate_input = initial_rate
        self.final_rate_input = final_rate
        self.energy_rate_change_per_update_input = energy_rate_change_per_update

        # buffer of populated input values Dict[DateTime, float]
        self.initial_rate_profile_buffer = {}
        self.final_rate_profile_buffer = {}
        self.energy_rate_change_per_update_profile_buffer = {}

        # dicts that are used for price calculations, contain only
        # all_markets Dict[DateTime, float]
        self.initial_rate = {}
        self.final_rate = {}
        self.energy_rate_change_per_update = {}

        self._read_or_rotate_rate_profiles()

        self.update_interval = update_interval
        self.update_counter = {}
        self.number_of_available_updates = 0
        self.rate_limit_object = rate_limit_object

    def _read_or_rotate_rate_profiles(self) -> None:
        """ Creates a new chunk of profiles if the current_timestamp is not in the profile buffers
        """
        # TODO: this needs to be implemented to except profile UUIDs and DB connection
        self.initial_rate_profile_buffer = global_objects.profiles_handler.rotate_profile(
            InputProfileTypes.IDENTITY, self.initial_rate_input)
        self.final_rate_profile_buffer = global_objects.profiles_handler.rotate_profile(
            InputProfileTypes.IDENTITY, self.final_rate_input)
        if self.fit_to_limit is False:
            self.energy_rate_change_per_update_profile_buffer = (
                global_objects.profiles_handler.rotate_profile(
                    InputProfileTypes.IDENTITY, self.energy_rate_change_per_update_input)
            )

    def delete_past_state_values(self, current_market_time_slot: DateTime) -> None:
        """Delete values from buffers before the current_market_time_slot"""
        to_delete = []
        for market_slot in self.initial_rate:
            if is_time_slot_in_past_markets(market_slot, current_market_time_slot):
                to_delete.append(market_slot)
        for market_slot in to_delete:
            self.initial_rate.pop(market_slot, None)
            self.final_rate.pop(market_slot, None)
            self.energy_rate_change_per_update.pop(market_slot, None)
            self.update_counter.pop(market_slot, None)

    @staticmethod
    def get_all_markets(area: "Area") -> List["OneSidedMarket"]:
        """Get list of available markets. Defaults to only the spot market."""
        return [area.spot_market]

    @staticmethod
    def get_all_time_slots(area: "Area") -> List[DateTime]:
        """Get list of available time slots. Defaults to only the spot market time slot."""
        return [area.spot_market.time_slot]

    def _populate_profiles(self, area: "Area") -> None:
        for time_slot in self.get_all_time_slots(area):
            if self.fit_to_limit is False:
                self.energy_rate_change_per_update[time_slot] = (
                    find_object_of_same_weekday_and_time(
                        self.energy_rate_change_per_update_profile_buffer, time_slot)
                )
            self.initial_rate[time_slot] = find_object_of_same_weekday_and_time(
                self.initial_rate_profile_buffer, time_slot)
            self.final_rate[time_slot] = find_object_of_same_weekday_and_time(
                self.final_rate_profile_buffer, time_slot)
            self._set_or_update_energy_rate_change_per_update(time_slot)
            write_default_to_dict(self.update_counter, time_slot, 0)

    def _set_or_update_energy_rate_change_per_update(self, time_slot: DateTime) -> None:
        energy_rate_change_per_update = {}
        if self.fit_to_limit:
            energy_rate_change_per_update[time_slot] = \
                (find_object_of_same_weekday_and_time(
                    self.initial_rate_profile_buffer, time_slot) -
                 find_object_of_same_weekday_and_time(
                     self.final_rate_profile_buffer, time_slot)) / \
                self.number_of_available_updates
        else:
            if self.rate_limit_object is min:
                energy_rate_change_per_update[time_slot] = \
                    -1 * find_object_of_same_weekday_and_time(
                        self.energy_rate_change_per_update_profile_buffer, time_slot)
            elif self.rate_limit_object is max:
                energy_rate_change_per_update[time_slot] = \
                    find_object_of_same_weekday_and_time(
                        self.energy_rate_change_per_update_profile_buffer, time_slot)
        self.energy_rate_change_per_update.update(energy_rate_change_per_update)

    @property
    def _time_slot_duration_in_seconds(self) -> int:
        return GlobalConfig.slot_length.seconds

    @property
    def _calculate_number_of_available_updates_per_slot(self) -> int:
        number_of_available_updates = \
            max(int((self._time_slot_duration_in_seconds / self.update_interval.seconds) - 1), 1)
        return number_of_available_updates

    def update_and_populate_price_settings(self, area: "Area") -> None:
        """Populate the price profiles for every available time slot."""
        assert (ConstSettings.GeneralSettings.MIN_UPDATE_INTERVAL * 60 <=
                self.update_interval.seconds < self._time_slot_duration_in_seconds)

        self.number_of_available_updates = \
            self._calculate_number_of_available_updates_per_slot

        self._populate_profiles(area)

    def get_updated_rate(self, time_slot: DateTime) -> float:
        """Compute the rate for offers/bids at a specific time slot."""
        calculated_rate = (
            self.initial_rate[time_slot] -
            self.energy_rate_change_per_update[time_slot] * self.update_counter[time_slot])
        updated_rate = self.rate_limit_object(calculated_rate, self.final_rate[time_slot])
        return updated_rate

    def _elapsed_seconds(self, strategy: "BaseStrategy") -> int:
        current_tick_number = strategy.area.current_tick % (
                self._time_slot_duration_in_seconds / strategy.area.config.tick_length.seconds)
        return current_tick_number * strategy.area.config.tick_length.seconds

    def increment_update_counter_all_markets(self, strategy: "BaseStrategy") -> bool:
        """Update method of the class. Should be called on each tick and increments the
        update counter in order to validate whether an update in the posted energy rates
        is required."""
        should_update = [
            self._increment_update_counter(strategy, time_slot)
            for time_slot in self.get_all_time_slots(strategy.area)
        ]
        return any(should_update)

    def _increment_update_counter(self, strategy: "BaseStrategy", time_slot) -> bool:
        """Increment the counter of the number of times in which prices have been updated."""
        if self.time_for_price_update(strategy, time_slot):
            self.update_counter[time_slot] += 1
            return True
        return False

    def time_for_price_update(self, strategy: "BaseStrategy", time_slot: DateTime) -> bool:
        """Check if the prices of bids/offers should be updated."""
        return self._elapsed_seconds(strategy) >= (
            self.update_interval.seconds * self.update_counter[time_slot])

    def set_parameters(self, *, initial_rate: float = None, final_rate: float = None,
                       energy_rate_change_per_update: float = None, fit_to_limit: bool = None,
                       update_interval: int = None) -> None:
        """Update the parameters of the class without the need to destroy and recreate
        the object."""
        should_update = False
        if initial_rate is not None:
            self.initial_rate_input = initial_rate
            should_update = True
        if final_rate is not None:
            self.final_rate_input = final_rate
            should_update = True
        if energy_rate_change_per_update is not None:
            self.energy_rate_change_per_update_input = energy_rate_change_per_update
            should_update = True
        if fit_to_limit is not None:
            self.fit_to_limit = fit_to_limit
            should_update = True
        if update_interval is not None:
            self.update_interval = update_interval
            should_update = True
        if should_update:
            self._read_or_rotate_rate_profiles()


class TemplateStrategyBidUpdater(TemplateStrategyUpdaterBase):
    """Manage bids posted by template strategies. Update bids periodically."""

    def reset(self, strategy: "BidEnabledStrategy") -> None:
        """Reset the price of all bids to use their initial rate."""
        # decrease energy rate for each market again, except for the newly created one
        for market in self.get_all_markets(strategy.area):
            self.update_counter[market.time_slot] = 0
            strategy.update_bid_rates(market, self.get_updated_rate(market.time_slot))

    def update(self, market: "TwoSidedMarket", strategy: "BidEnabledStrategy") -> None:
        """Update the price of existing bids to reflect the new rates."""
        if self.time_for_price_update(strategy, market.time_slot):
            if strategy.are_bids_posted(market.id):
                strategy.update_bid_rates(market, self.get_updated_rate(market.time_slot))


class TemplateStrategyOfferUpdater(TemplateStrategyUpdaterBase):
    """Manage offers posted by template strategies. Update offers periodically."""

    def reset(self, strategy: "BaseStrategy") -> None:
        """Reset the price of all offers based to use their initial rate."""
        for market in self.get_all_markets(strategy.area):
            self.update_counter[market.time_slot] = 0
            strategy.update_offer_rates(market, self.get_updated_rate(market.time_slot))

    def update(self, market: "OneSidedMarket", strategy: "BaseStrategy") -> None:
        """Update the price of existing offers to reflect the new rates."""
        if self.time_for_price_update(strategy, market.time_slot):
            if strategy.are_offers_posted(market.id):
                strategy.update_offer_rates(market, self.get_updated_rate(market.time_slot))
