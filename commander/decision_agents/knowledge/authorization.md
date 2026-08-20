# Authorization Knowledge

## AUTH-STATE-APPROVED: Approved authorization

Tags: authorization, approved

An approved authorization state may support demo handoff only when the selected
plan is within the authorization scope and has not expired.

## AUTH-STATE-PENDING: Pending authorization

Tags: authorization, pending, human-review

Pending authorization is not a blocking failure by itself, but it requires
human review before the plan can be marked approved for demo handoff.

## AUTH-STATE-DENIED: Denied authorization

Tags: authorization, denied, blocking

Denied authorization blocks demo handoff for the affected plan. The plan should
be revised or resubmitted for review.

## AUTH-STATE-EXPIRED: Expired authorization

Tags: authorization, expired, blocking

Expired authorization blocks demo handoff. A fresh review or renewed approval
is required before continuing.

