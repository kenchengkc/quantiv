from build_frontend_data import provider_event_fields


def test_provider_event_fields_flattens_product_signals():
    enrichment = {
        "short_interest": {
            "shares": 12_300_000,
            "days_to_cover": 4.2,
        },
        "options_flow": {
            "total_call_volume": 1000,
            "total_put_volume": 1800,
            "put_call_volume_ratio": 1.8,
            "put_call_open_interest_ratio": 1.2,
        },
        "signal_score": 0.55,
    }

    fields = provider_event_fields(enrichment)

    assert fields["short_days_to_cover"] == 4.2
    assert fields["short_interest_shares"] == 12_300_000
    assert fields["put_call_volume_ratio"] == 1.8
    assert fields["put_call_open_interest_ratio"] == 1.2
    assert fields["provider_options_volume"] == 2800
    assert fields["provider_signal_score"] == 0.55
    assert fields["provider_enrichment"] is enrichment
