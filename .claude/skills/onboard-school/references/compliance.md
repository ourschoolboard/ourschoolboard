# What you may fetch

The database-backed compliance records are authoritative per host. This file is
the operating standard for adding a verdict through `compliance.py`.

## The standard

**Terms of service are the gate.** A prohibition disqualifies a host only when
it *both*:

1. binds an ordinary visitor — a browsewrap reaching "casual browsers", not a
  contract between a vendor and its district customer; **and**
2. actually reaches what we do — robotic or automated collection.

**Terms of service are the only compliance gate.** Do not consult or enforce
`robots.txt`; it governs search-engine indexing, not this collection workflow.

## Recording a decision

Any override, and any new host, needs the sentence it rests on in its recorded
evidence — a boolean is not an answer a year later. Where an override is
authorised by the project owner, record that and the date in both the fetch
`reason` and the database-backed compliance note.

Record new compliance findings in the database-backed onboarding metadata. Do
not create a PR from this skill. If the repository's legal reference needs a
policy-level correction, report it separately for the owner to handle.
