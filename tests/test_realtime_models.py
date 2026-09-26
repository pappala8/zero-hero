from datetime import date, datetime, timezone
from data.realtime.models import RealtimeInstrument, RealtimeRawEvent

def test_option_identity_separates_right_and_expiry():
    t = datetime(2026, 9, 29, 9, 31, tzinfo=timezone.utc)
    a = RealtimeRawEvent("fake","a",t,t,1,RealtimeInstrument.OPTION,"NIFTY",date(2026,9,29),25800,"CE",10,11,9,10)
    b = RealtimeRawEvent("fake","b",t,t,2,RealtimeInstrument.OPTION,"NIFTY",date(2026,10,6),25800,"PE",10,11,9,10)
    assert a.contract.as_tuple() != b.contract.as_tuple()
