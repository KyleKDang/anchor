# Triage Labels

The skills speak in terms of two canonical category roles and five canonical state roles. This file maps those roles to the actual label strings used in this repo's issue tracker.
A triaged issue carries exactly one of each.

**Category** - what kind of thing this is:

| Label in mattpocock/skills | Label in our tracker | Meaning                     |
| -------------------------- | -------------------- | --------------------------- |
| `bug`                      | `bug`                | Shipped behaviour is broken |
| `enhancement`              | `enhancement`        | New feature or improvement  |

**State** - how far through triage it is:

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

The category role also decides where the issue lives, per "Where an issue goes" in the root `CLAUDE.md`: `bug` is a plain top-level issue, `enhancement` is a sub-issue of the implementation map at #21.
The feature slices already on the map predate this and carry no category label; leave them as they are rather than backfilling.

Edit the right-hand column to match whatever vocabulary you actually use.
