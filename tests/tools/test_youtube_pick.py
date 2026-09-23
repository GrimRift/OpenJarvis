"""Which YouTube result gets played: the one that fits the request, not the
first one YouTube lists."""

from __future__ import annotations

import json

from openjarvis.tools.youtube_pick import Candidate, parse_results, rank


def _video(video_id, title, channel="", length="10:00", views="1,000 views", badges=()):
    renderer = {
        "videoId": video_id,
        "title": {"runs": [{"text": title}]},
        "ownerText": {"runs": [{"text": channel}]},
        "viewCountText": {"simpleText": views},
        "publishedTimeText": {"simpleText": "1 year ago"},
        "badges": [{"metadataBadgeRenderer": {"label": b}} for b in badges],
    }
    if length:
        renderer["lengthText"] = {"simpleText": length}
    return {"videoRenderer": renderer}


def _page(*videos):
    data = {
        "contents": {"sectionListRenderer": {"contents": [{"items": list(videos)}]}}
    }
    return "<script>var ytInitialData = " + json.dumps(data) + ";</script>"


class TestParse:
    def test_videos_come_back_in_order_with_their_details(self):
        found = parse_results(
            _page(
                _video(
                    "aaaaaaaaaaa",
                    "Study Session",
                    "Lofi Girl",
                    "1:01:14",
                    "136,259,619 views",
                ),
                _video(
                    "bbbbbbbbbbb",
                    "Radio",
                    "Lofi Girl",
                    None,
                    "20,185 watching",
                    ["LIVE"],
                ),
            )
        )
        assert [c.video_id for c in found] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
        assert found[0].seconds == 3674 and found[0].views == 136_259_619
        assert found[0].channel == "Lofi Girl" and not found[0].live
        assert found[1].live and found[1].seconds is None

    def test_a_page_without_results_data_is_empty(self):
        assert parse_results("<html>consent page</html>") == []


def _candidate(position, title, channel="", seconds=600, live=False, views=10_000):
    return Candidate(
        video_id=f"{position:011d}",
        title=title,
        channel=channel,
        seconds=seconds,
        live=live,
        views=views,
        position=position,
    )


class TestRank:
    def test_a_live_stream_is_not_played_unless_asked_for(self):
        results = [
            _candidate(
                0, "lofi hip hop radio beats to study to", "Lofi Girl", None, live=True
            ),
            _candidate(1, "lofi study mix 3 hours", "Lofi Girl", 10_800),
        ]
        assert rank(results, "play some lofi to study to")[0].position == 1
        assert rank(results, "put on the lofi girl live stream")[0].position == 0

    def test_a_short_is_not_played_unless_asked_for(self):
        results = [
            _candidate(0, "chicken adobo recipe", seconds=45),
            _candidate(1, "how to cook chicken adobo recipe", seconds=420),
        ]
        assert rank(results, "how to cook chicken adobo")[0].position == 1

    def test_the_words_asked_for_beat_youtubes_order(self):
        results = [
            _candidate(0, "The Largest Black Hole in the Universe", "Kurzgesagt"),
            _candidate(1, "Black Holes Explained from Birth to Death", "Kurzgesagt"),
        ]
        best = rank(results, "kurzgesagt black holes explained")[0]
        assert best.position == 1

    def test_the_channel_named_is_preferred(self):
        results = [
            _candidate(0, "Opus 5.5 review", "Some Reviewer", views=900_000),
            _candidate(1, "Opus 5.5 is crazy good", "Matt Wolfe"),
        ]
        best = rank(results, "Matt Wolfe's video about Opus 5.5")[0]
        assert best.channel == "Matt Wolfe"
        assert "the channel you named" in best.reasons

    def test_with_nothing_to_tell_them_apart_youtubes_order_stands(self):
        results = [_candidate(0, "song"), _candidate(1, "song")]
        assert rank(results, "play song")[0].position == 0


class TestTheLatestOfAKind:
    """24 September: "the most latest F1 race highlight, the full race
    highlight of the twenty twenty six race" played F1's newest upload, a
    race-weekend preview."""

    def test_what_is_asked_beyond_the_channel_is_found(self):
        from openjarvis.tools.youtube_pick import extra_words

        request = (
            "play the most latest f one race highlight of the twenty twenty six race"
        )
        assert extra_words(request, "Formula 1") == ["race", "highlight", "2026"]
        assert extra_words("play the latest kurzgesagt video", "Kurzgesagt") == []

    def test_the_newest_matching_video_beats_the_newest_upload(self):
        results = [
            Candidate(
                "a" * 11,
                "Weekend Warm-Up | 2026 Azerbaijan Grand Prix",
                "FORMULA 1",
                1833,
                age="7 hours ago",
                position=0,
            ),
            Candidate(
                "b" * 11,
                "Drivers Look Ahead To Race Weekend | 2026 Azerbaijan Grand Prix",
                "FORMULA 1",
                849,
                age="5 hours ago",
                position=1,
            ),
            Candidate(
                "c" * 11,
                "Race Highlights | 2026 Spanish Grand Prix",
                "FORMULA 1",
                485,
                age="10 days ago",
                position=2,
            ),
            Candidate(
                "d" * 11,
                "Race Highlights | 2026 Hungarian Grand Prix",
                "FORMULA 1",
                495,
                age="1 month ago",
                position=3,
            ),
        ]
        request = (
            "latest f one race highlight, it should be the full race highlight "
            "of the twenty twenty six race Formula 1"
        )
        best = rank(results, request, latest=True)[0]
        assert best.title == "Race Highlights | 2026 Spanish Grand Prix"

    def test_ages_are_read_as_days(self):
        from openjarvis.tools.youtube_pick import age_days

        assert age_days("10 days ago") == 10
        assert age_days("2 weeks ago") == 14
        assert abs(age_days("Streamed 3 hours ago") - 3 / 24) < 1e-9
        assert age_days("") is None


class TestWhatIsRuledOut:
    """24 September: "not a preview or behind-the-scenes" counted "preview" as
    wanted, and the one preview in the results was played."""

    def test_words_after_not_are_refused(self):
        from openjarvis.tools.youtube_pick import refused_words

        request = (
            "full race highlight, actual F1 footage, not a preview or behind-the-scenes"
        )
        assert refused_words(request) == {"preview", "behind", "scene"}
        assert refused_words("not the preview but the race") == {"preview"}

    def test_a_refused_word_sinks_the_video(self):
        results = [
            Candidate(
                "a" * 11,
                "Azerbaijan GP Preview | F1 Nation Podcast",
                "FORMULA 1",
                3600,
                age="5h ago",
                position=0,
            ),
            Candidate(
                "b" * 11,
                "Race Highlights | 2026 Spanish Grand Prix",
                "FORMULA 1",
                485,
                age="10d ago",
                position=1,
            ),
        ]
        request = "Formula 1. the most recent race highlight, not a preview"
        assert rank(results, request, latest=True)[0].video_id == "b" * 11

    def test_short_ages_are_read_too(self):
        from openjarvis.tools.youtube_pick import age_days

        assert age_days("10d ago") == 10
        assert age_days("3mo ago") == 90
        assert abs(age_days("5h ago") - 5 / 24) < 1e-9
        assert age_days("1y ago") == 365
