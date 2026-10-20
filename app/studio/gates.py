"""Which stage rows offer Approve and Reject.

Only a review-gated stage whose latest run is still pending. video, lipsync,
finish and compose are never review gates, including when a template flag
says otherwise. An approved or rejected run does not show the buttons.
"""
NEVER_REVIEW = frozenset({"video", "lipsync", "finish", "compose"})


def show_review(stage_id, review, status, review_status):
    return (bool(review) and stage_id not in NEVER_REVIEW and status == "succeeded"
            and review_status == "pending")
