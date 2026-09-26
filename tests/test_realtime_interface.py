from data.realtime.interface import ProviderConnectionState, MarketDataProvider

def test_connection_states_exist():
    assert ProviderConnectionState.STREAMING.value == "STREAMING"
    assert ProviderConnectionState.AUTH_FAILED.value == "AUTH_FAILED"

def test_interface_has_no_order_methods():
    names = set(dir(MarketDataProvider))
    assert not ({"place_order", "modify_order", "cancel_order"} & names)
