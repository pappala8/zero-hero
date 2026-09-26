from datetime import date, datetime, timezone, timedelta
import pytest
from data.realtime.models import RealtimeInstrument, RealtimeRawEvent, RealtimeMarketEvent
from data.models import ContractIdentity
from data.realtime.validator import validate_raw_event, validate_market_event

def ev(**kw):
    t=datetime(2026,9,29,9,31,tzinfo=timezone.utc)
    base=dict(provider="fake",source_event_id="1",event_timestamp=t,available_at=t,instrument_token=1,instrument=RealtimeInstrument.SPOT,underlying="NIFTY",open=1,high=2,low=1,close=2)
    base.update(kw); return RealtimeRawEvent(**base)

def test_valid_event(): validate_raw_event(ev())

def test_timezone_required():
    with pytest.raises(ValueError): validate_raw_event(ev(event_timestamp=datetime(2026,9,29,9,31)))

def test_availability_cannot_be_before_event():
    t=datetime(2026,9,29,9,31,tzinfo=timezone.utc)
    with pytest.raises(ValueError): validate_raw_event(ev(event_timestamp=t,available_at=t-timedelta(seconds=1)))

def test_invalid_ohlc():
    with pytest.raises(ValueError): validate_raw_event(ev(high=0.5))

def test_negative_price():
    with pytest.raises(ValueError): validate_raw_event(ev(close=-1))

def test_option_requires_identity():
    with pytest.raises(ValueError): validate_raw_event(ev(instrument=RealtimeInstrument.OPTION))

def test_market_event_contract_validates():
    t=datetime(2026,9,29,9,31,tzinfo=timezone.utc)
    e=RealtimeMarketEvent("fake","1",t,t,2,RealtimeInstrument.OPTION,ContractIdentity("NIFTY",date(2026,9,29),25800,"CE"),10,11,9,10)
    validate_market_event(e)
