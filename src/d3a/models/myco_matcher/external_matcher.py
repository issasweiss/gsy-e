import json
import logging
from enum import Enum
from typing import Dict, List

from d3a_interface.dataclasses import BidOfferMatch

import d3a.constants
from d3a.d3a_core.exceptions import (
    InvalidBidOfferPairException, MycoValidationException)
from d3a.d3a_core.redis_connections.redis_area_market_communicator import ResettableCommunicator
from d3a.models.market import Market


class ExternalMatcherEventsEnum(Enum):
    MATCHING_DATA_RESPONSE = "matching_data_response"
    MATCH = "match"
    TICK = "tick"
    MARKET = "market_cycle"
    FINISH = "finish"


class ExternalMatcher:
    """Class responsible for external bids / offers matching."""
    def __init__(self):
        super().__init__()
        self.simulation_id = d3a.constants.COLLABORATION_ID
        self.myco_ext_conn = None
        self._channel_prefix = f"external-myco/{self.simulation_id}"
        self._events_channel = f"{self._channel_prefix}/events/"
        self._setup_redis_connection()
        self.area_uuid_markets_mapping = {}
        self.markets_mapping = {}  # Dict[market_id: market] mapping

    def _setup_redis_connection(self):
        self.myco_ext_conn = ResettableCommunicator()
        self.myco_ext_conn.sub_to_multiple_channels(
            {"external-myco/simulation-id/": self.publish_simulation_id,
             f"{self._channel_prefix}/matching-data/": self.publish_matching_data,
             f"{self._channel_prefix}/recommendations/": self.match_recommendations})

    def publish_matching_data(self, message):
        """Publish open offers and bids.

        Published data are of the following format:
            {"matching_data": {"market_id" : {"bids": [], "offers": [] }, filters: {}}}
        """
        response_data = {"event": ExternalMatcherEventsEnum.MATCHING_DATA_RESPONSE.value}
        data = json.loads(message.get("data"))
        filters = data.get("filters", {})
        # IDs of markets (Areas) the client is interested in
        filtered_areas_uuids = filters.get("markets")
        market_offers_bids_list_mapping = {}
        for area_uuid, markets in self.area_uuid_markets_mapping.items():
            if filtered_areas_uuids and area_uuid not in filtered_areas_uuids:
                # Client is uninterested in this Area -> skip
                continue
            for market in markets:
                # Cache the market (needed while matching)
                self.markets_mapping[market.id] = market
                bids_list, offers_list = self._get_bids_offers(market, filters)
                market_offers_bids_list_mapping[market.id] = {
                    "bids": bids_list, "offers": offers_list}
        response_data.update({
            "matching_data": market_offers_bids_list_mapping,
        })

        channel = f"{self._channel_prefix}/matching-data/response/"
        self.myco_ext_conn.publish_json(channel, response_data)

    def match_recommendations(self, message):
        """Receive trade recommendations and match them in the relevant market.

        Matching in bulk, any pair that fails validation will cancel the operation
        """
        channel = f"{self._channel_prefix}/recommendations/response/"
        response_data = {"event": ExternalMatcherEventsEnum.MATCH.value, "status": "success"}
        data = json.loads(message.get("data"))
        recommendations = data.get("recommended_matches", [])
        try:
            validated_recommendations = self._get_validated_recommendations(recommendations)
            for recommendation in validated_recommendations:
                market = self.markets_mapping.get(recommendation["market_id"])
                market.match_recommendations([recommendation])
        except Exception as ex:
            response_data["status"] = "fail"
            response_data["message"] = "Validation Error"
            if not isinstance(ex, MycoValidationException):
                logging.exception("Bid offer pair matching failed.")

        self.myco_ext_conn.publish_json(channel, response_data)

    def publish_simulation_id(self, message):
        """Publish the simulation id to the redis myco client.

        At the moment the id of the simulations run by the cli is set as ""
        however, this function guarantees that the myco is aware of the running collaboration id
        regardless of the value set in d3a.
        """

        channel = "external-myco/simulation-id/response/"
        self.myco_ext_conn.publish_json(channel, {"simulation_id": self.simulation_id})

    def publish_event_tick_myco(self):
        """Publish the tick event to the Myco client."""

        data = {"event": ExternalMatcherEventsEnum.TICK.value}
        self.myco_ext_conn.publish_json(self._events_channel, data)

    def publish_event_market_cycle_myco(self):
        """Publish the market event to the Myco client."""

        data = {"event": ExternalMatcherEventsEnum.MARKET.value}
        self.myco_ext_conn.publish_json(self._events_channel, data)

    def publish_event_finish_myco(self):
        """Publish the finish event to the Myco client."""

        data = {"event": ExternalMatcherEventsEnum.FINISH.value}
        self.myco_ext_conn.publish_json(self._events_channel, data)

    def update_area_uuid_markets_mapping(self, area_uuid_markets_mapping: Dict) -> None:
        """Interface for updating the area_uuid_markets_mapping mapping."""
        self.area_uuid_markets_mapping.update(area_uuid_markets_mapping)

    @staticmethod
    def _get_bids_offers(market: Market, filters: Dict):
        """Get bids and offers from market, apply filters and return serializable lists."""

        bids, offers = market.open_bids_and_offers
        bids_list = list(bid.serializable_dict() for bid in bids.values())
        filtered_offers_energy_type = filters.get("energy_type")
        if filtered_offers_energy_type:
            offers_list = list(
                offer.serializable_dict() for offer in offers.values()
                if offer.attributes and
                offer.attributes.get("energy_type") == filtered_offers_energy_type)
        else:
            offers_list = list(
                offer.serializable_dict() for offer in offers.values())
        return bids_list, offers_list

    def _get_validated_recommendations(
            self, recommendations: List[Dict]) -> List[BidOfferMatch.serializable_dict]:
        """Return a validated list of BidOfferMatch instances."""
        validated_recommendations = []
        for recommendation in recommendations:
            market = self.markets_mapping.get(recommendation.get("market_id"))
            if market is None or market.readonly:
                # The market is already finished or doesn't exist
                raise MycoValidationException

            # Get the original bid and offer from the market
            market_bids = [
                market.bids.get(bid.get("id"), None) for bid in recommendation.get("bids")]
            market_offers = [
                market.offers.get(offer.get("id"), None) for offer in recommendation.get("offers")]

            if not (all(market_bids) and all(market_offers)):
                # Offers or Bids either don't belong to market or were already matched
                continue
            try:
                market.validate_bid_offer_match(
                    market_bids,
                    market_offers,
                    recommendation.get("trade_rate"),
                    recommendation.get("selected_energy")
                )
            except InvalidBidOfferPairException:
                continue

            recommendation["bids"] = [bid.serializable_dict() for bid in market_bids]
            recommendation["offers"] = [offer.serializable_dict() for offer in market_offers]
            validated_recommendations.append(recommendation)
        return validated_recommendations
