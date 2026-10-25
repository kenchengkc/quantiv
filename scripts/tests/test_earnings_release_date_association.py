"""Administrative dates must not replace the announced earnings event."""

from datetime import date

import pytest

import reconcile_earnings_calendar as calendar


PUBLISHED = date(2026, 10, 8)


def test_question_platform_opening_cannot_replace_announced_release_date():
    text = (
        "Example to Report 3Q26 Results on November 6, 2026. "
        "Dubai and New York, October 8, 2026 – Example Ltd. (Nasdaq: EXMP) "
        "will release its financial and operating results for the third quarter and "
        "nine-month period ended September 30, 2026, at 9:00 GST / 0:00 ET on November 6, 2026. "
        "Senior management will host a results presentation and earnings call the same day. "
        "Shareholders can submit and upvote questions for management during the results conference call. "
        "Starting on October 30, 2026, at 16:00 GST / 8:00 ET, the Q&A platform will become "
        "available, and all shareholders will be able to submit and upvote questions."
    )
    assert calendar.extract_earnings_announcement(text, published_on=PUBLISHED) == {
        "date": "2026-11-06", "timing": "bmo",
    }


def test_call_headline_does_not_override_a_different_explicit_release_date():
    text = (
        "Example to Host Earnings Conference Call on November 6, 2026. "
        "Example will release financial results after market close on November 5, 2026. "
        "Management will discuss results on November 6, 2026 at 8:00 a.m. ET. "
        "Registration for the earnings conference call will open on October 30, 2026."
    )
    assert calendar.extract_earnings_announcement(text, published_on=PUBLISHED) == {
        "date": "2026-11-05", "timing": "amc",
    }


def test_registration_date_alone_cannot_create_an_earnings_event():
    assert calendar.extract_earnings_announcement(
        "Registration for the earnings conference call will open on October 30, 2026. "
        "The earnings release date will be announced separately.",
        published_on=PUBLISHED,
    ) is None


def test_explicit_release_date_is_not_discarded_when_its_sentence_mentions_registration():
    assert calendar.extract_earnings_announcement(
        "Example will release financial results after market close on November 5, 2026 "
        "and conference call registration is available through its investor website.",
        published_on=PUBLISHED,
    ) == {"date": "2026-11-05", "timing": "amc"}


@pytest.mark.parametrize("separator", [", and ", ", while ", "; "])
@pytest.mark.parametrize("reverse", [False, True])
def test_registration_clause_cannot_borrow_release_action_from_other_clause(separator, reverse):
    release = "Example will release financial results on November 6, 2026"
    registration = "registration will open on October 30, 2026"
    clauses = [registration, release] if reverse else [release, registration]
    text = separator.join(clauses) + "."
    assert calendar.extract_earnings_announcement(text, published_on=PUBLISHED) == {
        "date": "2026-11-06", "timing": "unknown",
    }


@pytest.mark.parametrize("separator", [", and ", ", while ", "; "])
def test_registration_without_another_date_does_not_discard_release_date(separator):
    text = ("Example will release financial results on November 6, 2026"
            + separator + "registration is available on its website.")
    assert calendar.extract_earnings_announcement(text, published_on=PUBLISHED) == {
        "date": "2026-11-06", "timing": "unknown",
    }
