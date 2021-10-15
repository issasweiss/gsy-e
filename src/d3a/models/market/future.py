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

from copy import deepcopy
from logging import getLogger
from typing import Dict, List, Union, Tuple  # noqa

from d3a.d3a_core.exceptions import (BidNotFoundException, MarketReadOnlyException,
                                     OfferNotFoundException)
from d3a.events.event_structures import MarketEvent
from d3a.models.market import lock_market_action
from d3a.models.market.two_sided import TwoSidedMarket
from d3a_interface.constants_limits import ConstSettings, GlobalConfig
from d3a_interface.data_classes import Bid, Offer, Trade
from pendulum import DateTime

log = getLogger(__name__)


class FutureMarket(TwoSidedMarket):
    """Class responsible for future markets."""

    def __init__(self, time_slot=None, bc=None, notification_listener=None, readonly=False,
                 grid_fee_type=ConstSettings.IAASettings.GRID_FEE_TYPE,
                 grid_fees=None, name=None, in_sim_duration=True, ):
        super().__init__(time_slot, bc, notification_listener, readonly, grid_fee_type,
                         grid_fees, name, in_sim_duration=in_sim_duration)

        self.time_slot = None
        self.time_slot_str = "future"
        self.slot_bid_mapping = {}  # type: Dict[DateTime, List[Bid]]
        self.slot_offer_mapping = {}  # type: Dict[DateTime, List[Offer]]
        self.slot_trade_mapping = {}  # type: Dict[DateTime, List[Trade]]

    @property
    def _debug_log_market_type_identifier(self):
        return "[FUTURE]"

    def __repr__(self):  # pragma: no cover
        return f"<{self._class_name}{self.time_slot_str}"

    @property
    def future_market_slots(self) -> List[DateTime]:
        """"""
        return list(self.slot_bid_mapping.keys())

    def rotate_future_markets(self, first_future_slot: DateTime) -> None:
        """"""
        self._create_future_markets(first_future_slot)
        self._delete_old_future_markets(first_future_slot)

    def _delete_old_future_markets(self, first_future_slot: DateTime) -> None:
        self._delete_order_buffer_market_slot(first_future_slot, self.slot_bid_mapping)
        self._delete_order_buffer_market_slot(first_future_slot, self.slot_offer_mapping)
        self._delete_order_buffer_market_slot(first_future_slot, self.slot_trade_mapping)

    @staticmethod
    def _delete_order_buffer_market_slot(first_future_slot: DateTime,
                                         order_buffer: Dict[DateTime, List]) -> None:
        delete_time_slots = []
        for time_slot, orders in order_buffer.items():
            if time_slot < first_future_slot:
                # TODO: see whether this is needed:
                # for order in orders:
                #     del order
                delete_time_slots.append(time_slot)
        for time_slot in delete_time_slots:
            del order_buffer[time_slot]

    def _create_future_markets(self, first_future_slot: DateTime):
        current_time_slot = first_future_slot
        while current_time_slot <= first_future_slot + GlobalConfig.future_market_duration:
            if current_time_slot not in self.slot_bid_mapping:
                self.slot_bid_mapping[current_time_slot] = []
                self.slot_offer_mapping[current_time_slot] = []
                self.slot_trade_mapping[current_time_slot] = []

    @lock_market_action
    def get_bids_per_slot(self, time_slot: DateTime) -> List[Bid]:
        """
        """
        return deepcopy(self.slot_bid_mapping[time_slot])

    @lock_market_action
    def get_offers_per_slot(self, time_slot: DateTime) -> List[Offer]:
        """
        """
        return deepcopy(self.slot_offer_mapping[time_slot])

    @lock_market_action
    def bid(self, price: float, energy: float, buyer: str, buyer_origin: str,
            bid_id: str = None, original_price=None, adapt_price_with_fees=True,
            add_to_history=True, buyer_origin_id=None, buyer_id=None,
            attributes: Dict = None, requirements: List[Dict] = None,
            time_slot: DateTime = None) -> Bid:

        bid = super().bid(price=price, energy=energy, buyer=buyer, buyer_origin=buyer_origin,
                          bid_id=bid_id, original_price=original_price,
                          add_to_history=False, adapt_price_with_fees=adapt_price_with_fees,
                          buyer_origin_id=buyer_origin_id, buyer_id=buyer_id,
                          attributes=attributes, requirements=requirements, time_slot=time_slot)
        self.slot_bid_mapping[time_slot].append(bid)
        return bid

    @lock_market_action
    def offer(self, price: float, energy: float, seller: str, seller_origin,
              offer_id=None, original_price=None, dispatch_event=True,
              adapt_price_with_fees=True, add_to_history=True, seller_origin_id=None,
              seller_id=None, attributes: Dict = None, requirements: List[Dict] = None,
              time_slot: DateTime = None) -> Offer:
        offer = super().offer(price, energy, seller, seller_origin, offer_id, original_price,
                              dispatch_event, adapt_price_with_fees, add_to_history,
                              seller_origin_id, seller_id, attributes, requirements, time_slot)
        self.slot_offer_mapping[time_slot].append(offer)
        return offer

    @lock_market_action
    def delete_bid(self, bid_or_id: Union[str, Bid]) -> None:
        if self.readonly:
            raise MarketReadOnlyException()

        bid_id = bid_or_id.id if isinstance(bid_or_id, Bid) else bid_or_id

        bid = self.bids.pop(bid_id)
        if not bid:
            raise BidNotFoundException()

        self.slot_bid_mapping[bid.time_slot].remove(bid)

        log.debug(f"{self._debug_log_market_type_identifier}[BID][DEL]"
                  f"[{self.time_slot_str}] {bid}")
        self._notify_listeners(MarketEvent.BID_DELETED, bid=bid)

    @lock_market_action
    def delete_offer(self, offer_or_id: Union[str, Offer]) -> None:
        if self.readonly:
            raise MarketReadOnlyException()

        offer_id = offer_or_id.id if isinstance(offer_or_id, Offer) else offer_or_id

        offer = self.offers.pop(offer_id)
        if not offer:
            raise OfferNotFoundException()

        self.slot_bid_mapping[offer.time_slot].remove(offer)

        log.debug(f"{self._debug_log_market_type_identifier}[OFFER][DEL]"
                  f"[{self.name}][{self.time_slot_str}] {offer}")

        self._notify_listeners(MarketEvent.OFFER_DELETED, offer=offer)

    def accept_bid(self, bid: Bid, energy: float = None,
                   seller: str = None, buyer: str = None, already_tracked: bool = False,
                   trade_rate: float = None, trade_offer_info=None, seller_origin=None,
                   seller_origin_id=None, seller_id=None) -> Trade:

        trade = super().accept_bid(bid, energy, seller, buyer, already_tracked, trade_rate,
                                   trade_offer_info, seller_origin, seller_origin_id, seller_id)
        if already_tracked is False:
            self.slot_trade_mapping[trade.time_slot].append(trade)
        return Trade

    def accept_offer(self, offer_or_id: Union[str, Offer], buyer: str, *, energy: int = None,
                     time: DateTime = None,
                     already_tracked: bool = False, trade_rate: float = None,
                     trade_bid_info=None, buyer_origin=None, buyer_origin_id=None,
                     buyer_id=None) -> Trade:

        trade = super().accept_offer(offer_or_id, buyer, energy, time, already_tracked, trade_rate,
                                     trade_bid_info, buyer_origin, buyer_origin_id, buyer_id)
        if already_tracked is False:
            self.slot_trade_mapping[trade.time_slot].append(trade)
        return Trade
